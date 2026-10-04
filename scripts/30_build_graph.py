#!/usr/bin/env python3
"""Build the atlas graph tables: data/graph/nodes.parquet + edges.parquet (+ synonyms.parquet).

Every edge carries provenance: source, source_id, date, confidence (0-1), evidence_type, detail (JSON).
  curated   : MONDO subclass_of, HPO/Orphanet gene->disease, HPO disease->phenotype, Reactome gene->pathway,
              CT.gov trial->disease / trial->paper / org->trial / investigator->trial, seed patient-org->disease
  extracted : PubMed MeSH + PubTator paper->disease / paper->gene mentions, PubMed author->paper
  inferred  : (written by 40_cluster.py) disease~disease similar_to
Requires scripts/20_ontology.py outputs. Re-run (then 40) after PubTator finishes to pick up more mentions.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config/atlas.yaml").read_text())
RAW = ROOT / CFG["paths"]["raw"]
OUT = ROOT / CFG["paths"]["graph"]
GCFG = CFG["graph"]
SOURCES = json.loads((RAW / "sources.json").read_text()) if (RAW / "sources.json").exists() else {}
TODAY = time.strftime("%Y-%m-%d")


def retrieved(key):
    return SOURCES.get(key, {}).get("retrieved", TODAY)


def norm(s: str) -> str:
    s = (s or "").lower().replace("'s ", " ").replace("’s ", " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def write_parquet(df, path):
    """Atomic write: the running app never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


class Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def node(self, nid, typ, label, group="", **attrs):
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "type": typ, "label": label, "group": group, "attrs": attrs}
        else:
            self.nodes[nid]["attrs"].update(attrs)

    def edge(self, src, dst, rel, source, source_id, date, confidence, evidence_type, **detail):
        self.edges.append({"src": src, "dst": dst, "rel": rel, "source": source, "source_id": str(source_id),
                           "date": str(date or ""), "confidence": round(float(confidence), 3),
                           "evidence_type": evidence_type, "detail": detail})


# ------------------------------------------------------------------------------------------ load
def load():
    t = {name: pd.read_parquet(OUT / f"{name}.parquet") for name in
         ("diseases", "xref_map", "hpo_terms", "genes", "pathways", "papers", "paper_disease", "paper_gene",
          "mesh_names", "synonyms_all")}
    return t


def add_diseases(G, T):
    dis = T["diseases"]
    for r in dis.itertuples():
        G.node(r.id, "Disease", r.label, r.group, definition=r.definition, synonyms=json.loads(r.synonyms)[:30],
               xrefs=[x for x in json.loads(r.xrefs) if x.split(":")[0] in ("OMIM", "ORPHA", "MESH", "DOID", "GARD")],
               bridge=bool(r.bridge))
    for r in dis.itertuples():
        for p in json.loads(r.parents):
            G.edge(r.id, p, "subclass_of", "MONDO", r.id, retrieved("mondo/mondo.json"), 1.0, "curated")
    return set(dis.id)


def xref_to_mondo(T):
    x = T["xref_map"]
    x = x[x.in_set]
    return x.groupby("xref").mondo_id.apply(lambda s: sorted(set(s))).to_dict()


# ----------------------------------------------------------------------------- gene -> disease
ORPHA_CAUSAL = ("Disease-causing", "Major susceptibility")


def add_gene_disease(G, T, x2m):
    genes = T["genes"].set_index("id")
    sym2id = dict(zip(T["genes"].symbol, T["genes"].id))
    pairs = defaultdict(lambda: {"sources": set(), "xrefs": set(), "types": set(), "status": set(), "pmids": set(),
                                 "conf": 0.0})
    g2d = pd.read_csv(RAW / "hpo/genes_to_disease.txt", sep="\t", dtype=str)
    for gid, sym, atype, did, src in g2d.itertuples(index=False):
        for m in x2m.get(did, ()):
            p = pairs[(gid, m)]
            label = "Orphanet (via HPO genes_to_disease)" if "orphadata" in src else "OMIM/MedGen mim2gene (via HPO)"
            p["sources"].add(label)
            p["xrefs"].add(did)
            p["types"].add(atype)
            p["conf"] = max(p["conf"], {"MENDELIAN": 0.9, "POLYGENIC": 0.5}.get(atype, 0.75))
    root = ET.parse(RAW / "orphanet/en_product6.xml").getroot()
    for dis in root.iter("Disorder"):
        orpha = f"ORPHA:{dis.findtext('OrphaCode')}"
        ms = x2m.get(orpha, ())
        if not ms:
            continue
        for a in dis.iter("DisorderGeneAssociation"):
            sym = a.findtext("Gene/Symbol")
            gid = sym2id.get(sym)
            if gid is None:
                continue
            atype = a.findtext("DisorderGeneAssociationType/Name") or ""
            status = a.findtext("DisorderGeneAssociationStatus/Name") or ""
            pm = re.findall(r"(\d+)\[PMID\]", a.findtext("SourceOfValidation") or "")
            for m in ms:
                p = pairs[(gid, m)]
                p["sources"].add("Orphanet product6")
                p["xrefs"].add(orpha)
                p["types"].add(atype)
                p["status"].add(status)
                p["pmids"].update(pm)
                c = 0.95 if status == "Assessed" else 0.7
                if not atype.startswith(ORPHA_CAUSAL):
                    c -= 0.25
                p["conf"] = max(p["conf"], c)
    gene_ids = set()
    for (gid, m), p in sorted(pairs.items()):
        if gid not in genes.index:
            continue
        gene_ids.add(gid)
        causal = any(t.startswith(ORPHA_CAUSAL) or t == "MENDELIAN" for t in p["types"])
        conf = min(1.0, p["conf"] + (0.05 if len(p["sources"]) > 1 else 0))
        src = " + ".join(sorted(p["sources"]))
        G.edge(gid, m, "causes" if causal else "associated_with", src, sorted(p["xrefs"])[0],
               retrieved("orphanet/en_product6.xml"), conf, "curated",
               association_types=sorted(p["types"]), status=sorted(p["status"]), xrefs=sorted(p["xrefs"]),
               pmids=sorted(p["pmids"], key=int)[:20])
    return gene_ids


# -------------------------------------------------------------------------- disease -> phenotype
FREQ_HP = {"HP:0040280": 1.0, "HP:0040281": 0.9, "HP:0040282": 0.55, "HP:0040283": 0.17, "HP:0040284": 0.02,
           "HP:0040285": 0.0}
EVID_CONF = {"PCS": 0.95, "TAS": 0.85, "IEA": 0.7}


def freq_value(f):
    if not f:
        return None
    if f in FREQ_HP:
        return FREQ_HP[f]
    m = re.match(r"(\d+)/(\d+)", f)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    m = re.match(r"([\d.]+)%", f)
    return float(m.group(1)) / 100 if m else None


def hpo_ancestors(hpo):
    parents = {r.id: json.loads(r.parents) for r in hpo.itertuples()}
    cache = {}

    def anc(t):
        if t in cache:
            return cache[t]
        out = {t}
        for p in parents.get(t, ()):
            out |= anc(p)
        cache[t] = frozenset(out)
        return cache[t]
    sys.setrecursionlimit(10000)
    for t in parents:
        anc(t)
    return cache, parents


def add_phenotypes(G, T, x2m, in_set):
    hpo = T["hpo_terms"]
    alt = {a: r.id for r in hpo.itertuples() for a in json.loads(r.alt_ids)}
    labels = dict(zip(hpo.id, hpo.label))
    defs = dict(zip(hpo.id, hpo.definition))
    anc, parents = hpo_ancestors(hpo)
    pa_root = "HP:0000118"  # Phenotypic abnormality
    hp = pd.read_csv(RAW / "hpo/phenotype.hpoa", sep="\t", comment="#", dtype=str).fillna("")
    hp = hp[hp.aspect == "P"]
    hp["hpo_id"] = hp.hpo_id.map(lambda h: alt.get(h, h))
    neg = hp[hp.qualifier == "NOT"]
    hp = hp[hp.qualifier != "NOT"]
    # IC over ALL annotated diseases, with true-path propagation to ancestors
    dis_terms = hp.groupby("database_id").hpo_id.apply(set)
    n_all = len(dis_terms)
    counts = Counter()
    for terms in dis_terms:
        prop = set()
        for t in terms:
            prop |= anc.get(t, {t})
        counts.update(prop)
    ic = {t: -math.log(c / n_all) for t, c in counts.items()}
    # annotations of in-set diseases (OMIM and ORPHA records for the same MONDO id are merged)
    ann = defaultdict(lambda: {"refs": set(), "freq": [], "evid": set(), "onset": set(), "via": set(), "date": ""})
    for r in hp.itertuples(index=False):
        for m in x2m.get(r.database_id, ()):
            a = ann[(m, r.hpo_id)]
            a["refs"].update(x for x in r.reference.split(";") if x)
            if r.frequency:
                a["freq"].append(r.frequency)
            a["evid"].add(r.evidence)
            if r.onset:
                a["onset"].add(r.onset)
            a["via"].add(r.database_id)
            d = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", r.biocuration)
            if d and d.group(1) > a["date"]:
                a["date"] = d.group(1)
    annotated = {t for (_, t) in ann}
    pheno_nodes = set()
    for t in annotated:
        pheno_nodes |= {a for a in anc.get(t, {t}) if pa_root in anc.get(a, ())}
    group_count = Counter()
    by_dis = defaultdict(set)
    for (m, t) in ann:
        by_dis[m].add(t)
    for m, ts in by_dis.items():
        prop = set()
        for t in ts:
            prop |= anc.get(t, {t})
        group_count.update(prop)
    thr = GCFG["informative_ic"]
    for t in sorted(pheno_nodes):
        G.node(t, "Phenotype", labels.get(t, t), "", ic=round(ic.get(t, 0.0), 3),
               informative=ic.get(t, 0.0) >= thr, n_diseases_all=counts.get(t, 0), n_diseases_all_total=n_all,
               n_group_diseases=group_count.get(t, 0), definition=defs.get(t, ""))
    for (m, t), a in sorted(ann.items()):
        fv = [v for v in (freq_value(f) for f in a["freq"]) if v is not None]
        refs = sorted(a["refs"])
        pm = [x for x in refs if x.startswith("PMID:")]
        G.edge(m, t, "has_phenotype", "HPO annotation (phenotype.hpoa)", (pm or refs or sorted(a["via"]))[0],
               a["date"] or retrieved("hpo/phenotype.hpoa"),
               max(EVID_CONF.get(e, 0.7) for e in a["evid"]), "curated",
               references=refs[:15], frequency=a["freq"][:5],
               frequency_value=round(max(fv), 3) if fv else None, evidence_codes=sorted(a["evid"]),
               onset=sorted(a["onset"]), via=sorted(a["via"]), ic=round(ic.get(t, 0.0), 3))
    for t in sorted(pheno_nodes):
        for p in parents.get(t, ()):
            if p in pheno_nodes:
                G.edge(t, p, "subclass_of", "HPO", t, retrieved("hpo/hp.json"), 1.0, "curated")
    # curated NOT annotations: evidence that a phenotype is explicitly absent (used as contradictions)
    excluded = defaultdict(list)
    for r in neg.itertuples(index=False):
        for m in x2m.get(r.database_id, ()):
            if m in in_set:
                excluded[m].append({"hpo_id": r.hpo_id, "label": labels.get(r.hpo_id, r.hpo_id),
                                    "reference": r.reference})
    for m, lst in excluded.items():
        G.node(m, "Disease", "", excluded_phenotypes=lst[:30])
    return len(ann), n_all, len(neg)


# ----------------------------------------------------------------------------- gene -> pathway
def add_pathways(G, T, gene_ids):
    r = pd.read_csv(RAW / "reactome/NCBI2Reactome.txt", sep="\t", header=None, dtype=str,
                    names=["gene", "pw", "url", "name", "evidence", "species"])
    r = r[r.species == "Homo sapiens"].drop_duplicates(["gene", "pw"])
    size = r.groupby("pw").gene.nunique()
    keep = set(size[size <= GCFG["max_pathway_genes"]].index)
    r = r[r.pw.isin(keep)]
    r["gid"] = "NCBIGene:" + r.gene
    r = r[r.gid.isin(gene_ids)].sort_values(["gid", "pw"])
    pws = T["pathways"].set_index("id")
    for p in sorted(set(r.pw)):
        lbl = pws.label.get(p, p).strip() if p in pws.index else p
        G.node(p, "Pathway", lbl, "", n_genes=int(size[p]), parents=json.loads(pws.parents.get(p, "[]")) if p in pws.index else [],
               url=f"https://reactome.org/content/detail/{p}")
    for row in r.itertuples(index=False):
        G.edge(row.gid, row.pw, "in_pathway", "Reactome NCBI2Reactome", row.pw, retrieved("reactome/NCBI2Reactome.txt"),
               0.9 if row.evidence == "TAS" else 0.7, "curated", reactome_evidence=row.evidence,
               pathway_genes=int(size[row.pw]))
    return len(r), len(size) - len(keep)


# ---------------------------------------------------------------------------- papers & mentions
def add_papers(G, T, in_set, gene_ids):
    pap = T["papers"]
    for r in pap.itertuples():
        G.node(f"PMID:{r.pmid}", "Paper", r.title[:300], "|".join(json.loads(r.groups)),
               year=int(r.year) if r.year == r.year and r.year else None, journal=r.journal, doi=r.doi,
               url=f"https://pubmed.ncbi.nlm.nih.gov/{r.pmid}/",
               pub_types=[t for t in json.loads(r.pub_types) if t not in ("Journal Article",)][:8],
               neg=json.loads(r.neg_signal) if r.neg_signal else None)
    year = dict(zip(pap.pmid, pap.year))
    pdz = T["paper_disease"]
    pdz = pdz[pdz.mondo_id.isin(in_set)]
    for r in pdz.itertuples():
        mesh, pt = "MeSH" in r.source, "PubTator" in r.source
        if mesh and pt:
            conf, src = (0.95 if r.major else 0.9), "PubMed MeSH + PubTator3"
        elif mesh:
            conf, src = (0.9 if r.major else 0.8), "PubMed MeSH indexing"
        else:
            conf, src = min(0.85, 0.5 + 0.1 * int(r.count)), "PubTator3 NER"
        G.edge(f"PMID:{r.pmid}", r.mondo_id, "mentions", src, f"PMID:{r.pmid}", year.get(r.pmid) or "", conf,
               "extracted", mesh_major=bool(r.major), pubtator_mentions=int(r.count))
    pg = T["paper_gene"]
    # PubTator genes: keep human disease genes already in the graph + well-supported ones (>=3 papers)
    support = pg.groupby("gene_id").pmid.nunique()
    known = set(T["genes"].id)
    keep_genes = set(gene_ids) | {g for g, n in support.items() if n >= 3 and g in known}
    gl = T["genes"].set_index("id")
    for g in sorted(keep_genes - gene_ids):
        G.node(g, "Gene", gl.symbol[g], "", name=gl.name[g], ncbi=g.split(":")[1] if g.startswith("NCBIGene") else "",
               source="PubTator3 mentions")
    pg = pg[pg.gene_id.isin(keep_genes)]
    for r in pg.itertuples():
        G.edge(f"PMID:{r.pmid}", r.gene_id, "mentions", "PubTator3 NER", f"PMID:{r.pmid}", year.get(r.pmid) or "",
               min(0.85, 0.5 + 0.1 * int(r.count)), "extracted", pubtator_mentions=int(r.count), text_name=r.name)
    return len(pdz), len(pg), keep_genes


# ----------------------------------------------------------------------------------- researchers
COUNTRY_ALIASES = {"usa": "usa", "u s a": "usa", "united states": "usa", "united states of america": "usa", "us": "usa",
                   "uk": "uk", "united kingdom": "uk", "england": "uk", "scotland": "uk", "wales": "uk",
                   "the netherlands": "netherlands", "holland": "netherlands", "p r china": "china",
                   "pr china": "china", "people s republic of china": "china", "republic of korea": "south korea",
                   "korea": "south korea", "deutschland": "germany", "brasil": "brazil", "espana": "spain"}
US_STATES = {"ny", "ca", "ma", "md", "pa", "nc", "oh", "tx", "mn", "il", "wa", "mi", "ga", "fl", "ct", "mo", "tn", "wi",
             "co", "va", "ut", "nj", "or", "az", "ia", "in", "ky", "la", "al", "sc", "ok", "ne", "ks", "ri", "nh", "de"}


def country_of(aff: str) -> str:
    if not aff:
        return ""
    aff = re.sub(r"\S+@\S+", "", aff)
    aff = re.sub(r"electronic address.*", "", aff, flags=re.I)
    last = norm(ascii_fold(aff.rsplit(",", 1)[-1]))
    last = re.sub(r"\d+", "", last).strip()
    if last in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[last]
    toks = last.split()
    if toks and toks[-1] in ("usa",):
        return "usa"
    if toks and toks[0] in US_STATES and len(toks) <= 2:
        return "usa"
    if 2 < len(last) <= 20 and len(toks) <= 3:
        return last
    return ""


def _forename(fore: str) -> str:
    """First full given name ('Teruyuki' from 'Teruyuki K'), '' when only initials are available."""
    tok = re.sub(r"[^a-z\- ]", "", ascii_fold(fore or "").lower()).replace("-", " ").split()
    return tok[0] if tok and len(tok[0]) >= 3 else ""


def researcher_keys(pap):
    """Disambiguate PubMed authors -> stable researcher ids.

    ORCID wins. Otherwise last|first-initial|country, split further by full forename when one name key covers
    several different forenames (e.g. Takashi vs Teruyuki Kobayashi). Initials-only records in such a split
    bucket stay on a separate '|?' key flagged homonym_risk, so they are never silently merged into a person.
    """
    recs = defaultdict(list)  # (last, init) -> [(pmid, fore, orcid, aff, country, year)]
    for pmid, authors, yr in zip(pap.pmid, pap.authors, pap.year):
        for last, fore, initials, aff, orcid in json.loads(authors):
            ln = re.sub(r"[^a-z]", "", ascii_fold(last).lower())
            ini = (ascii_fold(initials or fore)[:1] or "").lower()
            if not ln or not ini:
                continue
            orcid = re.sub(r"^https?://orcid.org/", "", orcid or "").strip()
            recs[(ln, ini)].append((pmid, fore, orcid, aff, country_of(aff), yr))
    assign = {}  # (pmid, ln, ini) -> key
    meta = defaultdict(lambda: {"fores": Counter(), "affs": [], "orcid": "", "countries": Counter(),
                                "homonym_risk": False})
    for (ln, ini), lst in recs.items():
        orcids = {r[2] for r in lst if r[2]}
        orcid_fn = {}
        orcid_country = {}
        for r in lst:
            if r[2]:
                orcid_fn.setdefault(r[2], _forename(r[1]))
                if r[4]:
                    orcid_country.setdefault(r[2], r[4])
        cc = Counter(r[4] for r in lst if r[4] and not r[2])
        major = [c for c, n in cc.items() if n >= 2]
        top = cc.most_common(1)[0][0] if cc else ""
        # bucket by country first
        buckets = defaultdict(list)
        for rec in lst:
            pmid, fore, orcid, aff, country, yr = rec
            if orcid:
                buckets[("orcid", orcid)].append(rec)
                continue
            if len(orcids) == 1:
                o = next(iter(orcids))
                fn = _forename(fore)
                if (not country or orcid_country.get(o, country) == country) and (not fn or fn == orcid_fn.get(o) or
                                                                                   not orcid_fn.get(o)):
                    buckets[("orcid", o)].append(rec)
                    continue
            c = country if country in major else (top if (top in major or not major) else "")
            buckets[("name", c)].append(rec)
        for (kind, val), brecs in buckets.items():
            if kind == "orcid":
                keys = {id(r): f"AUTH:{val}" for r in brecs}
                risk = False
            else:
                fns = Counter(_forename(r[1]) for r in brecs if _forename(r[1]))
                split = len(fns) >= 2
                keys = {}
                for r in brecs:
                    fn = _forename(r[1])
                    suffix = (f"|{fn}" if fn else "|?") if split else ""
                    keys[id(r)] = f"AUTH:{ln}|{ini}|{val}{suffix}"
                risk = split
            for r in brecs:
                pmid, fore, orcid, aff, country, yr = r
                key = keys[id(r)]
                assign[(pmid, ln, ini)] = key
                m = meta[key]
                m["fores"][fore] += 1
                if aff:
                    m["affs"].append((yr or 0, aff))
                if kind == "orcid":
                    m["orcid"] = val
                if country:
                    m["countries"][country] += 1
                m["ln"], m["ini"] = ln, ini
                m["homonym_risk"] = m["homonym_risk"] or (risk and key.endswith("|?"))
    return recs, assign, meta


def add_researchers(G, T):
    pap = T["papers"]
    recs, assign, meta = researcher_keys(pap)
    papers_of = defaultdict(set)
    for (pmid, ln, ini), key in assign.items():
        papers_of[key].add(pmid)
    keep = {k for k, v in papers_of.items() if len(v) >= 2}
    year = dict(zip(pap.pmid, pap.year))
    lastname, affil = {}, {}
    for pmid, authors in zip(pap.pmid, pap.authors):
        for last, fore, initials, aff, orcid in json.loads(authors):
            ln = re.sub(r"[^a-z]", "", ascii_fold(last).lower())
            lastname.setdefault(ln, last)
            ini = (ascii_fold(initials or fore)[:1] or "").lower()
            if aff:
                affil[(pmid, ln, ini)] = aff[:160]
    for key in sorted(keep):
        m = meta[key]
        fore = max(m["fores"], key=lambda f: (bool(_forename(f)), m["fores"][f], len(f))) if m["fores"] else ""
        affs = sorted(m["affs"], key=lambda x: -(x[0] or 0))
        how = "orcid" if m["orcid"] else ("name+initial+country (initials only; homonyms likely)" if m["homonym_risk"]
                                          else "name+initial+country")
        G.node(key, "Researcher", f"{fore} {lastname.get(m['ln'], m['ln'].title())}".strip(), "",
               orcid=m["orcid"], affiliation=affs[0][1] if affs else "",
               country=m["countries"].most_common(1)[0][0] if m["countries"] else "",
               n_papers=len(papers_of[key]), name_key=f"{m['ln']}|{m['ini']}",
               disambiguation=how, homonym_risk=bool(m["homonym_risk"]))
    n = 0
    for (pmid, ln, ini), key in sorted(assign.items(), key=lambda x: (x[1], int(x[0][0]))):
        if key in keep:
            m = meta[key]
            G.edge(key, f"PMID:{pmid}", "authored", "PubMed author list", f"PMID:{pmid}", year.get(pmid) or "",
                   0.95 if m["orcid"] else (0.5 if m["homonym_risk"] else 0.75), "extracted",
                   disambiguation="orcid" if m["orcid"] else "name+initial+country",
                   affiliation=affil.get((pmid, ln, ini), ""))
            n += 1
    by_name = defaultdict(list)
    for key in keep:
        by_name[(meta[key]["ln"], meta[key]["ini"])].append(key)
    return len(keep), n, by_name, meta


# ------------------------------------------------------------------------------------------ trials
ORG_INCLUDE = re.compile(r"foundation|society|association|alliance|network|fund\b|hope|battle|connect|parents|famil|"
                         r"support|coalition|charity|trust fund|advocacy", re.I)
ORG_EXCLUDE = re.compile(r"nhs|trust$|hospital|universit|institut|fundaci|fondazione|irccs|clinic|science foundation|"
                         r"research foundation for|dairy|society of|competence network|eurobloodnet|kidney association|"
                         r"national science|schizophrenia|eye center|medical|health network|cancer|oncolog|"
                         r"health system|ministry|council|gynaec", re.I)
BAD_OFFICIAL = re.compile(r"\d|xx|director|monitor|clinical|trial|sponsor|medical|study|call|center|centre|"
                          r"information|contact|department|global|lead|program|team|research", re.I)


def disease_matcher(T, x2m):
    dis = T["diseases"]
    exact = defaultdict(set)
    for r in dis.itertuples():
        for t in [r.label] + json.loads(r.synonyms):
            n = norm(t)
            if len(n) >= 3:
                exact[n].add(r.id)
    mesh = dict(zip(T["mesh_names"].name_norm, T["mesh_names"].ui))
    long_syn = sorted([(n, ids) for n, ids in exact.items() if len(n) >= 6], key=lambda x: -len(x[0]))

    def match(cond: str, is_mesh: bool):
        n = norm(cond)
        if n in exact:
            return exact[n], "exact synonym", 0.95
        if is_mesh and n in mesh:
            ms = x2m.get(f"MESH:{mesh[n]}", ())
            if ms:
                return set(ms), "MeSH condition", 0.9
        padded = f" {n} "
        for syn, ids in long_syn:
            if f" {syn} " in padded:
                return ids, f"contains '{syn}'", 0.75
        return set(), "", 0
    return match


def add_trials(G, T, x2m, by_name, rmeta, seed_by_norm):
    match = disease_matcher(T, x2m)
    studies = [json.loads(l) for l in open(RAW / "ctgov/studies.jsonl")]
    pm_in = set(T["papers"].pmid)
    n_tr, n_link, n_cite, n_off, n_org_edges, orgs_new = 0, 0, 0, 0, 0, 0
    for s in sorted(studies, key=lambda s: s["nct"]):
        links = {}
        for cond, is_mesh in [(c, False) for c in s.get("conditions", [])] + [(c, True) for c in s.get("mesh_conditions", [])]:
            ids, how, conf = match(cond, is_mesh)
            for m in ids:
                if m not in links or links[m][2] < conf:
                    links[m] = (cond, how, conf)
        if not links:
            continue
        n_tr += 1
        nct = s["nct"]
        text = f"{s.get('title', '')} {s.get('official_title', '')} {s.get('summary', '')}".lower()
        if s.get("patient_registry") or "registry" in (s.get("title") or "").lower():
            kind = "registry"
        elif "natural history" in text or (s.get("study_type") == "OBSERVATIONAL" and s.get("observational_model") == "COHORT"):
            kind = "natural_history"
        elif s.get("study_type") == "OBSERVATIONAL":
            kind = "observational"
        else:
            kind = "trial"
        sp = s.get("lead_sponsor") or {}
        G.node(nct, "Trial", s.get("title") or nct, "|".join(s.get("groups", [])), kind=kind, status=s.get("status"),
               phases=s.get("phases") or [], study_type=s.get("study_type"), start=s.get("start"),
               completion=s.get("completion"), enrollment=s.get("enrollment"), sponsor=sp.get("name"),
               sponsor_class=sp.get("class"), countries=s.get("countries") or [],
               interventions=[i.get("name") for i in s.get("interventions") or []][:10],
               conditions=(s.get("conditions") or [])[:25], n_conditions=len(s.get("conditions") or []),
               url=f"https://clinicaltrials.gov/study/{nct}",
               observational_model=s.get("observational_model"), why_stopped=s.get("why_stopped") or "",
               has_results=bool(s.get("has_results")), last_update=s.get("last_update"))
        for m, (cond, how, conf) in sorted(links.items()):
            G.edge(nct, m, "studies", "ClinicalTrials.gov", nct, s.get("start") or "", conf, "curated",
                   condition=cond, matched_by=how, kind=kind, status=s.get("status"))
            n_link += 1
        for pm in s.get("references") or []:
            if str(pm) in pm_in:
                G.edge(nct, f"PMID:{pm}", "cites", "ClinicalTrials.gov references", nct, s.get("start") or "", 0.9,
                       "curated")
                n_cite += 1
        # investigators
        for o in s.get("officials") or []:
            raw = (o.get("name") or "").split(",")[0].strip()
            toks = ascii_fold(raw).replace(".", " ").split()
            if len(toks) < 2 or BAD_OFFICIAL.search(raw):
                continue
            ln = re.sub(r"[^a-z]", "", toks[-1].lower())
            ini = toks[0][0].lower()
            aff = o.get("affiliation") or ""
            cands = by_name.get((ln, ini), [])
            key, how, conf = None, "", 0.0
            aff_words = {w for w in norm(ascii_fold(aff)).split() if len(w) > 3} - {"university", "hospital", "medical",
                                                                                  "center", "centre", "institute"}
            hits = [c for c in cands if aff_words & set(norm(ascii_fold(G.nodes[c]["attrs"].get("affiliation", ""))).split())]
            if len(hits) == 1:
                key, how, conf = hits[0], "name + affiliation match to PubMed author", 0.85
            elif len(cands) == 1:
                key, how, conf = cands[0], "name match to PubMed author (single candidate)", 0.7
            else:
                key = f"AUTH:ct|{ln}|{ini}|{norm(aff)[:40].replace(' ', '-')}"
                how, conf = "CT.gov official (no PubMed match)", 0.9
                G.node(key, "Researcher", raw, "", orcid="", affiliation=aff, country="", n_papers=0,
                       name_key=f"{ln}|{ini}", disambiguation="ctgov")
            G.edge(key, nct, "investigates", "ClinicalTrials.gov officials", nct, s.get("start") or "", conf, "curated",
                   role=o.get("role"), affiliation=aff, name_as_listed=o.get("name"), identity_match=how)
            n_off += 1
        # patient organisations among sponsors / collaborators
        for role, o in [("lead_sponsor", sp)] + [("collaborator", c) for c in s.get("collaborators") or []]:
            name = (o or {}).get("name") or ""
            if not name:
                continue
            nn = norm(name)
            oid = seed_by_norm.get(nn)
            if oid is None:
                if o.get("class") != "OTHER" or not ORG_INCLUDE.search(name) or ORG_EXCLUDE.search(name):
                    continue
                oid = "ORG:" + nn.replace(" ", "-")[:60]
                if oid not in G.nodes:
                    orgs_new += 1
                G.node(oid, "PatientOrg", name, "", url="", source="ClinicalTrials.gov sponsor/collaborator",
                       verified=False, kind="foundation/society (CT.gov heuristic)")
            G.edge(oid, nct, "runs", "ClinicalTrials.gov", nct, s.get("start") or "", 0.9, "curated", role=role,
                   name_as_listed=name)
            n_org_edges += 1
    return dict(trials=n_tr, studies_edges=n_link, cites=n_cite, investigates=n_off, org_trial_edges=n_org_edges,
                ctgov_orgs=orgs_new, studies_total=len(studies))


def add_seed_orgs(G, in_set):
    path = ROOT / CFG["paths"]["orgs_seed"]
    seed_by_norm, n_nodes, n_edges = {}, 0, 0
    if not path.exists():
        return seed_by_norm, 0, 0
    for r in csv.DictReader(open(path)):
        if str(r.get("verified", "")).lower() != "true":
            continue
        G.node(r["id"], "PatientOrg", r["name"], "", url=r["url"], source=r["source"], verified=True,
               kind="patient organisation (curated seed)")
        n_nodes += 1
        seed_by_norm[norm(r["name"])] = r["id"]
        seed_by_norm[norm(re.sub(r"\(.*?\)", "", r["name"]))] = r["id"]
        date = (re.search(r"\d{4}-\d{2}-\d{2}", r["source"]) or [TODAY])[0]
        for m in r["disease_mondo_ids"].split(";"):
            m = m.strip()
            if m in in_set:
                G.edge(r["id"], m, "serves", "Curated patient-organisation seed (website verified)", r["url"], date,
                       0.9, "curated", note=r["source"])
                n_edges += 1
    return seed_by_norm, n_nodes, n_edges



# ----------------------------------------------------------------------- NIH RePORTER grants
# NIH "terms" are an auto-generated concept list (often broad), so they rate below a title/abstract mention
GRANT_FIELD_CONF = {"title": 0.9, "abstract": 0.7, "terms": 0.6}
AMBIGUOUS_GENES = {"APOE", "GRN", "GNE", "TRIM37", "SCP2", "KCTD7", "CAT"}
TODAY_ISO = time.strftime("%Y-%m-%d")


def disease_text_matcher(T):
    """Free-text -> diseases. Longest synonym wins; a synonym that is the primary label of a disease points only to that
    disease (so 'Krabbe disease' does not fan out to every subtype that lists it as a synonym)."""
    dis = T["diseases"]
    syn, label_of = defaultdict(set), defaultdict(set)
    for r in dis.itertuples():
        for t in [r.label] + json.loads(r.exact_synonyms):
            n = norm(t)
            if len(n) >= 6:
                syn[n].add(r.id)
        label_of[norm(r.label)].add(r.id)
    names = sorted(syn, key=len, reverse=True)

    def ids_for(n):
        return sorted(label_of[n]) if n in label_of else sorted(syn[n])[:3]

    def find(text, exact=False):
        """-> {disease_id: matched_synonym}"""
        n = norm(text)
        if not n:
            return {}
        if exact:
            return {m: n for m in ids_for(n)} if n in syn else {}
        padded = f" {n} "
        hits = [x for x in names if f" {x} " in padded]
        hits = [x for x in hits if not any(x != y and f" {x} " in f" {y} " for y in hits)]
        out = {}
        for x in hits:
            for m in ids_for(x):
                out.setdefault(m, x)
        return out
    return find


def clean_name(n):
    return " ".join((n or "").split())


def match_pi(G, pi, org, by_name):
    """Link an NIH PI to an existing PubMed/CT.gov Researcher node by name + affiliation, else None."""
    name = ascii_fold(clean_name(pi.get("name"))).replace(".", " ")
    toks = [t for t in re.split(r"[\s,]+", name) if t]
    toks = [t for t in toks if t.lower() not in ("jr", "sr", "ii", "iii", "md", "phd")]
    if len(toks) < 2:
        return None, "", 0.0
    ln = re.sub(r"[^a-z]", "", toks[-1].lower())
    ini = toks[0][0].lower()
    cands = by_name.get((ln, ini), [])
    aff_words = {w for w in norm(ascii_fold(org or "")).split() if len(w) > 3} - {"university", "hospital", "medical",
                                                                                  "center", "centre", "institute", "school"}
    hits = [c for c in cands if aff_words & set(norm(ascii_fold(G.nodes[c]["attrs"].get("affiliation", ""))).split())]
    if len(hits) == 1:
        return hits[0], "name + affiliation match to existing researcher", 0.8
    if len(cands) == 1 and G.nodes[cands[0]]["attrs"].get("country") in ("usa", ""):
        return cands[0], "name match to existing researcher (single candidate, affiliation unconfirmed)", 0.6
    return None, "", 0.0


def add_grants(G, T, by_name):
    path = RAW / "reporter/projects.jsonl"
    if not path.exists():
        print("grants: data/raw/reporter/projects.jsonl missing (run scripts/13_reporter.py) - skipped")
        return {}
    rows = [json.loads(l) for l in open(path)]
    find = disease_text_matcher(T)
    # gene symbol -> causal diseases (for grants that name only the gene)
    sym_to_dis = defaultdict(set)
    gl = {r.id: r.symbol for r in T["genes"].itertuples()}
    for e in G.edges:
        if e["rel"] == "causes" and e["src"] in gl:
            sym_to_dis[gl[e["src"]]].add(e["dst"])
    sym_to_dis = {k: v for k, v in sym_to_dis.items() if len(k) >= 4 and k not in AMBIGUOUS_GENES}
    sym_rx = re.compile(r"\b(" + "|".join(sorted(map(re.escape, sym_to_dis), key=len, reverse=True)) + r")\b")
    dparents = defaultdict(set)
    for e in G.edges:
        if e["rel"] == "subclass_of" and e["src"].startswith("MONDO"):
            dparents[e["src"]].add(e["dst"])
    by_core = defaultdict(list)
    for r in rows:
        by_core[r["core"] or r["project_num"]].append(r)
    n_grants = n_funds = n_leads = n_pi_new = n_pi_linked = n_gene_only = 0
    for core in sorted(by_core):
        rs = sorted(by_core[core], key=lambda r: (r["fy"] or 0, r["project_num"]))
        last = rs[-1]
        matches = {}  # disease -> (field, synonym, conf)

        def put(m, field, syn):
            c = GRANT_FIELD_CONF[field]
            if m not in matches or matches[m][2] < c:
                matches[m] = (field, syn, c)
        for r in rs[-3:]:  # latest rows carry the current title/abstract; older rows can differ slightly
            for m, syn in find(r["title"] or "").items():
                put(m, "title", syn)
            for t in r.get("terms") or []:
                for m, syn in find(t, exact=True).items():
                    put(m, "terms", syn)
            for m, syn in find(r.get("abstract") or "").items():
                put(m, "abstract", syn)
        how_gene = None
        if not matches:
            syms = sorted(set(sym_rx.findall(last["title"] or ""))) if sym_to_dis else []  # symbol in the TITLE only
            for sym in syms:
                ds = sym_to_dis[sym]
                for m in ds:
                    if not any(p in ds for p in dparents.get(m, ())):  # most general disease(s) of the gene only
                        matches.setdefault(m, ("gene symbol", sym, 0.6))
            how_gene = bool(matches)
        if not matches:
            continue
        n_grants += 1
        n_gene_only += bool(how_gene)
        fys = sorted({r["fy"] for r in rs if r["fy"]})
        by_fy = defaultdict(float)
        for r in rs:
            by_fy[r["fy"]] += float(r["amount"] or 0)
        end = max((r["end"] for r in rs if r["end"]), default="")
        active = bool(end and end >= TODAY_ISO) or any(r["active"] and (r["fy"] or 0) >= 2026 for r in rs)
        gid = f"GRANT:{core}"
        url = f"https://reporter.nih.gov/project-details/{last['appl_id']}" if last.get("appl_id") else ""
        grp = "|".join(sorted({g for m in matches for g in G.nodes[m]["group"].split("|") if g}))
        G.node(gid, "Grant", last["title"] or core, grp, core_project_num=core, project_nums=sorted({r["project_num"] for r in rs}),
               latest_project_num=last["project_num"], fiscal_years=fys, fy_first=fys[0] if fys else None,
               fy_last=fys[-1] if fys else None, amount_total=round(sum(by_fy.values())),
               amount_by_fy={str(k): round(v) for k, v in sorted(by_fy.items()) if k}, active=active, end_date=end,
               start_date=min((r["start"] for r in rs if r["start"]), default=""), institute=last.get("ic"),
               institute_name=last.get("ic_name"), org=last.get("org"), city=last.get("city"), state=last.get("state"),
               country=last.get("country"), abstract=last.get("abstract") or "", terms=(last.get("terms") or [])[:20],
               pis=[clean_name(p["name"]) for p in last["pis"]], activity=last.get("activity"), url=url,
               source="NIH RePORTER v2")
        fy_txt = f"FY{fys[-1]}" if fys else ""
        for m, (field, syn, conf) in sorted(matches.items()):
            G.edge(gid, m, "funds", "NIH RePORTER", last["project_num"], fys[-1] if fys else "", conf, "extracted",
                   matched_by=f"'{syn}' in project {field}" if field != "gene symbol" else f"gene symbol {syn} in project title",
                   matched_field=field, matched_text=syn, project_num=last["project_num"], core_project_num=core,
                   fiscal_years=fys, award_total=round(sum(by_fy.values())), active=active, url=url, org=last.get("org"),
                   note=f"{last['project_num']} ({fy_txt}); linked to the disease by text match, not by NIH disease coding")
            n_funds += 1
        for pi in last["pis"]:
            if not pi.get("id") or not pi.get("name"):
                continue
            org = f"{last.get('org') or ''}, {last.get('city') or ''}"
            key, how, conf = match_pi(G, pi, last.get("org"), by_name)
            if key:
                n_pi_linked += 1
            else:
                key, how, conf = f"AUTH:nih|{pi['id']}", "NIH RePORTER PI profile id", 0.95
                if key not in G.nodes:
                    n_pi_new += 1
                    cty = (last.get("country") or "").lower().replace("united states", "usa")
                    G.node(key, "Researcher", clean_name(pi["name"]).title(), "", orcid="", affiliation=org.strip(", "), country=cty,
                           n_papers=0, name_key="", disambiguation="nih_profile", nih_profile_id=pi["id"])
            G.edge(key, gid, "leads", "NIH RePORTER", last["project_num"], fys[-1] if fys else "", conf, "curated",
                   contact_pi=bool(pi.get("contact")), identity_match=how, org=last.get("org"), project_num=last["project_num"],
                   fiscal_years=fys)
            n_leads += 1
    return dict(grants=n_grants, funds=n_funds, leads=n_leads, pi_new=n_pi_new, pi_linked=n_pi_linked,
                gene_only=n_gene_only, project_years=len(rows), distinct_projects=len(by_core))


# -------------------------------------------------------------------------------- ClinVar variants
PATHOGENIC = {"Pathogenic", "Likely pathogenic", "Pathogenic/Likely pathogenic"}
BENIGN = {"Benign", "Likely benign", "Benign/Likely benign"}
STARS = {"practice guideline": 4, "reviewed by expert panel": 3, "criteria provided, multiple submitters, no conflicts": 2,
         "criteria provided, single submitter": 1, "criteria provided, conflicting classifications": 1}
VARIANTS_PER_GENE = 50


def add_variants(G, x2m, in_set):
    path = RAW / "clinvar/variants.tsv"
    if not path.exists():
        print("variants: data/raw/clinvar/variants.tsv missing (run scripts/14_clinvar.py) - skipped")
        return {}
    v = pd.read_csv(path, sep="\t", dtype=str, low_memory=False).fillna("")
    v = v.rename(columns={"RS# (dbSNP)": "RSID"})
    v["gid"] = "NCBIGene:" + v.GeneID
    v = v[v.gid.isin(G.nodes)]
    v["cls"] = v.ClinicalSignificance.map(lambda c: "pathogenic" if c in PATHOGENIC else "benign" if c in BENIGN
                                          else "vus" if c == "Uncertain significance"
                                          else "conflicting" if c.startswith("Conflicting") else "other")
    uniq = v.drop_duplicates(["gid", "VariationID"])
    cnt = uniq.groupby(["gid", "cls"]).size().unstack(fill_value=0)
    for gid, r in cnt.iterrows():
        G.node(gid, "Gene", "", n_pathogenic=int(r.get("pathogenic", 0)), n_vus=int(r.get("vus", 0)),
               n_benign=int(r.get("benign", 0)), n_conflicting=int(r.get("conflicting", 0)),
               n_variants_total=int(r.sum()), clinvar_assembly="GRCh38")
    # (variant, gene) -> condition ids of the atlas
    pl = uniq[uniq.cls == "pathogenic"].copy()
    pl["stars"] = pl.ReviewStatus.map(lambda s: STARS.get(s, 0))
    pl = pl[pl.stars >= 1]
    pl["nsub"] = pd.to_numeric(pl.NumberSubmitters, errors="coerce").fillna(0)
    pl = pl.sort_values(["gid", "stars", "nsub", "VariationID"], ascending=[True, False, False, True])
    top = pl.groupby("gid").head(VARIANTS_PER_GENE)
    n_var = n_dis = n_nodis = 0
    for r in top.itertuples():
        vid = f"VAR:{r.VariationID}"
        ids = set()
        for chunk in re.split(r"[|;]", r.PhenotypeIDS):
            for tok in chunk.split(","):
                tok = tok.strip()
                if tok.startswith("MONDO:MONDO:"):
                    ids.add(tok[6:])
                elif tok.startswith("OMIM:"):
                    ids.update(x2m.get(tok, ()))
                elif tok.startswith("Orphanet:"):
                    ids.update(x2m.get("ORPHA:" + tok[9:], ()))
        ids = sorted(i for i in ids if i in in_set)
        conf = {1: 0.7, 2: 0.8, 3: 0.9, 4: 0.95}[r.stars]
        G.node(vid, "Variant", r.Name[:160], G.nodes[r.gid]["group"], variation_id=r.VariationID, hgvs=r.Name,
               gene=G.nodes[r.gid]["label"], variant_type=r.Type, clinical_significance=r.ClinicalSignificance,
               review_status=r.ReviewStatus, stars=r.stars, n_submitters=int(r.nsub), last_evaluated=r.LastEvaluated,
               rsid=(f"rs{r.RSID}" if r.RSID not in ("", "-") else ""), chrom=r.Chromosome,
               pos_grch38=r.PositionVCF, ref=r.ReferenceAlleleVCF, alt=r.AlternateAlleleVCF, conditions=r.PhenotypeList[:300],
               url=f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{r.VariationID}/", source="ClinVar variant_summary")
        n_var += 1
        G.edge(vid, r.gid, "variant_of", "ClinVar", f"VariationID:{r.VariationID}", r.LastEvaluated, conf, "curated",
               clinical_significance=r.ClinicalSignificance, review_status=r.ReviewStatus, stars=r.stars,
               n_submitters=int(r.nsub), url=f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{r.VariationID}/")
        for m in ids:
            G.edge(vid, m, "pathogenic_for", "ClinVar", f"VariationID:{r.VariationID}", r.LastEvaluated, conf, "curated",
                   clinical_significance=r.ClinicalSignificance, review_status=r.ReviewStatus, stars=r.stars,
                   n_submitters=int(r.nsub), condition_ids=r.PhenotypeIDS[:300],
                   url=f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{r.VariationID}/")
            n_dis += 1
        n_nodis += not ids
    return dict(rows=len(v), unique_variants=len(uniq), genes=int(uniq.gid.nunique()), pathogenic_total=int((uniq.cls == "pathogenic").sum()),
                pathogenic_1star_plus=len(pl), variant_nodes=n_var, pathogenic_for_edges=n_dis, no_atlas_disease=n_nodis)


# ------------------------------------------------------------------------ Orphanet epidemiology
def prevalence_summary(prev):
    """One readable line from Orphanet prevalence records: validated + worldwide + point/birth prevalence first."""
    def rank(p):
        return (p.get("validation") != "Validated", p.get("geographic") != "Worldwide",
                not (p.get("type") or "").endswith("prevalence"), not p.get("class"))
    rows = [p for p in prev if p.get("class") or (p.get("val_moy") not in (None, "", "0.0"))]
    if not rows:
        return None
    p = sorted(rows, key=rank)[0]
    value = p["class"] if p.get("class") else f"{p['val_moy']} (reported {p['type'] or 'value'})"
    return f"{value} - {p.get('type') or 'prevalence'}, {p.get('geographic') or 'unspecified region'}" \
           f"{'' if p.get('validation') == 'Validated' else ' (' + str(p.get('validation')) + ')'}"


def add_epidemiology(G, x2m):
    path = RAW / "orphanet/epidemiology.json"
    if not path.exists():
        print("epidemiology: data/raw/orphanet/epidemiology.json missing (run scripts/15_orphanet_epi.py) - skipped")
        return {}
    d = json.loads(path.read_text())
    n = 0
    for orpha, e in sorted(d["disorders"].items()):
        for m in x2m.get(orpha, ()):
            if m not in G.nodes:
                continue
            summ = prevalence_summary(e["prevalence"])
            if not (summ or e["onset"] or e["inheritance"]):
                continue
            # a MONDO disease with several ORPHA codes keeps the first (sorted) non-empty record
            if "epidemiology" in G.nodes[m]["attrs"]:
                continue
            G.node(m, "Disease", "", epidemiology={
                "prevalence": summ, "onset": e["onset"], "inheritance": e["inheritance"],
                "prevalence_records": [{k: p.get(k) for k in ("type", "class", "geographic", "validation", "val_moy")}
                                       for p in e["prevalence"]][:8],
                "source": "Orphanet / Orphadata product 9 (CC-BY-4.0)", "source_id": orpha,
                "source_url": f"https://www.orpha.net/en/disease/detail/{orpha[6:]}", "orphadata_date": d.get("orphadata_date", "")})
            n += 1
    return dict(diseases_with_epidemiology=n)


# --------------------------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    T = load()
    G = Graph()
    x2m = xref_to_mondo(T)
    in_set = add_diseases(G, T)
    gene_ids = add_gene_disease(G, T, x2m)
    gl = T["genes"].set_index("id")
    for g in sorted(gene_ids):
        G.node(g, "Gene", gl.symbol[g], "", name=gl.name[g], ncbi=g.split(":")[1] if g.startswith("NCBIGene") else "",
               source="HPO/Orphanet disease gene")
    n_ann, n_all, n_neg = add_phenotypes(G, T, x2m, in_set)
    print(f"has_phenotype: {n_ann} (IC over {n_all} HPO-annotated diseases; {n_neg} NOT annotations kept as exclusions)")
    n_men_d, n_men_g, all_genes = add_papers(G, T, in_set, gene_ids)
    print(f"mentions: paper->disease {n_men_d}, paper->gene {n_men_g}")
    n_pw, n_drop = add_pathways(G, T, all_genes)
    print(f"in_pathway: {n_pw} (dropped {n_drop} pathways with > {GCFG['max_pathway_genes']} genes)")
    n_res, n_auth, by_name, rmeta = add_researchers(G, T)
    print(f"researchers (>=2 papers): {n_res}, authored edges: {n_auth}")
    seed_by_norm, n_org, n_org_e = add_seed_orgs(G, in_set)
    print(f"seed patient orgs (verified): {n_org}, serves edges: {n_org_e}")
    tr = add_trials(G, T, x2m, by_name, rmeta, seed_by_norm)
    print(f"trials: {tr}")
    epi = add_epidemiology(G, x2m)
    print(f"epidemiology: {epi}")
    var = add_variants(G, x2m, in_set)
    print(f"variants: {var}")
    gr = add_grants(G, T, by_name)
    print(f"grants: {gr}")

    # group of genes = groups of the diseases they are linked to
    dgroup = {n: v["group"] for n, v in G.nodes.items() if v["type"] == "Disease"}
    ggroups = defaultdict(set)
    for e in G.edges:
        if e["rel"] in ("causes", "associated_with"):
            ggroups[e["src"]].update(dgroup.get(e["dst"], "").split("|"))
    for g, gs in ggroups.items():
        G.nodes[g]["group"] = "|".join(sorted(x for x in gs if x))

    for e in G.edges:
        if e["rel"] == "variant_of":
            G.nodes[e["src"]]["group"] = G.nodes[e["dst"]]["group"]
    nodes = pd.DataFrame([{**{k: v for k, v in n.items() if k != "attrs"},
                           "attrs": json.dumps(n["attrs"], default=str)} for n in G.nodes.values()])
    edges = pd.DataFrame(G.edges)
    # drop edges whose endpoints are not nodes (e.g. papers outside the corpus)
    ok = edges.src.isin(nodes.id) & edges.dst.isin(nodes.id)
    if (~ok).sum():
        print(f"dropping {(~ok).sum()} dangling edges")
    edges = edges[ok].reset_index(drop=True)
    edges.insert(0, "id", [f"e{i}" for i in range(len(edges))])
    edges["detail"] = edges.detail.map(lambda d: json.dumps(d, default=str))
    write_parquet(nodes, OUT / "nodes.parquet")
    write_parquet(edges, OUT / "edges.parquet")

    syn = T["synonyms_all"]
    syn = syn[syn.node_id.isin(set(nodes.id))]
    orgs = nodes[nodes.type == "PatientOrg"]
    syn = pd.concat([syn, pd.DataFrame({"text": orgs.label, "node_id": orgs.id, "type": "PatientOrg",
                                        "is_primary": True})], ignore_index=True)
    write_parquet(syn, OUT / "synonyms.parquet")

    print(f"\nnodes: {len(nodes)}")
    print(nodes.type.value_counts().to_string())
    print(f"\nedges: {len(edges)}")
    print(edges.groupby(["rel", "evidence_type"]).size().to_string())
    print(f"\nsynonyms: {len(syn)} by type {syn.type.value_counts().to_dict()}")
    meta = {"built": TODAY, "pubtator": json.loads((OUT / "ontology_meta.json").read_text()).get("pubtator")
            if (OUT / "ontology_meta.json").exists() else "unknown",
            "ctgov_studies_total": tr["studies_total"], "papers_total": int(len(T["papers"])),
            "hpo_diseases_total": n_all, "grants": gr, "variants": var, "epidemiology": epi,
            "ctgov_why_stopped": sum(1 for l in open(RAW / "ctgov/studies.jsonl") if '"why_stopped": "' in l)}
    tmpm = OUT / "graph_meta.json.tmp"
    tmpm.write_text(json.dumps(meta, indent=1))
    tmpm.rename(OUT / "graph_meta.json")
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
