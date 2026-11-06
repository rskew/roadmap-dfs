#!/usr/bin/env python3
"""W153 E1 accounting: what Hypothesis answer pages cost, and what an `assumed` status would have saved.

  python3 docs/dfs-w153-e1.py            (prints the roadmap-dfs-as-at-d595c44a table, then today's roadmap-dfs)

Reads every Hypothesis of roadmap-dfs, shop-app and farm-app (arguments as dfs-w153-mine.py).
It counts, per project and in total:
  * hypotheses, those naming an answer page, the page files' bytes (an output-token
    estimate is bytes / 4, a rough figure), pages missing from disk;
  * pages that would still be made under E1 ("a page only when the worker would otherwise
    raise"): taken here as the pages of hypotheses whose node also carries a raise;
  * the words the author reads of Hypotheses (whole, and the `Wrong if` half the screen
    shows on its own) today, and if the hypotheses whose `Wrong if` names the author were
    marked `assumed` and the rest were left to the session's own evidence;
  * the author's rulings on hypotheses (corrections, not backtracks): how many of them were
    on a hypothesis that carries a page, that names the author, that the author refuted;
  * how many rulings used the page's own option label as the reply (the only sign in the
    files that the page was opened).
"""
import importlib.util, re, statistics, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("mine", Path(__file__).with_name("dfs-w153-mine.py"))
mine = importlib.util.module_from_spec(spec); spec.loader.exec_module(mine)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

BYTES_PER_TOKEN = 4          # rough: the Background's "12 KB, about 3k output tokens"
# the answer-page template the tool ships; a page built from it carries its last 400 characters
_TPL = Path(sys.argv[0]).resolve().parent.parent / "templates" / "_TEMPLATE_ANSWER.html"
TEMPLATE = None
for cand in (_TPL, Path("/nix/store/pbqwhdjz8dds81713jnbf6b2j7wg140x-roadmap-dfs-d02e23e/share/roadmap-dfs/templates/_TEMPLATE_ANSWER.html")):
    if cand.exists():
        TEMPLATE = cand.read_text(); break


def split_wrong_if(h):
    m = re.search(r"wrong if", h, re.I)
    body = h[m.start():] if m else ""
    # the page path trails the Hypothesis; it is not words the author reads
    return mine.PAGE.sub("", body).strip()


def names_author(h):
    return bool(re.search(r"\bauthor\b", split_wrong_if(h), re.I))


def report(label, argv):
    a = mine.args_of(argv)
    P = mine.load(a)
    import tree
    # where page files live, per project (artefacts are not in the task files)
    root = {"roadmap-dfs": Path(mine.main_checkout()), "shop-app": Path(a.shop), "farm-app": Path(a.farm)}
    print(f"== {label}")
    tot = dict(hyp=0, page=0, missing=0, bytes=[], raised=0, rw=0, rw_auth=0, rw_other=0, w=0, rulings=0,
               r_page=0, r_auth=0, r_ref=0, r_label=0, r_auth_ref=0, r_other=0, r_other_ref=0, r_nopage=0, r_nopage_ref=0)
    for proj, T in P.items():
        c = dict(hyp=0, page=0, missing=0, raised=0, rw=0, rw_auth=0, rw_other=0, w=0)
        sizes = []
        own = []          # bytes beyond the template, for pages built from it
        n_tpl = 0
        raise_nodes = set()
        rulings = []
        for tid, t in T.items():
            by = {n["id"]: n for n in t["nodes"]}
            for e in t["log"]:
                if e["kind"] == "raise" and e["args"]:
                    raise_nodes.add((tid, e["args"][0]))
            for n in t["nodes"]:
                h = mine.hypothesis(n)
                if not h:
                    continue
                c["hyp"] += 1
                c["w"] += mine.words(mine.PAGE.sub("", h))
                rw = split_wrong_if(h)
                c["rw"] += mine.words(rw)
                if names_author(h):
                    c["rw_auth"] += mine.words(rw)
                else:
                    c["rw_other"] += mine.words(rw)
                m = mine.PAGE.search(h)
                if m:
                    c["page"] += 1
                    f = root[proj] / m.group(0)
                    if f.exists():
                        sizes.append(f.stat().st_size)
                        txt = f.read_text(errors="replace")
                        if TEMPLATE and TEMPLATE[-400:] in txt:
                            n_tpl += 1
                            own.append(max(0, len(txt.encode()) - len(TEMPLATE.encode())))
                    else:
                        c["missing"] += 1
                    if (tid, n["id"]) in raise_nodes:
                        c["raised"] += 1
            for r in tree.corrections(t):
                if r["disp"] == "backtrack":
                    continue
                n = by.get(r["node"]); h = mine.hypothesis(n) if n else ""
                if not h:
                    continue
                m = mine.PAGE.search(h)
                first = r["body"].strip().split("\n")[0][:30]
                label_hit = False
                if m and (root[proj] / m.group(0)).exists():
                    label_hit = first in (root[proj] / m.group(0)).read_text(errors="replace")
                rulings.append(dict(page=bool(m), auth=names_author(h), ref=r["disp"] == "refuted", label=label_hit))
        kb = sorted(sizes)
        med = statistics.median(kb) if kb else 0
        print(f"{proj}: hypotheses {c['hyp']}, with a page {c['page']} ({c['missing']} files missing), "
              f"page bytes total {sum(kb)} median {med:.0f}, est. output tokens {sum(kb)//BYTES_PER_TOKEN}")
        print(f"  pages built from the answer template: {n_tpl}; their bytes beyond the template: total {sum(own)}, median {statistics.median(own) if own else 0:.0f}")
        print(f"  pages on a node that also carries a raise (would still be made under E1): {c['raised']}")
        print(f"  author words: Hypothesis text {c['w']}; Wrong-if halves {c['rw']} "
              f"(naming the author {c['rw_auth']}, not {c['rw_other']})")
        k = len(rulings)
        print(f"  author rulings on a hypothesis: {k}; refuted {sum(r['ref'] for r in rulings)}; "
              f"on a page-bearing one {sum(r['page'] for r in rulings)}; on one naming the author {sum(r['auth'] for r in rulings)}; "
              f"replying with the page's own label {sum(r['label'] for r in rulings)}")
        for key, v in (("hyp", c["hyp"]), ("page", c["page"]), ("missing", c["missing"]), ("raised", c["raised"]),
                       ("rw", c["rw"]), ("rw_auth", c["rw_auth"]), ("rw_other", c["rw_other"]), ("w", c["w"])):
            tot[key] += v
        tot["bytes"] += kb
        tot.setdefault("own", []); tot["own"] += own
        tot["n_tpl"] = tot.get("n_tpl", 0) + n_tpl
        tot["rulings"] += k
        tot["r_page"] += sum(r["page"] for r in rulings)
        tot["r_auth"] += sum(r["auth"] for r in rulings)
        tot["r_ref"] += sum(r["ref"] for r in rulings)
        tot["r_label"] += sum(r["label"] for r in rulings)
        tot["r_auth_ref"] += sum(r["auth"] and r["ref"] for r in rulings)
        tot["r_other"] += sum(not r["auth"] for r in rulings)
        tot["r_other_ref"] += sum((not r["auth"]) and r["ref"] for r in rulings)
        tot["r_nopage"] += sum(not r["page"] for r in rulings)
        tot["r_nopage_ref"] += sum((not r["page"]) and r["ref"] for r in rulings)
    kb = sorted(tot["bytes"])
    print("TOTAL")
    print(f"  hypotheses {tot['hyp']}, with a page {tot['page']}; page bytes {sum(kb)}, median {statistics.median(kb):.0f}, "
          f"est. output tokens {sum(kb)//BYTES_PER_TOKEN} ({sum(kb)//BYTES_PER_TOKEN/max(1,len(kb)):.0f} a page)")
    print(f"  E1 pages saved: {tot['page']} of {tot['hyp']} hypotheses have one, {tot['raised']} of those sit on a node with a raise and would stay, "
          f"so {tot['page'] - tot['raised']} pages are not made")
    ow = sorted(tot["own"])
    print(f"  tokens of those pages (bytes / {BYTES_PER_TOKEN}): whole files {sum(kb)//BYTES_PER_TOKEN}; if the template is copied not typed, "
          f"{(sum(ow) + sum(kb) - sum(o + len(TEMPLATE.encode()) for o in ow))//BYTES_PER_TOKEN}")
    print(f"  of those pages {tot['n_tpl']} were built from the answer template ({len(TEMPLATE.encode())} bytes); beyond it they hold {sum(ow)} bytes "
          f"(median {statistics.median(ow):.0f}), about {sum(ow)//BYTES_PER_TOKEN} tokens if the template is copied and not typed")
    print(f"  author words (Wrong-if halves): today {tot['rw']}; if only the hypotheses naming the author are shown as `assumed` {tot['rw_auth']} "
          f"(saves {tot['rw_other']} = {100*tot['rw_other']/max(1,tot['rw']):.0f}%)")
    print(f"  rulings: {tot['rulings']}, refuted {tot['r_ref']}; on hypotheses naming the author {tot['r_auth']} (refuted {tot['r_auth_ref']}); "
          f"NOT naming the author {tot['r_other']} (refuted {tot['r_other_ref']}); with no page {tot['r_nopage']} (refuted {tot['r_nopage_ref']}); "
          f"replying with the page's option label {tot['r_label']}")
    print()


if __name__ == "__main__":
    report("roadmap-dfs as at d595c44a (the Background's pass), shop-app and farm-app as they are", ["--at", "d595c44a"])
    report("today's files", [])
