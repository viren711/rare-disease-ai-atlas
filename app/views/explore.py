"""Explore: one search box -> a disease (or gene, symptom, mechanism) -> similar diseases and a map.

Progressive reveal, top to bottom:
  search      always visible in the main panel; synonyms are resolved and said out loud
  summary     disease card: causes, distinctive vs common symptoms, mechanisms, patient groups
  depth       "Similar diseases" (each with why) or "Map" (click a node to expand it)
  evidence    the inspector on the right explains whichever connection was last opened
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backend, sections, theme, ui  # noqa: E402

LOOK_FOR = {"Anything": None, "Disease": ["Disease"], "Gene": ["Gene"], "Symptom": ["Phenotype"],
            "Mechanism": ["Pathway"], "Patient group": ["PatientOrg"]}
MAP_TYPES_DEFAULT = ["Disease", "Gene", "Phenotype", "Pathway", "Trial", "PatientOrg"]
VIEWS = ["Similar diseases", "Map", "Funding", "Variants", "Collaborators", "Assets"]
EXAMPLES = ["Krabbe disease", "globoid cell leukodystrophy", "ABCD1", "Leukodystrophy",
            "Glycosphingolipid metabolism"]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def set_focus(node_id: str, label: str, ntype: str) -> None:
    st.session_state["focus"] = {"id": node_id, "label": label, "type": ntype}
    st.session_state["map_expanded"] = []
    st.session_state["map_selected"] = None
    st.session_state.pop("map_edge_pick", None)


def _on_pick() -> None:
    pick = st.session_state.get("explore_pick")
    for r in st.session_state.get("explore_results", []):
        if r["id"] == pick:
            set_focus(r["id"], r["label"], r["type"])


def _use_example() -> None:
    ex = st.session_state.get("explore_example")
    if ex:
        st.session_state["explore_query"] = ex
        st.session_state["explore_q"] = ex
        st.session_state["explore_new_query"] = True
    st.session_state["explore_example"] = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_bar() -> None:
    if st.session_state.get("explore_new_query"):
        st.session_state["explore_q"] = st.session_state.get("explore_query", "")
    with st.form("explore_search", border=False, enter_to_submit=True):
        box, kind, btn = st.columns([5, 1.6, 1], vertical_alignment="bottom")
        q = box.text_input("Search a disease, gene, symptom or mechanism", key="explore_q",
                           placeholder="e.g. Krabbe disease, GALC, leukodystrophy")
        kind.selectbox("Look for", list(LOOK_FOR), key="explore_kind")
        if btn.form_submit_button("Search", type="primary", width="stretch", icon=":material/search:"):
            st.session_state["explore_query"] = q.strip()
            st.session_state["explore_new_query"] = True

    query = st.session_state.get("explore_query", "")
    if st.session_state.pop("explore_new_query", False) and query:
        with st.spinner("Searching names, synonyms and symptoms..."):
            results = backend.search(query, types=LOOK_FOR[st.session_state.get("explore_kind", "Anything")], k=10)
        st.session_state["explore_results"] = results
        st.session_state["explore_searched"] = query
        if results:
            set_focus(results[0]["id"], results[0]["label"], results[0]["type"])
            st.session_state["explore_pick"] = results[0]["id"]
        else:
            st.session_state.pop("focus", None)

    results = st.session_state.get("explore_results", [])
    searched = st.session_state.get("explore_searched")
    focus = st.session_state.get("focus")
    if searched and not results:
        st.warning(f"Nothing in the atlas matches **{ui.esc(searched)}**. Try another spelling, a gene symbol "
                   f"(e.g. GALC) or a symptom. The atlas currently covers lysosomal and peroxisomal diseases.",
                   icon=":material/search_off:")
        st.pills("Or try", EXAMPLES, key="explore_example", on_change=_use_example)
    elif results and focus:
        top = next((r for r in results if r["id"] == focus["id"]), None)
        if top and top.get("matched_text") and top["matched_text"].lower() != top["label"].lower():
            st.markdown(f"“{ui.esc(top['matched_text'])}” is another name for **{ui.esc(top['label'])}** "
                        f"({ui.type_label(top['type']).lower()}).")
        if len(results) > 1:
            if st.session_state.get("explore_pick") not in [r["id"] for r in results]:
                st.session_state["explore_pick"] = focus["id"]
            st.selectbox(
                "Not what you meant?", [r["id"] for r in results], key="explore_pick", on_change=_on_pick,
                format_func=lambda i: next(
                    f"{r['label']}  ·  {ui.type_label(r['type'])}"
                    + (f"  (matched “{r['matched_text']}”)" if r.get("matched_text")
                       and r["matched_text"].lower() != r["label"].lower() else "")
                    for r in results if r["id"] == i),
            )
    if not focus and not (searched and not results):
        st.pills("Try", EXAMPLES, key="explore_example", on_change=_use_example)


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------


def evidence_button(edge_id: str | None, key: str, label: str = "Evidence") -> None:
    if edge_id:
        st.button(label, key=key, type="tertiary", icon=":material/fact_check:", on_click=ui.inspect,
                  args=(edge_id,), help="Show the source and confidence for this link")


def linked_row(html: str, edge_id: str | None, key: str, compact: bool = True) -> None:
    """A chip plus its evidence button. Compact = icon-only button (tooltip says what it does)."""
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown(html, unsafe_allow_html=True, width="content")
        if compact and edge_id:
            st.button(":material/fact_check:", key=key, type="tertiary", on_click=ui.inspect, args=(edge_id,),
                      help="Evidence: where this link comes from and how sure the atlas is")
        else:
            evidence_button(edge_id, key)


# ---------------------------------------------------------------------------
# Disease card (summary first)
# ---------------------------------------------------------------------------


def disease_header(card: dict) -> None:
    st.markdown(f"### {ui.esc(card['label'])}")
    parts = [ui.type_chip("Disease"), ui.plain_chip(ui.group_label(card.get("group")))]
    cl = card.get("cluster") or {}
    if cl.get("label"):
        parts.append(ui.plain_chip(f"Cluster: {cl['label']}", "neutral",
                                   "Diseases grouped by shared symptoms and biology, not by name"))
    ui.chips(parts)
    if card.get("synonyms"):
        st.caption("Also called: " + ", ".join(card["synonyms"][:6]))
    if card.get("definition"):
        st.markdown(f"<div class='ra-big'>{ui.gloss(ui.esc(card['definition']))}</div>", unsafe_allow_html=True)
    ui.explain_simply("explore", card["id"], None, kind="disease",
                      technical_html=ui.gloss(ui.esc(card.get("definition") or "No definition recorded."))
                      + "<br><span class='ra-sub'>Technical view: the definition above plus the sourced links "
                        "below. Open Action plan for the cited narrative.</span>")
    sections.epidemiology_block(card["id"], card.get("epidemiology"))
    c = card.get("counts") or {}
    names = [("papers", "papers"), ("trials", "trials"), ("registries", "registries"),
             ("natural_history", "natural-history studies"),
             ("researchers", "researchers"), ("orgs", "patient groups")]
    st.markdown("<span class='ra-sub'>" + " · ".join(f"<b>{c[k]:,}</b> {n}" for k, n in names if k in c)
                + "</span>", unsafe_allow_html=True)


def community_block(card: dict, first: bool) -> None:
    """Devon's question: is there a patient group for this exact diagnosis? If not, the closest ones."""
    st.markdown("##### :material/diversity_3: Patient groups")
    orgs = card.get("orgs") or []
    if orgs:
        for i, o in enumerate(orgs):
            link = f"[{ui.esc(o['label'])}]({o['url']})" if o.get("url") else ui.esc(o["label"])
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(ui.dot("PatientOrg") + link, unsafe_allow_html=True, width="content")
                evidence_button(o.get("edge_id"), f"org_ev_{i}")
        return
    st.info("The atlas has no patient group recorded for this exact diagnosis.", icon=":material/info:")
    near = []
    for n in backend.neighbours(card["id"], k=3):
        try:
            for o in backend.disease_card(n["id"]).get("orgs") or []:
                near.append((o, n))
        except (KeyError, backend.DemoUnavailable):
            continue
    if near:
        st.markdown("Closest related communities (they support families with a similar disease):")
        for o, n in near[:5]:
            link = f"[{ui.esc(o['label'])}]({o['url']})" if o.get("url") else ui.esc(o["label"])
            st.markdown(f"- {link} — {ui.esc(n['label'])}")
    if first:
        st.caption("No group yet? Families often start one with help from a national rare-disease alliance; "
                   "the related groups above may share their registry or tools.")


def disease_summary(card: dict) -> None:
    did = card["id"]
    genes, phen, paths = card.get("genes") or [], card.get("phenotypes") or [], card.get("pathways") or []
    informative = [p for p in phen if p.get("informative")]
    common = [p for p in phen if not p.get("informative")]

    left, right = st.columns([2, 3], gap="medium")
    with left:
        st.markdown("##### :material/genetics: What causes it")
        if not genes:
            st.caption("No causal gene recorded.")
        for i, g in enumerate(genes):
            linked_row(ui.type_chip("Gene", g["label"], g["id"]), g.get("edge_id"), f"gene_ev_{did}_{i}")
        st.markdown("##### :material/hub: Mechanisms")
        if not paths:
            st.caption("No specific biological process recorded.")
        for i, p in enumerate(paths[:6]):
            linked_row(ui.type_chip("Pathway", p["label"],
                                    f"Through the gene {p['via_gene']}" if p.get("via_gene") else p["id"]),
                       p.get("edge_id"), f"path_ev_{did}_{i}")
    with right:
        st.markdown("##### :material/personal_injury: Distinctive symptoms")
        st.caption("Rare across diseases, so sharing one is meaningful.")
        if not informative:
            st.caption("None recorded; only common symptoms are known.")
        for i, p in enumerate(informative[:8]):
            linked_row(ui.type_chip("Phenotype", p["label"], f"Rare across diseases (IC {p.get('ic') or 0:.1f})"),
                       p.get("edge_id"), f"phen_ev_{did}_{i}")
        if common:
            with st.expander(f"Common symptoms ({len(common)}): seen in many diseases, weak evidence of a "
                             "shared cause"):
                for i, p in enumerate(common[:20]):
                    linked_row(ui.type_chip("Phenotype", p["label"], f"Seen in many diseases (IC {p.get('ic') or 0:.1f})"),
                               p.get("edge_id"), f"com_ev_{did}_{i}")

    with st.expander("Show technical detail"):
        st.caption(f"Disease id `{did}` · [{ui.node_url(did) or 'no link'}]({ui.node_url(did) or '#'})")
        st.caption("Symptom information content (IC): " + ", ".join(
            f"{p['label']} {p.get('ic', 0):.1f}" for p in phen[:15]))


# ---------------------------------------------------------------------------
# Similar diseases (with why)
# ---------------------------------------------------------------------------


def why_lines(why: dict) -> list[str]:
    lines = []
    sp = why.get("shared_phenotypes") or []
    def informative(p: dict) -> bool:
        return bool(p["informative"]) if "informative" in p else (p.get("ic") or 0) >= 4.0

    rare = [p["label"] for p in sp if informative(p)]
    common = [p["label"] for p in sp if not informative(p)]
    if rare:
        lines.append(f"**Shares distinctive symptoms:** {', '.join(rare[:4])}"
                     + (f" (+{len(rare) - 4})" if len(rare) > 4 else ""))
    if common:
        lines.append(f"Also shares common symptoms: {', '.join(common[:4])}"
                     + (f" (+{len(common) - 4})" if len(common) > 4 else ""))
    if why.get("shared_pathways"):
        lines.append("**Same mechanism:** " + ", ".join(p["label"] for p in why["shared_pathways"][:3]))
    if why.get("shared_genes"):
        lines.append("**Same gene:** " + ", ".join(g["label"] for g in why["shared_genes"][:3]))
    extra = []
    if why.get("shared_trials"):
        extra.append(f"{why['shared_trials']} shared stud{'y' if why['shared_trials'] == 1 else 'ies'}")
    if why.get("shared_researchers"):
        extra.append(f"{why['shared_researchers']} shared researcher{'s' if why['shared_researchers'] > 1 else ''}")
    if why.get("co_mention_papers"):
        extra.append(f"{why['co_mention_papers']} paper{'s' if why['co_mention_papers'] > 1 else ''} mention both")
    if extra:
        lines.append(" · ".join(extra))
    if not lines:
        lines.append("Linked by the similarity model only; no specific shared feature recorded.")
    return lines


def neighbours_view(focus: dict) -> None:
    other_only = st.toggle("Only diseases from a different family", key="nb_cross_only",
                           help="The dots nobody connected: diseases with a different name and category that "
                                "share symptoms or mechanism.")
    nbs = backend.neighbours(focus["id"], k=40 if other_only else 8)
    if other_only:
        nbs = [n for n in nbs if n.get("cross_group")][:8]
        if not nbs:
            st.info("No disease from a different family is linked to this one in the atlas. Turn the filter off "
                    "to see related diseases in the same family.", icon=":material/report:")
            return
    if not nbs:
        st.info("No disease in the atlas shares enough distinctive symptoms or biology with this one to be "
                "called similar. That is a gap in what is known, not proof there is no connection.",
                icon=":material/report:")
        return
    st.caption("Ranked by shared biology and symptoms, not by name. Dotted = a hypothesis the atlas computed.")
    for i, n in enumerate(nbs):
        why = n.get("why") or {}
        with st.container(border=True):
            parts = [f"<b class='ra-big'>{ui.esc(n['label'])}</b>&nbsp;", ui.plain_chip(ui.group_label(n.get("group")))]
            if n.get("cross_group"):
                txt = ("Different disease family, same mechanism" if why.get("shared_pathways")
                       else "Different disease family, shared symptoms")
                parts.append(ui.plain_chip(txt, "cross", "Organised by name these would never meet."))
            parts.append(ui.ev_chip("inferred", n.get("score")))
            ui.chips(parts)
            st.markdown("  \n".join(why_lines(why)))
            with st.container(horizontal=True, gap="small"):
                st.button("Open", key=f"nb_open_{i}", icon=":material/open_in_new:", on_click=set_focus,
                          args=(n["id"], n["label"], "Disease"))
                evidence_button(n.get("edge_id"), f"nb_ev_{i}", "Why linked?")
                if st.button("Plan with this", key=f"nb_plan_{i}", icon=":material/route:", type="tertiary"):
                    ui.go("plan", plan_a={"id": focus["id"], "label": focus["label"]},
                          plan_b={"id": n["id"], "label": n["label"]})


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------


def _merge(graphs: list[dict]) -> dict:
    nodes, edges = {}, {}
    for g in graphs:
        for n in g.get("nodes", []):
            nodes[n["id"]] = n
        for e in g.get("edges", []):
            edges[e["id"]] = e
    edges = {k: e for k, e in edges.items() if e["src"] in nodes and e["dst"] in nodes}
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def _short(s: str, n: int = 26) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def map_view(focus: dict) -> None:
    from streamlit_agraph import Config, Edge, Node, agraph

    c1, c2, c3 = st.columns([3, 2.2, 1], vertical_alignment="bottom")
    types = c1.multiselect("Show", list(ui.NODE_TYPES), default=MAP_TYPES_DEFAULT, key="map_types",
                           format_func=ui.type_label)
    depth = c2.segmented_control("Reach", [1, 2], default=1, key="map_depth",
                                 format_func=lambda d: "Direct links" if d == 1 else "Two steps") or 1
    if c3.button("Reset", key="map_reset", icon=":material/restart_alt:", width="stretch"):
        st.session_state["map_expanded"] = []
        st.session_state["map_selected"] = None

    types = types or None
    with st.spinner("Drawing the map..."):
        graphs = [backend.subgraph(focus["id"], depth=depth, max_nodes=45, types=types)]
        for nid in st.session_state.get("map_expanded", []):
            graphs.append(backend.subgraph(nid, depth=1, max_nodes=20, types=types))
    g = _merge(graphs)
    if not g["nodes"]:
        st.info("Nothing to draw for this selection.")
        return

    expanded = set(st.session_state.get("map_expanded", []))
    selected = st.session_state.get("map_selected")
    pal = theme.palette()
    nodes = []
    for n in g["nodes"]:
        is_focus = n["id"] == focus["id"]
        hexc = ui.type_hex(n["type"])
        nodes.append(Node(
            id=n["id"], label=_short(n.get("label") or n["id"]),
            title=f"{ui.esc(ui.type_label(n['type']))}: {ui.esc(n.get('label'))}",
            shape=ui.NODE_SHAPES.get(n["type"], "dot"),
            color={"background": hexc,
                   "border": pal["node_border"] if (is_focus or n["id"] == selected) else hexc,
                   "highlight": {"background": hexc, "border": pal["node_border"]},
                   "hover": {"background": hexc, "border": pal["node_border"]}},
            size=30 if is_focus else (20 if n["type"] == "Disease" else 14),
            borderWidth=4 if (is_focus or n["id"] in expanded or n["id"] == selected) else 1,
            font={"size": 15 if is_focus else 13, "color": pal["label"], "strokeWidth": 4,
                  "strokeColor": pal["label_stroke"]},
        ))
    edges = []
    for e in g["edges"]:
        name, _, _, dashes, _ = ui.EVIDENCE.get(e.get("evidence_type"), ui.EVIDENCE["inferred"])
        conf = e.get("confidence") or 0.5
        ec = ui.ev_hex(e.get("evidence_type"))
        edges.append(Edge(
            source=e["src"], target=e["dst"], color={"color": ec, "highlight": pal["accent"], "hover": pal["accent"], "opacity": 0.75},
            dashes=dashes, width=0.8 + 1.3 * float(conf),
            title=f"{ui.esc(ui.rel_label(e['rel']))} · {name} · confidence {float(conf):.0%}",
        ))
    config = Config(height=560, directed=False, physics=True, hierarchical=False,
                    interaction={"hover": True, "tooltipDelay": 120, "navigationButtons": False})
    config.width = "100%"
    config.edges = {"arrows": {"to": {"enabled": False}}, "smooth": {"type": "continuous"}}
    config.nodes = {"shadow": False}
    config.groups = {}  # agraph defaults (arrows "none", groups null) are rejected by vis-network
    # spread the ego graph out so labels stay readable
    config.physics = {"enabled": True, "solver": "barnesHut",
                      "barnesHut": {"gravitationalConstant": -9000, "centralGravity": 0.25,
                                    "springLength": 150, "springConstant": 0.03, "avoidOverlap": 0.6},
                      "stabilization": {"enabled": True, "iterations": 300, "fit": True},
                      "minVelocity": 1, "maxVelocity": 60, "timestep": 0.5}

    sig = hashlib.md5("|".join(sorted(n["id"] for n in g["nodes"])).encode()).hexdigest()[:10]
    with st.container(border=True, key="map_canvas"):
        clicked = agraph(nodes=nodes, edges=edges, config=config)
        clicked = st.session_state.pop("_test_map_click", None) or clicked  # hook for scripts/ui_test.py
        ui.legend(types or list(ui.NODE_TYPES))
    st.caption(f"{len(g['nodes'])} things, {len(g['edges'])} links. Click a dot to open its links; "
               "line style shows how we know each link.")

    if clicked and (sig, clicked) != st.session_state.get("_map_last_click"):
        st.session_state["_map_last_click"] = (sig, clicked)
        st.session_state["map_selected"] = clicked
        if clicked != focus["id"] and clicked not in expanded:
            st.session_state["map_expanded"] = list(expanded) + [clicked]
        st.rerun()

    labels = {n["id"]: n.get("label") or n["id"] for n in g["nodes"]}
    types_of = {n["id"]: n["type"] for n in g["nodes"]}
    if selected and selected in labels:
        with st.container(border=True):
            ui.chips([ui.type_chip(types_of[selected]), f"<b>{ui.esc(labels[selected])}</b>"])
            touching = [e for e in g["edges"] if selected in (e["src"], e["dst"])]
            touching.sort(key=lambda e: -(e.get("confidence") or 0))
            for i, e in enumerate(touching[:10]):
                other = e["dst"] if e["src"] == selected else e["src"]
                html = (f"{ui.esc(labels[e['src']])} <span class='ra-rel'>{ui.esc(ui.rel_label(e['rel']))}</span> "
                        f"{ui.esc(labels[e['dst']])} " + ui.ev_chip(e.get("evidence_type")))
                linked_row(html, e["id"], f"sel_ev_{i}_{other}", compact=False)
            if len(touching) > 10:
                st.caption(f"... and {len(touching) - 10} more links")
            if selected != focus["id"]:
                st.button(f"Make {_short(labels[selected], 30)} the centre", key="map_recentre",
                          icon=":material/center_focus_strong:", on_click=set_focus,
                          args=(selected, labels[selected], types_of[selected]))

    def _pick_edge() -> None:
        ui.inspect(st.session_state.get("map_edge_pick"))

    st.selectbox(
        "Inspect any link on the map", [e["id"] for e in g["edges"]], index=None, key="map_edge_pick",
        placeholder="Choose a link to see its evidence", on_change=_pick_edge,
        format_func=lambda eid: next(f"{labels[e['src']]} → {ui.rel_label(e['rel'])} → {labels[e['dst']]}"
                                     for e in g["edges"] if e["id"] == eid),
    )


# ---------------------------------------------------------------------------
# Non-disease focus: gene, symptom, mechanism, patient group
# ---------------------------------------------------------------------------


def node_summary(focus: dict) -> None:
    st.markdown(f"### {ui.esc(focus['label'])}")
    url = ui.node_url(focus["id"])
    ui.chips([ui.type_chip(focus["type"])])
    if url:
        st.caption(f"[{focus['id']}]({url})")
    g = backend.subgraph(focus["id"], depth=2, max_nodes=80, types=["Disease", "Gene", "Pathway", "Phenotype"])
    diseases = [n for n in g["nodes"] if n["type"] == "Disease" and n["id"] != focus["id"]]
    noun = {"Pathway": "this mechanism", "Gene": "this gene", "Phenotype": "this symptom"}.get(focus["type"],
                                                                                               "this")
    st.markdown(f"##### Diseases linked to {noun} ({len(diseases)})")
    if not diseases:
        st.info("No disease in the atlas is linked to this yet.", icon=":material/report:")
    groups = {n.get("group") for n in diseases}
    if len(groups) > 1:
        st.markdown(ui.plain_chip("Spans different disease families", "cross",
                                  "Same mechanism or symptom across families named differently"),
                    unsafe_allow_html=True)
    for i, n in enumerate(diseases[:20]):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown(ui.type_chip("Disease", n["label"]) + ui.plain_chip(ui.group_label(n.get("group"))),
                        unsafe_allow_html=True, width="content")
            st.button("Open", key=f"node_open_{i}", type="tertiary", icon=":material/open_in_new:",
                      on_click=set_focus, args=(n["id"], n["label"], "Disease"))


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def body() -> None:
    ui.tour_card(1)
    search_bar()
    focus = st.session_state.get("focus")
    if not focus:
        if not st.session_state.get("explore_searched"):
            ui.empty_state("Search for a disease, a gene, a symptom or a mechanism.",
                           "Everything opens the same card and map. Not sure where to start? Try Krabbe disease.")
        return

    main, side = st.columns([5, 3], gap="large")
    with side:
        ui.edge_inspector("explore")
    with main:
        persona = st.session_state.get("persona")
        if focus["type"] == "Disease":
            with st.spinner("Loading the disease card..."):
                card = backend.disease_card(focus["id"])
            ui.remember(card["id"], card["label"])
            disease_header(card)
            if persona == "family":
                community_block(card, first=True)
            disease_summary(card)
            if persona != "family":
                community_block(card, first=False)
            st.divider()
            default = "Map" if persona == "scout" else "Similar diseases"
            view = st.segmented_control("View", VIEWS, default=default,
                                        key="explore_view", label_visibility="collapsed") or default
            if view == "Similar diseases":
                neighbours_view(focus)
            elif view == "Map":
                map_view(focus)
            else:
                with st.spinner(f"Loading {view.lower()}..."):
                    {"Funding": sections.funding_section, "Variants": sections.variants_section,
                     "Collaborators": sections.collaborators_section,
                     "Assets": sections.assets_section}[view](focus["id"])
            with st.container(horizontal=True, gap="small"):
                ui.nav_button("Build an action plan", "plan", key="to_plan", icon=":material/route:",
                              type="primary", plan_a={"id": card["id"], "label": card["label"]}, plan_b=None)
                ui.nav_button("Ask the literature", "ask", key="to_ask", icon=":material/menu_book:",
                              ask_disease={"id": card["id"], "label": card["label"]})
        else:
            node_summary(focus)
            st.divider()
            map_view(focus)


ui.page(":material/travel_explore: Explore", "Search once; see what a disease shares with others, and why.", body,
        name="Explore", show_legend=True)
