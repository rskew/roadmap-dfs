#!/usr/bin/env python3
"""What this session has spent so far, read from its own transcript.

The budget is CONTEXT, not turns. A turn's weight varies about 3x — the 2026-09-06
overrun grew 532 tokens/turn against a 1,707 median, so its 136 turns cost about what
42 average ones would — and context is the quantity every turn is charged 0.1x on. A
turn count cannot see that difference; this can.

CLAUDE_CODE_SESSION_ID, CODEX_SESSION_ID and KIRO_SESSION_ID name their provider's
transcript, so a session can read its own state in one command rather than estimating it. Estimating
is what went wrong:
sessions reported "38 of 50" and "~36" while the transcript held 77 and 97 LINES, and
both numbers were wrong in different directions until the line/response distinction
was found.

  python3 <scripts>/dfs_context.py   exit 0 under the checkpoint, 1 over
  python3 <scripts>/dfs_context.py --measure <transcript> claude|codex|kiro|opencode
  python3 <scripts>/dfs_context.py --flat-to
  python3 <scripts>/dfs_context.py --chain-ceiling
"""
import glob
import json
import os
import re
import sys

# ⚠️ 140,000, not the corpus's 125,000, because a ROADMAP session has a different
# shape: a lower floor (46,718, being non-interactive) but a large MANDATORY
# orientation read, so its first work edit lands at a median 82,642. With that as F
# and the measured G = 984/turn, N = √(20·W·F/G) is 58 turns and the implied budget
# is 139,683 — which is where seven consecutive sessions had already been stopping
# and closing `overrun`. Provisional: it is set around a floor the AGENTS.md trim is
# meant to cut, and must be re-derived when that lands. §0.5b carries the argument.
# ⚠️ `dfs_metrics.py` reports a LOWER optimum (27 responses) from the same data and
# is not in conflict: it charges the mandatory roadmap read to G, this charges it to F.
# A restart really does re-read all of it, so F is its home. See the epic's finding.
CHECKPOINT = 140_000
FLAT_TO = 195_000      # within 10% of optimal from ~90k to here
BITES = 315_000        # where the curve stops being flat and starts costing

# ⚠️ THE CHAIN'S HARD STOP IS A DIFFERENT NUMBER FROM THE ADVICE, DELIBERATELY, AND
# WAS THE SAME ONE UNTIL 2026-09-17. `MAX_CONTEXT` read `FLAT_TO`, on the argument
# that "the number a session is told it has left and the number that ends the chain
# are one value" — which conflates two questions. `FLAT_TO` is a MEASURED fact about
# the cost curve and is what a session is asked to weigh when deciding whether to
# close; this is the point at which an UNATTENDED chain stops spending and fetches a
# person, and the only thing it has to be is far enough out that a session which
# overran its own checkpoint by a normal margin is not treated as machinery that has
# broken. A session at 224,700 — the 2026-09-16 W18 run — closed itself `overrun`
# with its work done, and stopping the whole walk on it cost more than the context
# did. Raising `FLAT_TO` instead would have been the lie the REGIONS comment below
# is about: it would tell a session at 380,000 it was inside the flat region.
CHAIN_CEILING = 400_000

# A region's upper bound and what to SAY inside it are ONE entry, so a region cannot
# be described without a bound to reach it. ⚠️ They came apart once: FLAT_TO appeared
# in the middle sentence and in no comparison, so every peak from the checkpoint to
# BITES was told it was "still inside the flat region to 195,000" — which told a
# session measured at 217,867 it was inside a region it had left 22,867 tokens
# earlier. A false reassurance in the one number a session checks itself against is
# worse than no number, and this epic has already had one instrument be wrong by 1.7x.
REGIONS = [
    (CHECKPOINT,
     "{to_check:,} to the {CHECKPOINT:,} checkpoint."),
    (FLAT_TO,
     "{over:,} over the {CHECKPOINT:,} checkpoint, still inside the flat region to "
     "{FLAT_TO:,}. Decide: wrap up now (commit a finished node, or write the open "
     "node's evidence so far), or carry on and say why in the node."),
    (BITES,
     "{past_flat:,} PAST {FLAT_TO:,}, the end of the measured region within 10% of "
     "optimum — {over:,} over the checkpoint, and short of {BITES:,} where the curve "
     "bites. Wrapping up is the default here; carrying on wants a reason in the node."),
    (None,
     "{past_bites:,} past {BITES:,}, where the curve stops being flat and starts "
     "costing, and {over:,} over the {CHECKPOINT:,} checkpoint. Wrap up, or say in the "
     "node what the extra bought."),
]


def verdict_for(peak):
    """Which measured region `peak` is in, said in that region's own words."""
    fields = dict(CHECKPOINT=CHECKPOINT, FLAT_TO=FLAT_TO, BITES=BITES,
                  peak=peak, to_check=CHECKPOINT - peak, over=peak - CHECKPOINT,
                  past_flat=peak - FLAT_TO, past_bites=peak - BITES)
    for bound, text in REGIONS:
        if bound is None or peak < bound:
            return text.format(**fields)


# What counts as a WRITE, and the one place it is defined. `dfs_metrics.py` owns
# the cost model and defined this first; `dfs_runs.py` asks the same question to
# find out what a cut-off session left behind, and a second regex agreeing with this
# one today is the duplicate nobody deletes.
WRITE = re.compile(r'(^|[;&|]\s*)(cat|tee)\s*>|>>|\bsed -i\b|python3 - <<|\bgit (commit|add|apply)\b|>\s*\S', re.I)
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit")


# How a path is recovered from a shell command. Not exhaustive and not meant to be:
# a command whose target cannot be read is still COUNTED as a write, so the answer
# degrades to "it wrote something" rather than to "it wrote nothing".
REDIRECT = re.compile(r"(?:^|[;&|]\s*)(?:cat|tee)\s*>>?\s*['\"]?([^\s'\";|&<>]+)")
SED_INPLACE = re.compile(r"\bsed -i\b[^;|&]*?\s(['\"]?)([^\s'\";|&<>]+)\1\s*(?:$|[;|&])")
# These sessions edit through `python3 - <<PY` heredocs, so the path is a literal in
# the script rather than a shell word.
PY_PATH = re.compile(r"(?:open\(|^\s*p\s*=\s*|\bp\s*=\s*)['\"]([^'\"]+\.[A-Za-z0-9_]{1,6})['\"]")
FD_REDIRECT = re.compile(r"\d>|&>")


def _paths_in(cmd):
    """Best-effort write targets named by one shell command."""
    found = []
    for m in REDIRECT.finditer(cmd):
        found.append(m.group(1))
    for m in SED_INPLACE.finditer(cmd):
        found.append(m.group(2))
    if "<<" in cmd:
        for m in PY_PATH.finditer(cmd):
            found.append(m.group(1))
    return [f for f in found if f not in ("/dev/null", "/dev/stderr", "/dev/stdout")]


def _codex_writes(rec):
    """(write calls, paths) named by one codex rollout record.

    ⚠️ A CODEX SESSION'S WRITES ARE NOT SHELL COMMANDS, and reading them as if they
    were answered zero for every codex run in this tree until 2026-09-21: the reader
    asked for `payload["command"]`, a key no rollout here carries at all. What a
    rollout does carry is `item_completed` items — a `FileChange` naming every path
    the patch tool changed, and a `CommandExecution` whose `command` is the argv list.
    So the patch tool is read from the CHANGE it made rather than from the call it
    was made by, which is also the only shape that cannot count a patch that failed
    to match as a write. Entry 685 is the false "nothing written" this produced,
    about a 161-response session that had rewritten four files.
    """
    payload = rec.get("payload") or {}
    if not isinstance(payload, dict):
        return 0, []
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    kind = item.get("type") or item.get("item_type")
    if kind == "FileChange":
        # ⚠️ Two shapes for one fact: the rollout keys `changes` BY PATH, while the
        # `--json` stream a runner reads writes a list of `{path, kind}`. Both are
        # read, because a reader that knew only the one it was written against
        # reports zero paths for the other and zero paths reads as nothing written.
        changes = item.get("changes") or []
        if isinstance(changes, dict):
            return 1, [k for k in changes if k]
        return 1, [c.get("path") for c in changes
                   if isinstance(c, dict) and c.get("path")]
    raw = item.get("command") if kind == "CommandExecution" else payload.get("command")
    cmd = " ".join(raw) if isinstance(raw, list) else str(raw or "")
    if cmd and WRITE.search(cmd) and not FD_REDIRECT.search(cmd):
        return 1, _paths_in(cmd)
    return 0, []


# ── kiro ──────────────────────────────────────────────────────────────────────────
#
# ⚠️ A KIRO SESSION'S TRANSCRIPT IS THE RUN LOG ITSELF. kiro-cli keeps its sessions in
# a sqlite database, not in a file per session, so there is nothing to glob for the
# way there is for claude and codex. What a chain DOES have is the stream it captured:
# `kiro-cli chat --output-format stream-json` writes the run's ACP events as JSON
# Lines, one self-describing event per line, and among them every session update
# (`agent_message_chunk`, `tool_call`, `usage_update`, ...). So the run log under
# `.dfs/runs/` is read as the transcript, and a session started outside a chain has
# none — which `main` says rather than measuring the wrong thing.
#
# ⚠️ THE ENVELOPE AROUND THE UPDATES IS NOT PINNED HERE. No captured run exists yet
# to write it against, so the readers below look for ACP's own field names
# (`sessionId`, `sessionUpdate`) at any depth of a record rather than at one path.
# When a real log is to hand, check `peak_and_turns` against it before trusting the
# turn count: it is the number of `usage_update` events, on the assumption that kiro
# sends one per model response.

def _walk(obj, depth=0):
    """Every dict inside one decoded record, the record first."""
    if depth > 6:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, depth + 1)


def acp_updates(rec):
    """The ACP session updates one kiro stream record carries."""
    return [d for d in _walk(rec) if isinstance(d.get("sessionUpdate"), str)]


def kiro_session_id(records):
    """The session a kiro stream belongs to, or "": the first `sessionId` in it."""
    for rec in records:
        for d in _walk(rec):
            sid = d.get("sessionId")
            if isinstance(sid, str) and sid:
                return sid
    return ""


def _kiro_log(session_id, root=None):
    """The newest chain run log under `.dfs/runs/` that names `session_id`, or None.

    Opencode's is found the same way: see the section below."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import dfs_paths
    runs = str(dfs_paths.runs())
    logs = glob.glob(os.path.join(runs, "*", "run-*.json"))
    logs.sort(key=lambda f: os.path.getmtime(f) if os.path.exists(f) else 0, reverse=True)
    needle = session_id.encode()
    for log in logs:
        try:
            with open(log, "rb") as fh:
                # The id is in the stream's first events; 256k is a bound, not a guess.
                if needle in fh.read(256 * 1024):
                    return log
        except OSError:
            continue
    return None


def _kiro_files_written(path):
    """(paths, write calls) from a kiro stream's tool calls.

    ACP gives every tool call a `kind`; an edit, delete or move is a write and names
    its paths in `locations`, and an `execute` is a shell command judged by WRITE like
    any other. `tool_call` and its `tool_call_update`s share a `toolCallId`, so each
    call is counted once, from whatever fields its records carried between them.
    """
    calls = {}
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            for u in acp_updates(rec):
                if u["sessionUpdate"] not in ("tool_call", "tool_call_update"):
                    continue
                tid = u.get("toolCallId") or "anon-%d" % len(calls)
                merged = calls.setdefault(tid, {})
                for k, v in u.items():
                    if v not in (None, "", [], {}):
                        merged[k] = v
    seen, paths, n = set(), [], 0
    def note(val):
        if val and val not in seen:
            seen.add(val); paths.append(val)
    for c in calls.values():
        if c.get("status") == "failed":
            continue
        kind = c.get("kind")
        raw = c.get("rawInput") if isinstance(c.get("rawInput"), dict) else {}
        if kind in ("edit", "delete", "move"):
            n += 1
            for loc in c.get("locations") or []:
                if isinstance(loc, dict):
                    note(loc.get("path"))
            note(raw.get("path") or raw.get("file_path"))
        elif kind == "execute":
            cmd = raw.get("command") or raw.get("cmd") or ""
            cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            hits = _paths_in(cmd)
            if hits or (WRITE.search(cmd) and not FD_REDIRECT.search(cmd)):
                n += 1
                for f in hits:
                    note(f)
    return paths, n


# ── opencode ──────────────────────────────────────────────────────────────────────
#
# ⚠️ AS KIRO'S, AN OPENCODE SESSION'S TRANSCRIPT IS THE RUN LOG. Its sessions live in
# a sqlite database (`opencode.db`, under its data directory), and what a chain
# captured is `opencode run --format json`: one JSON object per line, each with a
# `type` (`step_start`, `text`, `tool_use`, `step_finish`, `error`) and the
# `sessionID` at the top. Captured against opencode 1.18.34; the shapes read here are
# those:
#
#   tool_use     part.tool (`bash`, `edit`, `write`, ...), part.state.{status,input,output},
#                emitted once, when the call has finished (`completed` or `error`)
#   step_finish  part.tokens {input, output, reasoning, cache: {read, write}}, one per
#                model response, with `reason` `tool-calls` or `stop`
#   error        error.{name, data: {message, statusCode, ...}}, after which it exits 1
#
# Opencode exports no session id to the tools it runs, so a session cannot read its
# own budget from the environment as the other three do (`session_from_env`); a chain
# measures it from here.

OPENCODE_WRITE_TOOLS = ("edit", "write", "multiedit", "patch", "apply_patch")


def opencode_session_id(records):
    """The session an opencode stream belongs to, or "": the first `sessionID` in it."""
    for rec in records:
        if isinstance(rec, dict):
            sid = rec.get("sessionID")
            if isinstance(sid, str) and sid:
                return sid
    return ""


def _opencode_tools(rec):
    """The finished tool call one stream record carries, as its `part`, or None."""
    if not isinstance(rec, dict) or rec.get("type") != "tool_use":
        return None
    part = rec.get("part")
    return part if isinstance(part, dict) else None


def _opencode_files_written(path):
    """(paths, write calls) from an opencode stream's tool calls.

    A call that errored wrote nothing. The file tools name their target in
    `filePath`; `apply_patch` carries a patch whose paths are not read here, so it is
    counted and the count is the answer, as `files_written` says. `bash` is judged by
    WRITE like any other shell command.
    """
    seen, paths, n = set(), [], 0
    def note(val):
        if val and val not in seen:
            seen.add(val); paths.append(val)
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            part = _opencode_tools(rec)
            if not part:
                continue
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            if state.get("status") == "error":
                continue
            inp = state.get("input") if isinstance(state.get("input"), dict) else {}
            tool = part.get("tool")
            if tool in OPENCODE_WRITE_TOOLS:
                n += 1
                note(inp.get("filePath") or inp.get("file_path") or inp.get("path"))
            elif tool == "bash":
                cmd = str(inp.get("command") or "")
                hits = _paths_in(cmd)
                if hits or (WRITE.search(cmd) and not FD_REDIRECT.search(cmd)):
                    n += 1
                    for f in hits:
                        note(f)
    return paths, n


def _opencode_peak(rec):
    """The context one `step_finish` record reports, or None when it is not one.

    `input` is only the part that was not served from the cache, so the window the
    response read is `input + cache.read + cache.write` — which is what the record's
    own `total` is, less the output.
    """
    if not isinstance(rec, dict) or rec.get("type") != "step_finish":
        return None
    part = rec.get("part")
    tokens = part.get("tokens") if isinstance(part, dict) else None
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return sum(v for v in (tokens.get("input"), cache.get("read"), cache.get("write"))
               if isinstance(v, int))


def transcript_path(session_id, agent="claude", root=None):
    """Where a session's transcript is. GLOBBED first, derived only as a fallback.

    ⚠️ The project directory is named for the directory a session STARTED in, which a
    session that cd'd is no longer in — so deriving the name from the repo root
    answers wrongly for exactly the sessions most likely to be interesting. This
    file's own `main` globbed for that reason while `dfs_tui.py` derived, which
    is two answers to one question; the glob is the correct one and is now the only
    one. The derived path is still returned when nothing matches, because a caller
    waiting on a session that has not written its transcript yet needs a name to
    show rather than a None.
    """
    if not session_id:
        return None
    if agent in ("kiro", "opencode"):
        return _kiro_log(session_id, root)
    if agent == "codex":
        home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        found = glob.glob(os.path.join(home, "sessions", "**",
                                       "rollout-*-%s.jsonl" % session_id),
                          recursive=True)
        return found[0] if found else None
    found = glob.glob(os.path.expanduser("~/.claude/projects/*/%s.jsonl" % session_id))
    if found:
        return found[0]
    proj = re.sub(r"[^A-Za-z0-9]", "-", str(root or os.getcwd()))
    return os.path.join(os.path.expanduser("~/.claude/projects"), proj,
                        session_id + ".jsonl")


def files_written(path, provider="claude"):
    """What a session's OWN transcript says it wrote: (paths, number of write calls).

    ⚠️ THE TRANSCRIPT, NOT THE TREE, and that is the whole point. A working tree
    holding several sessions' uncommitted work cannot answer "did THIS session leave
    anything": `git status --porcelain` lists a file that was already modified
    identically before and after, so a session that edited four already-dirty files
    is indistinguishable from one that did nothing. That is not hypothetical — it is
    how entry 73 came to record a session as having "left the working tree unchanged"
    when it had made four edits in its last hundred seconds.

    ⚠️ Read across EVERY line sharing a message id. One response is written as one
    line per content block, so a scan that tests only the first line misses the
    tool_use and reports a session as having written nothing — the same trap the
    turn-count instrument fell into and was wrong by 2x for.

    ⚠️ The COUNT is the load-bearing half, not the paths. A redirection target is
    recovered by pattern and a heredoc's may not be recoverable at all, so the paths
    are evidence and the count is the answer: a session with write calls and no
    readable paths still did not do nothing.
    """
    if provider == "kiro":
        return _kiro_files_written(path)
    if provider == "opencode":
        return _opencode_files_written(path)
    seen, paths, calls = set(), [], 0
    def note(val):
        if val and val not in seen:
            seen.add(val); paths.append(val)
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if provider == "codex":
                made, found = _codex_writes(rec)
                calls += made
                for f in found:
                    note(f)
                continue
            msg = rec.get("message") or {}
            for b in (msg.get("content") or []):
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                inp = b.get("input") or {}
                if b.get("name") in EDIT_TOOLS:
                    calls += 1
                    note(inp.get("file_path"))
                elif b.get("name") == "Bash":
                    cmd = inp.get("command") or ""
                    hits = _paths_in(cmd)
                    # A bare `2>/dev/null` is not a write. Require either a
                    # recovered target or a write form that is not just a redirect.
                    if hits or (WRITE.search(cmd) and not FD_REDIRECT.search(cmd)):
                        calls += 1
                        for f in hits:
                            note(f)
    return paths, calls


def peak_and_turns(path, provider):
    """Peak context and response count for a Claude or Codex rollout, or a Kiro or Opencode stream."""
    best, seen = 0, set()
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if provider == "opencode":
                used = _opencode_peak(rec)
                if used is not None:
                    seen.add(len(seen))
                    best = max(best, used)
                continue
            if provider == "kiro":
                # ACP's `usage_update` says how much of the context window is in
                # use (`used`, out of `size`), which is the quantity the budget is
                # written in — not a sum over requests.
                for u in acp_updates(rec):
                    if u["sessionUpdate"] == "usage_update" and isinstance(u.get("used"), int):
                        seen.add(len(seen))
                        best = max(best, u["used"])
                continue
            if provider == "codex":
                if rec.get("type") != "event_msg":
                    continue
                payload = rec.get("payload") or {}
                if payload.get("type") != "token_count":
                    continue
                usage = ((payload.get("info") or {}).get("last_token_usage") or {})
                if usage:
                    seen.add(rec.get("ordinal", len(seen)))
                    best = max(best, usage.get("input_tokens", 0) or 0)
                continue
            if rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage or msg.get("id") in seen:
                continue
            seen.add(msg.get("id"))
            # `input_tokens` is left out deliberately and not by oversight: measured
            # over 12,026 responses it totals 23,892 tokens, at most 2 per response.
            best = max(best, (usage.get("cache_read_input_tokens") or 0)
                             + (usage.get("cache_creation_input_tokens") or 0))
    return best, len(seen)


def session_from_env():
    """(provider, session id) of the session this process runs inside, or (None, "")."""
    for provider, names in (("claude", ("CLAUDE_CODE_SESSION_ID",)),
                            ("codex", ("CODEX_SESSION_ID", "CODEX_THREAD_ID")),
                            ("kiro", ("KIRO_SESSION_ID",))):
        for name in names:
            if os.environ.get(name):
                return provider, os.environ[name]
    return None, ""


def main(argv):
    # The runner measures a session it LAUNCHED, so it cannot read an env var for it,
    # and the TUI measures a run dir's transcript. Both go through this file rather
    # than carrying their own copy: a loop, a screen and a session that disagreed
    # about the budget is the failure this roadmap keeps writing down. -1 is the
    # "unavailable" the runner's ceilings already branch on.
    if argv[:1] == ["--measure"]:
        try:
            peak, turns = peak_and_turns(argv[1], argv[2])
        except (FileNotFoundError, IsADirectoryError):
            peak, turns = -1, -1
        print(peak, turns)
        return 0
    if argv[:1] == ["--flat-to"]:
        print(FLAT_TO)
        return 0
    if argv[:1] == ["--chain-ceiling"]:
        print(CHAIN_CEILING)
        return 0

    provider, sid = session_from_env()
    if not sid:
        print("dfs_context: no Claude, Codex or Kiro session id is set — run this inside a session")
        return 2
    found = transcript_path(sid, provider)
    if not found or not os.path.exists(found):
        print(f"dfs_context: no transcript for {sid}")
        return 2

    peak, turns = peak_and_turns(found, provider)
    print(f"turns {turns} · context {peak:,} · {verdict_for(peak)}")
    return 1 if peak >= CHECKPOINT else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
