#!/usr/bin/env python3
"""Check the literature-RAG plumbing without a running LLM (ported from healthathon).

  * the retriever loads and returns citable hits (pmid/title/year), BM25 path works
  * disease scoping: no graph files -> no crash; keyword fallback; graph 'mentions' + subclass_of
  * layer 1 admits rare-disease questions, refuses off-topic ("best pizza in NYC")
  * layer 2 accepts/refuses on score
  * layer 3 rejects uncited / sentinel / out-of-range answers, drops single uncited sentences
  * pipeline.ask / ask_stream return the atlas/api.py contract shape against a stubbed LLM,
    including refusals, an unreachable LLM and a missing index

    .venv/bin/python scripts/smoke_test.py                      # against artifacts/index
    INDEX_DIR=/tmp/idx .venv/bin/python scripts/smoke_test.py    # against a --limit index
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL = "  ok  ", " FAIL "
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{PASS if condition else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        failures.append(name)


class StubLLM:
    """Stands in for the model server."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_finish_reason = "stop"

    def chat(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply

    def chat_stream(self, system: str, user: str):
        self.calls += 1
        for w in self.reply.split(" "):
            yield w + " "


class DeadLLM(StubLLM):
    def chat(self, system, user):
        from rag.llm import LLMError
        raise LLMError("connection refused (stub)")

    def chat_stream(self, system, user):
        from rag.llm import LLMError
        raise LLMError("connection refused (stub)")
        yield  # pragma: no cover


class FakeHit:
    def __init__(self, score):
        self.rerank_score = score
        self.dense_score = 0.0
        self.chunk = {"pmid": "1", "text": "x"}

    @property
    def score(self):
        return self.rerank_score


CONTRACT = {"answer", "refused", "reason", "sources"}
SOURCE_KEYS = {"n", "pmid", "title", "year", "journal", "snippet", "score"}


def contract_ok(res: dict) -> bool:
    return CONTRACT <= set(res) and all(SOURCE_KEYS <= set(s) for s in res["sources"])


def main() -> int:
    import pandas as pd

    from rag import guard, pipeline, scope
    from rag.config import load_config
    from rag.generate import fit_to_length
    from rag.retriever import get_retriever

    cfg = load_config()
    print(f"index dir : {cfg['paths']['index_dir']}")
    print(f"embedder  : {cfg['embedding']['model']}")
    print(f"llm       : {cfg['llm']['model']} @ {cfg['llm']['base_url']} (stubbed here)\n")

    # ---- retriever -------------------------------------------------------------
    r = get_retriever()
    check("index loads", r.meta["chunk_count"] > 0,
          f"{r.meta['chunk_count']} abstracts, partial={r.meta.get('partial')}")
    q = "What enzyme is deficient in Gaucher disease?"
    hits = r.search(q)
    check("search returns 5 hits", len(hits) == cfg["retrieval"]["final_top_k"], f"{len(hits)}")
    if hits:
        c = hits[0].chunk
        check("hits carry pmid/title/year", bool(c.get("pmid") and c.get("title") and c.get("year")),
              f"PMID {c.get('pmid')} {c.get('year')} {c.get('title', '')[:60]}")
        check("hits are reranked", hits[0].rerank_score is not None, f"top={hits[0].score:.2f}")
        check("top hit is about Gaucher", "gaucher" in c["text"].lower())
    exact = r.search("glucocerebrosidase")
    check("bm25/dense return results for an exact term", bool(exact))

    # ---- disease scope -------------------------------------------------------------
    orig_graph = cfg["paths"]["graph_dir"]
    with tempfile.TemporaryDirectory() as tmp:
        cfg["paths"]["graph_dir"] = tmp
        scope._edge_maps.cache_clear(); scope._labels.cache_clear()
        rows, info = r.scope_rows("MONDO:0000000")
        check("unknown disease, no graph files -> search all, no crash", rows is None, str(info))
        res = pipeline.ask(q, disease_id="MONDO:0000000", client=StubLLM("Enzyme is low [1]."))
        check("ask with disease_id and no graph still answers", contract_ok(res), res["status"])

        pd.DataFrame([{"id": "MONDO:0018150", "type": "Disease", "label": "Gaucher disease",
                       "group": "lysosomal", "attrs": "{}"}]).to_parquet(Path(tmp) / "nodes.parquet")
        scope._labels.cache_clear()
        rows, info = r.scope_rows("MONDO:0018150")
        check("keyword fallback from nodes.parquet label", rows is not None and len(rows) >= 3, str(info))
        if rows is not None:
            check("keyword-scoped papers mention the disease",
                  all("gaucher" in (r.chunks[i]["title"] + " ".join(r.chunks[i]["mesh"])
                                    + r.chunks[i]["text"]).lower() for i in rows[:50]))
            sh = r.search(q, rows=rows)
            check("scoped search returns only scoped papers",
                  {h.chunk["pmid"] for h in sh} <= {r.chunks[i]["pmid"] for i in rows})

        pmids = [c["pmid"] for c in r.chunks[:6]]
        edges = [{"id": f"e{i}", "src": f"PMID:{p}", "dst": "MONDO:0000002", "rel": "mentions"}
                 for i, p in enumerate(pmids)]
        edges.append({"id": "e99", "src": "MONDO:0000002", "dst": "MONDO:0000001", "rel": "subclass_of"})
        pd.DataFrame(edges).to_parquet(Path(tmp) / "edges.parquet")
        scope._edge_maps.cache_clear()
        rows, info = r.scope_rows("MONDO:0000001")
        check("graph mentions + descendants scope", rows is not None and len(rows) == 6, str(info))
    cfg["paths"]["graph_dir"] = orig_graph
    scope._edge_maps.cache_clear(); scope._labels.cache_clear()

    # ---- layer 1 -----------------------------------------------------------------------
    for good in ("What treatments have been tried for Krabbe disease?",
                 "Which biomarkers track disease progression in Fabry patients?",
                 "Is bone marrow transplant helpful for leukodystrophy?"):
        g = guard.topic_gate(good)
        check(f"topic gate admits: {good[:50]}", g.allowed, g.reason)
    for bad in ("best pizza in NYC", "Write a poem about the sea", "Who won the World Cup in 2022?"):
        g = guard.topic_gate(bad)
        check(f"topic gate refuses: {bad}", not g.allowed, f"{g.reason} {g.detail}")

    # ---- layer 2 -----------------------------------------------------------------------
    thr = cfg["guard"]["grounding"]["min_rerank_score"]
    check("grounding refuses low score", not guard.grounding_gate([FakeHit(thr - 5)]).allowed)
    check("grounding admits high score", guard.grounding_gate([FakeHit(thr + 5)]).allowed)
    check("grounding refuses empty", not guard.grounding_gate([]).allowed)

    # ---- layer 3 -----------------------------------------------------------------------
    v = guard.verify_answer("Krabbe disease is treated with transplant.", 3)
    check("citation check rejects uncited answer", not v.allowed, v.reason)
    v = guard.verify_answer("ANSWER_NOT_AVAILABLE", 3)
    check("citation check rejects sentinel", not v.allowed, v.reason)
    v = guard.verify_answer("Transplant was used [7].", 3)
    check("citation check rejects out-of-range", not v.allowed, v.reason)
    v = guard.verify_answer("Transplant was used early [1]. It slowed decline [2][3].", 3)
    check("citation check accepts fully cited", v.allowed, v.reason)
    v = guard.verify_answer("Transplant was used early [1]. It slowed decline [2]. Results varied "
                            "between children [3]. Everyone was cured completely.", 3)
    check("one uncited sentence of four is dropped", v.allowed and "cured" not in v.detail["text"],
          str(v.detail.get("uncited_dropped")))
    v = guard.verify_answer("Transplant was used [1]. Everyone was cured. Nobody had side effects.", 3)
    check("mostly uncited answer is rejected", not v.allowed, v.reason)
    v = guard.verify_answer("Transplant was used [1]. Long-term results are not known.", 3)
    check("uncited limitation sentence is kept", v.allowed and "not known" in v.detail["text"])
    class _H:
        def __init__(self, t):
            self.chunk = {"text": t, "pmid": "1"}
    src = [_H("Plasma ADAMTS-13, TNF-alpha, GDF-15 and VEGFA were increased in Fabry patients."),
           _H("Urinary lyso-Gb3 analogues correlated with severity.")]
    att, added = guard.attribute_uncited(
        "Markers exist [2]. ADAMTS-13, TNF-alpha, GDF-15 and VEGFA were increased in patients. "
        "Chocolate cures everyone who eats it daily.", src)
    check("uncited sentence attributed by lexical overlap", "VEGFA were increased in patients [1]." in att,
          str(added))
    check("unsupported sentence not attributed", "daily [" not in att)
    text = " ".join(f"Sentence number {i} is here [1]." for i in range(200))
    out, trimmed = fit_to_length(text, 300)
    check("answer trimmed to word budget at sentence boundary",
          trimmed and len(out.split()) <= 300 and out.endswith("[1]."), f"{len(out.split())} words")

    # ---- pipeline (contract) -------------------------------------------------------------
    stub = StubLLM("Gaucher disease is caused by low activity of the enzyme glucocerebrosidase [1]. "
                   "This enzyme normally breaks down a fatty substance [2].")
    res = pipeline.ask(q, client=stub)
    check("pipeline answers with stub LLM", res["status"] == "answered" and not res["refused"],
          f"{res['status']} {res['reason']}")
    check("answer matches contract", contract_ok(res), str(sorted(res)))
    check("sources carry snippets and pmids", all(s["snippet"] and s["pmid"] for s in res["sources"]))

    res = pipeline.ask("What is the best pizza in NYC?", client=StubLLM("x [1]."))
    check("off-topic refused before the LLM", res["refused"] and res["status"] == "out_of_scope",
          res["reason"])
    check("refusal matches contract", contract_ok(res))

    s = StubLLM("ANSWER_NOT_AVAILABLE")
    res = pipeline.ask(q, client=s)
    check("sentinel -> refused", res["refused"] and res["status"] == "not_available", res["reason"])

    res = pipeline.ask(q, client=DeadLLM(""))
    check("unreachable LLM -> error result with sources, no exception",
          res["refused"] and res["status"] == "error" and bool(res["sources"]), res["reason"])

    events = list(pipeline.ask_stream(q, client=stub))
    toks = [e for e in events if e["type"] == "token"]
    fin = events[-1]
    check("ask_stream yields tokens then one final", bool(toks) and fin["type"] == "final"
          and sum(e["type"] == "final" for e in events) == 1, f"{len(toks)} tokens")
    check("stream final matches contract", contract_ok(fin) and fin["status"] == "answered")

    # ---- missing index -------------------------------------------------------------------
    from rag import retriever as rmod

    orig_idx = cfg["paths"]["index_dir"]
    cfg["paths"]["index_dir"] = "/nonexistent/index"
    rmod.get_retriever.cache_clear()
    res = pipeline.ask(q, client=stub)
    check("missing index -> error result, no exception", res["refused"] and res["status"] == "error",
          res["reason"][:80])
    cfg["paths"]["index_dir"] = orig_idx
    rmod.get_retriever.cache_clear()

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
