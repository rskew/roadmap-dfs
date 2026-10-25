// opencode's way in for the author's messages: a plugin that watches the run's inbox
// (inbox.py, as the claude and codex hooks do) and hands each message to the running session
// as a prompt (opencode's own session inbox, `delivery: "steer"`). A message sent during a tool
// call is the model's next request; one sent while the model is on its last answer makes the
// session take one more turn. See ../inbox.py for the rules.
import { spawnSync } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const inboxPy = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "inbox.py")
const POLL_MS = 150

const waiting = (box) => {
  try {
    return fs.readdirSync(box).length > 0
  } catch {
    return false
  }
}

const inbox = (...args) => spawnSync("python3", [inboxPy, ...args], { input: "{}", encoding: "utf8" })

export default {
  id: "dfs-inbox",
  setup: async (ctx) => {
    let watching = false
    // The session's id is known from its first model request; the first is the run's own.
    ctx.session.hook("model.request", async (request) => {
      const box = process.env.DFS_INBOX
      if (watching || !box) return
      watching = true
      let busy = false
      const timer = setInterval(async () => {
        if (busy || !waiting(box)) return
        busy = true
        try {
          // Read, hand over, and only then move the messages to delivered/: a prompt opencode
          // refuses leaves them waiting for the next poll, and the log never calls them read.
          const peeked = inbox("peek")
          if (peeked.status !== 0) return
          const { names, context } = JSON.parse(peeked.stdout)
          if (!names.length) return
          await ctx.session.prompt({ sessionID: request.sessionID, text: context, delivery: "steer" })
          inbox("ack", ...names)
        } catch {
          // left waiting, tried again
        } finally {
          busy = false
        }
      }, POLL_MS)
      timer.unref()
    })
  },
}
