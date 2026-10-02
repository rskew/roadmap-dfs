#!/usr/bin/env python3
"""A net under the log panes — the only place a session is read after it ends.

⚠️ What is checked is the CONDENSATION, not the drawing: given the lines an agent
actually wrote, does the pane say what the session did? The rows are named by role
(`run`, `out`, `say`) rather than carrying curses attributes precisely so this file
can exist — `curses.color_pair` raises until `initscr`, which is why the Claude
renderer beside these went untested for as long as it did.

The fixtures are the two shapes codex writes, cut down from real logs under
`.dfs/runs/` and `~/.codex/sessions/`: the `exec --json` stream a run captures, and
the rollout the provider writes as it goes.

Run:  python3 roadmap-dfs/scripts/test_logs.py
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TUI_SPEC = importlib.util.spec_from_file_location("dfs_tui", HERE / "tui.py")
TUI = importlib.util.module_from_spec(TUI_SPEC)
TUI_SPEC.loader.exec_module(TUI)


def texts(rows, role=None):
    return [t for t, r in rows if role is None or r == role]


def roles(rows):
    return [r for _, r in rows]


# ── the captured `exec --json` stream ──────────────────────────────────────────

EXEC_RUN = [
    {"type": "thread.started", "thread_id": "01a0adcb-9224-7bf0-b5f0-16719e4ae040"},
    {"type": "turn.started"},
    {"type": "item.completed",
     "item": {"id": "item_0", "type": "agent_message",
              "text": "Tracing W18's dependencies."}},
    {"type": "item.started",
     "item": {"id": "item_1", "type": "command_execution",
              "command": "/bin/bash -lc \"sed -n '1,240p' roadmap-dfs/SKILL.md\"",
              "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
    {"type": "item.completed",
     "item": {"id": "item_1", "type": "command_execution",
              "command": "/bin/bash -lc \"sed -n '1,240p' roadmap-dfs/SKILL.md\"",
              "aggregated_output": "---\nname: roadmap-session\n", "exit_code": 0,
              "status": "completed"}},
    {"type": "item.completed",
     "item": {"id": "item_2", "type": "command_execution",
              "command": "/bin/bash -lc \"cabal build\"",
              "aggregated_output": "Main.hs:14:1: error:\nmore\n", "exit_code": 1,
              "status": "completed"}},
    {"type": "item.completed",
     "item": {"id": "item_3", "type": "file_change",
              "changes": [{"path": "/workspace/shop-app/.dfs/ROADMAP.md",
                           "kind": "update"}], "status": "completed"}},
    {"type": "turn.completed",
     "usage": {"input_tokens": 10027904, "cached_input_tokens": 9856768,
               "output_tokens": 14439}},
]


class TheCapturedStream(unittest.TestCase):
    """`codex_result_lines` — a run's own log, which is all a finished run leaves."""

    def test_the_session_id_is_the_thread_not_an_event_ordinal(self):
        # ⚠️ The bug this replaces: `e.get("id")` took the first id it saw, which in
        # the older stream is an event NUMBER. The pane then reported a transcript
        # missing at `<sessions>/0.jsonl` — an answer shaped exactly like a true one.
        self.assertEqual(TUI.codex_session_id(EXEC_RUN),
                         "01a0adcb-9224-7bf0-b5f0-16719e4ae040")
        self.assertEqual(TUI.codex_session_id([{"id": "0", "msg": {"type": "x"}}]), "")
        self.assertEqual(
            TUI.codex_session_id([{"id": "0", "msg": {"session_id": "abc"}}]), "abc")

    def test_a_command_is_drawn_once_though_it_arrives_twice(self):
        # `item.started` and `item.completed` carry the same item. Drawing both put
        # every command on the screen twice, once with an empty output.
        rows = TUI.codex_result_lines(EXEC_RUN)
        ran = texts(rows, "run")
        self.assertEqual(sum(1 for t in ran if "SKILL.md" in t), 1)

    def test_a_command_still_running_is_the_one_started_copy_worth_drawing(self):
        live = [e for e in EXEC_RUN if e["type"] != "item.completed"]
        rows = TUI.codex_result_lines(live)
        self.assertIn("  → exec  sed -n '1,240p' roadmap-dfs/SKILL.md",
                      texts(rows, "run"))
        self.assertIn("    ← running", texts(rows, "out"))

    def test_the_bash_wrapper_is_unquoted_off_the_front_of_every_command(self):
        # `/bin/bash -lc "<the command>"` is on all of them, so printed verbatim it
        # spends the first fourteen columns of every row saying nothing.
        self.assertIn("  → exec  sed -n '1,240p' roadmap-dfs/SKILL.md",
                      texts(TUI.codex_result_lines(EXEC_RUN), "run"))

    def test_a_command_that_could_not_be_unwrapped_is_shown_as_written(self):
        self.assertEqual(TUI.codex_command('/bin/bash -lc "unclosed'),
                         '/bin/bash -lc "unclosed')
        self.assertEqual(TUI.codex_command(["git", "status"]), "git status")

    def test_a_result_is_one_row_carrying_how_much_was_left_out(self):
        rows = TUI.codex_result_lines(EXEC_RUN)
        self.assertIn("    ← ---   [26 chars]", texts(rows))

    def test_a_non_zero_exit_says_so_on_the_row_and_reads_as_an_error(self):
        rows = TUI.codex_result_lines(EXEC_RUN)
        failed = [(t, r) for t, r in rows if "exit 1" in t]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0][0].startswith("    ← exit 1 · Main.hs:14:1: error:"))
        self.assertEqual(failed[0][1], "err")

    def test_a_file_change_names_the_file_and_what_happened_to_it(self):
        self.assertIn("  ✎ update /workspace/shop-app/.dfs/ROADMAP.md",
                      texts(TUI.codex_result_lines(EXEC_RUN), "run"))

    def test_the_turn_usage_is_spelt_out_as_a_sum(self):
        # ⚠️ `input 10028k` beside this screen's `peak context 176k` reads as a
        # second answer to one question, and is 57x the first. One is what the turn
        # SPENT over every request; the other is what it was CARRYING.
        head = texts(TUI.codex_result_lines(EXEC_RUN))[1]
        self.assertIn("summed over the turn's requests", head)
        self.assertIn("10028k", head)

    def test_the_head_counts_what_the_session_did(self):
        head = texts(TUI.codex_result_lines(EXEC_RUN))[0]
        self.assertIn("4 items", head)
        self.assertIn("command_execution 2", head)

    def test_a_line_that_is_not_json_is_kept(self):
        # On a run that died before it started, codex's own stderr is the whole of
        # what happened; `read_result` hands it over as `_raw`.
        rows = TUI.codex_result_lines([{"_raw": "error: usage limit reached"}])
        self.assertIn("  error: usage limit reached", texts(rows))

    def test_a_failed_turn_is_reported_rather_than_left_to_the_exit_code(self):
        rows = TUI.codex_result_lines(
            [{"type": "turn.failed", "error": {"message": "stream disconnected"}}])
        self.assertIn("  ⨯ stream disconnected", texts(rows, "err"))

    def test_an_item_type_nobody_taught_this_screen_still_gets_a_row(self):
        # A codex release that adds an item type should read as one unfamiliar row,
        # never as a session that did less than it did.
        rows = TUI.codex_result_lines(
            [{"type": "item.completed", "item": {"id": "i", "type": "new_thing"}}])
        self.assertTrue(any("new_thing" in t for t in texts(rows, "dim")))


# ── the provider's rollout ─────────────────────────────────────────────────────

ROLLOUT = [
    {"type": "session_meta", "payload": {"session_id": "01a0adcb"}},
    {"type": "response_item",
     "payload": {"type": "message", "role": "developer",
                 "content": [{"type": "input_text",
                              "text": "<skills_instructions>\nuse them\n"}]}},
    {"type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text",
                              "text": "Follow roadmap-dfs/SKILL.md. Pick W18."}]}},
    {"type": "response_item",
     "payload": {"type": "reasoning", "summary": [],
                 "encrypted_content": "gAAAAABqq3e3td3TPFe3F" * 40}},
    {"type": "response_item",
     "payload": {"type": "custom_tool_call", "name": "exec",
                 "input": 'const r = await tools.exec_command({"cmd":"sed -n '
                          '\'1,240p\' roadmap-dfs/SKILL.md","workdir":"/workspace"'
                          ',"yield_time_ms":10000}); text(r.output);\n'}},
    {"type": "event_msg",
     "payload": {"type": "item_completed",
                 "item": {"type": "UserMessage", "id": "x",
                          "content": [{"type": "text", "text": "a second copy"}]}}},
    {"type": "response_item",
     "payload": {"type": "custom_tool_call_output",
                 "output": [{"type": "input_text",
                             "text": "Script completed\nWall time 0.1 seconds\n"
                                     "Output:\n"},
                            {"type": "input_text",
                             "text": "---\nname: roadmap-session\n"},
                            {"type": "input_text", "text": "\n<RESULT_END>\n"}]}},
    {"type": "response_item",
     "payload": {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text",
                              "text": "W18 is unblocked."}]}},
    {"type": "event_msg", "payload": {"type": "token_count", "info": {}}},
]


class TheRollout(unittest.TestCase):
    """`codex_transcript_lines` — the turns, while and after they are taken."""

    def test_the_pane_is_not_empty_which_is_what_it_was(self):
        # ⚠️ The whole bug: `transcript_lines` keeps records whose type is
        # `assistant` or `user`, and a codex rollout has neither — so the pane drew
        # nothing, which reads as a session that took no turns.
        self.assertTrue(TUI.codex_transcript_lines(ROLLOUT))

    def test_what_the_author_typed_is_told_from_what_the_harness_said(self):
        rows = TUI.codex_transcript_lines(ROLLOUT)
        self.assertEqual(texts(rows, "you"),
                         ["you: Follow roadmap-dfs/SKILL.md. Pick W18."])
        self.assertTrue(any("skills_instructions" in t for t in texts(rows, "meta")))

    def test_a_turn_is_drawn_once_though_the_rollout_records_it_twice(self):
        # Every `response_item` has an `event_msg` twin for whoever is watching.
        rows = TUI.codex_transcript_lines(ROLLOUT)
        self.assertEqual([t for t in texts(rows) if "a second copy" in t], [])

    def test_the_command_is_decoded_out_of_the_javascript_it_arrives_in(self):
        rows = TUI.codex_transcript_lines(ROLLOUT)
        self.assertIn("  → exec  sed -n '1,240p' roadmap-dfs/SKILL.md",
                      texts(rows, "run"))

    def test_a_command_holding_a_brace_survives_being_decoded(self):
        arg = TUI.codex_call_argument(
            'const r = await tools.exec_command({"cmd":"awk \'{print $2}\' f",'
            '"workdir":"/w"}); text(r.output);')
        self.assertEqual(arg, "awk '{print $2}' f")

    def test_a_bare_key_object_is_decoded_as_well_as_a_json_one(self):
        # ⚠️ Codex writes `{"cmd": "<the command>"}` in some runs and `{cmd: "<the command>"}` in others —
        # two rollouts an hour apart on this box differ that way — and the second is
        # not JSON. Without the second pass a whole session prints its snippets.
        self.assertEqual(TUI.codex_call_argument(
            'const r = await tools.exec_command({cmd:"sed -n \'1,4p\' f",'
            '"workdir":"/w"}); text(r.output);'), "sed -n '1,4p' f")

    def test_the_harness_preamble_is_one_row_plus_its_size(self):
        # ⚠️ Codex's developer preamble is the skills table, the plugin catalogue
        # and the collaboration rules — about 44,000 characters. Wrapped, it is forty
        # dim rows ahead of the first thing the session did.
        rows = TUI.codex_transcript_lines([
            {"type": "response_item",
             "payload": {"type": "message", "role": "developer",
                         "content": [{"text": "<recommended_plugins>\n" + "x " * 900}]}}])
        self.assertEqual(len(rows), 1)
        self.assertIn("[1822 chars]", rows[0][0])
        self.assertNotIn("meta", TUI.WRAPPED_ROLES)

    def test_the_result_row_is_the_output_not_the_runners_preamble(self):
        # ⚠️ `← Script completed` was on every row of the pane: true, and identical
        # for a session that read ten files and one that read none.
        rows = TUI.codex_transcript_lines(ROLLOUT)
        out = texts(rows, "out")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("    ← ---"))
        self.assertNotIn("Script completed", out[0])
        self.assertNotIn("RESULT_END", out[0])

    def test_a_failed_script_is_marked_on_the_row(self):
        marker, body = TUI.codex_tool_output(
            [{"text": "Script failed\nWall time 0.1 seconds\nOutput:\n"},
             {"text": "bash: cabal: command not found\n"}])
        self.assertEqual(marker, "failed · ")
        self.assertEqual(body.strip(), "bash: cabal: command not found")

    def test_an_encrypted_reasoning_block_says_nothing_rather_than_a_wrong_size(self):
        # ⚠️ The blob is ciphertext plus base64, so `~ thinking, 4312 chars` would be
        # measuring the envelope and would be read as measuring the thought.
        rows = TUI.codex_transcript_lines(ROLLOUT)
        self.assertEqual(texts(rows, "think"), [])

    def test_a_reasoning_summary_that_is_there_is_shown(self):
        rows = TUI.codex_transcript_lines([
            {"type": "response_item",
             "payload": {"type": "reasoning",
                         "summary": [{"type": "summary_text",
                                      "text": "Reading the roadmap"}]}}])
        self.assertEqual(texts(rows, "think"), ["  ~ Reading the roadmap"])

    def test_an_aborted_turn_is_the_one_thing_read_out_of_an_event(self):
        # It is the record that says why a chain stopped where it did, and no
        # `response_item` carries it.
        rows = TUI.codex_transcript_lines([
            {"type": "event_msg",
             "payload": {"type": "turn_aborted", "reason": "interrupted"}}])
        self.assertEqual(texts(rows, "err"), ["  ⨯ turn aborted: interrupted"])

    def test_a_function_call_is_a_tool_call_like_any_other(self):
        rows = TUI.codex_transcript_lines([
            {"type": "response_item",
             "payload": {"type": "function_call", "name": "spawn_agent",
                         "arguments": '{"task_name":"oops","fork_turns":"all"}'}}])
        self.assertIn("spawn_agent", texts(rows, "run")[0])


def call(tool_input, name="exec"):
    return {"type": "response_item",
            "payload": {"type": "custom_tool_call", "name": name, "input": tool_input}}


def output(text):
    return {"type": "response_item",
            "payload": {"type": "custom_tool_call_output",
                        "output": [{"text": "Script completed\nWall time 30.0 seconds\n"
                                            "Output:\n"}, {"text": text}]}}


class TheCodeModeCalls(unittest.TestCase):
    """Every code-mode call is named `exec`; the tool it called is in the script."""

    def test_a_patch_is_the_files_it_touched_not_the_patch(self):
        # ⚠️ The whole patch is one JavaScript string literal, newlines escaped, so
        # drawn as an argument it was a single row dozens of screen rows long.
        rows = TUI.codex_transcript_lines([call(
            'const patch = "*** Begin Patch\\n*** Update File: /w/.dfs/ROADMAP.md'
            '\\n@@\\n+### 669 \\u00b7 PICK\\n*** Add File: /w/docs/new.md\\n+x'
            '\\n*** End Patch"; text(await tools.apply_patch(patch));')])
        self.assertEqual(texts(rows, "run"), ["  ✎ update /w/.dfs/ROADMAP.md",
                                              "  ✎ add /w/docs/new.md"])

    def test_a_poll_is_named_by_what_it_waits_on(self):
        rows = TUI.codex_transcript_lines([
            call('const r = await tools.write_stdin({"session_id":28333,"chars":"",'
                 '"yield_time_ms":30000,"max_output_tokens":12000}); text(r.output);'),
            {"type": "response_item",
             "payload": {"type": "function_call", "name": "wait",
                         "arguments": '{"cell_id":"21","yield_time_ms":30000}'}}])
        self.assertEqual(texts(rows, "run"), ["  → poll  session 28333",
                                              "  → wait  cell 21"])

    def test_writing_to_a_session_shows_what_was_written(self):
        rows = TUI.codex_transcript_lines([call(
            'await tools.write_stdin({"session_id":7,"chars":"q\\n"})')])
        self.assertEqual(texts(rows, "run"), ['  → write_stdin  session 7  "q\\n"'])

    def test_a_polled_result_is_what_was_printed_not_its_envelope(self):
        printed = "\u001b[2mRunning \u001b[22m1132\u001b[2m tests\u001b[22m\r\nmore"
        rows = TUI.codex_transcript_lines([
            output(json.dumps({"chunk_id": "9f90cd", "wall_time_seconds": 30.0,
                               "output": printed})),
            output(json.dumps({"chunk_id": "2da2bb", "output": ""}))])
        self.assertEqual(texts(rows, "out"), [
            "    ← Running 1132 tests   [%d chars]" % len(printed),
            "    ← no new output"])


class WhatReachesTheTerminal(unittest.TestCase):

    def test_colour_codes_and_control_characters_never_reach_addstr(self):
        # curses draws an ESC as the two cells `^[`, so a clipped row was wider than
        # its length, and a `\r` drew the rest of the row back over its start.
        self.assertEqual(TUI.printable("a\x1b[2mRun\x1b[22m\t5\rb\x1b]0;t\x07c"),
                         "aRun    5 bc")

    def test_the_runners_briefing_is_cut_and_a_typed_message_is_not(self):
        rows = TUI.codex_transcript_lines([
            {"type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"text": "Follow the skill. " + "rules " * 2000}]}}])
        (row,) = texts(rows, "you")
        self.assertTrue(row.startswith("you: Follow the skill. "))
        self.assertTrue(row.endswith("   [12018 chars]"))
        self.assertLess(len(row), TUI.YOU_CHARS)


class TheRolesAreDrawable(unittest.TestCase):
    """Every role a renderer emits has to be one the screen knows how to paint."""

    def test_no_renderer_invents_a_role_the_pane_cannot_draw(self):
        # `role_attr` falls back to 0, so an unknown role is not a crash — it is a
        # row that silently loses its colour, which is the kind of thing nobody
        # notices for months. Checked as a set rather than trusted.
        known = {"say", "you", "meta", "think", "run", "out", "err", "head", "dim"}
        seen = set(roles(TUI.codex_result_lines(EXEC_RUN)))
        seen |= set(roles(TUI.codex_transcript_lines(ROLLOUT)))
        self.assertTrue(seen)
        self.assertEqual(seen - known, set())
        self.assertEqual(TUI.WRAPPED_ROLES - known, frozenset())


class TheRealLogsIfTheyAreHere(unittest.TestCase):
    """The fixtures above are cut down; these are whatever this box actually has.

    Skipped where there is nothing to read, because a developer box with no codex run
    on it is the normal case and a test that fails there teaches nothing.
    """

    def test_every_captured_codex_run_condenses_without_raising(self):
        runs = sorted(Path(".dfs/runs").glob("*/run-*.json")) if Path(
            ".dfs/runs").is_dir() else []
        codex = []
        for run in runs:
            meta = run.parent / "meta.json"
            if not meta.exists():
                continue
            try:
                if json.loads(meta.read_text()).get("agent") != "codex":
                    continue
            except ValueError:
                continue
            codex.append(run)
        if not codex:
            self.skipTest("no codex run captured on this box")
        for run in codex:
            result, events = TUI.read_result(str(run))
            if result is not None:
                continue
            rows = TUI.codex_result_lines(events)
            self.assertTrue(rows, run)
            # The point of the pane: it says what was RUN, not what was logged. Asked
            # only of a run that ran something: a codex that ran no command has no
            # such row to draw, and a capture is not evidence that one did (the fake
            # codex of test_live once wrote into a live chain's directory).
            ran = any(isinstance(e, dict) and e.get("type", "").startswith("item.")
                      and (e.get("item") or {}).get("type") in (
                          "command_execution", "file_change", "mcp_tool_call")
                      for e in events)
            self.assertEqual(any(r == "run" for _, r in rows), ran, run)


if __name__ == "__main__":
    unittest.main(verbosity=2)
