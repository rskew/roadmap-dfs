#!/usr/bin/env python3
"""Run one implementation review, with a changed tool, on a copy of a finished replay tree.

  python3 docs/dfs-review-only.py REPLAYS_DIR TOOL OUT.jsonl [--only W36-c1c,...] [--jobs 2] [--suffix rv] [--blind]

For each replay `dfs-replay-<task>-<tag>` it copies the clone to `dfs-replay-<task>-<tag>-SUFFIX` (rv) (the
finished tree, its task file and log as the passing review left them), builds the replay's sandbox
around the copy (replay.py of TOOL: proot, a fresh home, no live checkout), and inside it asks TOOL's
`state.py --review-brief` for the briefing and gives that to one kiro session with the lead run.sh
gives a reviewer. The session may log a verdict into the copy's task file, which is read back after.
One JSON line per review: replay, the new nodes and verdict the copy's task file holds, the session's
credits if kiro printed them, the raw output. The originals are not touched.

--blind (W153.20, change E8): the copy's task file is stripped of its `## Summary` section and every
`Determination:` line, and committed so that `git status` does not show the removal; use it with a TOOL
whose brief also leaves them out (docs/dfs-w153-blind-tool.py). The record then carries `leaked`: how
many of the original's Determinations (their first 60 characters) and whether the Summary's first
60 characters appear in the session's output (a tool result that read them), and `git_log_calls`: how
many of the session's commands ran `git log`, `git show` or `git diff`, which can still reach the
subjects of the worker's commits.
"""
import concurrent.futures as cf, json, os, re, shutil, subprocess, sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LEAD = ("You are the implementation reviewer for task {t}. Everything below is your briefing: the "
        "roadmap's law verbatim, then what to review and how to record it.")
INNER = ('brief="$(python3 "$DFS_TOOL/scripts/state.py" --review-brief {t})" || exit 3; '
         'exec "$KIRO_CMD" chat --v3 --output-format stream-json --no-interactive --trust-all-tools "{lead}\n\n$brief"')


def strip_text(text):
    """The task file without its Summary section and its Determination lines."""
    text = re.sub(r"(?ms)^## Summary\n.*?(?=^## )", "", text)
    return re.sub(r"(?m)^Determination: .*\n", "", text)


def leaks(original, raw):
    """(Determinations whose first 60 characters are in `raw`, the Summary's, as 0 or 1)."""
    dets = re.findall(r"(?m)^Determination: (.+)$", original)
    summ = re.search(r"(?ms)^## Summary\n\n(.+?)\n\n(?=## )", original)
    return (sum(1 for d in dets if d[:60] in raw), int(bool(summ and summ.group(1)[:60] in raw)), len(dets))


def review(root, tool, replay_mod, name, suffix, blind=False):
    task, tag = name.rsplit("-", 1)
    src = root / f"dfs-replay-{name}"
    dst = root / f"dfs-replay-{name}-{suffix}"
    if dst.exists():
        replay_mod.remove_tree(dst)
        replay_mod.remove_tree(replay_mod.box_of(dst))
    shutil.copytree(src, dst, symlinks=True)
    taskfile = dst / ".dfs" / "items" / f"{task}.md"
    original = taskfile.read_text()
    if blind:
        taskfile.write_text(strip_text(original))
        subprocess.run(["git", "-C", str(dst), "add", str(taskfile)], check=True)
        subprocess.run(["git", "-C", str(dst), "-c", "core.hooksPath=/dev/null", "-c", "user.name=dfs", "-c", "user.email=dfs@dfs.invalid",
                        "commit", "-q", "-m", f"{task}: task file as the reviewer is given it"], check=True)
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
    log = taskfile.read_text()
    before = strip_text(original) if blind else original
    added = log[len(before):] if log.startswith(before) else log
    out = dict(replay=name, arm="blind" if blind else "informed", rc=r.returncode, credits=credits,
               task_file_added=added, raw_tail=raw[-3000:])
    if blind:
        dets, summ, total = leaks(original, raw)
        out["leaked"] = dict(determinations=dets, of=total, summary=summ)
    out["git_log_calls"] = len(re.findall(r"git(?: -C \S+)? (?:log|show|diff)\b", raw))
    out["raw_len"] = len(raw)
    (root / f"raw-{name}-{suffix}.txt").write_text(raw)
    return out


def main(argv):
    root, tool, out = Path(argv[0]).resolve(), Path(argv[1]).resolve(), Path(argv[2])
    opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    only = [x for x in opt("--only", "").split(",") if x]
    jobs, suffix, blind = int(opt("--jobs", 2)), opt("--suffix", "rv"), "--blind" in argv
    sys.path.insert(0, str(tool / "scripts"))
    import replay as replay_mod, history
    globals()["history"] = history
    with cf.ThreadPoolExecutor(jobs) as ex, out.open("a") as f:
        for res in ex.map(lambda n: review(root, tool, replay_mod, n, suffix, blind), only):
            f.write(json.dumps(res) + "\n"); f.flush()
            print(res["replay"], res["rc"], res["credits"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
