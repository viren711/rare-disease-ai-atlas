"""Deterministic, cited plain-language text. Every sentence ends with one or more [e#] edge citations.

No LLM is required. polish() optionally rewrites the narrative with the local LLM (rag.llm) and keeps the
template text whenever the LLM is unavailable or drops/invents a citation.
"""
from __future__ import annotations

import re

CITE_RE = re.compile(r"\[(e\d+)\]")


def cite(ids) -> str:
    ids = [i for i in dict.fromkeys(ids) if i]
    return "".join(f"[{i}]" for i in ids)


def _pct(x):
    return f"{100 * x:.0f}%"


def explain_edge(e: dict) -> str:
    """One-paragraph explanation for the edge inspector."""
    rel, d = e["rel"], e.get("detail", {}) or {}
    s, t = e["src_label"], e["dst_label"]
    conf = f"confidence {e['confidence']:.2f}, {e['evidence_type']}"
    if rel in ("causes", "associated_with"):
        types = "; ".join(d.get("association_types", [])) or "association"
        n = len(d.get("pmids", []))
        return (f"{s} is linked to {t} ({types}) according to {e['source']}"
                f"{f', with {n} supporting PubMed reference(s)' if n else ''} ({conf}).")
    if rel == "has_phenotype":
        f = d.get("frequency_value")
        freq = f" in about {_pct(f)} of reported patients" if f is not None else ""
        return (f"HPO curators annotate {s} with the phenotype '{t}'{freq} (IC {d.get('ic', '?')}: higher means more "
                f"specific; evidence {', '.join(d.get('evidence_codes', []))}; {conf}).")
    if rel == "in_pathway":
        return (f"Reactome places gene {s} in the pathway '{t}' ({d.get('pathway_genes', '?')} genes in that pathway; "
                f"{conf}).")
    if rel == "mentions":
        how = e["source"]
        return f"The PubMed paper '{s}' mentions {t} (found by {how}; {conf})."
    if rel == "authored":
        return f"{s} is an author of '{t}' (identity by {d.get('disambiguation', 'name')}; {conf})."
    if rel == "studies":
        return (f"ClinicalTrials.gov study {e['src']} ({d.get('kind', 'study')}, {d.get('status', '')}) lists the condition "
                f"'{d.get('condition', '')}', matched to {t} by {d.get('matched_by', '')} ({conf}).")
    if rel == "investigates":
        return (f"{s} is listed as {str(d.get('role', 'official')).replace('_', ' ').lower()} of {e['dst']} "
                f"({d.get('affiliation', '')}); identity: {d.get('identity_match', '')} ({conf}).")
    if rel == "runs":
        return f"{s} is the {str(d.get('role', 'sponsor')).replace('_', ' ')} of {e['dst']} per ClinicalTrials.gov ({conf})."
    if rel == "serves":
        return f"{s} is a patient organisation for {t}; its website was checked live ({d.get('note', '')})."
    if rel == "cites":
        return f"The study record {s} cites the paper '{t}' ({conf})."
    if rel == "subclass_of":
        return f"{s} is a subtype of {t} in {e['source']} ({conf})."
    if rel == "similar_to":
        c = d.get("components", {})
        sp = ", ".join(p["label"] for p in d.get("shared_phenotypes", [])[:4]) or "none"
        pw = ", ".join(p["label"] for p in d.get("shared_pathways", [])[:3]) or "none"
        txt = (f"INFERRED link: {s} and {t} score {d.get('score', e['confidence']):.2f} = "
               f"phenotype {c.get('phenotype', 0):.2f} x{d.get('weights', {}).get('phenotype', '')} + pathway "
               f"{c.get('pathway', 0):.2f} x{d.get('weights', {}).get('pathway', '')} + literature "
               f"{c.get('literature', 0):.2f} x{d.get('weights', {}).get('literature', '')}. Most informative shared "
               f"phenotypes: {sp}. Shared pathways: {pw}. Papers mentioning both: {d.get('co_mention_papers', 0)}. "
               f"Each shared item links to its own curated edge.")
        if d.get("below_main_threshold"):
            txt += " This is a weak cross-group lead kept below the main similarity threshold."
        return txt
    return f"{s} --{rel}--> {t} ({e['source']}, {conf})."


# ------------------------------------------------------------------------------- action narrative
def narrative(plan: dict) -> str:
    A = plan["a"]["label"]
    B = plan["b"]["label"] if plan.get("b") else None
    out = []
    routes = plan.get("routes", [])
    if plan.get("supported") and B:
        for r in routes[:3]:
            out.append(f"{r['text'][0].upper()}{r['text'][1:]} {cite(r['edge_ids'])}.")
        for asset in plan.get("shared_assets", [])[:2]:
            out.append(f"{asset['id']} ({asset['kind'].replace('_', ' ')}, {str(asset.get('status') or '').lower()}) "
                       f"{asset['relevance']} {cite(asset['edge_ids'][:3])}.")
        for r in plan.get("researchers", [])[:1]:
            out.append(f"{r['label']} has published on both diseases ({r['papers_a']} papers on {A}, "
                       f"{r['papers_b']} on {B}) {cite(r.get('edge_ids', [])[:4])}.")
        both = [o for o in plan.get("orgs", []) if o.get("serves_both")]
        for o in (both or plan.get("orgs", []))[:1]:
            who = f"both {A} and {B}" if o.get("serves_both") else o.get("disease_label", A)
            out.append(f"{o['label']} supports families affected by {who} {cite(o.get('edge_ids', [o.get('edge_id')]))}.")
        for d in plan.get("differences_detail", [])[:2]:
            out.append(f"Unlike {d['other']}, {d['disease']} is annotated with '{d['label']}' {cite([d['edge_id']])}.")
    else:
        ev_a = plan.get("evidence_a", [])
        ev_b = plan.get("evidence_b", [])
        if B:
            if ev_a and ev_b:
                out.append(f"The atlas found no supported mechanism or phenotype route between {A} and {B}, although "
                           f"{A} is linked to '{ev_a[0]['label']}' {cite([ev_a[0]['edge_id']])} and {B} to "
                           f"'{ev_b[0]['label']}' {cite([ev_b[0]['edge_id']])}.")
            elif ev_a:
                out.append(f"The atlas found no supported route from {A} to {B}; {A} is linked to "
                           f"'{ev_a[0]['label']}' {cite([ev_a[0]['edge_id']])}, but {B} has almost no curated data.")
        elif ev_a:
            out.append(f"The atlas has no supported neighbour for {A}; its only evidence includes "
                       f"'{ev_a[0]['label']}' {cite([ev_a[0]['edge_id']])}.")
    return " ".join(s for s in out if CITE_RE.search(s))


def check_citations(text: str, valid_ids) -> bool:
    """True if every sentence has at least one citation and all cited ids are valid."""
    valid = set(valid_ids)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if not sentences:
        return False
    for s in sentences:
        ids = CITE_RE.findall(s)
        if not ids or any(i not in valid for i in ids):
            return False
    return True


def polish(text: str, valid_ids) -> str:
    """Optional local-LLM rewrite in plainer language; falls back to the template on any problem."""
    try:
        from rag import llm  # noqa: WPS433 - optional dependency owned by the RAG lane
        prompt = ("Rewrite for a patient advocate in plain language. Keep every [e#] citation exactly and keep each "
                  "sentence ending with its citations. Do not add facts.\n\n" + text)
        fn = getattr(llm, "complete", None) or getattr(llm, "generate", None)
        if fn is None:
            return text
        out = fn(prompt)
        out = out if isinstance(out, str) else str(out.get("text", ""))
        if out and check_citations(out, valid_ids) and set(CITE_RE.findall(out)) <= set(CITE_RE.findall(text)):
            return out.strip()
    except Exception:  # noqa: BLE001 - any LLM failure -> deterministic text
        pass
    return text


# =====================================================================================================
# P1  Patient-friendly explanations
#   plain_explanation(plan, level)  /  plain_disease(card, level)  /  plain_edge(edge)
#   Facts are extracted from structured data -> (optional) local LLM rewrites them -> verified -> else the
#   deterministic template. Results cached on disk by hash of (facts, level, model, prompt version).
# =====================================================================================================
import hashlib
import json
import math
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAIN_CACHE = ROOT / "data" / "cache" / "plain"
LLM_TIMEOUT_S = float(os.environ.get("PLAIN_TIMEOUT", 45))
LLM_MAX_TOKENS = int(os.environ.get("PLAIN_MAX_TOKENS", 260))
MAX_SENTENCES = {"family": 16, "scientist": 18}
_llm_down_until = 0.0          # circuit breaker: skip the LLM for a while after an outage/timeout
TEMPLATE_VERSION = "t8"   # bump when template/verification wording changes (part of the cache key)

# ------------------------------------------------------------------------------------------ glossary
GLOSSARY: dict[str, str] = {
    "lysosome": "A tiny recycling compartment inside cells that breaks down waste with enzymes.",
    "lysosomal storage disease": "A genetic condition where a missing enzyme lets waste pile up inside lysosomes and harm cells.",
    "peroxisome": "A small cell compartment that breaks down certain fats and toxic chemicals.",
    "peroxisomal disease": "A genetic condition where peroxisomes do not work properly, so fats and toxins build up.",
    "enzyme": "A protein that speeds up one specific chemical reaction in the body.",
    "enzyme replacement therapy": "A treatment that gives patients a working copy of a missing enzyme, usually by drip.",
    "substrate reduction therapy": "A treatment that lowers how much of a harmful substance the body makes, so less piles up.",
    "chaperone therapy": "A treatment using a small drug that helps a faulty enzyme fold and work better.",
    "gene therapy": "An experimental treatment that delivers a working copy of a gene into a patient's cells.",
    "HSCT": "Hematopoietic stem cell transplant: replacing a patient's blood-forming cells with donor cells, sometimes used to slow some of these diseases.",
    "bone marrow transplant": "A procedure that replaces a patient's blood-forming cells with healthy donor cells.",
    "leukodystrophy": "A disease that damages the white matter (the insulation around nerve fibres) in the brain.",
    "myelin": "The fatty coating around nerve fibres that helps signals travel quickly.",
    "demyelination": "Loss of the protective coating (myelin) around nerve fibres.",
    "white matter": "The part of the brain made of nerve fibres that carry messages between areas.",
    "phenotype": "A visible or measurable feature of a condition, such as a symptom or test result.",
    "genotype": "The exact version of a gene a person carries.",
    "gene": "A section of DNA that holds the instructions for making one protein.",
    "variant": "A difference in the DNA letters of a gene; some variants are harmless and some cause disease.",
    "mutation": "A change in a gene that can stop it from working normally.",
    "pathogenic variant": "A gene change that is known to cause disease.",
    "autosomal recessive": "A way a condition is inherited: a child is affected only if both parents pass on a changed copy.",
    "X-linked": "Carried on the X chromosome, so it often affects boys more severely than girls.",
    "carrier": "A person with one changed copy of a gene who usually has no symptoms but can pass it on.",
    "pathway": "A chain of steps cells use to do a job, such as breaking down a fat.",
    "biomarker": "A measurable sign in blood, urine or scans that shows what a disease is doing.",
    "natural history study": "A study that follows patients over time, with no new treatment, to learn how the disease normally progresses.",
    "registry": "A shared database where patients or clinics record their health information for research.",
    "clinical trial": "A research study that tests a treatment or approach in people.",
    "phase 1": "An early trial that mainly checks safety in a small group.",
    "phase 2": "A mid-stage trial that checks whether a treatment seems to work and what dose is safe.",
    "phase 3": "A large trial that compares a treatment against the usual care.",
    "recruiting": "Currently looking for people to join the study.",
    "newborn screening": "Blood tests done on babies soon after birth to catch some conditions early.",
    "prevalence": "How many people in a population have a condition at a given time.",
    "incidence": "How many new cases appear in a given period.",
    "orphan drug": "A medicine developed for a rare disease, often with special incentives.",
    "patient organisation": "A group run by or for patients and families that offers support and sometimes funds research.",
    "advocacy group": "A group that supports families and pushes for research and care.",
    "neurodegeneration": "Gradual loss of nerve cells, which leads to worsening movement, thinking or senses.",
    "neurodegenerative": "Causing gradual loss of nerve cells over time.",
    "ataxia": "Poor balance and clumsy, unsteady movement.",
    "spasticity": "Stiff, tight muscles that are hard to move.",
    "hypotonia": "Low muscle tone, so the body feels floppy.",
    "seizure": "A burst of abnormal electrical activity in the brain.",
    "motor deterioration": "Gradual loss of movement skills.",
    "developmental delay": "Reaching milestones such as walking or talking later than expected.",
    "hepatosplenomegaly": "An enlarged liver and spleen.",
    "cardiomyopathy": "A disease of the heart muscle.",
    "glycosphingolipid": "A type of fat made of sugar and lipid parts that is found in cell membranes and in the brain.",
    "sphingolipid": "A type of fat found in cell membranes and the nervous system.",
    "lipid": "A fat or fat-like substance.",
    "very long chain fatty acid": "A very long fat molecule that peroxisomes normally break down; it can build up when they fail.",
    "Reactome": "A public database that maps the chains of reactions (pathways) inside cells.",
    "HPO": "Human Phenotype Ontology: a standard list of symptom names used by doctors and researchers.",
    "Orphanet": "A European reference database about rare diseases.",
    "MONDO": "A shared disease-naming system that links the same disease across databases.",
    "ClinicalTrials.gov": "The US government's public list of clinical studies.",
    "PubMed": "The US National Library of Medicine's index of medical research papers.",
    "ORCID": "A personal ID that tells researchers with similar names apart.",
    "information content": "A score for how specific a symptom is; rare symptoms score higher and are more useful for matching diseases.",
    "inferred": "Worked out by the computer from patterns in the data rather than stated by a source.",
    "curated": "Checked and entered by human experts.",
    "cross-group": "Linking diseases from two different disease families that are not usually studied together.",
    "co-mention": "Two things named in the same paper.",
    "off-label": "Using a medicine for a purpose it was not formally approved for.",
    "repurposing": "Testing an existing medicine for a different disease.",
    "genetic counselling": "A visit with a specialist who explains inheritance, testing and risks for the family.",
    "mechanism": "How something happens inside the body at the cell or gene level.",
    "rare disease": "A condition that affects only a small share of people (in the EU, fewer than 1 in 2,000).",
}
_GLOSS_ALIASES = {"ERT": "enzyme replacement therapy", "SRT": "substrate reduction therapy",
                  "lysosomal storage diseases": "lysosomal storage disease", "leukodystrophies": "leukodystrophy",
                  "phenotypes": "phenotype", "genes": "gene", "variants": "variant", "pathways": "pathway",
                  "registries": "registry", "enzymes": "enzyme", "clinical trials": "clinical trial",
                  "natural history studies": "natural history study", "peroxisomal diseases": "peroxisomal disease",
                  "seizures": "seizure", "mutations": "mutation", "lipids": "lipid", "biomarkers": "biomarker",
                  "bone-marrow transplant": "bone marrow transplant", "stem cell transplant": "HSCT",
                  "newborn screen": "newborn screening", "patient organization": "patient organisation",
                  "IC": "information content"}
_GLOSS_RE = None


def _gloss_re():
    global _GLOSS_RE
    if _GLOSS_RE is None:
        keys = sorted(set(GLOSSARY) | set(_GLOSS_ALIASES), key=len, reverse=True)
        _GLOSS_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w-])", re.I)
    return _GLOSS_RE


def _canon(term: str) -> str | None:
    if term in GLOSSARY:
        return term
    if term in _GLOSS_ALIASES:
        return _GLOSS_ALIASES[term]
    low = term.lower()
    for k in GLOSSARY:
        if k.lower() == low:
            return k
    for k, v in _GLOSS_ALIASES.items():
        if k.lower() == low:
            return v
    return None


def glossary_terms(text: str) -> list[dict]:
    """Glossary terms found in `text` (case-insensitive, whole words, longest match first, non-overlapping).

    -> [{"term": canonical glossary key, "text": matched span, "definition": str, "start": int, "end": int}]
    in order of appearance, so the UI can underline spans and show `definition` as a tooltip.
    """
    out = []
    for m in _gloss_re().finditer(text or ""):
        c = _canon(m.group(1))
        if c:
            out.append({"term": c, "text": m.group(1), "definition": GLOSSARY[c], "start": m.start(), "end": m.end()})
    return out


# ------------------------------------------------------------------------------------------ small helpers
def _ids(*groups) -> list[str]:
    out: list[str] = []
    for g in groups:
        if not g:
            continue
        for i in ([g] if isinstance(g, str) else g):
            if isinstance(i, str) and re.fullmatch(r"e\d+", i) and i not in out:
                out.append(i)
    return out


def _tags(ids) -> str:
    return "".join(f"[{i}]" for i in ids)


def _cap(s: str) -> str:
    s = s.strip()
    return s[:1].upper() + s[1:] if s else s


def _end(s: str, ids) -> str:
    """Sentence with citations placed before the final full stop."""
    s = s.strip()
    end = "?" if s.endswith("?") else "."
    s = s.rstrip(".?")
    return f"{s} {_tags(ids)}{end}" if ids else f"{s}{end}"


def _clean_route(text: str) -> str:
    t = re.sub(r"\s*\(IC [\d.]+\)", "", text)
    t = re.sub(r"\(\s*(\d+) genes\s*\)", r"(\1 genes)", t)
    t = t.replace("the specific phenotype", "the symptom").replace("specific phenotype", "symptom")
    t = t.replace("phenotype", "symptom")
    t = t.replace("act in the same Reactome pathway", "work in the same cell pathway")
    t = t.replace("share the disease gene", "are both linked to the gene")
    t = re.sub(r"^both (.+?) are annotated with", r"\1 are both listed as having", t, flags=re.I)
    t = t.replace("are annotated with", "are both listed as having")
    t = t.replace("annotated with", "listed as having")
    return re.sub(r"\s+", " ", t).strip()


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _extras(plan: dict) -> list[dict]:
    """Optional keys added by the DATA lane (funding, epidemiology, readiness, confidence_breakdown,
    contradictions). Handled defensively; each becomes a fact tied to its own edge ids or the similarity edge."""
    out, default = [], _ids(plan.get("similarity_edge_id"))

    def one(sect, text, ids):
        text = re.sub(r"https?://\S+", "", text).strip()
        ids = ids or default
        if text and ids:
            out.append({"sect": sect, "text": text[:260], "ids": ids[:4]})

    def render(v, depth=0) -> tuple[str, list[str]]:
        if v is None or v == "" or v == [] or v == {}:
            return "", []
        if isinstance(v, (str, int, float, bool)):
            return _fmt(v), []
        if isinstance(v, dict):
            ids = _ids(v.get("edge_ids"), v.get("edge_id"))
            pref = [k for k in ("label", "text", "summary", "note", "stance", "status", "level", "score", "value")
                    if k in v and isinstance(v[k], (str, int, float, bool))]
            keys = pref or [k for k, x in v.items() if isinstance(x, (str, int, float)) and not k.endswith("id")]
            return "; ".join(f"{k.replace('_', ' ')} {_fmt(v[k])}" for k in keys[:6]), ids
        if isinstance(v, list) and depth == 0:
            parts, ids = [], []
            for x in v[:2]:
                s, i = render(x, 1)
                if s:
                    parts.append(s)
                    ids += i
            return " | ".join(parts), _ids(ids)
        return "", []

    spec = (("contradictions", "uncertain", "Conflicting evidence: {}"),
            ("contradiction_stances", "uncertain", "Conflicting evidence: {}"),
            ("confidence_breakdown", "uncertain", "How the confidence is built up: {}"),
            ("readiness", "week", "Overall, the atlas rates how ready this lead is for action as: {}"),
            ("funding", "who", "Funding information: {}"),
            ("epidemiology", "answer", "How common it is: {}"))
    fund = plan.get("funding") or {}
    if isinstance(fund, dict):
        bits, ids = [], []
        sg, sp = fund.get("shared_grants") or [], fund.get("shared_pis") or []
        if sg:
            bits.append(f"{len(sg)} NIH grant(s) fund work on both diseases")
            for g in sg[:2]:
                ids += _ids(g.get("edge_ids"), g.get("edge_id")) if isinstance(g, dict) else []
        if sp:
            bits.append(f"{len(sp)} funded investigator(s) lead projects on both")
        notes = [n for n in (fund.get("gap_notes") or []) if isinstance(n, str)]
        if notes:
            bits.append("a funding gap was found: " + notes[0].rstrip("."))
        if bits:
            one("who", "On funding, " + "; ".join(bits), _ids(ids))
    for key, sect, fmt in spec:
        if key == "funding":
            continue
        s, ids = render(plan.get(key))
        if s:
            one(sect, fmt.format(s), ids)
    return out


# ------------------------------------------------------------------------------------------ fact builders
def _short(label: str, fam: bool) -> str:
    """Family text drops long qualifiers ('... due to saposin B deficiency') to keep sentences readable."""
    return re.split(r"\s+due to\s+", label)[0] if fam else label


def _plan_facts(plan: dict, level: str) -> dict:
    fam = level != "scientist"
    A = _short(plan["a"]["label"], fam)
    B = _short(plan["b"]["label"], fam) if plan.get("b") else None
    F: list[dict] = []

    def add(sect, text, ids=()):
        F.append({"sect": sect, "text": text, "ids": _ids(list(ids))})

    sim = plan.get("similarity")
    sim_ids = _ids(plan.get("similarity_edge_id"))
    routes = plan.get("routes", [])
    if B and plan.get("supported"):
        add("answer", f"Yes, the atlas found a supported lead linking {A} and {B}"
            + (f" (overall similarity score {sim:.2f} on a 0 to 1 scale)" if sim is not None else ""),
            sim_ids or (routes[0]["edge_ids"] if routes else []))
        for r in routes[:3]:
            txt = r["text"]
            for full, short in ((plan["a"]["label"], A), (plan["b"]["label"], B)):
                txt = txt.replace(full, short)
            txt = _clean_route(txt) if fam else txt
            add("answer", _cap(txt), r["edge_ids"])
    elif B:
        add("answer", f"No, the atlas did not find a supported lead between {A} and {B}")
        ea, eb = plan.get("evidence_a", []), plan.get("evidence_b", [])
        if ea:
            add("answer", f"{A} is linked to {ea[0]['label']}", [ea[0].get("edge_id")])
        if eb:
            add("answer", f"{B} is linked to {eb[0]['label']}", [eb[0].get("edge_id")])
    else:
        add("answer", f"The atlas found no related disease to compare with {A}")
        ea = plan.get("evidence_a", [])
        if ea:
            add("answer", f"The only evidence found for {A} includes {ea[0]['label']}", [ea[0].get("edge_id")])
    # uncertainty
    for c in plan.get("caveats", [])[:2]:
        add("uncertain", c.replace("B as a weak lead", f"{B} as a weak lead") if B else c, sim_ids)
    if plan.get("cross_group") and B and plan.get("supported"):
        add("uncertain", f"{A} and {B} belong to different disease families, so this is a lead for researchers to check",
            sim_ids)
    for d in plan.get("differences_detail", [])[:1]:
        add("uncertain", f"The two diseases still differ: only {d['disease']} is listed with {d['label']}",
            [d.get("edge_id")])
    gap = plan.get("gap") or {}
    for m in (gap.get("missing") or [])[:3]:
        add("uncertain", re.sub(r"\s+", " ", m))
    # extras from the DATA lane
    ex = _extras(plan)
    F += [dict(x) for x in ex]
    # who can help
    both = [o for o in plan.get("orgs", []) if o.get("serves_both")]
    for o in (both or plan.get("orgs", []))[:1]:
        who = f"both {A} and {B}" if o.get("serves_both") and B else o.get("disease_label", A)
        add("who", f"{o['label']} is a patient group that supports families affected by {who}",
            o.get("edge_ids") or [o.get("edge_id")])
    for r in plan.get("researchers", [])[:1]:
        add("who", f"Researcher {r['label']} has published {r['papers_a']} papers on {A} and {r['papers_b']} on {B}",
            r.get("edge_ids", [])[:3])
    for a_ in plan.get("shared_assets", [])[:1]:
        kind = a_["kind"].replace("_", " ")
        add("week", f"The {kind} '{a_.get('label') or a_['id']}' ({str(a_.get('status') or 'status unknown').lower()}) "
            f"could be useful for both diseases" if a_.get("disease") == "both" else
            f"The {kind} '{a_.get('label') or a_['id']}' ({str(a_.get('status') or 'status unknown').lower()}) "
            f"is a place to learn from", a_["edge_ids"][:2])
    if gap.get("next_question"):
        q = re.split(r"(?<=[.?!])\s+", gap["next_question"].strip())[0]
        q = re.sub(r"\s*\(e\.g\.[^)]*\)", "", q)
        add("week", f"Question to raise with a specialist or patient group: {q}")
    # edge ids allowed in the answer
    allowed = _ids(*[f["ids"] for f in F], plan.get("similarity_edge_id"),
                   [e for r in routes for e in r.get("edge_ids", [])][:60])
    for i, f in enumerate(F, 1):
        f["id"] = f"F{i}"
    return {"kind": "plan", "subject": f"{A} vs {B}" if B else A, "facts": F, "allowed": allowed,
            "names": {A, B} - {None}, "supported": bool(plan.get("supported") and B),
            "orgs_n": len(plan.get("orgs", []))}


def _disease_facts(card: dict, level: str) -> dict:
    L = card["label"]
    F: list[dict] = []

    def add(sect, text, ids=()):
        F.append({"sect": sect, "text": text, "ids": _ids(list(ids))})

    genes = card.get("genes", [])[:4]
    if genes:
        add("answer", f"{L} is linked to the gene{'s' if len(genes) > 1 else ''} "
            + ", ".join(g["label"] for g in genes), [g.get("edge_id") for g in genes])
    ph = [p for p in card.get("phenotypes", []) if p.get("informative")][:4] or card.get("phenotypes", [])[:4]
    if ph:
        add("answer", f"Symptoms listed for {L} include " + ", ".join(p["label"] for p in ph),
            [p.get("edge_id") for p in ph])
    pw = card.get("pathways", [])[:2]
    if pw:
        add("answer", "Its gene works in the cell pathway " + " and ".join(f"'{p['label']}'" for p in pw),
            [p.get("edge_id") for p in pw])
    cl = card.get("cluster") or {}
    if cl.get("label"):
        add("answer", f"It is grouped with similar diseases under '{cl['label']}'")
    c = card.get("counts", {})
    if c:
        add("uncertain", f"So far the atlas found only {c.get('papers', 0)} papers, {c.get('trials', 0)} studies and "
            f"{c.get('registries', 0)} registries about {L}")
    if not genes:
        add("uncertain", f"No curated gene is recorded for {L}")
    if not ph:
        add("uncertain", f"No symptom annotations are recorded for {L}, so it cannot be compared with others")
    orgs = card.get("orgs", [])[:2]
    for o in orgs:
        add("who", f"{o['label']} is a patient organisation for {L}", [o.get("edge_id")])
    if not orgs:
        add("who", f"No patient organisation for {L} is recorded in the atlas")
    if orgs:
        add("week", f"Contact {orgs[0]['label']} to ask about family support and research news", [orgs[0].get("edge_id")])
    allowed = _ids(*[f["ids"] for f in F])
    for i, f in enumerate(F, 1):
        f["id"] = f"F{i}"
    return {"kind": "disease", "subject": L, "facts": F, "allowed": allowed, "names": {L}, "supported": bool(genes or ph),
            "orgs_n": len(orgs)}


# ------------------------------------------------------------------------------------------ templates
_INLINE = ["leukodystrophy", "lysosomal storage disease", "registry", "natural history study", "gene", "pathway",
           "clinical trial"]
_INLINE_DEF = {
    "gene": "the instructions for making one protein",
    "pathway": "a chain of steps cells use to do a job",
    "registry": "a shared database of patient information for research",
    "natural history study": "a study that follows patients over time to learn how the disease progresses",
    "leukodystrophy": "a disease damaging the insulation around brain nerve fibres",
    "lysosomal storage disease": "waste builds up inside the cell's recycling compartments",
    "clinical trial": "a research study in people",
}


def _inline_defs(text: str, limit: int = 3) -> str:
    done = 0
    masked = {}

    def mask(m):
        k = f"\x00{len(masked)}\x00"
        masked[k] = m.group(0)
        return k
    # never define words that are part of a disease name or quoted title
    text = re.sub(r"'[^']*'|\b(?:[A-Za-z-]+ ){0,3}(?:leukodystrophy|disease|syndrome|disorder)s?\b", mask, text)
    for t in _INLINE:
        if done >= limit:
            break
        m = re.search(r"(?<![\w-])" + re.escape(t) + r"s?(?![\w-])", text)
        if m:
            end = m.end()
            nxt = re.match(r"\s+[A-Z][A-Za-z0-9-]*", text[end:])      # "gene PSAP" -> "gene PSAP (..)"
            if nxt and t == "gene":
                end += nxt.end()
            text = text[:end] + f" ({_INLINE_DEF[t]})" + text[end:]
            done += 1
    for k, v in masked.items():
        text = text.replace(k, v)
    return text


_ADVICE = "This is a research lead, not medical advice, so please talk to your care team before acting on it."


def _select(F: dict) -> dict[str, list[dict]]:
    """The facts the four-line output actually uses (and so the only ones worth sending to the LLM)."""
    by: dict[str, list[dict]] = {k: [] for k in ("answer", "uncertain", "who", "week")}
    for f in F["facts"]:
        by[f["sect"]].append(f)
    cap = {"answer": 3, "uncertain": 4, "who": 3, "week": 3}
    return {k: v[:cap[k]] for k, v in by.items()}


def _template(F: dict, level: str, rewrites: dict | None = None) -> str:
    rewrites = rewrites or {}
    fam = level != "scientist"
    facts = F["facts"]
    kind = F["kind"]
    labels = {"plan": ("Short answer", "What is uncertain", "Who can help", "This week"),
              "disease": ("Overview", "What is uncertain", "Who can help", "This week")}[kind]
    by = _select(F)

    def sent(f):
        t = rewrites.get(f["id"]) or f["text"]
        if fam and ":" in t and t.startswith("Question to raise"):
            t = t.replace("Question to raise with a specialist or patient group: ", "A good question to ask a specialist or patient group is: ")
        return _end(_cap(t), f["ids"])

    lines = []
    ans = [sent(f) for f in by["answer"]]
    lines.append(f"{labels[0]}: " + " ".join(ans))
    unc = [sent(f) for f in by["uncertain"]]
    if kind == "plan" and F["supported"]:
        unc.append("The link is a lead for researchers and doctors to check, not proof." if fam
                   else "The link is a hypothesis for review, not established evidence.")
    elif kind == "plan":
        unc.append("This means the atlas has nothing solid to act on yet, so the gaps below matter more than any guess.")
    if not unc:
        unc.append("Some information may be missing or out of date.")
    lines.append(f"{labels[1]}: " + " ".join(unc))
    who = [sent(f) for f in by["who"]]
    lines.append(f"{labels[2]}: " + (" ".join(who) if who else "No patient group or expert is recorded yet, "
                                       "so ask your clinic to point you to one."))
    week = [sent(f) for f in by["week"]]
    if F["orgs_n"] and kind == "plan" and not any("Contact" in w for w in week):
        o = next((f for f in by["who"] if "patient group" in f["text"]), None)
        if o:
            name = o["text"].split(" is a patient")[0]
            week.insert(0, _end(f"Contact {name} and share this page", o["ids"]))
    week.append(_ADVICE if fam else "Verify the cited edges before relying on this summary.")
    lines.append(f"{labels[3]}: " + " ".join(week))
    text = "\n".join(lines)
    return _inline_defs(text) if fam else text


# ------------------------------------------------------------------------------------------ verification
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
_LIMIT_RE = re.compile(r"\b(no|not|only|may|might|could|unclear|unknown|missing|lack|little|so far|weak|ask|contact|talk|"
                       r"check|share|consider|question|care team|lead|yet|uncertain|whether)\b", re.I)
_LABEL_RE = re.compile(r"^(Short answer|Overview|What is uncertain|Who can help|This week)\s*:\s*", re.I)
_COMMON = set("""the this that these those and for with from have has had are was were been will would should could may might
can not but also than then they them their there here what which who whom whose when where while about into over under
more most some any all each both either other another such same very much many few less least only just still even
yes lead research leads family families doctor doctors disease diseases symptom symptoms patient patients group
groups genes gene study studies paper papers care team week contact share ask check treatment therapy medical advice
database information evidence data link links linked linking found finding findings suggests suggest appears appear
researchers researcher clinic clinics specialist specialists organisation organisations support supports published
similar similarity score scale overall different differ differs still listed listing include includes including
atlas answer short uncertain help this word words people person based means mean good question questions
possible possibly likely unlikely important useful helpful learn learning place places step steps start
before after because since though although however therefore otherwise instead unless until
""".split())


def split_sentences(text: str) -> list[str]:
    body = re.sub(r"\s*\n\s*", " \n", text.strip())
    out = []
    for line in body.split("\n"):
        line = _LABEL_RE.sub("", line.strip())
        out += [s for s in _SENT_SPLIT.split(line) if s.strip()]
    return out


def _norm_llm(text: str) -> str:
    t = text.replace("**", "").replace("__", "")
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)
    t = re.sub(r"([.!?])\s*((?:\[e\d+\]\s*)+)", lambda m: f" {''.join(re.findall(r'\[e\d+\]', m.group(2)))}{m.group(1)}", t)
    t = re.sub(r"\]\s+\[e", "][e", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()


def _syllables(w: str) -> int:
    w = w.lower()
    w = re.sub(r"[^a-z]", "", w)
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(n, 1)


def fk_grade(text: str) -> float:
    """Flesch-Kincaid grade level of `text` (citations and labels ignored)."""
    t = CITE_RE.sub("", re.sub(r"\[e\d+\]", "", text))
    sents = split_sentences(t)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", t)
    if not sents or not words:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return round(0.39 * len(words) / len(sents) + 11.8 * syl / len(words) - 15.59, 1)


def _support_text(F: dict, level: str) -> str:
    base = " ".join(f["text"] for f in F["facts"]) + " " + F["subject"]
    terms = {g["term"] for g in glossary_terms(base)} | set(_INLINE_DEF)
    defs = " ".join(GLOSSARY[t] for t in terms if t in GLOSSARY) + " ".join(_INLINE_DEF.values())
    return base + " " + defs + " " + _ADVICE + " not established evidence hypothesis review Verify cited edges before relying on this summary"


def verify(text: str, F: dict, level: str, require_structure: bool = True) -> dict:
    """Checks on a candidate explanation. Returns {"ok": bool, "failed": [reason], ...metrics}."""
    allowed = set(F["allowed"])
    failed = []
    cited = CITE_RE.findall(text)
    bad = sorted({c for c in cited if c not in allowed})
    if bad:
        failed.append(f"citations outside allowed set: {bad}")
    sents = split_sentences(text)
    n = len(sents)
    if n < 3:
        failed.append("too short")
    if n > MAX_SENTENCES.get(level, 14):
        failed.append(f"too many sentences ({n})")
    if len(text.split()) > 330:
        failed.append("too long")
    if require_structure:
        have = [l for l in text.splitlines() if _LABEL_RE.match(l.strip())]
        if len(have) < 4:
            failed.append("missing section lines")
    uncited = [s for s in sents if not CITE_RE.search(s)]
    bad_uncited = [s for s in uncited if not _LIMIT_RE.search(s) and not s.rstrip().endswith("?")]
    if bad_uncited:
        failed.append(f"{len(bad_uncited)} factual sentence(s) without a citation")
    if cited == [] and F["facts"] and any(f["ids"] for f in F["facts"]):
        failed.append("no citations")
    # fabrication: numbers + capitalised words + very long rare words must be traceable to facts/definitions
    sup = _support_text(F, level)
    sup_low = sup.lower()
    sup_tokens = set(re.findall(r"[a-z0-9][a-z0-9'\-]*", sup_low))
    sup_stems = {t[:6] for t in sup_tokens if len(t) >= 6}
    fabricated = []
    plain = CITE_RE.sub("", re.sub(r"\[e\d+\]", "", text))
    for s in split_sentences(plain):
        toks = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-.]*", s)
        for j, w in enumerate(toks):
            w = w.rstrip(".")
            lw = w.lower()
            if re.fullmatch(r"\d+(\.\d+)?%?", w):
                if w.rstrip("%") not in sup and w not in ("1", "2", "3", "4", "0"):
                    fabricated.append(w)
            elif j > 0 and w[:1].isupper() and len(w) > 1 and lw not in sup_tokens and lw.rstrip("s") not in sup_tokens \
                    and lw not in _COMMON:
                fabricated.append(w)
            elif len(w) >= 11 and lw not in sup_tokens and lw not in _COMMON and lw[:6] not in sup_stems:
                fabricated.append(w)
    fabricated = list(dict.fromkeys(fabricated))
    if len(fabricated) > 0:
        failed.append(f"terms not in the supplied facts: {fabricated[:6]}")
    factual = [s for s in sents if len(s.split()) >= 4]
    pct = round(100 * sum(1 for s in factual if CITE_RE.search(s)) / len(factual), 1) if factual else 0.0
    return {"ok": not failed, "failed": failed, "citations_valid": not bad, "n_sentences": n, "pct_cited": pct,
            "fabricated": fabricated, "fk_grade": fk_grade(text), "n_citations": len(cited)}


# ------------------------------------------------------------------------------------------ LLM + cache
def _cache_path(F: dict, level: str, model: str) -> Path:
    from rag import plain
    key = json.dumps({"f": F["facts"], "l": level, "m": model, "v": plain.PROMPT_VERSION + TEMPLATE_VERSION, "s": F["subject"]},
                     sort_keys=True, default=str)
    return PLAIN_CACHE / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")


def _cache_get(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def _cache_put(p: Path, res: dict) -> None:
    try:
        PLAIN_CACHE.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res))
    except Exception:  # noqa: BLE001
        pass


def _client():
    from rag.llm import LLMClient
    c = LLMClient.from_config()
    c.temperature = 0.1
    c.max_tokens = LLM_MAX_TOKENS
    c.timeout = int(LLM_TIMEOUT_S)
    if os.environ.get("PLAIN_MODEL"):
        c.model = os.environ["PLAIN_MODEL"]
    return c


def _llm_stream(F: dict, level: str, client, facts: list[dict]):
    """Yield raw token pieces; raises on timeout / error / truncation."""
    from rag import plain
    user = plain.user_prompt(F["subject"], [{"id": f["id"], "text": f["text"]} for f in facts])
    t0 = time.time()
    for piece in client.chat_stream(plain.system_prompt(level, F["kind"]), user):
        if time.time() - t0 > LLM_TIMEOUT_S:
            raise TimeoutError("plain-language LLM exceeded the time budget")
        yield piece
    if client.last_finish_reason == "length":
        raise ValueError("LLM output cut off at max_tokens")


_WORD = r"[A-Za-z0-9][A-Za-z0-9'\-.]*"


def _tokens_of(text: str) -> set[str]:
    return {t.lower().rstrip(".") for t in re.findall(_WORD, text)}


def check_rewrite(src: str, out: str) -> list[str]:
    """Reasons a one-sentence paraphrase `out` of fact `src` is unacceptable ([] = fine)."""
    why = []
    if len(re.findall(r"\w+", out)) > 34 or len(out) < 12:
        why.append("length")
    if "[e" in out:
        why.append("model emitted tags")
    st = _tokens_of(src) | _tokens_of(" ".join(GLOSSARY[g["term"]] for g in glossary_terms(src)))
    stems = {t[:5] for t in st if len(t) >= 5}
    new = []
    for j, w in enumerate(re.findall(_WORD, out)):
        w = w.rstrip(".")
        lw = w.lower()
        if lw in st or lw.rstrip("s") in st or lw in _COMMON:
            continue
        if re.fullmatch(r"\d+(\.\d+)?%?", w) or (j > 0 and w[:1].isupper()) or (len(w) >= 9 and lw[:5] not in stems):
            new.append(w)
    if new:
        why.append(f"new terms {new[:4]}")
    must = [w.rstrip(".") for w in re.findall(_WORD, src)[1:]
            if w[:1].isupper() and len(w) > 1 and w.lower() not in _COMMON]       # dropping numbers is safe
    low = _tokens_of(out)
    lost = [m for m in must if m.lower() not in low]
    if must and len(lost) > 0.6 * len(must):
        why.append(f"dropped {lost[:4]}")
    src_c = {t[:5] for t in _tokens_of(src) if len(t) > 3 and t not in _COMMON and not t[0].isdigit()}
    out_c = {t[:5] for t in _tokens_of(out)}
    if src_c and len(src_c & out_c) / len(src_c) < 0.6:
        why.append("lost too much of the fact's wording")
    neg = re.compile(r"\b(no|not|only|without|none|never|weak|weakly|missing|unknown)\b", re.I)
    if bool(neg.search(src)) != bool(neg.search(out)):
        why.append("hedge/negation changed")
    return why


def _parse_rewrites(raw: str, facts: list[dict]) -> tuple[dict, dict]:
    by_id = {f["id"]: f for f in facts}
    ok, bad = {}, {}
    for line in raw.splitlines():
        m = re.match(r"^\s*\**(F\d+)\**\s*[:.)\-]\s*(.+?)\s*$", line)
        if not m or m.group(1) not in by_id:
            continue
        fid, out = m.group(1), re.sub(r"\*+|\s+", " ", m.group(2)).strip()
        why = check_rewrite(by_id[fid]["text"], out)
        if why:
            bad[fid] = why
        elif fid not in ok:
            ok[fid] = out
    return ok, bad


def _result(text, cites, source, checks, extra=None):
    out = {"text": text, "citations": cites, "source": source, "checks": checks}
    out.update(extra or {})
    return out


MIN_REWRITE_SHARE = 0.6        # fraction of facts the LLM must paraphrase acceptably to count as source="llm"


def _run(F: dict, level: str, use_llm: bool, stream: bool):
    """Generator of {"type": "token"|"final"}; shared by the blocking and streaming entry points."""
    global _llm_down_until
    t0 = time.time()
    client = None
    model = "template"
    if use_llm:
        try:
            client = _client()
            model = client.model
        except Exception:  # noqa: BLE001
            client = None
    path = _cache_path(F, level, model if client else "template")
    hit = _cache_get(path)
    if hit:
        hit["checks"] = {**hit["checks"], "cached": True}
        yield {"type": "final", **hit}
        return
    tmpl = _template(F, level)
    tmpl_res = _result(tmpl, _cited_ids(tmpl), "template",
                       {**verify(tmpl, F, level), "model": None, "latency_s": 0.0, "cached": False})
    sel = [f for v in _select(F).values() for f in v]
    if client is None or time.time() < _llm_down_until or not sel:
        yield {"type": "final", **tmpl_res}
        return
    raw, reason = "", None
    try:
        for piece in _llm_stream(F, level, client, sel):
            raw += piece
            if stream:
                yield {"type": "token", "text": piece}
    except Exception as e:  # noqa: BLE001 - unreachable, timeout, truncation
        reason = f"llm error: {type(e).__name__}: {str(e)[:120]}"
        _llm_down_until = time.time() + 90
    lat = round(time.time() - t0, 1)
    ok, bad = ({}, {}) if reason else _parse_rewrites(raw, sel)
    share = len(ok) / len(sel) if sel else 0.0
    info = {"model": model, "latency_s": lat, "cached": False, "n_facts": len(sel), "n_rewritten": len(ok),
            "rewrite_share": round(share, 2), "rejected_lines": bad}
    if reason is None and share >= MIN_REWRITE_SHARE:
        text = _template(F, level, ok)
        chk = verify(text, F, level)
        if chk["ok"]:
            res = _result(text, _cited_ids(text), "llm", {**chk, **info})
            _cache_put(path, res)
            yield {"type": "final", **res}
            return
        reason = "assembled text failed verification: " + "; ".join(chk["failed"])[:200]
    elif reason is None:
        reason = f"only {len(ok)}/{len(sel)} lines passed per-fact checks"
    res = {**tmpl_res, "checks": {**tmpl_res["checks"], **info, "llm_rejected": reason, "llm_output": raw[:900]}}
    if not reason.startswith("llm error"):
        _cache_put(path, res)      # deterministic outcome for these facts: do not pay for the LLM again
    yield {"type": "final", **res}


def _cited_ids(text: str) -> list[str]:
    return list(dict.fromkeys(CITE_RE.findall(text)))


def _final(gen):
    last = None
    for ev in gen:
        last = ev
    return {k: v for k, v in last.items() if k != "type"}


# ------------------------------------------------------------------------------------------ public API
def plain_explanation(plan: dict, level: str = "family", use_llm: bool = True) -> dict:
    """Plain-language rewrite of an action_plan, every factual sentence ending in [e#] markers.

    level: "family" (grade ~8-10, jargon defined in brackets) or "scientist" (concise technical).
    Returns {"text": str (4 lines: Short answer / What is uncertain / Who can help / This week),
             "citations": [edge ids used], "source": "llm"|"template",
             "checks": {"ok", "failed": [...], "citations_valid", "n_sentences", "pct_cited", "fabricated": [...],
                        "fk_grade", "model", "latency_s", "cached", optional "llm_rejected"}}.
    Blocking: instant on a cache hit or when the LLM is down (template); otherwise 10-45 s.
    """
    return _final(_run(_plan_facts(plan, level), level, use_llm, stream=False))


def plain_explanation_stream(plan: dict, level: str = "family", use_llm: bool = True):
    """Generator: {"type": "token", "text": str} ... (UNVERIFIED draft, show as provisional), then exactly one
    {"type": "final", "text", "citations", "source", "checks"} that REPLACES the streamed draft."""
    yield from _run(_plan_facts(plan, level), level, use_llm, stream=True)


def plain_disease(card: dict, level: str = "family", use_llm: bool = True) -> dict:
    """Same contract as plain_explanation for a disease_card dict (labels: Overview / What is uncertain / ...)."""
    return _final(_run(_disease_facts(card, level), level, use_llm, stream=False))


def plain_disease_stream(card: dict, level: str = "family", use_llm: bool = True):
    yield from _run(_disease_facts(card, level), level, use_llm, stream=True)


_EV_WORD = {"curated": "checked by human experts", "extracted": "found automatically in papers, so less certain",
            "inferred": "worked out by the atlas computer, not stated by any source"}


def plain_edge(edge: dict, level: str = "family") -> dict:
    """Instant deterministic plain-language sentence(s) for one edge dict (api.edge output). Never calls the LLM.
    Returns the same {"text","citations","source": "template","checks"} shape."""
    e = edge
    rel, d = e.get("rel"), e.get("detail", {}) or {}
    s, t = e.get("src_label", e.get("src", "?")), e.get("dst_label", e.get("dst", "?"))
    eid = e.get("id")
    ev = _EV_WORD.get(e.get("evidence_type"), "")
    conf = e.get("confidence")
    fam = level != "scientist"
    if rel in ("causes", "associated_with"):
        core = f"Changes in the gene {s} are linked to {t}" if rel == "causes" else f"{s} is linked to {t}"
    elif rel == "has_phenotype":
        f = d.get("frequency_value")
        core = f"{t} is a recognised symptom or feature of {s}" + (f", seen in about {100 * f:.0f}% of reported patients" if f is not None else "")
    elif rel == "in_pathway":
        core = f"The gene {s} works in the cell pathway '{t}'"
    elif rel == "similar_to":
        core = f"The atlas computer scores {s} and {t} as similar by shared symptoms, pathways and papers"
        if d.get("below_main_threshold"):
            core += ", but only weakly"
    elif rel == "studies":
        core = f"The clinical study {e.get('src')} lists a condition matched to {t}"
    elif rel in ("serves", "runs"):
        core = f"{s} is a patient organisation that supports families affected by {t}" if rel == "serves" else f"{s} runs {t}"
    elif rel == "mentions":
        core = f"The paper '{s}' mentions {t}"
    elif rel == "authored":
        core = f"{s} is an author of '{t}'"
    elif rel == "investigates":
        core = f"{s} leads the study {e.get('dst')}"
    elif rel == "subclass_of":
        core = f"{s} is a type of {t}"
    else:
        core = f"{s} is connected to {t}"
    sents = [_end(core, [eid])]
    if ev:
        sents.append(_end(f"This link was {ev}" + (f" (confidence {conf:.2f} out of 1)" if conf is not None else ""), [eid]))
    n_con = len(e.get("contradicting") or [])
    if n_con:
        sents.append(_end(f"{n_con} paper(s) point the other way, so treat it with caution", [eid]))
    text = " ".join(sents)
    if fam:
        text = _inline_defs(text, 1)
    F = {"allowed": _ids(eid), "facts": [{"text": f"{conf:.2f} {ev} {d.get('frequency_value', '')}", "ids": [eid]}] if conf is not None else [],
         "subject": f"{s} {t}", "kind": "edge", "supported": True, "orgs_n": 0}
    chk = verify(text, F, level, require_structure=False)
    chk.update({"model": None, "latency_s": 0.0, "cached": False})
    return _result(text, _cited_ids(text), "template", chk)
