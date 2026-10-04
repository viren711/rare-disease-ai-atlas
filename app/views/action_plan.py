"""Action plan: disease A (+ a neighbour B) -> a sourced lead Maria can act on, or an honest gap.

Order on the page follows the brief's three questions:
  verdict     supported lead, or a prominent gap report (what was searched, what is missing, what to test)
  why         the connection step by step, every step with its evidence
  what exists shared studies / registries, people who work on both, patient groups
  what next   what must be checked before joining forces, next steps, and a proposal to copy
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backend, sections, ui  # noqa: E402

BEST = "__best__"


# ---------------------------------------------------------------------------
# Pickers
# ---------------------------------------------------------------------------


def pickers() -> tuple[dict | None, str | None]:
    pre_a = st.session_state.pop("plan_a", None)
    pre_b = st.session_state.pop("plan_b", "__keep__")
    if pre_a:
        st.session_state["plan_a_q"] = pre_a["label"]
        st.session_state["plan_a_pick"] = pre_a["id"]
        st.session_state["plan_b_pick"] = BEST
    if pre_b not in ("__keep__", None):
        st.session_state["plan_b_pick"] = pre_b["id"]
    if "plan_a_q" not in st.session_state:
        focus = st.session_state.get("focus")
        if focus and focus.get("type") == "Disease":
            st.session_state["plan_a_q"] = focus["label"]
            st.session_state["plan_a_pick"] = focus["id"]

    left, right = st.columns(2, gap="medium")
    with left:
        q = st.text_input("Your disease", key="plan_a_q", placeholder="Type a disease name, e.g. Krabbe disease")
        matches = backend.search(q, types=["Disease"], k=8) if q.strip() else []
        if q.strip() and not matches:
            st.warning("No disease in the atlas matches that name. Try another spelling or a synonym.")
            return None, None
        if not matches:
            return None, None
        ids = [m["id"] for m in matches]
        if st.session_state.get("plan_a_pick") not in ids:
            st.session_state["plan_a_pick"] = ids[0]
        a_id = st.selectbox("Matching disease", ids, key="plan_a_pick",
                            format_func=lambda i: next(
                                m["label"] + (f"  (matched “{m['matched_text']}”)" if m.get("matched_text")
                                              and m["matched_text"].lower() != m["label"].lower() else "")
                                for m in matches if m["id"] == i))
        a = next(m for m in matches if m["id"] == a_id)
    with right:
        nbs = backend.neighbours(a["id"], k=30)
        opts = [BEST] + [n["id"] for n in nbs]
        if st.session_state.get("plan_b_pick") not in opts:
            st.session_state["plan_b_pick"] = BEST

        def fmt(i: str) -> str:
            if i == BEST:
                return "Best match (the atlas chooses)"
            n = next(n for n in nbs if n["id"] == i)
            return (f"{n['label']}  ·  similarity {n['score']:.2f}"
                    + ("  ·  different disease family" if n.get("cross_group") else ""))

        b = st.selectbox("Compare with a similar disease", opts, key="plan_b_pick", format_func=fmt)
        if not nbs:
            st.caption("The atlas found no similar disease to compare with.")
    return {"id": a["id"], "label": a["label"]}, (None if b == BEST else b)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def gap_panel(plan: dict) -> None:
    gap = plan.get("gap") or {}
    b = plan.get("b") or {}
    with st.container(border=True, key="gap_panel"):
        st.markdown("### :material/report: No supported route found")
        st.markdown(ui.plain_chip("Readiness: none", "bad", ui.READY["none"][2]) if not plan.get("readiness") else
                    ui.readiness_badge(plan["readiness"]), unsafe_allow_html=True)
        st.markdown(f"The atlas could not find evidence strong enough to recommend joining forces between "
                    f"**{ui.esc(plan['a']['label'])}**"
                    + (f" and **{ui.esc(b.get('label'))}**" if b else "")
                    + ". This is what is unknown, not a verdict that no link exists.")
        c1, c2 = st.columns(2, gap="medium")
        with c1:
            st.markdown("**What we searched**")
            st.markdown("\n".join(f"- {ui.esc(s)}" for s in gap.get("searched") or ["(not reported)"]))
        with c2:
            st.markdown("**What is missing**")
            st.markdown("\n".join(f"- {ui.esc(s)}" for s in gap.get("missing") or ["(not reported)"]))
        if gap.get("next_question"):
            st.warning(f"**Next question to test:** {gap['next_question']}", icon=":material/science:")


def narrative(plan: dict) -> None:
    text = plan.get("narrative") or ""
    if not text:
        return
    st.markdown("#### In plain words")
    st.markdown(ui.render_cited(text), unsafe_allow_html=True)
    cites = ui.cited_edges(text)
    if cites:
        rows = {r["edge_id"]: r for r in plan.get("path") or []}

        def label(eid: str) -> str:
            n = cites.index(eid) + 1
            r = rows.get(eid)
            if not r:
                try:
                    e = backend.edge(eid)
                    r = {"dst_label": e.get("dst_label") or eid}
                except Exception:  # noqa: BLE001 - a label is cosmetic
                    return f"[{n}]"
            dst = r["dst_label"] if len(r["dst_label"]) <= 22 else r["dst_label"][:21] + "…"
            return f"[{n}] {dst}"

        def _on_cite() -> None:
            ui.inspect(st.session_state.get("plan_cite"))
            st.session_state["plan_cite"] = None

        st.pills("Evidence for each sentence (click to inspect)", cites, key="plan_cite", format_func=label,
                 on_change=_on_cite)


def path_steps(plan: dict) -> None:
    path = plan.get("path") or []
    if not path:
        return
    st.markdown("#### The connection, step by step")
    for i, s in enumerate(path):
        text_col, btn_col = st.columns([6, 1.3], vertical_alignment="center")
        with text_col:
            st.markdown(
                f"<div class='ra-step'><span class='ra-step-n'>{i + 1}</span><span>"
                f"<b>{ui.esc(s.get('src_label'))}</b> <span class='ra-rel'>{ui.esc(ui.rel_label(s.get('rel', '')))}"
                f"</span> <b>{ui.esc(s.get('dst_label'))}</b><br>{ui.ev_chip(s.get('evidence_type'))}"
                f"<span class='ra-sub'>{ui.esc(s.get('source') or '')}</span></span></div>",
                unsafe_allow_html=True)
        with btn_col:
            st.button("Evidence", key=f"step_ev_{i}", type="tertiary", icon=":material/fact_check:",
                      on_click=ui.inspect, args=(s.get("edge_id"),))


def assets(plan: dict) -> None:
    st.markdown("#### What already exists that you could share")
    items = plan.get("shared_assets") or []
    if not items:
        st.caption("No study, registry or natural-history effort in the atlas includes both diseases.")
        return
    for i, x in enumerate(items[:4]):
        with st.container(border=True):
            url = ui.node_url(x.get("id"))
            title = f"[{ui.esc(x['label'])}]({url})" if url else ui.esc(x["label"])
            ui.chips([ui.type_chip("Trial", ui.ASSET_KIND.get(x.get("kind"), x.get("kind") or "Study")),
                      ui.plain_chip((x.get("status") or "status unknown").replace("_", " ").capitalize(), "neutral")])
            st.markdown(title)
            with st.container(horizontal=True, gap="small"):
                for j, eid in enumerate(x.get("edge_ids") or []):
                    st.button(f"Evidence {j + 1}", key=f"asset_ev_{i}_{j}", type="tertiary",
                              icon=":material/fact_check:", on_click=ui.inspect, args=(eid,))
    if len(items) > 4:
        with st.expander(f"{len(items) - 4} more shared studies and registries"):
            for i, x in enumerate(items[4:], start=4):
                url = ui.node_url(x.get("id"))
                title = f"[{ui.esc(x['label'])}]({url})" if url else ui.esc(x["label"])
                with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                    st.markdown(ui.type_chip("Trial", ui.ASSET_KIND.get(x.get("kind"), x.get("kind") or "Study"))
                                + f"{title} <span class='ra-sub'>· "
                                f"{ui.esc((x.get('status') or 'status unknown').replace('_', ' ').lower())}</span>",
                                unsafe_allow_html=True, width="content")
                    if x.get("edge_ids"):
                        st.button(":material/fact_check:", key=f"asset_ev_{i}_0", type="tertiary",
                                  on_click=ui.inspect, args=(x["edge_ids"][0],), help="Evidence")


def people(plan: dict) -> None:
    a, b = plan["a"]["label"], (plan.get("b") or {}).get("label", "B")
    st.markdown("#### People who work on both")
    rs = plan.get("researchers") or []
    if not rs:
        st.caption("No researcher in the atlas has published on both diseases.")
    for r in rs:
        orcid = (f" · <a href='{ui.esc(ui.orcid_url(r['orcid']))}' target='_blank' rel='noopener'>ORCID</a>"
                 if r.get("orcid") else "")
        why = r.get("why") or r.get("reason")
        why = "; ".join(map(str, why)) if isinstance(why, list) else why
        st.markdown(ui.type_chip("Researcher", r["label"]) + f"<span class='ra-sub'>{ui.esc(r.get('affiliation'))}"
                    f" · {r.get('papers_a', 0)} papers on {ui.esc(a)}, {r.get('papers_b', 0)} on {ui.esc(b)}</span>"
                    + orcid + (f"<br><span class='ra-sub'>Why: {ui.esc(why)}</span>" if why else ""),
                    unsafe_allow_html=True)
    with st.expander(f"More collaborators, ranked for {a}"):
        sections.collaborators_section(plan["a"]["id"])

    st.markdown("#### Patient groups")
    orgs = plan.get("orgs") or []
    if not orgs:
        st.caption("No patient group recorded for either disease.")
    labels = {plan["a"]["id"]: plan["a"]["label"]}
    if plan.get("b"):
        labels[plan["b"]["id"]] = plan["b"]["label"]
    for o in orgs:
        link = f"[{ui.esc(o['label'])}]({o['url']})" if o.get("url") else ui.esc(o["label"])
        st.markdown(ui.dot("PatientOrg") + link
                    + f" <span class='ra-sub'>· {ui.esc(labels.get(o.get('disease_id'), ''))}</span>",
                    unsafe_allow_html=True)


def checks_and_steps(plan: dict) -> None:
    diffs = plan.get("differences") or []
    if diffs:
        st.markdown("#### What must be checked before joining forces")
        with st.container(border=True):
            st.markdown("\n".join(f"- {ui.esc(d)}" for d in diffs))
    steps = plan.get("next_steps") or []
    if steps:
        st.markdown("#### Next steps")
        for i, s in enumerate(steps):
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"<div class='ra-step'><span class='ra-step-n'>{i + 1}</span>"
                            f"<span>{ui.esc(s.get('text'))}</span></div>", unsafe_allow_html=True, width="content")
                for j, eid in enumerate(s.get("edge_ids") or []):
                    st.button("Evidence", key=f"next_ev_{i}_{j}", type="tertiary", icon=":material/fact_check:",
                              on_click=ui.inspect, args=(eid,))


def proposal_text(plan: dict) -> str:
    a = plan["a"]["label"]
    b = (plan.get("b") or {}).get("label")
    ev = {k: v[0].lower() for k, v in ui.EVIDENCE.items()}
    lines: list[str] = []
    if plan.get("supported"):
        org_b = next((o for o in plan.get("orgs") or [] if plan.get("b") and o.get("disease_id") == plan["b"]["id"]),
                     None)
        lines += [f"Subject: Shared research between {a} and {b}?", "",
                  f"Hello {org_b['label'] if org_b else 'team'},", "",
                  f"I lead a patient group for {a}. The Rare Disease Atlas links our disease with {b}. "
                  "Each link below has a public source:"]
        for s in plan.get("path") or []:
            lines.append(f"- {s['src_label']} {ui.rel_label(s['rel'])} {s['dst_label']} "
                         f"({ev.get(s.get('evidence_type'), s.get('evidence_type'))}, {s.get('source')})")
        if plan.get("shared_assets"):
            lines += ["", "Existing work we might share:"]
            for x in plan["shared_assets"]:
                url = ui.node_url(x.get("id"))
                lines.append(f"- {x['label']} ({ui.ASSET_KIND.get(x.get('kind'), x.get('kind'))}, "
                             f"{(x.get('status') or 'status unknown').replace('_', ' ').lower()})" + (f" {url}" if url else ""))
        if plan.get("researchers"):
            lines += ["", "Researchers who have published on both diseases:"]
            lines += [f"- {r['label']}, {r.get('affiliation') or ''}" for r in plan["researchers"]]
        if plan.get("differences"):
            lines += ["", "What we would need to check together first:"]
            lines += [f"- {d}" for d in plan["differences"]]
        if plan.get("next_steps"):
            lines += ["", f"Proposed first step: {plan['next_steps'][0]['text']}"]
        lines += ["", "Links marked 'inferred by the atlas' are hypotheses, not established facts. "
                      "Would you be open to a short call to discuss?"]
    else:
        gap = plan.get("gap") or {}
        lines += [f"Subject: Open question about {a}", "",
                  f"I lead a patient group for {a}. We searched the Rare Disease Atlas for diseases that share its "
                  "biology and found no supported connection yet.", "", "What was searched:"]
        lines += [f"- {s}" for s in gap.get("searched") or []]
        lines += ["", "What is missing:"] + [f"- {s}" for s in gap.get("missing") or []]
        if gap.get("next_question"):
            lines += ["", f"The question we would like to test: {gap['next_question']}"]
        lines += ["", "Could you advise whether this is worth investigating, or point us to someone who could?"]
    return "\n".join(lines)


def proposal(plan: dict) -> None:
    st.markdown("#### Proposal to send")
    st.caption("Plain text you can paste into an email to a partner group or researcher. Use the copy icon.")
    text = proposal_text(plan)
    st.code(text, language=None, wrap_lines=True)
    st.download_button("Download as text", text, file_name="atlas_proposal.txt", icon=":material/download:",
                       key="proposal_dl")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def verdict(plan: dict) -> None:
    a, b = plan["a"]["label"], (plan.get("b") or {}).get("label")
    ready = plan.get("readiness")
    badge = ui.readiness_badge(ready) if ready else ""
    if plan.get("supported"):
        ui.banner("good", "Supported lead",
                  f"<b>{ui.esc(a)}</b> and <b>{ui.esc(b)}</b> share evidence-backed features and existing work. "
                  "Check the differences below before acting.")
    if badge:
        reasons = ready.get("reasons") if isinstance(ready, dict) else None
        st.markdown(badge + (" &nbsp;<span class='ra-sub'>" + ui.esc(" · ".join(map(str, reasons[:3]))) + "</span>"
                             if reasons else ""), unsafe_allow_html=True)
    for c in plan.get("caveats") or []:
        st.caption(f":material/warning: {c}")


def body() -> None:
    ui.tour_card(2)
    a, b = pickers()
    if not a:
        ui.empty_state("Pick your disease to see who shares its biology, what already exists, and what to do next.",
                       "Type a name above, for example Krabbe disease. The atlas will choose the closest match "
                       "and compare it with its best neighbour.")
        return
    ui.set_current(a["id"], a["label"])
    holder = st.empty()
    holder.markdown(ui.skeleton(5), unsafe_allow_html=True)
    with st.spinner("Building the plan: comparing symptoms, mechanisms, studies and people..."):
        plan = backend.action_plan(a["id"], b)
    holder.empty()
    main, side = st.columns([5, 3], gap="large")
    with side:
        ui.edge_inspector("plan")
    with main:
        if plan.get("supported"):
            verdict(plan)
        else:
            gap_panel(plan)
        ui.explain_simply("plan", plan["a"]["id"], (plan.get("b") or {}).get("id"),
                          technical_html=ui.render_cited(plan.get("narrative") or "No narrative for this plan."))
        narrative(plan)
        path_steps(plan)
        if plan.get("epidemiology") is not None:
            st.markdown("#### How common, and when it starts")
            sections.epidemiology_compare(plan["epidemiology"], plan["a"]["label"], (plan.get("b") or {}).get("label"))
        assets(plan)
        people(plan)
        st.markdown("#### Funding")
        sections.plan_funding(plan.get("funding"), plan["a"]["id"])
        with st.expander(f"Genetic variants for {plan['a']['label']}"):
            sections.variants_section(plan["a"]["id"])
        checks_and_steps(plan)
        proposal(plan)


ui.page(":material/route: Action plan",
        "Who shares our disease's characteristics? What already exists? What should we do together next?",
        body, name="Action plan", show_legend=True)
