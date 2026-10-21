#!/usr/bin/env python3
"""The whole suite, in parallel: every test_*.py beside this file, split into shards that run
in a pool of subprocesses, one per core unless -j says otherwise.

A shard is a few tests of one class, packed to about SHARD_SECONDS by what each took last time
(remembered in $XDG_CACHE_HOME/roadmap-dfs/test-times.json), and the slowest shards start first,
so the run takes about as long as its slowest test. Each shard is its own `python3`, so a test
that patches a module or os.environ cannot reach another shard's.

Run:  python3 roadmap-dfs/scripts/test_all.py [-j N] [name ...]
      a name is a module, class or test id prefix (`test_chain`, `test_web.Web`); none runs all.
      With /tmp mounted noexec, set TMPDIR to a directory inside the repo, as for any one module.
"""
import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARD_SECONDS = 2.0     # what a shard is packed to; past it, a subprocess per shard costs little
UNKNOWN_SECONDS = 0.5   # a test with no time remembered
MARK = "\0test_all "    # the line a shard ends its stdout with, ahead of its results


def times_file():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "roadmap-dfs" / "test-times.json"


def ids(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from ids(t)
        else:
            yield t.id()


def discover(names):
    sys.path.insert(0, str(HERE))
    loader = unittest.TestLoader()
    suite = loader.discover(str(HERE), pattern="test_*.py", top_level_dir=str(HERE))
    # a module that does not import is a _FailedTest: run it by name so its shard reports the error
    found = [i.replace("unittest.loader._FailedTest.", "") for i in ids(suite)
             if not i.startswith(Path(__file__).stem + ".")]
    return [i for i in found if not names or any(i == n or i.startswith(n + ".") for n in names)]


def shards(test_ids, known):
    """Consecutive tests of one class, packed to about SHARD_SECONDS, slowest shard first."""
    out, cur, cur_cls, cost = [], [], None, 0.0
    for t in test_ids:
        cls = t.rsplit(".", 1)[0]
        if cur and (cls != cur_cls or cost >= SHARD_SECONDS):
            out.append((cost, cur))
            cur, cost = [], 0.0
        cur_cls = cls
        cur.append(t)
        cost += known.get(t, UNKNOWN_SECONDS)
    if cur:
        out.append((cost, cur))
    return sorted(out, key=lambda s: -s[0])


class TimedResult(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.took = {}

    def startTest(self, test):
        self._t0 = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.took[test.id()] = time.monotonic() - self._t0
        super().stopTest(test)


def run_shard(names):
    """In the shard's own process: run the tests, the report to stderr, the tally to stdout."""
    sys.path.insert(0, str(HERE))
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    r = unittest.TextTestRunner(stream=sys.stderr, verbosity=2, resultclass=TimedResult).run(suite)
    tally = {"run": r.testsRun, "failures": len(r.failures), "errors": len(r.errors),
             "skipped": len(r.skipped), "took": r.took}
    sys.stdout.write("\n" + MARK + json.dumps(tally) + "\n")
    return 0 if r.wasSuccessful() else 1


def spawn(names, timeout):
    t0 = time.monotonic()
    try:
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--shard", *names],
                           cwd=str(HERE), capture_output=True, text=True, timeout=timeout)
        out, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out, err, rc = "", "%s\nthe shard ran past %ds and was killed" % (e.stderr or "", timeout), -1
    tally = None
    head, sep, tail = out.rpartition(MARK)
    if sep:
        tally = json.loads(tail)
    if tally is None:   # it died before reporting: every test in it is an error
        tally = {"run": len(names), "failures": 0, "errors": len(names), "skipped": 0, "took": {}}
    return names, rc, head if sep else out, err, tally, time.monotonic() - t0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--timeout", type=int, default=900, help="seconds one shard may run")
    ap.add_argument("--slowest", type=int, default=10, help="how many of the slowest tests to list")
    ap.add_argument("names", nargs="*")
    a = ap.parse_args(argv)

    t0 = time.monotonic()
    tf = times_file()
    try:
        known = json.loads(tf.read_text())
    except (OSError, ValueError):
        known = {}
    todo = shards(discover(a.names), known)
    if not todo:
        print("no tests match %s" % " ".join(a.names), file=sys.stderr)
        return 2
    print("%d tests in %d shards on %d jobs" % (sum(len(s) for _, s in todo), len(todo), a.jobs), flush=True)

    total = {"run": 0, "failures": 0, "errors": 0, "skipped": 0}
    failed = []
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        for f in concurrent.futures.as_completed([pool.submit(spawn, s, a.timeout) for _, s in todo]):
            names, rc, out, err, tally, _ = f.result()
            for k in total:
                total[k] += tally[k]
            known.update(tally["took"])
            ok = rc == 0 and not tally["failures"] and not tally["errors"]
            if not ok:
                failed.append((names, out, err))
            print("." if ok else "F", end="", flush=True)
    print()

    for names, out, err in failed:
        print("=" * 70 + "\nshard: %s\n" % " ".join(names) + "-" * 70)
        print((out + err).rstrip())
    wall = time.monotonic() - t0
    took = sorted(((known[t], t) for _, s in todo for t in s if t in known), reverse=True)
    if a.slowest and took:
        print("-" * 70 + "\nslowest:")
        for s, t in took[:a.slowest]:
            print("%8.2fs  %s" % (s, t))
    print("-" * 70)
    print("Ran %d tests in %.1fs (%.1fs of tests)" % (total["run"], wall, sum(s for s, _ in took)))
    bad = ", ".join("%s=%d" % (k, total[k]) for k in ("failures", "errors", "skipped") if total[k])
    print(("FAILED (%s)" % bad) if failed else ("OK (%s)" % bad if bad else "OK"))
    for f in sorted(Path(__file__).resolve().parent.glob(".dfs/**/*")):     # every test has a root of its own: none writes here
        if f.is_file() and f.stat().st_mtime >= time.time() - wall - 5:
            print("a test wrote %s at %s" % (f.relative_to(Path.cwd()) if f.is_relative_to(Path.cwd()) else f, time.strftime("%H:%M:%S", time.localtime(f.stat().st_mtime))))

    try:
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text(json.dumps(known, indent=0, sort_keys=True))
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--shard"]:
        sys.exit(run_shard(sys.argv[2:]))
    sys.exit(main(sys.argv[1:]))
