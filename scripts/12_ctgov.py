#!/usr/bin/env python3
"""Fetch ClinicalTrials.gov (API v2) studies for both disease groups.

Studies, registries and natural-history studies become Trial/Asset nodes; sponsors and collaborators
seed PatientOrg nodes; overall officials seed Researcher nodes.
Stdlib only. Output: data/raw/ctgov/studies.jsonl (one compact record per NCT id, deduped).
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/ctgov"
API = "https://clinicaltrials.gov/api/v2/studies"

QUERIES = {
    "lysosomal": ["lysosomal storage disease", "Fabry disease", "Gaucher disease", "Pompe disease",
                  "mucopolysaccharidosis", "Niemann-Pick disease", "metachromatic leukodystrophy",
                  "Krabbe disease", "Tay-Sachs disease", "GM1 gangliosidosis", "neuronal ceroid lipofuscinosis",
                  "cystinosis", "alpha-mannosidosis", "Wolman disease", "mucolipidosis"],
    "peroxisomal": ["peroxisomal disorder", "adrenoleukodystrophy", "adrenomyeloneuropathy",
                    "Zellweger spectrum disorder", "Refsum disease", "rhizomelic chondrodysplasia punctata",
                    "primary hyperoxaluria"],
}


def get(params):
    url = f"{API}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.load(r)
        except Exception as e:
            print(f"  {params.get('query.cond')}: attempt {attempt + 1} {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(url)


def compact(s, group, query):
    p = s.get("protocolSection", {})
    idm, st, de = p.get("identificationModule", {}), p.get("statusModule", {}), p.get("designModule", {})
    sc = p.get("sponsorCollaboratorsModule", {})
    cl = p.get("contactsLocationsModule", {})
    return {
        "nct": idm.get("nctId"),
        "title": idm.get("briefTitle"),
        "official_title": idm.get("officialTitle"),
        "acronym": idm.get("acronym"),
        "group": group,
        "query": query,
        "status": st.get("overallStatus"),
        "start": (st.get("startDateStruct") or {}).get("date"),
        "completion": (st.get("completionDateStruct") or {}).get("date"),
        "why_stopped": st.get("whyStopped"),
        "has_results": bool(s.get("hasResults")),
        "last_update": (st.get("lastUpdatePostDateStruct") or {}).get("date"),
        "study_type": de.get("studyType"),
        "phases": de.get("phases", []),
        "patient_registry": de.get("patientRegistry", False),
        "observational_model": (de.get("designInfo") or {}).get("observationalModel"),
        "enrollment": (de.get("enrollmentInfo") or {}).get("count"),
        "conditions": p.get("conditionsModule", {}).get("conditions", []),
        "keywords": p.get("conditionsModule", {}).get("keywords", []),
        "interventions": [{"type": i.get("type"), "name": i.get("name")}
                          for i in p.get("armsInterventionsModule", {}).get("interventions", [])],
        "summary": p.get("descriptionModule", {}).get("briefSummary", ""),
        "eligibility": (p.get("eligibilityModule", {}).get("eligibilityCriteria") or "")[:3000],
        "min_age": p.get("eligibilityModule", {}).get("minimumAge"),
        "max_age": p.get("eligibilityModule", {}).get("maximumAge"),
        "lead_sponsor": (sc.get("leadSponsor") or {}),
        "collaborators": sc.get("collaborators", []),
        "officials": cl.get("overallOfficials", []),
        "countries": sorted({loc.get("country") for loc in cl.get("locations", []) if loc.get("country")}),
        "mesh_conditions": [m.get("term") for m in s.get("derivedSection", {}).get("conditionBrowseModule", {})
                            .get("meshes", [])],
        "references": [r.get("pmid") for r in p.get("referencesModule", {}).get("references", []) if r.get("pmid")],
    }


def run(group, query):
    recs, token = [], None
    while True:
        params = {"query.cond": query, "pageSize": 1000, "format": "json"}
        if token:
            params["pageToken"] = token
        d = get(params)
        recs += [compact(s, group, query) for s in d.get("studies", [])]
        token = d.get("nextPageToken")
        if not token:
            return group, query, recs


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(g, q) for g, qs in QUERIES.items() for q in qs]
    by_nct = {}
    with ThreadPoolExecutor(6) as ex:
        for group, query, recs in ex.map(lambda j: run(*j), jobs):
            print(f"[ctgov] {group:12s} {query:40s} {len(recs)}", flush=True)
            for r in recs:
                if r["nct"] in by_nct:
                    old = by_nct[r["nct"]]
                    old["groups"] = sorted(set(old["groups"]) | {group})
                    old["queries"] = sorted(set(old["queries"]) | {query})
                else:
                    r["groups"], r["queries"] = [group], [query]
                    by_nct[r["nct"]] = r
    tmp = OUT / "studies.jsonl.tmp"
    with open(tmp, "w") as f:
        for r in by_nct.values():
            r.pop("group"), r.pop("query")
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.rename(OUT / "studies.jsonl")
    print(f"[ctgov] wrote {len(by_nct)} unique studies "
          f"({sum(r['patient_registry'] for r in by_nct.values())} registries)")


if __name__ == "__main__":
    main()
