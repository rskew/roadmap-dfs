#!/usr/bin/env python3
"""Run a finished task again from where it began, under a system you changed, and score it.

A finished task's starting point is recoverable from git: the repo as it was the commit
before the task's first node, and a task file holding what the author wrote (its title,
Goal and Background) and nothing the sessions added. This clones the repo there, lets a
chain (run.sh) work the task, and measures the result with history.py's `session()` and
`tasks_of()`, the code that measured the original, so the two columns are comparable.

  python3 <scripts>/replay.py plan    TASK [--repo PATH]
  python3 <scripts>/replay.py prepare TASK DEST [--repo PATH] [--tool DIR | --tool-ref REV] [--law FILE]
  python3 <scripts>/replay.py run     DEST TASK [--cap N] [--tool DIR]
  python3 <scripts>/replay.py score   DEST TASK [--repo PATH]
  python3 <scripts>/replay.py replay  TASK DEST [--cap N] [... prepare's flags]   (all three)

The system that changes is the TOOL (`--tool DIR`: a roadmap-dfs checkout or store path,
or `--tool-ref REV`: a revision of the repo being replayed, when that repo is roadmap-dfs
itself, exported with git archive; SKILL.md, the scripts and the templates all come from
it) and optionally the LAW (`--law FILE`, copied over `.dfs/ROADMAP.md`). With neither, the
replay runs the current tool under the law of the starting commit.

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
IDENTITY = ("-c", "user.name=dfs replay", "-c", "user.email=replay@dfs.invalid")


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


def transcripts(checkout):
    """Glob for the transcripts of a checkout and of every worktree of it."""
    enc = re.sub(r"[/.]", "-", str(Path(checkout).resolve()))
    return os.path.join(config_dir(), "projects", enc + "*", "*.jsonl")


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
    for (project, name), t in tasks.items():
        if name == task:
            return t
    return None


def prepare(repo, task, dest, tool=None, tool_ref=None, law=None):
    dest = Path(dest).resolve()
    if "dfs-replay-" not in dest.name:
        raise SystemExit("replay: DEST's name must contain 'dfs-replay-' (history.py skips it by that)")
    if dest.exists():
        raise SystemExit(f"replay: {dest} exists; a replay starts from nothing")
    sha, text = starting_point(repo, task)
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(repo), str(dest)], check=True)
    git(dest, "checkout", "-q", "-B", "main", sha)
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
    git(dest, *IDENTITY, "commit", "-q", "--allow-empty", "-m",
        f"replay: {task} as the author left it, from {sha[:8]}", "--no-verify")
    (dest / ".git" / "replay.json").write_text(json.dumps({"task": task, "from": sha, "tool": str(tool)}))
    return dest, tool


def export_tool(repo, rev, into):
    into.mkdir(parents=True)
    tar = subprocess.run(["git", "archive", rev], cwd=repo, capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(into)], input=tar, check=True)
    return into


def run(dest, task, cap=None, tool=None):
    dest = Path(dest).resolve()
    info = json.loads((dest / ".git" / "replay.json").read_text())
    tool = Path(tool or info["tool"])
    env = {**os.environ, "DFS_TOOL": str(tool)}
    cmd = ["bash", str(tool / "scripts" / "run.sh"), task, str(cap or 6)]
    # The chain's own words are kept beside the replay: its last "dfs_run: DONE|BLOCKED|CAP"
    # line is why it stopped, which the runner's meta.json (the task's status) does not say.
    with subprocess.Popen(cmd, cwd=dest, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace") as p, open(dest / ".git" / "replay.out", "w") as log:
        for line in p.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
    return p.returncode


def stopped_on(dest):
    """Why the chain stopped: DONE, BLOCKED or CAP (empty when it was never run here)."""
    try:
        text = (Path(dest) / ".git" / "replay.out").read_text()
    except OSError:
        return ""
    found = re.findall(r"dfs_run: (DONE|BLOCKED|CAP)\b", text)
    return found[-1] if found else "other"


def score(dest, task):
    dest = Path(dest).resolve()
    enc = re.sub(r"[/.]", "-", str(dest))
    repos = {enc: str(dest)}
    sessions, tasks, _, _ = history.load(transcripts(dest), repos, replays=True)
    t = next((t for (p, n), t in tasks.items() if n == task), None)
    if t:
        t["stopped"] = stopped_on(dest)
        t["finished"] = merged(dest, task)
    return t


def merged(dest, task):
    """Whether the replay's branch reached main: the runner merges only after a review passes."""
    log = git(dest, "log", "main", "--format=%s", "-E", f"--grep=^{task}: merge")
    return bool(log.strip())


ROWS = [
    ("sessions", lambda t: sum(t["sessions"].values())),
    ("  work / review / critic", lambda t: "/".join(str(t["sessions"].get(k, 0)) for k in ("work", "review", "critic"))),
    ("dollars", lambda t: f"{t['usd']:.2f}"),
    ("nodes committed", lambda t: t["nodes"]),
    ("first review", lambda t: t["first_review"] or "none"),
    ("reviews with issues / rounds", lambda t: f"{t['review_issues']} / {t['review_ok'] + t['review_issues']}"),
    ("critic issues", lambda t: t["critic_issues"]),
    ("raises", lambda t: t["raises"]),
    ("refused commits", lambda t: t["refused"]),
    ("spent after a review said issues", lambda t: f"{t['after_issues']:.2f}"),
]


def compare(task, before, after):
    w = sys.stdout.write
    w(f"\n{task}: original against replay\n{'':36} {'original':>10} {'replay':>10}\n")
    for name, f in ROWS:
        w(f"{name:36} {str(f(before)) if before else '-':>10} {str(f(after)) if after else '-':>10}\n")
    if after:
        w(f"{'chain stopped on':36} {'':>10} {after.get('stopped') or '-':>10}\n")
        w(f"{'review passed and merged':36} {'':>10} {'yes' if after.get('finished') else 'no':>10}\n")


def main(argv):
    if not argv or argv[0] not in ("plan", "prepare", "run", "score", "replay"):
        sys.stdout.write(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    opts = {"repo": main_checkout(), "cap": None, "tool": None, "tool-ref": None, "law": None}
    pos = []
    it = iter(rest)
    for a in it:
        if a.startswith("--") and a[2:] in opts:
            opts[a[2:]] = next(it)
        else:
            pos.append(a)
    repo = opts["repo"]
    if cmd == "plan":
        task, = pos
        sha, text = starting_point(repo, task)
        before = original(repo, task)
        sys.stdout.write(f"{task} starts from {sha[:8]}; the task file as the author left it:\n\n{text}\n")
        compare(task, before, None)
    elif cmd == "prepare":
        task, dest = pos
        dest, tool = prepare(repo, task, dest, opts["tool"], opts["tool-ref"], opts["law"])
        sys.stdout.write(f"prepared {dest} under tool {tool}\n")
    elif cmd == "run":
        dest, task = pos
        return run(dest, task, opts["cap"], opts["tool"])
    elif cmd == "score":
        dest, task = pos
        compare(task, original(repo, task), score(dest, task))
    elif cmd == "replay":
        task, dest = pos
        dest, tool = prepare(repo, task, dest, opts["tool"], opts["tool-ref"], opts["law"])
        before = original(repo, task)
        cap = opts["cap"] or (max(6, 2 * sum(before["sessions"].values())) if before else 6)
        run(dest, task, cap, tool)
        compare(task, before, score(dest, task))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
