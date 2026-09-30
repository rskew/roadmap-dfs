#!/usr/bin/env python3
"""A net under the one stop the walk is allowed to RETRY on.

`walk_decide` is already tested as a policy (test_dfs_walk.py): `limit` holds an hour,
everything else stops. What nothing tested was the fact it decides on — whether a run
that died on a usage limit is recorded `limit` at all. It was a grep for two
adjectives, and the day the provider used a third the walk turned itself off with two
hours of its limit still to run.

⚠️ The cases below are the SHAPES, not one wording: the first is the real
2026-09-16 W18 log, byte-for-byte in the fields that matter.

Run:  python3 roadmap-dfs/scripts/test_dfs_limit.py
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("dfs_limit", HERE / "dfs_limit.py")
LIM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LIM)


def claude(**fields):
    base = dict(session_id="s", total_cost_usd=1.0, num_turns=29, type="result")
    base.update(fields)
    return json.dumps(base)


class Claude(unittest.TestCase):
    def test_the_session_limit_that_stopped_the_walk(self):
        # W18, 2026-09-16 12:37. The old grep wanted "weekly" or "usage"; this said
        # "session", carried 429, and was recorded `failed`.
        raw = claude(is_error=True, subtype="success", api_error_status=429,
                     terminal_reason="api_error",
                     result="You've hit your session limit · resets 3pm (UTC)")
        self.assertEqual(LIM.classify(raw, "claude"), (True, "resets 3pm (UTC)"))

    def test_the_weekly_and_usage_wordings_still_read_as_limits(self):
        for word in ("weekly", "usage", "5-hour"):
            raw = claude(is_error=True, api_error_status=429,
                         result="You've hit your %s limit · resets 9am" % word)
            self.assertEqual(LIM.classify(raw, "claude"), (True, "resets 9am"), word)

    def test_a_429_with_no_reset_time_is_still_a_limit(self):
        raw = claude(is_error=True, api_error_status=429, result="rate limited")
        self.assertEqual(LIM.classify(raw, "claude"), (True, ""))

    def test_the_words_without_the_status_are_enough_when_the_run_errored(self):
        # A shape that carries the sentence but no status — the fallback that keeps
        # this from being one field's hostage.
        raw = claude(is_error=True, result="You've hit your session limit")
        self.assertEqual(LIM.classify(raw, "claude")[0], True)

    def test_a_session_that_merely_WROTE_about_limits_is_not_one(self):
        # ⚠️ `result` is the session's own last message. Prose alone must not
        # convict it, or a chain about rate limiting holds an hour for nothing.
        raw = claude(is_error=False, subtype="success",
                     result="Fixed the runner: it now detects 'hit your session limit'.")
        self.assertEqual(LIM.classify(raw, "claude"), (False, ""))

    def test_an_ordinary_failure_is_not_a_limit(self):
        # The 2026-09-16 03:47 W2 run: nix could not read a file out of the tarball.
        raw = "error:\n  … while fetching the input 'github:nixos/nixpkgs'\n"
        self.assertEqual(LIM.classify(raw, "claude"), (False, ""))

    def test_an_empty_log_is_not_a_limit(self):
        self.assertEqual(LIM.classify("", "claude"), (False, ""))


class Codex(unittest.TestCase):
    """No result object is pinned here, so codex keeps the whole-file prose match."""

    def test_a_limit_line_anywhere_in_the_stream(self):
        raw = "\n".join([
            json.dumps({"type": "thread.started", "thread_id": "t"}),
            json.dumps({"type": "error", "message": "You've hit your usage limit · resets 3pm"}),
        ])
        self.assertEqual(LIM.classify(raw, "codex"), (True, "resets 3pm"))

    def test_an_ordinary_stream_is_not_a_limit(self):
        raw = json.dumps({"type": "thread.started", "thread_id": "t"})
        self.assertEqual(LIM.classify(raw, "codex"), (False, ""))


class TheRunnerContract(unittest.TestCase):
    """What `dfs_run.sh` evals: two lines, shell-quoted, always both."""

    def test_the_output_is_safe_to_eval(self):
        import subprocess, tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(claude(is_error=True, api_error_status=429,
                            result="You've hit your session limit · resets 3pm (UTC)"))
            path = fh.name
        try:
            out = subprocess.run([sys.executable, str(HERE / "dfs_limit.py"), path, "claude"],
                                 capture_output=True, text=True, check=True).stdout
            got = subprocess.run(["bash", "-c", 'eval "$1"; printf "%s|%s" "$limit" "$resets"',
                                  "_", out], capture_output=True, text=True, check=True).stdout
            self.assertEqual(got, "1|resets 3pm (UTC)")
        finally:
            os.unlink(path)



class ResetAt(unittest.TestCase):
    """When the walk may retry: the stated reset, as an instant, from the real shapes."""
    import calendar
    NOW = calendar.timegm((2026, 9, 21, 8, 22, 0))   # the 09-21 08:22 start

    def test_a_time_later_today(self):
        at = LIM.reset_at("resets 8pm (UTC)", self.NOW)
        self.assertEqual(at - self.NOW, (20 - 8) * 3600 - 22 * 60 + LIM.RESET_MARGIN)

    def test_minutes_and_a_time_already_gone_is_tomorrow(self):
        at = LIM.reset_at("resets 1:40am (UTC)", self.NOW)
        self.assertEqual(at - self.NOW, (24 + 1 - 8) * 3600 + 18 * 60 + LIM.RESET_MARGIN)

    def test_a_dated_weekly_reset(self):
        at = LIM.reset_at("resets Sep 28, 8pm (UTC)", self.NOW)
        self.assertEqual(at, self.calendar.timegm((2026, 9, 28, 20, 0, 0)) + LIM.RESET_MARGIN)

    def test_noon_and_midnight(self):
        self.assertEqual(LIM.reset_at("resets 12pm (UTC)", self.NOW) % 86400,
                         12 * 3600 + LIM.RESET_MARGIN)
        self.assertEqual(LIM.reset_at("resets 12am (UTC)", self.NOW) % 86400,
                         LIM.RESET_MARGIN)

    def test_an_unknown_shape_is_none_not_a_guess(self):
        for s in ("", "resets Delivered by", "resets soon", "resets 8pm (PST)"):
            self.assertIsNone(LIM.reset_at(s, self.NOW), s)

if __name__ == "__main__":
    unittest.main(verbosity=2)
