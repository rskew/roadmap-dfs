#!/usr/bin/env python3
"""Run a finished task again from where it began, under a system you changed, and score it.

A finished task's starting point is recoverable from git: the repo as it was the commit
before the task's first node, and a task file holding what the author wrote (its title,
Goal and Background) and nothing the sessions added. This clones the repo there, lets a
chain (run.sh) work the task, and measures the result with history.py's `session()` and
`tasks_of()`, the code that measured the original, so the two columns are comparable.

  python3 <scripts>/replay.py candidates [--repo PATH]       (tasks the loop's selection rule picks from)
  python3 <scripts>/replay.py plan    TASK [--repo PATH]
  python3 <scripts>/replay.py prepare TASK DEST [--repo PATH] [--tool DIR | --tool-ref REV] [--law FILE] [--agent A]
  python3 <scripts>/replay.py run     DEST TASK [--cap N] [--tool DIR] [--bash-minutes M] [--agent A]    (inside proot)
  python3 <scripts>/replay.py score   DEST TASK [--repo PATH]
  python3 <scripts>/replay.py replay  TASK DEST [--cap N] [--bash-minutes M] [... prepare's flags]   (all three)

The replay runs under the agent the screen is set to (`.dfs/runs/agent`: claude, codex, kiro or
opencode), or the one `--agent` names; `prepare` records it. `score` reads claude's transcripts and
kiro's run logs (`.dfs/runs/dfs_run_*/run-N.json`, with credits where claude has dollars); codex and
opencode replays can be run but not yet scored.

The system that changes is the TOOL (`--tool DIR`: a roadmap-dfs checkout or store path,
or `--tool-ref REV`: a revision of the repo being replayed, when that repo is roadmap-dfs
itself, exported with git archive; SKILL.md, the scripts and the templates all come from
it) and optionally the LAW (`--law FILE`, copied over `.dfs/ROADMAP.md`). With neither, the
replay runs the current tool under the law of the starting commit.

A replay's sessions run inside proot (nix: pkgs.proot, no privileges needed), so they see
their own clone and a fresh HOME and not the live checkout, its runs, its task files or the
author's `~/.claude` and `~/.codex`; see `sandbox_argv`. proot hides paths, it is not a
security boundary: `score` still counts the tool calls that name a live path.

DEST's basename must contain `dfs-replay-`: that is how history.py keeps the replay's
transcripts out of the history it mines. A replay spends real money (the original's cost
is printed by `plan`) and cannot answer a raise: a chain that stops on one is scored as
blocked, which is itself a result.
"""
import glob
import json
import os
import re
import shutil
import statistics as st
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import history  # noqa: E402

KEEP = ("Goal", "Background")   # the sections the author writes; the rest is the sessions'
IDENTITY = {"user.name": "dfs replay", "user.email": "replay@dfs.invalid"}   # where the checkout has none


def git(repo, *args, check=True):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"replay: git {' '.join(args)} in {repo}: {r.stderr.strip()}")
    return r.stdout


def main_checkout():
    """The repo the history belongs to: the checkout that owns this directory's worktree."""
    common = git(os.getcwd(), "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    return os.path.dirname(common)


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def transcripts(checkout, config=None):
    """Globs for the transcripts of a checkout and of its own worktrees: the exact directory
    and `<dir>--dfs-worktrees-*`, so `dfs-replay-W55-1` never takes in `dfs-replay-W55-10`.
    They are under `config` (a replay's own claude config dir), else the author's."""
    enc = glob.escape(re.sub(r"[/.]", "-", str(Path(checkout).resolve())))
    base = os.path.join(glob.escape(str(config or config_dir())), "projects")
    return [os.path.join(base, enc, "*.jsonl"),
            os.path.join(base, enc + "--dfs-worktrees-*", "*.jsonl")]


def author_part(text):
    """A task file as the author left it: title, Goal and Background, no Summary, Tree or
    Log. Sections are split at `## ` headings; the title and what precedes the first stay."""
    parts = re.split(r"(?m)^(?=## )", text)
    keep = [p for p in parts if not p.startswith("## ") or p[3:].split("\n", 1)[0].strip() in KEEP]
    return "".join(keep).rstrip() + "\n"


def starting_point(repo, task):
    """(sha before the task's first node, the task file as the author wrote it)."""
    subjects = git(repo, "log", "--reverse", "--format=%H", "-E", f"--grep=^{task}\\.[0-9]").split()
    if not subjects:
        raise SystemExit(f"replay: no commit in {repo} starts with {task}.<n>")
    first = subjects[0]
    rel = f".dfs/items/{task}.md"
    parent = git(repo, "rev-parse", first + "^").strip()
    # The author's file may exist already at the parent; if not, the first node's commit
    # added it and the part the author wrote is still in it.
    for rev in (parent, first):
        r = subprocess.run(["git", "show", f"{rev}:{rel}"], cwd=repo, capture_output=True, text=True)
        if r.returncode == 0:
            return parent, author_part(r.stdout)
    raise SystemExit(f"replay: {rel} is in neither {parent[:8]} nor {first[:8]}")


def original(repo, task):
    """The original run of `task`, scored by the code that scores a replay."""
    repos = {}
    sessions, tasks, _, _ = history.load(transcripts(repo), repos)
    mine = history.project_of(re.sub(r"[/.]", "-", str(Path(repo).resolve())))
    for (project, name), t in tasks.items():
        if name == task and project == mine:
            return t
    return None


def candidates(repo, limit=3.0):
    """The tasks of this repo that the loop's selection rule picks from the history: original
    at most `limit` dollars, a first review that found issues, and a node commit to start
    before. Whether a task's evidence lies in the repo is for a reader to check."""
    tasks = history.load(transcripts(repo), {})[1]
    mine = history.project_of(re.sub(r"[/.]", "-", str(Path(repo).resolve())))
    rows = []
    for (project, name), t in tasks.items():
        if project != mine or t["first_review"] != "issues" or not t["sessions"].get("work") \
                or not 0 < t["usd"] <= limit:
            continue
        try:
            starting_point(repo, name)
        except SystemExit:
            continue
        rows.append(t)
    return sorted(rows, key=lambda t: t["usd"])


def hooks_can_run(where):
    """Whether a script written under `where` can be executed: git skips a hook on a noexec
    mount ("hook was ignored because it's not set as executable"), and a replay with no
    pre-commit hook runs without the dfs guard, a different system from the original."""
    with tempfile.TemporaryDirectory(dir=where) as d:
        probe = Path(d) / "probe"
        probe.write_text("#!/bin/sh\nexit 0\n")
        probe.chmod(0o755)
        try:
            return subprocess.run([str(probe)], capture_output=True).returncode == 0
        except OSError:
            return False


def identity(repo):
    """The author identity of the checkout being replayed, for the clone's config."""
    return {k: git(repo, "config", k, check=False).strip() or d for k, d in IDENTITY.items()}


def seal(dest):
    """Leave nothing of the original's later work in the clone: no `origin` (its refs hold
    the task's own nodes and every commit since), no branch but `main`, no tag, no reflog entry naming them, and
    no object that is not reachable from the starting point."""
    git(dest, "remote", "remove", "origin")
    for branch in git(dest, "branch", "--format=%(refname:short)").split():
        if branch != "main":   # a source on `trunk` clones with its tip, the original's later work, as a branch
            git(dest, "branch", "-D", branch)
    for tag in git(dest, "tag", "--list").split():
        git(dest, "tag", "-d", tag)
    git(dest, "reflog", "expire", "--expire=now", "--all")
    git(dest, "gc", "-q", "--prune=now")


def prepare(repo, task, dest, tool=None, tool_ref=None, law=None, agent=None):
    dest = Path(dest).resolve()
    if "dfs-replay-" not in dest.name:
        raise SystemExit("replay: DEST's name must contain 'dfs-replay-' (history.py skips it by that)")
    if dest.exists():
        raise SystemExit(f"replay: {dest} exists; a replay starts from nothing")
    if not dest.parent.is_dir() or not hooks_can_run(dest.parent):
        raise SystemExit(f"replay: scripts cannot run under {dest.parent} (a noexec mount, as /tmp often is), "
                         "so git would skip the dfs hooks and the replay would run without the guard; "
                         "choose a DEST on a mount that can execute")
    sha, text = starting_point(repo, task)
    ident = identity(repo)
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(repo), str(dest)], check=True)
    git(dest, "checkout", "-q", "-B", "main", sha)
    seal(dest)
    for k, v in ident.items():
        git(dest, "config", k, v)
    (dest / ".dfs" / "items").mkdir(parents=True, exist_ok=True)
    (dest / ".dfs" / "items" / f"{task}.md").write_text(text)
    if law:
        shutil.copy(law, dest / ".dfs" / "ROADMAP.md")
    if tool_ref:
        tool = export_tool(repo, tool_ref, dest.parent / (dest.name + "-tool"))
    tool = Path(tool or os.environ.get("DFS_TOOL") or HERE.parent).resolve()
    # init.py brings the hooks and .gitignore up to the tool's; the starting point is a
    # commit of its own after it, so nothing is left for the first session to find
    # uncommitted and the task's first node is the next commit.
    subprocess.run([sys.executable, str(tool / "scripts" / "init.py")], cwd=dest, check=True,
                   env={**os.environ, "DFS_TOOL": str(tool)}, capture_output=True)
    git(dest, "add", "-A", ".dfs")
    git(dest, "commit", "-q", "--allow-empty", "-m",
        f"replay: {task} as the author left it, from {sha[:8]}", "--no-verify")
    (dest / ".git" / "replay.json").write_text(json.dumps({"task": task, "from": sha, "tool": str(tool),
                                                            "agent": configured_agent(repo, agent)}))
    return dest, tool


def export_tool(repo, rev, into):
    into.mkdir(parents=True)
    tar = subprocess.run(["git", "archive", rev], cwd=repo, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(into)], input=tar, check=True)
    return into


BASH_MINUTES = 15


def bash_bound(minutes):
    """The child's Bash limits, so one call cannot sit for the runner's hour (W55's replay
    spent 53 minutes in `grep -r /`): an unnamed call gets `minutes`, and no call more than
    twice that. A call that really needs longer fails loudly and the session sees why."""
    ms = int(float(minutes) * 60_000)
    return {"BASH_DEFAULT_TIMEOUT_MS": str(ms), "BASH_MAX_TIMEOUT_MS": str(2 * ms)}


TOOL_ENTRIES = ("SKILL.md", "README.md", "LICENCE.txt", "scripts", "templates")   # what a tool is for
# `docs` holds every task's review findings (`dfs-review-findings.tsv`, `dfs-rounds.tsv`), the
# original's first-review defects among them, so the sessions see a `docs` of the box's own with
# only the file the tool uses at run time (SKILL.md names it).
TOOL_DOCS = ("artefacts.md",)
SANDBOX_HOME = "/home/replay"
ETC = ("/etc/resolv.conf", "/etc/ssl", "/etc/hosts", "/etc/nsswitch.conf", "/etc/passwd", "/etc/group")
# kiro unpacks a generic Linux `node` that wants the host's dynamic loader at /lib64 and its libraries;
# the store holds none of that for it. Libraries only: no home, no /var, no /etc beyond ETC.
HOST_LIBS = {"kiro": (("/usr/lib64", "/lib64"), ("/usr/lib", "/lib"), ("/usr/lib", "/usr/lib"))}
PASS_ENV = ("TERM", "LANG", "LC_ALL", "TZ", "ANTHROPIC_API_KEY")   # the rest of the box's environment stays out
SIGN_IN_KEYS = ("hasCompletedOnboarding", "lastOnboardingVersion", "oauthAccount", "userID")
_NIX_OUT = {}


def box_of(dest):
    """The replay's own scratch beside DEST (not in it, so it never lands in the clone): the
    proot root, proot's temp dir and the home the sessions run with."""
    dest = Path(dest).resolve()
    return dest.parent / (dest.name + "-box")


def nix_bin(attr, binary, given, repo):
    """`binary` of nixpkgs' `attr` at the repo's agent pin: `given` (an env var's value) if set,
    else built or found in the store by nix (offline when the store has it)."""
    if given:
        return given
    key = (attr, str(repo))
    if key not in _NIX_OUT:
        rev = subprocess.run([sys.executable, str(HERE / "agent.py"), "--rev"], cwd=repo, capture_output=True, text=True)
        if rev.returncode or not rev.stdout.strip():
            raise SystemExit(f"replay: no agent pin to take {attr} from ({rev.stderr.strip()}); "
                             f"set REPLAY_{attr.upper().replace('-', '_')}=/path/to/{binary}")
        out = subprocess.run(["nix", "build", "--no-link", "--print-out-paths", "--impure",
                              f"github:nixos/nixpkgs/{rev.stdout.strip()}#{attr}"],
                             capture_output=True, text=True, timeout=900)
        if out.returncode or not out.stdout.strip():
            raise SystemExit(f"replay: nix could not build {attr}: {out.stderr.strip()[-300:]}")
        _NIX_OUT[key] = out.stdout.split()[0]
    return os.path.join(_NIX_OUT[key], "bin", binary)


AGENTS = ("claude", "codex", "kiro", "opencode")   # the agents run.sh can launch (walk.py's AGENTS)
# per agent: nixpkgs attribute, the binary in it, the variable run.sh takes its command from, and the
# variable that names a binary of your own
AGENT_BIN = {"claude": ("claude-code", "claude", "CLAUDE_CMD", "REPLAY_CLAUDE_CODE"),
             "codex": ("codex", "codex", "CODEX_CMD", "REPLAY_CODEX"),
             "kiro": ("kiro-cli-unwrapped", "kiro-cli", "KIRO_CMD", "REPLAY_KIRO_CLI"),
             "opencode": ("opencode", "opencode", "OPENCODE_CMD", "REPLAY_OPENCODE")}
SCORABLE = ("claude", "kiro")   # the agents whose transcripts `score` reads (history.py parses claude's and kiro's)


def configured_agent(repo=None, override=None):
    """The agent a replay runs under: `override` (`--agent`) if given, else the one the screen
    keeps in `.dfs/runs/agent` (walk.py's `agent_file`; `$DFS_RUNS` first, then the checkout's own
    runs), claude when there is no such file or it names nothing run.sh launches."""
    if override:
        if override not in AGENTS:
            raise SystemExit(f"replay: --agent must be one of {', '.join(AGENTS)}, not {override!r}")
        return override
    for runs in (os.environ.get("DFS_RUNS"), os.path.join(repo or main_checkout(), ".dfs", "runs")):
        if not runs:
            continue
        try:
            name = Path(runs, "agent").read_text(errors="replace").strip()
        except OSError:
            continue
        return name if name in AGENTS else "claude"
    return "claude"


def tool_bins(repo, agent="claude"):
    """The programs the sandbox is built from, as paths in /nix/store (the one place it can see):
    proot, the agent's own binary at the repo's pin (keyed by the agent's name), bash and env."""
    attr, binary, _, given = AGENT_BIN[agent]
    found = {
        "proot": nix_bin("proot", "proot", os.environ.get("REPLAY_PROOT") or shutil.which("proot"), repo),
        agent: nix_bin(attr, binary, os.environ.get(given), repo),
        "bash": shutil.which("bash"),
        "env": shutil.which("env"),
    }
    for name, path in found.items():
        if not path or not os.path.exists(path):
            raise SystemExit(f"replay: cannot find {name} for the sandbox")
        if name in ("bash", "env") and not path.startswith("/nix/store/"):
            raise SystemExit(f"replay: {name} is {path}, outside /nix/store, where the sandbox can see only nix's")
    return found


def copy_private(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    dst.chmod(0o600)


def kiro_sign_in(home, box_home):
    """kiro-cli's sign-in in the box: its settings, and a copy of its database with the
    conversation and history tables emptied, so no earlier session of the author's comes along."""
    import sqlite3
    db = Path(home) / ".local" / "share" / "kiro-cli" / "data.sqlite3"
    out = box_home / ".local" / "share" / "kiro-cli" / "data.sqlite3"
    if db.is_file() and not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst = sqlite3.connect(out)
        with dst:
            src.backup(dst)
            for (table,) in dst.execute("select name from sqlite_master where type='table'").fetchall():
                if table in ("history", "conversations", "conversations_v2"):
                    dst.execute(f'delete from "{table}"')
        dst.close()
        src.close()
        out.chmod(0o600)
    # The engine kiro-cli starts is not in the nix package: it unpacks a node, a bun, the TUI and
    # the agent server (`kas`) into this directory, and a fresh home that lacks them fails to
    # spawn the engine. They are the program, not the author's data (no sessions, logs or knowledge).
    share, out_share = Path(home) / ".local" / "share" / "kiro-cli", out.parent
    for entry in ("node", "node.sha256", "bun", "bun.sha256", "tui.js", "tui.js.sha256", "kas"):
        if (share / entry).exists() and not (out_share / entry).exists():
            if (share / entry).is_dir():
                shutil.copytree(share / entry, out_share / entry, symlinks=True)
            else:
                shutil.copy2(share / entry, out_share / entry)
    settings = Path(home) / ".kiro" / "settings" / "cli.json"
    if settings.is_file() and not (box_home / ".kiro" / "settings" / "cli.json").exists():
        copy_private(settings, box_home / ".kiro" / "settings" / "cli.json")


def make_box(dest, agent="claude"):
    """The box of DEST: an empty proot root, a temp dir for proot, and a fresh home holding
    only the agent's sign-in (claude: credentials, the account keys of `.claude.json` and
    `settings.json`; kiro: `kiro_sign_in`; codex: `auth.json` and `config.toml`; opencode: its
    `auth.json` and config), so the sessions find no `~/.claude/projects`, no history and no
    `~/.codex` sessions of the author's."""
    box = box_of(dest)
    for d in ("root/tmp", "tmp", "home/.claude"):
        (box / d).mkdir(parents=True, exist_ok=True)
    home = Path(os.path.expanduser("~"))
    box_home = box / "home"
    if agent == "kiro":
        kiro_sign_in(home, box_home)
    elif agent == "codex":
        for name in ("auth.json", "config.toml"):
            if (home / ".codex" / name).is_file() and not (box_home / ".codex" / name).exists():
                copy_private(home / ".codex" / name, box_home / ".codex" / name)
    elif agent == "opencode":
        for rel in (Path(".local/share/opencode/auth.json"), Path(".config/opencode/opencode.json")):
            if (home / rel).is_file() and not (box_home / rel).exists():
                copy_private(home / rel, box_home / rel)
    if agent != "claude":
        return box
    cfg = Path(config_dir())
    for name in (".credentials.json", "settings.json"):
        if (cfg / name).is_file() and not (box_home / ".claude" / name).exists():
            copy_private(cfg / name, box_home / ".claude" / name)
    sign_in = box_home / ".claude.json"
    if not sign_in.exists():
        for src in (cfg / ".claude.json", home / ".claude.json"):
            if src.is_file():
                data = json.loads(src.read_text())
                sign_in.write_text(json.dumps({k: data[k] for k in SIGN_IN_KEYS if k in data}))
                sign_in.chmod(0o600)
                break
    return box


def sandbox_env(tool, bins, bash_minutes=BASH_MINUTES, agent="claude"):
    """What the sessions inside the sandbox are told: nothing of the live checkout (no
    `DFS_*`, no `XDG_*`, no `CLAUDE_CONFIG_DIR`), a PATH of store entries only."""
    path = ":".join(p for p in os.environ.get("PATH", "").split(":") if p.startswith("/nix/store/"))
    env = {"PATH": path, "HOME": SANDBOX_HOME, "DFS_TOOL": str(tool), AGENT_BIN[agent][2]: bins[agent],
           "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt", **bash_bound(bash_minutes)}
    env.update({k: os.environ[k] for k in PASS_ENV if k in os.environ})
    return env


STORE_ROOT = re.compile(r"^(/nix/store/[^/]+)")


def store_closure(paths):
    """The store paths the sandbox needs: each of `paths` that is in the store and exists, and
    everything they refer to (`nix-store -qR`). The store as a whole holds the flake sources nix
    copied when it built this repo, which include the original task files, so it is never bound."""
    roots = sorted({m.group(1) for p in paths if (m := STORE_ROOT.match(str(p))) and os.path.exists(m.group(1))})
    if not roots:
        return []
    r = subprocess.run(["nix-store", "-qR", *roots], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"replay: nix-store -qR could not list what the sandbox needs from the store "
                         f"({r.stderr.strip()[-200:]}); it binds those paths and not all of /nix")
    return sorted(set(r.stdout.split()))


def tool_docs(dest, tool):
    """The box's `docs`: a directory holding only `TOOL_DOCS` of the tool's docs (none of the
    findings, rounds, break-it or optimization files, which name the original run's defects),
    or None when the tool has no docs. Built afresh each launch."""
    src = Path(tool) / "docs"
    if not src.is_dir():
        return None
    out = box_of(dest) / "docs"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    for name in TOOL_DOCS:
        if (src / name).is_file():
            shutil.copy(src / name, out / name)
    return out


def sandbox_argv(dest, tool, inner, bins, env, agent="claude", store=None):
    """The command that runs `inner` in a proot of DEST: an empty root, the store paths it runs from
    (`store_closure`; the rest of /nix/store, with its `*-source` copies of task files, is not there), /proc and /dev, the
    nix `bash` as /bin/sh and `env` as /usr/bin/env, the network files of /etc, the box's home as
    HOME, DEST at its own path, and (unless the tool is in the store) the tool's own entries at
    theirs: never the tool as a whole, which when it is the live checkout holds its `.dfs/`.
    The sessions see the host's `/workspace/...` only as the path down to DEST."""
    dest, tool, box = Path(dest).resolve(), Path(tool).resolve(), box_of(dest)
    # The store paths the chain runs from (the bins, the tool when it is in the store, and the
    # store entries of the sessions' PATH) and their closure, one bind each: never `-b /nix`.
    if store is None:
        store = store_closure([*bins.values(), tool, *env.get("PATH", "").split(":")])
    cmd = [bins["proot"], "-r", str(box / "root")]
    for path in store:
        cmd += ["-b", path]
    cmd += ["-b", "/proc", "-b", "/dev", "-b", f"{bins['bash']}:/bin/sh", "-b", f"{bins['env']}:/usr/bin/env"]
    for e in ETC:
        if os.path.exists(e):
            cmd += ["-b", e]
    for src, at in HOST_LIBS.get(agent, ()):
        if os.path.exists(src):
            cmd += ["-b", f"{src}:{at}"]
    cmd += ["-b", f"{box / 'home'}:{SANDBOX_HOME}", "-b", f"{dest}:{dest}"]
    if not str(tool).startswith("/nix/store/"):
        for entry in TOOL_ENTRIES:
            if (tool / entry).exists():
                cmd += ["-b", f"{tool / entry}:{tool / entry}"]
    # a tool in the store is bound whole above (its bin and libexec are on the sessions' PATH),
    # a live checkout's entries one by one: either way its docs are the box's trimmed copy
    docs = tool_docs(dest, tool)
    if docs:
        cmd += ["-b", f"{docs}:{tool / 'docs'}"]
    return cmd + ["-w", str(dest), "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in env.items()], *inner]


def launch(dest, task, cap, tool, bash_minutes, repo=None, agent="claude"):
    """(argv, environment) of the chain's launch: run.sh inside the sandbox, told which agent
    to run (`--kiro`, `--codex`, `--opencode`; none for claude, run.sh's default)."""
    dest = Path(dest).resolve()
    make_box(dest, agent)
    bins = tool_bins(repo or main_checkout(), agent)
    flag = [] if agent == "claude" else [f"--{agent}"]
    inner = ["bash", str(tool / "scripts" / "run.sh"), *flag, task, str(cap or 6)]
    argv = sandbox_argv(dest, tool, inner, bins, sandbox_env(tool, bins, bash_minutes, agent), agent)
    # proot unpacks its loader into PROOT_TMP_DIR, which has to be able to execute: the box is
    # beside DEST, on the mount DEST is on
    return argv, {"PROOT_TMP_DIR": str(box_of(dest) / "tmp"), "PATH": os.environ.get("PATH", "")}


def run(dest, task, cap=None, tool=None, bash_minutes=BASH_MINUTES, repo=None, agent=None):
    dest = Path(dest).resolve()
    info = json.loads((dest / ".git" / "replay.json").read_text())
    tool = Path(tool or info["tool"])
    # the agent `prepare` recorded, unless this run names another
    cmd, env = launch(dest, task, cap, tool, bash_minutes, repo, configured_agent(repo, agent) if agent else info.get("agent", "claude"))
    # The chain's own words are kept beside the replay: its last "dfs_run: DONE|BLOCKED|CAP"
    # line is why it stopped, which the runner's meta.json (the task's status) does not say.
    with subprocess.Popen(cmd, cwd=dest, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace") as p, open(dest / ".git" / "replay.out", "w") as log:
        for line in p.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
    return p.returncode


def replay_config(dest):
    """Where a replay's claude kept its transcripts: its box's home, or (a replay made before
    the sandbox) the author's own config dir."""
    home = box_of(dest) / "home" / ".claude"
    return home if (home / "projects").is_dir() else None


def stopped_on(dest):
    """Why the chain stopped: DONE, BLOCKED or CAP (empty when it was never run here)."""
    try:
        text = (Path(dest) / ".git" / "replay.out").read_text()
    except OSError:
        return ""
    found = re.findall(r"dfs_run: (DONE|BLOCKED|CAP)\b", text)
    return found[-1] if found else "other"


REACH_FIELDS = ("command", "file_path", "path", "pattern", "notebook_path", "includePattern")


def live_paths(repo):
    """What a replay's sessions can reach that the original's later work or evidence lives
    in: the live checkout (its runs, items, worktrees and scripts), the codex session logs
    and the transcripts of every earlier session, the original task's included."""
    home = os.path.expanduser("~")
    paths = [str(Path(repo).resolve()), os.path.join(home, ".codex"), os.path.join(config_dir(), "projects")]
    # a session writes the home as `~` or `$HOME` as often as in full
    return paths + [p.replace(home, h, 1) for p in paths[1:] if p.startswith(home) for h in ("~", "$HOME")]


# The store holds copies of repos' `.dfs/items` (the flake sources nix made when it built them), so a
# call that names a `*-source` directory, or the store as a whole (`ls /nix/store`, `grep -r x /nix/store/*`,
# `find /nix/store`), is looking where the original's task file may be. The sandbox binds only the
# chain's closure, so such a call finds nothing; it is counted all the same.
STORE_LOOK = r"/nix/store/[^/\s\"']*-source|/nix/store(?:/\*|/?(?=[\s\"'|;)&]|$))"


def result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def answered_nothing(block):
    """Whether a claude tool result came back with nothing (`nothing_text` of its text)."""
    return nothing_text(result_text(block))


def nothing_text(text):
    """Whether a tool result came back with nothing: no output, or only lines saying the path
    is not there. What the sandbox returns for a path it hides; a result with any other line is
    output, which a reader opens (a command can name a live path and print only what its
    in-sandbox part found). An error result is read by its text like any other: a command that
    reads a live file and then fails (`cat live/file; which codex`, exit 127) exits non-zero and
    still printed the file, so a failure alone does not make it nothing. The `Exit code N` line,
    kiro's `Output:` header and the `<tool_use_error>` wrapper are not output."""
    text = re.sub(r"</?tool_use_error>", "", text or "").strip()
    if text in ("", "(Bash completed with no output)"):
        return True
    lines = [l for l in text.splitlines()
             if l.strip() and not re.match(r"Exit code\b|Output:\s*$", l, re.I)]
    return all("No such file or directory" in l or "cannot access" in l or "does not exist" in l
               or "File not found" in l or "No files found" in l for l in lines)


def claude_calls(f):
    """[(call id, tool, input, result block)] of a claude transcript, results joined by call id."""
    calls, results = [], {}
    with open(f, errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            msg = r.get("message") or {}
            if not isinstance(msg.get("content"), list):
                continue
            for b in msg["content"]:
                if not isinstance(b, dict):
                    continue
                if r.get("type") == "user" and b.get("type") == "tool_result":
                    results[b.get("tool_use_id")] = b
                elif r.get("type") == "assistant" and b.get("type") == "tool_use":
                    calls.append((b.get("id"), b.get("name"), b.get("input") or {}))
    return [(tid, name, inp, results.get(tid)) for tid, name, inp in calls]


def kiro_calls(f):
    """The same for a kiro run log (`history.acp_calls`): the result is the text the call printed."""
    return [(c["id"], c["title"] or c["kind"], c["input"], c["text"]) for c in history.acp_calls(f) if not c["system"]]


def calls_of(f, agent="claude"):
    """[(session id, tool, input, answered nothing or None when the call has no result)] of a transcript."""
    if agent == "kiro":
        sid = os.path.basename(os.path.dirname(f)) + "/" + os.path.basename(f)[:-5]
        return [(sid, name, inp, None if res is None else nothing_text(res)) for _, name, inp, res in kiro_calls(f)]
    sid = os.path.basename(f)[:-6]
    return [(sid, name, inp, None if res is None else answered_nothing(res)) for _, name, inp, res in claude_calls(f)]


def reached(dest, repo, files, tool=None, agent="claude"):
    """The tool calls in `files` (transcripts; a kiro replay's run logs) that name a live path
    outside `dest` (or look in the nix store for flake sources: `STORE_LOOK`), as
    (session id, tool, text, answered nothing). DEST, its exported tool and what the tool the
    replay runs under is for (its SKILL.md, scripts, templates and docs/artefacts.md) are cut out first,
    so a replay kept inside the live checkout, or run under it, does not count reading its own
    tool. The rest of a tool that is the live checkout (its `.dfs/` runs, items and worktrees)
    still counts. A path built at run time (`cd ..`, a variable) is not seen, so this is a
    floor. Under the sandbox a call is an attempt: `answered nothing` is true when its result
    was empty or an error (`answered_nothing`)."""
    dest = str(Path(dest).resolve())
    cut = [re.escape(dest + "-tool"), re.escape(dest) + r"(?![\w-])"]
    if tool:
        base = re.escape(str(Path(tool).resolve()))
        cut.append(base + r"/(?:SKILL\.md|README\.md|LICENCE\.txt|docs/artefacts\.md|(?:scripts|templates)(?![\w-]))")
    own = re.compile("(?:" + "|".join(cut) + ")")
    live = re.compile("(?:" + "|".join(re.escape(p) for p in live_paths(repo)) + "|" + STORE_LOOK + r")(?![\w-])")
    found = []
    for f in files:
        for sid, name, inp, nothing in calls_of(f, agent):
            text = " ".join(str(inp[k]) for k in REACH_FIELDS if inp.get(k))
            if live.search(own.sub("", text)):
                found.append((sid, name, text, bool(nothing)))
    return found


def original_lines(repo, task):
    """What the original run concluded, as strings a tool result can be searched for: each
    node's title and the head of its Determination, and the lines of each review and critic
    entry in its log (the findings: `W55.2 added: ...`), from the task file and its archive."""
    lines = set()
    for rel in (f".dfs/items/{task}.md", f".dfs/archive/items/{task}.md"):
        try:
            text = (Path(repo) / rel).read_text()
        except OSError:
            continue
        for m in re.finditer(r"(?m)^### (W\d+\.\d+ · .+)$|^Determination: (.{40,})$", text):
            lines.add((m.group(1) or m.group(2))[:60].strip())
        for m in re.finditer(r"(?m)^- \S+ · (?:review|critic) · .*\n((?:  .*\n?)*)", text):
            lines.update(l.strip()[:60] for l in m.group(1).splitlines() if len(l.strip()) >= 40)
    return lines


def answer_seen(dest, repo, task, files, agent="claude"):
    """The original's conclusions (`original_lines`) that appear in a tool result of a
    replay's sessions and are not in the replay's own record (its committed task file and
    commit messages), as {line: [session ids]}: a replay that read them was shown the answer,
    whether or not it used it. Looks at what a tool printed, not at what the session then
    did; a conclusion the replay printed from its own uncommitted task file and wrote
    identically counts as shown (negligible)."""
    own = git(dest, "log", "--all", "-p", "--format=%B", "--", ".dfs/items", check=False)
    own += git(dest, "log", "--all", "--format=%B", check=False)
    wanted = {l for l in original_lines(repo, task) if l not in own}
    seen = {}
    if agent == "kiro":   # a result is the text a call printed
        for f in files:
            sid = os.path.basename(os.path.dirname(f)) + "/" + os.path.basename(f)[:-5]
            for c in history.acp_calls(f):
                for w in wanted:
                    if w in c["text"] and sid not in seen.setdefault(w, []):
                        seen[w].append(sid)
        return seen
    for f in files:
        sid = os.path.basename(f)[:-6]
        with open(f, errors="replace") as fh:
            for line in fh:
                if '"tool_result"' not in line:
                    continue
                for w in wanted:
                    # a transcript writes `…` raw or as \u2026, so either spelling counts
                    if any(json.dumps(w, ensure_ascii=a)[1:-1] in line for a in (True, False)) \
                            and sid not in seen.setdefault(w, []):
                        seen[w].append(sid)
    return seen


def score(dest, task, repo=None):
    """The replay's score, with how many tool calls named the live checkout (`repo`, the
    checkout that owns this directory when not given) from outside DEST. Fails loudly on a
    DEST that is not this task's prepared replay (arguments swapped, a typo) or that has no
    transcript of the task, since a table of dashes reads as a replay that did nothing."""
    dest = Path(dest).resolve()
    try:
        info = json.loads((dest / ".git" / "replay.json").read_text())
    except (OSError, ValueError):
        raise SystemExit(f"replay: {dest} is not a prepared replay (no .git/replay.json); "
                         "usage: replay.py score DEST TASK")
    if info.get("task") != task:
        raise SystemExit(f"replay: {dest} replays {info.get('task')}, not {task}; "
                         "usage: replay.py score DEST TASK")
    agent = info.get("agent", "claude")
    if agent not in SCORABLE:
        raise SystemExit(f"replay: {dest} ran under {agent}, whose transcripts score cannot read yet "
                         f"(it reads {', '.join(SCORABLE)}); the replay's work is in {dest} and its run logs "
                         f"in {dest}/.dfs/runs")
    if agent == "kiro":
        # no transcript but the chain's own run logs, in the replay's .dfs/runs
        files = history._glob(os.path.join(glob.escape(str(dest)), ".dfs", "runs", "dfs_run_*", "run-*.json"))
        sessions, tasks = history.load_streams(files, kinds=history.session_kinds(*task_logs(dest, task)))
        where = f"{dest}/.dfs/runs"
    else:
        enc = re.sub(r"[/.]", "-", str(dest))
        repos = {enc: str(dest)}
        cfg = replay_config(dest)
        sessions, tasks, _, _ = history.load(transcripts(dest, cfg), repos, replays=True)
        files = history._glob(transcripts(dest, cfg))
        where = ", ".join(transcripts(dest, cfg))
    t = next((t for (p, n), t in tasks.items() if n == task), None)
    if t is None:
        raise SystemExit(f"replay: no session of {task} in the transcripts of {dest} "
                         f"({len(sessions)} sessions found under {where})")
    t["stopped"] = stopped_on(dest)
    t["finished"] = merged(dest, task)
    repo = repo or main_checkout()
    t["reach"] = reached(dest, repo, files, info.get("tool"), agent)
    t["outside"] = sum(1 for *_, nothing in t["reach"] if not nothing)   # answered with output
    t["blocked"] = len(t["reach"]) - t["outside"]
    t["answer"] = answer_seen(dest, repo, task, files, agent)
    return t


def task_logs(dest, task):
    """The texts of a replay's task file that hold the runner's log: the clone's, its worktree's
    and what `main` and the item's branch committed (a replay's run directories have no console.log,
    and the log is what says which session was a work, a review or a critic)."""
    dest, rel = Path(dest), f".dfs/items/{task}.md"
    texts = []
    for f in [dest / rel, *dest.glob(f".dfs/worktrees/*/{rel}")]:
        try:
            texts.append(f.read_text(errors="replace"))
        except OSError:
            pass
    return texts + [git(dest, "show", f"{ref}:{rel}", check=False) for ref in ("main", f"dfs/{task}")]


def merged(dest, task):
    """Whether the replay's branch reached main: the runner merges only after a review passes."""
    log = git(dest, "log", "main", "--format=%s", "-E", f"--grep=^{task}: merge")
    return bool(log.strip())


ROWS = [
    ("sessions", lambda t: sum(t["sessions"].values())),
    ("  work / review / critic", lambda t: "/".join(str(t["sessions"].get(k, 0)) for k in ("work", "review", "critic"))),
    # a credit is not a dollar: a kiro replay's cost is its own row, and the dollar rows say so
    ("dollars", lambda t: f"{t['usd']:.2f}" if t.get("unit", "usd") == "usd" else "n/a"),
    ("credits (kiro)", lambda t: (f"{t['credits']:.1f}" + (f"+{t['unpriced']}?" if t["unpriced"] else "")) if t.get("unit") == "credits" else "-"),
    ("nodes committed", lambda t: t["nodes"]),
    ("first review", lambda t: t["first_review"] or "none"),
    ("reviews with issues / rounds", lambda t: f"{t['review_issues']} / {t['review_ok'] + t['review_issues']}"),
    ("critic issues", lambda t: t["critic_issues"]),
    ("raises", lambda t: t["raises"]),
    ("refused commits", lambda t: t["refused"]),
    ("spent after a review said issues", lambda t: f"{t['after_issues']:.2f}" if t.get("unit", "usd") == "usd" else "n/a"),
    ("  credits after a review said issues", lambda t: f"{t['after_credits']:.1f}" if t.get("unit") == "credits" else "-"),
    ("calls naming a live path, answered nothing", lambda t: t.get("blocked", "-")),
    ("calls naming a live path, answered output", lambda t: t.get("outside", "-")),
    ("original's nodes shown in a result", lambda t: len(t["answer"]) if "answer" in t else "-"),
]


def compare(task, before, after):
    w = sys.stdout.write
    w(f"\n{task}: original against replay\n{'':46} {'original':>10} {'replay':>10}\n")
    for name, f in ROWS:
        w(f"{name:46} {str(f(before)) if before else '-':>10} {str(f(after)) if after else '-':>10}\n")
    if after:
        w(f"{'chain stopped on':46} {'':>10} {after.get('stopped') or '-':>10}\n")
        w(f"{'review passed and merged':46} {'':>10} {'yes' if after.get('finished') else 'no':>10}\n")


def main(argv):
    if not argv or argv[0] not in ("plan", "prepare", "run", "score", "replay", "candidates"):
        sys.stdout.write(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    opts = {"repo": main_checkout(), "cap": None, "tool": None, "tool-ref": None, "law": None, "bash-minutes": BASH_MINUTES, "agent": None}
    pos = []
    it = iter(rest)
    for a in it:
        if a.startswith("--") and a[2:] in opts:
            opts[a[2:]] = next(it)
        else:
            pos.append(a)
    repo = opts["repo"]
    if cmd == "candidates":
        rows = candidates(repo)
        sys.stdout.write(f"{len(rows)} tasks: original at most $3, first review said issues, a node commit to start before\n")
        for t in rows:
            sys.stdout.write(f"  {t['task']:6} ${t['usd']:5.2f}  sessions {sum(t['sessions'].values())}  "
                             f"nodes {t['nodes']}  review issues {t['review_issues']}/{t['review_issues'] + t['review_ok']}\n")
    elif cmd == "plan":
        task, = pos
        sha, text = starting_point(repo, task)
        before = original(repo, task)
        sys.stdout.write(f"{task} starts from {sha[:8]}; the task file as the author left it:\n\n{text}\n")
        compare(task, before, None)
    elif cmd == "prepare":
        task, dest = pos
        dest, tool = prepare(repo, task, dest, opts["tool"], opts["tool-ref"], opts["law"], opts["agent"])
        sys.stdout.write(f"prepared {dest} under tool {tool}, agent {configured_agent(repo, opts['agent'])}\n")
    elif cmd == "run":
        dest, task = pos
        return run(dest, task, opts["cap"], opts["tool"], opts["bash-minutes"], repo, opts["agent"])
    elif cmd == "score":
        dest, task = pos
        compare(task, original(repo, task), score(dest, task, repo))
    elif cmd == "replay":
        task, dest = pos
        agent = configured_agent(repo, opts["agent"])
        if agent not in SCORABLE:   # refuse before spending: run it with `prepare` and `run`, score by hand
            raise SystemExit(f"replay: the agent is {agent}, whose transcripts score cannot read yet; "
                             "use prepare and run, or --agent claude")
        dest, tool = prepare(repo, task, dest, opts["tool"], opts["tool-ref"], opts["law"], agent)
        before = original(repo, task)
        cap = opts["cap"] or (max(6, 2 * sum(before["sessions"].values())) if before else 6)
        run(dest, task, cap, tool, opts["bash-minutes"], repo, agent)
        compare(task, before, score(dest, task, repo))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
