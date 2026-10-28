#!/usr/bin/env python3
"""What past dfs sessions did and what it cost, mined from every project they ran in.

metrics.py asks how big a session's context gets; this asks what the sessions were FOR
and how their work turned out, so that a change to the skill, the law or the runner can
be judged against the history it would have changed. It reads only what is already on
disk and writes nothing:

- Claude Code's transcripts (`$CLAUDE_CONFIG_DIR` or `~/.claude`, `projects/*/*.jsonl`):
  one per session, in a directory named after its checkout or worktree, so the project
  is the directory name with any `--dfs-worktrees-W<n>` taken off.
- Each runner session's `run-N.json` under `<repo>/.dfs/runs`, where the repo is on this
  box: `claude -p`'s own `total_cost_usd`, joined to its transcript by session id and
  used to put a dollar figure on the sessions that have none.
- A project's task files and archive, where its checkout is on this box: the author's
  answers, corrections and accepts, which no transcript of a session carries.

  python3 <scripts>/history.py [--projects GLOB] [--repo PROJECT=PATH ...] [--json OUT]

`session(path)` and `load(pattern)` measure transcripts, so a replay (replay.py) is scored
by the same code as the history it is compared with. A replay's transcripts sit in a
directory named `...-dfs-replay-...`, which the default pattern leaves out: a replay is an
experiment on a task, not another task.
"""
import collections
import glob
import json
import os
import re
import statistics as st
import sys

# A session's kind and task are in its first prompt, whichever wording of the briefing
# it was given: the runner's prompts have changed shape several times since 2026-09.
KINDS = [
    ("review", re.compile(r"implementation reviewer for task (W\d+)")),
    ("critic", re.compile(r"You are the critic for task (W\d+)")),
    ("work", re.compile(r"SKILL\.md\. (?:Work task|Pick) (W\d+)")),
    ("work", re.compile(r"Read \.dfs/ROADMAP\.md and task (W\d+)'s parts")),
    ("chat", re.compile(r"Discuss the project with the author()")),
]
REPLAY = "-dfs-replay-"
VERDICT = re.compile(r"tree\.py\b.*?\blog (W\d+) (review|critic|raise|backtrack)\b ?(ok|issues)?")
STARTED = re.compile(r"tree\.py\b.*?\bstart (W\d+) (W[\d.]+(?:@\S+)?)")
SUBJECT = re.compile(r"""git\b(?:[^|;&'"]|"[^"]*"|'[^']*')*?\bcommit\b.*?(?:-m\s+(['"])(.+?)\1|<<-?\s*'?EOF'?\s*\n(.+?)\n)""", re.S)
REFUSED = "dfs_check: the roadmap broke its own rules"
# What git prints when a commit did not happen, wherever in a chained command it failed:
# `git commit ...; tree.py ...` exits with the last step, so the tool result is no error.
# A `[branch sha] subject` line shows a commit that did happen, e.g. a retry that worked.
FAILED = re.compile(r"Author identity unknown|^(?:fatal|error): |^nothing (?:added )?to commit"
                    r"|^no changes added to commit", re.M)
MADE = re.compile(r"^\[[^\]\n]+ [0-9a-f]{7,}\]", re.M)
# Relative prices, in input-token units: a cache read is a tenth of an input token, a
# one-hour cache write two, an output token five. Turned into dollars by the ratio the
# runner's own `total_cost_usd` shows (see `calibrate`), so only the ratios are assumed.
WEIGHT = {"input": 1.0, "read": 0.1, "write": 2.0, "output": 5.0}


def project_of(dirname):
    return re.sub(r"--dfs-worktrees-W\d+$", "", dirname)


def _text(content):
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict))


def session(path):
    """One transcript's record: what it was for, what it did, what it spent."""
    rec = {"sid": os.path.basename(path)[:-6],
           "project": project_of(os.path.basename(os.path.dirname(path))),
           "kind": "other", "task": None, "start": None, "end": None, "model": None,
           "responses": 0, "peak": 0, "input": 0, "read": 0, "write": 0, "output": 0,
           "tools": collections.Counter(), "reads": 0, "rereads": 0, "errors": 0,
           "commits": [], "refused": 0, "started": [], "verdicts": []}
    seen, read_paths, pending = set(), set(), {}
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = r.get("timestamp")
            if t:
                rec["start"] = rec["start"] or t
                rec["end"] = t
            msg = r.get("message") or {}
            if r.get("type") == "user":
                if rec["kind"] == "other" and rec["task"] is None and isinstance(msg.get("content"), (str, list)):
                    first = _text(msg["content"])
                    if first and not first.startswith("<"):
                        for kind, pat in KINDS:
                            m = pat.search(first)
                            if m:
                                rec["kind"], rec["task"] = kind, m.group(1) or None
                                break
                        rec["task"] = rec["task"] or ""
                for b in msg.get("content") or []:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    out = _text(b.get("content"))
                    rec["errors"] += bool(b.get("is_error"))
                    if pending.get(b.get("tool_use_id")) == "commit":
                        refused = REFUSED in out
                        rec["refused"] += refused
                        if rec["commits"]:
                            rec["commits"][-1]["ok"] = not (b.get("is_error") or refused) and \
                                (bool(MADE.search(out)) or not FAILED.search(out))
                continue
            if r.get("type") != "assistant":
                continue
            u = msg.get("usage") or {}
            if u and msg.get("id") not in seen:
                seen.add(msg.get("id"))
                rec["model"] = msg.get("model") or rec["model"]
                rec["responses"] += 1
                rd, wr = u.get("cache_read_input_tokens") or 0, u.get("cache_creation_input_tokens") or 0
                rec["peak"] = max(rec["peak"], rd + wr)
                rec["input"] += u.get("input_tokens") or 0
                rec["read"] += rd
                rec["write"] += wr
                rec["output"] += u.get("output_tokens") or 0
            for b in msg.get("content") or []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name"), b.get("input") or {}
                rec["tools"][name] += 1
                cmd = inp.get("command", "") if name == "Bash" else ""
                target = inp.get("file_path") if name == "Read" else None
                if target:
                    rec["reads"] += 1
                    key = (target, inp.get("offset"), inp.get("limit"))
                    rec["rereads"] += key in read_paths
                    read_paths.add(key)
                for m in VERDICT.finditer(cmd):
                    rec["verdicts"].append((m.group(2), m.group(3) or ""))
                for m in STARTED.finditer(cmd):
                    rec["started"].append(m.group(2))
                m = SUBJECT.search(cmd)
                if m and "--dry-run" not in cmd:
                    subj = (m.group(2) or m.group(3) or "").strip().splitlines()[0]
                    rec["commits"].append({"subject": subj[:160], "ok": None})
                    pending[b.get("id")] = "commit"
    rec["tools"] = dict(rec["tools"])
    rec["units"] = sum(rec[k] * w for k, w in WEIGHT.items())
    return rec


def runner_costs(repo):
    """{session id: total_cost_usd} from a repo's runner directories."""
    out = {}
    for f in glob.glob(os.path.join(repo, ".dfs", "runs", "dfs_run_*", "run-*.json")):
        try:
            j = json.load(open(f))
        except (ValueError, OSError):
            continue
        if isinstance(j, dict) and j.get("session_id") and j.get("total_cost_usd") is not None:
            out[j["session_id"]] = float(j["total_cost_usd"])
    return out


def calibrate(sessions, costs):
    """Dollars per weighted unit, per model, from sessions the runner priced itself."""
    ratios = collections.defaultdict(list)
    for s in sessions:
        c = costs.get(s["sid"])
        if c and s["units"]:
            ratios[s["model"]].append(c / s["units"])
    every = [r for v in ratios.values() for r in v]
    rate = {m: st.median(v) for m, v in ratios.items() if len(v) >= 5}
    return rate, (st.median(every) if every else None), {m: len(v) for m, v in ratios.items()}


def author_log(repo):
    """{task: Counter of log kinds} from a checkout's task files and archive."""
    out = collections.defaultdict(collections.Counter)
    for f in glob.glob(os.path.join(repo, ".dfs", "items", "*.md")) + \
            glob.glob(os.path.join(repo, ".dfs", "archive", "items", "*.md")):
        task = os.path.basename(f)[:-3]
        for m in re.finditer(r"^- \d{4}-[\dT:-]+Z · (\w+)(?: · (\w+))?(?: · (\w+))?", open(f).read(), re.M):
            kind = m.group(1)
            if kind in ("review", "critic"):
                kind += ":" + (m.group(2) or "")
            out[task][kind] += 1
    return out


def tasks_of(sessions, logs):
    """Per (project, task): what its sessions spent and how its work was judged."""
    out = {}
    for s in sorted(sessions, key=lambda s: s["start"] or ""):
        if not s["task"]:
            continue
        t = out.setdefault((s["project"], s["task"]), {
            "project": s["project"], "task": s["task"], "sessions": collections.Counter(),
            "usd": 0.0, "units": 0.0, "nodes": 0, "refused": 0, "rereads": 0,
            "first_review": None, "review_ok": 0, "review_issues": 0, "critic_issues": 0, "raises": 0,
            "after_issues": 0.0, "start": s["start"], "end": s["end"]})
        # Spent once a review has already found the work wanting: the price of the
        # first finished tree not being finished.
        t["after_issues"] += (s["usd"] or 0) if t["review_issues"] else 0
        t["sessions"][s["kind"]] += 1
        t["usd"] += s["usd"] or 0
        t["units"] += s["units"]
        t["nodes"] += sum(1 for c in s["commits"] if c["ok"] is not False and re.match(r"W\d+\.\d+", c["subject"]))
        t["refused"] += s["refused"]
        t["rereads"] += s["rereads"]
        for kind, value in s["verdicts"]:
            if kind == "review":
                t["review_" + ("ok" if value == "ok" else "issues")] += 1
                # Whether the first tree declared finished passes: what a change to the
                # skill or the law is meant to move.
                t["first_review"] = t["first_review"] or ("ok" if value == "ok" else "issues")
            elif kind == "backtrack" or (kind == "critic" and value == "issues"):
                t["critic_issues"] += 1
            elif kind == "raise":
                t["raises"] += 1
        t["start"], t["end"] = min(t["start"], s["start"]), max(t["end"], s["end"])
    for (project, task), t in out.items():
        log = logs.get(project, {}).get(task)
        t["author"] = sum(log[k] for k in ("answer", "correct", "accept")) if log else None
        t["sessions"] = dict(t["sessions"])
    return out


def q(v, p):
    v = sorted(v)
    return v[min(int(len(v) * p), len(v) - 1)] if v else 0


def report(sessions, tasks, rates, n_priced):
    w = sys.stdout.write
    dfs = [s for s in sessions if s["kind"] in ("work", "review", "critic")]
    w(f"{len(sessions)} transcripts, {len(dfs)} dfs sessions (work, review, critic); "
      f"dollars from {sum(n_priced.values())} runner-priced sessions, ${rates:.6f} per unit\n\n")
    w("Where the spend goes, by project and session kind ($ estimated where the runner did not record it)\n")
    w(f"{'project':34} {'kind':7} {'n':>5} {'$':>9} {'$ med':>7} {'peak med':>9} {'resp med':>8}\n")
    by = collections.defaultdict(list)
    for s in dfs:
        by[(s["project"], s["kind"])].append(s)
    for (p, k), v in sorted(by.items()):
        w(f"{p[-34:]:34} {k:7} {len(v):5} {sum(s['usd'] for s in v):9.0f} "
          f"{st.median(s['usd'] for s in v):7.2f} {st.median(s['peak'] for s in v):9.0f} "
          f"{st.median(s['responses'] for s in v):8.0f}\n")
    w("\nTasks, by project: how many sessions a task takes and how its work was judged\n")
    w(f"{'project':34} {'tasks':>5} {'sess med':>8} {'p90':>4} {'$ med':>6} {'$ p90':>6} "
      f"{'review rounds':>13} {'issues/task':>11} {'critic iss':>10} {'raises':>6}\n")
    byp = collections.defaultdict(list)
    for t in tasks.values():
        if t["sessions"].get("work"):
            byp[t["project"]].append(t)
    for p, v in sorted(byp.items()):
        n = [sum(t["sessions"].values()) for t in v]
        usd = [t["usd"] for t in v]
        rounds = sum(t["review_ok"] + t["review_issues"] for t in v)
        w(f"{p[-34:]:34} {len(v):5} {st.median(n):8.0f} {q(n, .9):4} {st.median(usd):6.1f} {q(usd, .9):6.1f} "
          f"{rounds:13} {sum(t['review_issues'] for t in v) / len(v):11.2f} "
          f"{sum(t['critic_issues'] for t in v):10} {sum(t['raises'] for t in v):6}\n")
    total = sum(s["usd"] for s in dfs) or 1
    w("\nShare of all dfs spend\n")
    for k in ("work", "review", "critic"):
        w(f"  {k:28} {sum(s['usd'] for s in dfs if s['kind'] == k) / total:6.1%}\n")
    after = sum(t["after_issues"] for t in tasks.values())
    w(f"  {'after a review found issues':28} {after / total:6.1%}  (every session on a task once one of its reviews said issues)\n")
    refused = [s for s in dfs if s["refused"]]
    w(f"  sessions with a refused commit {len(refused)} of {len(dfs)}, {sum(s['refused'] for s in dfs)} refusals\n")
    w(f"  Read calls repeating an earlier read of the same slice: {sum(s['rereads'] for s in dfs)} "
      f"of {sum(s['reads'] for s in dfs)}\n")
    w("\nMost expensive tasks\n")
    for t in sorted(tasks.values(), key=lambda t: -t["usd"])[:12]:
        w(f"  {t['project'][-24:]:24} {t['task']:5} ${t['usd']:6.0f} sessions {t['sessions']} "
          f"review issues {t['review_issues']}/{t['review_ok'] + t['review_issues']} raises {t['raises']}\n")


def _glob(pattern):
    """The files matching a glob, or any of a list of globs, each once."""
    pats = [pattern] if isinstance(pattern, str) else pattern
    return sorted({f for p in pats for f in glob.glob(p)})


def find_repos(pattern, repos):
    """Add to `repos` each project in `pattern` whose checkout is on this box. The
    transcript directory is the checkout's path with every `/` and `.` made a `-`."""
    for d in {project_of(os.path.basename(os.path.dirname(f))) for f in _glob(pattern)}:
        for cand in ("/" + d[1:].replace("-", "/"), "/workspace/" + d.split("-workspace-")[-1]):
            if d not in repos and os.path.isdir(os.path.join(cand, ".dfs")):
                repos[d] = cand
    return repos


def price(sessions, costs):
    """Put `usd` and `priced` on each session: the runner's own figure where it has one,
    else the weighted units at the rate its model's priced sessions show."""
    rate, overall, n_priced = calibrate(sessions, costs)
    for s in sessions:
        s["usd"] = costs.get(s["sid"]) or s["units"] * rate.get(s["model"], overall or 0)
        s["priced"] = s["sid"] in costs
    return overall or 0, n_priced


def load(pattern, repos, replays=False):
    """(sessions, tasks, rate, n_priced) for the transcripts matching `pattern` (a glob or
    a list of them), with the checkouts in `repos` ({project: path}, extended here)
    supplying costs and logs."""
    files = [f for f in _glob(pattern)
             if replays or REPLAY not in os.path.dirname(f)]
    find_repos(pattern, repos)
    sessions = [session(f) for f in files]
    costs = {}
    for repo in repos.values():
        costs.update(runner_costs(repo))
    rate, n_priced = price(sessions, costs)
    logs = {p: author_log(r) for p, r in repos.items()}
    return sessions, tasks_of(sessions, logs), rate, n_priced


def main(argv):
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    pattern = os.path.join(base, "projects", "*", "*.jsonl")
    repos, out = {}, None
    it = iter(argv)
    for a in it:
        if a == "--projects":
            pattern = next(it)
        elif a == "--repo":
            k, _, v = next(it).partition("=")
            repos[k] = v
        elif a == "--json":
            out = next(it)
    sessions, tasks, rate, n_priced = load(pattern, repos)
    report(sessions, tasks, rate, n_priced)
    if out:
        with open(out, "w") as fh:
            json.dump({"sessions": sessions, "tasks": list(tasks.values()), "repos": repos}, fh)


if __name__ == "__main__":
    main(sys.argv[1:])
