#!/usr/bin/env python3
"""Ontology layer: disease set, xref maps, synonym table, paper<->disease/gene mentions.

Inputs  (data/raw): mondo/mondo.json, hpo/hp.json, hpo/genes_to_disease.txt, hpo/genes_to_phenotype.txt,
                    orphanet/en_product6.xml, reactome/ReactomePathways.txt, pubmed/*/abstracts.jsonl,
                    pubtator/annotations.jsonl (or partial pubtator/batches/*.jsonl; optional)
Outputs (data/graph):
  diseases.parquet       id, label, group, bridge, definition, synonyms(json), xrefs(json), parents(json in-set)
  xref_map.parquet       xref (OMIM:/ORPHA:/MESH:/DOID:), mondo_id, in_set
  hpo_terms.parquet      id, label, definition, synonyms(json), parents(json)
  genes.parquet          id (NCBIGene:x | HGNC:x), symbol, name, synonyms(json)
  pathways.parquet       id, label, parents(json)
  papers.parquet         pmid, title, year, journal, doi, groups(json), mesh(json), authors(json)
  paper_disease.parquet  pmid, mondo_id, source (MeSH|PubTator|MeSH+PubTator), major, count
  paper_gene.parquet     pmid, gene_id, name, count
  mesh_names.parquet     name_norm, ui      (MeSH descriptor names seen in the corpus, for trial matching)
  synonyms_all.parquet   text, node_id, type, is_primary   (30_build_graph filters to nodes in the graph)
                         genes: HPO/Orphanet symbols, names, synonyms (PubTator names only feed paper_gene)
Deterministic; prints row counts.
"""
from __future__ import annotations

import glob
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config/atlas.yaml").read_text())
RAW = ROOT / CFG["paths"]["raw"]
OUT = ROOT / CFG["paths"]["graph"]
MONDO_P = "http://purl.obolibrary.org/obo/MONDO_"
HP_P = "http://purl.obolibrary.org/obo/HP_"


def norm(s: str) -> str:
    s = (s or "").lower().replace("'s ", " ").replace("’s ", " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def curie(uri: str) -> str:
    return uri.rsplit("/", 1)[-1].replace("_", ":", 1)


def xref_norm(x: str) -> str:
    x = x.strip()
    if x.startswith("Orphanet:"):
        return "ORPHA:" + x.split(":", 1)[1]
    if x.startswith("MSH:"):
        return "MESH:" + x.split(":", 1)[1]
    return x


# ----------------------------------------------------------------------------------------- MONDO
def load_mondo():
    g = json.load(open(RAW / "mondo/mondo.json"))["graphs"][0]
    nodes = {curie(n["id"]): n for n in g["nodes"] if n["id"].startswith(MONDO_P)}
    parents, children = defaultdict(set), defaultdict(set)
    for e in g["edges"]:
        if e["pred"] == "is_a" and e["sub"].startswith(MONDO_P) and e["obj"].startswith(MONDO_P):
            s, o = curie(e["sub"]), curie(e["obj"])
            parents[s].add(o)
            children[o].add(s)
    return nodes, parents, children


def descendants(root, children):
    seen, stack = set(), [root]
    while stack:
        for c in children.get(stack.pop(), ()):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    return seen


def build_diseases(nodes, parents, children):
    groups = CFG["groups"]
    bridge_roots = CFG.get("bridge_roots", {})
    member = defaultdict(set)
    for gname, g in groups.items():
        root = g["mondo_root"]
        for d in descendants(root, children) | {root}:
            member[d].add(gname)
    bridged = set()
    for bname, root in bridge_roots.items():
        bridged |= descendants(root, children)
    in_set = set(member) | set(bridge_roots.values())
    rows = []
    for did in sorted(in_set):
        n = nodes.get(did, {})
        meta = n.get("meta", {})
        if meta.get("deprecated"):
            continue
        if did in member:
            group = "|".join(sorted(member[did]))
        else:
            group = "bridge"
        syns = sorted({s["val"] for s in meta.get("synonyms", []) if s.get("pred") in
                       ("hasExactSynonym", "hasRelatedSynonym", "hasNarrowSynonym")})
        exact = sorted({s["val"] for s in meta.get("synonyms", []) if s.get("pred") == "hasExactSynonym"})
        xrefs = sorted({xref_norm(x["val"]) for x in meta.get("xrefs", [])})
        # nearest ancestors that are themselves in the set
        near, stack, seen = set(), list(parents.get(did, ())), set()
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            if p in in_set:
                near.add(p)
            else:
                stack.extend(parents.get(p, ()))
        rows.append({
            "id": did, "label": n.get("lbl", did), "group": group,
            "bridge": did in bridged or did in bridge_roots.values(),
            "definition": meta.get("definition", {}).get("val", ""),
            "synonyms": json.dumps(syns), "exact_synonyms": json.dumps(exact),
            "xrefs": json.dumps(xrefs), "parents": json.dumps(sorted(near)),
        })
    return pd.DataFrame(rows)


def build_xref_map(nodes, in_set):
    rows = []
    for did, n in nodes.items():
        meta = n.get("meta", {})
        if meta.get("deprecated"):
            continue
        for x in meta.get("xrefs", []):
            xr = xref_norm(x["val"])
            if xr.split(":")[0] in ("OMIM", "ORPHA", "MESH", "DOID", "UMLS", "GARD"):
                rows.append({"xref": xr, "mondo_id": did, "in_set": did in in_set})
    return pd.DataFrame(rows).drop_duplicates().sort_values(["xref", "mondo_id"]).reset_index(drop=True)


# ------------------------------------------------------------------------------------------- HPO
def build_hpo():
    g = json.load(open(RAW / "hpo/hp.json"))["graphs"][0]
    parents = defaultdict(set)
    for e in g["edges"]:
        if e["pred"] == "is_a" and e["sub"].startswith(HP_P) and e["obj"].startswith(HP_P):
            parents[curie(e["sub"])].add(curie(e["obj"]))
    rows = []
    for n in g["nodes"]:
        if not n["id"].startswith(HP_P) or n.get("type") != "CLASS":
            continue
        meta = n.get("meta", {})
        if meta.get("deprecated"):
            continue
        hid = curie(n["id"])
        syns = sorted({s["val"] for s in meta.get("synonyms", [])})
        alts = [b["val"] for b in meta.get("basicPropertyValues", []) if b["pred"].endswith("hasAlternativeId")]
        rows.append({"id": hid, "label": n.get("lbl", hid), "definition": meta.get("definition", {}).get("val", ""),
                     "synonyms": json.dumps(syns), "parents": json.dumps(sorted(parents.get(hid, ()))),
                     "alt_ids": json.dumps(alts)})
    return pd.DataFrame(rows).sort_values("id").reset_index(drop=True)


# ----------------------------------------------------------------------------------------- genes
def build_genes(pubtator_gene_names):
    sym2id, names = {}, defaultdict(set)
    g2d = pd.read_csv(RAW / "hpo/genes_to_disease.txt", sep="\t", dtype=str)
    g2p = pd.read_csv(RAW / "hpo/genes_to_phenotype.txt", sep="\t", dtype=str, usecols=["ncbi_gene_id", "gene_symbol"])
    for gid, sym in pd.concat([g2d[["ncbi_gene_id", "gene_symbol"]],
                               g2p.assign(ncbi_gene_id="NCBIGene:" + g2p.ncbi_gene_id)]).drop_duplicates().values:
        sym2id[sym] = gid
    full_name = {}
    root = ET.parse(RAW / "orphanet/en_product6.xml").getroot()
    for gene in root.iter("Gene"):
        sym = gene.findtext("Symbol")
        if not sym:
            continue
        gid = sym2id.get(sym)
        if gid is None:
            hgnc = [r.findtext("Reference") for r in gene.iter("ExternalReference") if r.findtext("Source") == "HGNC"]
            gid = f"HGNC:{hgnc[0]}" if hgnc else None
            if gid is None:
                continue
            sym2id[sym] = gid
        full_name[gid] = gene.findtext("Name") or ""
        names[gid].add(full_name[gid])
        for s in gene.iter("Synonym"):
            if s.text:
                names[gid].add(s.text)
    id2sym = {}
    for sym, gid in sym2id.items():
        id2sym.setdefault(gid, sym)
    for gid, nm in pubtator_gene_names.items():
        if gid in id2sym:
            names[gid] |= nm
    rows = [{"id": gid, "symbol": sym, "name": full_name.get(gid, ""),
             "synonyms": json.dumps(sorted(n for n in names.get(gid, ()) if n and n != sym))}
            for gid, sym in sorted(id2sym.items())]
    return pd.DataFrame(rows), sym2id


# -------------------------------------------------------------------------------------- reactome
def build_pathways():
    pw = pd.read_csv(RAW / "reactome/ReactomePathways.txt", sep="\t", header=None, names=["id", "label", "species"])
    pw = pw[pw.species == "Homo sapiens"]
    rel = pd.read_csv(RAW / "reactome/ReactomePathwaysRelation.txt", sep="\t", header=None, names=["parent", "child"])
    rel = rel[rel.child.str.startswith("R-HSA")]
    par = rel.groupby("child").parent.apply(lambda s: json.dumps(sorted(s))).to_dict()
    pw = pw.assign(parents=pw.id.map(par).fillna("[]"))[["id", "label", "parents"]]
    return pw.sort_values("id").reset_index(drop=True)


# ----------------------------------------------------------------------------------- PubMed/Tator
NEG_TITLE = re.compile(r"\bno (significant |statistically significant )?(benefit|effect|improvement|difference|efficacy)|"
                       r"\bfail(ed|ure to)\b|\bnegative (result|trial|study|finding)|\black of (efficacy|benefit|effect|response)|"
                       r"\bineffective|\bfutil|\bnot (effective|beneficial)|\bwithout (clinical )?(benefit|improvement)", re.I)
NEG_ABSTRACT = re.compile(r"did not (significantly )?(improve|reduce|differ|prevent|slow|show)|"
                          r"no (statistically )?significant (difference|benefit|improvement|effect|change)|"
                          r"failed to (improve|show|demonstrate|prevent|slow)|lack of (efficacy|benefit|clinical benefit)|"
                          r"not (clinically )?(effective|beneficial)|ineffective|\bfutil", re.I)


def _neg_signal(title, abstract):
    """Heuristic flag for a possible negative / null finding. Title hit is stronger than abstract hit."""
    m = NEG_TITLE.search(title or "")
    if m:
        return json.dumps({"where": "title", "match": m.group(0)})
    m = NEG_ABSTRACT.search(abstract or "")
    return json.dumps({"where": "abstract", "match": m.group(0)}) if m else ""


def _parse_pubmed(path_group):
    path, group = path_group
    out = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            out.append({
                "pmid": str(r["pmid"]), "title": r.get("title", ""), "year": r.get("year"),
                "journal": r.get("journal", ""), "doi": r.get("doi", ""), "group": group,
                "pub_types": json.dumps(r.get("pub_types") or []),
                "neg_signal": _neg_signal(r.get("title", ""), r.get("abstract", "")),
                "mesh": json.dumps([[m.get("ui"), m.get("term"), bool(m.get("major"))] for m in r.get("mesh", [])]),
                "authors": json.dumps([[a.get("last", ""), a.get("fore", ""), a.get("initials", ""),
                                        (a.get("affiliations") or [""])[0][:300], a.get("orcid", "")]
                                       for a in r.get("authors", [])]),
            })
    return out


def _parse_pubtator(path):
    out = {}
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[str(r["pmid"])] = [(e["type"], str(e["id"]), e.get("name", ""), int(e.get("count", 1)),
                                    tuple(e.get("texts", ())))
                                   for e in r.get("entities", []) if e.get("type") in ("Gene", "Disease")]
    return out


def load_pubtator():
    merged = RAW / "pubtator/annotations.jsonl"
    files = [str(merged)] if merged.exists() else sorted(glob.glob(str(RAW / "pubtator/batches/*.jsonl")))
    ann = {}
    if not files:
        return ann, "absent"
    with ProcessPoolExecutor() as ex:
        for part in ex.map(_parse_pubtator, files, chunksize=8):
            ann.update(part)
    return ann, ("complete" if merged.exists() else f"partial ({len(files)} batches)")


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    nodes, parents, children = load_mondo()
    dis = build_diseases(nodes, parents, children)
    in_set = set(dis.id)
    xmap = build_xref_map(nodes, in_set)
    dis.to_parquet(OUT / "diseases.parquet", index=False)
    xmap.to_parquet(OUT / "xref_map.parquet", index=False)
    print(f"diseases: {len(dis)}  by group: {dis.group.value_counts().to_dict()}  bridge: {int(dis.bridge.sum())}")
    print(f"xref_map: {len(xmap)} rows ({int(xmap.in_set.sum())} into the disease set)")

    hpo = build_hpo()
    hpo.to_parquet(OUT / "hpo_terms.parquet", index=False)
    print(f"hpo_terms: {len(hpo)}")

    # PubMed + PubTator (parallel parse)
    jobs = [(str(p), p.parent.name) for p in sorted((RAW / "pubmed").glob("*/abstracts.jsonl"))]
    with ProcessPoolExecutor() as ex:
        recs = [r for part in ex.map(_parse_pubmed, jobs) for r in part]
    pap = pd.DataFrame(recs)
    groups = pap.groupby("pmid").group.apply(lambda s: json.dumps(sorted(set(s))))
    pap = pap.drop_duplicates("pmid").set_index("pmid").drop(columns="group")
    pap["groups"] = groups
    pap = pap.reset_index().sort_values("pmid", key=lambda s: s.astype(int)).reset_index(drop=True)
    pap.to_parquet(OUT / "papers.parquet", index=False)
    print(f"papers: {len(pap)}")
    ann, pt_status = load_pubtator()
    print(f"pubtator: {len(ann)} annotated papers ({pt_status})")

    # mesh -> mondo (in set)
    x_in = xmap[xmap.in_set]
    mesh2mondo = x_in[x_in.xref.str.startswith("MESH:")].groupby("xref").mondo_id.apply(list).to_dict()
    omim2mondo = x_in[x_in.xref.str.startswith("OMIM:")].groupby("xref").mondo_id.apply(list).to_dict()

    # exact (normalised) disease names for PubTator mention texts whose MeSH id does not map
    syn2mondo = defaultdict(set)
    for r in dis.itertuples():
        for t in [r.label] + json.loads(r.exact_synonyms):
            n = norm(t)
            if len(n) >= 4:
                syn2mondo[n].add(r.id)
    syn2mondo = {k: v for k, v in syn2mondo.items() if len(v) <= 2}

    pd_rows, mesh_names = {}, {}
    pmids = set(pap.pmid)
    for pmid, mesh in zip(pap.pmid, pap.mesh):
        for ui, term, major in json.loads(mesh):
            if term and ui:
                mesh_names[norm(term)] = ui
            for m in mesh2mondo.get(f"MESH:{ui}", ()):
                k = (pmid, m)
                r = pd_rows.setdefault(k, {"pmid": pmid, "mondo_id": m, "mesh": False, "pubtator": False,
                                           "major": False, "count": 0})
                r["mesh"] = True
                r["major"] |= major
    pt_gene_rows, pt_gene_names = [], defaultdict(set)
    for pmid, ents in ann.items():
        if pmid not in pmids:
            continue
        for typ, eid, name, cnt, texts in ents:
            if typ == "Disease":
                ms = set(mesh2mondo.get(eid, ()) if eid.startswith("MESH:") else omim2mondo.get(eid, ()))
                for t in (name,) + texts:
                    ms |= syn2mondo.get(norm(t), set())
                if eid.startswith("MESH:") and name:
                    mesh_names.setdefault(norm(name), eid[5:])
                for m in sorted(ms):
                    k = (pmid, m)
                    r = pd_rows.setdefault(k, {"pmid": pmid, "mondo_id": m, "mesh": False, "pubtator": False,
                                               "major": False, "count": 0})
                    r["pubtator"] = True
                    r["count"] += cnt
            else:
                for gid in eid.split(";"):
                    if gid.isdigit():
                        pt_gene_rows.append({"pmid": pmid, "gene_id": f"NCBIGene:{gid}", "name": name, "count": cnt})
                        if name:
                            pt_gene_names[f"NCBIGene:{gid}"].add(name)
    pdis = pd.DataFrame(list(pd_rows.values()))
    pdis["source"] = pdis.apply(lambda r: "+".join(s for s, f in (("MeSH", r.mesh), ("PubTator", r.pubtator)) if f), axis=1)
    pdis = pdis[["pmid", "mondo_id", "source", "major", "count"]].sort_values(["pmid", "mondo_id"]).reset_index(drop=True)
    pdis.to_parquet(OUT / "paper_disease.parquet", index=False)
    print(f"paper_disease: {len(pdis)}  by source: {pdis.source.value_counts().to_dict()}  "
          f"papers covered: {pdis.pmid.nunique()}  diseases covered: {pdis.mondo_id.nunique()}")
    pgen = pd.DataFrame(pt_gene_rows, columns=["pmid", "gene_id", "name", "count"])
    pgen = pgen.groupby(["pmid", "gene_id"], as_index=False).agg(name=("name", "first"), count=("count", "sum"))
    pgen.to_parquet(OUT / "paper_gene.parquet", index=False)
    print(f"paper_gene: {len(pgen)}")
    pd.DataFrame(sorted(mesh_names.items()), columns=["name_norm", "ui"]).to_parquet(OUT / "mesh_names.parquet", index=False)
    print(f"mesh_names: {len(mesh_names)}")

    # PubTator gene "names" are often mention texts ("Krabbe disease", multi-gene spans): not used as synonyms
    genes, _ = build_genes({})
    genes.to_parquet(OUT / "genes.parquet", index=False)
    print(f"genes: {len(genes)}")
    pws = build_pathways()
    pws.to_parquet(OUT / "pathways.parquet", index=False)
    print(f"pathways (human): {len(pws)}")

    # synonyms
    syn = []
    for r in dis.itertuples():
        syn.append((r.label, r.id, "Disease", True))
        syn += [(s, r.id, "Disease", False) for s in json.loads(r.synonyms)]
    for r in hpo.itertuples():
        syn.append((r.label, r.id, "Phenotype", True))
        syn += [(s, r.id, "Phenotype", False) for s in json.loads(r.synonyms)]
    for r in genes.itertuples():
        syn.append((r.symbol, r.id, "Gene", True))
        if r.name:
            syn.append((r.name, r.id, "Gene", False))
        syn += [(s, r.id, "Gene", False) for s in json.loads(r.synonyms)]
    for r in pws.itertuples():
        syn.append((r.label.strip(), r.id, "Pathway", True))
    sdf = pd.DataFrame(syn, columns=["text", "node_id", "type", "is_primary"])
    sdf = sdf[sdf.text.str.len() > 1].sort_values(["node_id", "is_primary", "text"], ascending=[True, False, True])
    sdf = sdf.drop_duplicates(["text", "node_id"]).reset_index(drop=True)
    sdf.to_parquet(OUT / "synonyms_all.parquet", index=False)
    print(f"synonyms_all: {len(sdf)}  by type: {sdf.type.value_counts().to_dict()}")
    (OUT / "ontology_meta.json").write_text(json.dumps({"pubtator": pt_status, "built": time.strftime("%Y-%m-%d")}))
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
