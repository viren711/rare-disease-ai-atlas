#!/usr/bin/env python3
"""Disease~disease similarity by mechanism + phenotype (NOT by name) and Louvain clusters.

  phenotype  = cosine over HPO term sets (true-path propagated), each term weighted by its IC (= IDF)
  pathway    = Jaccard over Reactome pathways of the disease genes
  literature = Ochiai co-mention: |papers(A) & papers(B)| / sqrt(|papers(A)| * |papers(B)|)
  score      = sum(sim_weights[c] * component[c])
A grouping class (e.g. "metachromatic leukodystrophy") is profiled by the union of itself and its subtypes.
Pairs where one disease is an ancestor of the other are skipped (that link is already subclass_of).
Group roots / very broad grouping classes are not given similar_to edges.

Writes: similar_to edges (evidence_type inferred) appended to data/graph/edges.parquet (old ones replaced),
        data/graph/clusters.parquet (disease_id, cluster_id, cluster_label).
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((ROOT / "config/atlas.yaml").read_text())
OUT = ROOT / CFG["paths"]["graph"]
G = CFG["graph"]
W = G["sim_weights"]
MAX_DESC = 25          # grouping classes with more in-set descendants than this are too broad for similar_to
TODAY = time.strftime("%Y-%m-%d")
# best cross-group candidates are kept down to CROSS_FACTOR * min_similarity, flagged as below the main threshold
CROSS_FACTOR = 0.75


def main():
    t0 = time.time()
    nodes = pd.read_parquet(OUT / "nodes.parquet")
    edges = pd.read_parquet(OUT / "edges.parquet")
    edges = edges[edges.rel != "similar_to"].reset_index(drop=True)
    label = dict(zip(nodes.id, nodes.label))
    attrs = {r.id: json.loads(r.attrs) for r in nodes[nodes.type == "Phenotype"].itertuples()}
    ic = {k: v.get("ic", 0.0) for k, v in attrs.items()}
    dis = nodes[nodes.type == "Disease"]
    dgroup = dict(zip(dis.id, dis.group))

    # disease hierarchy within the set
    sub = edges[(edges.rel == "subclass_of") & edges.src.str.startswith("MONDO") & edges.dst.str.startswith("MONDO")]
    dparents = defaultdict(set)
    for s, d in zip(sub.src, sub.dst):
        dparents[s].add(d)

    def ancestors(d, par):
        out, st = set(), list(par.get(d, ()))
        while st:
            x = st.pop()
            if x not in out:
                out.add(x)
                st.extend(par.get(x, ()))
        return out
    danc = {d: ancestors(d, dparents) for d in dis.id}
    ddesc = defaultdict(set)
    for d, an in danc.items():
        for a in an:
            ddesc[a].add(d)
    roots = {g["mondo_root"] for g in CFG["groups"].values()} | set(CFG.get("bridge_roots", {}).values())
    cand = sorted(d for d in dis.id if d not in roots and len(ddesc[d]) <= MAX_DESC)

    # phenotype hierarchy (only phenotype nodes in graph)
    psub = edges[(edges.rel == "subclass_of") & edges.src.str.startswith("HP:")]
    pparents = defaultdict(set)
    for s, d in zip(psub.src, psub.dst):
        pparents[s].add(d)
    panc_cache = {}

    def panc(t):
        if t not in panc_cache:
            panc_cache[t] = ancestors(t, pparents) | {t}
        return panc_cache[t]

    hp = edges[edges.rel == "has_phenotype"]
    direct = defaultdict(dict)          # disease -> {term: edge_id}
    for s, d, eid in zip(hp.src, hp.dst, hp.id):
        direct[s][d] = eid
    gd = edges[edges.rel.isin(["causes", "associated_with"])]
    dgenes = defaultdict(dict)          # disease -> {gene: edge_id}
    for s, d, eid in zip(gd.src, gd.dst, gd.id):
        dgenes[d][s] = eid
    ip = edges[edges.rel == "in_pathway"]
    gpw = defaultdict(dict)             # gene -> {pathway: edge_id}
    for s, d, eid in zip(ip.src, ip.dst, ip.id):
        gpw[s][d] = eid
    men = edges[(edges.rel == "mentions") & edges.dst.str.startswith("MONDO")]
    dpapers = defaultdict(set)
    for s, d in zip(men.src, men.dst):
        dpapers[d].add(s)

    prof = {}
    for d in cand:
        members = [d] + sorted(ddesc[d])
        terms = {}
        for m in members:
            for t, eid in direct.get(m, {}).items():
                terms.setdefault(t, eid)
        genes = {}
        for m in members:
            for g, eid in dgenes.get(m, {}).items():
                genes.setdefault(g, eid)
        pws = {}
        for g, geid in genes.items():
            for p, peid in gpw.get(g, {}).items():
                pws.setdefault(p, (g, geid, peid))
        papers = set().union(*[dpapers.get(m, set()) for m in members])
        prop = {}
        for t, eid in terms.items():
            for a in panc(t):
                # supporting annotated term for an ancestor: keep the most specific (highest IC) one
                if a not in prop or ic.get(t, 0) > ic.get(prop[a][0], 0):
                    prop[a] = (t, eid)
        prof[d] = {"terms": terms, "prop": prop, "genes": genes, "pws": pws, "papers": papers}

    # ---- phenotype cosine (vectorised): binary propagated sets x IC weights
    vocab = sorted({t for p in prof.values() for t in p["prop"]})
    vidx = {t: i for i, t in enumerate(vocab)}
    X = np.zeros((len(cand), len(vocab)), dtype=np.float32)
    for i, d in enumerate(cand):
        for t in prof[d]["prop"]:
            X[i, vidx[t]] = ic.get(t, 0.0)
    nrm = np.linalg.norm(X, axis=1, keepdims=True)
    Xn = np.divide(X, nrm, out=np.zeros_like(X), where=nrm > 0)
    S_ph = Xn @ Xn.T
    # ---- pathway Jaccard
    n = len(cand)
    S_pw = np.zeros((n, n), dtype=np.float32)
    S_lit = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        pi, li = set(prof[cand[i]]["pws"]), prof[cand[i]]["papers"]
        for j in range(i + 1, n):
            pj, lj = set(prof[cand[j]]["pws"]), prof[cand[j]]["papers"]
            if pi and pj:
                inter = len(pi & pj)
                if inter:
                    S_pw[i, j] = S_pw[j, i] = inter / len(pi | pj)
            if li and lj:
                co = len(li & lj)
                if co:
                    S_lit[i, j] = S_lit[j, i] = co / np.sqrt(len(li) * len(lj))
    S = W["phenotype"] * S_ph + W["pathway"] * S_pw + W["literature"] * S_lit
    np.fill_diagonal(S, 0)
    for i, d in enumerate(cand):          # no similar_to between ancestor and descendant
        for j, e in enumerate(cand):
            if e in danc[d] or d in danc[e]:
                S[i, j] = 0

    k, thr = G["top_k_neighbours"], G["min_similarity"]
    pairs = set()
    for i in range(n):
        order = np.argsort(-S[i])[:k]
        for j in order:
            if S[i, j] >= thr:
                pairs.add((min(i, j), max(i, j)))
        # also keep the best cross-group neighbours (the "unconnected communities" the atlas is for)
        cross = [j for j in np.argsort(-S[i]) if dgroup[cand[j]] != dgroup[cand[i]]][:3]
        for j in cross:
            if S[i, j] >= thr * CROSS_FACTOR:
                pairs.add((min(i, j), max(i, j)))
    if len(sys.argv) == 3:   # debug: python 40_cluster.py MONDO:a MONDO:b
        i, j = cand.index(sys.argv[1]), cand.index(sys.argv[2])
        print(f"debug {sys.argv[1]}~{sys.argv[2]}: score={S[i, j]:.4f} phenotype={S_ph[i, j]:.4f} "
              f"pathway={S_pw[i, j]:.4f} literature={S_lit[i, j]:.4f} "
              f"co-papers={len(prof[cand[i]]['papers'] & prof[cand[j]]['papers'])}")
    rows = []
    for i, j in sorted(pairs, key=lambda p: (cand[p[0]], cand[p[1]])):
        a, b = cand[i], cand[j]
        A, B = prof[a], prof[b]
        shared = set(A["prop"]) & set(B["prop"])
        # non-redundant: drop a shared term if one of its shared descendants is also listed
        shared_sorted = sorted(shared, key=lambda t: (-ic.get(t, 0), t))
        chosen = []
        for t in shared_sorted:
            if ic.get(t, 0) < 1.0:
                break
            if any(t in panc(c) for c in chosen):
                continue
            chosen.append(t)
            if len(chosen) >= 12:
                break
        sp = [{"id": t, "label": label.get(t, t), "ic": round(ic.get(t, 0), 2),
               "edge_a": A["prop"][t][1], "edge_b": B["prop"][t][1],
               "via_a": A["prop"][t][0], "via_b": B["prop"][t][0]} for t in chosen]
        spw = [{"id": p, "label": label.get(p, p), "gene_a": label.get(A["pws"][p][0]), "gene_b": label.get(B["pws"][p][0]),
                "edge_ids": sorted({A["pws"][p][1], A["pws"][p][2], B["pws"][p][1], B["pws"][p][2]})}
               for p in sorted(set(A["pws"]) & set(B["pws"]))][:12]
        sg = [{"id": g, "label": label.get(g, g), "edge_a": A["genes"][g], "edge_b": B["genes"][g]}
              for g in sorted(set(A["genes"]) & set(B["genes"]))]
        co = sorted(A["papers"] & B["papers"], key=lambda p: -int(p.split(":")[1]))
        rows.append({
            "src": a, "dst": b, "rel": "similar_to",
            "source": "Atlas similarity (HPO IC-weighted cosine + Reactome Jaccard + PubMed co-mention)",
            "source_id": "atlas-sim-v1", "date": TODAY, "confidence": round(float(S[i, j]), 3),
            "evidence_type": "inferred",
            "detail": json.dumps({
                "score": round(float(S[i, j]), 4),
                "components": {"phenotype": round(float(S_ph[i, j]), 4), "pathway": round(float(S_pw[i, j]), 4),
                               "literature": round(float(S_lit[i, j]), 4)},
                "weights": W, "cross_group": dgroup[a] != dgroup[b],
                "below_main_threshold": bool(S[i, j] < thr),
                "note": ("kept as a best cross-group candidate below min_similarity; treat as a weak lead"
                         if S[i, j] < thr else ""),
                "shared_phenotypes": sp, "shared_pathways": spw, "shared_genes": sg,
                "co_mention_papers": len(co), "co_mention_sample": co[:10],
                "n_papers_a": len(A["papers"]), "n_papers_b": len(B["papers"]),
                "n_phenotypes_a": len(A["terms"]), "n_phenotypes_b": len(B["terms"]),
                "profile_includes_subtypes": {"a": len(ddesc[a]), "b": len(ddesc[b])},
            }),
        })
    sim = pd.DataFrame(rows)
    start = len(edges)
    sim.insert(0, "id", [f"e{start + i}" for i in range(len(sim))])
    # keep ids of existing edges untouched
    if not edges.id.equals(pd.Series([f"e{i}" for i in range(len(edges))])):
        mx = max(int(x[1:]) for x in edges.id) + 1
        sim["id"] = [f"e{mx + i}" for i in range(len(sim))]
    out = pd.concat([edges, sim], ignore_index=True)
    _tmp = OUT / "edges.parquet.tmp"
    out.to_parquet(_tmp, index=False)
    _tmp.replace(OUT / "edges.parquet")
    print(f"candidates: {len(cand)} diseases; similar_to edges: {len(sim)} "
          f"(cross-group: {int(sum(json.loads(d)['cross_group'] for d in sim.detail))})")

    # ---- Louvain
    Gs = nx.Graph()
    Gs.add_nodes_from(cand)
    for r in rows:
        Gs.add_edge(r["src"], r["dst"], weight=r["confidence"])
    comms = nx.community.louvain_communities(Gs, weight="weight", seed=42, resolution=1.0)
    comms = sorted([sorted(c) for c in comms], key=lambda c: (-len(c), c[0]))
    crow = []
    cid_n = 0
    used_labels = set()
    for c in comms:
        if len(c) == 1:
            continue
        cid = f"C{cid_n:02d}"
        cid_n += 1
        cnt = Counter(t for d in c for t in prof[d]["prop"])
        best = sorted(((cnt[t] / len(c)) * ic.get(t, 0), t) for t in cnt if cnt[t] / len(c) >= 0.5)
        parts = []
        best = [t for _, t in reversed(best) if label.get(t, t) not in used_labels]
        if best:
            parts.append(label.get(best[0], best[0]))
        pcnt = Counter(p for d in c for p in prof[d]["pws"])
        for p, m in pcnt.most_common():
            if m < max(2, len(c) / 2):
                break
            if label.get(p, p).strip() not in used_labels:
                parts.append(label.get(p, p).strip())
                break
        used_labels.update(parts)
        groups = Counter(dgroup[d] for d in c)
        lbl = " · ".join(parts) or label[c[0]]
        if len(groups) > 1:
            lbl += " (cross-group)"
        for d in c:
            crow.append({"disease_id": d, "cluster_id": cid, "cluster_label": lbl})
    clustered = {r["disease_id"] for r in crow}
    for d in sorted(set(dis.id) - clustered):
        crow.append({"disease_id": d, "cluster_id": "", "cluster_label": "unclustered (no supported neighbours)"})
    cl = pd.DataFrame(crow)
    _tmp = OUT / "clusters.parquet.tmp"
    cl.to_parquet(_tmp, index=False)
    _tmp.replace(OUT / "clusters.parquet")
    print(f"clusters: {cid_n} (multi-member), unclustered diseases: {len(set(dis.id) - clustered)}")
    for cid, grp in cl[cl.cluster_id != ""].groupby("cluster_id"):
        print(f"  {cid} [{len(grp)}] {grp.cluster_label.iloc[0]} :: "
              f"{', '.join(label[d] for d in grp.disease_id[:6])}{' ...' if len(grp) > 6 else ''}")
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    sys.exit(main())
