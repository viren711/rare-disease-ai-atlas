"""Home: one search box, three ways in (by who you are), and what the atlas covers."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backend, ui  # noqa: E402

EXAMPLES = ["Krabbe disease", "globoid cell leukodystrophy", "ABCD1", "Leukodystrophy",
            "Glycosphingolipid metabolism"]

PERSONAS = [
    {
        "key": "leader",
        "icon": ":material/groups:",
        "title": "I lead a patient group",
        "who": "Like Maria",
        "body": "See which diseases share your disease's symptoms or biology, which studies and registries "
                "already exist, who works on both, and get a sourced proposal to send to a partner.",
        "cta": "Build an action plan",
        "page": "plan",
    },
    {
        "key": "family",
        "icon": ":material/family_restroom:",
        "title": "We were just diagnosed",
        "who": "Like Devon",
        "body": "A plain-language summary of the diagnosis, the patient group for it, and the closest "
                "related communities. If nothing useful is known, the atlas says so.",
        "cta": "Find our community",
        "page": "explore",
    },
    {
        "key": "scout",
        "icon": ":material/biotech:",
        "title": "I'm a researcher or scout",
        "who": "Like Priya or Dr. Osei",
        "body": "Start from a gene or a mechanism and see every disease it touches, the evidence for each "
                "link, who works on it and which assets exist.",
        "cta": "Explore by mechanism",
        "page": "explore",
    },
]


def _use_example() -> None:
    picked = st.session_state.get("home_example")
    if picked:
        st.session_state["home_query"] = picked
    st.session_state["home_example"] = None


def hero() -> None:
    st.markdown(
        '<div class="ra-hero"><div class="ra-eyebrow">AI Atlas for rare diseases</div>'
        '<h1>Find the dots nobody connected.</h1>'
        '<p>Start from one diagnosis. See which diseases share its symptoms and biology, what research and '
        'registries already exist, who works on it, and a next step you can take this week. Every link shows '
        'its source, and gaps are said out loud.</p>'
        '<div class="ra-steps"><span>1 &nbsp;Search a disease</span><span>2 &nbsp;See what it shares, and why</span>'
        '<span>3 &nbsp;Get a sourced plan</span></div></div>', unsafe_allow_html=True)


def _start_demo() -> None:
    ui.start_tour()
    st.session_state["_demo_go"] = True


def search_box() -> None:
    with st.container(border=True):
        with st.form("home_search", border=False, enter_to_submit=True):
            box, btn = st.columns([5, 1], vertical_alignment="bottom")
            text = box.text_input(
                "Search a disease, gene, symptom or mechanism", key="home_query",
                placeholder="e.g. Krabbe disease, GALC, leukodystrophy, glycosphingolipid metabolism",
            )
            submitted = btn.form_submit_button("Search", type="primary", width="stretch",
                                               icon=":material/search:")
        st.pills("Try", EXAMPLES, key="home_example", on_change=_use_example)
        st.button("Guided demo: Krabbe disease to X-ALD", key="guided_demo", icon=":material/play_circle:",
                  on_click=_start_demo, help="A 2-step walk through Explore and the Action plan using one real "
                                             "example: Krabbe disease and X-linked adrenoleukodystrophy.")
        if st.session_state.pop("_demo_go", False):
            ui.go("explore")
        if submitted:
            if text.strip():
                ui.go("explore", explore_query=text.strip(), explore_new_query=True)
            else:
                st.warning("Type a disease, gene, symptom or mechanism first, or pick an example.")


def persona_cards() -> None:
    st.markdown("#### Where would you like to start?")
    for col, p in zip(st.columns(3, gap="medium"), PERSONAS):
        with col, st.container(border=True, height="stretch", gap="small"):
            st.markdown(f"#### {p['icon']} {p['title']}")
            st.caption(p["who"])
            st.markdown(p["body"])
            st.space("stretch")
            if st.button(p["cta"], key=f"persona_{p['key']}", type="primary", width="stretch",
                         icon=":material/arrow_forward:", icon_position="right"):
                state = {"persona": p["key"]}
                if p["key"] == "scout" and not st.session_state.get("explore_query"):
                    state.update(explore_query="Glycosphingolipid metabolism", explore_new_query=True)
                ui.go(p["page"], **state)


def coverage() -> None:
    s = backend.stats()
    st.markdown("#### What the atlas covers")
    st.caption("Counts come from the live graph. Each type has its own colour and shape, used the same way on every page.")
    nb = s.get("nodes_by_type", {})
    order = ["Disease", "Gene", "Phenotype", "Pathway", "Paper", "Trial", "Researcher", "PatientOrg"]
    with st.container(horizontal=True, gap="medium", wrap=True):
        for t in order:
            if t in nb:
                st.markdown(f"<div class='ra-stat' style='min-width:9.5rem'>{ui.type_chip(t)}<div class='ra-v'>"
                            f"{nb[t]:,}</div></div>", unsafe_allow_html=True, width="content")

    ev = s.get("edges_by_evidence", {})
    if ev:
        total = sum(ev.values()) or 1
        st.markdown("**Links, by how we know them**")
        ui.chips([ui.ev_chip(k) + f"<b>{v:,}</b>&nbsp;({v / total:.0%})" for k, v in ev.items()])

    groups = s.get("groups", {})
    with st.container(horizontal=True, gap="small", wrap=True):
        for g, v in groups.items():
            st.badge(f"{ui.group_label(g)}: {v.get('diseases', 0):,} diseases · {v.get('papers', 0):,} papers · "
                     f"{v.get('trials', 0):,} studies", color="gray")
        st.badge("Literature index ready" if s.get("index_ready") else "Literature index not built yet",
                 icon=":material/check_circle:" if s.get("index_ready") else ":material/hourglass_empty:",
                 color="green" if s.get("index_ready") else "orange")
        st.badge("Local language model ready" if s.get("llm_ready") else "Language model off",
                 icon=":material/check_circle:" if s.get("llm_ready") else ":material/power_off:",
                 color="green" if s.get("llm_ready") else "gray")
    srcs = s.get("sources") or []
    if srcs:
        with st.expander("Data sources and when they were retrieved"):
            st.markdown(ui.html_table(
                ["Source", "Retrieved", "Records / size"],
                [[ui.esc(x.get("name")), ui.esc(x.get("retrieved") or "n/a"),
                  ui.esc(f"{x['records']:,}" if isinstance(x.get("records"), int) else (x.get("records") or "n/a"))]
                 for x in srcs]), unsafe_allow_html=True)


def footer() -> None:
    st.write("")
    notes = [
        (":material/fact_check:", "Every link has a source",
         "Click Evidence on any connection to see the database or paper it comes from, and how sure we are."),
        (":material/help:", "Hypotheses are marked",
         "Dotted links are computed by the atlas, not established facts. Check them with an expert."),
        (":material/report:", "Gaps are said out loud",
         "When no supported connection exists, the atlas tells you what it searched and what is missing."),
    ]
    for col, (icon, title, text) in zip(st.columns(3, gap="medium"), notes):
        with col:
            st.markdown(f"**{icon} {title}**")
            st.caption(text)


def body() -> None:
    hero()
    search_box()
    persona_cards()
    coverage()
    footer()


ui.page(None, "", body, name="Home")
