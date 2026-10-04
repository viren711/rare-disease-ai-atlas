#!/usr/bin/env python3
"""Smoke check of the graph lane through atlas.api (no LLM, no RAG): search, card, neighbours, edge, subgraph,
action plan Krabbe -> X-ALD, gap reports, timings and graph counts. Exit code 1 on a failed check."""
from __future__ import annotations

from collections import Counter
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas import api, explain, graph  # noqa: E402

FAIL = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


def round2(api, graph, K, X):
    """Round 2: grants, ClinVar, epidemiology, contradiction handling, readiness, new API functions."""
    S = graph.state()
    print("\nround 2: funding / variants / epidemiology / contradictions")
    st = api.stats()
    for t in ("Grant", "Variant"):
        check(st["nodes_by_type"].get(t, 0) > 0, f"{st['nodes_by_type'].get(t, 0)} {t} nodes")
    names = " ".join(x["name"] for x in st["sources"])
    for want in ("reporter", "clinvar", "en_product9_prev", "en_product9_ages"):
        check(want in names.lower() or want.replace("reporter", "RePORTER") in names, f"stats().sources lists {want}")
    f = api.funding(K)
    check(f["total"] >= 20 and f["total_active"] >= 1 and f["gap"] is False, f"funding(Krabbe): {f['total']} grants, "
                                                                           f"{f['total_active']} active, gap={f['gap']}")
    g0 = f["grants"][0]
    check(all(k in g0 for k in ("id", "title", "pi", "org", "years", "amount", "url", "edge_id")), "grant row has contract keys")
    check(api.edge(g0["edge_id"])["rel"] == "funds" and "FY" in api.edge(g0["edge_id"])["explanation"] + g0["years"],
          "funds edge resolves and cites project + fiscal year")
    gaps = [d for d, t in S.node_type.items() if t == "Disease" and api.funding(d)["gap"]]
    check(len(gaps) > 0, f"funding gaps surfaced for {len(gaps)} of 274 diseases, e.g. {[S.node_label[d] for d in gaps[:3]]}")
    check(bool(api.funding(gaps[0])["gap_note"]), "gap carries an explanatory note")
    v = api.variants("NCBIGene:2581")  # GALC
    check(v["counts"]["pathogenic"] > 50 and len(v["top"]) > 5 and v["top"][0]["stars"] >= 1, f"variants(GALC): {v['counts']}")
    check(all(x["stars"] >= 1 and "athogenic" in x["significance"] for x in v["top"]), "only >=1-star P/LP variants listed")
    vd = api.variants(K)
    check(vd["scope"] == "disease" and vd["top"], f"variants(Krabbe) -> {len(vd['top'])} top variants")
    check(api.edge(v["top"][0]["edge_id"])["source_url"].startswith("https://www.ncbi.nlm.nih.gov/clinvar/"), "variant edge -> ClinVar URL")
    card = api.disease_card(K)
    epi = card["epidemiology"]
    check(bool(epi["prevalence"]) and "Autosomal recessive" in epi["inheritance"] and epi["onset"], f"Krabbe epidemiology: {epi['prevalence']}, {epi['onset']}")
    check("funding" in card and "variants" in card, "card carries funding + variants briefs")
    rs = api.researchers_for(K, 8)
    check(len(rs) >= 5 and rs[0]["why"] and "contact" in rs[0] and rs == sorted(rs, key=lambda r: -r["score"]),
          f"researchers_for: {[(r['label'], r['score']) for r in rs[:3]]}")
    check(any(r["grants"] for r in rs), "some collaborators lead NIH grants")
    a = api.assets_for(K)
    check(a["trials"] and a["registries"] is not None and "models" in a, f"assets_for counts {a['counts']}")
    # --- contradiction handling
    e = S.edges
    stopped = [eid for eid in e[e.rel == "studies"].id if S.attrs(e.at[eid, "src"]).get("status") in graph.STOP_STATUSES
               and S.attrs(e.at[eid, "src"]).get("why_stopped")]
    check(bool(stopped), f"{len(stopped)} trial links carry a recorded why_stopped")
    eg = api.edge(stopped[0])
    tf = [c for c in eg["contradicting"] if c["kind"] == "trial_stopped"]
    check(bool(tf) and "why_stopped" in tf[0] and "Possible counter-evidence" in tf[0]["note"], "stopped trial -> labelled possible counter-evidence")
    print(f"   {eg['id']}: {eg['evidence_summary']} {eg['confidence_breakdown']}\n   {tf[0]['note'][:260]}")
    check(set(eg["evidence_summary"]) >= {"supporting", "contradicting", "stance"}
          and eg["evidence_summary"]["stance"] in ("supported", "mixed", "weak", "unsupported"), "evidence_summary shape")
    check(set(eg["confidence_breakdown"]) >= {"source_reliability", "n_independent_sources", "recency", "penalty"}, "confidence_breakdown keys")
    ge = api.edge(S.edges[(S.edges.rel == "causes")].id.iloc[0])
    check("evidence_summary" in ge, "gene-disease edge has evidence_summary")
    stances = Counter(api.edge(x)["evidence_summary"]["stance"] for x in e[e.rel == "similar_to"].id.iloc[:80])
    check(len(stances) >= 2, f"similar_to stances vary: {dict(stances)}")
    neg = [p for p, t in S.node_type.items() if t == "Paper" and S.attrs(p).get("neg", {}) and S.attrs(p)["neg"].get("where") == "title"]
    check(len(neg) > 0, f"{len(neg)} papers with a negative-result title signal; {sum(1 for p, t in S.node_type.items() if t == 'Paper' and 'Retracted Publication' in (S.attrs(p).get('pub_types') or []))} retracted")
    mixed = [x for x in e[e.rel == "studies"].id if api.edge(x)["evidence_summary"]["stance"] == "mixed"]
    check(len(mixed) > 0, f"{len(mixed)} trial links come out as 'mixed'")
    # --- plan
    t = time.time()
    p = api.action_plan(K, X)
    dt = time.time() - t
    check(p["readiness"]["level"] in ("strong", "moderate", "weak", "none") and p["readiness"]["reasons"],
          f"Krabbe->X-ALD readiness={p['readiness']['level']}: {p['readiness']['reasons'][:3]}")
    fu = p["funding"]
    check(fu["a"]["n_active"] >= 1 and "shared_grants" in fu and "shared_pis" in fu, f"plan funding: A active={fu['a']['n_active']} B active={fu['b']['n_active']} shared grants={len(fu['shared_grants'])}")
    check(p["epidemiology"]["a"]["prevalence"] and p["epidemiology"]["b"]["prevalence"], "plan epidemiology for A and B")
    check(any("NIH grant" in s["text"] for s in p["next_steps"]), "next_steps cite NIH grants")
    check(all(set(s["edge_ids"]) <= set(S.edges.index) for s in p["next_steps"]), "all next-step edge ids exist")
    check(any("stopped" in c.lower() or "terminated" in c.lower() or "withdrawn" in c.lower() for c in p["caveats"]), "stopped shared trials listed as caveats")
    t = time.time(); api.disease_card(K); api.neighbours(K); dt2 = time.time() - t
    check(dt2 < 0.3, f"card + neighbours {dt2:.3f}s (< 0.3 s); plan {dt:.2f}s")
    print(f"   funding gap example plan: {api.action_plan(gaps[0])['caveats'][:1]}")


def main():
    t0 = time.time()
    st = api.stats()
    load = time.time() - t0
    print(f"load + stats: {load:.1f}s  (graph load {st['load_seconds']}s)")
    check(load < 15, "graph loads in < 15 s")
    print("nodes:", json.dumps(st["nodes_by_type"]))
    print("edges by evidence:", json.dumps(st["edges_by_evidence"]))
    print("edges by rel/evidence:", json.dumps(st["edges_by_rel"]))
    print("groups:", json.dumps(st["groups"]))

    print("\nsearch")
    expect = {"Krabbe": "MONDO:0009499", "GALC": "NCBIGene:2581", "seizures": "HP:0001250", "X-ALD": "MONDO:0018544",
              "Niemann Pick C": "MONDO:0018982", "Krabe disese": "MONDO:0009499", "Hunter syndrome": "MONDO:0010674"}
    for q, want in expect.items():
        r = api.search(q, k=5)
        top = r[0]["id"] if r else None
        check(top == want, f"search({q!r}) -> {r[0]['label'] if r else None} ({top})")
    check(api.search("zzqqxx") == [], "nonsense query returns []")

    K, X = "MONDO:0009499", "MONDO:0018544"
    t = time.time()
    card = api.disease_card(K)
    nb = api.neighbours(K)
    dt = time.time() - t
    check(dt < 1.0, f"card + neighbours in {dt:.2f}s (< 1 s)")
    print(f"  card: {card['label']} genes={[g['label'] for g in card['genes']]} counts={card['counts']}")
    print(f"  top informative phenotypes: {[p['label'] for p in card['phenotypes'] if p['informative']][:6]}")
    print(f"  cluster: {card['cluster']['label']} ({len(card['cluster']['members'])} other members)")
    print(f"  orgs: {[o['label'] for o in card['orgs']][:6]}")
    for n in nb:
        w = n["why"]
        print(f"   {n['score']:.3f} {n['label']} [{n['group']}{', cross-group' if n['cross_group'] else ''}"
              f"{', weak' if n['weak'] else ''}] phen={len(w['shared_phenotypes'])} pw={len(w['shared_pathways'])} "
              f"genes={len(w['shared_genes'])} co-papers={w['co_mention_papers']} researchers={w['shared_researchers']} "
              f"trials={w['shared_trials']}")
    labels = {n["label"] for n in nb}
    check(any("metachromatic leukodystrophy" in l for l in labels), "MLD among Krabbe neighbours")
    check(any("adrenoleukodystrophy" in l for l in labels), "an adrenoleukodystrophy entry among Krabbe neighbours")
    try:
        api.disease_card("MONDO:0000000")
        check(False, "unknown id raises KeyError")
    except KeyError:
        check(True, "unknown id raises KeyError")

    e = api.edge(nb[0]["edge_id"])
    print(f"\nedge {e['id']}: {e['explanation'][:240]}")
    check(e["evidence_type"] == "inferred" and e["rel"] == "similar_to", "similar_to edge is inferred")
    ge = api.edge(card["genes"][0]["edge_id"])
    print(f"edge {ge['id']}: {ge['explanation']}  url={ge['source_url']}  papers={len(ge['supporting_papers'])}")
    sg = api.subgraph(K, depth=1, max_nodes=60)
    check(0 < len(sg["nodes"]) <= 60, f"subgraph: {len(sg['nodes'])} nodes, {len(sg['edges'])} edges")

    print("\naction_plan(Krabbe, X-ALD)")
    t = time.time()
    p = api.action_plan(K, X)
    check(time.time() - t < 2, f"action plan in {time.time() - t:.2f}s")
    check(p["supported"], f"supported={p['supported']} similarity={p['similarity']} caveats={p['caveats']}")
    for s in p["path"]:
        print(f"   path: {s['src_label']} -[{s['rel']}, {s['evidence_type']}, {s['source']}]-> {s['dst_label']} ({s['edge_id']})")
    for r in p["routes"]:
        print(f"   route {r['kind']} {r['strength']:.2f}: {r['text'][:150]}")
    for a in p["shared_assets"][:8]:
        print(f"   asset {a['id']} {a['kind']} {a['status']}: {a['relevance'][:110]}")
    for r in p["researchers"][:6]:
        print(f"   researcher {r['label']} ({r['affiliation'][:60]}) A={r['papers_a']} B={r['papers_b']} "
              f"orcid={r['orcid'] or '-'} homonym_risk={r['homonym_risk']}")
    for o in p["orgs"][:6]:
        print(f"   org {o['label']} serves_both={o['serves_both']} {o['url']}")
    for s in p["next_steps"]:
        print(f"   next: {s['text'][:160]} {s['edge_ids']}")
    print(f"   narrative: {p['narrative']}")
    valid = set(graph.state().edges.index)
    check(explain.check_citations(p["narrative"], valid), "every narrative sentence cites a real edge id")
    cited = set(re.findall(r"\[(e\d+)\]", p["narrative"]))
    check(cited <= valid, f"{len(cited)} cited edge ids all exist")

    print("\ndefault neighbour plan for Krabbe")
    p2 = api.action_plan(K)
    print(f"   b={p2['b']['label'] if p2['b'] else None} supported={p2['supported']}")

    print("\ngap reports")
    S = graph.state()
    sparse = [d for d, t in S.node_type.items() if t == "Disease" and not S.phenotype_profile(d) and not S.gene_profile(d)
              and not S.disease_papers(d)]
    print(f"   diseases with no phenotypes, genes or papers: {len(sparse)} e.g. {[S.node_label[d] for d in sparse[:4]]}")
    if sparse:
        g = api.action_plan(sparse[0])
        check(not g["supported"] and g["gap"], f"no-neighbour plan for {S.node_label[sparse[0]]} -> gap report")
        print(f"   {json.dumps(g['gap'], indent=1)}")
        g2 = api.action_plan(K, sparse[0])
        check(not g2["supported"] and g2["gap"], f"Krabbe vs {S.node_label[sparse[0]]} -> gap report")
        print(f"   missing: {g2['gap']['missing']}\n   next_question: {g2['gap']['next_question']}")
        print(f"   narrative: {g2['narrative']}")
    # a pair of data-rich diseases with no supported route
    acat = "MONDO:0013571"  # acatalasia
    g3 = api.action_plan(acat, "MONDO:0010526")  # Fabry disease
    print(f"   acatalasia vs Fabry: supported={g3['supported']}")
    if g3["gap"]:
        print("   searched:\n     " + "\n     ".join(g3["gap"]["searched"]))
        print(f"   missing: {g3['gap']['missing']}")
        print(f"   narrative: {g3['narrative']}")
    round2(api, graph, K, X)
    print(f"\n{'ALL CHECKS PASSED' if not FAIL else 'FAILED: ' + '; '.join(FAIL)}  ({time.time() - t0:.1f}s)")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
