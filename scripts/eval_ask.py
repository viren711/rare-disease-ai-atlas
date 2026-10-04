#!/usr/bin/env python3
"""Evaluate and calibrate the literature RAG on eval/questions.yaml.

Retrieval + guards (no LLM, ~1 s/question):
  * Hit@5 (any expected PMID in the top 5), P@5 (share of the top 5 that is expected),
    MRR over the reranked candidate list -- answerable questions only
  * layer-1 / layer-2 refusal accuracy on all questions
  * score distributions and a proposed retrieval.min_rerank_score (keeps >= --min-answer-rate
    of answerable questions above the floor, then refuses as many unanswerable ones as possible)
  * proposed topic-gate junk thresholds (midpoint of the in-domain / off-topic gap)

    .venv/bin/python scripts/eval_ask.py                 # report only
    .venv/bin/python scripts/eval_ask.py --apply         # write min_rerank_score to config/atlas.yaml
    .venv/bin/python scripts/eval_ask.py --llm           # also run the full pipeline with the real LLM
    .venv/bin/python scripts/eval_ask.py --llm --ids krabbe_treatments,pizza
Writes eval/report.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def propose_threshold(pos: list[float], neg: list[float], min_answer_rate: float,
                      margin: float = 0.5) -> float:
    """Highest floor that keeps >= min_answer_rate of `pos` (answerable top scores) answerable,
    placed midway to the next lower score, but never closer than `margin` to the lowest
    answerable score that must pass (lay phrasings score lower than the labelled set; erring
    towards answering is safe because layer 3 / the sentinel still refuses)."""
    if not pos:
        return 0.0
    s = sorted(pos)
    allowed_drop = int(len(s) * (1 - min_answer_rate) + 1e-9)
    floor_at = s[allowed_drop]               # lowest answerable score that must still pass
    below = [x for x in s[:allowed_drop]] + [x for x in neg if x < floor_at]
    lower = max(below) if below else floor_at - 1.0
    mid = (floor_at + lower) / 2 if lower < floor_at else floor_at - 0.05
    return round(min(mid, floor_at - margin), 2)


def apply_threshold(value: float) -> None:
    path = ROOT / "config" / "atlas.yaml"
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"(^\s*min_rerank_score:\s*)([-0-9.]+)(.*)$",
                     lambda m: f"{m.group(1)}{value}{'  # calibrated by scripts/eval_ask.py'}",
                     text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("could not find retrieval.min_rerank_score in config/atlas.yaml")
    path.write_text(new, encoding="utf-8")
    print(f"wrote retrieval.min_rerank_score = {value} to {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", default=str(ROOT / "eval" / "questions.yaml"))
    ap.add_argument("--apply", action="store_true", help="write the proposed min_rerank_score")
    ap.add_argument("--min-answer-rate", type=float, default=1.0)
    ap.add_argument("--margin", type=float, default=0.5)
    ap.add_argument("--llm", action="store_true", help="run the full pipeline with the real LLM")
    ap.add_argument("--ids", help="comma-separated question ids to restrict to")
    args = ap.parse_args()

    import yaml

    from rag import guard
    from rag.config import load_config
    from rag.retriever import get_retriever

    cfg = load_config()
    qs = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    if args.ids:
        keep = set(args.ids.split(","))
        qs = [q for q in qs if q["id"] in keep]

    r = get_retriever()
    thr = cfg["guard"]["grounding"]["min_rerank_score"]
    print(f"index: {r.meta['chunk_count']} abstracts, {r.meta['embedding_model']}; "
          f"current min_rerank_score={thr}\n")

    rows = []
    for q in qs:
        t0 = time.time()
        vec = r.embed_query(q["question"])
        topic = guard.topic_gate(q["question"], vec)
        seed, cen = guard.topic_scores(q["question"], vec)
        hits = r.search(q["question"], vec, final_k=None)       # full reranked candidate list
        top5 = [h.chunk["pmid"] for h in hits[:5]]
        exp = set(q.get("expected_pmids") or [])
        rank = next((i + 1 for i, h in enumerate(hits) if h.chunk["pmid"] in exp), None)
        top = hits[0].score if hits else float("-inf")
        row = {
            "id": q["id"], "kind": q["kind"], "broad": bool(q.get("broad")), "question": q["question"],
            "topic_allowed": topic.allowed, "topic_reason": topic.reason,
            "seed_sim": round(seed, 3), "centroid_sim": round(cen, 3),
            "top_score": round(top, 3), "top5": top5,
            "hit5": bool(exp & set(top5)) if exp else None,
            "p5": round(len(exp & set(top5)) / 5, 2) if exp else None,
            "rr": (1.0 / rank) if rank else 0.0,
            "retrieval_ms": int((time.time() - t0) * 1000),
            "top_title": hits[0].chunk.get("title", "")[:90] if hits else "",
        }
        rows.append(row)
        print(f"{q['kind'][:6]:6} {q['id']:22} topic={'Y' if topic.allowed else 'n'} "
              f"seed={seed:.3f} cen={cen:.3f} top={top:6.2f} "
              f"hit5={row['hit5']} p5={row['p5']} rr={row['rr']:.2f} {row['retrieval_ms']}ms")

    ans = [x for x in rows if x["kind"] == "answerable"]
    una = [x for x in rows if x["kind"] == "unanswerable"]
    off = [x for x in rows if x["kind"] == "off_topic"]

    report: dict = {"n": len(rows)}
    if ans:
        report["hit_at_5"] = round(sum(x["hit5"] for x in ans) / len(ans), 3)
        report["precision_at_5"] = round(sum(x["p5"] for x in ans) / len(ans), 3)
        report["mrr"] = round(sum(x["rr"] for x in ans) / len(ans), 3)
        for label, sub in (("precise", [x for x in ans if not x.get("broad")]),
                           ("broad", [x for x in ans if x.get("broad")])):
            if sub:
                report[f"{label}_n"] = len(sub)
                report[f"{label}_hit_at_5"] = round(sum(x["hit5"] for x in sub) / len(sub), 3)
                report[f"{label}_mrr"] = round(sum(x["rr"] for x in sub) / len(sub), 3)

    # --- calibration ---------------------------------------------------------------
    pos = [x["top_score"] for x in ans]
    neg = [x["top_score"] for x in una + off if x["topic_allowed"]]
    proposed = propose_threshold(pos, neg, args.min_answer_rate, args.margin)
    report["score_answerable"] = sorted(pos)
    report["score_unanswerable_or_offtopic_passing_layer1"] = sorted(neg)
    report["proposed_min_rerank_score"] = proposed

    in_dom = [x for x in ans + una]
    report["topic"] = {
        "in_domain_seed_min": min((x["seed_sim"] for x in in_dom), default=None),
        "in_domain_centroid_min": min((x["centroid_sim"] for x in in_dom), default=None),
        "off_topic_seed_max": max((x["seed_sim"] for x in off), default=None),
        "off_topic_centroid_max": max((x["centroid_sim"] for x in off), default=None),
    }

    def refused_at(x, floor):
        return (not x["topic_allowed"]) or x["top_score"] < floor

    for name, floor in (("current", thr), ("proposed", proposed)):
        correct = sum((not refused_at(x, floor)) if x["kind"] == "answerable" else refused_at(x, floor)
                      for x in rows)
        report[f"refusal_accuracy_layers12_{name}"] = round(correct / len(rows), 3) if rows else None
    report["offtopic_refused_at_layer1"] = f"{sum(not x['topic_allowed'] for x in off)}/{len(off)}"

    # --- optional end-to-end with the real LLM ---------------------------------------
    if args.llm:
        from rag import pipeline

        e2e = []
        for q in qs:
            res = pipeline.ask(q["question"])
            ok = (not res["refused"]) if q["kind"] == "answerable" else res["refused"]
            e2e.append({"id": q["id"], "kind": q["kind"], "status": res["status"], "correct": ok,
                        "reason": res["reason"], "elapsed_ms": res["elapsed_ms"],
                        "words": len(res["answer"].split()), "cited": res.get("cited"),
                        "answer": res["answer"]})
            print(f"  llm {q['id']:22} {res['status']:13} {res['elapsed_ms'] / 1000:6.1f}s "
                  f"{'OK ' if ok else 'BAD'} {res['reason'] or ''}")
        report["e2e_refusal_accuracy"] = round(sum(x["correct"] for x in e2e) / len(e2e), 3)
        report["e2e_answer_rate_answerable"] = round(
            sum(x["status"] == "answered" for x in e2e if x["kind"] == "answerable")
            / max(1, sum(x["kind"] == "answerable" for x in e2e)), 3)
        ms = [x["elapsed_ms"] for x in e2e if x["status"] == "answered"]
        report["e2e_mean_latency_s_answered"] = round(sum(ms) / len(ms) / 1000, 1) if ms else None
        report["e2e"] = e2e

    report["rows"] = rows
    out = ROOT / "eval" / "report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== summary ===")
    for k, v in report.items():
        if k not in ("rows", "e2e"):
            print(f"  {k}: {v}")
    print(f"  report -> {out}")
    if args.apply:
        apply_threshold(proposed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
