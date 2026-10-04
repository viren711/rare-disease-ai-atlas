"""Shared look and building blocks for every page.

One colour AND one shape per node type, one line style per evidence type, used the same way in the map,
the chips, the legend and the inspector. Colours are never hard-coded here: they are CSS variables
(--ra-t-<Type>, --ra-e-<evidence>) defined per theme in app/theme.py, so Light, Dark and Auto all work.

  node types   hue + shape (Disease circle, Gene diamond, Symptom triangle, Mechanism hexagon, ...)
  evidence     line style + lightness: solid = database record, dashed = found in papers,
               dotted = inferred by the atlas (a hypothesis)
"""
from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from pathlib import Path

import streamlit as st

from app import backend, theme

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

NODE_TYPES = {  # type: (lay label, css colour variable, material icon)
    "Disease": ("Disease", "var(--ra-t-Disease)", ":material/coronavirus:"),
    "Gene": ("Gene", "var(--ra-t-Gene)", ":material/genetics:"),
    "Phenotype": ("Symptom", "var(--ra-t-Phenotype)", ":material/personal_injury:"),
    "Pathway": ("Mechanism", "var(--ra-t-Pathway)", ":material/hub:"),
    "Trial": ("Study / registry", "var(--ra-t-Trial)", ":material/clinical_notes:"),
    "PatientOrg": ("Patient group", "var(--ra-t-PatientOrg)", ":material/diversity_3:"),
    "Researcher": ("Researcher", "var(--ra-t-Researcher)", ":material/person_search:"),
    "Paper": ("Paper", "var(--ra-t-Paper)", ":material/article:"),
}
# vis-network shape names; the chips draw the same shapes with CSS (.ra-shape-*)
NODE_SHAPES = {"Disease": "dot", "Gene": "diamond", "Phenotype": "triangle", "Pathway": "hexagon",
               "Trial": "square", "PatientOrg": "star", "Researcher": "triangleDown", "Paper": "dot"}

EVIDENCE = {  # evidence_type: (lay label, css colour variable, css border style, vis dashes, help)
    "curated": ("Database record", "var(--ra-e-curated)", "solid", False,
                "Taken from a curated database (Orphanet, HPO, Reactome, ClinicalTrials.gov...). Drawn as a solid line."),
    "extracted": ("Found in papers", "var(--ra-e-extracted)", "dashed", [8, 6],
                  "Found automatically in published papers by text mining. Usually right, sometimes not. "
                  "Drawn as a dashed line."),
    "inferred": ("Inferred by the atlas", "var(--ra-e-inferred)", "dotted", [2, 6],
                 "Computed by the atlas from overlapping symptoms, biology and research. A hypothesis to check. "
                 "Drawn as a dotted line."),
}

REL_LABEL = {
    "causes": "is a known cause of",
    "has_phenotype": "has the symptom",
    "in_pathway": "works in the process",
    "mentions": "mentions",
    "authored": "wrote",
    "studies": "studies",
    "runs": "supports families with",
    "similar_to": "is similar to",
    "subclass_of": "is a type of",
    "serves": "serves families with",
    "investigates": "investigates",
    "cites": "cites",
    "associated_with": "is associated with",
}

ASSET_KIND = {"trial": "Clinical trial", "registry": "Registry", "natural_history": "Natural-history study"}

GROUP_LABEL = {"lysosomal": "Lysosomal storage diseases", "peroxisomal": "Peroxisomal diseases"}

PAGES = {
    "home": "views/home.py",
    "explore": "views/explore.py",
    "plan": "views/action_plan.py",
    "ask": "views/ask.py",
    "about": "views/about.py",
}



def type_label(t: str | None) -> str:
    return NODE_TYPES.get(t or "", (t or "Item", "", ""))[0]


def type_color(t: str | None) -> str:
    """CSS colour (a variable) for HTML; use type_hex() for Python-side widgets such as the map."""
    return NODE_TYPES.get(t or "", ("", "var(--ra-faint)", ""))[1]


def type_hex(t: str | None) -> str:
    p = theme.palette()
    return p.get(f"t_{t}", p["faint"])


def ev_hex(evidence_type: str | None) -> str:
    p = theme.palette()
    return p.get(f"e_{evidence_type}", p["faint"])


def rel_label(rel: str) -> str:
    return REL_LABEL.get(rel, rel.replace("_", " "))


def group_label(g: str | None) -> str:
    return GROUP_LABEL.get(g or "", g or "")


def node_url(node_id: str | None) -> str | None:
    if not node_id:
        return None
    if node_id.startswith("MONDO:"):
        return f"https://monarchinitiative.org/{node_id}"
    if node_id.startswith("NCBIGene:"):
        return f"https://www.ncbi.nlm.nih.gov/gene/{node_id.split(':', 1)[1]}"
    if node_id.startswith("HP:"):
        return f"https://hpo.jax.org/browse/term/{node_id}"
    if node_id.startswith("R-HSA-"):
        return f"https://reactome.org/content/detail/{node_id}"
    if node_id.startswith("PMID:"):
        return pubmed_url(node_id)
    if node_id.startswith("NCT"):
        return f"https://clinicaltrials.gov/study/{node_id}"
    return None


def pubmed_url(pmid: str | int) -> str:
    return f"https://pubmed.ncbi.nlm.nih.gov/{str(pmid).replace('PMID:', '')}/"


def orcid_url(orcid: str) -> str:
    return orcid if orcid.startswith("http") else f"https://orcid.org/{orcid}"


# ---------------------------------------------------------------------------
# Chips, banners, bars (inline HTML; colours are CSS variables)
# ---------------------------------------------------------------------------


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def type_chip(t: str, text: str | None = None, title: str | None = None) -> str:
    label = esc(text if text is not None else type_label(t))
    tip = f' title="{esc(title)}"' if title else ""
    shape = NODE_SHAPES.get(t, "dot")
    return (f'<span class="ra-chip" style="--c:{type_color(t)}"{tip}>'
            f'<span class="ra-dot ra-shape-{shape}"></span>{label}</span>')


def dot(t: str) -> str:
    shape = NODE_SHAPES.get(t, "dot")
    return (f'<span class="ra-dot ra-shape-{shape}" style="--c:{type_color(t)};margin-right:.5em" '
            f'title="{esc(type_label(t))}"></span>')


def ev_chip(evidence_type: str | None, confidence: float | None = None) -> str:
    name, color, style, _, tip = EVIDENCE.get(evidence_type or "", (evidence_type or "unknown", "var(--ra-faint)",
                                                                      "solid", False, ""))
    conf = f" · {confidence:.0%}" if isinstance(confidence, (int, float)) else ""
    return (f'<span class="ra-chip ra-ev" style="--c:{color}" title="{esc(tip)}">'
            f'<span class="ra-line" style="border-top-style:{style}"></span>{esc(name)}{conf}</span>')


def plain_chip(text: str, tone: str = "neutral", title: str | None = None) -> str:
    tip = f' title="{esc(title)}"' if title else ""
    return f'<span class="ra-chip ra-{tone}"{tip}>{esc(text)}</span>'


def chips(parts: list[str]) -> None:
    if parts:
        st.markdown(" ".join(parts), unsafe_allow_html=True)


def banner(kind: str, title: str, body: str = "", icon: str = "") -> None:
    """Coloured callout. kind: good | warn | bad | info. `body` is trusted HTML (escape user text first)."""
    glyph = icon or {"good": "✓", "warn": "⚠", "bad": "✕", "info": "ℹ"}.get(kind, "")
    st.markdown(f'<div class="ra-banner ra-banner-{kind}" role="status"><b class="ra-bt">{glyph} {title}</b>'
                + (f"<div>{body}</div>" if body else "") + "</div>", unsafe_allow_html=True)


def empty_state(text: str, hint: str = "") -> None:
    st.markdown(f'<div class="ra-empty">{esc(text)}' + (f"<br><span class='ra-sub'>{esc(hint)}</span>" if hint else "")
                + "</div>", unsafe_allow_html=True)


def skeleton(lines: int = 4) -> str:
    widths = [92, 78, 85, 60, 70, 88]
    return "".join(f'<div class="ra-skel" style="width:{widths[i % len(widths)]}%"></div>' for i in range(lines))


def legend(types: list[str] | None = None, links_only: bool = False) -> None:
    """Always-visible key. links_only=True is the compact one-line version used under every page title."""
    ev_part = " ".join(ev_chip(e) for e in EVIDENCE)
    if links_only:
        st.markdown(f'<div class="ra-legend"><span class="ra-legend-h">Links</span>{ev_part}</div>',
                    unsafe_allow_html=True)
        return
    types = types or list(NODE_TYPES)
    node_part = " ".join(type_chip(t) for t in types)
    st.markdown(
        f'<div class="ra-legend"><span class="ra-legend-h">Things</span>{node_part}'
        f'<br><span class="ra-legend-h">Links</span>{ev_part}</div>',
        unsafe_allow_html=True,
    )


def rarity(ic: float | None, informative: bool | None) -> str:
    """Distinctive vs common symptom, in words a family can read."""
    if informative:
        return plain_chip("distinctive", "amber", f"Rare across all diseases (information content {ic:.1f}), "
                                                  "so sharing it means more." if ic is not None else None)
    return plain_chip("common", "muted", f"Seen in many diseases (information content {ic:.1f}), so sharing it "
                                         "means little on its own." if ic is not None else None)


def conf_bar(value: float | None, label: str = "Confidence") -> str:
    if not isinstance(value, (int, float)):
        return ""
    v = max(0.0, min(1.0, float(value)))
    return (f'<div class="ra-conf"><b>{esc(label)}</b><div class="ra-bar" role="progressbar" aria-valuemin="0" '
            f'aria-valuemax="100" aria-valuenow="{v * 100:.0f}" aria-label="{esc(label)}"><i style="width:{v * 100:.0f}%">'
            f'</i></div><span class="ra-pct">{v:.0%}</span></div>')


STANCES = {  # stance: (tone, glyph, label, plain meaning)
    "supported": ("good", "✓", "Supported", "Sources agree and nothing disagrees."),
    "mixed": ("warn", "±", "Mixed", "Some evidence supports this and some disagrees."),
    "weak": ("warn", "~", "Weak", "Only thin or indirect evidence."),
    "unsupported": ("bad", "✕", "Unsupported", "No direct evidence found."),
}


def stance_of(e: dict) -> str:
    """supported / mixed / weak / unsupported from edge['evidence_summary'] when present, else derived."""
    es = e.get("evidence_summary")
    raw = es.get("stance") or es.get("label") if isinstance(es, dict) else es
    if isinstance(raw, str) and raw.strip().lower() in STANCES:
        return raw.strip().lower()
    conf = e.get("confidence")
    if e.get("contradicting"):
        return "mixed"
    if isinstance(conf, (int, float)):
        return "supported" if conf >= 0.7 else "weak" if conf >= 0.4 else "unsupported"
    return "weak"


def stance_chip(stance: str) -> str:
    tone, glyph, label, tip = STANCES.get(stance, STANCES["weak"])
    return plain_chip(f"{glyph} {label}", tone, tip)


BREAKDOWN_LABELS = {"source_reliability": "Source reliability", "recency": "Recency",
                    "corroboration_bonus": "Corroboration bonus", "penalty": "Penalty (counter-evidence)"}


def breakdown_of(e: dict) -> list[tuple[str, float]]:
    """Bar rows (label, 0..1) from edge['confidence_breakdown'] (dict or list of {name, value})."""
    cb = e.get("confidence_breakdown")
    items: list[tuple[str, float]] = []
    if isinstance(cb, dict):
        for k, v in cb.items():
            if k in ("overall", "formula", "n_independent_sources"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                items.append((BREAKDOWN_LABELS.get(k, k.replace("_", " ").capitalize()), float(v)))
    elif isinstance(cb, list):
        for x in cb:
            if isinstance(x, dict):
                name = x.get("name") or x.get("label") or x.get("component")
                val = x.get("value", x.get("score"))
                if name and isinstance(val, (int, float)):
                    items.append((str(name), float(val)))
    return [(n, max(0.0, min(1.0, v))) for n, v in items]


READY = {  # level: (pips, tone, plain meaning)
    "strong": (3, "good", "Several independent, curated links and existing work to build on."),
    "moderate": (2, "info", "A real lead, but some links are inferred or no shared study exists yet."),
    "weak": (1, "warn", "A thin lead. Treat it as a question to test, not a plan."),
    "none": (0, "bad", "No supported route. See what is missing below."),
}


def readiness_level(r) -> str | None:
    raw = r.get("level") or r.get("label") or r.get("status") if isinstance(r, dict) else r
    raw = str(raw).strip().lower() if raw is not None else ""
    return raw if raw in READY else None


def readiness_badge(r) -> str:
    level = readiness_level(r)
    if not level:
        return ""
    pips, tone, tip = READY[level]
    meter = "".join(f'<i class="{"on" if i < pips else ""}"></i>' for i in range(3))
    return (f'<span class="ra-ready" style="--c:var(--ra-{tone})" title="{esc(tip)}">'
            f'<span class="ra-meter">{meter}</span>Readiness: {level}</span>')


# ---------------------------------------------------------------------------
# Plain-language tooltips (glossary)
# ---------------------------------------------------------------------------

_TAG = re.compile(r"(<[^>]+>)")


def gloss(html_text: str, max_terms: int = 12) -> str:
    """Wrap the first occurrence of each glossary term in <abbr title="plain definition">.
    Input must already be escaped/trusted HTML; tags and text inside <abbr>/<a> are left alone."""
    terms = backend.glossary()
    if not terms or not html_text:
        return html_text
    parts = _TAG.split(html_text)
    used = 0
    for term in sorted(terms, key=len, reverse=True):
        if used >= max_terms or len(term) < 4:
            break
        pat = re.compile(r"(?<![\w-])(" + re.escape(term) + r")(?![\w-])", re.IGNORECASE)
        parts = _TAG.split("".join(parts))  # re-split so inserted <abbr title="..."> stays a tag, not text
        depth = 0
        for i, seg in enumerate(parts):
            if seg.startswith("<"):
                tag = seg.lower()
                if tag.startswith(("<abbr", "<a ", "<code")):
                    depth += 1
                elif tag.startswith(("</abbr", "</a>", "</code")):
                    depth = max(0, depth - 1)
                continue
            if depth or not seg:
                continue
            m = pat.search(seg)
            if m:
                parts[i] = (seg[: m.start()] + f'<abbr class="ra-gloss" title="{esc(terms[term])}">{m.group(1)}</abbr>'
                            + seg[m.end():])
                used += 1
                break
    return "".join(parts)


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def inspect(edge_id: str | None) -> None:
    """Open an edge in the inspector panel (used as on_click)."""
    if edge_id:
        st.session_state["inspect_edge"] = edge_id


def close_inspector() -> None:
    st.session_state.pop("inspect_edge", None)


def remember(node_id: str, label: str) -> None:
    recent = [r for r in st.session_state.get("recent", []) if r[0] != node_id]
    st.session_state["recent"] = [(node_id, label)] + recent[:7]
    set_current(node_id, label)


def set_current(node_id: str, label: str) -> None:
    """The disease shown in the 'Current disease' chip on every page."""
    st.session_state["current_disease"] = {"id": node_id, "label": label}


def current_disease() -> dict | None:
    cur = st.session_state.get("current_disease")
    if cur:
        return cur
    focus = st.session_state.get("focus")
    return focus if focus and focus.get("type") == "Disease" else None


def go(page: str, **state) -> None:
    """Switch page, priming session state for it first."""
    for k, v in state.items():
        st.session_state[k] = v
    try:
        st.switch_page(PAGES[page])
    except Exception:  # noqa: BLE001 - only resolvable when run through app/streamlit_app.py
        st.session_state["_nav_pending"] = page


def nav_button(label: str, page: str, key: str, icon: str | None = None, type: str = "secondary",
               width: str = "content", **state) -> None:
    if st.button(label, key=key, icon=icon, type=type, width=width):
        go(page, **state)


# ---------------------------------------------------------------------------
# Guided demo (Krabbe -> X-ALD), shown as a thin banner on Explore and the Action plan
# ---------------------------------------------------------------------------

TOUR_A = ("MONDO:0009499", "Krabbe disease")
TOUR_B = ("MONDO:0018544", "X-linked adrenoleukodystrophy")


def start_tour() -> None:
    st.session_state["tour"] = 1
    st.session_state["persona"] = None
    st.session_state["explore_query"] = TOUR_A[1]
    st.session_state["explore_new_query"] = True
    close_inspector()


def tour_next_to_plan() -> None:
    st.session_state["tour"] = 2
    go("plan", plan_a={"id": TOUR_A[0], "label": TOUR_A[1]}, plan_b={"id": TOUR_B[0], "label": TOUR_B[1]})


def end_tour() -> None:
    st.session_state.pop("tour", None)


def tour_card(step: int) -> None:
    """Step banner for the guided demo; only draws when the tour is at `step`."""
    if st.session_state.get("tour") != step:
        return
    with st.container(border=True, key=f"tour_card_{step}"):
        c1, c2 = st.columns([5, 1.4], vertical_alignment="center")
        if step == 1:
            c1.markdown("**Guided demo, step 1 of 2.** Krabbe disease is open. Scroll to *Similar diseases*: "
                        "X-linked adrenoleukodystrophy appears as a neighbour from a *different* disease family, "
                        "linked by shared brain symptoms. Press *Why linked?* to see the evidence.")
            c2.button("Next: the plan", key="tour_next", type="primary", icon=":material/arrow_forward:",
                      icon_position="right", on_click=tour_next_to_plan, width="stretch")
        else:
            c1.markdown("**Guided demo, step 2 of 2.** This is the plan for Krabbe disease with X-ALD: the verdict, "
                        "the evidence for each step, what already exists to share, who to contact, and a "
                        "proposal you can copy.")
            c2.button("End the demo", key="tour_end", icon=":material/check:", on_click=end_tour, width="stretch")


# ---------------------------------------------------------------------------
# "Explain simply": plain-language rewrite with a toggle back to the technical view
# ---------------------------------------------------------------------------


def _plain_html(text: str, order: dict[str, int]) -> str:
    """The four-line plain text ('Short answer:' ...) as HTML: bold labels, [e#] as numbered markers."""
    lines = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        head, sep, rest = line.partition(":")
        if sep and len(head) <= 24:
            lines.append(f"<b>{esc(head)}:</b>{_cite_html(rest, order)}")
        else:
            lines.append(_cite_html(line, order))
    return "<br>".join(lines)


def _cite_html(text: str, order: dict[str, int]) -> str:
    return gloss(CITE_RE.sub(lambda m: f"<sup class='ra-cite'>[{order.get(m.group(1), '?')}]</sup>", esc(text)))


def cite_pills(cites: list[str], key: str, label: str = "Evidence for each sentence (click to inspect)") -> None:
    """Clickable [n] chips that open the edge inspector."""
    if not cites:
        return

    def fmt(eid: str) -> str:
        return f"[{cites.index(eid) + 1}]"

    def _on() -> None:
        inspect(st.session_state.get(key))
        st.session_state[key] = None

    st.pills(label, cites[:12], key=key, format_func=fmt, on_change=_on)


def explain_simply(key: str, a_id: str, b_id: str | None, technical_html: str, level: str = "family",
                   kind: str = "plan") -> None:
    """'Explain simply' button, then a callout with the plain-language text and a toggle to the technical view.

    The text comes from atlas.explain (plan: plain_explanation_stream, disease: plain_disease_stream). It can take
    10-30 s uncached, so it only runs on press: draft tokens stream in as provisional text, then the verified
    final text replaces them. Results are kept per (disease pair, level) in the session; the atlas also caches
    on disk."""
    on_key, view_key, res_key = f"{key}_plain_on", f"{key}_plain_view", f"{key}_plain_res"
    sig = (kind, a_id, b_id, level)
    if not st.session_state.get(on_key):
        if st.button("Explain simply", key=f"{key}_btn", icon=":material/translate:",
                     help="Rewrite this in everyday language for a family. The first time can take 10 to 30 "
                          "seconds on the local language model."):
            st.session_state[on_key] = True
            st.session_state[view_key] = "Plain language"
            st.rerun()
        return
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    c1.markdown("**:material/translate: Explained simply**")
    view = c2.segmented_control("Version", ["Plain language", "Technical view"], key=view_key,
                                label_visibility="collapsed") or "Plain language"
    if view == "Technical view":
        st.markdown(f'<div class="ra-callout">{technical_html}</div>', unsafe_allow_html=True)
    else:
        cached = st.session_state.get(res_key)
        final = cached[1] if cached and cached[0] == sig else None
        if final is None:
            slot = st.empty()
            slot.markdown(f'<div class="ra-callout">{skeleton(4)}</div>', unsafe_allow_html=True)
            draft = ""
            with st.spinner("Writing a plain-language version. The local language model can take 10 to 30 "
                            "seconds the first time; it is remembered afterwards."):
                for ev in backend.plain_stream(kind, a_id, b_id, level):
                    if ev.get("type") == "token":
                        draft += ev.get("text", "")
                        slot.markdown(f'<div class="ra-callout" style="opacity:.7;border-left-style:dashed">'
                                      f'<span class="ra-sub">Draft, not yet checked against the evidence</span><br>'
                                      f'{_plain_html(draft, {})}</div>', unsafe_allow_html=True)
                    elif ev.get("type") == "final":
                        final = {k: v for k, v in ev.items() if k != "type"}
            slot.empty()
            if final and final.get("text"):
                st.session_state[res_key] = (sig, final)
        if final and final.get("text"):
            cites = final.get("citations") or cited_edges(final["text"])
            order = {eid: i + 1 for i, eid in enumerate(dict.fromkeys(list(cites) + cited_edges(final["text"])))}
            badge = (plain_chip("✓ AI-rewritten, citations verified", "good",
                                "Written by the local language model; every citation was checked against the graph.")
                     if final.get("source") == "llm" else
                     plain_chip("Template text", "neutral", "Built from fixed templates, no language model involved."))
            st.markdown(f'<div class="ra-callout">{_plain_html(final["text"], order)}<div style="margin-top:.5rem">'
                        f'{badge}<span class="ra-sub">Check anything important against the evidence.</span></div></div>',
                        unsafe_allow_html=True)
            cite_pills(list(order), f"{key}_plain_cite")
        else:
            st.info("The plain-language writer is not available right now, so only the technical view can be "
                    "shown.", icon=":material/info:")
    if st.button("Hide", key=f"{key}_hide", type="tertiary", icon=":material/close:"):
        st.session_state[on_key] = False
        st.rerun()


# ---------------------------------------------------------------------------
# Page frame: top strip (breadcrumb, current disease, theme, demo badge) + title
# ---------------------------------------------------------------------------

_PAGE_NAMES = {"Home": "Home", "Explore": "Explore", "Action plan": "Action plan",
               "Ask the literature": "Ask the literature", "How it works": "How it works"}


def current_chip() -> str:
    cur = current_disease()
    if not cur:
        return ('<span class="ra-current" style="opacity:.75" title="Pick a disease on Explore or Action plan; it '
                'follows you across pages."><small>Current disease</small> none yet</span>')
    return (f'<span class="ra-current" title="{esc(cur["id"])}"><small>Current disease</small> '
            f'<b>{esc(cur["label"])}</b></span>')


def crumbs(page_name: str) -> str:
    items = ["Atlas"] if page_name == "Home" else ["Atlas", page_name]
    cur = current_disease()
    if cur and page_name in ("Explore", "Action plan"):
        items.append(esc(cur["label"]))
    parts = [f"<b>{i}</b>" if n == len(items) - 1 else esc(i) if i == "Atlas" else esc(i)
             for n, i in enumerate(items)]
    return '<nav class="ra-crumbs" aria-label="You are here">' + '<span class="ra-sep">›</span>'.join(
        f"<span>{p}</span>" for p in parts) + "</nav>"


_IFRAME_FIX = """<script>
if (!window.__raFrameFix) { window.__raFrameFix = setInterval(function () {
  document.querySelectorAll('iframe').forEach(function (f) { try { var d = f.contentDocument;
    if (d && d.body) { d.body.style.setProperty('background', 'transparent', 'important');
      d.documentElement.style.setProperty('background', 'transparent', 'important'); } } catch (e) {} }); }, 400); }
</script>"""


def page(title: str | None, subtitle: str, body: Callable[[], None], name: str = "Home",
         show_legend: bool = False) -> None:
    """Top strip, title and a demo-badge slot; runs the body, then fills the strip (the body may change
    the current disease)."""
    st.html(f"<style>{theme.stylesheet()}</style>")
    # The map is a component iframe whose page paints Streamlit's native background; make it transparent so the
    # canvas colour of the chosen theme shows through (same-origin iframe, re-applied as it re-renders).
    st.html(_IFRAME_FIX, unsafe_allow_javascript=True)
    backend.reset_demo_flag()
    c_crumb, c_chip, c_theme, c_badge = st.columns([3.2, 3, 2.3, 1.4], vertical_alignment="center", gap="small")
    crumb_slot, chip_slot, badge_slot = c_crumb.empty(), c_chip.empty(), c_badge.empty()
    with c_theme:
        theme.toggle()
    if title:
        st.markdown(f"## {title}")
        if subtitle:
            st.caption(subtitle)
    if show_legend:
        legend(links_only=True)
    try:
        body()
    except backend.DemoUnavailable as exc:
        st.info(f":material/construction: {exc} Try *Krabbe disease* or *X-linked adrenoleukodystrophy*.")
    except Exception as exc:  # noqa: BLE001 - keep the page usable, show the error honestly
        st.error("Something went wrong while asking the atlas. You can retry, or go back to Home and search again.")
        with st.expander("Technical details of the error"):
            st.exception(exc)
    finally:
        crumb_slot.markdown(crumbs(name), unsafe_allow_html=True)
        chip_slot.markdown(current_chip(), unsafe_allow_html=True)
        reasons = backend.demo_reasons()
        if reasons:
            with badge_slot.container():
                st.badge("Demo data", icon=":material/science:", color="orange",
                         help="The atlas data is not ready, so this page shows a small built-in example. "
                              "Do not rely on these records. Reason: " + "; ".join(reasons[:3]))
    if st.session_state.pop("_nav_pending", None):
        st.caption("Open the app through `streamlit run app/streamlit_app.py` to move between pages.")


# ---------------------------------------------------------------------------
# Edge inspector
# ---------------------------------------------------------------------------


def edge_inspector(key: str) -> None:
    """The panel that explains one connection: source, confidence, stance, papers, contradictions."""
    eid = st.session_state.get("inspect_edge")
    with st.container(border=True, key=f"{key}_inspector"):
        top, close = st.columns([4, 1], vertical_alignment="center")
        top.markdown("##### :material/fact_check: Why is this linked?")
        if not eid:
            st.caption("Press **Evidence** next to any connection to see where it comes from, how sure the "
                       "atlas is, and whether any paper disagrees.")
            return
        close.button(":material/close:", key=f"{key}_close", on_click=close_inspector, help="Close",
                     type="tertiary")
        try:
            e = backend.edge(eid)
        except KeyError:
            st.warning(f"Connection {eid} is not in the atlas any more.")
            return
        st.markdown(f"**{esc(e.get('src_label'))}** "
                    f"<span class='ra-rel'>{esc(rel_label(e.get('rel', '')))}</span> "
                    f"**{esc(e.get('dst_label'))}**", unsafe_allow_html=True)
        stance = stance_of(e)
        chips([ev_chip(e.get("evidence_type")), stance_chip(stance)])
        bar = conf_bar(e.get("confidence"))
        if bar:
            st.markdown(bar, unsafe_allow_html=True)
        parts = breakdown_of(e)
        if parts:
            with st.expander("How the confidence is made up"):
                cbd = e.get("confidence_breakdown")
                if isinstance(cbd, dict) and cbd.get("formula"):
                    st.caption(str(cbd["formula"]) + (f" ({cbd['n_independent_sources']} independent source(s))"
                                                      if cbd.get("n_independent_sources") is not None else ""))
                st.markdown('<div class="ra-parts">' + "".join(
                    f'<span>{esc(n)}</span><div class="ra-bar"><i style="width:{v * 100:.0f}%"></i></div>'
                    f"<span>{v:.0%}</span>" for n, v in parts) + "</div>", unsafe_allow_html=True)
        es = e.get("evidence_summary")
        if isinstance(es, dict):
            bits_ = [f"{es[k]} {lbl}" for k, lbl in (("supporting", "supporting"), ("contradicting", "contradicting"),
                                                      ("caveats", "caveat(s)")) if isinstance(es.get(k), int)]
            if bits_:
                st.caption("Evidence tally: " + " · ".join(bits_) + f". {STANCES[stance][3]}")
        plain = backend.plain_edge(e)
        if plain:
            st.markdown(f'<div class="ra-callout">{_cite_html(re.sub(r"\s+([.,;])", r"\1", CITE_RE.sub("", plain)), {})}</div>', unsafe_allow_html=True)
        if e.get("explanation"):
            with st.expander("Technical explanation"):
                st.markdown(gloss(esc(e["explanation"])), unsafe_allow_html=True)

        src = esc(e.get("source") or "unknown source")
        if e.get("source_url"):
            src = f"[{src}]({e['source_url']})"
        bits = [f"**Source:** {src}"]
        if e.get("source_id"):
            bits.append(f"`{esc(e['source_id'])}`")
        if e.get("date"):
            bits.append(f"dated {esc(e['date'])}")
        st.markdown(" · ".join(bits))

        sup = e.get("supporting_papers") or []
        st.markdown(f"**Supporting papers** ({len(sup)})")
        if sup:
            st.markdown("\n".join(
                f"- [{esc(p.get('title') or 'PMID ' + str(p.get('pmid')))}]({pubmed_url(p.get('pmid'))})"
                + (f" ({p['year']})" if p.get("year") else "") for p in sup[:8]))
            if len(sup) > 8:
                st.caption(f"... and {len(sup) - 8} more")
        else:
            st.caption("No paper attached; this link rests on the database record above.")

        con = e.get("contradicting") or []
        st.markdown(f"**Contradicting evidence** ({len(con)})")
        if con:
            for c in con:
                kind = f" ({c['kind']}, weight {c['weight']})" if c.get("kind") else ""
                st.warning(f"**Disagreeing evidence{kind}:** {c.get('note') or ''}  \n"
                           + (f"[{c.get('title') or 'PMID ' + str(c.get('pmid'))}]({pubmed_url(c.get('pmid'))})"
                              if c.get("pmid") else ""),
                           icon=":material/report:")
        else:
            st.caption("No contradicting paper recorded for this link.")
        with st.expander("Show technical detail"):
            st.caption(f"Edge `{e.get('id')}` · `{e.get('src')}` → `{e.get('dst')}` · relation `{e.get('rel')}`")
            if e.get("detail"):
                st.code(json.dumps(e["detail"], indent=2, default=str), language="json")


# ---------------------------------------------------------------------------
# Citations like [e12] in narratives
# ---------------------------------------------------------------------------

CITE_RE = re.compile(r"\[(e\d+)\]")


def cited_edges(text: str) -> list[str]:
    seen: list[str] = []
    for m in CITE_RE.findall(text or ""):
        if m not in seen:
            seen.append(m)
    return seen


def render_cited(text: str) -> str:
    """Markdown with [e12] turned into small numbered markers matching the citation pills."""
    order = {eid: i + 1 for i, eid in enumerate(cited_edges(text))}
    return gloss(CITE_RE.sub(lambda m: f"<sup class='ra-cite'>[{order[m.group(1)]}]</sup>", esc(text)))


# ---------------------------------------------------------------------------
# Small data helpers shared by the page sections
# ---------------------------------------------------------------------------


def as_list(x, *keys: str) -> list:
    """A list from a list, or from a dict holding one under any of `keys` (API shapes are not final)."""
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in keys:
            if isinstance(x.get(k), list):
                return x[k]
    return []


def pick(d: dict, *keys: str, default=None):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return default


def html_table(headers: list[str], rows: list[list[str]]) -> str:
    """Themeable table (st.dataframe draws on a canvas that ignores our palette). Cells are trusted HTML."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="ra-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def money(amount, currency: str | None = "USD") -> str:
    if not isinstance(amount, (int, float)):
        return esc(amount) if amount else "n/a"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency or "", "")
    suffix = "" if sym or not currency else f" {currency}"
    return f"{sym}{amount:,.0f}{suffix}"
