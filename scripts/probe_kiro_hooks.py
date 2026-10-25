#!/usr/bin/env python3
"""What hooks can do in a REAL kiro-cli's v3 agent against the stand-in model (test_kiro_model.py).

    TMPDIR=<dir inside the repo> python3 probe_kiro_hooks.py <path to kiro-cli>

It runs `kiro-cli chat --v3 --no-interactive` under a custom agent `probe` that names a hook for
each event, one tool call, and prints which events fired and which hooks' output reached the
model's next request. Settled against kiro-cli-unwrapped 2.31.1 (the chain's pin) in W104.12:
all five events fire; agentSpawn and userPromptSubmit stdout reaches the model, but only once,
at the start; postToolUse and stop output does not reach it; a preToolUse hook that exits 2
blocks the tool call and its stderr reaches the model as the tool's result. An agent is found only
as `.kiro/agents/<name>.json` in the working directory or the home directory; `--agent` takes no path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kiro_model as T  # noqa: E402

EVENTS = ["agentSpawn", "userPromptSubmit", "preToolUse", "postToolUse", "stop"]


def probe(kiro, command, stream=">&2"):
    """Run one tool-calling turn with `command` as every hook; return (events fired, requests that carried each event's output)."""
    model = T.FakeKiroModel().__enter__()
    home = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR") or None))
    work = home / "work"
    (work / ".kiro" / "agents").mkdir(parents=True)
    hooks = {e: [dict(command=command.format(event=e))] for e in EVENTS}
    (work / ".kiro" / "agents" / "probe.json").write_text(json.dumps(dict(name="probe", tools=["*"], hooks=hooks)))
    env = dict(os.environ, PATH=os.path.dirname(kiro) + ":" + os.environ["PATH"], KIRO_NO_AUTO_UPDATE="1", HOME=str(home), **model.env())
    subprocess.run([kiro, "chat", "--v3", "--output-format", "stream-json", "--no-interactive", "--trust-all-tools", "--agent", "probe",
                    "--", "use a tool please"], env=env, cwd=str(work), capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    seen = [[e for e in EVENTS if ("W104-PING-" + e).encode() in body] for target, body in model.requests if target.endswith("GenerateAssistantResponse")]
    model.__exit__(None, None, None)
    shutil.rmtree(home, ignore_errors=True)
    return seen


if __name__ == "__main__":
    kiro = sys.argv[1]
    for label, command in (("each hook prints to stdout", "echo W104-PING-{event}"),
                           ("each hook prints to stderr and exits 2", "echo W104-PING-{event} >&2; exit 2")):
        print("##", label)
        for i, events in enumerate(probe(kiro, command)):
            print("  model request", i, "carried the output of", events)
