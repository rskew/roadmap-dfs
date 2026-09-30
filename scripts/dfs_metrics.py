#!/usr/bin/env python3
"""What our own sessions cost, read out of Claude Code's transcripts.

Every assistant turn in ~/.claude/projects/*/*.jsonl carries cache_read_input_tokens,
cache_creation_input_tokens and a timestamp, so the whole question is answerable from
history without instrumenting anything. This is the loop that keeps
roadmap-dfs/SKILL.md's session budget honest: re-run it after a batch of work and
see whether the numbers it is built on still hold.

  python3 <scripts>/dfs_metrics.py [--projects GLOB]
"""
import json, glob, os, re, math, sys, statistics as st
from datetime import datetime

# Claude Code's transcripts: CLAUDE_CONFIG_DIR when set, else ~/.claude.
GLOB = os.path.join(os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude'),
                    'projects', '*', '*.jsonl')
for i, a in enumerate(sys.argv):
    if a == '--projects' and i + 1 < len(sys.argv): GLOB = sys.argv[i + 1]

# ⚠️ WRITE is DEFINED in dfs_context.py and imported, not restated. That file is
# already the shared transcript reader (the runner and the TUI both take peak_and_turns
# from it), and dfs_runs.py asks the same what-did-it-write question to tell a
# cut-off session from an idle one. Hyphenated filename, hence importlib.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('dfs_context',
                                     os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  'dfs_context.py'))
_sc = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sc)
WRITE = _sc.WRITE
def ts(s): return datetime.fromisoformat(s.replace('Z', '+00:00'))
def q(v, p):
    v = sorted(v); return v[min(int(len(v) * p), len(v) - 1)] if v else 0

def is_productive(content):
    """Whether any content block performs the session's first real write."""
    for block in content or []:
        if not isinstance(block, dict) or block.get('type') != 'tool_use':
            continue
        if block.get('name') in ('Edit', 'Write', 'NotebookEdit'):
            return True
        if block.get('name') == 'Bash' and WRITE.search(
                (block.get('input') or {}).get('command', '') or ''):
            return True
    return False


turns, sessions, ttl = [], {}, {}
for f in glob.glob(GLOB):
    sid, prev, responses = os.path.basename(f)[:-6], None, {}
    for line in open(f, errors='replace'):
        try: r = json.loads(line)
        except Exception: continue
        if r.get('type') != 'assistant' or not r.get('timestamp'): continue
        m = r.get('message') or {}; u = m.get('usage') or {}
        if not u: continue
        # ⚠️ ONE API RESPONSE IS ONE LINE PER CONTENT BLOCK — thinking, text and tool_use
        # each get their own line carrying the SAME usage. Counting lines overstated turns
        # by ~1.7x and double-counted 2.0B of context; it is what made G look like 987/turn
        # (it is 1,707) and the optimum 52 turns (it is ~37).
        # The later blocks still matter for first-write detection. The transcript
        # usually puts thinking first and tool_use later, so skipping a repeated id
        # here would count usage correctly while moving R to a later response.
        mid = m.get('id')
        if mid in responses:
            responses[mid]['prod'] |= is_productive(m.get('content'))
            continue
        for k, v in (u.get('cache_creation') or {}).items():
            if isinstance(v, int) and v: ttl[k] = ttl.get(k, 0) + v
        t = ts(r['timestamp'])
        w = u.get('cache_creation_input_tokens', 0) or 0
        rd = u.get('cache_read_input_tokens', 0) or 0
        row = dict(t=t, w=w, r=rd, ctx=w + rd, out=u.get('output_tokens', 0) or 0,
                   prod=is_productive(m.get('content')),
                   gap=(t - prev).total_seconds() if prev else None)
        responses[mid] = row
        prev = t; turns.append(row); sessions.setdefault(sid, []).append(row)

print(f"{len(turns):,} assistant responses across {len(sessions)} sessions\n")

print("TTL in use:", ", ".join(f"{k}={v:,}" for k, v in sorted(ttl.items(), key=lambda x: -x[1])) or "none")
# Write multiplier is 1.25x on the 5-minute TTL and 2x on the 1-hour one, so it is READ
# OFF the transcripts rather than assumed — assuming 1.25 while the harness used 1h is
# exactly the error this line exists to prevent. Reads are 0.1x on every model but
# Fable/Mythos 5.1 (0.025x).
h = ttl.get('ephemeral_1h_input_tokens', 0); m = ttl.get('ephemeral_5m_input_tokens', 0)
WMUL = 2.0 if h >= m else 1.25
print(f"write multiplier {WMUL}x  (1h tokens {h:,} vs 5m {m:,})")
tw, tr = sum(t['w'] for t in turns), sum(t['r'] for t in turns)
cw, cr, co = tw * 5 * WMUL / 1e6, tr * 5 * 0.1 / 1e6, sum(t['out'] for t in turns) * 25 / 1e6
print(f"cache write {tw:>14,}   read {tr:>16,}")
print(f"at Opus 5 list: writes ${cw:,.0f}  reads ${cr:,.0f}  output ${co:,.0f}  "
      f"-> reads are {cr/(cw+cr+co)*100:.0f}% of the bill\n")

print("gap before a turn vs what that turn pays (the TTL cliff)")
print(f"  {'gap':<12}{'n':>7}{'med write':>12}{'med read':>12}")
for name, lo, hi in [("<5 min",0,300),("5-15 min",300,900),("15-60 min",900,3600),(">60 min",3600,1e9)]:
    sel = [t for t in turns if t['gap'] is not None and lo <= t['gap'] < hi]
    if sel: print(f"  {name:<12}{len(sel):>7}{st.median([t['w'] for t in sel]):>12,.0f}{st.median([t['r'] for t in sel]):>12,.0f}")

longs = {s: v for s, v in sessions.items() if len(v) >= 20}
ns = [len(v) for v in longs.values()]
pk = [max(t['ctx'] for t in v) for v in longs.values()]
first = [v[0]['ctx'] for v in longs.values() if v[0]['ctx']]
ramp = [(next((i for i, t in enumerate(v) if t['prod']), None), v) for v in longs.values()]
ramp = [(i, v) for i, v in ramp if i is not None]
R = [v[i]['ctx'] for i, v in ramp]
RC = [v[i]['ctx'] / max(t['ctx'] for t in v) for i, v in ramp]
growth = [max(t['ctx'] for t in v) / len(v) for v in longs.values()]

print(f"\nshape of {len(longs)} sessions >=20 responses    p25      median         p75")
for label, v, f in [("responses", ns, '{:>11,.0f}'), ("peak context (C)", pk, '{:>11,.0f}'),
                    ("context at 1st write (R)", R, '{:>11,.0f}'), ("R / C", RC, '{:>11.2f}')]:
    print(f"  {label:<26}" + "".join(f.format(q(v, p)) for p in (.25, .5, .75)))

F, G = st.median(first), st.median(growth)
N = math.sqrt(20 * WMUL * F / G)
print(f"\n  floor F = {F:,.0f} (first-response context)   growth G = {G:,.0f}/response   write {WMUL}x")
print(f"  OPTIMAL SESSION LENGTH  N = sqrt(20*W*F/G) = {N:.0f} responses   (within 10%: {N*0.48:.0f}-{N*2.1:.0f})")
def cost_per_response(n):
    return (0.1 * (n * F + G * n * n / 2) + WMUL * (F + G * n)) / n


print(f"  current median {q(ns,.5)} responses costs "
      f"{cost_per_response(q(ns,.5)) / cost_per_response(N):.1f}x the optimum per response of work")
