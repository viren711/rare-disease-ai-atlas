"""Load data/graph/*.parquet once into a NetworkX MultiDiGraph and answer the card / neighbour / edge queries.

Thread-safe lazy singleton: the first caller pays the load (~5-10 s), everyone else reuses it.
Edge rows (with their JSON detail) stay in a pandas frame indexed by edge id; the graph keeps only
(rel, evidence_type, confidence) per edge so traversal is cheap.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import networkx as nx
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config/atlas.yaml").read_text())
GDIR = ROOT / CFG["paths"]["graph"]
INFORMATIVE_IC = float(CFG["graph"]["informative_ic"])
MIN_PAPERS = int(CFG["graph"].get("min_papers_per_disease", 2))
BROAD_TRIAL_CONDITIONS = 10

_LOCK = threading.Lock()
_STATE = None


class State:
    def __init__(self):
        t0 = time.time()
        for f in ("nodes.parquet", "edges.parquet"):
            if not (GDIR / f).exists():
                raise FileNotFoundError(f"{GDIR / f} missing - run scripts/20_ontology.py, 30_build_graph.py, 40_cluster.py")
        nodes = pd.read_parquet(GDIR / "nodes.parquet")
        edges = pd.read_parquet(GDIR / "edges.parquet")
        self.node_type = dict(zip(nodes.id, nodes.type))
        self.node_label = dict(zip(nodes.id, nodes.label))
        self.node_group = dict(zip(nodes.id, nodes.group.fillna("")))
        self._attrs_raw = dict(zip(nodes.id, nodes["attrs"]))
        self.edges = edges.set_index("id", drop=False)
        G = nx.MultiDiGraph()
        G.add_nodes_from(nodes.id)
        G.add_edges_from(zip(edges.src, edges.dst, edges.id,
                             ({"rel": r, "ev": e, "conf": c} for r, e, c in
                              zip(edges.rel, edges.evidence_type, edges.confidence))))
        self.G = G
        cl_path = GDIR / "clusters.parquet"
        self.clusters = pd.read_parquet(cl_path) if cl_path.exists() else pd.DataFrame(
            columns=["disease_id", "cluster_id", "cluster_label"])
        self.cluster_of = {r.disease_id: (r.cluster_id, r.cluster_label) for r in self.clusters.itertuples()}
        self.cluster_members = defaultdict(list)
        for r in self.clusters.itertuples():
            if r.cluster_id:
                self.cluster_members[r.cluster_id].append(r.disease_id)
        # paper -> researchers (for shared-researcher queries)
        au = edges[edges.rel == "authored"]
        self.paper_authors = defaultdict(list)
        for s, d in zip(au.src, au.dst):
            self.paper_authors[d].append(s)
        # pathway relevance: how many atlas disease genes sit in each pathway. A pathway that only one
        # pleiotropic gene reaches (e.g. PSAP -> platelet degranulation) is not a disease mechanism.
        dgenes = set(edges.src[edges.rel.isin(["causes", "associated_with"])])
        pw = edges[(edges.rel == "in_pathway") & edges.src.isin(dgenes)]
        self.pathway_disease_genes = pw.groupby("dst").src.nunique().to_dict()
        self.meta = json.loads((GDIR / "graph_meta.json").read_text()) if (GDIR / "graph_meta.json").exists() else {}
        self.load_seconds = round(time.time() - t0, 2)

    # ------------------------------------------------------------------ helpers
    def attrs(self, nid) -> dict:
        return _parse(self._attrs_raw.get(nid) or "{}")

    def out(self, nid, rel=None):
        """[(dst, edge_id, data)]"""
        return [(v, k, d) for _, v, k, d in self.G.out_edges(nid, keys=True, data=True) if rel is None or d["rel"] == rel]

    def inn(self, nid, rel=None):
        """[(src, edge_id, data)]"""
        return [(u, k, d) for u, _, k, d in self.G.in_edges(nid, keys=True, data=True) if rel is None or d["rel"] == rel]

    @lru_cache(maxsize=4096)
    def family(self, did) -> tuple:
        """The disease plus all its in-set subtypes (subclass_of children, recursively)."""
        out, stack = [did], [did]
        seen = {did}
        while stack:
            x = stack.pop()
            for c, _, _ in self.inn(x, "subclass_of"):
                if c not in seen and self.node_type.get(c) == "Disease":
                    seen.add(c)
                    out.append(c)
                    stack.append(c)
        return tuple(out)

    @lru_cache(maxsize=2048)
    def disease_papers(self, did) -> frozenset:
        return frozenset(p for m in self.family(did) for p, _, _ in self.inn(m, "mentions"))

    @lru_cache(maxsize=2048)
    def disease_researchers(self, did) -> dict:
        c = Counter()
        for p in self.disease_papers(did):
            c.update(set(self.paper_authors.get(p, ())))
        return dict(c)

    @lru_cache(maxsize=2048)
    def disease_trials(self, did) -> dict:
        """trial id -> [studies edge ids] over the disease family."""
        out = defaultdict(list)
        for m in self.family(did):
            for t, eid, _ in self.inn(m, "studies"):
                out[t].append(eid)
        return dict(out)

    @lru_cache(maxsize=2048)
    def phenotype_profile(self, did) -> dict:
        """term -> (edge_id, via_disease). Own annotations; falls back to subtypes for grouping classes."""
        own = {t: (eid, did) for t, eid, _ in self.out(did, "has_phenotype")}
        if own:
            return own
        prof = {}
        for m in self.family(did)[1:]:
            for t, eid, _ in self.out(m, "has_phenotype"):
                prof.setdefault(t, (eid, m))
        return prof

    @lru_cache(maxsize=2048)
    def gene_profile(self, did) -> dict:
        """gene -> (edge_id, via_disease) over the family."""
        prof = {}
        for m in self.family(did):
            for g, eid, d in self.inn(m):
                if d["rel"] in ("causes", "associated_with"):
                    prof.setdefault(g, (eid, m))
        return prof

    @lru_cache(maxsize=2048)
    def disease_ancestors(self, did) -> frozenset:
        out, stack = set(), [did]
        while stack:
            for p, _, _ in self.out(stack.pop(), "subclass_of"):
                if p not in out and self.node_type.get(p) == "Disease":
                    out.add(p)
                    stack.append(p)
        return frozenset(out)

    @lru_cache(maxsize=8192)
    def pheno_ancestors(self, t) -> frozenset:
        out, stack = {t}, [t]
        while stack:
            x = stack.pop()
            for p, _, _ in self.out(x, "subclass_of"):
                if p not in out:
                    out.add(p)
                    stack.append(p)
        return frozenset(out)

    def ic(self, t) -> float:
        return float(self.attrs(t).get("ic", 0.0))


@lru_cache(maxsize=200_000)
def _parse(s: str) -> dict:
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return {}


def state() -> State:
    global _STATE
    if _STATE is None:
        with _LOCK:
            if _STATE is None:
                _STATE = State()
    return _STATE


def reload() -> State:
    global _STATE
    with _LOCK:
        _STATE = State()
    return _STATE


def node(nid: str) -> dict:
    S = state()
    if nid not in S.node_type:
        raise KeyError(nid)
    return {"id": nid, "type": S.node_type[nid], "label": S.node_label[nid], "group": S.node_group[nid],
            "attrs": S.attrs(nid)}


# --------------------------------------------------------------------------------------- card
def _require_disease(S, did):
    if S.node_type.get(did) != "Disease":
        raise KeyError(did)


def disease_card(disease_id: str) -> dict:
    S = state()
    _require_disease(S, disease_id)
    a = S.attrs(disease_id)
    fam = S.family(disease_id)
    cid, clabel = S.cluster_of.get(disease_id, ("", ""))
    cluster = {"id": cid, "label": clabel,
               "members": [{"id": m, "label": S.node_label[m]} for m in S.cluster_members.get(cid, []) if m != disease_id]}
    genes = [{"id": g, "label": S.node_label[g], "edge_id": eid, "via": (None if via == disease_id else S.node_label[via]),
              "rel": S.G.edges[g, via, eid]["rel"], "confidence": S.G.edges[g, via, eid]["conf"]}
             for g, (eid, via) in sorted(S.gene_profile(disease_id).items(), key=lambda x: S.node_label[x[0]])]
    phen = []
    for t, (eid, via) in S.phenotype_profile(disease_id).items():
        ic = S.ic(t)
        phen.append({"id": t, "label": S.node_label.get(t, t), "ic": round(ic, 2), "informative": ic >= INFORMATIVE_IC,
                     "edge_id": eid, "via": None if via == disease_id else S.node_label[via],
                     "frequency": _parse(S.edges.at[eid, "detail"]).get("frequency_value")})
    phen.sort(key=lambda p: (-p["ic"], p["label"]))
    pws, seen = [], set()
    for g in genes:
        for p, peid, _ in S.out(g["id"], "in_pathway"):
            if p in seen:
                continue
            seen.add(p)
            n_genes = S.attrs(p).get("n_genes") or 999
            n_dis = S.pathway_disease_genes.get(p, 0)
            pws.append({"id": p, "label": S.node_label[p], "via_gene": g["label"], "edge_id": peid,
                        "gene_edge_id": g["edge_id"], "n_genes": n_genes, "atlas_genes": n_dis,
                        "relevance": round(n_dis / n_genes, 3)})
    pws.sort(key=lambda p: (-p["relevance"], p["n_genes"], p["label"]))
    # a mechanism needs >= 2 atlas disease genes making up >= 5% of the pathway; the rest stay as other_pathways
    relevant = [p for p in pws if p["atlas_genes"] >= 2 and p["relevance"] >= 0.05] or pws[:3]
    other_pws = [p for p in pws if p not in relevant]
    pws = relevant
    trials = S.disease_trials(disease_id)
    kinds = Counter(S.attrs(t).get("kind") for t in trials)
    orgs = _orgs_for(S, disease_id)
    papers = S.disease_papers(disease_id)
    trial_list = sorted(({"id": t, "label": S.node_label[t], "kind": S.attrs(t).get("kind"),
                          "status": S.attrs(t).get("status"), "start": S.attrs(t).get("start"),
                          "phases": S.attrs(t).get("phases"), "edge_id": eids[0]} for t, eids in trials.items()),
                        key=lambda x: (x["kind"] != "registry", x["kind"] != "natural_history", str(x["start"] or "")),
                        reverse=False)
    return {
        "id": disease_id, "label": S.node_label[disease_id], "group": S.node_group[disease_id],
        "bridge": bool(a.get("bridge")),
        "synonyms": a.get("synonyms", []), "definition": a.get("definition", ""), "xrefs": a.get("xrefs", []),
        "cluster": cluster, "genes": genes, "phenotypes": phen, "pathways": pws,
        "other_pathways": [p for p in other_pws if p not in pws],
        "counts": {"papers": len(papers), "trials": sum(v for k, v in kinds.items() if k != "registry"),
                   "registries": kinds.get("registry", 0), "natural_history": kinds.get("natural_history", 0),
                   "researchers": sum(1 for v in S.disease_researchers(disease_id).values() if v >= MIN_PAPERS),
                   "orgs": len(orgs), "phenotypes": len(phen),
                   "informative_phenotypes": sum(p["informative"] for p in phen)},
        "orgs": orgs, "trials": trial_list[:25],
        "subtypes": [{"id": m, "label": S.node_label[m]} for m in fam[1:]],
        "parents": [{"id": p, "label": S.node_label[p], "edge_id": eid} for p, eid, _ in S.out(disease_id, "subclass_of")],
        "excluded_phenotypes": a.get("excluded_phenotypes", []),
        "epidemiology": epidemiology(S, disease_id),
        "funding": _funding_brief(S, disease_id),
        "variants": _variants_brief(S, disease_id),
    }


def _orgs_for(S, did) -> list[dict]:
    """Patient orgs serving the disease (or a parent/subtype), then orgs running trials on it."""
    out, seen = [], set()
    targets = list(S.family(did)) + [p for p, _, _ in S.out(did, "subclass_of")]
    for m in targets:
        for o, eid, _ in S.inn(m, "serves"):
            if o in seen:
                continue
            seen.add(o)
            out.append({"id": o, "label": S.node_label[o], "url": S.attrs(o).get("url", ""), "edge_id": eid,
                        "disease_id": m, "how": "serves" if m == did else f"serves {S.node_label[m]}"})
    for t, teids in S.disease_trials(did).items():
        if (S.attrs(t).get("n_conditions") or 0) > BROAD_TRIAL_CONDITIONS:
            continue  # broad screening programmes (e.g. 185 conditions) say little about this disease's community
        for o, eid, _ in S.inn(t, "runs"):
            if o in seen:
                continue
            seen.add(o)
            out.append({"id": o, "label": S.node_label[o], "url": S.attrs(o).get("url", ""), "edge_id": eid,
                        "disease_id": did, "how": f"sponsors/collaborates on {t}", "trial_edge_id": teids[0]})
    return out


# --------------------------------------------------------------------------------- neighbours
def _why(S, a, b, detail) -> dict:
    ra, rb = S.disease_researchers(a), S.disease_researchers(b)
    shared_r = sum(1 for r in set(ra) & set(rb) if ra[r] >= MIN_PAPERS and rb[r] >= MIN_PAPERS)
    shared_t = len(set(S.disease_trials(a)) & set(S.disease_trials(b)))
    sp = detail.get("shared_phenotypes", [])
    return {
        "shared_phenotypes": [{"id": p["id"], "label": p["label"], "ic": p["ic"], "informative": p["ic"] >= INFORMATIVE_IC,
                               "edge_ids": [p["edge_a"], p["edge_b"]]} for p in sp],
        "shared_pathways": [{"id": p["id"], "label": p["label"], "edge_ids": p.get("edge_ids", [])}
                            for p in detail.get("shared_pathways", [])],
        "shared_genes": [{"id": g["id"], "label": g["label"], "edge_ids": [g["edge_a"], g["edge_b"]]}
                         for g in detail.get("shared_genes", [])],
        "co_mention_papers": int(detail.get("co_mention_papers", 0)),
        "shared_researchers": shared_r, "shared_trials": shared_t,
        "components": detail.get("components", {}),
    }


def neighbours(disease_id: str, k: int = 10) -> list[dict]:
    S = state()
    _require_disease(S, disease_id)
    rows = []
    for u, v, eid, d in list(S.G.out_edges(disease_id, keys=True, data=True)) + \
            list(S.G.in_edges(disease_id, keys=True, data=True)):
        if d["rel"] != "similar_to":
            continue
        other = v if u == disease_id else u
        # a disease's own subtypes and parent classes are already on its card; they are not discoveries
        if other in S.family(disease_id) or other in S.disease_ancestors(disease_id):
            continue
        rows.append((float(d["conf"]), other, eid))
    rows.sort(key=lambda x: (-x[0], x[1]))
    mygroup = S.node_group[disease_id]
    top = rows[:k]
    # reserve up to two slots for the best cross-group neighbours (bridges between communities)
    cross = [r for r in rows if S.node_group[r[1]] != mygroup]
    missing = [r for r in cross[:2] if r not in top]
    if missing and len(top) >= k:
        top = top[:k - len(missing)] + missing
    else:
        top = top + missing
    out = []
    for score, other, eid in top:
        detail = _parse(S.edges.at[eid, "detail"])
        out.append({"id": other, "label": S.node_label[other], "group": S.node_group[other], "score": round(score, 3),
                    "cross_group": S.node_group[other] != mygroup, "why": _why(S, disease_id, other, detail),
                    "edge_id": eid, "weak": bool(detail.get("below_main_threshold", False))})
    return out


# ---------------------------------------------------------------------------------- subgraph
TYPE_PRIORITY = {"Disease": 0, "Gene": 1, "Pathway": 2, "Phenotype": 3, "Trial": 4, "Grant": 4, "PatientOrg": 5,
                 "Variant": 6, "Researcher": 6, "Paper": 7}


def subgraph(node_id: str, depth: int = 1, max_nodes: int = 60, types: list[str] | None = None) -> dict:
    S = state()
    if node_id not in S.node_type:
        raise KeyError(node_id)
    allowed = set(types) if types else None
    chosen = [node_id]
    chosen_set = {node_id}
    frontier = [node_id]
    used_edges = []
    for _ in range(max(1, depth)):
        cands = []
        for n in frontier:
            for u, v, eid, d in list(S.G.out_edges(n, keys=True, data=True)) + list(S.G.in_edges(n, keys=True, data=True)):
                other = v if u == n else u
                if other in chosen_set:
                    continue
                t = S.node_type[other]
                if allowed and t not in allowed:
                    continue
                if t == "Phenotype":
                    rank = -S.ic(other)
                elif t == "Paper":
                    rank = -(S.attrs(other).get("year") or 0)
                else:
                    rank = -float(d["conf"])
                if d["rel"] == "subclass_of" and t == "Phenotype":
                    rank += 100   # keep the phenotype hierarchy for later
                cands.append((TYPE_PRIORITY.get(t, 9), rank, other, eid))
        cands.sort()
        # balance: at most a share of the budget per type so a disease view isn't 60 papers
        per_type = Counter()
        cap = max(4, (max_nodes - 1) // 4)
        nxt = []
        for _, _, other, eid in cands:
            if len(chosen) >= max_nodes:
                break
            if other in chosen_set:
                continue
            t = S.node_type[other]
            if per_type[t] >= cap and not allowed:
                continue
            per_type[t] += 1
            chosen.append(other)
            chosen_set.add(other)
            nxt.append(other)
        # fill remaining budget ignoring the per-type cap
        for _, _, other, eid in cands:
            if len(chosen) >= max_nodes:
                break
            if other not in chosen_set:
                chosen.append(other)
                chosen_set.add(other)
                nxt.append(other)
        frontier = nxt
        if len(chosen) >= max_nodes:
            break
    sub = S.G.subgraph(chosen_set)
    for u, v, eid, d in sub.edges(keys=True, data=True):
        used_edges.append({"id": eid, "src": u, "dst": v, "rel": d["rel"], "evidence_type": d["ev"],
                           "confidence": float(d["conf"])})
    used_edges.sort(key=lambda e: int(e["id"][1:]))
    if len(used_edges) > 6 * max_nodes:
        used_edges = [e for e in used_edges if node_id in (e["src"], e["dst"])] + \
                     [e for e in used_edges if node_id not in (e["src"], e["dst"])][:5 * max_nodes]
    return {"nodes": [{"id": n, "type": S.node_type[n], "label": S.node_label[n], "group": S.node_group[n]}
                      for n in chosen],
            "edges": used_edges}


# -------------------------------------------------------------------------------------- edge
def source_url(source_id: str, source: str = "") -> str:
    sid = str(source_id or "")
    if sid.startswith("PMID:"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{sid[5:]}/"
    if sid.startswith("NCT"):
        return f"https://clinicaltrials.gov/study/{sid}"
    if sid.startswith("VariationID:"):
        return f"https://www.ncbi.nlm.nih.gov/clinvar/variation/{sid[12:]}/"
    if sid.startswith("OMIM:"):
        return f"https://omim.org/entry/{sid[5:]}"
    if sid.startswith("ORPHA:"):
        return f"https://www.orpha.net/en/disease/detail/{sid[6:]}"
    if sid.startswith("R-HSA"):
        return f"https://reactome.org/content/detail/{sid}"
    if sid.startswith("MONDO:"):
        return f"https://monarchinitiative.org/{sid}"
    if sid.startswith("HP:"):
        return f"https://hpo.jax.org/browse/term/{sid}"
    if sid.startswith("http"):
        return sid
    return ""


def _paper(S, pmid: str) -> dict:
    pid = pmid if pmid.startswith("PMID:") else f"PMID:{pmid}"
    a = S.attrs(pid) if pid in S.node_type else {}
    return {"pmid": pid[5:], "title": S.node_label.get(pid, ""), "year": a.get("year"), "in_corpus": pid in S.node_type}


def edge(edge_id: str) -> dict:
    from atlas import explain
    S = state()
    if edge_id not in S.edges.index:
        raise KeyError(edge_id)
    r = S.edges.loc[edge_id]
    detail = _parse(r["detail"])
    pmids = []
    if str(r["source_id"]).startswith("PMID:"):
        pmids.append(r["source_id"])
    for key in ("pmids", "references", "co_mention_sample"):
        for x in detail.get(key, []) or []:
            x = str(x)
            if x.startswith("PMID:") or x.isdigit():
                pmids.append(x if x.startswith("PMID:") else f"PMID:{x}")
    seen, supporting = set(), []
    for p in pmids:
        if p not in seen:
            seen.add(p)
            supporting.append(_paper(S, p))
    a = assess_edge(S, r, detail, n_supporting_papers=len(supporting))
    url = source_url(r["source_id"], r["source"]) or detail.get("url", "")
    out = {"id": edge_id, "src": r["src"], "dst": r["dst"], "src_label": S.node_label.get(r["src"], r["src"]),
           "dst_label": S.node_label.get(r["dst"], r["dst"]), "src_type": S.node_type.get(r["src"]),
           "dst_type": S.node_type.get(r["dst"]), "rel": r["rel"], "source": r["source"],
           "source_id": r["source_id"], "source_url": url, "date": r["date"],
           "confidence": a["overall"] if r["evidence_type"] != "curated" else float(r["confidence"]),
           "confidence_raw": float(r["confidence"]), "confidence_overall": a["overall"],
           "evidence_type": r["evidence_type"], "detail": detail,
           "supporting_papers": supporting[:20], "contradicting": a["contradicting"],
           "confidence_breakdown": a["breakdown"], "evidence_summary": a["summary"]}
    out["explanation"] = explain.explain_edge(out)
    return out


# ------------------------------------------------------------------ contradiction + confidence handling
# Every contradicting item: {"kind", "weight" (0-1), "label", "pmid", "title", "note", ...}. Kinds:
#   trial_stopped        terminated / withdrawn / suspended trial (CT.gov whyStopped)         weight 0.7 efficacy/safety, 0.25 operational
#   retracted_paper      PubMed publication type Retracted/Retraction                          weight 1.0
#   negative_title       title signals a null / negative result (keyword heuristic)           weight 0.5
#   negative_abstract    abstract signals a null result (weak keyword heuristic)              weight 0.15
#   comment_paper        Comment / Editorial / Letter attached to the claim                    weight 0.2
#   conflicting_status   sources disagree (Orphanet vs HPO gene-disease type / status)       weight 0.6
#   excluded_phenotype   HPO NOT annotation                                                   weight 0.5
#   absence              nothing in the corpus confirms the link (not counter-evidence)       weight 0.3
#   caveat               weaker matching / identity method                                    weight 0.1-0.3
# Items with weight >= COUNTS_AS_CONTRA count in evidence_summary.contradicting; the rest are caveats.
COUNTS_AS_CONTRA = 0.45
STOP_STATUSES = ("TERMINATED", "WITHDRAWN", "SUSPENDED")
EFFICACY_SAFETY = re.compile(r"efficacy|futil|safety|adverse|toxic|no benefit|lack of (effect|benefit|response|efficacy)|"
                             r"ineffective|not effective|death|serious|interim analysis|endpoint|did not (meet|show)", re.I)
BENIGN_STOP = re.compile(r"objective(s)? (was |were )?(achieved|met|reached)|achieved|study completed|completed as planned|"
                         r"business decision|administrative", re.I)
RETRACTED_TYPES = {"Retracted Publication", "Retraction of Publication"}
COMMENT_TYPES = {"Comment", "Editorial", "Published Erratum", "Expression of Concern"}
POSSIBLE = "Possible counter-evidence (automated flag, not a verified finding): "
SOURCE_FAMILIES = ("Orphanet", "HPO", "OMIM", "MONDO", "Reactome", "ClinicalTrials.gov", "PubMed", "PubTator", "MeSH",
                   "NIH RePORTER", "ClinVar", "Curated patient-organisation")


def _c(kind, weight, note, **kw):
    return {"kind": kind, "weight": weight, "pmid": kw.pop("pmid", ""), "title": kw.pop("title", ""), "note": note, **kw}


def _trial_flags(S, nct, edge_id) -> list[dict]:
    at = S.attrs(nct)
    st = (at.get("status") or "").upper()
    if st not in STOP_STATUSES:
        return []
    why = (at.get("why_stopped") or "").strip()
    if why and BENIGN_STOP.search(why):
        w, tag = 0.1, "stopped after its objective was met or for administrative reasons"
    elif why and EFFICACY_SAFETY.search(why):
        w, tag = 0.7, "efficacy/safety-related"
    elif why:
        w, tag = 0.25, "operational (funding, recruitment or sponsor decision)"
    else:
        w, tag = 0.3, "no reason given"
    return [_c("trial_stopped", w,
               f"{POSSIBLE}trial {nct} is {st.lower()}"
               + (f" - reason recorded on ClinicalTrials.gov: \"{why[:240]}\"" if why else " with no reason recorded")
               + f" [{tag}]. A stopped trial does not show the approach failed, but it limits what this link can claim.",
               nct=nct, status=st, why_stopped=why, reason_type=tag, trial_edge_id=edge_id)]


def _paper_flags(S, pids, claim_words=None, cap=4) -> list[dict]:
    """Retracted / comment / negative-signal papers among pids (PMID:xxx). claim_words: lower-case tokens that must
    appear in the paper title (used for treatment-type claims so only on-topic papers are flagged)."""
    out = []
    for pid in pids:
        if pid not in S.node_type:
            continue
        at = S.attrs(pid)
        title = S.node_label.get(pid, "")
        if claim_words and not any(w in title.lower() for w in claim_words):
            continue
        types = set(at.get("pub_types") or [])
        neg = at.get("neg")
        base = dict(pmid=pid[5:], title=title[:200], year=at.get("year"))
        if types & RETRACTED_TYPES:
            out.append(_c("retracted_paper", 1.0, f"{POSSIBLE}this paper is flagged by PubMed as "
                                                  f"{sorted(types & RETRACTED_TYPES)[0].lower()}.", **base))
        elif neg and neg.get("where") == "title":
            out.append(_c("negative_title", 0.5, f"{POSSIBLE}the title contains \"{neg['match']}\", which can signal a null "
                                                 "or negative result. Read the paper before relying on it.", **base))
        elif types & COMMENT_TYPES:
            out.append(_c("comment_paper", 0.2, f"{POSSIBLE}publication type {sorted(types & COMMENT_TYPES)[0]} - a "
                                                "response or correction may qualify the original claim.", **base))
        elif neg:
            out.append(_c("negative_abstract", 0.15, f"{POSSIBLE}abstract wording (\"{neg['match']}\") may signal a null "
                                                     "finding; weak keyword heuristic.", **base))
    out.sort(key=lambda x: (-x["weight"], str(x.get("year") or "")))
    return out[:cap]


def _gene_status_conflict(detail, source) -> list[dict]:
    types = [str(t) for t in detail.get("association_types", [])]
    causal = [t for t in types if t.startswith(("Disease-causing", "Major susceptibility")) or t == "MENDELIAN"]
    non = [t for t in types if t not in causal and t.upper() != "UNKNOWN"]   # UNKNOWN = HPO "type not stated"
    out = []
    if causal and non:
        out.append(_c("conflicting_status", 0.6,
                      f"Sources disagree on the gene-disease relation: {', '.join(sorted(set(causal)))} versus "
                      f"{', '.join(sorted(set(non)))} (Orphanet and HPO use different vocabularies; the weaker label may "
                      "describe a different subtype or a modifier)."))
    if "Not yet assessed" in detail.get("status", []):
        out.append(_c("conflicting_status", 0.5, "Orphanet marks this gene-disease association 'Not yet assessed'."))
    if "HPO" in source and "Orphanet" not in source:
        out.append(_c("caveat", 0.1, "Only the HPO/OMIM route lists this association; Orphanet has no matching record."))
    elif "Orphanet" in source and "HPO" not in source and "OMIM" not in source:
        out.append(_c("caveat", 0.1, "Only Orphanet lists this association; HPO/OMIM has no matching record."))
    return out


def _contradictions(S, r, detail) -> list[dict]:
    """Back-compat wrapper: the list under edge()['contradicting']."""
    return _flags(S, r, detail)


def _flags(S, r, detail) -> list[dict]:
    out = []
    rel = r["rel"]
    if rel == "similar_to":
        co = int(detail.get("co_mention_papers", 0))
        if co == 0:
            out.append(_c("absence", 0.3, "No abstract in the PubMed corpus mentions both diseases: the link rests on "
                                          "ontology annotations only and is not confirmed by literature."))
        for x, y in ((r["src"], r["dst"]), (r["dst"], r["src"])):
            excl = {e["hpo_id"]: e for e in S.attrs(x).get("excluded_phenotypes", [])}
            prof = S.phenotype_profile(y)
            for t in excl:
                if t in prof:
                    ref = excl[t].get("reference", "")
                    out.append(_c("excluded_phenotype", 0.5,
                                  f"HPO records '{excl[t]['label']}' as explicitly ABSENT in {S.node_label[x]} ({ref}) "
                                  f"but present in {S.node_label[y]}.", pmid=ref[5:] if ref.startswith("PMID:") else ""))
        if detail.get("below_main_threshold"):
            out.append(_c("caveat", 0.3, detail.get("note", "Similarity is below the main threshold.")))
        if co:  # papers that mention both diseases but are retracted / negative-signalled
            both = sorted(S.disease_papers(r["src"]) & S.disease_papers(r["dst"]))
            out += _paper_flags(S, [p for p in both if S.attrs(p).get("neg") or S.attrs(p).get("pub_types")], cap=3)
    elif rel == "has_phenotype":
        excl = {e["hpo_id"]: e for e in S.attrs(r["src"]).get("excluded_phenotypes", [])}
        if r["dst"] in excl:
            ref = excl[r["dst"]].get("reference", "")
            out.append(_c("excluded_phenotype", 0.5, f"Another HPO record marks this phenotype as NOT present ({ref}); "
                                                     "annotations disagree (often a subtype difference).",
                          pmid=ref[5:] if ref.startswith("PMID:") else ""))
    elif rel in ("causes", "associated_with"):
        out += _gene_status_conflict(detail, r["source"])
        pm = [f"PMID:{x}" for x in detail.get("pmids", []) or []]
        out += _paper_flags(S, pm, cap=2)
    elif rel == "mentions" and r["src"].startswith("PMID:"):
        if r["evidence_type"] == "extracted" and "MeSH" not in r["source"] and int(detail.get("pubtator_mentions", 0)) <= 1:
            out.append(_c("caveat", 0.2, "Single automated text-mining hit; not confirmed by MeSH indexing."))
        out += [f for f in _paper_flags(S, [r["src"]], cap=1) if f["kind"] == "retracted_paper"]  # keyword flags are only for treatment claims
    elif rel == "authored" and detail.get("disambiguation") != "orcid":
        out.append(_c("caveat", 0.2, "Author identity resolved by name+initial+country (no ORCID); homonyms are possible."))
    elif rel == "studies":
        nct = r["src"]
        out += _trial_flags(S, nct, r["id"])
        # papers on this disease that mention one of the trial's interventions in the title and carry a null signal
        words = {w for iv in (S.attrs(nct).get("interventions") or []) for w in re.findall(r"[a-z][a-z\-]{5,}", str(iv).lower())}
        words -= {"placebo", "treatment", "therapy", "standard", "disease", "patients", "control", "observation", "transplant"}
        if words and r["dst"] in S.node_type:
            cand = [p for p in S.disease_papers(r["dst"]) if S.attrs(p).get("neg") or
                    (set(S.attrs(p).get("pub_types") or []) & (RETRACTED_TYPES | COMMENT_TYPES))]
            out += _paper_flags(S, cand, claim_words=words, cap=3)
    elif rel in ("investigates", "runs") and S.node_type.get(r["dst"]) == "Trial":
        out += _trial_flags(S, r["dst"], r["id"])
    elif rel == "cites" and S.node_type.get(r["src"]) == "Trial":
        out += _trial_flags(S, r["src"], r["id"])
        out += _paper_flags(S, [r["dst"]], cap=1)
    elif rel == "funds":
        if detail.get("matched_field") == "terms":
            out.append(_c("caveat", 0.3, "Linked only through NIH auto-generated concept terms, not the title or abstract."))
        elif detail.get("matched_field") == "gene symbol":
            out.append(_c("caveat", 0.3, "Linked through a gene symbol in the project title, not a disease name."))
        if detail.get("active") is False:
            out.append(_c("caveat", 0.1, f"Project ended ({', '.join(map(str, detail.get('fiscal_years', [])[-1:]))}): not active funding."))
    elif rel == "leads" and "existing researcher" in str(detail.get("identity_match", "")):
        out.append(_c("caveat", 0.25, f"PI matched to an existing researcher by name ({detail.get('identity_match')}); "
                                      "verify identity before contacting."))
    elif rel in ("variant_of", "pathogenic_for") and int(detail.get("stars", 0) or 0) <= 1:
        out.append(_c("caveat", 0.2, "ClinVar review status is a single submitter (1 star); no expert-panel review."))
    return out


def _n_sources(S, r, detail) -> int:
    """Number of independent source families behind the edge."""
    rel, src = r["rel"], r["source"]
    fams = {f for f in SOURCE_FAMILIES if f.lower() in src.lower()}
    if rel == "similar_to":
        comp = detail.get("components", {})
        return max(1, sum(1 for v in comp.values() if (v or 0) > 0.0))
    if rel in ("causes", "associated_with"):
        return max(1, len(fams) + (1 if detail.get("pmids") else 0))
    if rel == "mentions":
        return max(1, len(fams))
    if rel == "studies":
        t = r["src"]
        return 1 + (1 if any(True for _ in S.out(t, "cites")) else 0)
    if rel == "has_phenotype":
        return 1 + (1 if any(str(x).startswith("PMID:") for x in detail.get("references", [])) else 0)
    if rel == "funds":
        return 1
    return max(1, len(fams))


def _recency(date) -> float:
    m = re.match(r"(\d{4})", str(date or ""))
    if not m:
        return 1.0
    age = max(0, time.localtime().tm_year - int(m.group(1)))
    return round(max(0.85, 1.0 - 0.01 * max(0, age - 5)), 3)


def assess_edge(S, r, detail, n_supporting_papers=0) -> dict:
    """Contradicting items + confidence_breakdown + evidence_summary for one edge row. Cheap (no explain / no paper load)."""
    flags = _flags(S, r, detail)
    n_src = _n_sources(S, r, detail)
    rel_src = float(r["confidence"])
    rec = _recency(r["date"])
    contra_w = sum(f["weight"] for f in flags if f["weight"] >= COUNTS_AS_CONTRA)
    caveat_w = sum(f["weight"] for f in flags if f["weight"] < COUNTS_AS_CONTRA)
    penalty = round(min(0.4, 0.25 * contra_w + 0.05 * caveat_w), 3)
    corro = 0.0 if n_src <= 1 else 0.05 if n_src == 2 else 0.08
    overall = round(max(0.0, min(1.0, rel_src * rec + corro - penalty)), 3)
    n_contra = sum(1 for f in flags if f["weight"] >= COUNTS_AS_CONTRA)
    if r["rel"] in ("mentions", "authored"):
        n_support = max(1, n_supporting_papers)          # the paper itself is the evidence
    else:                                                # supporting papers + one per independent source record
        n_support = n_supporting_papers + n_src
    if rel_src <= 0:
        n_support = 0
    if n_support == 0:
        stance = "unsupported"
    elif contra_w >= 0.6:
        stance = "mixed"
    elif overall < 0.5 or (r["evidence_type"] == "inferred" and n_src < 2) or caveat_w >= 0.5:
        stance = "weak"
    else:
        stance = "supported"
    return {"contradicting": flags, "overall": overall,
            "breakdown": {"source_reliability": round(rel_src, 3), "n_independent_sources": n_src, "recency": rec,
                          "corroboration_bonus": corro, "penalty": penalty, "overall": overall,
                          "formula": "overall = source_reliability x recency + corroboration_bonus - penalty "
                                     "(penalty = 0.25 x counter-evidence weight + 0.05 x caveat weight, capped at 0.4)"},
            "summary": {"supporting": int(n_support), "contradicting": n_contra,
                        "caveats": len(flags) - n_contra, "stance": stance}}


def edge_assessment(edge_id: str) -> dict:
    """Light-weight assess_edge for plans: {'summary','breakdown','contradicting','confidence'}."""
    S = state()
    r = S.edges.loc[edge_id]
    a = assess_edge(S, r, _parse(r["detail"]))
    return {"summary": a["summary"], "breakdown": a["breakdown"], "contradicting": a["contradicting"],
            "confidence": a["overall"]}




# ------------------------------------------------------------------ epidemiology / funding / variants / people / assets
def epidemiology(S, did) -> dict:
    """{"prevalence","onset","inheritance","source", ...} from Orphanet product 9; None values when Orphanet has no record.
    A grouping class borrows nothing from its subtypes (their prevalences differ)."""
    e = S.attrs(did).get("epidemiology") or {}
    return {"prevalence": e.get("prevalence"), "onset": e.get("onset") or [], "inheritance": e.get("inheritance") or [],
            "source": e.get("source", "Orphanet / Orphadata product 9 (CC-BY-4.0)"), "source_url": e.get("source_url", ""),
            "source_id": e.get("source_id", ""), "records": e.get("prevalence_records", []),
            "available": bool(e)}


def _grant_row(S, gid, edge_id, disease_id=None) -> dict:
    at = S.attrs(gid)
    e = S.edges.loc[edge_id]
    d = _parse(e["detail"])
    fys = at.get("fiscal_years") or []
    return {"id": gid, "title": S.node_label[gid], "pi": ", ".join(at.get("pis") or []), "org": at.get("org") or "",
            "location": ", ".join(x for x in (at.get("city"), at.get("state"), at.get("country")) if x),
            "years": (f"FY{fys[0]}-{fys[-1]}" if len(fys) > 1 else f"FY{fys[0]}") if fys else "",
            "fiscal_years": fys, "amount": at.get("amount_total"), "url": at.get("url") or d.get("url", ""),
            "edge_id": edge_id, "active": bool(at.get("active")), "project_num": at.get("latest_project_num"),
            "institute": at.get("institute"), "confidence": float(e["confidence"]), "matched_by": d.get("matched_by", ""),
            "end_date": at.get("end_date", ""), "disease_id": e["dst"]}


def disease_grants(S, did, family=True) -> list[dict]:
    """Grant rows (active first, then by latest fiscal year) for the disease and its subtypes; one row per grant."""
    out, seen = [], set()
    for m in (S.family(did) if family else (did,)):
        for g, eid, _ in S.inn(m, "funds"):
            if g in seen:
                continue
            seen.add(g)
            out.append(_grant_row(S, g, eid))
    out.sort(key=lambda x: (not x["active"], -(x["fiscal_years"][-1] if x["fiscal_years"] else 0), -float(x["amount"] or 0)))
    return out


def _funding_brief(S, did) -> dict:
    gs = disease_grants(S, did)
    act = sum(1 for g in gs if g["active"])
    return {"n_grants": len(gs), "n_active": act, "gap": act == 0}


def funding(disease_id: str) -> dict:
    """NIH RePORTER grants (FY2005+) that name the disease (or a subtype) in title/abstract/terms."""
    S = state()
    _require_disease(S, disease_id)
    gs = disease_grants(S, disease_id)
    act = [g for g in gs if g["active"]]
    broader = []
    for p in S.disease_ancestors(disease_id):
        if S.node_type.get(p) == "Disease":
            for g, eid, _ in S.inn(p, "funds"):
                if g not in {x["id"] for x in gs}:
                    row = _grant_row(S, g, eid)
                    row["context"] = f"funds the broader class '{S.node_label[p]}'"
                    broader.append(row)
    broader.sort(key=lambda x: (not x["active"], -(x["fiscal_years"][-1] if x["fiscal_years"] else 0)))
    gap = not act
    note = None
    if gap:
        note = (f"No active NIH grant naming {S.node_label[disease_id]} was found in RePORTER (FY2005-2026, "
                f"{len(gs)} past grant(s)). Other funders (foundations, EU, industry) are not covered by this source.")
    return {"grants": gs[:60], "total_active": len(act), "total": len(gs), "gap": gap, "gap_note": note,
            "broader_class_grants": broader[:8], "active_amount_last_fy": sum(float((g["amount"] or 0)) for g in act),
            "source": "NIH RePORTER v2 (public domain)", "disease": {"id": disease_id, "label": S.node_label[disease_id]}}


def _variant_row(S, vid, edge_id=None) -> dict:
    at = S.attrs(vid)
    dis = [{"id": d, "label": S.node_label[d], "edge_id": eid} for d, eid, _ in S.out(vid, "pathogenic_for")]
    ge = [(g, eid) for g, eid, _ in S.out(vid, "variant_of")]
    return {"id": vid, "label": S.node_label[vid], "hgvs": at.get("hgvs"), "gene": at.get("gene"),
            "gene_id": ge[0][0] if ge else None, "significance": at.get("clinical_significance"), "stars": at.get("stars"),
            "review_status": at.get("review_status"), "n_submitters": at.get("n_submitters"), "type": at.get("variant_type"),
            "rsid": at.get("rsid"), "conditions": at.get("conditions"), "last_evaluated": at.get("last_evaluated"),
            "diseases": dis, "url": at.get("url"), "edge_id": edge_id or (ge[0][1] if ge else None)}


def _gene_counts(S, gid) -> dict:
    a = S.attrs(gid)
    return {k: int(a.get(k, 0)) for k in ("n_pathogenic", "n_vus", "n_benign", "n_conflicting", "n_variants_total")}


def _variants_brief(S, did) -> dict:
    tot = Counter()
    for g in S.gene_profile(did):
        tot.update(_gene_counts(S, g))
    return {"n_pathogenic": tot["n_pathogenic"], "n_vus": tot["n_vus"], "n_benign": tot["n_benign"],
            "n_genes": len(S.gene_profile(did))}


def variants(gene_or_disease_id: str, k: int = 20) -> dict:
    """ClinVar (GRCh38). Counts cover ALL ClinVar records of the gene; `top` lists only the highest-reviewed
    pathogenic / likely pathogenic variants kept in the graph (<=1-star filtered out, <=50 per gene)."""
    S = state()
    nid = gene_or_disease_id
    t = S.node_type.get(nid)
    if t not in ("Gene", "Disease"):
        raise KeyError(nid)
    if t == "Gene":
        genes = [nid]
        vids = [(v, eid) for v, eid, _ in S.inn(nid, "variant_of")]
    else:
        genes = list(S.gene_profile(nid))
        vids = [(v, eid) for m in S.family(nid) for v, eid, _ in S.inn(m, "pathogenic_for")]
        seen, uniq = set(), []
        for v, eid in vids:
            if v not in seen:
                seen.add(v)
                uniq.append((v, eid))
        vids = uniq
    rows = [_variant_row(S, v, e) for v, e in vids]
    rows.sort(key=lambda r: (-(r["stars"] or 0), -(r["n_submitters"] or 0), r["id"]))
    per_gene, tot = [], Counter()
    for g in genes:
        c = _gene_counts(S, g)
        tot.update(c)
        per_gene.append({"id": g, "label": S.node_label[g], **c})
    return {"id": nid, "scope": t.lower(), "label": S.node_label[nid],
            "counts": {"pathogenic": tot["n_pathogenic"], "vus": tot["n_vus"], "benign": tot["n_benign"],
                       "conflicting": tot["n_conflicting"], "total": tot["n_variants_total"],
                       "in_graph": len(rows)},
            "genes": sorted(per_gene, key=lambda x: -x["n_pathogenic"]), "top": rows[:k],
            "source": "ClinVar variant_summary (NCBI), GRCh38", "has_data": bool(genes) and tot["n_variants_total"] > 0}


def researchers_for(disease_id: str, k: int = 10) -> list[dict]:
    """Ranked collaborators for a disease: PubMed papers on it, NIH grants they lead, trials they investigate."""
    S = state()
    _require_disease(S, disease_id)
    papers = S.disease_researchers(disease_id)
    grants = {g["id"]: g for g in disease_grants(S, disease_id)}
    trials = S.disease_trials(disease_id)
    gl, tl = defaultdict(list), defaultdict(list)
    for gid, g in grants.items():
        for r, eid, _ in S.inn(gid, "leads"):
            gl[r].append((gid, eid))
    for t in trials:
        for r, eid, _ in S.inn(t, "investigates"):
            tl[r].append((t, eid))
    pool = {r for r, _ in sorted(papers.items(), key=lambda x: -x[1])[:400]} | set(gl) | set(tl)
    out = []
    for r in pool:
        at = S.attrs(r)
        n_p, n_g, n_t = papers.get(r, 0), len(gl.get(r, [])), len(tl.get(r, []))
        n_act = sum(1 for gid, _ in gl.get(r, []) if grants[gid]["active"])
        score = (0.45 * min(n_p, 15) / 15 + 0.3 * min(n_g, 3) / 3 + 0.1 * min(n_act, 2) / 2 + 0.15 * min(n_t, 3) / 3
                 + (0.05 if at.get("orcid") else 0) - (0.15 if at.get("homonym_risk") else 0))
        if n_p < MIN_PAPERS and n_g == 0 and n_t == 0:
            continue
        why = []
        if n_p:
            why.append(f"{n_p} PubMed paper(s) on this disease")
        if n_g:
            why.append(f"leads {n_g} NIH grant(s)" + (f", {n_act} active" if n_act else ""))
        if n_t:
            why.append(f"investigator on {n_t} trial/registry record(s)")
        eids = [e for _, e in gl.get(r, [])[:2]] + [e for _, e in tl.get(r, [])[:2]]
        if n_p:
            for p, eid, _ in sorted(S.out(r, "authored"), key=lambda x: -(S.attrs(x[0]).get("year") or 0)):
                if p in S.disease_papers(disease_id):
                    eids.append(eid)
                    break
        aff = at.get("affiliation", "")
        if not aff and gl.get(r):
            aff = grants[gl[r][0][0]]["org"]
        out.append({"id": r, "label": S.node_label[r], "score": round(score, 3), "papers": n_p, "grants": n_g,
                    "active_grants": n_act, "trials": n_t, "affiliation": aff, "country": at.get("country", ""),
                    "orcid": at.get("orcid", ""), "why": why, "edge_ids": eids, "identity": at.get("disambiguation"),
                    "homonym_risk": bool(at.get("homonym_risk")),
                    "contact": {"affiliation": aff, "orcid_url": f"https://orcid.org/{at['orcid']}" if at.get("orcid") else "",
                                "note": "Contact via the institution page or the corresponding-author address on the paper; "
                                        "no e-mail addresses are stored."},
                    "grant_ids": [g for g, _ in gl.get(r, [])][:5], "trial_ids": [t for t, _ in tl.get(r, [])][:5]})
    out.sort(key=lambda x: (-x["score"], x["label"]))
    return out[:k]


MODEL_RX = re.compile(r"\b(mouse|mice|murine|zebrafish|drosophila|canine|dog|feline|cat|ovine|sheep|bovine|porcine|pig|"
                      r"rat|knock-?out|knock-?in|animal model|disease model|ipsc|organoid|cell model|model of)\b", re.I)


def assets_for(disease_id: str) -> dict:
    """Shared research assets for a disease (and subtypes): trials, registries, natural-history studies, model systems."""
    S = state()
    _require_disease(S, disease_id)
    groups = {"trials": [], "registries": [], "natural_history": [], "observational": []}
    rank = {"RECRUITING": 0, "ENROLLING_BY_INVITATION": 1, "ACTIVE_NOT_RECRUITING": 2, "NOT_YET_RECRUITING": 3}
    for t, eids in S.disease_trials(disease_id).items():
        at = S.attrs(t)
        row = {"id": t, "label": S.node_label[t], "kind": at.get("kind"), "status": at.get("status"), "start": at.get("start"),
               "phases": at.get("phases"), "sponsor": at.get("sponsor"), "url": at.get("url"), "edge_id": eids[0],
               "enrollment": at.get("enrollment"), "countries": (at.get("countries") or [])[:6],
               "interventions": (at.get("interventions") or [])[:4], "why_stopped": at.get("why_stopped") or "",
               "stopped": (at.get("status") or "") in STOP_STATUSES,
               "broad": (at.get("n_conditions") or 0) > BROAD_TRIAL_CONDITIONS}
        key = {"registry": "registries", "natural_history": "natural_history", "observational": "observational"}.get(at.get("kind"), "trials")
        groups[key].append(row)
    for v in groups.values():
        v.sort(key=lambda x: (x["broad"], rank.get(x["status"], 5), str(x["start"] or "")[::-1]))
    models = []
    for g in disease_grants(S, disease_id):
        txt = f"{g['title']} {S.attrs(g['id']).get('abstract', '')[:400]}"
        m = MODEL_RX.search(txt)
        if m:
            models.append({"id": g["id"], "label": g["title"], "kind": "grant", "organism_or_system": m.group(0).lower(),
                           "year": (g["fiscal_years"] or [None])[-1], "edge_id": g["edge_id"], "url": g["url"]})
    pm = []
    for p in S.disease_papers(disease_id):
        m = MODEL_RX.search(S.node_label[p])
        if m:
            pm.append((S.attrs(p).get("year") or 0, p, m.group(0).lower()))
    for yr, p, org in sorted(pm, reverse=True)[:8]:
        eid = next((e for d, e, _ in S.out(p, "mentions") if d in S.family(disease_id)), None)
        models.append({"id": p, "label": S.node_label[p], "kind": "paper", "organism_or_system": org, "year": yr,
                       "edge_id": eid, "url": S.attrs(p).get("url")})
    return {"disease": {"id": disease_id, "label": S.node_label[disease_id]}, **groups, "models": models[:16],
            "counts": {k: len(v) for k, v in groups.items()} | {"models": len(models)}}


# ------------------------------------------------------------------------------------- stats
_LLM_CACHE = {"t": 0.0, "v": False}


def _llm_ready() -> bool:
    if time.time() - _LLM_CACHE["t"] < 30:
        return _LLM_CACHE["v"]
    ok = False
    try:
        u = urlparse(CFG["llm"]["base_url"])
        with socket.create_connection((u.hostname, u.port or 80), timeout=0.3):
            ok = True
    except OSError:
        ok = False
    _LLM_CACHE.update(t=time.time(), v=ok)
    return ok


def stats() -> dict:
    S = state()
    nt = Counter(S.node_type.values())
    ev = S.edges.evidence_type.value_counts().to_dict()
    rel = S.edges.groupby(["rel", "evidence_type"]).size()
    groups = {}
    for gname in CFG["groups"]:
        dis = [n for n, t in S.node_type.items() if t == "Disease" and gname in S.node_group[n].split("|")]
        groups[gname] = {"label": CFG["groups"][gname]["label"], "diseases": len(dis),
                         "papers": sum(1 for n, t in S.node_type.items() if t == "Paper" and gname in S.node_group[n]),
                         "trials": sum(1 for n, t in S.node_type.items() if t == "Trial" and gname in S.node_group[n])}
    raw = ROOT / CFG["paths"]["raw"]
    srcs = []
    sj = raw / "sources.json"
    if sj.exists():
        for k, v in json.loads(sj.read_text()).items():
            srcs.append({"name": k, "url": v.get("url"), "retrieved": v.get("retrieved"), "records": v.get("records", v.get("bytes")),
                         "note": v.get("note")})
    for g in CFG["groups"].values():
        q = raw / "pubmed" / g["pubmed_slice"] / "query.json"
        if q.exists():
            qq = json.loads(q.read_text())
            srcs.append({"name": f"PubMed: {qq.get('term')}", "retrieved": qq.get("fetched_at"), "records": qq.get("n")})
    ex = {x["name"] for x in srcs}
    gm = S.meta.get("grants") or {}
    vm = S.meta.get("variants") or {}
    if gm and "reporter/projects.jsonl" not in ex:
        srcs.append({"name": "NIH RePORTER v2 grants", "retrieved": S.meta.get("built"), "records": gm.get("distinct_projects")})
    if vm and "clinvar/variants.tsv" not in ex:
        srcs.append({"name": "ClinVar variant_summary (GRCh38)", "retrieved": S.meta.get("built"), "records": vm.get("rows")})
    srcs.append({"name": "ClinicalTrials.gov API v2", "retrieved": S.meta.get("built"),
                 "records": S.meta.get("ctgov_studies_total")})
    srcs.append({"name": f"PubTator3 annotations ({S.meta.get('pubtator', 'unknown')})", "retrieved": S.meta.get("built"),
                 "records": None})
    idx = ROOT / CFG["paths"]["index"]
    index_ready = idx.exists() and any(idx.iterdir())
    return {"nodes_by_type": dict(nt), "edges_by_evidence": ev,
            "edges_by_rel": {f"{a}/{b}": int(n) for (a, b), n in rel.items()},
            "groups": groups, "sources": srcs, "index_ready": bool(index_ready), "llm_ready": _llm_ready(),
            "load_seconds": S.load_seconds, "n_nodes": S.G.number_of_nodes(), "n_edges": S.G.number_of_edges(),
            "clusters": int(S.clusters.cluster_id[S.clusters.cluster_id != ""].nunique())}
