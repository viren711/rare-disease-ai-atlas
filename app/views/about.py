"""How it works: the pipeline in five steps, where each fact comes from, how fresh it is, and the limits.

This page is the source for the README's "how it works" section; keep the two consistent.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backend, ui  # noqa: E402

STEPS = [
    ("1", "Collect", "Public databases and papers are downloaded: disease names (MONDO), symptoms (HPO), genes "
                     "(Orphanet, HPO), biological processes (Reactome), PubMed abstracts, PubTator gene and disease "
                     "mentions, and ClinicalTrials.gov studies. A small hand-checked list holds patient groups."),
    ("2", "Connect", "Everything becomes one graph of things (diseases, genes, symptoms, mechanisms, papers, "
                     "researchers, studies, patient groups) joined by links. Each link remembers its source, date and "
                     "confidence."),
    ("3", "Compare", "Diseases are compared by what they share, not by name: how many rare symptoms (weighted by "
                     "rarity), mechanisms, and papers mention both. The score is 0.5 symptoms + 0.3 mechanism + "
                     "0.2 literature."),
    ("4", "Explain", "For a chosen pair the atlas finds the shared route, the studies and registries that cover both "
                     "diseases, the people who published on both, and what differs. Every sentence cites its links. "
                     "If no supported route exists it says what it searched and what is missing."),
    ("5", "Answer", "Questions about the literature use the PubMed abstracts and a language model that runs on this "
                    "computer. It only answers when retrieved abstracts support every statement."),
]

HOW_TO_READ = [
    ("Line style", "Solid = a database record. Dashed = found in papers by text mining. Dotted = inferred by the "
                   "atlas, a hypothesis to check."),
    ("Shape and colour", "Each kind of thing has its own colour and shape, so nothing relies on colour alone: "
                         "circle disease, diamond gene, triangle symptom, hexagon mechanism, square study, star "
                         "patient group."),
    ("Confidence", "A 0 to 100% bar for how sure the atlas is. Curated database links score high; text-mined and "
                   "inferred links score lower. Open any link's Evidence to see how the number is made up."),
    ("Stance", "Supported, mixed, weak or unsupported: whether the sources agree, some disagree, there is little "
               "direct evidence, or none. Disagreeing papers are always listed."),
    ("Readiness", "Strong, moderate, weak or none: how ready a lead is to act on, from how many curated links and "
                  "existing studies back it."),
]

LIMITS = [
    "Two disease families only (lysosomal storage and peroxisomal diseases), so a missing link may just mean the "
    "disease is outside the atlas.",
    "Inferred links are hypotheses. They are meant to start a conversation with an expert, not to settle one.",
    "Text mining makes mistakes. Papers that disagree are shown when the atlas knows about them, but it cannot know "
    "about every paper.",
    "Nothing here is medical advice. Variant and funding sections appear only when those sources are loaded in this "
    "build; otherwise they say so.",
    "The plain-language wording is written by a small local language model from the cited plan; check anything "
    "important against the evidence.",
]


def pipeline() -> None:
    st.markdown("#### From public data to a sourced plan")
    for col, (n, title, text) in zip(st.columns(5, gap="small"), STEPS):
        with col, st.container(border=True, height="stretch"):
            st.markdown(f"<span class='ra-step-n'>{n}</span> **{title}**", unsafe_allow_html=True)
            st.caption(text)


def how_to_read() -> None:
    st.markdown("#### How to read the screens")
    ui.legend()
    for k, v in HOW_TO_READ:
        st.markdown(f"**{k}.** {v}")
    st.markdown(ui.readiness_badge("strong") + " " + ui.readiness_badge("moderate") + " "
                + ui.readiness_badge("weak") + " " + ui.readiness_badge("none") + " &nbsp; "
                + " ".join(ui.stance_chip(s) for s in ui.STANCES), unsafe_allow_html=True)


def sources() -> None:
    st.markdown("#### Data sources and freshness")
    s = backend.stats()
    srcs = s.get("sources") or []
    if not srcs:
        ui.empty_state("No source list is available.", "It is read from the atlas build record.")
        return
    st.caption("When each source was last downloaded. Re-run the build scripts to refresh; nothing updates by "
               "itself while the app runs.")
    rows = []
    for x in srcs:
        name = ui.esc(x.get("name"))
        if x.get("url"):
            name = f'<a href="{ui.esc(x["url"])}" target="_blank" rel="noopener">{name}</a>'
        rec = x.get("records")
        rows.append([name, ui.esc(x.get("retrieved") or "unknown"),
                     ui.esc(f"{rec:,}" if isinstance(rec, int) else (rec or "n/a"))])
    st.markdown(ui.html_table(["Source", "Retrieved", "Size / records"], rows), unsafe_allow_html=True)
    with st.container(horizontal=True, gap="small", wrap=True):
        st.badge("Literature index ready" if s.get("index_ready") else "Literature index not built",
                 icon=":material/check_circle:" if s.get("index_ready") else ":material/hourglass_empty:",
                 color="green" if s.get("index_ready") else "orange")
        st.badge("Local language model ready" if s.get("llm_ready") else "Language model off",
                 icon=":material/check_circle:" if s.get("llm_ready") else ":material/power_off:",
                 color="green" if s.get("llm_ready") else "gray")


def limits() -> None:
    st.markdown("#### What to keep in mind")
    st.markdown("\n".join(f"- {t}" for t in LIMITS))


def body() -> None:
    pipeline()
    how_to_read()
    sources()
    limits()
    ui.nav_button("Try the guided demo", "home", key="about_demo", icon=":material/play_circle:", type="primary")


ui.page(":material/info: How it works",
        "Where the facts come from, how links are scored, and what the atlas cannot tell you.", body,
        name="How it works")
