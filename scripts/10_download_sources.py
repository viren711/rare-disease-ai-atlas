#!/usr/bin/env python3
"""Download the bulk biology files (ontologies, annotations, pathways) in parallel.

Stdlib only. Skips files already present (delete to refresh). Writes data/raw/sources.json
with URL, size and retrieval date for each file so the README can cite provenance.
"""
import json
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"

SOURCES = {
    "mondo/mondo.json": "https://purl.obolibrary.org/obo/mondo.json",
    "hpo/hp.json": "https://purl.obolibrary.org/obo/hp.json",
    "hpo/phenotype.hpoa": "https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa",
    "hpo/genes_to_phenotype.txt": "https://purl.obolibrary.org/obo/hp/hpoa/genes_to_phenotype.txt",
    "hpo/genes_to_disease.txt": "https://purl.obolibrary.org/obo/hp/hpoa/genes_to_disease.txt",
    "orphanet/en_product6.xml": "https://www.orphadata.com/data/xml/en_product6.xml",
    "reactome/NCBI2Reactome.txt": "https://reactome.org/download/current/NCBI2Reactome.txt",
    "reactome/ReactomePathways.txt": "https://reactome.org/download/current/ReactomePathways.txt",
    "reactome/ReactomePathwaysRelation.txt": "https://reactome.org/download/current/ReactomePathwaysRelation.txt",
}


def fetch(rel, url, retries=4):
    out = RAW / rel
    if out.exists() and out.stat().st_size > 0:
        return rel, out.stat().st_size, "cached"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rare-disease-atlas/0.1"})
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            tmp.rename(out)
            return rel, out.stat().st_size, "downloaded"
        except Exception as e:
            print(f"  {rel}: attempt {attempt + 1} failed: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return rel, 0, "FAILED"


def main():
    t0 = time.time()
    manifest = {}
    with ThreadPoolExecutor(len(SOURCES)) as ex:
        for rel, size, status in ex.map(lambda kv: fetch(*kv), SOURCES.items()):
            print(f"[{status}] {rel} {size / 1e6:.1f} MB", flush=True)
            manifest[rel] = {"url": SOURCES[rel], "bytes": size, "status": status,
                             "retrieved": time.strftime("%Y-%m-%d")}
    (RAW / "sources.json").write_text(json.dumps(manifest, indent=2))
    print(f"done in {time.time() - t0:.0f}s")
    sys.exit(any(m["status"] == "FAILED" for m in manifest.values()))


if __name__ == "__main__":
    main()
