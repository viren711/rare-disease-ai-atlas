"""The app's only door into the atlas: atlas.api, or demo fixtures when the atlas is not ready.

    value = backend.call("disease_card", "MONDO:0009499")

Rules:
  * ATLAS_FAKE=1                 -> always fixtures.
  * otherwise atlas.api is called; if it raises ImportError / FileNotFoundError /
    NotImplementedError (or the function does not exist yet) -> fixtures for that call.
  * any fixture value marks the current page run as "demo"; ui.page() then shows a
    Demo data badge at the top. Fixture data is never shown without it.
  * any other exception from the real atlas propagates (shown as an error, not hidden).

Real results are cached with st.cache_data; fixture results are not cached at all, so the
app switches to real data as soon as the graph lane's files appear.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import streamlit as st

from app import fixtures

FALLBACK_ERRORS = (ImportError, FileNotFoundError, NotImplementedError)
DEMO_FLAG = "_demo_reasons"


class DemoUnavailable(Exception):
    """The real atlas is not ready and the demo data does not cover this id."""


def fake_forced() -> bool:
    return os.environ.get("ATLAS_FAKE", "").strip().lower() not in ("", "0", "false", "no")


def _is_fallback(exc: BaseException) -> bool:
    if isinstance(exc, FALLBACK_ERRORS):
        return True
    # atlas/graph.py exists but does not define the function yet
    return isinstance(exc, AttributeError) and type(getattr(exc, "obj", None)).__name__ == "module"


def _mark_demo(reason: str) -> None:
    try:
        reasons = st.session_state.setdefault(DEMO_FLAG, [])
        if reason not in reasons:
            reasons.append(reason)
    except Exception:  # noqa: BLE001 - outside a Streamlit run
        pass


def demo_reasons() -> list[str]:
    return list(st.session_state.get(DEMO_FLAG, []))


def reset_demo_flag() -> None:
    st.session_state[DEMO_FLAG] = []


@st.cache_data(show_spinner=False, max_entries=1024)
def _real(name: str, args: tuple, kwargs: tuple) -> Any:
    from atlas import api

    return getattr(api, name)(*args, **dict(kwargs))


@st.cache_data(show_spinner=False, ttl=30)
def _real_stats() -> dict:
    from atlas import api

    return api.stats()


def _fixture(name: str, args: tuple, kwargs: dict, reason: str) -> Any:
    _mark_demo(reason)
    try:
        return getattr(fixtures, name)(*args, **kwargs)
    except KeyError as exc:
        raise DemoUnavailable(
            f"The atlas data is not built yet and the demo data does not include {exc}."
        ) from exc


def call(name: str, *args, **kwargs) -> Any:
    """Call atlas.api.<name>, falling back to fixtures as described above."""
    reason = "ATLAS_FAKE=1"
    if not fake_forced():
        try:
            if name == "stats":
                return _real_stats()
            frozen = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in kwargs.items()))
            return _real(name, args, frozen)
        except Exception as exc:  # noqa: BLE001
            if not _is_fallback(exc):
                raise
            reason = f"{name}: {type(exc).__name__}: {exc}"
    return _fixture(name, args, kwargs, reason)


def ask_stream(question: str, disease_id: str | None = None) -> Iterator[dict]:
    """atlas.api.ask_stream with the same fallback (the import happens on first next())."""
    reason = "ATLAS_FAKE=1"
    if not fake_forced():
        try:
            from atlas import api

            gen = api.ask_stream(question, disease_id=disease_id)
            first = next(gen)
        except StopIteration:
            return
        except Exception as exc:  # noqa: BLE001
            if not _is_fallback(exc):
                raise
            reason = f"ask_stream: {type(exc).__name__}: {exc}"
        else:
            yield first
            yield from gen
            return
    _mark_demo(reason)
    yield from fixtures.ask_stream(question, disease_id=disease_id)


# Convenience wrappers -------------------------------------------------------


def search(q: str, types: list[str] | None = None, k: int = 10) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    return call("search", q, types=types, k=k)


def disease_card(disease_id: str) -> dict:
    return call("disease_card", disease_id)


def neighbours(disease_id: str, k: int = 10) -> list[dict]:
    return call("neighbours", disease_id, k=k)


def subgraph(node_id: str, depth: int = 1, max_nodes: int = 60, types: list[str] | None = None) -> dict:
    return call("subgraph", node_id, depth=depth, max_nodes=max_nodes, types=types)


def edge(edge_id: str) -> dict:
    return call("edge", edge_id)


def action_plan(disease_a: str, disease_b: str | None = None) -> dict:
    return call("action_plan", disease_a, disease_b)


def stats() -> dict:
    return call("stats")


# ---------------------------------------------------------------------------
# Optional pieces: new atlas.api functions and atlas.explain, wired defensively.
# A missing function / signature mismatch / failure gives None (the page shows an empty state);
# it never raises and never raises the "Demo data" badge on its own.
# ---------------------------------------------------------------------------


def optional(name: str, *args, **kwargs) -> Any:
    """atlas.api.<name>(...) if it exists and works, else None. Fixtures only with ATLAS_FAKE=1 (or when
    the whole atlas is already running on fixtures)."""
    use_fixture = fake_forced()
    if not use_fixture:
        try:
            frozen = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in kwargs.items()))
            return _real(name, args, frozen)
        except Exception as exc:  # noqa: BLE001 - optional section: degrade quietly
            use_fixture = _is_fallback(exc) and bool(demo_reasons())
            if not use_fixture:
                return None
    fn = getattr(fixtures, name, None)
    if fn is None:
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def funding(disease_id: str) -> Any:
    return optional("funding", disease_id)


def variants(disease_id: str) -> Any:
    return optional("variants", disease_id)


def researchers_for(disease_id: str, k: int = 10) -> Any:
    out = optional("researchers_for", disease_id, k=k)
    if out is None:  # the real signature may not take k
        out = optional("researchers_for", disease_id)
    return out


def assets_for(disease_id: str) -> Any:
    return optional("assets_for", disease_id)


def has_api(name: str) -> bool:
    """True when the real atlas.api defines `name` (used to say 'not available in this build')."""
    if fake_forced():
        return hasattr(fixtures, name)
    try:
        from atlas import api

        return hasattr(api, name)
    except Exception:  # noqa: BLE001
        return False


@st.cache_data(show_spinner=False)
def _explain_module_names() -> tuple[str, ...]:
    try:
        from atlas import explain

        return tuple(n for n in dir(explain) if not n.startswith("_"))
    except Exception:  # noqa: BLE001
        return ()


def has_explain(name: str) -> bool:
    return fake_forced() and hasattr(fixtures, name) or (not fake_forced() and name in _explain_module_names())


def _explain_mod():
    if fake_forced():
        return fixtures
    try:
        from atlas import explain

        return explain
    except Exception:  # noqa: BLE001
        return None


def plain_available() -> bool:
    m = _explain_mod()
    return bool(m and hasattr(m, "plain_explanation_stream"))


def plain_stream(kind: str, obj_id: str, other_id: str | None = None, level: str = "family") -> Iterator[dict]:
    """Stream atlas.explain.plain_explanation_stream (kind='plan') or plain_disease_stream (kind='disease').

    Yields {"type":"token","text"} (unverified draft) ... then exactly one {"type":"final", text, citations,
    source: 'llm'|'template', checks}. A cached answer or a downed LLM yields only the final event. On any
    failure yields a final event with text=None so the UI can say so."""
    m = _explain_mod()
    try:
        if m is None:
            raise ImportError("atlas.explain")
        if kind == "plan":
            plan = call("action_plan", obj_id, other_id)
            gen = m.plain_explanation_stream(plan, level)
        else:
            card = call("disease_card", obj_id)
            gen = m.plain_disease_stream(card, level)
        yield from gen
    except Exception as exc:  # noqa: BLE001 - the explainer is optional
        yield {"type": "final", "text": None, "citations": [], "source": "error", "checks": {"error": str(exc)}}


def plain_edge(edge: dict, level: str = "family") -> str | None:
    """Instant, template-based sentence for the edge inspector (never calls the LLM)."""
    m = _explain_mod()
    fn = getattr(m, "plain_edge", None) if m else None
    if fn is None:
        return None
    try:
        out = fn(edge, level)
        return out.get("text") if isinstance(out, dict) else out
    except Exception:  # noqa: BLE001
        return None


_BASE_GLOSSARY = {
    "phenotype": "A symptom or clinical sign, such as seizures or low muscle tone.",
    "pathway": "A chain of steps the body's cells use to make or break down something.",
    "mechanism": "The biological process that goes wrong in a disease.",
    "information content": "How rare a symptom is across all diseases. Rarer symptoms say more about a disease.",
    "inferred": "Worked out by the atlas from overlapping data, not stated in a source. A hypothesis to check.",
    "curated": "Entered and checked by experts in a database.",
    "registry": "A list of patients (with consent) kept so researchers can study a disease.",
    "natural history": "How a disease normally progresses without treatment. Needed to judge whether a therapy works.",
    "ORCID": "A permanent ID that tells apart researchers who share a name.",
    "prevalence": "How many people in a population have the disease.",
    "inheritance": "How the disease passes through families (for example autosomal recessive).",
    "variant": "A change in a gene's DNA. Some variants cause disease.",
}


@st.cache_data(show_spinner=False)
def glossary() -> dict[str, str]:
    """Plain-language definitions: the atlas.explain GLOSSARY when it exists, plus a built-in base."""
    out = dict(_BASE_GLOSSARY)
    try:
        from atlas import explain

        g = getattr(explain, "GLOSSARY", None)
        if isinstance(g, dict):
            out.update({str(k): str(v if not isinstance(v, dict) else v.get("plain") or v.get("definition") or v)
                        for k, v in g.items()})
    except Exception:  # noqa: BLE001
        pass
    return out
