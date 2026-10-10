#!/usr/bin/env python3
"""Did this run hit a usage limit, and when does it reset?

⚠️ THE ONE FACT THE WALK IS ALLOWED TO RETRY ON. `walk_decide` (tui.py) holds an
hour for `limit` and STOPS for everything else, deliberately — a crash retried hourly
is a subscription spent overnight. So the whole difference between "wait an hour" and
"the walk is over until somebody looks" is this classification, and it used to be a
grep for a sentence:

    grep -qiE "hit your (weekly|usage) limit|rate.?limit"

On 2026-09-16 at 12:37 a chain on W18 was answered `You've hit your session limit ·
resets 3pm (UTC)` with `api_error_status: 429`. "session limit" is neither "weekly"
nor "usage", the grep found nothing, the run was recorded `failed`, and the walk
turned itself off two hours and twenty minutes before the limit it was waiting on
would have lifted. That is this repo's own rule about reading a marker's definition
before putting a conclusion on it, applied to a phrase the provider is free to
reword.

So the STRUCTURED field is read first and the prose is a fallback:

  claude  `-p --output-format json` writes one object. `api_error_status` is the HTTP
          status the run died on and 429 is the rate-limit status — a fact with a
          definition, not a sentence. `is_error` plus limit prose in `result` is the
          second read, for a shape that carries the words but not the status.
          ⚠️ Prose alone is NOT enough here: `result` is the session's own last
          message, and a session that wrote about rate limits must not be read as one
          that hit one.
  codex   `exec --json` writes a JSONL stream whose error shape is not pinned here, so
          this stays the whole-file prose match it already was — no worse than before,
          and the phrase set is wider.
  kiro    `chat --output-format stream-json` writes ACP events as JSONL, with kiro's own
          stderr captured beside them. Its monthly limit is matched by its own words
          and its `monthlyLimitReached` reason code, and ONLY outside what the
          session itself said or ran (message, thought and tool-call updates, and
          the `finalText` that repeats its last message), for
          the same reason claude's `result` is not enough alone.

  opencode `run --format json` writes JSONL, and a run that dies on the provider ends in
          one `error` event whose `data.statusCode` is the HTTP status: 429 is the
          rate-limit status, as for claude, and the provider's wording
          is the second read. ONLY `error` events and lines that are not JSON (its
          stderr, captured beside) are read, so a `text` or `tool_use` event, which
          is the session's own, cannot convict it. The status is the provider's, so
          this reads a quota or rate limit of ANY provider the user configured.

Usage:  limit.py <log> <agent>   ->  limit=0|1  resets='<text>'  (shell-quotable)
"""
import json
import re
import shlex
import sys

# "hit your session limit", "hit your weekly limit", "hit your usage limit", and
# whatever the next adjective turns out to be.
LIMIT_RE = re.compile(
    r"hit your [\w' -]{0,40}\blimit\b"
    r"|usage limit reached"
    r"|\brate.?limit(ed|s)?\b",
    re.I,
)
# The one thing worth printing beside the stop: when it lifts. Stops at the quote or
# backslash that ends it inside JSON, exactly as the grep it replaces did.
# kiro-cli's own wordings, read out of its binary (2.24.0): "The monthly usage limit
# has been reached", "Monthly request limit reached", and the reason code.
# ⚠️ AND THE OVERAGE CAP, which is the stop a plan with overages enabled reaches
# instead of the monthly one. Its sentence comes from the service, not the binary, so
# it is matched loosely (an overage and a limit within a few words of each other)
# beside the error code the binary does carry. "You've used your monthly included
# requests and are now using overages" names no limit and is a warning, not a stop.
KIRO_LIMIT_RE = re.compile(
    r"monthly (usage|request) limit"
    r"|monthlyLimitReached|MONTHLY_REQUEST_COUNT"
    r"|OverageRequestLimitExceeded"
    r"|\boverages?\b[^\"\n]{0,60}\blimit\b|\blimit\b[^\"\n]{0,60}\boverages?\b",
    re.I,
)
# Session updates that carry the SESSION's words, which must not convict it.
KIRO_OWN_WORDS = ("user_message_chunk", "agent_message_chunk", "agent_thought_chunk",
                  "tool_call", "tool_call_update", "plan")
RESETS_RE = re.compile(r"resets [^\"\\\n]*")
# ⚠️ CODEX does not say "resets". Its sentence is "You've hit your usage limit. Upgrade
# to Pro (...), visit ... to purchase more credits or try again at 5:59 PM." and, for a
# reset more than a day off, "try again at Oct 14th, 2026 4:01 AM." (read out of 39 limit
# errors in ~/.codex/sessions). The clock time names no zone: it is codex's local one.
# Missed by RESETS_RE, so a codex limit was recorded with no reset and the walk held the
# flat hour instead of until the time codex gave.
TRY_AGAIN_RE = re.compile(r"try again at [^\"\\\n.]*?[AP]M", re.I)


def resets_in(text):
    m = RESETS_RE.search(text or "")
    return m.group(0).strip() if m else ""


def try_again_in(text):
    m = TRY_AGAIN_RE.search(text or "")
    return m.group(0) if m else ""


# ⚠️ WHEN IT LIFTS, AS AN INSTANT — because the walk held a flat hour and never read
# it. Measured over 09-19..25: 36 of 124 sessions were a 429 on their first turn, e.g.
# twelve hourly starts from 08:22 to 19:23 against "resets 8pm (UTC)". The shapes seen:
# "resets 8pm (UTC)", "resets 1:40pm (UTC)", "resets Sep 28, 8pm (UTC)". Anything else
# is None, and the caller falls back to its flat hold rather than guessing.
RESET_AT_RE = re.compile(
    r"resets (?:(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}), )?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)\s*\(UTC\)",
    re.I,
)
# Codex's shape: "try again at 5:59 PM" or "try again at Oct 14th, 2026 4:01 AM".
TRY_AT_RE = re.compile(
    r"try again at (?:(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2})(?:st|nd|rd|th)?, (?P<year>\d{4}) )?"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>am|pm)",
    re.I,
)
RESET_MARGIN = 120              # seconds past the stated reset before retrying


def reset_at(resets, now):
    """The epoch second to retry at, or None if `resets` is not a shape we know."""
    import calendar
    import time as _t
    m = TRY_AT_RE.search(resets or "")
    if m:
        return _try_again_at(m, now)
    m = RESET_AT_RE.search(resets or "")
    if not m:
        return None
    h = int(m["h"]) % 12 + (12 if m["ap"].lower() == "pm" else 0)
    mi = int(m["m"] or 0)
    t = _t.gmtime(now)
    if m["mon"]:
        try:
            mon = _t.strptime(m["mon"], "%b").tm_mon
        except ValueError:
            return None
        at = calendar.timegm((t.tm_year, mon, int(m["day"]), h, mi, 0))
        if at < now - 86400:    # a date already well past means next year
            at = calendar.timegm((t.tm_year + 1, mon, int(m["day"]), h, mi, 0))
    else:
        at = calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0))
        if at <= now:           # a time of day already gone today is tomorrow's
            at += 86400
    return at + RESET_MARGIN


def _try_again_at(m, now):
    """Codex's stamp, which is on the local clock of the box it ran on (this one)."""
    import time as _t
    h = int(m["h"]) % 12 + (12 if m["ap"].lower() == "pm" else 0)
    mi = int(m["m"])
    t = _t.localtime(now)
    if m["mon"]:
        try:
            mon = _t.strptime(m["mon"], "%b").tm_mon
        except ValueError:
            return None
        at = _t.mktime((int(m["year"]), mon, int(m["day"]), h, mi, 0, 0, 0, -1))
    else:
        at = _t.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, 0, 0, 0, -1))
        if at <= now:           # a time of day already gone today is tomorrow's
            at += 86400
    return at + RESET_MARGIN


def _kiro_said(rec):
    """Whether one stream record is the session's own words rather than kiro's."""
    stack = [rec]
    while stack:
        d = stack.pop()
        if isinstance(d, dict):
            # `finalText` is the run's closing record repeating the session's last
            # message, so it is the session's words too.
            if d.get("sessionUpdate") in KIRO_OWN_WORDS or "finalText" in d:
                return True
            stack.extend(d.values())
        elif isinstance(d, list):
            stack.extend(d)
    return False


def _opencode_error(rec):
    """The `error` event's data: {message, statusCode, ...}, or {}."""
    err = rec.get("error")
    data = err.get("data") if isinstance(err, dict) else None
    return data if isinstance(data, dict) else {}


def classify(raw, agent):
    """(is_limit, resets) for one run log's bytes."""
    if agent == "opencode":
        for line in raw.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            if rec is None:
                if LIMIT_RE.search(line):
                    return True, resets_in(line)
            elif isinstance(rec, dict) and rec.get("type") == "error":
                data = _opencode_error(rec)
                message = str(data.get("message") or "")
                if data.get("statusCode") == 429 or LIMIT_RE.search(message):
                    return True, resets_in(message)
        return False, ""
    if agent == "kiro":
        for line in raw.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            if rec is not None and _kiro_said(rec):
                continue
            if KIRO_LIMIT_RE.search(line) or (rec is None and LIMIT_RE.search(line)):
                return True, resets_in(line)
        return False, ""
    if agent != "codex":
        try:
            rec = json.loads(raw)
        except Exception:
            rec = None
        if isinstance(rec, dict):
            result = str(rec.get("result") or "")
            errored = bool(rec.get("is_error")) or rec.get("terminal_reason") == "api_error"
            if rec.get("api_error_status") == 429 or (errored and LIMIT_RE.search(result)):
                return True, resets_in(result) or resets_in(raw)
            return False, ""
    m = LIMIT_RE.search(raw)
    if not m:
        return False, ""
    if agent == "codex":
        # Read from the limit sentence on, so a "try again at" earlier in the stream
        # (the session's own words) is not taken for it.
        return True, resets_in(raw) or try_again_in(raw[m.start():])
    return True, resets_in(raw)


def main(argv):
    if len(argv) != 3:
        print("usage: limit.py <log> <agent>", file=sys.stderr)
        return 2
    path, agent = argv[1], argv[2]
    try:
        with open(path, errors="replace") as fh:
            raw = fh.read()
    except OSError:
        raw = ""
    limit, resets = classify(raw, agent)
    print("limit=%d" % int(limit))
    print("resets=%s" % shlex.quote(resets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
