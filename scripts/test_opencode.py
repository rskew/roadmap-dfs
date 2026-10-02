#!/usr/bin/env python3
"""opencode as a chain's agent: how it is launched, and how its stream is read.

The stream here has the shape of a real capture: `opencode run --format json` 1.18.34,
pointed at a local OpenAI-compatible endpoint that answered with one `bash` call and
then a closing message, and at one that answered 429. Only the ids are made up.

Run:  python3 roadmap-dfs/scripts/test_opencode.py
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
RUNNER = HERE / "run.sh"
sys.path.insert(0, str(HERE))
import context as dfs_context  # noqa: E402
import limit as dfs_limit  # noqa: E402

SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
TUI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TUI)

SID = "ses_f037f832fffe%s"


def ev(n, kind, part=None, **extra):
    rec = {"type": kind, "timestamp": 1790942675221, "sessionID": SID % n}
    if part is not None:
        rec["part"] = dict(part, sessionID=SID % n)
    rec.update(extra)
    return rec


def tool(n, name, status="completed", **inp):
    return ev(n, "tool_use", {"type": "tool", "tool": name, "callID": "c-" + name,
                              "state": {"status": status, "input": inp,
                                        "output": "no match" if status == "error" else "",
                                        "title": name}})


def finish(n, inp, read=0, write=0, out=10, reason="tool-calls"):
    return ev(n, "step_finish", {"type": "step-finish", "reason": reason, "cost": 0,
                                 "tokens": {"total": inp + read + write + out, "input": inp,
                                            "output": out, "reasoning": 0,
                                            "cache": {"write": write, "read": read}}})


def stream(n, said="Done."):
    n = int(n)
    return [
        ev(n, "step_start", {"type": "step-start"}),
        tool(n, "read", filePath=".dfs/items/W1.md"),
        tool(n, "edit", filePath=".dfs/items/W1.md"),
        tool(n, "bash", command="echo hi > notes.txt", description="write"),
        tool(n, "bash", command="git status"),
        tool(n, "edit", status="error", filePath="nope.txt"),
        finish(n, 1000, read=40000),
        ev(n, "step_start", {"type": "step-start"}),
        ev(n, "text", {"type": "text", "text": said}),
        finish(n, 2000 + n, read=50000, write=0, reason="stop"),
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

# Stands in for opencode: writes down its argv, prints the stream, and moves the task.
# Run 1 adds evidence; run 2 confirms the node. LIMIT=1 makes it die as opencode does
# on a 429 (an `error` event on stdout, exit 1), LIMIT=stderr on its stderr alone.
STUB = r"""#!/usr/bin/env bash
set -euo pipefail
n=$(( $(cat "$COUNT_FILE") + 1 )); echo "$n" > "$COUNT_FILE"
printf '%s\n' "$@" > "$ARGV_DIR/argv-$n"
printf '%s\n' "${OPENCODE_DISABLE_AUTOUPDATE:-<unset>}" > "$ARGV_DIR/env-$n"
if [ "${LIMIT:-0}" = 1 ]; then
  python3 "$EMIT" "$n" limit; exit 1
fi
if [ "${LIMIT:-0}" = stderr ]; then
  echo "rate limit exceeded, retry later" >&2; exit 1
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
import test_opencode as t
if len(sys.argv) > 2:
    recs = t.stream(sys.argv[1])[:1] + [t.limit_error(sys.argv[1])]
else:
    recs = t.stream(sys.argv[1])
for rec in recs:
    print(json.dumps(rec))
""" % str(HERE)


def limit_error(n):
    return ev(int(n), "error", error={"name": "APIError", "data": {
        "message": "You exceeded your current quota, please check your plan and billing details.",
        "statusCode": 429, "isRetryable": True}})


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
        e = dict(os.environ, OPENCODE_CMD="bash %s" % (self.aside / "stub"),
                 COUNT_FILE=str(self.aside / "count"), ARGV_DIR=str(self.aside),
                 EMIT=str(self.aside / "emit.py"), HOME=str(self.aside), **env)
        e.pop("RUNDIR", None)
        p = subprocess.run([str(RUNNER), "--opencode", "W1", str(cap)], cwd=self.root, env=e,
                           capture_output=True, text=True, timeout=300)
        metas = sorted((self.root / ".dfs" / "runs").glob("*/meta.json"),
                       key=lambda f: f.stat().st_mtime)
        meta = json.loads(metas[-1].read_text()) if metas else {}
        return p.returncode, meta, p.stdout + p.stderr

    def test_a_chain_runs_opencode_headless_and_reads_its_stream(self):
        rc, meta, out = self.run_chain()
        self.assertEqual(meta.get("stop"), "cap", out)
        self.assertEqual(meta.get("agent"), "opencode")
        argv = (self.aside / "argv-1").read_text().splitlines()
        self.assertEqual(argv[:4], ["run", "--format", "json", "--auto"], argv)
        self.assertTrue(argv[4].startswith("Follow "), argv[4][:80])
        # The session id is opencode's, read out of its own stream, and the peak is the
        # largest input + cache read/write of any response.
        self.assertEqual(meta.get("sid"), SID % 2, out)
        self.assertEqual(meta.get("peak"), str(52002), out)
        self.assertEqual(meta.get("turns"), "2", out)
        self.assertIn("W1.1 [confirmed]", out)

    def test_it_is_told_not_to_update_itself(self):
        self.run_chain(cap=1)
        self.assertEqual((self.aside / "env-1").read_text().split(), ["true"])

    def test_a_429_in_the_stream_stops_the_chain_as_a_limit(self):
        rc, meta, out = self.run_chain(LIMIT="1")
        self.assertEqual((rc, meta.get("stop")), (1, "limit"), out)

    def test_a_rate_limit_on_stderr_stops_the_chain_as_a_limit(self):
        rc, meta, out = self.run_chain(LIMIT="stderr")
        self.assertEqual((rc, meta.get("stop")), (1, "limit"), out)

    def test_the_run_log_is_the_transcript(self):
        self.run_chain(cap=1)
        old = os.getcwd()
        os.chdir(self.root)
        try:
            found = dfs_context.transcript_path(SID % 1, "opencode")
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

    def test_peak_is_the_largest_window_a_response_read(self):
        # input + cache.read + cache.write, not the record's `total`, which adds output.
        self.assertEqual(dfs_context.peak_and_turns(self.write(stream(1)), "opencode"),
                         (52001, 2))

    def test_writes_are_edits_and_writing_commands_not_reads_or_failures(self):
        paths, calls = dfs_context.files_written(self.write(stream(1)), "opencode")
        self.assertEqual(calls, 2)
        # `echo hi > notes.txt` is counted; its target is not recovered, as for any agent.
        self.assertEqual(paths, [".dfs/items/W1.md"])

    def test_the_session_id_is_the_first_one_named(self):
        self.assertEqual(dfs_context.opencode_session_id(stream(7)), SID % 7)

    def test_a_line_that_is_not_an_object_is_skipped(self):
        path = self.write(stream(1), extra="[1, 2]\n3\nnot json\n")
        self.assertEqual(dfs_context.peak_and_turns(path, "opencode"), (52001, 2))
        self.assertEqual(dfs_context.files_written(path, "opencode")[1], 2)

    def test_a_429_error_event_is_a_limit(self):
        raw = Path(self.write([limit_error(1)])).read_text()
        self.assertEqual(dfs_limit.classify(raw, "opencode")[0], True)

    def test_the_providers_own_limit_wording_is_one_without_a_status(self):
        err = ev(1, "error", error={"name": "APIError", "data": {
            "message": "You've hit your usage limit · resets 3pm (UTC)"}})
        self.assertEqual(dfs_limit.classify(Path(self.write([err])).read_text(), "opencode"),
                         (True, "resets 3pm (UTC)"))

    def test_a_session_that_wrote_about_limits_is_not_one(self):
        said = "Back off on a 429: the rate limit has been reached."
        raw = Path(self.write(stream(1, said=said) + [
            tool(1, "bash", command="grep -r 'rate limit' .")])).read_text()
        self.assertEqual(dfs_limit.classify(raw, "opencode"), (False, ""))

    def test_any_other_error_is_not_a_limit(self):
        err = ev(1, "error", error={"name": "APIError", "data": {
            "message": "Invalid API key", "statusCode": 401}})
        self.assertEqual(dfs_limit.classify(Path(self.write([err])).read_text(), "opencode"),
                         (False, ""))


class Screen(unittest.TestCase):
    def test_the_stream_condenses_to_what_was_said_and_run(self):
        rows = TUI.opencode_result_lines(stream(1) + [{"_raw": "\x1b[31mwarn: x\x1b[0m"},
                                                      limit_error(1)])
        text = [t for t, _ in rows]
        self.assertEqual(rows[0], ("5 tool calls · 1 messages", "head"))
        self.assertIn(("context 52k", "dim"), rows)
        self.assertIn(("Done.", "say"), rows)
        self.assertIn(("  → edit  .dfs/items/W1.md", "run"), rows)
        self.assertIn(("  → bash  echo hi > notes.txt", "run"), rows)
        self.assertTrue(any(t.startswith("    ← ⨯ no match") for t in text), text)
        self.assertIn(("  warn: x", "dim"), rows)
        self.assertTrue(any(t.startswith("  ⨯ You exceeded your current quota")
                            and t.endswith("(HTTP 429)") for t in text), text)

    def test_the_session_id_is_read_from_the_events(self):
        self.assertEqual(TUI.opencode_session_id(stream(3)), SID % 3)

    def test_the_x_key_cycles_every_agent(self):
        self.assertEqual(TUI.AGENTS, ("claude", "codex", "kiro", "opencode"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
