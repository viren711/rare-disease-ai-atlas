#!/usr/bin/env python3
"""NIH RePORTER v2 (https://api.reporter.nih.gov/v2/projects/search): grants for the atlas diseases and genes.

Parallel, resumable (one cache file per query in data/raw/reporter/q/). One query = a quoted phrase searched in project
title + abstract, fiscal years FY_FROM..current. Offsets are capped at 14,999 by the API, so a query that reports more
hits is split by fiscal year. Output: data/raw/reporter/projects.jsonl, one compact row per (project_num, fiscal year).
Public-domain US government data (NIH RePORTER terms: https://reporter.nih.gov/about). Stdlib only.
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/reporter"
API = "https://api.reporter.nih.gov/v2/projects/search"
FY_FROM, FY_TO = 2005, 2026
PAGE = 500
INCLUDE = ["ApplId", "ProjectNum", "CoreProjectNum", "ProjectTitle", "AbstractText", "FiscalYear", "AwardAmount",
           "PrincipalInvestigators", "Organization", "AgencyIcAdmin", "Terms", "ProjectStartDate", "ProjectEndDate",
           "IsActive", "ProjectDetailUrl", "ActivityCode"]

DISEASES = [
    "Krabbe disease", "globoid cell leukodystrophy", "galactosylceramidase", "metachromatic leukodystrophy",
    "adrenoleukodystrophy", "adrenomyeloneuropathy", "Zellweger", "peroxisome biogenesis disorder", "peroxisomal disorder",
    "peroxisomal disease", "Refsum disease", "rhizomelic chondrodysplasia punctata", "primary hyperoxaluria",
    "acatalasia", "cerebrotendinous xanthomatosis", "Fabry disease", "Gaucher disease", "Pompe disease",
    "glycogen storage disease type II", "acid maltase deficiency", "mucopolysaccharidosis", "Hurler syndrome",
    "Hunter syndrome", "Sanfilippo", "Morquio", "Maroteaux-Lamy", "Sly syndrome", "Niemann-Pick", "acid sphingomyelinase",
    "Tay-Sachs", "Sandhoff disease", "GM1 gangliosidosis", "GM2 gangliosidosis", "neuronal ceroid lipofuscinosis",
    "Batten disease", "cystinosis", "alpha-mannosidosis", "beta-mannosidosis", "fucosidosis", "sialidosis",
    "galactosialidosis", "Salla disease", "mucolipidosis", "Wolman disease", "lysosomal acid lipase deficiency",
    "cholesteryl ester storage disease", "Danon disease", "Farber disease", "aspartylglucosaminuria",
    "lysosomal storage disease", "lysosomal storage disorder", "lysosomal disorder", "sphingolipidosis",
    "leukodystrophy", "pycnodysostosis", "multiple sulfatase deficiency", "saposin", "neutral lipid storage disease",
    "Chanarin-Dorfman", "triglyceride deposit cardiomyovasculopathy", "oligosaccharidosis", "glycoproteinosis",
    "Kufs disease", "Schindler disease", "sialuria", "lipid storage disease", "enzyme replacement therapy lysosomal",
]
GENES = ["ABCD1", "ABCD3", "ACOX1", "AGXT", "AMACR", "ARSA", "ARSB", "ASAH1", "ATP13A2", "CLN3", "CLN5", "CLN6", "CLN8",
         "CTNS", "CYP27A1", "DNAJC5", "FUCA1", "GALC", "GALNS", "GBA1", "GLB1", "GM2A", "GNPTAB", "GNPTG", "GUSB", "HEXA",
         "HEXB", "HGSNAT", "HSD17B4", "IDS", "IDUA", "KCTD7", "LAMP2", "LIPA", "MAN2B1", "MANBA", "MCOLN1", "MFSD8",
         "NAGA", "NAGLU", "NEU1", "NPC1", "NPC2", "PEX1", "PEX2", "PEX3", "PEX5", "PEX6", "PEX7", "PEX10", "PEX12",
         "PEX13", "PEX14", "PEX16", "PEX19", "PEX26", "PHYH", "PPT1", "PSAP", "SGSH", "SLC17A5", "SMPD1", "SUMF1", "TPP1",
         "GNPAT", "AGPS", "FAR1", "PNPLA2", "ABHD5", "CTSD", "CTSF", "ACP2"]


def post(body, tries=6):
    data = json.dumps(body).encode()
    for a in range(tries):
        try:
            req = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "rare-disease-atlas/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            time.sleep(2 ** a)
            err = e
    raise RuntimeError(f"RePORTER failed: {err}")


def crit(phrase, years):
    return {"advanced_text_search": {"operator": "and", "search_field": "projecttitle,abstracttext",
                                     "search_text": f'"{phrase}"'}, "fiscal_years": years}


def pull(phrase, years):
    out, off, total = [], 0, None
    while True:
        d = post({"criteria": crit(phrase, years), "include_fields": INCLUDE, "offset": off, "limit": PAGE,
                  "sort_field": "fiscal_year", "sort_order": "desc"})
        total = d["meta"]["total"]
        res = d.get("results", [])
        out += res
        off += PAGE
        if not res or off >= total or off > 14999:
            break
        time.sleep(0.2)
    return total, out


def query(phrase):
    cache = RAW / "q" / (phrase.lower().replace(" ", "_").replace("/", "-") + ".json")
    if cache.exists():
        return phrase, json.loads(cache.read_text())
    years = list(range(FY_FROM, FY_TO + 1))
    total, rows = pull(phrase, years)
    if total > 14000:
        rows = []
        for y in years:
            _, r = pull(phrase, [y])
            rows += r
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"total": total, "rows": rows}))
    return phrase, {"total": total, "rows": rows}


def compact(r):
    org = r.get("organization") or {}
    ic = r.get("agency_ic_admin") or {}
    abst = " ".join((r.get("abstract_text") or "").split())
    return {"project_num": r.get("project_num"), "core": r.get("core_project_num"), "fy": r.get("fiscal_year"),
            "title": r.get("project_title"), "abstract": abst[:700], "amount": r.get("award_amount"),
            "pis": [{"id": p.get("profile_id"), "name": p.get("full_name"), "contact": p.get("is_contact_pi")}
                    for p in r.get("principal_investigators") or []],
            "org": org.get("org_name"), "city": org.get("org_city"), "state": org.get("org_state"),
            "country": org.get("org_country"), "ic": ic.get("abbreviation"), "ic_name": ic.get("name"),
            "terms": [t for t in (r.get("terms") or "").strip("<>").split("><") if t][:60],
            "start": (r.get("project_start_date") or "")[:10], "end": (r.get("project_end_date") or "")[:10],
            "active": bool(r.get("is_active")), "url": r.get("project_detail_url"), "activity": r.get("activity_code"),
            "appl_id": r.get("appl_id")}


def main():
    t0 = time.time()
    phrases = DISEASES + GENES
    rows = {}
    with ThreadPoolExecutor(4) as ex:
        for phrase, d in ex.map(query, phrases):
            print(f"[reporter] {phrase:45s} total={d['total']:6d} fetched={len(d['rows'])}", flush=True)
            for r in d["rows"]:
                c = compact(r)
                key = (c["project_num"], c["fy"])
                if key in rows:
                    rows[key]["queries"].append(phrase)
                else:
                    c["queries"] = [phrase]
                    rows[key] = c
    tmp = RAW / "projects.jsonl.tmp"
    with open(tmp, "w") as f:
        for k in sorted(rows, key=lambda k: (str(k[0]), k[1] or 0)):
            f.write(json.dumps(rows[k], ensure_ascii=False) + "\n")
    tmp.rename(RAW / "projects.jsonl")
    cores = {r["core"] for r in rows.values()}
    print(f"[reporter] {len(rows)} project-years, {len(cores)} distinct projects in {time.time() - t0:.0f}s")
    sj = ROOT / "data/raw/sources.json"
    m = json.loads(sj.read_text()) if sj.exists() else {}
    m["reporter/projects.jsonl"] = {"url": API, "bytes": (RAW / "projects.jsonl").stat().st_size, "records": len(cores),
                                    "status": "downloaded", "retrieved": time.strftime("%Y-%m-%d"),
                                    "note": f"NIH RePORTER v2, FY{FY_FROM}-{FY_TO}; {len(phrases)} disease/gene phrase queries "
                                            f"on title+abstract; {len(rows)} project-years; US public domain"}
    sj.write_text(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
