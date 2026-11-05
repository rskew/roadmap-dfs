#!/usr/bin/env python3
"""Run one implementation review, with a changed tool, on a copy of a finished replay tree.

  python3 docs/dfs-review-only.py REPLAYS_DIR TOOL OUT.jsonl [--only W36-c1c,...] [--jobs 2] [--suffix rv]

For each replay `dfs-replay-<task>-<tag>` it copies the clone to `dfs-replay-<task>-<tag>-SUFFIX` (rv) (the
finished tree, its task file and log as the passing review left them), builds the replay's sandbox
around the copy (replay.py of TOOL: proot, a fresh home, no live checkout), and inside it asks TOOL's
`state.py --review-brief` for the briefing and gives that to one kiro session with the lead run.sh
gives a reviewer. The session may log a verdict into the copy's task file, which is read back after.
One JSON line per review: replay, the new nodes and verdict the copy's task file holds, the session's
credits if kiro printed them, the raw output. The originals are not touched.
"""
import concurrent.futures as cf, json, os, re, shutil, subprocess, sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LEAD = ("You are the implementation reviewer for task {t}. Everything below is your briefing: the "
        "roadmap's law verbatim, then what to review and how to record it.")
INNER = ('brief="$(python3 "$DFS_TOOL/scripts/state.py" --review-brief {t})" || exit 3; '
         'exec "$KIRO_CMD" chat --v3 --output-format stream-json --no-interactive --trust-all-tools "{lead}\n\n$brief"')


def review(root, tool, replay_mod, name, suffix):
    task, tag = name.rsplit("-", 1)
    src = root / f"dfs-replay-{name}"
    dst = root / f"dfs-replay-{name}-{suffix}"
    if dst.exists():
        replay_mod.remove_tree(dst)
        replay_mod.remove_tree(replay_mod.box_of(dst))
    shutil.copytree(src, dst, symlinks=True)
    replay_mod.make_box(dst, "kiro")
    bins = replay_mod.tool_bins(replay_mod.main_checkout(), "kiro")
    env = replay_mod.sandbox_env(tool, bins, replay_mod.BASH_MINUTES, "kiro")
    inner = ["bash", "-c", INNER.format(t=task, lead=LEAD.format(t=task))]
    argv = replay_mod.sandbox_argv(dst, tool, inner, bins, env, "kiro", docs=replay_mod.tool_docs(dst, tool))
    r = subprocess.run(argv, cwd=dst, env={"PROOT_TMP_DIR": str(replay_mod.box_of(dst) / "tmp"),
                                          "PATH": os.environ.get("PATH", "")},
                       capture_output=True, text=True, errors="replace", timeout=2400)
    raw = ANSI.sub("", r.stdout + "\n" + r.stderr)
    stream = dst / ".git" / "review.json"          # the run log, in the form history.stream_session reads
    stream.write_text(raw)
    credits = history.stream_session(str(stream)).get("credits")
    log = (dst / ".dfs" / "items" / f"{task}.md").read_text()
    before = (src / ".dfs" / "items" / f"{task}.md").read_text()
    added = log[len(before):] if log.startswith(before) else log
    return dict(replay=name, rc=r.returncode, credits=credits, task_file_added=added, raw_tail=raw[-3000:])


def main(argv):
    root, tool, out = Path(argv[0]).resolve(), Path(argv[1]).resolve(), Path(argv[2])
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    only = [x for x in opt("--only", "").split(",") if x]
    jobs, suffix = int(opt("--jobs", 2)), opt("--suffix", "rv")
    sys.path.insert(0, str(tool / "scripts"))
    import replay as replay_mod, history
    globals()["history"] = history
    with cf.ThreadPoolExecutor(jobs) as ex, out.open("a") as f:
        for res in ex.map(lambda n: review(root, tool, replay_mod, n, suffix), only):
            f.write(json.dumps(res) + "\n"); f.flush()
            print(res["replay"], res["rc"], res["credits"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
