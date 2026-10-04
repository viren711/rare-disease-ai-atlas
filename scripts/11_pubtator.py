#!/usr/bin/env python3
"""Fetch PubTator3 entity annotations (Gene, Disease, Chemical, Variant) for every downloaded PMID.

These give paper->gene / paper->disease "extracted" edges without running any LLM.
Stdlib only, resumable (one file per batch), ~3 req/s.
Output: data/raw/pubtator/annotations.jsonl  {pmid, entities:[{type,id,name,count,texts}], relations:[...]}
"""
import json
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_pubmed import RateLimiter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/pubtator"
API = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"
KEEP = {"Gene", "Disease", "Chemical", "Variant", "Mutation", "DNAMutation", "ProteinMutation", "SNP"}
RATE = RateLimiter(2.8)
BATCH = 100


def compact(doc):
    ents = defaultdict(lambda: {"count": 0, "texts": set()})
    meta = {}
    for p in doc.get("passages", []):
        for a in p.get("annotations", []):
            inf = a.get("infons", {})
            t = inf.get("type")
            ident = inf.get("identifier")
            if t not in KEEP or not ident or ident == "-":
                continue
            key = (t, str(ident))
            ents[key]["count"] += 1
            ents[key]["texts"].add(a.get("text", ""))
            meta[key] = inf.get("name") or a.get("text", "")
    return {
        "pmid": str(doc.get("id", "")).split("|")[0],
        "entities": [{"type": t, "id": i, "name": meta[(t, i)], "count": v["count"],
                      "texts": sorted(v["texts"])[:5]} for (t, i), v in ents.items()],
        "relations": [r.get("infons", {}) for r in doc.get("relations", [])],
    }


def fetch(i, pmids, bdir):
    out = bdir / f"batch_{i:05d}.jsonl"
    if out.exists():
        return 0
    for attempt in range(6):
        RATE.wait()
        try:
            with urllib.request.urlopen(f"{API}?pmids={','.join(pmids)}", timeout=120) as r:
                docs = json.load(r).get("PubTator3", [])
            break
        except Exception as e:
            print(f"  batch {i} attempt {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"batch {i} failed")
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for d in docs:
            f.write(json.dumps(compact(d)) + "\n")
    tmp.rename(out)
    return len(docs)


def main():
    pmids = sorted({p for f in (ROOT / "data/raw/pubmed").glob("*/pmids.txt") for p in f.read_text().split()}, key=int)
    bdir = OUT / "batches"
    bdir.mkdir(parents=True, exist_ok=True)
    batches = [pmids[i:i + BATCH] for i in range(0, len(pmids), BATCH)]
    print(f"[pubtator] {len(pmids)} pmids, {len(batches)} batches")
    t0, done = time.time(), 0
    with ThreadPoolExecutor(3) as ex:
        futs = [ex.submit(fetch, i, b, bdir) for i, b in enumerate(batches)]
        for fu in as_completed(futs):
            try:
                fu.result()
            except Exception as e:
                print(f"[pubtator] {e}", file=sys.stderr)
            done += 1
            if done % 25 == 0:
                print(f"[pubtator] {done}/{len(batches)} {time.time() - t0:.0f}s", flush=True)
    files = sorted(bdir.glob("batch_*.jsonl"))
    if len(files) < len(batches):
        sys.exit(f"[pubtator] {len(batches) - len(files)} batches missing; rerun to resume")
    n = 0
    with open(OUT / "annotations.jsonl", "w") as out:
        for fp in files:
            for line in open(fp):
                out.write(line)
                n += 1
    print(f"[pubtator] wrote {n} docs")


if __name__ == "__main__":
    main()
