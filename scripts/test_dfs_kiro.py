#!/usr/bin/env python3
"""kiro-cli as a chain's agent: how it is launched, and how its stream is read.

⚠️ THE STREAM HERE IS A STAND-IN. `kiro-cli chat --output-format stream-json` writes
"the run's ACP events as JSON Lines, one self-describing event per line" (its own
--help), and no real capture was to hand when this was written. So the envelope below
is a guess and the ACP inside it is not: `sessionId`, `sessionUpdate`, `toolCallId`,
`kind`, `locations`, `used`/`size` are the protocol's own names, and the readers find
them at any depth precisely so a different envelope still reads. Replace STREAM with
a real capture when there is one.

Run:  python3 roadmap-dfs/scripts/test_dfs_kiro.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "dfs_run.sh"
sys.path.insert(0, str(HERE))
import dfs_context  # noqa: E402
import dfs_limit  # noqa: E402

SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "dfs_tui.py")
TUI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TUI)

SID = "5f0c1e2a-0000-4000-8000-00000000000%s"


def update(n, **u):
    return {"type": "sessionUpdate",
            "payload": {"sessionId": SID % n, "update": u}}


def stream(n, said="Done."):
    n = int(n)
    return [
        {"type": "runStarted", "acpProtocolVersion": 1, "sessionId": SID % n},
        update(n, sessionUpdate="user_message_chunk",
               content={"type": "text", "text": "Work task W1."}),
        update(n, sessionUpdate="agent_thought_chunk",
               content={"type": "text", "text": "Look first."}),
        update(n, sessionUpdate="tool_call", toolCallId="t1", title="Reading W1.md",
               kind="read", status="pending", locations=[{"path": ".dfs/items/W1.md"}]),
        update(n, sessionUpdate="tool_call_update", toolCallId="t1", status="completed"),
        update(n, sessionUpdate="usage_update", used=41000, size=200000),
        update(n, sessionUpdate="tool_call", toolCallId="t2", title="Editing W1.md",
               kind="edit", status="pending", locations=[{"path": ".dfs/items/W1.md"}]),
        update(n, sessionUpdate="tool_call_update", toolCallId="t2", status="completed"),
        update(n, sessionUpdate="tool_call", toolCallId="t3", title="Shell",
               kind="execute", rawInput={"command": "echo hi > notes.txt"}),
        update(n, sessionUpdate="tool_call", toolCallId="t4", title="Shell",
               kind="execute", rawInput={"command": "git status"}),
        update(n, sessionUpdate="tool_call", toolCallId="t5", title="Bad edit",
               kind="edit", locations=[{"path": "nope.txt"}]),
        update(n, sessionUpdate="tool_call_update", toolCallId="t5", status="failed",
               content=[{"type": "content", "content": {"type": "text", "text": "no match"}}]),
        update(n, sessionUpdate="usage_update", used=52000 + n, size=200000),
        update(n, sessionUpdate="agent_message_chunk", content={"type": "text", "text": "Do"}),
        update(n, sessionUpdate="agent_message_chunk", content={"type": "text", "text": "ne."}
               if said == "Done." else {"type": "text", "text": said[2:]}),
        {"type": "runFinished", "finalText": said, "finalTextTruncated": False},
    ]


ROADMAP = "# Roadmap\n\n## §0 How work is done\n\nEach task is a decision tree.\n"
ITEM = """# W1 · the only thing

## Goal

The only thing.

## Tree

### W1.1 · the step
Status: open
Hypothesis: it can be done.
Evidence:

## Log
"""

# Stands in for kiro-cli: writes down its argv, prints the stream, and moves the task.
# Run 1 adds evidence; run 2 confirms the node. LIMIT=1 makes it die as kiro does on
# its monthly limit, on stderr and not in the stream.
STUB = r"""#!/usr/bin/env bash
set -euo pipefail
n=$(( $(cat "$COUNT_FILE") + 1 )); echo "$n" > "$COUNT_FILE"
printf '%s\n' "$@" > "$ARGV_DIR/argv-$n"
printf '%s\n' "${KIRO_SESSION_ID:-<unset>} ${KIRO_NO_AUTO_UPDATE:-<unset>}" > "$ARGV_DIR/env-$n"
if [ "${LIMIT:-0}" = 1 ]; then
  echo "error: The monthly usage limit has been reached" >&2; exit 1
fi
python3 "$EMIT" "$n"
python3 - "$n" <<'PY'
import os, sys
from pathlib import Path
p = Path(os.environ["DFS_ITEMS"]) / "W1.md"
t = p.read_text().replace("Evidence:\n", "Evidence:\n- for: run %s looked\n" % sys.argv[1])
if int(sys.argv[1]) >= 2:
    t = t.replace("Status: open", "Status: confirmed\nDetermination: done.")
p.write_text(t)
PY
"""

EMIT = """import json, sys
sys.path.insert(0, %r)
import test_dfs_kiro as t
for rec in t.stream(sys.argv[1]):
    print(json.dumps(rec))
""" % str(HERE)


class Chain(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".dfs" / "items").mkdir(parents=True)
        (self.root / ".dfs" / "ROADMAP.md").write_text(ROADMAP)
        (self.root / ".dfs" / "items" / "W1.md").write_text(ITEM)
        (self.root / ".gitignore").write_text(".dfs/runs/\n")
        self.aside = Path(tempfile.mkdtemp())
        (self.aside / "stub").write_text(STUB)
        (self.aside / "emit.py").write_text(EMIT)
        (self.aside / "count").write_text("0")
        subprocess.run("git init -q && git add -A && git -c user.name=t -c user.email=t@t "
                       "commit -qm init", shell=True, cwd=self.root, check=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.aside, ignore_errors=True)

    def run_chain(self, cap=2, **env):
        e = dict(os.environ, KIRO_CMD="bash %s" % (self.aside / "stub"),
                 COUNT_FILE=str(self.aside / "count"), ARGV_DIR=str(self.aside),
                 EMIT=str(self.aside / "emit.py"), HOME=str(self.aside),
                 KIRO_SESSION_ID="the-parent-session", **env)
        e.pop("RUNDIR", None)
        p = subprocess.run([str(RUNNER), "--kiro", "W1", str(cap)], cwd=self.root, env=e,
                           capture_output=True, text=True, timeout=300)
        metas = sorted((self.root / ".dfs" / "runs").glob("*/meta.json"),
                       key=lambda f: f.stat().st_mtime)
        meta = json.loads(metas[-1].read_text()) if metas else {}
        return p.returncode, meta, p.stdout + p.stderr

    def test_a_chain_runs_kiro_headless_and_reads_its_stream(self):
        rc, meta, out = self.run_chain()
        self.assertEqual(meta.get("stop"), "cap", out)
        self.assertEqual(meta.get("agent"), "kiro")
        argv = (self.aside / "argv-1").read_text().splitlines()
        self.assertEqual(argv[:5], ["chat", "--output-format", "stream-json",
                                    "--no-interactive", "--trust-all-tools"], argv)
        self.assertTrue(argv[5].startswith("Follow "), argv[5][:80])
        # The session id is kiro's, read out of its own stream, and the peak is the
        # largest `used` in it.
        self.assertEqual(meta.get("sid"), SID % 2, out)
        self.assertEqual(meta.get("peak"), str(52002), out)
        self.assertEqual(meta.get("turns"), "2", out)
        self.assertIn("W1.1 [confirmed]", out)

    def test_the_parent_s_session_is_not_handed_down(self):
        self.run_chain(cap=1)
        self.assertEqual((self.aside / "env-1").read_text().split(), ["<unset>", "1"])

    def test_a_monthly_limit_on_stderr_stops_the_chain_as_a_limit(self):
        rc, meta, out = self.run_chain(LIMIT="1")
        self.assertEqual((rc, meta.get("stop")), (1, "limit"), out)

    def test_the_run_log_is_the_transcript(self):
        self.run_chain(cap=1)
        old = os.getcwd()
        os.chdir(self.root)
        try:
            found = dfs_context.transcript_path(SID % 1, "kiro")
        finally:
            os.chdir(old)
        self.assertTrue(found and found.endswith("run-1.json"), found)


class Reading(unittest.TestCase):
    def write(self, recs, extra=""):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            fh.write(extra + "".join(json.dumps(r) + "\n" for r in recs))
        self.addCleanup(os.unlink, path)
        return path

    def test_peak_is_the_largest_context_in_use(self):
        self.assertEqual(dfs_context.peak_and_turns(self.write(stream(1)), "kiro"),
                         (52001, 2))

    def test_writes_are_edits_and_writing_commands_not_reads_or_failures(self):
        paths, calls = dfs_context.files_written(self.write(stream(1)), "kiro")
        self.assertEqual(calls, 2)
        # `echo hi > notes.txt` is counted; its target is not recovered, as for any
        # agent (dfs_context.REDIRECT reads `cat`/`tee` targets only).
        self.assertEqual(paths, [".dfs/items/W1.md"])

    def test_the_session_id_is_the_first_one_named(self):
        self.assertEqual(dfs_context.kiro_session_id(stream(7)), SID % 7)

    def test_a_session_that_wrote_about_limits_is_not_one(self):
        raw = Path(self.write(stream(1, said="Do it before the monthly usage limit "
                                             "has been reached."))).read_text()
        self.assertEqual(dfs_limit.classify(raw, "kiro"), (False, ""))

    def test_kiro_s_own_limit_in_the_stream_is_one(self):
        raw = Path(self.write(stream(1) + [{"type": "runError",
                                            "reason": "monthlyLimitReached"}])).read_text()
        self.assertEqual(dfs_limit.classify(raw, "kiro")[0], True)


    def test_the_overage_cap_is_a_limit(self):
        # Reported 2026-10-01: kiro stopped on reaching its limit for overages, which
        # the monthly wording did not match, so the run read as `failed`.
        for line in ("error: You have reached your limit for overages",
                     "error: You have reached the limit for overages.",
                     "error: Overage limit reached for this month",
                     '{"type":"runError","code":"OverageRequestLimitExceeded"}'):
            raw = Path(self.write(stream(1), extra=line + "\n")).read_text()
            self.assertEqual(dfs_limit.classify(raw, "kiro")[0], True, line)

    def test_the_overage_warning_is_not_a_limit(self):
        raw = Path(self.write(stream(1), extra="You've used your monthly included "
                                               "requests and are now using overages.\n")
                   ).read_text()
        self.assertEqual(dfs_limit.classify(raw, "kiro"), (False, ""))


class Screen(unittest.TestCase):
    def test_the_stream_condenses_to_what_was_said_and_run(self):
        rows = TUI.kiro_result_lines(stream(1) + [{"_raw": "\x1b[31mwarn: x\x1b[0m"}])
        text = [t for t, _ in rows]
        self.assertEqual(rows[0], ("5 tool calls · 1 messages", "head"))
        self.assertIn(("context 52k of 200k", "dim"), rows)
        self.assertIn(("Done.", "say"), rows)
        self.assertIn(("you: Work task W1.", "you"), rows)
        self.assertIn(("  → edit  Editing W1.md", "run"), rows)
        self.assertTrue(any(t.startswith("    ← ⨯ no match") for t in text), text)
        self.assertIn(("  warn: x", "dim"), rows)

    def test_the_x_key_cycles_every_agent(self):
        self.assertEqual(TUI.AGENTS, ("claude", "codex", "kiro"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
