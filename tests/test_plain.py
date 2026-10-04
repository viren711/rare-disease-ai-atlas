"""Offline tests for atlas.explain plain-language layer (no LLM, no graph needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atlas import explain as x  # noqa: E402

PLAN = {
    "a": {"id": "MONDO:1", "label": "Alpha disease"}, "b": {"id": "MONDO:2", "label": "Beta disease due to X"},
    "supported": True, "similarity": 0.41, "similarity_edge_id": "e9", "cross_group": True,
    "routes": [{"kind": "shared_gene", "text": "Alpha disease and Beta disease due to X share the disease gene GENE1",
                "edge_ids": ["e1", "e2"]}],
    "orgs": [{"label": "Help Group", "serves_both": True, "edge_ids": ["e3"], "disease_label": "Alpha disease"}],
    "researchers": [], "shared_assets": [], "caveats": [], "gap": None, "differences_detail": [],
    "funding": {"label": "grant data sparse", "edge_ids": ["e5"]},
}


def test_template_cited_and_valid():
    r = x.plain_explanation(PLAN, "family", use_llm=False)
    assert r["source"] == "template" and r["checks"]["citations_valid"] and r["checks"]["ok"], r["checks"]
    assert set(r["citations"]) <= {"e1", "e2", "e3", "e5", "e9"}
    assert "Short answer:" in r["text"] and "This week:" in r["text"]
    assert "Beta disease" in r["text"] and "due to X" not in r["text"]      # family text uses short names


def test_gap_plan_without_b():
    plan = {"a": {"id": "M", "label": "Gamma"}, "b": None, "supported": False, "evidence_a": [
        {"label": "gene G", "edge_id": "e4"}], "gap": {"missing": ["No gene recorded"], "next_question": "Who sees it? More."}}
    r = x.plain_explanation(plan, use_llm=False)
    assert r["checks"]["ok"] and "no related disease" in r["text"].lower()


def test_verify_rejects_bad_citation_and_fabrication():
    F = x._plan_facts(PLAN, "family")
    bad = ("Short answer: Alpha disease is linked to Beta disease [e999].\nWhat is uncertain: Maybe Zorgon 77 matters [e1].\n"
           "Who can help: Help Group may help [e3].\nThis week: Ask a doctor [e3].")
    v = x.verify(bad, F, "family")
    assert not v["ok"] and not v["citations_valid"] and v["fabricated"]


def test_check_rewrite():
    src = "Alpha disease and Beta disease are both linked to the gene GENE1"
    assert x.check_rewrite(src, "Alpha disease and Beta disease are both linked to the gene GENE1.") == []
    assert x.check_rewrite(src, "Alpha disease and Beta disease are both linked to the gene GENE1 and 42 drugs.")
    assert x.check_rewrite("Only Alpha disease has this", "Alpha disease has this symptom.")


def test_glossary():
    assert len(x.GLOSSARY) >= 60
    hits = x.glossary_terms("Enzyme replacement therapy and HSCT help some leukodystrophies; see the registry.")
    terms = [h["term"] for h in hits]
    assert terms[:3] == ["enzyme replacement therapy", "HSCT", "leukodystrophy"] and "registry" in terms
    assert all(x_["start"] < x_["end"] for x_ in hits)


def test_edge_plain():
    e = {"id": "e7", "rel": "causes", "src_label": "GENE1", "dst_label": "Alpha disease", "evidence_type": "inferred",
         "confidence": 0.5, "detail": {}, "contradicting": [{"pmid": "1"}]}
    r = x.plain_edge(e)
    assert r["citations"] == ["e7"] and "not stated by any source" in r["text"] and "point the other way" in r["text"]


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(x, "PLAIN_CACHE", tmp_path)
    monkeypatch.setattr(x, "_client", lambda: (_ for _ in ()).throw(RuntimeError("no llm")))
    r1 = x.plain_explanation(PLAN)           # client creation fails -> template, never raises
    assert r1["source"] == "template"
