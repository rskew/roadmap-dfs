#!/usr/bin/env python3
"""Regression checks for response-block aggregation in dfs_metrics.py."""
import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT = Path(__file__).with_name("dfs_metrics.py")


class SessionMetricsTest(unittest.TestCase):
    def test_split_tool_block_counts_one_turn_and_finds_first_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            transcript = project / "session.jsonl"
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            records = []
            turns = 158
            first_context = 59_983
            last_context = turns * 1_707
            for turn in range(turns):
                context = round(first_context +
                                (last_context - first_context) * turn / (turns - 1))
                message_id = f"response-{turn}"
                usage = {
                    "cache_read_input_tokens": context,
                    "cache_creation_input_tokens": 0,
                    "cache_creation": {"ephemeral_1h_input_tokens": 1},
                    "output_tokens": 1,
                }
                timestamp = (start + timedelta(seconds=turn)).isoformat()
                common = {
                    "type": "assistant",
                    "timestamp": timestamp,
                    "message": {"id": message_id, "usage": usage},
                }
                thinking = json.loads(json.dumps(common))
                thinking["message"]["content"] = [{"type": "thinking", "thinking": "x"}]
                records.append(thinking)

                tool = json.loads(json.dumps(common))
                tool["message"]["content"] = ([{
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "result.txt", "content": "x"},
                }] if turn == 0 else [{"type": "text", "text": "x"}])
                records.append(tool)

            transcript.write_text("".join(json.dumps(record) + "\n" for record in records))
            result = subprocess.run(
                ["python3", str(SCRIPT), "--projects", str(Path(tmp) / "*" / "*.jsonl")],
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertIn("158 assistant responses across 1 sessions", result.stdout)
        self.assertRegex(
            result.stdout,
            re.compile(r"context at 1st write \(R\)\s+59,983\s+59,983\s+59,983"),
        )
        self.assertIn("write multiplier 2.0x", result.stdout)
        self.assertIn("current median 158 responses costs 1.5x", result.stdout)


if __name__ == "__main__":
    unittest.main()
