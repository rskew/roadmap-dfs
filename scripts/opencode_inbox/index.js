// opencode's way in for the author's messages: after each tool call, hand the run's
// inbox (inbox.py, as the claude and codex hooks do) over as one more part of the tool's
// result, so the next model request carries it. See ../inbox.py for the rules.
import { spawnSync } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"

const inboxPy = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "inbox.py")

export default {
  id: "dfs-inbox",
  setup: async (ctx) => {
    ctx.tool.hook("execute.after", async (call) => {
      if (!process.env.DFS_INBOX) return
      const ran = spawnSync("python3", [inboxPy, "post"], { input: "{}", encoding: "utf8" })
      if (ran.status !== 0 || !ran.stdout.trim()) return
      const said = JSON.parse(ran.stdout).hookSpecificOutput?.additionalContext
      if (said) call.result.content.push({ type: "text", text: said })
    })
  },
}
