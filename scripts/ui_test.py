#!/usr/bin/env python3
"""UI regression tests for the Rare Disease Atlas app, driven by Streamlit's AppTest harness.

Runs every page with demo data (ATLAS_FAKE=1 is forced here) and clicks the real controls:
search -> synonym resolution -> disease card -> similar diseases -> evidence inspector -> map
(node click simulated through the `_test_map_click` hook, since the graph is a custom component)
-> action plan (supported lead and honest gap) -> ask the literature (answer and refusal).

    .venv/bin/python scripts/ui_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["ATLAS_FAKE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

ENTRY = str(ROOT / "app" / "streamlit_app.py")
HOME = str(ROOT / "app" / "views" / "home.py")
EXPLORE = str(ROOT / "app" / "views" / "explore.py")
PLAN = str(ROOT / "app" / "views" / "action_plan.py")
ASK = str(ROOT / "app" / "views" / "ask.py")
KRABBE, XALD, ML4 = "MONDO:0009499", "MONDO:0018544", "MONDO:0009653"
TIMEOUT = 60

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'  ok  ' if cond else ' FAIL '}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def md(at) -> str:
    return "\n".join(m.value for m in at.markdown)


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    for kind in ("warning", "info", "success", "error"):
        parts += [str(x.value) for x in getattr(at, kind)]
    parts += [c.value for c in at.code]
    return "\n".join(parts)


def buttons(at, label: str):
    return [b for b in at.button if label in (b.label or "")]


def no_exc(at, name: str) -> None:
    check(f"{name}: no exception", not at.exception, "; ".join(str(e.value)[:200] for e in at.exception))


def demo_badge(at) -> bool:
    return "Demo data" in md(at)


# ---------------------------------------------------------------------------


def test_entry_and_home() -> None:
    print("\n== Entry point and Home ==")
    at = AppTest.from_file(ENTRY, default_timeout=TIMEOUT)
    at.run()
    no_exc(at, "entrypoint")
    check("home renders the hero", "Find the dots nobody connected" in md(at))
    check("demo data badge is shown with fixture data", demo_badge(at))

    at = AppTest.from_file(HOME, default_timeout=TIMEOUT)
    at.run()
    no_exc(at, "home")
    check("one global search box on the main panel",
          any("Search a disease, gene, symptom or mechanism" in t.label for t in at.main.text_input))
    for label in ("Build an action plan", "Find our community", "Explore by mechanism"):
        check(f"persona entry point: {label}", bool(buttons(at, label)))
    check("coverage stats shown", "What the atlas covers" in md(at) and "Database record" in md(at))
    check("nothing lives in the sidebar", len(at.sidebar.text_input) + len(at.sidebar.selectbox) == 0)
    # empty search warns instead of navigating
    sub = [b for b in at.button if b.label == "Search"]
    if sub:
        sub[0].click().run()
        check("empty search asks for input", any("Type a disease" in w.value for w in at.warning))
    at.pills[0].set_value("ABCD1").run()
    check("example pill fills the search box", at.text_input[0].value == "ABCD1", at.text_input[0].value)
    no_exc(at, "home after example")


def explore_with(query: str) -> AppTest:
    at = AppTest.from_file(EXPLORE, default_timeout=TIMEOUT)
    at.session_state["explore_query"] = query
    at.session_state["explore_new_query"] = True
    at.run()
    return at


def test_explore() -> None:
    print("\n== Explore ==")
    at = AppTest.from_file(EXPLORE, default_timeout=TIMEOUT)
    at.run()
    no_exc(at, "explore (empty)")
    check("search box visible before any query",
          any("Search a disease" in t.label for t in at.main.text_input))

    # Search through the real form
    at.text_input[0].set_value("globoid cell leukodystrophy")
    [b for b in at.button if b.label == "Search"][0].click().run()
    no_exc(at, "explore search")
    text = md(at)
    check("synonym resolved and explained", "is another name for **Krabbe disease**" in text)
    check("disease card header", "### Krabbe disease" in text)
    check("causal gene shown", "GALC" in text)
    check("distinctive symptoms section", "Distinctive symptoms" in text and "Increased CSF protein" in text)
    check("mechanism shown", "Glycosphingolipid metabolism" in text)
    check("patient group link shown", "huntershope.org" in text)
    check("similar diseases listed", "X-linked adrenoleukodystrophy" in text)
    check("cross-family badge on a neighbour", "Different disease family" in text)
    check("why: shared distinctive symptoms", "Shares distinctive symptoms" in text)
    check("inspector placeholder before any click", "Press **Evidence**" in all_text(at))

    # Open evidence for the X-ALD neighbour (second 'Why linked?')
    why = buttons(at, "Why linked?")
    check("each neighbour has a 'Why linked?' button", len(why) >= 2, str(len(why)))
    if len(why) >= 2:
        why[1].click().run()
        no_exc(at, "edge inspector")
        t = all_text(at)
        check("inspector shows the relation", "is similar to" in t)
        check("inspector shows evidence type", "Inferred by the atlas" in t)
        check("inspector shows source", "Source:" in t)
        check("inspector lists supporting papers with PubMed links", "pubmed.ncbi.nlm.nih.gov" in t)
        check("inspector shows contradicting evidence", "Disagreeing evidence" in t)
        close = [b for b in at.button if b.key and b.key.endswith("_close")]
        if close:
            close[0].click().run()
            check("inspector closes", "Press **Evidence**" in all_text(at))

    # Evidence on a gene (icon button in the card)
    gene_btn = [b for b in at.button if b.key and b.key.startswith("gene_ev_")]
    if gene_btn:
        gene_btn[0].click().run()
        check("gene evidence opens a curated record", "Database record" in all_text(at)
              and "is a known cause of" in all_text(at))

    # Map view
    at.segmented_control(key="explore_view").set_value("Map").run()
    no_exc(at, "map view")
    check("map legend shown", "Inferred by the atlas" in md(at) and "Study / registry" in md(at))
    check("map summary caption", any("things," in c.value and "links" in c.value for c in at.caption))
    # simulate clicking the X-ALD node
    at.session_state["_test_map_click"] = XALD
    at.run()
    no_exc(at, "map node click")
    check("clicked node is expanded", XALD in at.session_state["map_expanded"])
    check("selected-node panel lists its links", any(b.key and b.key.startswith("sel_ev_") for b in at.button))
    check("re-centre button offered", bool(buttons(at, "the centre")))
    # inspect a link from the map picker
    at.selectbox(key="map_edge_pick").set_value(_edge_ids(at)[0]).run()
    no_exc(at, "map edge pick")
    check("map edge picker opens the inspector", "Confidence" in all_text(at) or "Source:" in all_text(at))
    # re-centre on X-ALD
    buttons(at, "the centre")[0].click().run()
    no_exc(at, "map re-centre")
    check("re-centred on X-ALD", at.session_state["focus"]["id"] == XALD)

    # Open a neighbour from the list
    at = explore_with("Krabbe disease")
    opens = buttons(at, "Open")
    if opens:
        opens[0].click().run()
        no_exc(at, "open neighbour")
        check("Open re-focuses the card", at.session_state["focus"]["id"] != KRABBE)

    # Gene / mechanism searches open the same graph
    at = explore_with("Glycosphingolipid metabolism")
    no_exc(at, "mechanism search")
    t = md(at)
    check("mechanism focus lists linked diseases", "Diseases linked to this mechanism" in t and "Krabbe disease" in t)
    check("mechanism spanning note or diseases listed", "Fabry disease" in t)
    at = explore_with("ABCD1")
    no_exc(at, "gene search")
    check("gene focus lists its disease", "X-linked adrenoleukodystrophy" in md(at))

    # Nothing found
    at = explore_with("zzqqxx not a disease")
    no_exc(at, "no match")
    check("no-match warning", any("Nothing in the atlas matches" in w.value for w in at.warning))

    # Devon: community first
    at = AppTest.from_file(EXPLORE, default_timeout=TIMEOUT)
    at.session_state["persona"] = "family"
    at.session_state["explore_query"] = "Krabbe"
    at.session_state["explore_new_query"] = True
    at.run()
    no_exc(at, "family persona")
    t = md(at)
    check("family persona: patient groups before symptoms",
          t.find("Patient groups") < t.find("Distinctive symptoms"))
    check("explore controls are in the main panel",
          len(at.sidebar.selectbox) == 0 and len(at.main.selectbox) >= 1)


def _edge_ids(at) -> list[str]:
    """Edge ids on the current map (selectbox options are shown formatted, values are ids)."""
    from app import fixtures
    focus = at.session_state["focus"]["id"]
    return [e["id"] for e in fixtures.subgraph(focus, 1, 45)["edges"]]


def test_plan() -> None:
    print("\n== Action plan ==")
    at = AppTest.from_file(PLAN, default_timeout=TIMEOUT)
    at.run()
    no_exc(at, "plan (empty)")
    check("disease picker on the main panel", any(t.label == "Your disease" for t in at.main.text_input))

    at = AppTest.from_file(PLAN, default_timeout=TIMEOUT)
    at.session_state["plan_a"] = {"id": KRABBE, "label": "Krabbe disease"}
    at.session_state["plan_b"] = {"id": XALD, "label": "X-linked adrenoleukodystrophy"}
    at.run()
    no_exc(at, "plan supported")
    t = all_text(at)
    check("supported verdict banner", "Supported lead" in md(at))
    check("readiness badge on the plan", "Readiness: moderate" in md(at))
    check("epidemiology compared", "How common, and when it starts" in md(at) and "Autosomal recessive" in md(at))
    check("funding section", "Funding" in md(at) and "(demo) Natural history" in md(at))
    check("collaborators ranked, expander present", any("More collaborators" in e.label for e in at.expander))
    check("plain-words narrative with citation markers", "In plain words" in t and "ra-cite" in t)
    check("step-by-step path with evidence types", "The connection, step by step" in t and "Database record" in t)
    check("shared assets listed", "Stem cell transplant" in t and "clinicaltrials.gov/study" in t)
    check("researcher on both diseases", "Dr. Lena Hartmann" in t)
    check("patient groups with links", "aldconnect.org" in t)
    check("differences to check", "What must be checked before joining forces" in t and "Different gene" in t)
    check("next steps", "Next steps" in t)
    check("copyable proposal", any("Subject: Shared research" in c.value for c in at.code))
    check("proposal download button", len(at.get("download_button")) >= 1)
    check("compare-with selector shows neighbour", at.selectbox(key="plan_b_pick").value == XALD)

    steps = [b for b in at.button if b.key and b.key.startswith("step_ev_")]
    if steps:
        steps[0].click().run()
        no_exc(at, "plan step evidence")
        check("step evidence opens inspector", "has the symptom" in all_text(at) and "Source:" in all_text(at))
    cite = at.pills(key="plan_cite")
    cite.set_value(cite.options[2] if len(cite.options) > 2 else cite.options[0]).run()
    no_exc(at, "citation pill")
    check("citation pill opens inspector", at.session_state["inspect_edge"] is not None)

    # change B to "best match" and then A by typing
    at.selectbox(key="plan_b_pick").set_value("__best__").run()
    no_exc(at, "plan best match")
    at.text_input(key="plan_a_q").set_value("Mucolipidosis").run()
    no_exc(at, "plan gap")
    t = all_text(at)
    check("gap panel shown prominently", "No supported route found" in t)
    check("gap: what was searched", "What we searched" in t)
    check("gap: what is missing", "What is missing" in t)
    check("gap: next question", "Next question to test" in t)
    check("gap proposal is a request for help", any("Open question about" in c.value for c in at.code))

    at.text_input(key="plan_a_q").set_value("qqqzzz").run()
    check("unknown disease warns", any("No disease in the atlas matches" in w.value for w in at.warning))


def test_ask() -> None:
    print("\n== Ask the literature ==")
    at = AppTest.from_file(ASK, default_timeout=TIMEOUT)
    at.run()
    no_exc(at, "ask (empty)")
    at.text_area(key="ask_q").set_value("Does stem cell transplantation help children with Krabbe disease?")
    [b for b in at.button if b.label == "Ask"][0].click().run()
    no_exc(at, "ask answer")
    check("answer rendered", "Demo answer" in md(at))
    check("sources listed as expanders", len(at.expander) >= 2, str(len(at.expander)))
    check("PubMed links in sources", "pubmed.ncbi.nlm.nih.gov" in md(at))

    at.text_area(key="ask_q").set_value("What is the weather tomorrow?")
    [b for b in at.button if b.label == "Ask"][0].click().run()
    no_exc(at, "ask refusal")
    check("refusal shown clearly", any("No answer from the literature" in w.value
                                       for w in list(at.warning) + list(at.info)))
    check("earlier question kept", "Earlier questions" in md(at))

    at = AppTest.from_file(ASK, default_timeout=TIMEOUT)
    at.session_state["ask_disease"] = {"id": KRABBE, "label": "Krabbe disease"}
    at.run()
    check("disease filter pre-set from Explore", at.selectbox(key="ask_scope").value == KRABBE)
    at.pills(key="ask_example").set_value(at.pills(key="ask_example").options[0]).run()
    check("example fills the question", "stem cell" in at.text_area(key="ask_q").value)


def test_theme() -> None:
    print("\n== Theme ==")
    import re
    from app import theme, ui

    at = AppTest.from_file(HOME, default_timeout=TIMEOUT)
    at.run()
    tog = at.segmented_control(key="theme_widget")
    check("theme toggle is visible in the top strip", tog is not None and list(tog.options) == ["Auto", "Light", "Dark"])
    check("default theme is auto", at.session_state["theme_pref"] == "auto")
    tog.set_value("Dark").run()
    no_exc(at, "theme dark")
    check("dark choice persisted", at.session_state["theme_pref"] == "dark")
    tog = at.segmented_control(key="theme_widget")
    tog.set_value("Light").run()
    check("light choice persisted", at.session_state["theme_pref"] == "light")
    tog.set_value("Auto").run()
    check("back to auto", at.session_state["theme_pref"] == "auto")

    def lum(h):
        h = h.lstrip("#")
        r, g, b = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4  # noqa: E731
        return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)

    def cr(a, b):
        x, y = sorted([lum(a), lum(b)], reverse=True)
        return (x + .05) / (y + .05)

    for mode, p in theme.PALETTES.items():
        for fg, bg, need in [("text", "bg", 7), ("muted", "bg", 4.5), ("muted", "surface", 4.5), ("faint", "surface", 4.5),
                             ("accent", "bg", 4.5), ("on_accent", "accent", 4.5), ("link", "bg", 4.5),
                             ("good", "good_bg", 4.5), ("warn", "warn_bg", 4.5), ("bad", "bad_bg", 4.5),
                             ("info", "info_bg", 4.5), ("label", "canvas", 4.5)]:
            check(f"{mode}: {fg} on {bg} contrast >= {need}", cr(p[fg], p[bg]) >= need, f"{cr(p[fg], p[bg]):.1f}")
        for k in p:
            if k.startswith(("t_", "e_")):
                check(f"{mode}: {k} visible on the canvas (>=3:1)", cr(p[k], p["canvas"]) >= 3, f"{cr(p[k], p['canvas']):.1f}")
    g = ui.gloss("The white matter and a lipid pathway in the gene; white matter again")
    check("glossary tooltips never nest inside attributes", g.count("<abbr") == g.count("</abbr>") and 'title="A fat' in g
          and 'title="The part of the brain made of nerve fibres that carry messages between areas.">' in g
          and "\">" not in g.replace('">', "", 0).split("title=")[1].split('">')[0])
    check("both palettes define the same keys", set(theme.PALETTES["light"]) == set(theme.PALETTES["dark"]))
    css = (ROOT / "app" / "style.css").read_text()
    check("style.css has no hard-coded colours", not re.search(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"/\*.*?\*/", "", css, flags=re.S)))
    ui_src = (ROOT / "app" / "ui.py").read_text()
    check("ui.py has no hard-coded colours", not re.search(r"[\"']#[0-9a-fA-F]{6}[\"']", ui_src))
    from app import ui
    check("node types have distinct shapes", len({ui.NODE_SHAPES[t] for t in ("Disease", "Gene", "Phenotype", "Pathway",
                                                                                 "Trial", "PatientOrg", "Researcher")}) == 7)


def test_new_sections() -> None:
    print("\n== Funding / variants / collaborators / assets / epidemiology / explain ==")
    at = explore_with("Krabbe disease")
    no_exc(at, "explore for sections")
    t = md(at)
    check("epidemiology tiles on the card", "Prevalence" in t and "Autosomal recessive" in t)
    check("explain-simply button on the card", bool(buttons(at, "Explain simply")))
    for view, needle in [("Funding", "Grants found"), ("Variants", "Pathogenic"), ("Collaborators", "Why:"),
                         ("Assets", "Registries")]:
        at.segmented_control(key="explore_view").set_value(view).run()
        no_exc(at, f"{view} view")
        check(f"{view} section renders", needle in md(at), md(at)[-300:])
    at.segmented_control(key="explore_view").set_value("Similar diseases").run()
    buttons(at, "Explain simply")[0].click().run()
    no_exc(at, "explain simply (stub)")
    check("plain-language callout shown", "(demo)" in md(at) and "Explained simply" in md(at))
    check("source badge shown", "Template text" in md(at))
    at.segmented_control(key="explore_plain_view").set_value("Technical view").run()
    check("toggle to technical view", "Technical view:" in md(at))

    at = AppTest.from_file(PLAN, default_timeout=TIMEOUT)
    at.session_state["plan_a"] = {"id": KRABBE, "label": "Krabbe disease"}
    at.session_state["plan_b"] = {"id": XALD, "label": "X-linked adrenoleukodystrophy"}
    at.run()
    buttons(at, "Explain simply")[0].click().run()
    no_exc(at, "plan explain simply")
    check("plan plain callout has the four-line format", "Short answer:" in md(at) and "This week:" in md(at))
    # inspector shows stance chip, confidence bar and the breakdown
    steps = [b for b in at.button if b.key and b.key.startswith("step_ev_")]
    steps[0].click().run()
    t = md(at)
    check("inspector: confidence bar", "ra-bar" in t)
    check("inspector: stance chip", any(x in t for x in ("Supported", "Mixed", "Weak", "Unsupported")))
    check("inspector: contradicting evidence count", "Contradicting evidence" in t)
    # guided demo
    at = AppTest.from_file(HOME, default_timeout=TIMEOUT)
    at.run()
    check("guided demo button on Home", bool(buttons(at, "Guided demo")))
    buttons(at, "Guided demo")[0].click().run()
    check("guided demo primes the tour", at.session_state["tour"] == 1)
    at = AppTest.from_file(str(ROOT / "app" / "views" / "about.py"), default_timeout=TIMEOUT)
    at.run()
    no_exc(at, "how it works")
    check("how-it-works page explains the pipeline and sources", "Collect" in md(at) and "Data sources and freshness" in md(at))


def test_backend_fallback() -> None:
    print("\n== Backend fallback rules ==")
    from app import backend

    check("ImportError falls back", backend._is_fallback(ImportError("x")))
    check("FileNotFoundError falls back", backend._is_fallback(FileNotFoundError("x")))
    check("NotImplementedError falls back", backend._is_fallback(NotImplementedError()))
    check("ValueError does not fall back", not backend._is_fallback(ValueError("x")))
    check("missing module attribute falls back", backend._is_fallback(AttributeError("x", obj=os)))


def main() -> int:
    test_entry_and_home()
    test_explore()
    test_plan()
    test_ask()
    test_theme()
    test_new_sections()
    test_backend_fallback()
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
