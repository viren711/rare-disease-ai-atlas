#!/usr/bin/env python3
"""Build the literature retrieval index over the PubMed abstracts.

Adapted from healthathon scripts/03_build_index.py. One chunk = one abstract
("title. abstract", truncated by the embedder to retrieval.max_seq_length tokens).

  * dense: BAAI/bge-small-en-v1.5 embeddings -> exact FAISS IndexFlatIP
  * sparse: BM25Okapi, fitted concurrently in a worker process
  * resumable: embeddings are written in shards to <index>/shards/; a restart
    skips shards already on disk (as long as the corpus fingerprint matches)

Outputs (artifacts/index/): faiss.index, embeddings.npy, bm25.pkl, chunks.jsonl,
centroid.npy, index_meta.json.

    .venv/bin/python scripts/50_build_index.py
    .venv/bin/python scripts/50_build_index.py --limit 500 --out-dir /tmp/idx   # quick pipeline check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SHARD_SIZE = 2048


def physical_cores() -> int:
    """Physical core count (torch scales with physical cores; SMT oversubscribes BLAS)."""
    try:
        ids = set()
        current: dict[str, str] = {}
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if not line.strip():
                if "physical id" in current and "core id" in current:
                    ids.add((current["physical id"], current["core id"]))
                current = {}
                continue
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
        if ids:
            return len(ids)
    except OSError:
        pass
    return max(1, (os.cpu_count() or 2) // 2)


def tokenize_for_bm25(text: str) -> list[str]:
    """Lowercase alphanumeric tokens (keeps gene/enzyme codes like 'abcd1', 'gm2')."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_bm25(texts: list[str]) -> bytes:
    from rank_bm25 import BM25Okapi

    return pickle.dumps(BM25Okapi([tokenize_for_bm25(t) for t in texts]))


def corpus_fingerprint(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(r["chunk_id"].encode())
        h.update(str(len(r["text"])).encode())
    return h.hexdigest()[:16]


def load_rows(raw_dir: Path, groups: list[str]) -> list[dict]:
    """Read abstracts.jsonl per group, dedupe by PMID (a paper can sit in both groups)."""
    by_pmid: dict[str, dict] = {}
    for group in groups:
        path = raw_dir / "pubmed" / group / "abstracts.jsonl"
        if not path.exists():
            print(f"  warning: missing {path}", file=sys.stderr)
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = json.loads(line)
                pmid = str(rec.get("pmid") or "").strip()
                if not pmid:
                    continue
                if pmid in by_pmid:
                    if group not in by_pmid[pmid]["groups"]:
                        by_pmid[pmid]["groups"].append(group)
                    continue
                title = (rec.get("title") or "").strip()
                abstract = (rec.get("abstract") or "").strip()
                if not title and not abstract:
                    continue
                mesh = rec.get("mesh") or []
                text = title if not abstract else (f"{title.rstrip('.')}. {abstract}" if title else abstract)
                by_pmid[pmid] = {
                    "chunk_id": f"PMID:{pmid}",
                    "pmid": pmid,
                    "title": title,
                    "year": rec.get("year"),
                    "journal": rec.get("journal"),
                    "doi": rec.get("doi"),
                    "groups": [group],
                    "mesh_major": [m.get("term") for m in mesh if m.get("major") and m.get("term")],
                    "mesh": [m.get("term") for m in mesh if m.get("term")],
                    "pub_types": rec.get("pub_types") or [],
                    "has_abstract": bool(abstract),
                    "text": text,
                }
    rows = list(by_pmid.values())
    rows.sort(key=lambda r: int(r["pmid"]) if r["pmid"].isdigit() else 0)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="override retrieval.embedding_model")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--limit", type=int, help="index only the first N abstracts (pipeline check only)")
    ap.add_argument("--out-dir", help="write here instead of paths.index")
    ap.add_argument("--threads", type=int, help="torch threads (default: physical cores)")
    args = ap.parse_args()

    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "atlas.yaml").read_text(encoding="utf-8"))
    rcfg = cfg["retrieval"]
    model_name = args.model or rcfg["embedding_model"]
    max_seq = int(rcfg.get("max_seq_length", 320))
    groups = [g.get("pubmed_slice", name) for name, g in cfg["groups"].items()]
    raw_dir = ROOT / cfg["paths"]["raw"]

    t_all = time.time()
    rows = load_rows(raw_dir, groups)
    if args.limit:
        rows = rows[: args.limit]
        print(f"!! --limit {args.limit}: indexing a SUBSET")
    if not rows:
        print("no abstracts found", file=sys.stderr)
        return 1
    fp = corpus_fingerprint(rows)
    print(f"abstracts: {len(rows)}  (groups {groups})  fingerprint {fp}", flush=True)

    threads = args.threads or physical_cores()
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / cfg["paths"]["index"]
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    shard_dir = out_dir / "shards"
    shard_dir.mkdir(exist_ok=True)
    shard_tag = shard_dir / "fingerprint.txt"
    tag = f"{fp}|{model_name}|{max_seq}"
    if shard_tag.exists() and shard_tag.read_text().strip() != tag:
        print("  shard fingerprint mismatch -> discarding old shards")
        for p in shard_dir.glob("emb_*.npy"):
            p.unlink()
    shard_tag.write_text(tag)

    texts = [r["text"] for r in rows]

    # BM25 needs no embeddings: fit it in a worker process while torch embeds.
    bm25_pool = ProcessPoolExecutor(max_workers=1)
    bm25_future = bm25_pool.submit(_build_bm25, texts)

    import numpy as np
    import torch
    import faiss
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(threads)
    print(f"embedding model: {model_name}  threads: {threads}  max_seq: {max_seq}", flush=True)
    model = SentenceTransformer(model_name, device="cpu")
    model.max_seq_length = max_seq

    n_shards = (len(rows) + SHARD_SIZE - 1) // SHARD_SIZE
    t0 = time.time()
    done_new = 0
    parts = []
    for s in range(n_shards):
        path = shard_dir / f"emb_{s:04d}.npy"
        lo, hi = s * SHARD_SIZE, min(len(rows), (s + 1) * SHARD_SIZE)
        if path.exists():
            arr = np.load(path)
            if arr.shape[0] == hi - lo:
                parts.append(arr)
                print(f"  shard {s + 1}/{n_shards}: cached", flush=True)
                continue
        ts = time.time()
        arr = model.encode(
            texts[lo:hi],
            batch_size=args.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")
        np.save(path, arr)
        parts.append(arr)
        done_new += hi - lo
        el = time.time() - t0
        rate = done_new / el if el else 0
        remaining = len(rows) - hi
        print(
            f"  shard {s + 1}/{n_shards}: {hi - lo} docs in {time.time() - ts:.1f}s "
            f"({rate:.1f} docs/s overall, ~{remaining / rate / 60 if rate else 0:.1f} min left)",
            flush=True,
        )
    vectors = np.concatenate(parts, axis=0)
    embed_s = time.time() - t0
    print(f"embedded in {embed_s:.1f}s -> {vectors.shape}", flush=True)

    dim = int(vectors.shape[1])
    index = faiss.IndexFlatIP(dim)
    index.add(vectors)
    faiss.write_index(index, str(out_dir / "faiss.index"))
    np.save(out_dir / "embeddings.npy", vectors)

    tb = time.time()
    (out_dir / "bm25.pkl").write_bytes(bm25_future.result())
    bm25_pool.shutdown()
    print(f"bm25 collected (waited {time.time() - tb:.1f}s)", flush=True)

    with (out_dir / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    centroid = vectors.mean(axis=0)
    centroid /= np.linalg.norm(centroid) + 1e-12
    np.save(out_dir / "centroid.npy", centroid.astype("float32"))

    meta = {
        "embedding_model": model_name,
        "embedding_dim": dim,
        "normalize": True,
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "doc_prefix": "",
        "max_seq_length": max_seq,
        "reranker_model": rcfg.get("reranker_model"),
        "chunk_count": len(rows),
        "partial": bool(args.limit),
        "corpus_fingerprint": fp,
        "groups": {g: sum(1 for r in rows if g in r["groups"]) for g in groups},
        "with_abstract": sum(1 for r in rows if r["has_abstract"]),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "embed_seconds": round(embed_s, 1),
        "total_seconds": round(time.time() - t_all, 1),
        "threads": threads,
    }
    (out_dir / "index_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nwrote index -> {out_dir}")
    for k, v in meta.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
