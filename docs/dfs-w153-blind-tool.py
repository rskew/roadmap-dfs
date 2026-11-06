#!/usr/bin/env python3
"""Make the two tool copies the E8 review-only run needs, and check their briefs on one tree.

  python3 docs/dfs-w153-blind-tool.py TOOL OUTDIR [--check REPLAY_CLONE TASK]

TOOL is an installed roadmap-dfs (a directory holding scripts/, SKILL.md, templates/). OUTDIR gets
`informed/` (a verbatim copy) and `blind/` (the same copy with two edits to scripts/):

  * tree.py `outline` (the tree in the review brief) leaves out each node's Determination;
  * state.py `review_brief` lists each commit by sha and node id and leaves out its subject, since
    a commit subject carries what the node established.

The copy is outside /nix/store so that the sandbox binds its entries one by one (replay.py
TOOL_ENTRIES) and the edits are what the reviewer's `$DFS_TOOL/scripts/state.py` runs. Each edit
is an exact string replacement that must match once, so a tool that has moved on fails here
rather than quietly giving a brief that still holds the Determinations.

`--check` runs `state.py --review-brief TASK` of both copies inside the clone (a finished replay
tree, or a copy of it) and prints whether the Determination of any node, or the Summary, is in
each brief.
"""
import re, shutil, subprocess, sys
from pathlib import Path

OUTLINE_OLD = '''            det = nd["fields"].get("Determination", "")
            lines.append("%s%s [%s] %s%s" % ("  " * d, nd["id"], st, nd["title"],
                                             (" — " + det) if det else ""))'''
OUTLINE_NEW = '''            lines.append("%s%s [%s] %s" % ("  " * d, nd["id"], st, nd["title"]))'''
COMMITS_OLD = '''            out += ["- %s %s (%s)" % (sha[:10], subj, tag)]'''
COMMITS_NEW = '''            out += ["- %s %s (%s)" % (sha[:10], nd["id"], tag)]'''


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"blind tool: {path.name} holds {text.count(old)} copies of the text to replace, expected 1")
    path.write_text(text.replace(old, new))


def copy_tool(src, dst):
    if dst.exists():
        subprocess.run(["chmod", "-R", "u+w", str(dst)], check=True)
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=True)
    subprocess.run(["chmod", "-R", "u+w", str(dst)], check=True)


def brief(tool, clone, task):
    return subprocess.run([sys.executable, str(tool / "scripts" / "state.py"), "--review-brief", task],
                          cwd=clone, capture_output=True, text=True, check=True).stdout


def determinations(clone, task):
    text = (Path(clone) / ".dfs" / "items" / f"{task}.md").read_text()
    return [m.strip() for m in re.findall(r"(?m)^Determination: (.+)$", text)]


def main(argv):
    src, out = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    copy_tool(src, out / "informed")
    copy_tool(src, out / "blind")
    replace_once(out / "blind" / "scripts" / "tree.py", OUTLINE_OLD, OUTLINE_NEW)
    replace_once(out / "blind" / "scripts" / "state.py", COMMITS_OLD, COMMITS_NEW)
    print("tools written to", out)
    if "--check" in argv:
        clone, task = Path(argv[argv.index("--check") + 1]), argv[argv.index("--check") + 2]
        dets = determinations(clone, task)
        for name in ("informed", "blind"):
            b = brief(out / name, clone, task)
            found = sum(1 for d in dets if d[:60] in b)
            print(f"{name}: brief {len(b.split())} words; {found} of {len(dets)} Determinations (first 60 chars) in it")


if __name__ == "__main__":
    main(sys.argv[1:])
