"""Configuration for the literature RAG, adapted from healthathon rag/config.py.

The only file a user edits is config/atlas.yaml (sections `paths`, `retrieval`, `llm`).
Everything else -- guard thresholds, prompts' messages, reranker settings -- has a default
here, and any key may be overridden by adding a `rag:` section to atlas.yaml with the same
nested shape as DEFAULTS below. Environment overrides: LLM_BASE_URL, LLM_MODEL, LLM_API_KEY,
LLM_TEMPERATURE, INDEX_DIR, EMBEDDING_MODEL, RERANKER_ENABLED (read from .env too).
"""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "paths": {"index_dir": "artifacts/index", "graph_dir": "data/graph"},
    "embedding": {
        "model": "BAAI/bge-small-en-v1.5",
        # bge expects this prefix on the QUERY side only
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "normalize": True,
        "max_seq_length": 320,
    },
    "reranker": {
        "enabled": True,
        "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "max_length": 512,
        "batch_size": 32,
    },
    "retrieval": {
        "dense_top_k": 50,
        "bm25_top_k": 50,
        "rrf_k": 60,
        "rerank_candidates": 15,
        "final_top_k": 5,
        # a disease filter keeps only papers linked to the disease; if fewer than this many
        # papers are linked the filter is dropped and the whole corpus is searched instead
        "min_scoped_papers": 3,
    },
    "guard": {
        "topic": {
            # Layer 1 is a junk filter only: refuse when the query is unlike a biomedical
            # question on BOTH signals. Cosine thresholds are embedder-specific (bge-small);
            # calibrated by scripts/eval_ask.py.
            "junk_max_seed": 0.53,
            "junk_max_centroid": 0.55,
            "lexicon_override": True,
        },
        "grounding": {
            # Cross-encoder logit floor; overridden by retrieval.min_rerank_score in atlas.yaml.
            "min_rerank_score": 0.3,
            "min_supporting_chunks": 1,
            "support_margin": 3.0,
            "min_dense_similarity": 0.55,
        },
        "citation": {
            "sentinel": "ANSWER_NOT_AVAILABLE",
            "require_at_least_one_citation": True,
            "reject_out_of_range_citations": True,
            # every sentence must cite [n]; uncited factual sentences are dropped from the
            # shown answer, and if more than this share had to be dropped the answer is refused
            "max_uncited_fraction": 0.5,
        },
    },
    "messages": {
        "not_available": (
            "The indexed research abstracts do not contain a supported answer to this question."
        ),
        "out_of_scope": (
            "I can only answer questions about lysosomal and peroxisomal rare diseases, using "
            "the PubMed research abstracts indexed in this atlas."
        ),
    },
    "llm": {
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "qwen2.5-3b",
        "temperature": 0.1,
        "max_answer_words": 300,
        # ~1.5 tokens/word incl. citation markers, so the model can finish naturally
        "max_tokens": 520,
        "timeout_seconds": 300,
    },
}


def _deep_update(base: dict, upd: dict) -> dict:
    for k, v in (upd or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    _load_dotenv()
    cfg = copy.deepcopy(DEFAULTS)
    atlas_path = ROOT / "config" / "atlas.yaml"
    atlas = yaml.safe_load(atlas_path.read_text(encoding="utf-8")) if atlas_path.exists() else {}
    atlas = atlas or {}

    paths = atlas.get("paths") or {}
    if paths.get("index"):
        cfg["paths"]["index_dir"] = paths["index"]
    if paths.get("graph"):
        cfg["paths"]["graph_dir"] = paths["graph"]

    r = atlas.get("retrieval") or {}
    if r.get("embedding_model"):
        cfg["embedding"]["model"] = r["embedding_model"]
    if r.get("max_seq_length"):
        cfg["embedding"]["max_seq_length"] = int(r["max_seq_length"])
    if r.get("reranker_model"):
        cfg["reranker"]["model"] = r["reranker_model"]
    for src, dst in (("dense_k", "dense_top_k"), ("bm25_k", "bm25_top_k"),
                     ("rerank_candidates", "rerank_candidates"), ("top_k", "final_top_k"),
                     ("rrf_k", "rrf_k")):
        if r.get(src) is not None:
            cfg["retrieval"][dst] = int(r[src])
    if r.get("min_rerank_score") is not None:
        cfg["guard"]["grounding"]["min_rerank_score"] = float(r["min_rerank_score"])

    _deep_update(cfg["llm"], atlas.get("llm") or {})
    _deep_update(cfg, atlas.get("rag") or {})

    if os.environ.get("EMBEDDING_MODEL"):
        cfg["embedding"]["model"] = os.environ["EMBEDDING_MODEL"]
    if os.environ.get("INDEX_DIR"):
        cfg["paths"]["index_dir"] = os.environ["INDEX_DIR"]
    if os.environ.get("RERANKER_ENABLED"):
        cfg["reranker"]["enabled"] = os.environ["RERANKER_ENABLED"].lower() in ("1", "true", "yes")

    llm = cfg["llm"]
    # `or`, not get(default): an exported-but-empty variable falls back to the config
    llm["base_url"] = os.environ.get("LLM_BASE_URL") or llm["base_url"]
    llm["model"] = os.environ.get("LLM_MODEL") or llm["model"]
    llm["api_key"] = os.environ.get("LLM_API_KEY", "not-needed")
    if os.environ.get("LLM_TEMPERATURE"):
        llm["temperature"] = float(os.environ["LLM_TEMPERATURE"])
    return cfg


def path_of(key: str) -> Path:
    p = Path(load_config()["paths"][key])
    return p if p.is_absolute() else ROOT / p
