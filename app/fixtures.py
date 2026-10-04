"""Demo data in the exact shape of atlas/api.py, for building and testing the UI offline.

Used when ATLAS_FAKE=1, or when the real atlas raises ImportError / FileNotFoundError /
NotImplementedError (graph not built yet). The UI always shows a "Demo data" badge when any
value on the page came from here (see app/backend.py) -- nothing here is presented as real.

It is a tiny but self-consistent graph, so every function is computed from the same node and
edge tables rather than hand-written per screen: the disease card, neighbours, map, edge
inspector and action plan all agree with each other.

Real: gene, HPO, Reactome, MONDO-style identifiers and the patient organisations (their public
websites). Made up: trial NCT numbers (NCT9xxxxxxx do not exist), PMIDs (99xxxxxx), researcher
names, similarity scores and dates.
"""
from __future__ import annotations

import difflib
import time
from collections import defaultdict

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

KRABBE, MLD, XALD, ZSD, FABRY, ML4 = (
    "MONDO:0009499", "MONDO:0018868", "MONDO:0018544", "MONDO:0019609", "MONDO:0010526", "MONDO:0009653",
)

DISEASES = {
    KRABBE: dict(label="Krabbe disease", group="lysosomal",
                 synonyms=["globoid cell leukodystrophy", "GLD", "galactosylceramide lipidosis",
                           "galactocerebrosidase deficiency"],
                 definition="An inherited disorder in which the enzyme galactocerebrosidase (made from the GALC "
                            "gene) does not work, so a fatty substance builds up and destroys the protective "
                            "myelin around nerves in the brain and body. Most children show signs before 6 months."),
    MLD: dict(label="Metachromatic leukodystrophy", group="lysosomal",
              synonyms=["MLD", "arylsulfatase A deficiency", "sulfatide lipidosis"],
              definition="A lysosomal storage disease caused by a missing arylsulfatase A enzyme (ARSA gene). "
                         "Sulfatides build up and damage myelin, causing loss of movement and thinking skills."),
    XALD: dict(label="X-linked adrenoleukodystrophy", group="peroxisomal",
               synonyms=["X-ALD", "ALD", "adrenoleukodystrophy", "Addison-Schilder disease"],
               definition="A peroxisomal disorder caused by changes in the ABCD1 gene. Very-long-chain fatty acids "
                          "build up, damaging myelin in the brain and the adrenal glands; the childhood cerebral "
                          "form progresses quickly."),
    ZSD: dict(label="Zellweger spectrum disorder", group="peroxisomal",
              synonyms=["Zellweger syndrome", "peroxisome biogenesis disorder", "PBD-ZSD", "cerebrohepatorenal syndrome"],
              definition="A group of conditions in which peroxisomes are not assembled correctly (PEX genes), "
                         "affecting the brain, liver, kidneys, hearing and vision."),
    FABRY: dict(label="Fabry disease", group="lysosomal",
                synonyms=["Anderson-Fabry disease", "alpha-galactosidase A deficiency", "angiokeratoma corporis diffusum"],
                definition="A lysosomal storage disease caused by changes in the GLA gene. A fatty substance builds "
                           "up in blood vessels, kidneys, heart and nerves, causing pain and organ damage."),
    ML4: dict(label="Mucolipidosis type IV", group="lysosomal",
              synonyms=["ML4", "ML IV", "sialolipidosis"],
              definition="A lysosomal disorder caused by changes in MCOLN1, a channel in the lysosome membrane. "
                         "It mainly affects movement, speech and vision."),
}

GENES = {
    "NCBIGene:2581": ("GALC", KRABBE, "ORPHA:487"),
    "NCBIGene:410": ("ARSA", MLD, "ORPHA:512"),
    "NCBIGene:215": ("ABCD1", XALD, "ORPHA:43"),
    "NCBIGene:5189": ("PEX1", ZSD, "ORPHA:912"),
    "NCBIGene:2717": ("GLA", FABRY, "ORPHA:324"),
    "NCBIGene:57192": ("MCOLN1", ML4, "ORPHA:578"),
}

PHENOTYPES = {  # id: (label, information content, synonyms)
    "HP:0002415": ("Leukodystrophy", 5.1, ["white matter disease", "loss of myelin in the brain"]),
    "HP:0011096": ("Peripheral demyelination", 5.6, ["nerve myelin loss"]),
    "HP:0002922": ("Increased CSF protein", 6.2, ["high protein in spinal fluid"]),
    "HP:0000846": ("Adrenal insufficiency", 4.7, ["Addison disease", "adrenal failure"]),
    "HP:0000239": ("Large fontanelles", 4.9, ["wide soft spot"]),
    "HP:0001014": ("Angiokeratoma", 6.0, []),
    "HP:0000966": ("Hypohidrosis", 4.4, ["reduced sweating"]),
    "HP:0007957": ("Corneal opacity", 3.9, ["cloudy cornea"]),
    "HP:0000737": ("Irritability", 3.8, []),
    "HP:0002376": ("Developmental regression", 3.4, ["loss of skills"]),
    "HP:0000546": ("Retinal degeneration", 3.5, []),
    "HP:0000648": ("Optic atrophy", 3.2, []),
    "HP:0001257": ("Spasticity", 2.9, ["stiff muscles"]),
    "HP:0002240": ("Hepatomegaly", 2.8, ["enlarged liver"]),
    "HP:0000093": ("Proteinuria", 2.5, []),
    "HP:0000365": ("Hearing impairment", 2.3, ["hearing loss", "deafness"]),
    "HP:0001250": ("Seizure", 1.6, ["epilepsy", "fits"]),
    "HP:0001252": ("Hypotonia", 1.4, ["floppy baby", "low muscle tone"]),
    "HP:0001263": ("Global developmental delay", 1.2, []),
}

DISEASE_PHENOTYPES = {
    KRABBE: ["HP:0002922", "HP:0011096", "HP:0002415", "HP:0000737", "HP:0002376", "HP:0000648",
             "HP:0001257", "HP:0001250", "HP:0001252"],
    MLD: ["HP:0002922", "HP:0011096", "HP:0002415", "HP:0002376", "HP:0000648", "HP:0001257", "HP:0001250"],
    XALD: ["HP:0002415", "HP:0000846", "HP:0002376", "HP:0000648", "HP:0001257", "HP:0000365", "HP:0001250"],
    ZSD: ["HP:0002415", "HP:0000239", "HP:0002240", "HP:0000365", "HP:0000546", "HP:0001250", "HP:0001252"],
    FABRY: ["HP:0001014", "HP:0000966", "HP:0000093", "HP:0000365"],
    ML4: ["HP:0007957", "HP:0000546", "HP:0001263", "HP:0001252"],
}

PATHWAYS = {  # id: (label, genes)
    "R-HSA-1660662": ("Glycosphingolipid metabolism", ["NCBIGene:2581", "NCBIGene:410", "NCBIGene:2717"]),
    "R-HSA-390247": ("Beta-oxidation of very long chain fatty acids", ["NCBIGene:215"]),
    "R-HSA-390918": ("Peroxisomal lipid metabolism", ["NCBIGene:215", "NCBIGene:5189"]),
    "R-HSA-9033241": ("Peroxisomal protein import", ["NCBIGene:5189"]),
    "R-HSA-983712": ("Ion channel transport", ["NCBIGene:57192"]),
}

TRIALS = {  # id: (label, kind, status, diseases, date)
    "NCT90000001": ("Stem cell transplant for inherited leukodystrophies (demo)", "trial", "Recruiting",
                    [KRABBE, MLD, XALD], "2024-03-01"),
    "NCT90000002": ("Leukodystrophy natural history registry (demo)", "registry", "Enrolling by invitation",
                    [KRABBE, MLD, XALD, ZSD], "2022-09-15"),
    "NCT90000003": ("Newborn screening follow-up study: Krabbe and X-ALD (demo)", "natural_history", "Active",
                    [KRABBE, XALD], "2023-06-10"),
    "NCT90000004": ("Long-term follow-up after HSC gene therapy (demo)", "natural_history", "Recruiting",
                    [MLD, XALD], "2021-11-20"),
    "NCT90000005": ("Enzyme replacement therapy dose study in Fabry disease (demo)", "trial", "Completed",
                    [FABRY], "2019-02-01"),
}

ORGS = {  # real organisations, public homepages
    "ORG:hunters-hope": ("Hunter's Hope Foundation", "https://www.huntershope.org", [KRABBE]),
    "ORG:mld-foundation": ("MLD Foundation", "https://mldfoundation.org", [MLD]),
    "ORG:ald-connect": ("ALD Connect", "https://aldconnect.org", [XALD]),
    "ORG:gfpd": ("Global Foundation for Peroxisomal Disorders", "https://thegfpd.org", [ZSD, XALD]),
    "ORG:ulf": ("United Leukodystrophy Foundation", "https://ulf.org", [KRABBE, MLD, XALD, ZSD]),
    "ORG:fabry-support": ("Fabry Support & Information Group", "https://www.fabry.org", [FABRY]),
    "ORG:ml4": ("ML4 Foundation", "https://www.ml4.org", [ML4]),
}

PAPERS = {  # id: (title, year, diseases, genes)
    "PMID:99000001": ("Outcomes of hematopoietic stem cell transplantation in early-onset Krabbe disease", 2022,
                      [KRABBE], ["NCBIGene:2581"]),
    "PMID:99000002": ("Cerebral adrenoleukodystrophy: timing of transplantation and neurological outcome", 2021,
                      [XALD], ["NCBIGene:215"]),
    "PMID:99000003": ("Newborn screening for Krabbe disease and X-ALD: lessons from a shared pilot", 2023,
                      [KRABBE, XALD], ["NCBIGene:2581", "NCBIGene:215"]),
    "PMID:99000004": ("Myelin loss in lysosomal and peroxisomal leukodystrophies: a comparative MRI study", 2020,
                      [KRABBE, XALD, MLD], []),
    "PMID:99000005": ("Lentiviral gene therapy for metachromatic leukodystrophy: long-term follow-up", 2022,
                      [MLD], ["NCBIGene:410"]),
    "PMID:99000006": ("Galactosylceramide and sulfatide metabolism share a degradation route", 2019,
                      [KRABBE, MLD], ["NCBIGene:2581", "NCBIGene:410"]),
    "PMID:99000007": ("Peripheral neuropathy is uncommon in childhood cerebral X-ALD", 2018,
                      [XALD], ["NCBIGene:215"]),
    "PMID:99000008": ("Hearing and vision outcomes in Zellweger spectrum disorder", 2021,
                      [ZSD], ["NCBIGene:5189"]),
    "PMID:99000009": ("Very-long-chain fatty acids in peroxisomal disorders", 2020,
                      [XALD, ZSD], ["NCBIGene:215", "NCBIGene:5189"]),
    "PMID:99000010": ("Pain and kidney outcomes under enzyme replacement in Fabry disease", 2019,
                      [FABRY], ["NCBIGene:2717"]),
    "PMID:99000011": ("Natural history of mucolipidosis type IV", 2017,
                      [ML4], ["NCBIGene:57192"]),
}

RESEARCHERS = {  # fictional people
    "AUTH:demo-hartmann": ("Dr. Lena Hartmann (demo)", "Example Children's Hospital, Neurogenetics",
                           ["PMID:99000001", "PMID:99000003", "PMID:99000004", "PMID:99000002"]),
    "AUTH:demo-okafor": ("Dr. Samuel Okafor (demo)", "Example University, Bone Marrow Transplant Program",
                         ["PMID:99000001", "PMID:99000005", "PMID:99000006"]),
    "AUTH:demo-lindqvist": ("Dr. Maja Lindqvist (demo)", "Example Institute for Metabolic Disease",
                            ["PMID:99000008", "PMID:99000009", "PMID:99000007"]),
}

CLUSTERS = {
    "C1": ("Leukodystrophies: myelin loss in childhood", [KRABBE, MLD, XALD, ZSD]),
    "C2": ("Lysosomal multi-organ storage", [FABRY, ML4]),
}

# Inferred disease~disease similarity (score, contradiction note or None)
SIMILAR = [
    (KRABBE, MLD, 0.71, None),
    (KRABBE, XALD, 0.48,
     ("PMID:99000007", "Peripheral nerve damage is common in Krabbe but uncommon in childhood cerebral X-ALD, "
                       "so the shared-phenotype link is weaker outside the brain.")),
    (MLD, XALD, 0.45, None),
    (XALD, ZSD, 0.52, None),
    (KRABBE, ZSD, 0.31, None),
    (KRABBE, FABRY, 0.19, None),
    (FABRY, ML4, 0.22, None),
]

GROUP_LABEL = {"lysosomal": "Lysosomal storage diseases", "peroxisomal": "Peroxisomal diseases"}

# ---------------------------------------------------------------------------
# Build the node and edge tables once
# ---------------------------------------------------------------------------

NODES: dict[str, dict] = {}
EDGES: dict[str, dict] = {}


def _node(nid, ntype, label, group=None, **attrs):
    NODES[nid] = {"id": nid, "type": ntype, "label": label, "group": group, **attrs}


def _edge(src, dst, rel, source, source_id, date, confidence, evidence_type, **detail):
    eid = f"e{len(EDGES) + 1}"
    EDGES[eid] = {"id": eid, "src": src, "dst": dst, "rel": rel, "source": source, "source_id": source_id,
                  "date": date, "confidence": confidence, "evidence_type": evidence_type, "detail": detail}
    return eid


def _build():
    for did, d in DISEASES.items():
        _node(did, "Disease", d["label"], d["group"], synonyms=d["synonyms"])
    for gid, (sym, did, orpha) in GENES.items():
        _node(gid, "Gene", sym, DISEASES[did]["group"], synonyms=[])
        _edge(gid, did, "causes", "Orphanet", orpha, "2025-06-30", 0.98, "curated")
    for pid, (label, ic, syns) in PHENOTYPES.items():
        _node(pid, "Phenotype", label, None, synonyms=syns, ic=ic)
    for did, pids in DISEASE_PHENOTYPES.items():
        for pid in pids:
            _edge(did, pid, "has_phenotype", "HPO annotations", "phenotype.hpoa", "2025-09-01", 0.95, "curated",
                  frequency="frequent" if PHENOTYPES[pid][1] > 3 else "occasional")
    for rid, (label, genes) in PATHWAYS.items():
        _node(rid, "Pathway", label, None, synonyms=[])
        for gid in genes:
            _edge(gid, rid, "in_pathway", "Reactome", "NCBI2Reactome.txt", "2025-06-12", 0.97, "curated")
    for tid, (label, kind, status, dids, date) in TRIALS.items():
        _node(tid, "Trial", label, None, kind=kind, status=status, synonyms=[])
        for did in dids:
            _edge(tid, did, "studies", "ClinicalTrials.gov", tid, date, 0.99, "curated")
    for oid, (label, url, dids) in ORGS.items():
        _node(oid, "PatientOrg", label, None, url=url, synonyms=[])
        for did in dids:
            _edge(oid, did, "runs", "Patient group directory (seed list)", oid, "2026-10-01", 0.9, "curated")
    for pmid, (title, year, dids, gids) in PAPERS.items():
        _node(pmid, "Paper", title, None, year=year, synonyms=[])
        for x in dids + gids:
            _edge(pmid, x, "mentions", "PubTator3", pmid, f"{year}-01-01", 0.86, "extracted")
    for aid, (name, aff, papers) in RESEARCHERS.items():
        _node(aid, "Researcher", name, None, affiliation=aff, synonyms=[])
        for pmid in papers:
            _edge(aid, pmid, "authored", "PubMed author list", pmid, f"{PAPERS[pmid][1]}-01-01", 0.93, "extracted")
    for a, b, score, contra in SIMILAR:
        _edge(a, b, "similar_to", "Atlas similarity model (HPO + Reactome + co-mention)", "similarity-v1",
              "2026-10-04", score, "inferred", contradiction=contra)


_build()

OUT: dict[str, list[str]] = defaultdict(list)
IN: dict[str, list[str]] = defaultdict(list)
for _e in EDGES.values():
    OUT[_e["src"]].append(_e["id"])
    IN[_e["dst"]].append(_e["id"])


def _incident(nid):
    return OUT[nid] + IN[nid]


def _other(e, nid):
    return e["dst"] if e["src"] == nid else e["src"]


def _find_edge(src, dst, rel=None):
    for eid in OUT[src]:
        e = EDGES[eid]
        if e["dst"] == dst and (rel is None or e["rel"] == rel):
            return eid
    for eid in OUT[dst]:
        e = EDGES[eid]
        if e["dst"] == src and (rel is None or e["rel"] == rel):
            return eid
    return None


def _need_disease(did):
    if did not in DISEASES:
        raise KeyError(did)


def _papers_for(did):
    return {EDGES[e]["src"] for e in IN[did] if EDGES[e]["rel"] == "mentions"}


def _researchers_for(did):
    papers = _papers_for(did)
    out = {}
    for aid, (_, _, plist) in RESEARCHERS.items():
        n = len(papers.intersection(plist))
        if n:
            out[aid] = n
    return out


# ---------------------------------------------------------------------------
# Contract functions (same names and shapes as atlas/api.py)
# ---------------------------------------------------------------------------


def search(q: str, types: list[str] | None = None, k: int = 10) -> list[dict]:
    q = (q or "").strip().lower()
    if not q:
        return []
    hits = []
    for n in NODES.values():
        if n["type"] not in ("Disease", "Gene", "Phenotype", "Pathway", "PatientOrg"):
            continue
        if types and n["type"] not in types:
            continue
        best, best_text = 0.0, n["label"]
        for text in [n["label"]] + list(n.get("synonyms") or []) + [n["id"]]:
            t = text.lower()
            if t == q:
                s = 1.0
            elif t.startswith(q) or q in t.split():
                s = 0.9
            elif q in t:
                s = 0.8
            else:
                s = difflib.SequenceMatcher(None, q, t).ratio() * 0.75
            if s > best:
                best, best_text = s, text
        if best >= 0.5:
            hits.append({"id": n["id"], "type": n["type"], "label": n["label"], "matched_text": best_text,
                         "score": round(best, 3), "group": n["group"]})
    hits.sort(key=lambda h: (-h["score"], h["label"]))
    return hits[:k]


def disease_card(disease_id: str) -> dict:
    _need_disease(disease_id)
    d = DISEASES[disease_id]
    cid, (clabel, members) = next((c, v) for c, v in CLUSTERS.items() if disease_id in v[1])
    genes = [{"id": g, "label": GENES[g][0], "edge_id": _find_edge(g, disease_id, "causes")}
             for g in GENES if GENES[g][1] == disease_id]
    phen = []
    for pid in DISEASE_PHENOTYPES[disease_id]:
        label, ic, _ = PHENOTYPES[pid]
        phen.append({"id": pid, "label": label, "ic": ic, "informative": ic >= 4.0,
                     "edge_id": _find_edge(disease_id, pid, "has_phenotype")})
    phen.sort(key=lambda p: -p["ic"])
    pathways = []
    for g in genes:
        for rid, (label, glist) in PATHWAYS.items():
            if g["id"] in glist:
                pathways.append({"id": rid, "label": label, "via_gene": g["label"],
                                 "edge_id": _find_edge(g["id"], rid, "in_pathway")})
    trials = [t for t, v in TRIALS.items() if disease_id in v[3]]
    orgs = [{"id": o, "label": v[0], "url": v[1], "edge_id": _find_edge(o, disease_id, "runs")}
            for o, v in ORGS.items() if disease_id in v[2]]
    return {
        "id": disease_id, "label": d["label"], "group": d["group"], "synonyms": d["synonyms"],
        "definition": d["definition"],
        "cluster": {"id": cid, "label": clabel,
                    "members": [{"id": m, "label": DISEASES[m]["label"]} for m in members]},
        "genes": genes, "phenotypes": phen, "pathways": pathways,
        "counts": {"papers": len(_papers_for(disease_id)),
                   "trials": sum(1 for t in trials if TRIALS[t][1] == "trial"),
                   "registries": sum(1 for t in trials if TRIALS[t][1] != "trial"),
                   "researchers": len(_researchers_for(disease_id)), "orgs": len(orgs)},
        "orgs": orgs,
    }


def _disease_pathways(did):
    gids = [g for g in GENES if GENES[g][1] == did]
    return {rid for rid, (_, gl) in PATHWAYS.items() if set(gl) & set(gids)}


def _why(a, b):
    pa, pb = set(DISEASE_PHENOTYPES[a]), set(DISEASE_PHENOTYPES[b])
    shared_p = sorted(pa & pb, key=lambda p: -PHENOTYPES[p][1])
    shared_r = sorted(_disease_pathways(a) & _disease_pathways(b))
    ga = {g for g in GENES if GENES[g][1] == a}
    gb = {g for g in GENES if GENES[g][1] == b}
    ra, rb = _researchers_for(a), _researchers_for(b)
    return {
        "shared_phenotypes": [{"id": p, "label": PHENOTYPES[p][0], "ic": PHENOTYPES[p][1]} for p in shared_p],
        "shared_pathways": [{"id": r, "label": PATHWAYS[r][0]} for r in shared_r],
        "shared_genes": [{"id": g, "label": GENES[g][0]} for g in sorted(ga & gb)],
        "co_mention_papers": len(_papers_for(a) & _papers_for(b)),
        "shared_researchers": len(set(ra) & set(rb)),
        "shared_trials": sum(1 for v in TRIALS.values() if a in v[3] and b in v[3]),
    }


def neighbours(disease_id: str, k: int = 10) -> list[dict]:
    _need_disease(disease_id)
    out = []
    for eid in _incident(disease_id):
        e = EDGES[eid]
        if e["rel"] != "similar_to":
            continue
        other = _other(e, disease_id)
        out.append({"id": other, "label": DISEASES[other]["label"], "group": DISEASES[other]["group"],
                    "score": e["confidence"],
                    "cross_group": DISEASES[other]["group"] != DISEASES[disease_id]["group"],
                    "why": _why(disease_id, other), "edge_id": eid})
    out.sort(key=lambda n: -n["score"])
    return out[:k]


def subgraph(node_id: str, depth: int = 1, max_nodes: int = 60, types: list[str] | None = None) -> dict:
    if node_id not in NODES:
        raise KeyError(node_id)
    keep = {node_id}
    frontier = [node_id]
    for _ in range(max(1, depth)):
        nxt = []
        for nid in frontier:
            # strongest evidence first so truncation drops the weakest links
            for eid in sorted(_incident(nid), key=lambda x: -EDGES[x]["confidence"]):
                other = _other(EDGES[eid], nid)
                if types and NODES[other]["type"] not in types:
                    continue
                if other not in keep and len(keep) < max_nodes:
                    keep.add(other)
                    nxt.append(other)
        frontier = nxt
    edges = [{"id": e["id"], "src": e["src"], "dst": e["dst"], "rel": e["rel"],
              "evidence_type": e["evidence_type"], "confidence": e["confidence"]}
             for e in EDGES.values() if e["src"] in keep and e["dst"] in keep]
    nodes = [{"id": n, "type": NODES[n]["type"], "label": NODES[n]["label"], "group": NODES[n]["group"]}
             for n in keep]
    return {"nodes": nodes, "edges": edges}


def _source_url(e):
    sid = e["source_id"] or ""
    if sid.startswith("ORPHA:"):
        return f"https://www.orpha.net/en/disease/detail/{sid.split(':')[1]}"
    if sid.startswith("NCT"):
        return f"https://clinicaltrials.gov/study/{sid}"
    if sid.startswith("PMID:"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{sid.split(':')[1]}/"
    if e["source"] == "Reactome":
        return f"https://reactome.org/content/detail/{e['dst']}"
    if e["source"] == "HPO annotations":
        return f"https://hpo.jax.org/browse/term/{e['dst']}"
    if e["src"].startswith("ORG:"):
        return ORGS[e["src"]][1]
    return None


_EXPLAIN = {
    "causes": "Changes in the gene {s} are a known cause of {d}. This comes from a curated disease database.",
    "has_phenotype": "{d} is a recorded symptom of {s} in the Human Phenotype Ontology annotations.",
    "in_pathway": "The protein made by {s} works in the biological process '{d}' (Reactome).",
    "studies": "The study '{s}' includes people with {d} (ClinicalTrials.gov record).",
    "runs": "{s} is a patient organisation that supports families affected by {d}.",
    "mentions": "The paper '{s}' mentions {d}; this was found automatically by text mining (PubTator).",
    "authored": "{s} is an author of the paper '{d}'.",
    "similar_to": "The atlas rates {s} and {d} as similar because they share distinctive symptoms, biology "
                  "and research. This is a computed hypothesis, not an established fact.",
}


def edge(edge_id: str) -> dict:
    if edge_id not in EDGES:
        raise KeyError(edge_id)
    e = EDGES[edge_id]
    s, d = NODES[e["src"]], NODES[e["dst"]]
    supporting, contradicting = [], []
    if e["rel"] == "similar_to":
        both = _papers_for(e["src"]) & _papers_for(e["dst"])
        supporting = [{"pmid": p.split(":")[1], "title": PAPERS[p][0], "year": PAPERS[p][1]} for p in sorted(both)]
        if e["detail"].get("contradiction"):
            pmid, note = e["detail"]["contradiction"]
            contradicting = [{"pmid": pmid.split(":")[1], "title": PAPERS[pmid][0], "note": note}]
    elif e["src"].startswith("PMID:"):
        p = e["src"]
        supporting = [{"pmid": p.split(":")[1], "title": PAPERS[p][0], "year": PAPERS[p][1]}]
    elif e["dst"].startswith("PMID:"):
        p = e["dst"]
        supporting = [{"pmid": p.split(":")[1], "title": PAPERS[p][0], "year": PAPERS[p][1]}]
    detail = {k: v for k, v in e["detail"].items() if k != "contradiction"}
    if e["rel"] == "similar_to":
        w = _why(e["src"], e["dst"])
        detail.update({"shared_phenotypes": [p["label"] for p in w["shared_phenotypes"]],
                       "shared_pathways": [p["label"] for p in w["shared_pathways"]],
                       "components": {"phenotype": round(e["confidence"] * 0.62, 2),
                                      "pathway": 0.0 if not w["shared_pathways"] else 0.6,
                                      "literature": round(min(1.0, w["co_mention_papers"] / 3), 2)}})
    return {**e, "src_label": s["label"], "dst_label": d["label"], "source_url": _source_url(e),
            "detail": detail, "supporting_papers": supporting, "contradicting": contradicting,
            "explanation": _EXPLAIN.get(e["rel"], "{s} is linked to {d}.").format(s=s["label"], d=d["label"])}


def _edge_row(eid):
    e = EDGES[eid]
    return {"edge_id": eid, "src_label": NODES[e["src"]]["label"], "rel": e["rel"],
            "dst_label": NODES[e["dst"]]["label"], "evidence_type": e["evidence_type"], "source": e["source"]}


def action_plan(disease_a: str, disease_b: str | None = None) -> dict:
    _need_disease(disease_a)
    if disease_b is None:
        nb = neighbours(disease_a, k=1)
        disease_b = nb[0]["id"] if nb else None
    if disease_b is not None:
        _need_disease(disease_b)
    A = {"id": disease_a, "label": DISEASES[disease_a]["label"]}
    B = {"id": disease_b, "label": DISEASES[disease_b]["label"]} if disease_b else None
    orgs = [{"id": o, "label": v[0], "url": v[1], "disease_id": did}
            for o, v in ORGS.items() for did in v[2] if did in (disease_a, disease_b)]
    seen, orgs_u = set(), []
    for o in orgs:
        if o["id"] not in seen:
            seen.add(o["id"])
            orgs_u.append(o)

    sim = _find_edge(disease_a, disease_b, "similar_to") if B else None
    w = _why(disease_a, disease_b) if B else None
    assets = []
    if B:
        for tid, (label, kind, status, dids, _) in TRIALS.items():
            if disease_a in dids and disease_b in dids:
                assets.append({"id": tid, "label": label, "kind": kind, "status": status,
                               "edge_ids": [_find_edge(tid, disease_a), _find_edge(tid, disease_b)]})
    ra, rb = (_researchers_for(disease_a), _researchers_for(disease_b)) if B else ({}, {})
    researchers = [{"id": r, "label": RESEARCHERS[r][0], "affiliation": RESEARCHERS[r][1],
                    "papers_a": ra[r], "papers_b": rb[r], "orcid": None} for r in ra if r in rb]

    informative = [p for p in (w["shared_phenotypes"] if w else []) if p["ic"] >= 4.0]
    supported = bool(B and sim and informative and (assets or researchers))
    if not supported:
        searched = [f"Disease similarity edges for {A['label']}",
                    "Shared distinctive symptoms (HPO, IC >= 4)", "Shared Reactome pathways",
                    "ClinicalTrials.gov studies listing both diseases", "PubMed authors publishing on both",
                    "Patient group directory"]
        missing = []
        if not B:
            missing.append("No disease is similar enough to compare with.")
        else:
            if not sim:
                missing.append(f"No similarity link between {A['label']} and {B['label']}.")
            if not informative:
                missing.append("No shared distinctive symptom: the only overlap is in common, non-specific symptoms.")
            if not assets:
                missing.append("No study, registry or natural-history effort includes both diseases.")
            if not researchers:
                missing.append("No researcher has published on both diseases.")
        gap = {"searched": searched, "missing": missing,
               "next_question": (f"Is the {GENES[next(g for g in GENES if GENES[g][1] == disease_a)][0]} "
                                 f"defect connected to any process studied in another disease? A literature "
                                 f"review or a cell-model experiment could answer this.")}
        path = [_edge_row(sim)] if sim else []
        return {"a": A, "b": B, "supported": False, "path": path, "shared_assets": assets,
                "researchers": researchers, "orgs": orgs_u, "differences": [], "next_steps": [
                    {"text": "Join or start a natural-history registry for your disease so evidence can build up.",
                     "edge_ids": []}],
                "narrative": "", "gap": gap}

    p0 = informative[0]
    e_pa = _find_edge(disease_a, p0["id"], "has_phenotype")
    e_pb = _find_edge(disease_b, p0["id"], "has_phenotype")
    path = [_edge_row(e_pa), _edge_row(e_pb), _edge_row(sim)]
    if assets:
        path.append(_edge_row(assets[0]["edge_ids"][1]))
    org_b = next((o for o in orgs_u if o["disease_id"] == disease_b), None)
    if org_b:
        path.append(_edge_row(_find_edge(org_b["id"], disease_b, "runs")))

    only_a = [PHENOTYPES[p][0] for p in DISEASE_PHENOTYPES[disease_a]
              if p not in DISEASE_PHENOTYPES[disease_b] and PHENOTYPES[p][1] >= 3.0]
    only_b = [PHENOTYPES[p][0] for p in DISEASE_PHENOTYPES[disease_b]
              if p not in DISEASE_PHENOTYPES[disease_a] and PHENOTYPES[p][1] >= 3.0]
    ga = next(GENES[g][0] for g in GENES if GENES[g][1] == disease_a)
    gb = next(GENES[g][0] for g in GENES if GENES[g][1] == disease_b)
    differences = [f"Different gene: {ga} ({A['label']}) vs {gb} ({B['label']}). Treatments that replace or "
                   f"repair the gene product will not transfer directly."]
    if DISEASES[disease_a]["group"] != DISEASES[disease_b]["group"]:
        differences.append(f"Different disease family: {GROUP_LABEL[DISEASES[disease_a]['group']]} vs "
                           f"{GROUP_LABEL[DISEASES[disease_b]['group']]}. The overlap is in symptoms and care, "
                           f"not the root cause.")
    if only_a:
        differences.append(f"Only in {A['label']}: {', '.join(only_a)}.")
    if only_b:
        differences.append(f"Only in {B['label']}: {', '.join(only_b)}.")
    differences.append("Eligibility: check age at onset and disease stage in each study before assuming families "
                       "from both groups can join.")

    a0 = assets[0] if assets else None
    next_steps = []
    if a0:
        next_steps.append({"text": f"Ask the team behind '{a0['label']}' whether its protocol or data could "
                                   f"include {A['label']} families.", "edge_ids": a0["edge_ids"]})
    if researchers:
        r0 = researchers[0]
        next_steps.append({"text": f"Contact {r0['label']} ({r0['affiliation']}), who has published on both "
                                   f"diseases, to review whether the shared symptom reflects a shared mechanism.",
                           "edge_ids": [sim]})
    if org_b:
        next_steps.append({"text": f"Reach out to {org_b['label']} to compare registries and pool natural-history "
                                   f"data on {p0['label'].lower()}.",
                           "edge_ids": [_find_edge(org_b["id"], disease_b, "runs")]})
    narrative = (
        f"{A['label']} and {B['label']} both show {p0['label'].lower()}, a distinctive symptom "
        f"[{e_pa}] [{e_pb}]. The atlas rates them as similar with score {EDGES[sim]['confidence']:.2f}, "
        f"which is a computed hypothesis [{sim}]."
    )
    if a0:
        narrative += f" An existing {a0['kind'].replace('_', ' ')} already includes both diseases [{a0['edge_ids'][0]}] [{a0['edge_ids'][1]}]."
    if org_b:
        narrative += f" {org_b['label']} supports families with {B['label']} [{_find_edge(org_b['id'], disease_b, 'runs')}]."
    return {"a": A, "b": B, "supported": True, "path": path, "shared_assets": assets,
            "researchers": researchers, "orgs": orgs_u, "differences": differences, "next_steps": next_steps,
            "narrative": narrative, "gap": None}


_REFUSE_WORDS = ("weather", "football", "capital of", "stock", "recipe")


def ask(question: str, disease_id: str | None = None) -> dict:
    q = (question or "").lower()
    if not q.strip() or any(w in q for w in _REFUSE_WORDS):
        return {"answer": "", "refused": True,
                "reason": "The abstracts in the atlas do not cover this question.", "sources": []}
    pool = [p for p in PAPERS if disease_id is None or disease_id in PAPERS[p][2]] or list(PAPERS)
    words = {w for w in q.replace("?", " ").split() if len(w) > 3}
    ranked = sorted(pool, key=lambda p: -len(words & set(PAPERS[p][0].lower().split())))[:3]
    sources = [{"n": i + 1, "pmid": p.split(":")[1], "title": PAPERS[p][0], "year": PAPERS[p][1],
                "journal": "Demo Journal of Rare Disease", "snippet": f"(demo abstract) {PAPERS[p][0]}.",
                "score": round(0.8 - 0.1 * i, 2)} for i, p in enumerate(ranked)]
    answer = (f"Demo answer. In the indexed abstracts, '{sources[0]['title']}' is the most relevant record [1]. "
              f"Related work is described in '{sources[1]['title']}' [2]. This text is generated from demo "
              f"data so the page can be tested without the literature index.")
    return {"answer": answer, "refused": False, "reason": None, "sources": sources}


def ask_stream(question: str, disease_id: str | None = None):
    res = ask(question, disease_id)
    if not res["refused"]:
        for word in res["answer"].split(" "):
            time.sleep(0.005)
            yield {"type": "token", "text": word + " "}
    yield {"type": "final", **res}


def stats() -> dict:
    by_type: dict[str, int] = defaultdict(int)
    for n in NODES.values():
        by_type[n["type"]] += 1
    by_ev: dict[str, int] = defaultdict(int)
    for e in EDGES.values():
        by_ev[e["evidence_type"]] += 1
    groups = {}
    for g in ("lysosomal", "peroxisomal"):
        dids = [d for d, v in DISEASES.items() if v["group"] == g]
        groups[g] = {"diseases": len(dids),
                     "papers": len({p for d in dids for p in _papers_for(d)}),
                     "trials": len({t for t, v in TRIALS.items() if set(v[3]) & set(dids)})}
    return {"nodes_by_type": dict(by_type), "edges_by_evidence": dict(by_ev), "groups": groups,
            "sources": [{"name": "Demo fixture", "retrieved": "2026-10-04", "records": len(EDGES)}],
            "index_ready": False, "llm_ready": False}


# ---------------------------------------------------------------------------
# Round-2 additions, in the shapes the real atlas.api returns (see atlas/api.py DATA lane):
#   disease_card: epidemiology{prevalence,onset[],inheritance[],source,source_url}, funding{n_grants,n_active,gap},
#                 variants{n_pathogenic,n_vus,n_benign,n_genes}
#   action_plan:  readiness{level,reasons[]}, funding{a,b{n_grants,n_active,gap,label,top[]},shared_grants,
#                 shared_pis,gap_a,gap_b,gap_notes}, epidemiology{a,b}
#   edge:         confidence_breakdown{source_reliability,recency,...,overall,formula}, evidence_summary{supporting,
#                 contradicting,caveats,stance}
#   funding / variants / researchers_for / assets_for, and atlas.explain-shaped plain_* (dicts with text, citations,
#   source, checks)
# ---------------------------------------------------------------------------

_edge_base = edge
_plan_base = action_plan
_card_base = disease_card

_EPI = {
    KRABBE: {"prevalence": "1-9 / 1 000 000 - Prevalence at birth, Worldwide", "onset": ["Infancy", "Childhood"],
             "inheritance": ["Autosomal recessive"], "source": "Demo data"},
    XALD: {"prevalence": "1-9 / 100 000 - Prevalence at birth, Europe", "onset": ["Childhood", "Adult"],
           "inheritance": ["X-linked recessive"], "source": "Demo data"},
    MLD: {"prevalence": "1-9 / 100 000 - Prevalence at birth, Worldwide", "onset": ["Infancy", "Adult"],
          "inheritance": ["Autosomal recessive"], "source": "Demo data"},
}


def edge(edge_id: str) -> dict:  # noqa: F811
    e = _edge_base(edge_id)
    n_con = len(e.get("contradicting") or [])
    conf = float(e.get("confidence") or 0)
    stance = "mixed" if n_con else ("supported" if conf >= 0.7 else "weak" if conf >= 0.4 else "unsupported")
    e["evidence_summary"] = {"supporting": len(e.get("supporting_papers") or []), "contradicting": n_con,
                             "caveats": 0, "stance": stance}
    e["confidence_breakdown"] = {"source_reliability": round(min(1, conf + .1), 2), "n_independent_sources": 1,
                                 "recency": 0.9, "corroboration_bonus": 0.0, "penalty": 0.25 if n_con else 0.0,
                                 "overall": conf,
                                 "formula": "overall = source_reliability x recency + corroboration_bonus - penalty"}
    e["contradicting"] = [{**c, "kind": "paper", "weight": 0.5} for c in e.get("contradicting") or []]
    return e


def disease_card(disease_id: str) -> dict:  # noqa: F811
    c = _card_base(disease_id)
    c["epidemiology"] = {**_EPI.get(disease_id, {}), "available": disease_id in _EPI}
    c["funding"] = {"n_grants": 2, "n_active": 1, "gap": False}
    c["variants"] = {"n_pathogenic": 2, "n_vus": 1, "n_benign": 0, "n_genes": 1}
    return c


def _grants(disease_id: str) -> list[dict]:
    label = DISEASES[disease_id]["label"]
    return [{"id": "GRANT:DEMO1", "title": f"(demo) Natural history of {label}", "pi": "Dr. Demo Investigator",
             "org": "Example University", "years": "FY2024-2027", "amount": 450000, "url": None, "active": True,
             "edge_id": None, "disease_id": disease_id},
            {"id": "GRANT:DEMO2", "title": f"(demo) Biomarkers for {label}", "pi": "Dr. Sample Scientist",
             "org": "Example Institute", "years": "FY2021-2023", "amount": 1200000, "url": None, "active": False,
             "edge_id": None, "disease_id": disease_id}]


def action_plan(disease_a: str, disease_b: str | None = None) -> dict:  # noqa: F811
    p = _plan_base(disease_a, disease_b)
    b = (p.get("b") or {}).get("id")
    p["readiness"] = ({"level": "moderate", "reasons": [
        "Shared mechanism and symptoms are curated", "A shared study exists", "No joint registry yet"]}
        if p.get("supported") else {"level": "none", "reasons": ["No supported route found"]})
    p["epidemiology"] = {"a": _EPI.get(disease_a, {}), "b": _EPI.get(b, {}) if b else {}}
    side = lambda d: {"n_grants": 2, "n_active": 1, "gap": False, "label": DISEASES[d]["label"], "top": _grants(d)}  # noqa: E731
    p["funding"] = {"a": side(disease_a), "b": side(b) if b else None, "shared_grants": [], "shared_pis": [],
                    "gap_a": False, "gap_b": False, "gap_notes": []}
    return p


def funding(disease_id: str) -> dict:
    _need_disease(disease_id)
    g = _grants(disease_id)
    return {"grants": g, "total_active": 1, "total": 2, "gap": False, "gap_note": None,
            "broader_class_grants": [], "source": "Demo data"}


def variants(gene_or_disease_id: str, k: int = 25) -> dict:
    _need_disease(gene_or_disease_id)
    gene = next((g[0] for g in GENES.values() if g[1] == gene_or_disease_id), None)
    if not gene:
        return {"id": gene_or_disease_id, "counts": {"total": 0}, "genes": [], "top": [], "has_data": False}
    top = [{"id": f"VAR:{i}", "label": f"{gene} {c}", "hgvs": f"{gene} {c}", "gene": gene, "significance": sig,
            "stars": 2, "type": "single nucleotide variant", "url": None, "conditions": DISEASES[gene_or_disease_id]["label"]}
           for i, (c, sig) in enumerate([("c.1901T>C", "Pathogenic"), ("c.121G>A", "Likely pathogenic"),
                                         ("c.350A>G", "Uncertain significance")])]
    return {"id": gene_or_disease_id, "counts": {"pathogenic": 2, "vus": 1, "benign": 0, "conflicting": 0, "total": 3},
            "genes": [{"id": "g", "label": gene, "n_pathogenic": 2, "n_vus": 1, "n_benign": 0}], "top": top,
            "source": "Demo data", "has_data": True}


def researchers_for(disease_id: str, k: int = 10) -> list[dict]:
    _need_disease(disease_id)
    out = []
    for aid, n in sorted(_researchers_for(disease_id).items(), key=lambda kv: -kv[1])[:k]:
        r = RESEARCHERS[aid]
        out.append({"id": aid, "label": r[0], "affiliation": r[1], "orcid": None, "papers": n, "grants": 0,
                    "trials": 0, "score": round(min(1.0, 0.3 + 0.2 * n), 2),
                    "why": [f"{n} paper(s) on {DISEASES[disease_id]['label']}"], "edge_ids": [],
                    "contact": {"affiliation": r[1], "orcid_url": None, "note": "Demo person; not real."}})
    return out


def assets_for(disease_id: str) -> dict:
    _need_disease(disease_id)
    rows = [{"id": t, "label": v[0], "kind": v[1], "status": v[2], "url": None, "sponsor": "Demo", "stopped": False,
             "why_stopped": ""} for t, v in TRIALS.items() if disease_id in v[3]]
    out = {"disease": {"id": disease_id}, "trials": [], "registries": [], "natural_history": [],
           "observational": [], "models": []}
    for r in rows:
        out["registries" if r["kind"] == "registry" else "natural_history" if r["kind"] == "natural_history"
            else "trials"].append(r)
    return out


def _plain_text(plan: dict) -> str:
    a = plan["a"]["label"]
    b = (plan.get("b") or {}).get("label")
    if plan.get("supported") and b:
        return (f"Short answer: (demo) {a} and {b} share some biology, so studies for {b} may help {a} families.\n"
                "What is uncertain: the differences between the two diseases need a specialist to check.\n"
                "Who can help: a researcher who has published on both diseases.\n"
                "This week: ask a partner group whether they would join a short call.")
    return (f"Short answer: (demo) No supported link for {a} yet; that means unknown, not ruled out.\n"
            "What is uncertain: whether any shared biology exists.\nWho can help: a clinician who sees this disease.\n"
            "This week: share the open question with a patient group.")


def plain_explanation(plan: dict, level: str = "family", use_llm: bool = True) -> dict:
    return {"text": _plain_text(plan), "citations": [], "source": "template", "checks": {"ok": True}}


def plain_explanation_stream(plan: dict, level: str = "family"):
    yield {"type": "final", **plain_explanation(plan, level)}


def plain_disease(card: dict, level: str = "family") -> dict:
    return {"text": f"Overview: (demo) {card['label']} is a rare inherited condition. {card.get('definition') or ''}",
            "citations": [], "source": "template", "checks": {"ok": True}}


def plain_disease_stream(card: dict, level: str = "family"):
    yield {"type": "final", **plain_disease(card, level)}
