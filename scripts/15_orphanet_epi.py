#!/usr/bin/env python3
"""Orphanet epidemiology (Orphadata product 9): prevalence + age of onset / inheritance.

Downloads en_product9_prev.xml and en_product9_ages.xml in parallel (CC-BY-4.0), keeps only ORPHA codes mapped to atlas
diseases and writes data/raw/orphanet/epidemiology.json: {"ORPHA:58": {"name","prevalence":[{class,type,geo,
qualification,validation,val_moy,source}],"onset":[...],"inheritance":[...]}}.
"""
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/orphanet"
FILES = {"en_product9_prev.xml": "https://www.orphadata.com/data/xml/en_product9_prev.xml",
         "en_product9_ages.xml": "https://www.orphadata.com/data/xml/en_product9_ages.xml"}


def fetch(name, url):
    out = RAW / name
    if out.exists() and out.stat().st_size > 1000:
        return name, "cached"
    for a in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rare-disease-atlas/0.1"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            tmp = out.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.rename(out)
            return name, "downloaded"
        except Exception as e:
            print(f"  {name}: attempt {a + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** a)
    return name, "FAILED"


def main():
    import pandas as pd
    t0 = time.time()
    with ThreadPoolExecutor(2) as ex:
        res = dict(ex.map(lambda kv: fetch(*kv), FILES.items()))
    print(res)
    x = pd.read_parquet(ROOT / "data/graph/xref_map.parquet")
    atlas = set(x[x.in_set & x.xref.str.startswith("ORPHA:")].xref)
    out, root_date = {}, ""
    root = ET.parse(RAW / "en_product9_prev.xml").getroot()
    root_date = root.get("date", "")
    for d in root.iter("Disorder"):
        key = f"ORPHA:{d.findtext('OrphaCode')}"
        if key not in atlas:
            continue
        prev = []
        for p in d.iter("Prevalence"):
            prev.append({"type": p.findtext("PrevalenceType/Name"), "qualification": p.findtext("PrevalenceQualification/Name"),
                         "class": p.findtext("PrevalenceClass/Name"), "geographic": p.findtext("PrevalenceGeographic/Name"),
                         "validation": p.findtext("PrevalenceValidationStatus/Name"), "val_moy": p.findtext("ValMoy"),
                         "source": (p.findtext("Source") or "").strip()})
        out[key] = {"name": d.findtext("Name"), "prevalence": prev, "onset": [], "inheritance": []}
    n_prev = len(out)
    root = ET.parse(RAW / "en_product9_ages.xml").getroot()
    for d in root.iter("Disorder"):
        key = f"ORPHA:{d.findtext('OrphaCode')}"
        if key not in atlas:
            continue
        e = out.setdefault(key, {"name": d.findtext("Name"), "prevalence": [], "onset": [], "inheritance": []})
        e["onset"] = [n.text for n in d.iterfind("AverageAgeOfOnsetList/AverageAgeOfOnset/Name")]
        e["inheritance"] = [n.text for n in d.iterfind("TypeOfInheritanceList/TypeOfInheritance/Name")]
    tmp = RAW / "epidemiology.json.tmp"
    tmp.write_text(json.dumps({"orphadata_date": root_date, "disorders": out}, ensure_ascii=False))
    tmp.rename(RAW / "epidemiology.json")
    print(f"[orphanet-epi] atlas ORPHA codes {len(atlas)}; with prevalence {n_prev}; total with any epi {len(out)}; "
          f"with onset {sum(bool(v['onset']) for v in out.values())}; inheritance {sum(bool(v['inheritance']) for v in out.values())} "
          f"({time.time() - t0:.0f}s)")
    sj = ROOT / "data/raw/sources.json"
    m = json.loads(sj.read_text()) if sj.exists() else {}
    for n, u in FILES.items():
        m[f"orphanet/{n}"] = {"url": u, "bytes": (RAW / n).stat().st_size, "records": len(out), "status": res[n],
                              "retrieved": time.strftime("%Y-%m-%d"), "note": f"Orphadata {root_date}; CC-BY-4.0"}
    sj.write_text(json.dumps(m, indent=2))


if __name__ == "__main__":
    main()
