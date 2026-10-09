#!/usr/bin/env python3
"""Start a roadmap in the repo you are standing in, or finish starting one.

Run it from the ROOT of the project's git repo (the work root is the current
directory, `paths.py`). It creates only what is missing and never overwrites:

    .dfs/ROADMAP.md          the law, from templates/ROADMAP.md: edit it to suit
    .dfs/items/              where tasks go (`run.sh --open` writes them)
    .dfs/.gitignore          keeps `runs/`, the chain logs, and `artefact-state/` out of git
    .dfs/playwright-shell    only with --playwright-shell <flake ref>: the dev shell
                             that supplies playwright for the artefact check
    <hooks>/pre-commit       the roadmap guard (`hook.sh`), in the hooks directory
    <hooks>/pre-merge-commit git uses here (core.hooksPath, else .git/hooks)

`.dfs/` may be committed with the project or gitignored whole (`dfs_paths.ignored`):
ignored, it is planning only, the hooks never fire, and git history is the record
of the walk, one commit per node.

A hook that already exists is left alone and the lines to add are printed instead,
since it is the project's. Hooks in .git/hooks belong to this clone only, so run
this again in every clone; it is idempotent.

From the flake, with nothing installed: `nix run github:rskew/roadmap-dfs#init`.
The hooks and the next steps it prints then go through the flake too (`nix run
$DFS_FLAKE#hook`, default dfs_paths.FLAKE).

Usage:
    init.py [--no-hooks] [--playwright-shell <flake ref>]
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths as dfs_paths  # noqa: E402

HOOKS = ("pre-commit", "pre-merge-commit")
# `dfs_hook`, `dfs-hook` are the names a hook written before they lost their prefix has.
MARKERS = ("dfs_hook", "dfs-hook", "scripts/hook.sh")


def git(*args):
    return subprocess.run(["git", *args], cwd=dfs_paths.work_root(), capture_output=True,
                          text=True)


def tool_default():
    """How a hook should spell the tool when DFS_TOOL is unset: relative when the tool
    sits inside this repo (a vendored copy, which moves with the repo), absolute when
    it is a checkout elsewhere on this machine, and nothing when it is a nix store
    path, which the next garbage collection deletes: then `hook` on PATH is the
    way in, else the flake."""
    tool = dfs_paths.TOOL_ROOT
    if str(tool).startswith("/nix/store/"):
        return ""
    try:
        return str(tool.relative_to(dfs_paths.work_root()))
    except ValueError:
        return str(tool)


def snippet(hook):
    """The lines a hook needs: find the tool, run its guard, and say so when neither is
    here rather than skip in silence. Exit status is the guard's, so a violation stops
    the commit. The flake is the last resort, and only for a commit that touches
    `.dfs`, since it may fetch: every other commit stays as fast as without it."""
    base = " HEAD" if hook == "pre-merge-commit" else ""
    return ("# The roadmap guard (roadmap-dfs). DFS_TOOL, else the path below, else\n"
            "# `dfs-hook` on PATH, else the tool's flake (DFS_FLAKE).\n"
            'dfs_tool="${DFS_TOOL:-%s}"\n'
            'if [ -n "$dfs_tool" ] && [ -f "$dfs_tool/scripts/hook.sh" ]; then\n'
            '  sh "$dfs_tool/scripts/hook.sh" %s || exit 1\n'
            "elif command -v dfs-hook >/dev/null 2>&1; then\n"
            "  dfs-hook %s || exit 1\n"
            'elif [ -n "$(git diff --cached --name-only%s -- .dfs)" ]; then\n'
            "  if command -v nix >/dev/null 2>&1; then\n"
            '    nix run "${DFS_FLAKE:-%s}#hook" -- %s || exit 1\n'
            "  else\n"
            '    echo "%s: the roadmap guard did not run: no roadmap-dfs found (set DFS_TOOL)" >&2\n'
            "  fi\n"
            "fi\n" % (tool_default(), hook, hook, base, dfs_paths.FLAKE, hook, hook))


def say(what, path):
    print("  %-9s %s" % (what, dfs_paths.rel(path)))


def write_new(path, text, mode=None):
    if path.exists():
        say("kept", path)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode:
        path.chmod(path.stat().st_mode | mode)
    say("wrote", path)
    return True


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    top = git("rev-parse", "--show-toplevel")
    if top.returncode != 0:
        print("dfs_init: %s is not in a git repo. Each node ends in a commit, so "
              "start one with `git init` first." % dfs_paths.work_root(),
              file=sys.stderr)
        return 2
    if Path(top.stdout.strip()).resolve() != dfs_paths.work_root().resolve():
        print("dfs_init: run this from the repo's root, %s: the hooks judge `.dfs` there."
              % top.stdout.strip(), file=sys.stderr)
        return 2
    shell = None
    if "--playwright-shell" in argv:
        i = argv.index("--playwright-shell")
        if i + 1 >= len(argv):
            print("dfs_init: --playwright-shell takes a flake ref, e.g. .#e2e", file=sys.stderr)
            return 2
        shell = argv[i + 1]

    print("dfs_init: %s" % dfs_paths.state())
    write_new(dfs_paths.roadmap(), (dfs_paths.TEMPLATES / "ROADMAP.md").read_text())
    if dfs_paths.read_title():
        say("kept", dfs_paths.title_file())
    else:
        dfs_paths.write_title(dfs_paths.git_project_name())
        say("made", dfs_paths.title_file())
    if dfs_paths.items().is_dir():
        say("kept", dfs_paths.items())
    else:
        dfs_paths.items().mkdir(parents=True)
        say("made", dfs_paths.items())
    ignore = dfs_paths.state() / ".gitignore"
    have = ignore.read_text() if ignore.exists() else ""
    wanted = (("runs/", "chain logs: one directory per run.sh run"),
              ("artefact-state/", "what a reader left on an artefact page: local, not the roadmap"))
    missing = [(name, why) for name, why in wanted if name not in have.split()]
    if not missing:
        say("kept", ignore)
    else:
        ignore.write_text(have + ("" if not have or have.endswith("\n") else "\n")
                          + "".join("# %s\n%s\n" % (why, name) for name, why in missing))
        say("wrote", ignore)
    if shell is not None:
        dfs_paths.playwright_shell().write_text(shell + "\n")
        say("wrote", dfs_paths.playwright_shell())

    hooks_to_add = []
    if "--no-hooks" not in argv:
        hooks = Path(git("rev-parse", "--git-path", "hooks").stdout.strip())
        hooks = hooks if hooks.is_absolute() else dfs_paths.work_root() / hooks
        for hook in HOOKS:
            path = hooks / hook
            if path.exists():
                if any(m in path.read_text() for m in MARKERS):
                    say("kept", path)
                else:
                    say("left", path)
                    hooks_to_add.append((path, hook))
                continue
            write_new(path, "#!/usr/bin/env sh\n" + snippet(hook),
                      stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    for path, hook in hooks_to_add:
        print("\n%s is the project's own, so it was not edited. Add these lines to it:\n"
              % dfs_paths.rel(path))
        print("    " + snippet(hook).rstrip("\n").replace("\n", "\n    "))

    print("""
Next:
  1. Read .dfs/ROADMAP.md and make its law yours; every session reads it whole.
  2. %s
  3. Open a task:      %s
     Work it:          %s
     Or the screen:    %s
  Run all of them from here, the repo's root.
  Artefacts are served from .dfs/artefacts on port %s (DFS_ARTEFACT_PORT):
     python3 -m http.server %s --directory .dfs/artefacts""" % (
        ".dfs/ is gitignored here, so there is nothing to commit: it stays planning,\n"
        "     and each node's commit carries its code alone." if dfs_paths.ignored() else
        "Commit what it wrote under .dfs/ (or gitignore .dfs/ to keep it as planning).",
        dfs_paths.command("run.sh", "--open"),
        dfs_paths.command("run.sh", "<task>", "[sessions]"),
        dfs_paths.command("tui.py"), os.environ.get("DFS_ARTEFACT_PORT") or 3016,
        os.environ.get("DFS_ARTEFACT_PORT") or 3016))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
