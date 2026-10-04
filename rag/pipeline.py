"""End-to-end literature Q&A: topic gate -> retrieve -> grounding gate -> generate -> verify.

Ported from healthathon rag/pipeline.py. The two public functions implement the contract in
atlas/api.py:

  ask(question, disease_id=None) -> {"answer", "refused", "reason", "sources": [...], ...extra}
  ask_stream(question, disease_id=None) -> yields {"type":"token","text"} ... then
                                           {"type":"final", **ask() result}

sources: [{"n", "pmid", "title", "year", "journal", "snippet", "score", ...extra}]
Every path returns that shape; nothing raises for a refusal, a missing index or a dead LLM.
Extra keys: status (answered|not_available|out_of_scope|error), cited, elapsed_ms, scope, trace.
Streamed tokens are UNVERIFIED drafts: the UI must replace them with final["answer"].
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterator

from rag import guard
from rag.config import load_config

_STOP = set("""a an the of in on for to and or is are was were be been with by from at as that this
these those what which who whom whose how why when where does do did can could should would may might
there their it its into about any all have has had not no than then so such vs versus""".split())


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-]+", text.lower()) if t not in _STOP}


def make_snippet(chunk: dict, question: str, max_chars: int = 320) -> str:
    """The abstract sentence(s) sharing most words with the question."""
    title = (chunk.get("title") or "").rstrip(".")
    body = chunk.get("text") or ""
    if title and body.startswith(title):
        body = body[len(title):].lstrip(". ")
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", body) if s.strip()]
    if not sents:
        return title[:max_chars]
    q = _tokens(question)
    best = max(range(len(sents)), key=lambda i: (len(q & _tokens(sents[i])), -i))
    snip = sents[best]
    if len(snip) < 160 and best + 1 < len(sents):
        snip += " " + sents[best + 1]
    if len(snip) > max_chars:
        snip = snip[: max_chars].rsplit(" ", 1)[0] + " ..."
    return snip


def _sources(hits: list, question: str, cited: list[int] | None = None) -> list[dict]:
    out = []
    for i, h in enumerate(hits, start=1):
        c = h.chunk
        out.append({
            "n": i,
            "pmid": str(c.get("pmid")),
            "title": c.get("title", ""),
            "year": c.get("year"),
            "journal": c.get("journal"),
            "snippet": make_snippet(c, question),
            "score": round(float(h.score), 3),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{c.get('pmid')}/",
            "groups": c.get("groups", []),
            "cited": bool(cited) and i in cited,
        })
    return out


def _fit_answer(raw: str, trace: dict) -> str:
    from rag.config import load_config
    from rag.generate import fit_to_length

    budget = load_config()["llm"].get("max_answer_words", 0)
    before = len(raw.split())
    fitted, trimmed = fit_to_length(raw, budget)
    trace["length"] = {"words": len(fitted.split()), "words_generated": before,
                       "budget": budget, "trimmed": trimmed}
    return fitted


def _scoped_message(message: str, scope: dict) -> str:
    if scope.get("scope") != "disease":
        return message
    return (f"{message.rstrip()} The search was limited to the {scope.get('papers')} papers linked "
            "to the selected disease; the answer may exist in papers about related diseases.")


def _run(question: str, disease_id: str | None, client, stream: bool) -> Iterator[dict]:
    started = time.time()
    trace: dict[str, Any] = {}
    scope: dict = {"scope": "all"}

    def final(answer: str, status: str, reason: str | None, sources=None, cited=None) -> dict:
        return {
            "type": "final",
            "answer": answer,
            "refused": status != "answered",
            "reason": reason,
            "sources": sources or [],
            "status": status,
            "cited": cited or [],
            "elapsed_ms": int((time.time() - started) * 1000),
            "scope": scope,
            "trace": trace,
        }

    question = (question or "").strip()
    try:
        from rag.retriever import get_retriever

        retriever = get_retriever()
    except Exception as exc:  # noqa: BLE001 - missing index must not crash the UI
        trace["error"] = str(exc)
        yield final("The literature index is not available yet. Build it with "
                    "scripts/50_build_index.py.", "error", f"index unavailable: {exc}")
        return

    query_vec = retriever.embed_query(question or " ")

    # --- Layer 1 ---------------------------------------------------------------
    topic = guard.topic_gate(question, query_vec)
    trace["topic_gate"] = {"allowed": topic.allowed, "reason": topic.reason, **(topic.detail or {})}
    if not topic.allowed:
        yield final(topic.message, "out_of_scope", topic.reason)
        return

    # --- Retrieval (optionally scoped to one disease) ----------------------------
    rows, scope = retriever.scope_rows(disease_id)
    # A question asked from a disease page often omits the disease ("what treatments were
    # tried?"); the cross-encoder then scores every abstract as off-topic. Name it explicitly.
    search_q, search_vec = question, query_vec
    label = scope.get("label")
    if disease_id and label and not set(_tokens(label)) <= set(_tokens(question)):
        search_q = f"{question} ({label})"
        search_vec = retriever.embed_query(search_q)
        trace["search_query"] = search_q
    hits = retriever.search(search_q, search_vec, rows=rows)
    trace["retrieved"] = [{"pmid": h.chunk.get("pmid"), "score": round(h.score, 3),
                           "fused": round(h.fused_score, 4)} for h in hits]

    # --- Layer 2 ---------------------------------------------------------------
    grounding = guard.grounding_gate(hits)
    trace["grounding_gate"] = {"allowed": grounding.allowed, "reason": grounding.reason,
                               **(grounding.detail or {})}
    if not grounding.allowed:
        # Show the closest abstracts anyway, clearly as "not enough to answer".
        yield final(_scoped_message(grounding.message, scope), "not_available", grounding.reason,
                    _sources(hits, question))
        return

    supporting = guard.filter_supporting(hits)
    sources = _sources(supporting, question)

    # --- Generation --------------------------------------------------------------
    from rag.generate import build_prompt
    from rag.llm import LLMClient, LLMError

    client = client or LLMClient.from_config()
    system, user = build_prompt(search_q, supporting)
    t_gen = time.time()
    try:
        if stream:
            parts: list[str] = []
            for piece in client.chat_stream(system, user):
                parts.append(piece)
                yield {"type": "token", "text": piece}
            raw = "".join(parts).strip()
        else:
            raw = client.chat(system, user)
    except LLMError as exc:
        trace["llm_error"] = str(exc)
        yield final(f"The local language model is not reachable ({exc}). The most relevant "
                    "abstracts are listed below.", "error", f"llm unavailable: {exc}", sources)
        return
    trace["generation_ms"] = int((time.time() - t_gen) * 1000)
    trace["finish_reason"] = getattr(client, "last_finish_reason", None)
    trace["raw_answer"] = raw

    from rag.generate import normalize_citations

    raw = _fit_answer(normalize_citations(raw, supporting), trace)
    # Attribute uncited sentences by lexical overlap -- only when the model itself cited
    # something and did not abstain, so an uncited answer is never rescued wholesale.
    sentinel = load_config()["guard"]["citation"]["sentinel"]
    if sentinel not in raw and re.search(r"\[\d{1,2}\]", raw):
        raw, added = guard.attribute_uncited(raw, supporting)
        if added:
            trace["auto_cited"] = added

    # --- Layer 3 ---------------------------------------------------------------
    verdict = guard.verify_answer(raw, len(supporting))
    detail = dict(verdict.detail or {})
    clean = detail.pop("text", raw)
    trace["citation_check"] = {"allowed": verdict.allowed, "reason": verdict.reason, **detail}
    if not verdict.allowed:
        yield final(_scoped_message(verdict.message, scope), "not_available", verdict.reason,
                    sources)
        return

    cited = [int(c) for c in re.findall(r"\[(\d{1,2})\]", clean)]
    cited = sorted(set(cited))
    yield final(clean, "answered", None, _sources(supporting, question, cited), cited)


def ask(question: str, disease_id: str | None = None, client=None) -> dict:
    result: dict = {}
    for event in _run(question, disease_id, client, stream=False):
        result = event
    result = dict(result)
    result.pop("type", None)
    return result


def ask_stream(question: str, disease_id: str | None = None, client=None) -> Iterator[dict]:
    yield from _run(question, disease_id, client, stream=True)


def status() -> dict:
    """{"index_ready", "llm_ready", "llm_detail", "chunks"} for the UI status panel."""
    from rag.llm import LLMClient
    from rag.retriever import index_ready

    ok, detail = LLMClient.from_config().health()
    out = {"index_ready": index_ready(), "llm_ready": ok, "llm_detail": detail}
    if out["index_ready"]:
        import json

        from rag.config import path_of

        meta = json.loads((path_of("index_dir") / "index_meta.json").read_text())
        out["chunks"] = meta.get("chunk_count")
        out["partial"] = meta.get("partial")
    return out
