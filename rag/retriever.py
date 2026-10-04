"""Hybrid retrieval over PubMed abstracts: dense FAISS + BM25, fused with RRF, then
cross-encoder reranked. Ported from healthathon rag/retriever.py; the healthathon
pathology/organ-system scopes are replaced by a disease scope (MONDO id -> papers).

Loading is lazy and cached, so the Streamlit app pays the model load cost once.
"""

from __future__ import annotations

import json
import pickle
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from rag.config import load_config, path_of


def tokenize_for_bm25(text: str) -> list[str]:
    """Must match scripts/50_build_index.py exactly or BM25 scores are garbage."""
    return re.findall(r"[a-z0-9]+", text.lower())


@dataclass
class Hit:
    chunk: dict[str, Any]
    dense_score: float = 0.0
    bm25_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float | None = None

    @property
    def score(self) -> float:
        """The score the grounding gate judges: reranker if present, else dense."""
        return self.rerank_score if self.rerank_score is not None else self.dense_score

    @property
    def text(self) -> str:
        return self.chunk["text"]

    @property
    def citation(self) -> str:
        c = self.chunk
        return f"PMID:{c.get('pmid')} ({c.get('year') or 'n.d.'})"


class IndexMismatchError(RuntimeError):
    """The index on disk was built with a different embedding model."""


@dataclass
class Retriever:
    chunks: list[dict]
    meta: dict
    _faiss: Any = field(repr=False, default=None)
    _bm25: Any = field(repr=False, default=None)
    _embedder: Any = field(repr=False, default=None)
    _reranker: Any = field(repr=False, default=None)
    _centroid: Any = field(repr=False, default=None)
    _vectors: Any = field(repr=False, default=None)
    _row_by_pmid: dict = field(repr=False, default_factory=dict)

    @classmethod
    def load(cls, device: str = "cpu") -> "Retriever":
        import faiss
        from sentence_transformers import SentenceTransformer

        cfg = load_config()
        index_dir = path_of("index_dir")
        meta_path = index_dir / "index_meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(
                f"no literature index at {index_dir}. Build it with: "
                ".venv/bin/python scripts/50_build_index.py"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        configured = cfg["embedding"]["model"]
        if meta["embedding_model"] != configured:
            raise IndexMismatchError(
                f"index was built with '{meta['embedding_model']}' but config says "
                f"'{configured}'. Rebuild the index or restore the model name."
            )
        chunks = [
            json.loads(line)
            for line in (index_dir / "chunks.jsonl").read_text(encoding="utf-8").split("\n")
            if line.strip()
        ]
        obj = cls(chunks=chunks, meta=meta)
        obj._faiss = faiss.read_index(str(index_dir / "faiss.index"))
        obj._bm25 = pickle.loads((index_dir / "bm25.pkl").read_bytes())
        cpath = index_dir / "centroid.npy"
        obj._centroid = np.load(cpath) if cpath.exists() else None
        epath = index_dir / "embeddings.npy"
        obj._vectors = np.load(epath, mmap_mode="r") if epath.exists() else None
        obj._row_by_pmid = {str(c["pmid"]): i for i, c in enumerate(chunks)}

        obj._embedder = SentenceTransformer(meta["embedding_model"], device=device)
        obj._embedder.max_seq_length = meta.get("max_seq_length", 320)
        if cfg["reranker"]["enabled"]:
            from sentence_transformers import CrossEncoder

            obj._reranker = CrossEncoder(cfg["reranker"]["model"], device=device)
            obj._reranker.max_length = cfg["reranker"].get("max_length", 512)
        return obj

    # -- embedding ----------------------------------------------------------

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed_query_batch([query])

    def embed_query_batch(self, queries: list[str]) -> np.ndarray:
        prefix = self.meta.get("query_prefix", "")
        return self._embedder.encode(
            [prefix + q for q in queries],
            convert_to_numpy=True,
            normalize_embeddings=self.meta.get("normalize", True),
        ).astype("float32")

    def centroid_similarity(self, query_vec: np.ndarray) -> float:
        if self._centroid is None:
            return 1.0
        return float(np.dot(query_vec[0], self._centroid))

    # -- disease scope ------------------------------------------------------

    def scope_rows(self, disease_id: str | None) -> tuple[Any, dict]:
        """Row indices of papers linked to `disease_id`, or (None, info) for all.

        Never raises: a missing graph or an unknown id just means "search everything".
        """
        if not disease_id:
            return None, {"scope": "all"}
        from rag import scope as scope_mod

        try:
            pmids, info = scope_mod.papers_for_disease(disease_id, self)
        except Exception as exc:  # noqa: BLE001 - scoping must never break answering
            return None, {"scope": "all", "disease_id": disease_id, "error": str(exc)}
        rows = sorted({self._row_by_pmid[p] for p in pmids if p in self._row_by_pmid})
        info = {**info, "disease_id": disease_id, "papers": len(rows)}
        if len(rows) < load_config()["retrieval"].get("min_scoped_papers", 3):
            info["scope"] = "all"
            info["note"] = "too few papers linked to this disease; searched the whole corpus"
            return None, info
        info["scope"] = "disease"
        return np.array(rows, dtype="int64"), info

    # -- search -------------------------------------------------------------

    def _dense_within(self, query_vec: np.ndarray, rows: Any, top_k: int):
        if self._vectors is None:
            raise RuntimeError("embeddings.npy missing from the index; rebuild it")
        subset = np.asarray(self._vectors[rows])
        scores = subset @ query_vec[0]
        k = min(top_k, len(rows))
        if k <= 0:
            return np.array([], dtype="int64"), np.array([])
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return rows[top], scores[top]

    def _bm25_within(self, query: str, rows: Any, top_k: int):
        all_scores = self._bm25.get_scores(tokenize_for_bm25(query))
        if rows is None:
            ids = np.argsort(all_scores)[::-1][:top_k]
            return ids, all_scores[ids]
        sub = all_scores[rows]
        k = min(top_k, len(rows))
        if k <= 0:
            return np.array([], dtype="int64"), np.array([])
        top = np.argpartition(-sub, k - 1)[:k]
        top = top[np.argsort(-sub[top])]
        return rows[top], sub[top]

    def _fuse_and_rerank(self, query, dense_ids, dense_vals, bm25_ids, bm25_vals,
                         rerank_candidates: int, final_k: int | None) -> list[Hit]:
        cfg = load_config()
        dense_rank = {int(i): r for r, i in enumerate(dense_ids)}
        dense_score = {int(i): float(v) for i, v in zip(dense_ids, dense_vals)}
        # BM25 returns zero-score rows when the query shares no term; they carry no signal
        bm25_rank = {int(i): r for r, (i, v) in enumerate(zip(bm25_ids, bm25_vals)) if v > 0}
        bm25_score = {int(i): float(v) for i, v in zip(bm25_ids, bm25_vals)}

        k = cfg["retrieval"]["rrf_k"]
        fused: dict[int, float] = {}
        for ranks in (dense_rank, bm25_rank):
            for idx, rank in ranks.items():
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank + 1)

        ordered = sorted(fused.items(), key=lambda kv: -kv[1])[:rerank_candidates]
        hits = [
            Hit(chunk=self.chunks[idx], dense_score=dense_score.get(idx, 0.0),
                bm25_score=bm25_score.get(idx, 0.0), fused_score=score)
            for idx, score in ordered
        ]
        if self._reranker is not None and hits:
            pairs = [(query, h.chunk["text"]) for h in hits]
            scores = self._reranker.predict(pairs, batch_size=cfg["reranker"]["batch_size"])
            for hit, sc in zip(hits, scores):
                hit.rerank_score = float(sc)
            hits.sort(key=lambda h: -h.rerank_score)
        return hits[:final_k] if final_k else hits

    def search(self, query: str, query_vec: np.ndarray | None = None,
               disease_id: str | None = None, rows: Any = None,
               final_k: int | None = -1) -> list[Hit]:
        """Hybrid retrieval, optionally restricted to papers linked to one disease.

        Scoping happens BEFORE the top-k cut (filtering a corpus-wide top 50 afterwards
        would usually leave nothing for a rare disease). Pass `rows` to reuse a scope
        already computed by `scope_rows`.
        """
        cfg = load_config()["retrieval"]
        if rows is None and disease_id:
            rows, _ = self.scope_rows(disease_id)
        if query_vec is None:
            query_vec = self.embed_query(query)

        if rows is None:
            d_scores, d_ids = self._faiss.search(query_vec, cfg["dense_top_k"])
            dense_ids = [int(i) for i in d_ids[0] if i >= 0]
            dense_vals = [float(v) for i, v in zip(d_ids[0], d_scores[0]) if i >= 0]
        else:
            dense_ids, dense_vals = self._dense_within(query_vec, rows, cfg["dense_top_k"])
        bm25_ids, bm25_vals = self._bm25_within(query, rows, cfg["bm25_top_k"])
        k = cfg["final_top_k"] if final_k == -1 else final_k
        return self._fuse_and_rerank(query, dense_ids, dense_vals, bm25_ids, bm25_vals,
                                     cfg["rerank_candidates"], k)


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    return Retriever.load()


def index_ready() -> bool:
    return (path_of("index_dir") / "index_meta.json").exists()
