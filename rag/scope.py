"""Map a disease id (MONDO) to the PubMed papers the literature search should be limited to.

Order of preference, each step tolerant of missing files:
  1. data/graph/edges.parquet  rel == 'mentions', Paper(PMID:x) -> Disease, for the disease and
     its descendants (subclass_of edges, child -> parent), so "mucopolysaccharidosis" covers MPS I-VII.
  2. keyword match of the disease label + synonyms (nodes.parquet / synonyms.parquet) against
     paper titles and MeSH terms, then full text if titles/MeSH give too few.
  3. nothing found -> empty set (the retriever then searches the whole corpus).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from rag.config import load_config, path_of

MAX_DESCENDANT_DEPTH = 4


def _graph_file(name: str) -> Path | None:
    p = path_of("graph_dir") / name
    return p if p.exists() else None


def _mtime(p: Path | None) -> float:
    return p.stat().st_mtime if p else 0.0


@lru_cache(maxsize=2)
def _edge_maps(_mtime_key: float):
    """(disease -> set(pmid), parent -> set(children)) from edges.parquet."""
    import pandas as pd

    p = _graph_file("edges.parquet")
    if p is None:
        return {}, {}
    df = pd.read_parquet(p, columns=["src", "dst", "rel"])
    mentions = df[(df["rel"] == "mentions") & df["src"].astype(str).str.startswith("PMID:")]
    mentions = mentions[mentions["dst"].astype(str).str.startswith("MONDO:")]
    by_disease: dict[str, set[str]] = {}
    for src, dst in zip(mentions["src"].astype(str), mentions["dst"].astype(str)):
        by_disease.setdefault(dst, set()).add(src.split(":", 1)[1])
    sub = df[df["rel"] == "subclass_of"]
    children: dict[str, set[str]] = {}
    for src, dst in zip(sub["src"].astype(str), sub["dst"].astype(str)):
        children.setdefault(dst, set()).add(src)
    return by_disease, children


@lru_cache(maxsize=2)
def _labels(_mtime_key: float) -> dict[str, list[str]]:
    """node_id -> [label, synonyms...] for Disease nodes, from nodes/synonyms parquet."""
    import pandas as pd

    out: dict[str, list[str]] = {}
    nodes = _graph_file("nodes.parquet")
    if nodes is not None:
        df = pd.read_parquet(nodes, columns=["id", "type", "label"])
        df = df[df["type"] == "Disease"]
        for i, lab in zip(df["id"].astype(str), df["label"].astype(str)):
            out.setdefault(i, []).append(lab)
    syn = _graph_file("synonyms.parquet")
    if syn is not None:
        df = pd.read_parquet(syn)
        if {"text", "node_id"} <= set(df.columns):
            for t, i in zip(df["text"].astype(str), df["node_id"].astype(str)):
                if i.startswith("MONDO:"):
                    out.setdefault(i, []).append(t)
    return out


def descendants(disease_id: str, children: dict[str, set[str]]) -> set[str]:
    seen = {disease_id}
    frontier = {disease_id}
    for _ in range(MAX_DESCENDANT_DEPTH):
        nxt = set()
        for d in frontier:
            nxt |= children.get(d, set()) - seen
        if not nxt:
            break
        seen |= nxt
        frontier = nxt
    return seen


def _usable_terms(names: list[str]) -> list[str]:
    """Drop abbreviations / very short synonyms that would match unrelated papers."""
    terms = []
    for n in names:
        n = re.sub(r"\s+", " ", n.strip().lower())
        n = re.sub(r"\s*\((?:disease|disorder)\)$", "", n)
        if len(n) >= 6 and n not in terms:
            terms.append(n)
    return terms


def keyword_pmids(terms: list[str], chunks: list[dict], min_hits: int) -> tuple[set[str], str]:
    if not terms:
        return set(), "none"
    pat = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
                     + r")", re.IGNORECASE)
    hits = {c["pmid"] for c in chunks
            if pat.search(c.get("title") or "") or any(pat.search(m) for m in c.get("mesh") or [])}
    if len(hits) >= min_hits:
        return hits, "title/mesh"
    hits |= {c["pmid"] for c in chunks if pat.search(c.get("text") or "")}
    return hits, "title/mesh/text"


def papers_for_disease(disease_id: str, retriever) -> tuple[set[str], dict]:
    min_hits = load_config()["retrieval"].get("min_scoped_papers", 3)
    edges_p = _graph_file("edges.parquet")
    pmids: set[str] = set()
    info: dict = {"method": None}
    if edges_p is not None:
        by_disease, children = _edge_maps(_mtime(edges_p))
        ids = descendants(disease_id, children)
        for d in ids:
            pmids |= by_disease.get(d, set())
        info = {"method": "graph mentions", "diseases_included": len(ids)}
    if len(pmids) < min_hits:
        key = _mtime(_graph_file("nodes.parquet")) + _mtime(_graph_file("synonyms.parquet"))
        names = _labels(key).get(disease_id, [])
        terms = _usable_terms(names)
        kw, where = keyword_pmids(terms, retriever.chunks, min_hits)
        if kw:
            pmids |= kw
            info = {"method": f"keyword ({where})", "terms": terms[:8]}
        elif info["method"] is None:
            info = {"method": "none", "terms": terms[:8]}
    if "label" not in info:
        key = _mtime(_graph_file("nodes.parquet")) + _mtime(_graph_file("synonyms.parquet"))
        names = _labels(key).get(disease_id, [])
        if names:
            info["label"] = names[0]
    return pmids, info


def disease_label(disease_id: str) -> str | None:
    key = _mtime(_graph_file("nodes.parquet")) + _mtime(_graph_file("synonyms.parquet"))
    names = _labels(key).get(disease_id, [])
    return names[0] if names else None
