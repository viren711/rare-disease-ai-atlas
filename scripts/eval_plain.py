#!/usr/bin/env python3
"""Evaluate atlas.explain plain-language output on eval/plain_eval.yaml.

Per case: source (llm/template), latency, citation validity, Flesch-Kincaid grade, % factual sentences cited,
fabricated terms, per-fact LLM acceptance. Prints a table + recommendation; writes eval/plain_report.json.

    .venv/bin/python scripts/eval_plain.py                 # LLM on, cache bypassed (honest latency)
    .venv/bin/python scripts/eval_plain.py --no-llm        # template only (instant)
    .venv/bin/python scripts/eval_plain.py --cache         # allow cache hits
    PLAIN_MODEL=qwen3-4b PLAIN_TIMEOUT=60 .venv/bin/python scripts/eval_plain.py   # try another model
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from atlas import api, explain  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--cache", action="store_true", help="do not bypass the disk cache")
    ap.add_argument("--only", help="comma-separated case names")
    args = ap.parse_args()
    cases = yaml.safe_load((ROOT / "eval" / "plain_eval.yaml").read_text())["cases"]
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["name"] in keep]
    if not args.cache:
        import tempfile
        explain.PLAIN_CACHE = Path(tempfile.mkdtemp(prefix="plain_eval_"))
    rows = []
    for c in cases:
        t0 = time.time()
        try:
            if c["kind"] == "plan":
                plan = api.action_plan(c["a"], c.get("b"))
                r = explain.plain_explanation(plan, c["level"], use_llm=not args.no_llm)
            elif c["kind"] == "disease":
                r = explain.plain_disease(api.disease_card(c["a"]), c["level"], use_llm=not args.no_llm)
            else:
                plan = api.action_plan(c["edge_from_plan"])
                r = explain.plain_edge(api.edge(plan["path"][0]["edge_id"]), c["level"])
        except Exception as e:  # noqa: BLE001
            rows.append({"name": c["name"], "error": f"{type(e).__name__}: {e}"})
            continue
        k = r["checks"]
        rows.append({"name": c["name"], "level": c["level"], "source": r["source"],
                     "latency_s": round(time.time() - t0, 1), "citations_valid": k["citations_valid"],
                     "fk_grade": k["fk_grade"], "pct_cited": k["pct_cited"], "fabricated": k["fabricated"],
                     "verified": k["ok"], "facts_rewritten": f"{k.get('n_rewritten', '-')}/{k.get('n_facts', '-')}",
                     "llm_rejected": k.get("llm_rejected"), "text": r["text"]})
    hdr = f"{'case':24} {'lvl':9} {'src':8} {'sec':>5} {'cite_ok':7} {'FK':>5} {'%cited':>6} {'rewr':>5} fabricated"
    print(hdr + "\n" + "-" * len(hdr))
    for r in rows:
        if "error" in r:
            print(f"{r['name']:24} ERROR {r['error']}")
            continue
        print(f"{r['name']:24} {r['level']:9} {r['source']:8} {r['latency_s']:>5} {str(r['citations_valid']):7} "
              f"{r['fk_grade']:>5} {r['pct_cited']:>6} {r['facts_rewritten']:>5} {r['fabricated'] or ''}")
    ok = [r for r in rows if "error" not in r]
    llm = [r for r in ok if r["source"] == "llm"]
    tried = [r for r in ok if r["facts_rewritten"] != "-/-"]
    if tried:
        print(f"\nLLM pass rate (verified llm output / LLM attempts): {len(llm)}/{len(tried)} = "
              f"{100 * len(llm) / len(tried):.0f}%")
        lat = [r["latency_s"] for r in tried]
        print(f"latency s: median {st.median(lat):.1f}, max {max(lat):.1f}")
        for r in tried:
            if r["source"] != "llm":
                print(f"  fell back [{r['name']}]: {r['llm_rejected']}")
    print(f"all outputs citation-valid: {all(r['citations_valid'] for r in ok)}; "
          f"fabricated-term cases: {sum(1 for r in ok if r['fabricated'])}; "
          f"median FK {st.median([r['fk_grade'] for r in ok]):.1f}")
    print("\nRecommendation: keep the configured 3B model, timeout 45 s, max_tokens 260, cache on; the template is "
          "the guaranteed floor.  Lower per-case latency comes from the disk cache (pre-warm demo cases) and from the "
          "streaming generator.  Switch models only if the pass rate above is < 90%.")
    (ROOT / "eval" / "plain_report.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
