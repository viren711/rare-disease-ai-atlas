#!/usr/bin/env python3
"""ClinVar variant_summary.txt.gz -> data/raw/clinvar/variants.tsv (GRCh38 rows for atlas genes only).

1. Parallel, resumable ranged download of the 450 MB gz (8 connections, stdlib).
2. Stream-decompress, keep rows whose GeneID is an atlas disease gene (Gene nodes with a causes/associated_with edge)
   and Assembly == GRCh38. Prints counts and appends provenance to data/raw/sources.json.
Stdlib + pandas/pyarrow only to read the atlas gene list.
"""
import gzip
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/clinvar"
URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz"
GZ = RAW / "variant_summary.txt.gz"
OUT = RAW / "variants.tsv"
N_PARTS = 8


def total_size():
    req = urllib.request.Request(URL, method="HEAD", headers={"User-Agent": "rare-disease-atlas/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"]), r.headers.get("Last-Modified", "")


def fetch_part(i, lo, hi):
    part = RAW / f"part{i}.bin"
    have = part.stat().st_size if part.exists() else 0
    if lo + have > hi:
        return i
    for attempt in range(6):
        try:
            have = part.stat().st_size if part.exists() else 0
            if lo + have > hi:
                return i
            req = urllib.request.Request(URL, headers={"Range": f"bytes={lo + have}-{hi}",
                                                       "User-Agent": "rare-disease-atlas/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            if lo + part.stat().st_size > hi:
                return i
        except Exception as e:
            print(f"  part {i} attempt {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"part {i} failed")


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    size, lm = total_size()
    if GZ.exists() and GZ.stat().st_size == size:
        return size, lm, "cached"
    step = -(-size // N_PARTS)
    ranges = [(i, i * step, min(size, (i + 1) * step) - 1) for i in range(N_PARTS)]
    with ThreadPoolExecutor(N_PARTS) as ex:
        list(ex.map(lambda a: fetch_part(*a), ranges))
    tmp = GZ.with_suffix(".part")
    with open(tmp, "wb") as out:
        for i, _, _ in ranges:
            out.write((RAW / f"part{i}.bin").read_bytes())
            (RAW / f"part{i}.bin").unlink()
    tmp.rename(GZ)
    return size, lm, "downloaded"


def atlas_genes():
    import pandas as pd
    e = pd.read_parquet(ROOT / "data/graph/edges.parquet", columns=["src", "rel"])
    g = set(e.src[e.rel.isin(["causes", "associated_with"])])
    return {x.split(":")[1] for x in g if x.startswith("NCBIGene:")}


def main():
    t0 = time.time()
    size, lm, status = download()
    print(f"[clinvar] {status} {size / 1e6:.0f} MB (Last-Modified {lm}) in {time.time() - t0:.0f}s", flush=True)
    genes = atlas_genes()
    print(f"[clinvar] atlas genes: {len(genes)}")
    n_all = n_keep = 0
    tmp = OUT.with_suffix(".tmp")
    with gzip.open(GZ, "rt", encoding="utf-8", errors="replace") as f, open(tmp, "w") as out:
        header = f.readline()
        cols = header.lstrip("#").rstrip("\n").split("\t")
        gi, ai = cols.index("GeneID"), cols.index("Assembly")
        out.write("\t".join(cols) + "\n")
        for line in f:
            n_all += 1
            p = line.split("\t", 17)
            if p[gi] in genes and p[ai] == "GRCh38":
                out.write(line)
                n_keep += 1
    tmp.rename(OUT)
    print(f"[clinvar] scanned {n_all} rows, kept {n_keep} GRCh38 rows for {len(genes)} genes in {time.time() - t0:.0f}s")
    sj = ROOT / "data/raw/sources.json"
    m = json.loads(sj.read_text()) if sj.exists() else {}
    m["clinvar/variants.tsv"] = {"url": URL, "bytes": OUT.stat().st_size, "records": n_keep, "status": "downloaded",
                                 "retrieved": time.strftime("%Y-%m-%d"), "source_last_modified": lm,
                                 "note": "variant_summary.txt.gz filtered to GRCh38 rows of atlas genes; NCBI public domain"}
    sj.write_text(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
