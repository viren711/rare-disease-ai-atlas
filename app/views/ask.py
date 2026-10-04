"""Ask the literature: a question in plain words -> an answer cited to PubMed abstracts, or a clear refusal.

The draft streams in as it is written and is styled as provisional until the citation check has
run (the final event); a refused answer replaces the draft entirely.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import backend, ui  # noqa: E402

EXAMPLES = [
    "Does stem cell transplantation help children with Krabbe disease?",
    "What do Krabbe disease and X-linked adrenoleukodystrophy have in common?",
    "Is newborn screening useful for X-ALD?",
    "Which gene therapy approaches exist for metachromatic leukodystrophy?",
]
ALL = "__all__"


def _use_example() -> None:
    ex = st.session_state.get("ask_example")
    if ex:
        st.session_state["ask_q"] = ex
    st.session_state["ask_example"] = None


def scope_options() -> dict[str, str]:
    opts = {ALL: "All diseases"}
    pre = st.session_state.pop("ask_disease", None)
    if pre:
        opts[pre["id"]] = pre["label"]
        st.session_state["ask_scope"] = pre["id"]
    focus = st.session_state.get("focus")
    if focus and focus.get("type") == "Disease":
        opts.setdefault(focus["id"], focus["label"])
    for did, label in st.session_state.get("recent", []):
        opts.setdefault(did, label)
    sel = st.session_state.get("ask_scope")
    if sel and sel not in opts:
        opts[sel] = st.session_state.get("ask_scope_labels", {}).get(sel, sel)
    st.session_state["ask_scope_labels"] = {**st.session_state.get("ask_scope_labels", {}), **opts}
    return opts


def render_sources(sources: list[dict], key: str) -> None:
    for s in sources:
        title = s.get("title") or f"PMID {s.get('pmid')}"
        head = f"**[{s.get('n')}]** {title}" + (f" ({s['year']})" if s.get("year") else "")
        with st.expander(head):
            bits = [x for x in [s.get("journal"), f"PMID {s.get('pmid')}" if s.get("pmid") else None,
                                f"match {s['score']:.2f}" if isinstance(s.get("score"), (int, float)) else None] if x]
            st.caption(" · ".join(bits))
            if s.get("snippet"):
                st.markdown(f"<div style='border-left:3px solid var(--ra-faint);padding-left:12px'>{ui.esc(s['snippet'])}"
                            f"</div>", unsafe_allow_html=True)
            if s.get("pmid"):
                st.markdown(f"[Open on PubMed]({ui.pubmed_url(s['pmid'])})")


def render_answer(res: dict, key: str) -> None:
    if res.get("refused"):
        reason = res.get("reason") or "The indexed abstracts do not support an answer."
        if res.get("status") == "error":
            st.error(f"**The literature search is not available right now.** {reason}", icon=":material/error:")
        elif res.get("status") == "out_of_scope":
            st.info(f"**No answer from the literature.** {reason}", icon=":material/block:")
        else:
            st.warning(f"**No answer from the literature.** {reason}", icon=":material/block:")
        st.caption("The atlas only answers when retrieved abstracts support every statement. Try rephrasing, "
                   "removing the disease filter, or asking about a disease in the atlas.")
        if res.get("sources"):
            with st.expander("Closest abstracts that were considered"):
                render_sources(res["sources"], key)
        return
    st.markdown(res.get("answer") or "")
    if res.get("sources"):
        st.markdown("**Sources**")
        render_sources(res["sources"], key)
    else:
        st.caption("No sources were returned with this answer; treat it with caution.")


def stream_answer(question: str, disease_id: str | None) -> dict:
    status, draft = st.empty(), st.empty()
    status.caption(":material/progress_activity: Searching the abstracts (the first question can take a few seconds while the model wakes up)...")
    text, final = "", None
    t0 = time.time()
    for ev in backend.ask_stream(question, disease_id=disease_id):
        if ev.get("type") == "token":
            text += ev.get("text", "")
            status.caption("Drafting an answer — citations not yet checked")
            draft.markdown(f"<div style='opacity:.65;border-left:3px solid var(--ra-warn);padding-left:12px'>"
                           f"{ui.esc(text)}</div>", unsafe_allow_html=True)
        elif ev.get("type") == "final":
            final = {k: v for k, v in ev.items() if k != "type"}
    status.empty()
    draft.empty()
    if final is None:
        final = {"answer": "", "refused": True, "reason": "The answer stream ended without a result.",
                 "sources": []}
    final["_elapsed"] = time.time() - t0
    return final


def body() -> None:
    opts = scope_options()
    with st.form("ask_form", border=True, enter_to_submit=False):
        q = st.text_area("Your question", key="ask_q", height=90,
                         placeholder="e.g. Does stem cell transplantation help children with Krabbe disease?")
        c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
        scope = c1.selectbox("Only use papers about", list(opts), key="ask_scope", format_func=lambda i: opts[i])
        submitted = c2.form_submit_button("Ask", type="primary", width="stretch", icon=":material/send:")
    st.pills("Examples", EXAMPLES, key="ask_example", on_change=_use_example)

    history = st.session_state.setdefault("ask_history", [])
    if submitted:
        if not q.strip():
            st.warning("Type a question first, or pick an example.")
        else:
            disease_id = None if scope == ALL else scope
            with st.chat_message("user"):
                st.markdown(ui.esc(q))
                if disease_id:
                    st.caption(f"Only papers about {opts[scope]}")
            with st.chat_message("assistant"):
                res = stream_answer(q.strip(), disease_id)
                render_answer(res, f"a{len(history)}")
            history.append({"q": q.strip(), "scope": opts[scope] if disease_id else None, "res": res})

    past = history[:-1] if submitted and q.strip() else history
    if past:
        st.markdown("#### Earlier questions")
        for i, turn in reversed(list(enumerate(past))):
            with st.chat_message("user"):
                st.markdown(ui.esc(turn["q"]))
                if turn.get("scope"):
                    st.caption(f"Only papers about {turn['scope']}")
            with st.chat_message("assistant"):
                render_answer(turn["res"], f"h{i}")


ui.page(":material/menu_book: Ask the literature",
        "Plain-language answers from PubMed abstracts on lysosomal and peroxisomal diseases, each cited, "
        "or a clear 'not supported'.",
        body, name="Ask the literature")
