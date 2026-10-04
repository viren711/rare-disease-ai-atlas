#!/usr/bin/env python3
"""Download PubMed abstracts for a disease slice (default: Lysosomal Storage Diseases MeSH).

Stdlib only. Resumable: PMID lists and fetched batches are cached on disk, reruns skip
finished work. Respects NCBI rate limits (3 req/s, or 10 req/s with NCBI_API_KEY).

Output: data/raw/pubmed/<slice>/batches/*.jsonl  -> merged into abstracts.jsonl
Each record: pmid, title, abstract, sections, journal, year, doi, pmcid,
             authors[{last, fore, initials, affiliations, orcid}], mesh[], keywords[], pub_types[]
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
API_KEY = os.environ.get("NCBI_API_KEY", "")
TOOL = "rare-disease-atlas"


class RateLimiter:
    def __init__(self, per_sec):
        self.interval = 1.0 / per_sec
        self.lock = threading.Lock()
        self.next_t = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next_t:
                time.sleep(self.next_t - now)
            self.next_t = max(now, self.next_t) + self.interval


RATE = RateLimiter(9 if API_KEY else 2.8)


def call(endpoint, params, retries=6):
    params = dict(params, tool=TOOL)
    if API_KEY:
        params["api_key"] = API_KEY
    data = urllib.parse.urlencode(params).encode()
    for attempt in range(retries):
        RATE.wait()
        try:
            req = urllib.request.Request(f"{EUTILS}/{endpoint}", data=data)
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:  # 429 / 5xx / timeouts
            wait = 2 ** attempt
            print(f"  [{endpoint}] attempt {attempt + 1} failed: {e}; retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"{endpoint} failed after {retries} retries")


def esearch_ids(term, mindate, maxdate):
    p = {"db": "pubmed", "term": term, "retmode": "json", "retmax": 9999,
         "datetype": "pdat", "mindate": mindate, "maxdate": maxdate}
    res = json.loads(call("esearch.fcgi", p))["esearchresult"]
    count = int(res["count"])
    if count > 9999:
        return None, count
    return res["idlist"], count


def collect_pmids(term, start_year, end_year):
    """Split by date ranges so each esearch stays under the 10k cap."""
    pmids = []
    stack = [(start_year, end_year)]
    while stack:
        a, b = stack.pop()
        ids, count = esearch_ids(term, f"{a}/01/01", f"{b}/12/31")
        if ids is None:
            if a == b:  # single year still > 10k: split by half-year
                for lo, hi in ((f"{a}/01/01", f"{a}/06/30"), (f"{a}/07/01", f"{a}/12/31")):
                    sub, _ = esearch_ids(term, lo, hi)
                    pmids.extend(sub or [])
                continue
            mid = (a + b) // 2
            stack += [(a, mid), (mid + 1, b)]
        else:
            print(f"  {a}-{b}: {count} pmids")
            pmids.extend(ids)
    return sorted(set(pmids), key=int)


def text(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_article(art):
    mc = art.find("MedlineCitation")
    a = mc.find("Article")
    secs = []
    for t in a.findall("Abstract/AbstractText"):
        secs.append({"label": t.get("Label") or t.get("NlmCategory") or "", "text": text(t)})
    authors = []
    for au in a.findall("AuthorList/Author"):
        orcid = next((text(i) for i in au.findall("Identifier") if i.get("Source") == "ORCID"), "")
        authors.append({
            "last": text(au.find("LastName")) or text(au.find("CollectiveName")),
            "fore": text(au.find("ForeName")),
            "initials": text(au.find("Initials")),
            "affiliations": [text(x) for x in au.findall("AffiliationInfo/Affiliation")],
            "orcid": orcid,
        })
    ids = {i.get("IdType"): text(i) for i in art.findall("PubmedData/ArticleIdList/ArticleId")}
    year = text(a.find("Journal/JournalIssue/PubDate/Year")) or \
        text(a.find("Journal/JournalIssue/PubDate/MedlineDate"))[:4]
    mesh = []
    for mh in mc.findall("MeshHeadingList/MeshHeading"):
        d = mh.find("DescriptorName")
        mesh.append({"term": text(d), "ui": d.get("UI"), "major": d.get("MajorTopicYN") == "Y",
                     "qualifiers": [text(q) for q in mh.findall("QualifierName")]})
    return {
        "pmid": text(mc.find("PMID")),
        "title": text(a.find("ArticleTitle")),
        "abstract": "\n".join((f"{s['label']}: " if s["label"] else "") + s["text"] for s in secs),
        "sections": secs,
        "journal": text(a.find("Journal/Title")),
        "year": int(year) if year[:4].isdigit() else None,
        "doi": ids.get("doi", ""),
        "pmcid": ids.get("pmc", ""),
        "authors": authors,
        "mesh": mesh,
        "keywords": [text(k) for k in mc.findall("KeywordList/Keyword")],
        "pub_types": [text(p) for p in a.findall("PublicationTypeList/PublicationType")],
        "chemicals": [text(c.find("NameOfSubstance")) for c in mc.findall("ChemicalList/Chemical")],
    }


def fetch_batch(i, ids, out_dir):
    out = out_dir / f"batch_{i:05d}.jsonl"
    if out.exists():
        return i, 0, True
    xml = call("efetch.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    root = ET.fromstring(xml)
    recs = [parse_article(x) for x in root.findall("PubmedArticle")]
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.rename(out)
    return i, len(recs), False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slice", default="lysosomal")
    ap.add_argument("--term", default='"Lysosomal Storage Diseases"[MeSH] AND hasabstract')
    ap.add_argument("--start-year", type=int, default=1950)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / "data/raw/pubmed"))
    args = ap.parse_args()

    base = Path(args.root) / args.slice
    bdir = base / "batches"
    bdir.mkdir(parents=True, exist_ok=True)
    pmid_file = base / "pmids.txt"

    if pmid_file.exists():
        pmids = pmid_file.read_text().split()
        print(f"[pmids] cached: {len(pmids)}")
    else:
        print(f"[pmids] searching: {args.term}")
        pmids = collect_pmids(args.term, args.start_year, args.end_year)
        pmid_file.write_text("\n".join(pmids))
        (base / "query.json").write_text(json.dumps(vars(args) | {"n": len(pmids),
                                                                   "fetched_at": time.strftime("%Y-%m-%d")}, indent=2))
        print(f"[pmids] total unique: {len(pmids)}")

    batches = [pmids[i:i + args.batch] for i in range(0, len(pmids), args.batch)]
    done = n_recs = 0
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(fetch_batch, i, b, bdir) for i, b in enumerate(batches)]
        for fu in as_completed(futs):
            try:
                i, n, skipped = fu.result()
            except Exception as e:
                print(f"[fetch] batch failed: {e}", file=sys.stderr)
                continue
            done += 1
            n_recs += n
            if done % 10 == 0 or done == len(batches):
                print(f"[fetch] {done}/{len(batches)} batches, {n_recs} new records, {time.time() - t0:.0f}s",
                      flush=True)

    files = sorted(bdir.glob("batch_*.jsonl"))
    if len(files) < len(batches):
        print(f"[merge] {len(batches) - len(files)} batches missing; rerun to resume", file=sys.stderr)
        sys.exit(1)
    seen, n = set(), 0
    with open(base / "abstracts.jsonl", "w") as out:
        for fp in files:
            for line in open(fp):
                pmid = json.loads(line)["pmid"]
                if pmid not in seen:
                    seen.add(pmid)
                    out.write(line)
                    n += 1
    print(f"[merge] wrote {n} abstracts -> {base / 'abstracts.jsonl'}")


if __name__ == "__main__":
    main()
