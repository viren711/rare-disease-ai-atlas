"""The three independent layers that keep the literature answers honest.
Ported from healthathon rag/guard.py (oncology seeds/lexicon replaced by rare-disease ones).

  1. topic gate      -- before retrieval: is this a biomedical / rare-disease question at all?
                        (a junk filter only; whether the corpus can answer is layer 2's job)
  2. grounding gate  -- after retrieval: did the cross-encoder find a supporting abstract?
  3. citation check  -- after generation: does every sentence cite a supplied source [n]?

Layers 1 and 2 refuse without ever calling the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from rag.config import load_config

# Canonical in-scope questions. Layer 1 compares a query against these and the corpus centroid.
SEED_QUESTIONS = [
    "What treatments have been tried for Krabbe disease?",
    "Is hematopoietic stem cell transplantation used in X-linked adrenoleukodystrophy?",
    "Which biomarkers track Fabry disease progression?",
    "How effective is enzyme replacement therapy in Pompe disease?",
    "What are the first symptoms of metachromatic leukodystrophy in children?",
    "What causes Zellweger spectrum disorder?",
    "How is Gaucher disease type 1 diagnosed?",
    "What is the role of newborn screening for lysosomal storage disorders?",
    "Does gene therapy work for mucopolysaccharidosis?",
    "What is the life expectancy of children with Tay-Sachs disease?",
    "How does substrate reduction therapy work in Niemann-Pick disease type C?",
    "Which genes cause neuronal ceroid lipofuscinosis?",
    "What diet is recommended for Refsum disease?",
    "How is cystinosis treated with cysteamine?",
    "What are the neurological features of Sanfilippo syndrome?",
    "How do peroxisome biogenesis disorders affect very long chain fatty acids?",
    "What clinical trials exist for lysosomal acid lipase deficiency?",
    "Are there patient registries for Hunter syndrome?",
    "What is the mechanism of pharmacological chaperone therapy?",
    "How does lysosomal dysfunction relate to Parkinson disease?",
]

# Cheap, high-precision vocabulary shortcut. Prefix patterns (no trailing \b) so that
# "lysosom" covers lysosome / lysosomal, "mucopolysacchar" covers the whole MPS family, etc.
LEXICON = [
    "lysosom", "peroxisom", "storage disease", "storage disorder", "leukodystroph",
    "adrenoleukodystroph", "adrenomyeloneuropath", "gaucher", "fabry", "pompe", "krabbe",
    "niemann", "tay-sachs", "tay sachs", "sandhoff", "mucopolysacchar", "hurler", "hunter syndrome",
    "scheie", "sanfilippo", "morquio", "maroteaux", "sly syndrome", "zellweger", "refsum",
    "cystinosis", "batten", "ceroid lipofuscinos", "wolman", "acid lipase", "mucolipidos",
    "fucosidos", "mannosidos", "sialidos", "galactosialidos", "aspartylglucosaminur", "farber",
    "danon", "gm1", "gm2", "gangliosidos", "sphingolipid", "glycosaminoglycan", "plasmalogen",
    "very long chain fatty", "vlcfa", "abcd1", "pex1", "pex6", "rhizomelic", "chondrodysplasia punctata",
    "enzyme replacement", "substrate reduction", "pharmacological chaperone", "chaperone therap",
    "hematopoietic stem cell", "haematopoietic stem cell", "bone marrow transplant", "hsct",
    "gene therapy", "newborn screening", "rare disease", "orphan drug", "glucocerebrosidase",
    "alpha-galactosidase", "acid alpha-glucosidase", "galactocerebrosidase", "arylsulfatase",
    "hexosaminidase", "sphingomyelinase", "iduronidase", "lyso-gb3", "globotriaosylceramide",
    "alpha-mannosidosis", "multiple sulfatase", "primary hyperoxaluria", "acatalas", "dbp deficiency",
    "d-bifunctional", "acyl-coa oxidase",
]


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    message: str = ""
    detail: dict | None = None


# ---------------------------------------------------------------------------
# Layer 1 -- topic gate (pre-retrieval)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _lexicon_pattern() -> re.Pattern:
    esc = sorted((re.escape(t) for t in LEXICON), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(esc) + r")", re.IGNORECASE)


@lru_cache(maxsize=1)
def _seed_vectors():
    from rag.retriever import get_retriever

    return get_retriever().embed_query_batch(SEED_QUESTIONS)


def topic_scores(query: str, query_vec: np.ndarray | None = None) -> tuple[float, float]:
    """(seed_similarity, centroid_similarity) for calibration and the gate."""
    from rag.retriever import get_retriever

    retriever = get_retriever()
    if query_vec is None:
        query_vec = retriever.embed_query(query)
    return float(np.max(_seed_vectors() @ query_vec[0])), retriever.centroid_similarity(query_vec)


def topic_gate(query: str, query_vec: np.ndarray | None = None) -> GateResult:
    """Junk filter. Refuses only a query unlike a rare-disease question on BOTH signals."""
    cfg = load_config()
    gcfg = cfg["guard"]["topic"]
    msg = cfg["messages"]["out_of_scope"].strip()

    if not query or len(query.strip()) < 3:
        return GateResult(False, "empty query", msg)

    terms = sorted({m.group(0).lower() for m in _lexicon_pattern().finditer(query)})
    if terms and gcfg.get("lexicon_override", True):
        return GateResult(True, f"rare-disease term present: {', '.join(terms)}",
                          detail={"terms": terms})

    seed_sim, centroid_sim = topic_scores(query, query_vec)
    detail = {"seed_similarity": round(seed_sim, 4), "centroid_similarity": round(centroid_sim, 4)}
    if seed_sim < gcfg["junk_max_seed"] and centroid_sim < gcfg["junk_max_centroid"]:
        return GateResult(False, "not related to the indexed rare-disease literature", msg,
                          detail=detail)
    return GateResult(True, "plausibly a question about the corpus", detail=detail)


# ---------------------------------------------------------------------------
# Layer 2 -- grounding gate (post-retrieval)
# ---------------------------------------------------------------------------

def _threshold(hits: list) -> tuple[float, bool]:
    gcfg = load_config()["guard"]["grounding"]
    use_rerank = bool(hits) and hits[0].rerank_score is not None
    return (gcfg["min_rerank_score"] if use_rerank else gcfg["min_dense_similarity"]), use_rerank


def grounding_gate(hits: list) -> GateResult:
    cfg = load_config()
    gcfg = cfg["guard"]["grounding"]
    msg = cfg["messages"]["not_available"].strip()
    if not hits:
        return GateResult(False, "no abstracts retrieved", msg, detail={"supporting": 0})
    threshold, use_rerank = _threshold(hits)
    supporting = [h for h in hits if h.score >= threshold]
    detail = {
        "scored_by": "reranker" if use_rerank else "dense",
        "threshold": threshold,
        "top_score": round(hits[0].score, 4),
        "supporting": len(supporting),
    }
    if len(supporting) < gcfg["min_supporting_chunks"]:
        return GateResult(False, f"top score {hits[0].score:.3f} below threshold {threshold}",
                          msg, detail=detail)
    return GateResult(True, "sufficient supporting evidence", detail=detail)


def filter_supporting(hits: list) -> list:
    """Once the gate passed, keep every hit within support_margin of the best one (or above
    the threshold), so near-ties are not dropped and the model can corroborate."""
    gcfg = load_config()["guard"]["grounding"]
    if not hits:
        return []
    threshold, use_rerank = _threshold(hits)
    margin = gcfg.get("support_margin", 3.0 if use_rerank else 0.08)
    floor = min(threshold, hits[0].score - margin)
    return [h for h in hits if h.score >= floor] or hits[:1]


# ---------------------------------------------------------------------------
# Layer 3 -- citation verification (post-generation)
# ---------------------------------------------------------------------------

_CITE = re.compile(r"\[(\d{1,2})\]")
# Sentences that only state a limitation ("the abstracts do not report ...") may go uncited:
# the prompt asks the model to say what is unknown, and that statement has no source.
_LIMITATION = re.compile(
    r"\b(?:not (?:clear|known|reported|described|covered|addressed|mentioned|available|established)"
    r"|unknown|unclear|no (?:data|information|evidence|studies)|do(?:es)? not (?:say|report|describe|"
    r"address|mention|cover|provide|include|state)|(?:remains?|is) (?:uncertain|unclear|unknown)"
    r"|more research|further (?:research|studies)|talk (?:to|with) (?:a|your) (?:doctor|specialist)"
    r"|consult)",
    re.IGNORECASE,
)


def split_sentences(text: str) -> list[str]:
    from rag.generate import _split_units

    return [u for _, u in _split_units(text) if u.strip() and len(u.split()) >= 3]


def drop_uncited(answer: str) -> tuple[str, list[str], int]:
    """Remove factual sentences that carry no [n] marker.

    Returns (clean_text, dropped_sentences, n_sentences). Limitation statements and
    headings/lead-ins ending with ':' are kept without a citation.
    """
    from rag.generate import _split_units

    kept, dropped, n = [], [], 0
    for sep, unit in _split_units(answer):
        u = unit.strip()
        if not u or len(u.split()) < 3 or u.endswith(":"):
            kept.append((sep, unit))
            continue
        n += 1
        if _CITE.search(u) or _LIMITATION.search(u):
            kept.append((sep, unit))
        else:
            dropped.append(u)
    text = "".join(sep + unit for sep, unit in kept).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, dropped, n


_STOP = set("""a an the of in on for to and or is are was were be been with by from at as that this these
those it its into about can could may might also has have had not no than then so such which who what
when where how there their they them he she his her patients patient disease diseases study studies
shown showed found other some more most many used use using including include includes like well
been being both each after before during while however therefore additionally overall""".split())


def _content_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-\.α-ω]*[a-z0-9α-ω]|[a-z0-9]", text.lower())
            if t not in _STOP and len(t) > 2 or t.isdigit()}


def attribute_uncited(answer: str, hits: list, min_overlap: float = 0.6) -> tuple[str, list[dict]]:
    """Give an uncited sentence the [n] of the supplied source it was evidently drawn from.

    Small models often cite the first sentence of a paragraph and not the ones that follow.
    A sentence is attributed only when >= min_overlap of its content words (and at least 4 of
    them) occur in one supplied abstract; otherwise it stays uncited and layer 3 drops it.
    This is a lexical-support check, stricter than inheriting the neighbour's marker.
    """
    from rag.generate import _split_units

    src_tokens = [_content_tokens(h.chunk.get("text", "")) for h in hits]
    out, added = [], []
    for sep, unit in _split_units(answer):
        u = unit.strip()
        if u and len(u.split()) >= 3 and not u.endswith(":") and not _CITE.search(u) \
                and not _LIMITATION.search(u):
            toks = _content_tokens(u)
            if len(toks) >= 4 and src_tokens:
                scores = [len(toks & s) / len(toks) for s in src_tokens]
                best = max(range(len(scores)), key=scores.__getitem__)
                if scores[best] >= min_overlap:
                    m = re.match(r"^(.*?)([.!?][\"')\]]*)?$", unit.rstrip(), re.S)
                    body, stop = m.group(1), m.group(2) or ""
                    unit = f"{body} [{best + 1}]{stop}"
                    added.append({"sentence": u[:120], "source": best + 1,
                                  "overlap": round(scores[best], 2)})
        out.append((sep, unit))
    return "".join(sep + unit for sep, unit in out).strip(), added


def verify_answer(answer: str, n_sources: int) -> GateResult:
    """Reject an answer not demonstrably tied to the supplied sources.

    KNOWN LIMIT (as in healthathon): this checks that markers EXIST, point at a supplied
    source and cover every factual sentence. It does not check that the cited abstract
    actually supports the sentence -- the user verifies that by opening the PMID.
    """
    cfg = load_config()
    ccfg = cfg["guard"]["citation"]
    msg = cfg["messages"]["not_available"].strip()

    if not answer or not answer.strip():
        return GateResult(False, "model returned nothing", msg)
    if ccfg["sentinel"] in answer:
        return GateResult(False, "model emitted the not-available sentinel", msg)

    cited = {int(m) for m in _CITE.findall(answer)}
    if ccfg["require_at_least_one_citation"] and not cited:
        return GateResult(False, "answer cited no source, so it cannot be verified", msg,
                          detail={"cited": []})
    out_of_range = sorted(c for c in cited if not 1 <= c <= n_sources)
    if out_of_range and ccfg["reject_out_of_range_citations"]:
        return GateResult(False, f"answer cited sources that were never supplied: {out_of_range}",
                          msg, detail={"cited": sorted(cited), "out_of_range": out_of_range})

    clean, dropped, n = drop_uncited(answer)
    frac = len(dropped) / n if n else 0.0
    detail = {"cited": sorted(cited), "uncited_dropped": dropped, "sentences": n,
              "text": clean}
    if frac > ccfg.get("max_uncited_fraction", 0.34):
        return GateResult(False, f"{len(dropped)} of {n} sentences cite no source", msg,
                          detail=detail)
    if not _CITE.search(clean):
        return GateResult(False, "no cited sentence left after removing uncited ones", msg,
                          detail=detail)
    return GateResult(True, "every factual sentence cites a supplied source", detail=detail)
