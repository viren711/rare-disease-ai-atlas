"""Action plan for disease A (vs neighbour B): supported route, shared assets, people, orgs, next steps -
or an explicit gap report when no supported route exists.

A route is "supported" only through curated / extracted edges (never through the inferred similar_to edge):
  shared gene (Orphanet/HPO), shared Reactome pathway of their genes, shared *informative* HPO phenotype
  (IC >= informative_ic, directly or via a common specific ancestor term), or >= MIN_CO_PAPERS PubMed papers
  that mention both. Shared trials, researchers and orgs are reported as assets but do not alone make a route.
"""
from __future__ import annotations

import re
from collections import defaultdict

from atlas import explain, graph

MIN_CO_PAPERS = 3
MODALITIES = {
    "haematopoietic stem cell transplant": r"stem cell|transplant|hsct|cord blood|bone marrow|\bbmt\b",
    "gene therapy": r"gene therapy|lentivir|\baav\b|autologous cd34|gene transfer|transduced",
    "enzyme replacement therapy": r"enzyme replacement|\bert\b|alfa\b|alfa |\balglucosidase|agalsidase|imiglucerase",
    "substrate reduction therapy": r"substrate reduction|miglustat|eliglustat|venglustat|lucerastat",
    "newborn screening": r"newborn screening|neonatal screening",
    "natural history / registry": r"natural history|registry",
    "biomarker study": r"biomarker",
}


def _step(S, eid) -> dict:
    r = S.edges.loc[eid]
    return {"edge_id": eid, "src": r["src"], "dst": r["dst"], "src_label": S.node_label.get(r["src"], r["src"]),
            "rel": r["rel"], "dst_label": S.node_label.get(r["dst"], r["dst"]), "evidence_type": r["evidence_type"],
            "source": r["source"], "confidence": float(r["confidence"])}


def _pheno_chain(S, t, target) -> list[str]:
    """subclass_of edge ids from term t up to ancestor target (BFS)."""
    if t == target:
        return []
    prev, frontier, seen = {}, [t], {t}
    while frontier:
        nxt = []
        for x in frontier:
            for p, eid, _ in S.out(x, "subclass_of"):
                if p in seen:
                    continue
                seen.add(p)
                prev[p] = (x, eid)
                if p == target:
                    chain, cur = [], p
                    while cur != t:
                        cur, e = prev[cur]
                        chain.append(e)
                    return chain[::-1]
                nxt.append(p)
        frontier = nxt
    return []


def _closure(S, prof) -> dict:
    """ancestor term -> most specific annotated term under it (from the profile)."""
    out = {}
    for t in prof:
        for a in S.pheno_ancestors(t):
            if a not in out or S.ic(t) > S.ic(out[a]):
                out[a] = t
    return out


def _modalities(S, trial) -> set:
    at = S.attrs(trial)
    text = " ".join([S.node_label.get(trial, "")] + [str(x) for x in at.get("interventions", []) or []]).lower()
    return {m for m, rx in MODALITIES.items() if re.search(rx, text)}


def _routes(S, a, b):
    routes = []
    ga, gb = S.gene_profile(a), S.gene_profile(b)
    for g in sorted(set(ga) & set(gb)):
        e1, e2 = ga[g][0], gb[g][0]
        routes.append({"kind": "shared_gene", "strength": 0.95 * min(S.G.edges[g, ga[g][1], e1]["conf"], 1.0),
                       "text": f"{S.node_label[a]} and {S.node_label[b]} share the disease gene {S.node_label[g]}",
                       "edge_ids": [e1, e2]})
    pa = {}
    for g, (eid, via) in ga.items():
        for p, peid, _ in S.out(g, "in_pathway"):
            pa.setdefault(p, (g, eid, peid))
    pb = {}
    for g, (eid, via) in gb.items():
        for p, peid, _ in S.out(g, "in_pathway"):
            pb.setdefault(p, (g, eid, peid))
    for p in sorted(set(pa) & set(pb), key=lambda p: S.attrs(p).get("n_genes", 999)):
        n = S.attrs(p).get("n_genes", 999)
        (g1, e1, p1), (g2, e2, p2) = pa[p], pb[p]
        if g1 == g2:
            continue  # already covered by shared_gene
        routes.append({"kind": "shared_pathway", "strength": 0.85 if n <= 50 else 0.7 if n <= 150 else 0.55,
                       "text": (f"{S.node_label[g1]} ({S.node_label[a]}) and {S.node_label[g2]} ({S.node_label[b]}) "
                                f"act in the same Reactome pathway '{S.node_label[p]}' ({n} genes)"),
                       "edge_ids": [e1, p1, p2, e2]})
    # phenotypes: direct shared informative terms first, then informative common ancestors
    fa, fb = S.phenotype_profile(a), S.phenotype_profile(b)
    thr = graph.INFORMATIVE_IC
    direct = sorted((t for t in set(fa) & set(fb) if S.ic(t) >= thr), key=lambda t: (-S.ic(t), t))
    for t in direct[:5]:
        routes.append({"kind": "shared_phenotype", "strength": min(0.9, 0.5 + S.ic(t) / 20), "ic": round(S.ic(t), 2),
                       "text": (f"both {S.node_label[a]} and {S.node_label[b]} are annotated with the specific "
                                f"phenotype '{S.node_label[t]}' (IC {S.ic(t):.1f})"),
                       "edge_ids": [fa[t][0], fb[t][0]], "phenotype": t})
    if True:  # informative common ancestors (e.g. both have a specific kind of white-matter abnormality)
        ca, cb = _closure(S, fa), _closure(S, fb)
        anc = sorted((t for t in set(ca) & set(cb) if S.ic(t) >= thr and t not in direct), key=lambda t: (-S.ic(t), t))
        chosen = []
        for t in anc:
            if any(t in S.pheno_ancestors(c) for c in chosen + direct):
                continue
            chosen.append(t)
            ta, tb = ca[t], cb[t]
            chain = [fa[ta][0]] + _pheno_chain(S, ta, t) + _pheno_chain(S, tb, t)[::-1] + [fb[tb][0]]
            routes.append({"kind": "shared_phenotype_family", "strength": min(0.8, 0.4 + S.ic(t) / 20),
                           "ic": round(S.ic(t), 2),
                           "text": (f"{S.node_label[a]} ('{S.node_label[ta]}') and {S.node_label[b]} "
                                    f"('{S.node_label[tb]}') share the specific phenotype class '{S.node_label[t]}' "
                                    f"(IC {S.ic(t):.1f})"),
                           "edge_ids": chain, "phenotype": t})
            if len(chosen) >= (3 if len(direct) < 3 else 2):
                break
    # literature
    pa_set, pb_set = S.disease_papers(a), S.disease_papers(b)
    co = sorted(pa_set & pb_set, key=lambda p: -(S.attrs(p).get("year") or 0))
    if co:
        p = co[0]
        famA, famB = set(S.family(a)), set(S.family(b))
        ea = next((eid for d, eid, _ in S.out(p, "mentions") if d in famA), None)
        eb = next((eid for d, eid, _ in S.out(p, "mentions") if d in famB), None)
        routes.append({"kind": "co_mention", "strength": min(0.75, 0.3 + 0.03 * len(co)), "n_papers": len(co),
                       "text": (f"{len(co)} PubMed paper(s) mention both {S.node_label[a]} and {S.node_label[b]}, "
                                f"most recently '{S.node_label[p][:120]}' ({S.attrs(p).get('year')})"),
                       "edge_ids": [x for x in (ea, eb) if x]})
    routes.sort(key=lambda r: -r["strength"])
    return routes, co


def _assets(S, a, b):
    ta, tb = S.disease_trials(a), S.disease_trials(b)
    out = []
    for t in sorted(set(ta) & set(tb)):
        at = S.attrs(t)
        out.append({"id": t, "label": S.node_label[t], "kind": at.get("kind"), "status": at.get("status"),
                    "edge_ids": ta[t][:1] + tb[t][:1], "disease": "both", "url": at.get("url"),
                    "relevance": f"studies both {S.node_label[a]} and {S.node_label[b]}", "modalities": sorted(_modalities(S, t))})
    mods_b = defaultdict(list)
    for t in tb:
        for m in _modalities(S, t):
            mods_b[m].append(t)
    shared_mod = []
    for t in sorted(ta):
        if t in tb:
            continue
        mods = _modalities(S, t) & set(mods_b)
        at = S.attrs(t)
        if at.get("kind") in ("registry", "natural_history"):
            out.append({"id": t, "label": S.node_label[t], "kind": at.get("kind"), "status": at.get("status"),
                        "edge_ids": ta[t][:1], "disease": "a", "url": at.get("url"),
                        "relevance": (f"is a {at.get('kind').replace('_', ' ')} study in {S.node_label[a]} whose design "
                                      f"could be reused for {S.node_label[b]}"), "modalities": sorted(mods)})
        elif mods - {"natural history / registry", "biomarker study"}:
            m = sorted(mods - {"natural history / registry", "biomarker study"})[0]
            other = sorted(mods_b[m])[0]
            shared_mod.append({"id": t, "label": S.node_label[t], "kind": at.get("kind"), "status": at.get("status"),
                               "edge_ids": ta[t][:1] + tb[other][:1], "disease": "a", "url": at.get("url"),
                               "relevance": (f"tests {m} in {S.node_label[a]}, the same modality as {other} in "
                                             f"{S.node_label[b]}"), "modalities": sorted(mods), "paired_with": other})
    for t in sorted(tb):
        if t in ta:
            continue
        at = S.attrs(t)
        if at.get("kind") in ("registry", "natural_history"):
            out.append({"id": t, "label": S.node_label[t], "kind": at.get("kind"), "status": at.get("status"),
                        "edge_ids": tb[t][:1], "disease": "b", "url": at.get("url"),
                        "relevance": (f"is a {at.get('kind').replace('_', ' ')} study in {S.node_label[b]} that "
                                      f"{S.node_label[a]} families could learn from"), "modalities": sorted(_modalities(S, t))})
    status_rank = {"RECRUITING": 0, "ENROLLING_BY_INVITATION": 1, "ACTIVE_NOT_RECRUITING": 2, "NOT_YET_RECRUITING": 3}
    key = lambda x: ({"both": 0, "a": 1, "b": 2}[x["disease"]], status_rank.get(x["status"], 5), x["id"])  # noqa: E731
    out.sort(key=key)
    shared_mod.sort(key=lambda x: (status_rank.get(x["status"], 5), x["id"]))
    # interleave: shared first, then registries/natural history, then same-modality trials
    both = [x for x in out if x["disease"] == "both"]
    rest = [x for x in out if x["disease"] != "both"]
    # narrow studies first among the shared ones: a 185-condition screening programme is a weaker shared asset
    both.sort(key=lambda x: ((S.attrs(x["id"]).get("n_conditions") or 0) > graph.BROAD_TRIAL_CONDITIONS,
                             status_rank.get(x["status"], 5), x["id"]))
    return both[:8] + rest[:4] + shared_mod[:3]


def _researchers(S, a, b, k=10):
    ra, rb = S.disease_researchers(a), S.disease_researchers(b)
    m = graph.MIN_PAPERS
    shared = [r for r in set(ra) & set(rb) if ra[r] >= m and rb[r] >= m]
    # ORCID-identified people first; initials-only name keys that may merge homonyms last
    shared.sort(key=lambda r: (bool(S.attrs(r).get("homonym_risk")), -min(ra[r], rb[r]), -(ra[r] + rb[r]), r))
    famA, famB = set(S.family(a)), set(S.family(b))
    pa, pb = S.disease_papers(a), S.disease_papers(b)
    trials_a, trials_b = set(S.disease_trials(a)), set(S.disease_trials(b))
    out = []
    for r in shared[:k]:
        at = S.attrs(r)
        eids, got_a, got_b = [], False, False
        for p, eid, _ in sorted(S.out(r, "authored"), key=lambda x: -(S.attrs(x[0]).get("year") or 0)):
            if not got_a and p in pa:
                me = next((e for d, e, _ in S.out(p, "mentions") if d in famA), None)
                eids += [eid] + ([me] if me else [])
                got_a = True
            elif not got_b and p in pb:
                me = next((e for d, e, _ in S.out(p, "mentions") if d in famB), None)
                eids += [eid] + ([me] if me else [])
                got_b = True
            if got_a and got_b:
                break
        tr = [(t, eid) for t, eid, _ in S.out(r, "investigates") if t in trials_a | trials_b]
        # affiliation as printed on the most recent relevant paper (not on an unrelated homonym's paper)
        aff = ""
        for e in eids:
            if S.G.edges[S.edges.at[e, "src"], S.edges.at[e, "dst"], e]["rel"] == "authored":
                aff = graph._parse(S.edges.at[e, "detail"]).get("affiliation", "")
                if aff:
                    break
        out.append({"id": r, "label": S.node_label[r], "affiliation": aff or at.get("affiliation", ""),
                    "country": at.get("country", ""), "papers_a": ra[r], "papers_b": rb[r], "orcid": at.get("orcid", ""),
                    "edge_ids": eids + [e for _, e in tr], "trials": [t for t, _ in tr],
                    "identity": at.get("disambiguation"), "homonym_risk": bool(at.get("homonym_risk"))})
    return out


def _orgs(S, a, b):
    oa = {o["id"]: o for o in graph._orgs_for(S, a)}
    ob = {o["id"]: o for o in graph._orgs_for(S, b)}
    out = []
    for oid in list(oa) + [o for o in ob if o not in oa]:
        both = oid in oa and oid in ob
        src = oa.get(oid) or ob.get(oid)
        eids = [x["edge_id"] for x in (oa.get(oid), ob.get(oid)) if x]
        out.append({"id": oid, "label": src["label"], "url": src.get("url", ""),
                    "disease_id": (a if oid in oa else b), "disease_label": S.node_label[a if oid in oa else b],
                    "edge_id": eids[0], "edge_ids": eids, "serves_both": both, "how": src.get("how")})
    # verified patient organisations first (serving both, then either), then CT.gov sponsor foundations
    out.sort(key=lambda o: (not o["url"], not o["serves_both"], o["label"]))
    return out[:12]


def _differences(S, a, b, k=4):
    fa, fb = S.phenotype_profile(a), S.phenotype_profile(b)
    ca, cb = _closure(S, fa), _closure(S, fb)
    thr = graph.INFORMATIVE_IC
    det = []
    for x, fx, cy, y in ((a, fa, cb, b), (b, fb, ca, a)):
        uniq = sorted((t for t in fx if S.ic(t) >= thr and t not in cy), key=lambda t: (-S.ic(t), t))[:k]
        for t in uniq:
            det.append({"disease": S.node_label[x], "other": S.node_label[y], "id": t, "label": S.node_label[t],
                        "ic": round(S.ic(t), 2), "edge_id": fx[t][0], "kind": "phenotype"})
    ga, gb = S.gene_profile(a), S.gene_profile(b)
    for x, gx, gy, y in ((a, ga, gb, b), (b, gb, ga, a)):
        for g in sorted(set(gx) - set(gy))[:3]:
            det.append({"disease": S.node_label[x], "other": S.node_label[y], "id": g, "label": S.node_label[g],
                        "edge_id": gx[g][0], "kind": "gene"})
    strings = []
    for d in det:
        if d["kind"] == "phenotype":
            strings.append(f"Only {d['disease']}: phenotype '{d['label']}' (IC {d['ic']}) [{d['edge_id']}]")
        else:
            strings.append(f"Only {d['disease']}: gene {d['label']} [{d['edge_id']}]")
    return strings, det


def _evidence_summary(S, d, k=3):
    """Strongest curated facts about one disease (used to cite gap narratives)."""
    ev = []
    for g, (eid, _) in S.gene_profile(d).items():
        ev.append({"label": f"gene {S.node_label[g]}", "edge_id": eid, "w": 2.0})
    for t, (eid, _) in S.phenotype_profile(d).items():
        ev.append({"label": S.node_label[t], "edge_id": eid, "w": S.ic(t) / 10})
    if not ev:
        for t, eid, _ in S.inn(d, "studies")[:1] + S.inn(d, "mentions")[:1]:
            ev.append({"label": S.node_label[t][:80], "edge_id": eid, "w": 0})
    ev.sort(key=lambda x: -x["w"])
    return ev[:k]


def _gap(S, a, b, routes, co, assets, researchers, orgs) -> dict:
    meta = S.meta
    A, B = S.node_label[a], (S.node_label[b] if b else None)
    fa = S.phenotype_profile(a)
    ga = S.gene_profile(a)
    searched, missing = [], []
    if b is None:
        searched = [f"Disease~disease similarity over HPO phenotypes, Reactome pathways and PubMed co-mentions for {A}",
                    f"HPO annotations: {len(fa)} phenotype terms for {A}",
                    f"Orphanet + HPO gene-disease: {len(ga)} gene(s) for {A}",
                    f"PubMed corpus ({meta.get('papers_total', '?')} abstracts): {len(S.disease_papers(a))} mention {A}"]
        if not fa:
            missing.append(f"No HPO phenotype annotations for {A} (cannot compare symptoms)")
        if not ga:
            missing.append(f"No curated causal gene for {A} in Orphanet/HPO (no mechanism to compare)")
        missing.append("No other disease in the two groups passed the similarity threshold")
        return {"searched": searched, "missing": missing,
                "next_question": (f"Which symptoms or gene findings do {A} families actually see? Recording them (e.g. "
                                  f"in a registry) is what would let the atlas find mechanism neighbours.")}
    fb, gb = S.phenotype_profile(b), S.gene_profile(b)
    thr = graph.INFORMATIVE_IC
    shared_all = set(_closure(S, fa)) & set(_closure(S, fb))
    shared_inf = [t for t in shared_all if S.ic(t) >= thr]
    shared_broad = sorted((t for t in shared_all if 1.0 <= S.ic(t) < thr), key=lambda t: (-S.ic(t), t))
    searched = [
        f"HPO phenotype annotations: {A} {len(fa)} terms, {B} {len(fb)} terms; shared informative (IC>={thr:g}): "
        f"{len(shared_inf)}; shared broad only: {len(shared_broad)}"
        + (f" (e.g. {', '.join(S.node_label[t] for t in shared_broad[:3])})" if shared_broad else ""),
        f"Orphanet + HPO gene-disease links: {A} {len(ga)} gene(s), {B} {len(gb)} gene(s); shared: {len(set(ga) & set(gb))}",
        "Reactome pathways of those genes (pathways with <= "
        f"{graph.CFG['graph']['max_pathway_genes']} genes): shared {sum(1 for r in routes if r['kind'] == 'shared_pathway')}",
        f"PubMed corpus ({meta.get('papers_total', '?')} abstracts, MeSH + PubTator): {len(S.disease_papers(a))} on {A}, "
        f"{len(S.disease_papers(b))} on {B}, {len(co)} mention both",
        f"ClinicalTrials.gov ({meta.get('ctgov_studies_total', '?')} studies): {len(S.disease_trials(a))} on {A}, "
        f"{len(S.disease_trials(b))} on {B}, {sum(1 for x in assets if x['disease'] == 'both')} shared",
        f"Researchers with >= {graph.MIN_PAPERS} papers on each: {len(researchers)}",
        f"Patient organisations serving both: {sum(1 for o in orgs if o['serves_both'])}",
    ]
    if not fa or not fb:
        missing.append(f"No HPO phenotype annotations for {A if not fa else B}: symptoms cannot be compared")
    elif not shared_inf:
        missing.append("No specific (informative) phenotype in common; only broad symptoms are shared"
                       if shared_broad else "No phenotype in common")
    if not ga or not gb:
        missing.append(f"No curated gene for {A if not ga else B} in Orphanet/HPO: mechanisms cannot be compared")
    elif not set(ga) & set(gb) and not any(r["kind"] == "shared_pathway" for r in routes):
        missing.append("Genes differ and share no specific Reactome pathway")
    if len(co) < MIN_CO_PAPERS:
        missing.append(f"Only {len(co)} paper(s) in the corpus mention both diseases (need >= {MIN_CO_PAPERS})")
    if not researchers:
        missing.append("No researcher in the corpus has published on both")
    hint = S.node_label[shared_broad[0]] if shared_broad else None
    nq = (f"Is the shared broad symptom '{hint}' caused by the same process in {A} and {B}? A clinician who sees both "
          f"(or a joint registry query) could answer that." if hint else
          f"Is there any clinical or mechanistic overlap between {A} and {B} that is not yet published? Ask a patient "
          f"organisation or a researcher on {A} whether they have seen it.")
    return {"searched": searched, "missing": missing, "next_question": nq}


def _next_steps(S, a, b, routes, assets, researchers, orgs):
    A, B = S.node_label[a], S.node_label[b]
    steps = []
    both = [o for o in orgs if o["serves_both"]]
    if both:
        o = both[0]
        steps.append({"text": f"Contact {o['label']} ({o['url'] or 'no website listed'}): it serves both {A} and {B} "
                              f"families - propose a joint call on shared care and research priorities.",
                      "edge_ids": o["edge_ids"]})
    elif orgs:
        oa = next((o for o in orgs if o["disease_id"] == a), None)
        ob = next((o for o in orgs if o["disease_id"] == b), None)
        if oa and ob:
            steps.append({"text": f"Introduce {oa['label']} ({A}) to {ob['label']} ({B}) - the two communities have not "
                                  f"been linked in the sources searched.", "edge_ids": [oa["edge_id"], ob["edge_id"]]})
    if researchers:
        r = researchers[0]
        steps.append({"text": f"Ask {r['label']}{' (' + r['affiliation'][:80] + ')' if r['affiliation'] else ''}, who has "
                              f"{r['papers_a']} papers on {A} and {r['papers_b']} on {B}, whether the overlap below "
                              f"is clinically meaningful.", "edge_ids": r["edge_ids"][:4]})
    for x in assets[:2]:
        verb = {"both": "Check eligibility / outcome measures of", "a": "Share the protocol of", "b": "Learn from"}[x["disease"]]
        steps.append({"text": f"{verb} {x['id']} ({x['kind'].replace('_', ' ')}, {str(x['status']).lower()}): it "
                              f"{x['relevance']}.", "edge_ids": x["edge_ids"]})
    ph = next((r for r in routes if r["kind"].startswith("shared_phenotype")), None)
    if ph:
        steps.append({"text": f"Propose '{S.node_label[ph['phenotype']]}' as a common outcome measure when collecting "
                              f"natural-history data for {A} and {B}.", "edge_ids": ph["edge_ids"]})
    mech = next((r for r in routes if r["kind"] in ("shared_gene", "shared_pathway")), None)
    if mech:
        steps.append({"text": f"Ask whether therapies being developed for {B} that act on this mechanism could apply to "
                              f"{A}: {mech['text']}.", "edge_ids": mech["edge_ids"]})
    return steps


def _funding(S, a, b, researchers):
    """Funding picture for A (and B): grants per disease, grants/PIs shared by both, funding gaps."""
    ga, gb = graph.disease_grants(S, a), (graph.disease_grants(S, b) if b else [])
    ida, idb = {g["id"]: g for g in ga}, {g["id"]: g for g in gb}
    shared = []
    for gid in sorted(set(ida) & set(idb)):
        if min(ida[gid]["confidence"], idb[gid]["confidence"]) < 0.7:   # concept-term-only matches are too weak to cite
            continue
        g = dict(ida[gid])
        g["edge_ids"] = [ida[gid]["edge_id"], idb[gid]["edge_id"]]
        shared.append(g)
    shared.sort(key=lambda g: (not g["active"], -(g["fiscal_years"][-1] if g["fiscal_years"] else 0)))
    # PIs who lead a grant on A and a (different) grant on B
    def pis(gs):
        d = {}
        for g in gs:
            for r, eid, _ in S.inn(g["id"], "leads"):
                d.setdefault(r, []).append((g, eid))
        return d
    pa, pb = pis(ga), pis(gb)
    shared_pis = []
    for r in sorted(set(pa) & set(pb), key=lambda r: S.node_label[r]):
        at = S.attrs(r)
        shared_pis.append({"id": r, "label": S.node_label[r], "affiliation": at.get("affiliation", ""),
                           "grants_a": [x[0]["id"] for x in pa[r]][:3], "grants_b": [x[0]["id"] for x in pb[r]][:3],
                           "edge_ids": [pa[r][0][1], pb[r][0][1]]})
    act_a, act_b = sum(g["active"] for g in ga), sum(g["active"] for g in gb)
    def brief(gs, n_act, lab):
        return {"n_grants": len(gs), "n_active": n_act, "gap": n_act == 0, "label": lab,
                "top": [{k: g[k] for k in ("id", "title", "pi", "org", "years", "amount", "url", "edge_id", "active")}
                        for g in gs[:5]]}
    gap_notes = []
    if act_a == 0:
        gap_notes.append(f"No active NIH grant names {S.node_label[a]} (RePORTER, FY2005-2026; {len(ga)} past grant(s)).")
    if b and act_b == 0:
        gap_notes.append(f"No active NIH grant names {S.node_label[b]} (RePORTER, FY2005-2026; {len(gb)} past grant(s)).")
    return {"a": brief(ga, act_a, S.node_label[a]), "b": brief(gb, act_b, S.node_label[b]) if b else None,
            "shared_grants": shared[:6], "shared_pis": shared_pis[:6], "gap_a": act_a == 0, "gap_b": bool(b) and act_b == 0,
            "gap_notes": gap_notes, "source": "NIH RePORTER v2 (US NIH grants only; other funders not covered)"}


def _readiness(S, plan, routes, co, path_assess) -> dict:
    """strong | moderate | weak | none, with the reasons. Built from route kinds, edge stances and counter-evidence."""
    if not plan["supported"]:
        return {"level": "none", "reasons": ["No route met the evidence rule (shared gene, pathway, specific phenotype or "
                                              f">= {MIN_CO_PAPERS} co-mention papers)."]}
    reasons = []
    kinds = {r["kind"] for r in routes}
    mech = bool(kinds & {"shared_gene", "shared_pathway"})
    n_kinds = len({("mechanism" if k in ("shared_gene", "shared_pathway") else "phenotype" if k.startswith("shared_phenotype")
                    else "literature") for k in kinds})
    stances = [x["summary"]["stance"] for x in path_assess]
    mixed = sum(1 for x in stances if x == "mixed")
    weak = sum(1 for x in stances if x in ("weak", "unsupported"))
    contra = [f for x in path_assess for f in x["contradicting"] if f["weight"] >= graph.COUNTS_AS_CONTRA]
    reasons.append(("Mechanism-level link (shared gene or Reactome pathway)" if mech else
                    "Link is phenotypic/literature-based only (no shared gene or pathway)"))
    reasons.append(f"{n_kinds} independent kind(s) of evidence ({', '.join(sorted(kinds))})")
    if len(co) >= MIN_CO_PAPERS:
        reasons.append(f"{len(co)} PubMed papers mention both diseases")
    elif len(co) == 0:
        reasons.append("no paper in the corpus mentions both diseases")
    if mixed:
        reasons.append(f"{mixed} edge(s) on the path have mixed evidence (counter-evidence flagged)")
    if contra:
        reasons.append(f"{len(contra)} possible counter-evidence item(s) on the path edges, e.g. {contra[0]['note'][:140]}")
    if weak:
        reasons.append(f"{weak} path edge(s) are weakly supported")
    sim = plan.get("similarity")
    if sim is not None and sim < graph.CFG["graph"]["min_similarity"]:
        reasons.append("overall similarity score is below the main threshold")
    stopped = [x for x in plan["shared_assets"] if str(x.get("status")).upper() in graph.STOP_STATUSES]
    if stopped:
        reasons.append(f"{len(stopped)} shared asset(s) are stopped trials (see their why_stopped)")
    if plan["shared_assets"] and not stopped:
        reasons.append(f"{len(plan['shared_assets'])} shared/reusable asset(s) exist")
    if mech and n_kinds >= 2 and not mixed and not contra and weak == 0:
        level = "strong"
    elif (mech or n_kinds >= 2) and mixed == 0 and weak <= 1:
        level = "moderate"
    elif contra or mixed >= 2 or not mech and n_kinds == 1 and len(co) == 0:
        level = "weak"
    else:
        level = "moderate" if mech else "weak"
    return {"level": level, "reasons": reasons}


def _funding_steps(S, a, b, funding):
    A, B = S.node_label[a], S.node_label[b]
    steps = []
    if funding["shared_grants"]:
        g = funding["shared_grants"][0]
        steps.append({"text": f"NIH grant {g['project_num']} ({g['years']}, {g['org']}) names both {A} and {B}: ask its PI "
                              f"({g['pi']}) how the two communities share the funded resources.", "edge_ids": g["edge_ids"]})
    elif funding["shared_pis"]:
        p = funding["shared_pis"][0]
        steps.append({"text": f"{p['label']} leads NIH grants on both {A} and {B}: a natural bridge to propose a joint "
                              f"natural-history or outcome-measure effort.", "edge_ids": p["edge_ids"]})
    for lab, side in ((A, funding["a"]), (B, funding["b"])):
        if side and side["top"] and not side["gap"]:
            g = side["top"][0]
            if g["active"]:
                e = S.edges.loc[g["edge_id"]]
                lead = next(((r, eid) for r, eid, _ in S.inn(g["id"], "leads")), None)
                steps.append({"text": f"Active NIH grant on {lab}: {g['title'][:110]} ({g['org']}, {g['years']}; PI {g['pi']}). "
                                      f"Its funded team is a candidate research partner.",
                              "edge_ids": [g["edge_id"]] + ([lead[1]] if lead else [])})
                break
    return steps[:2]


def action_plan(disease_a: str, disease_b: str | None = None) -> dict:
    S = graph.state()
    graph._require_disease(S, disease_a)
    a = disease_a
    sim_edge = None
    if disease_b is None:
        nb = graph.neighbours(a, k=10)
        disease_b = nb[0]["id"] if nb else None
        sim_edge = nb[0]["edge_id"] if nb else None
    else:
        graph._require_disease(S, disease_b)
        for u, v, eid, d in list(S.G.out_edges(a, keys=True, data=True)) + list(S.G.in_edges(a, keys=True, data=True)):
            if d["rel"] == "similar_to" and disease_b in (u, v):
                sim_edge = eid
    b = disease_b
    plan = {"a": {"id": a, "label": S.node_label[a], "group": S.node_group[a]},
            "b": ({"id": b, "label": S.node_label[b], "group": S.node_group[b]} if b else None),
            "supported": False, "path": [], "routes": [], "shared_assets": [], "researchers": [], "orgs": [],
            "differences": [], "differences_detail": [], "next_steps": [], "narrative": "", "gap": None,
            "similarity_edge_id": sim_edge,
            "similarity": (float(S.G.edges[S.edges.at[sim_edge, 'src'], S.edges.at[sim_edge, 'dst'], sim_edge]["conf"])
                           if sim_edge else None),
            "cross_group": bool(b and S.node_group[a] != S.node_group[b]),
            "evidence_a": _evidence_summary(S, a), "evidence_b": _evidence_summary(S, b) if b else [],
            "epidemiology": {"a": graph.epidemiology(S, a), "b": graph.epidemiology(S, b) if b else None},
            "funding": None, "readiness": {"level": "none", "reasons": ["No comparison disease selected."]}}
    plan["funding"] = _funding(S, a, b, [])
    if b is None:
        plan["orgs"] = [{**o, "serves_both": False, "edge_ids": [o["edge_id"]], "disease_label": S.node_label[a]}
                        for o in graph._orgs_for(S, a)][:8]
        plan["gap"] = _gap(S, a, None, [], [], [], [], [])
        plan["caveats"] = list(plan["funding"]["gap_notes"])
        plan["narrative"] = explain.narrative(plan)
        return plan
    if a == b:
        plan["gap"] = {"searched": [], "missing": ["A and B are the same disease"],
                       "next_question": "Pick a different disease to compare."}
        plan["readiness"] = {"level": "none", "reasons": ["A and B are the same disease."]}
        return plan
    routes, co = _routes(S, a, b)
    mech = [r for r in routes if r["kind"] in ("shared_gene", "shared_pathway", "shared_phenotype",
                                                "shared_phenotype_family")]
    supported = bool(mech) or len(co) >= MIN_CO_PAPERS
    assets = _assets(S, a, b)
    researchers = _researchers(S, a, b)
    orgs = _orgs(S, a, b)
    diffs, diff_det = _differences(S, a, b)
    primary = (mech or routes)[:1]
    plan.update({
        "supported": supported,
        "path": [_step(S, e) for e in primary[0]["edge_ids"]] if primary and supported else [],
        "routes": [{k: v for k, v in r.items()} | {"steps": [_step(S, e) for e in r["edge_ids"]]} for r in routes[:8]],
        "shared_assets": assets, "researchers": researchers, "orgs": orgs,
        "differences": diffs, "differences_detail": diff_det,
        "next_steps": _next_steps(S, a, b, routes, assets, researchers, orgs) if supported else [],
    })
    if not supported:
        plan["gap"] = _gap(S, a, b, routes, co, assets, researchers, orgs)
        if orgs:
            plan["next_steps"] = [{"text": f"Raise the open question with {o['label']}: {plan['gap']['next_question']}",
                                   "edge_ids": o["edge_ids"]} for o in orgs[:1]]
    plan["funding"] = _funding(S, a, b, researchers)
    path_assess = [graph.edge_assessment(e) for e in dict.fromkeys(primary[0]["edge_ids"])] if primary and supported else []
    plan["path_assessment"] = [{"edge_id": e, **{k: v[k] for k in ("summary", "confidence")}}
                               for e, v in zip(dict.fromkeys(primary[0]["edge_ids"]) if primary and supported else [], path_assess)]
    if supported:
        plan["next_steps"] = plan["next_steps"] + _funding_steps(S, a, b, plan["funding"])
    caveats = list(plan["funding"]["gap_notes"])
    for x in path_assess:
        for f in x["contradicting"]:
            if f["weight"] >= graph.COUNTS_AS_CONTRA and f["note"] not in caveats:
                caveats.append(f["note"])
    stopped = [x for x in assets if str(x.get("status")).upper() in graph.STOP_STATUSES]
    for x in stopped[:2]:
        why = S.attrs(x["id"]).get("why_stopped") or "no reason recorded"
        caveats.append(f"Shared asset {x['id']} is {str(x['status']).lower()} (why stopped: {why[:140]}); do not treat it as an "
                       f"open opportunity.")
    if sim_edge and plan["similarity"] is not None and plan["similarity"] < graph.CFG["graph"]["min_similarity"]:
        caveats.append("The overall similarity score is below the main threshold; treat B as a weak lead.")
    if supported and not any(r["kind"] in ("shared_gene", "shared_pathway") for r in routes):
        caveats.append("The link is phenotypic (shared specific symptoms), not a shared gene or pathway.")
    if supported and len(co) == 0:
        caveats.append("No paper in the corpus mentions both diseases: the link is not yet discussed in the literature.")
    plan["caveats"] = caveats
    plan["readiness"] = _readiness(S, plan, routes, co, path_assess)
    plan["narrative"] = explain.narrative(plan)
    return plan
