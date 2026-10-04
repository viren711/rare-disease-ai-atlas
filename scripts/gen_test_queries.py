"""Regenerate tests/queries.txt from the live graph (expected values are the atlas's own current output).

    .venv/bin/python scripts/gen_test_queries.py
"""
import sys, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from atlas import api, graph

n = pd.read_parquet(str(ROOT / "data/graph/nodes.parquet"))
D = n[n.type == "Disease"]
lab = dict(zip(D.id, D.label))
cards = {}
for did in D.id:
    try:
        cards[did] = api.disease_card(did)
    except Exception:
        pass
BROAD = {"lysosomal storage disease", "lysosomal lipid storage disorder", "sphingolipidosis", "peroxisomal disease",
         "leukodystrophy", "peroxisomal single enzyme/protein defect", "mucopolysaccharidosis"}
pool = sorted((d for d in cards if cards[d]["counts"]["papers"] > 0 and lab[d] not in BROAD),
              key=lambda d: -cards[d]["counts"]["papers"])
gaps = [d for d in cards if cards[d]["counts"]["papers"] == 0 and not cards[d]["genes"]]
out = []


def section(num, title, how, lines):
    assert len(lines) == 50, (title, len(lines))
    out.append(f"\n{'=' * 100}\n{num}. {title}\n{'=' * 100}\nHOW TO TEST: {how}\n")
    for i, l in enumerate(lines, 1):
        out.append(f"{i:>2}. {l}")


def pick(offset, k, extra=()):
    base = pool[offset:offset + k]
    return base


# ---------------------------------------------------------------- 1 Search
S = [
    ("Krabbe disease", "exact name"), ("globoid cell leukodystrophy", "synonym of Krabbe"), ("GALC deficiency", "synonym"),
    ("Krabbe's", "possessive form"), ("X-ALD", "abbreviation"), ("adrenoleukodystrophy", "exact"),
    ("Gaucher disease", "exact"), ("Fabry disease", "exact"), ("Pompe disease", "synonym of glycogen storage disease II"),
    ("Hunter syndrome", "synonym of MPS II"), ("Hurler syndrome", "synonym of MPS I"), ("Sanfilippo", "partial name (MPS III)"),
    ("Niemann-Pick type C", "exact"), ("NPC", "abbreviation"), ("Tay-Sachs", "partial name"), ("Sandhoff disease", "exact"),
    ("MLD", "abbreviation"), ("metachromatic leukodystrophy", "exact"), ("Zellweger syndrome", "peroxisomal"),
    ("Refsum disease", "peroxisomal"), ("cystinosis", "single word"), ("Batten disease", "synonym of neuronal ceroid lipofuscinosis"),
    ("CLN3", "gene"), ("mucolipidosis", "exact"), ("Morquio", "partial; sparse-data disease"), ("Wolman disease", "exact"),
    ("acatalasia", "sparse peroxisomal disease"),
    ("GALC", "gene"), ("ABCD1", "gene (X-ALD)"), ("GBA1", "gene (Gaucher)"), ("GLA", "gene (Fabry)"), ("GAA", "gene (Pompe)"),
    ("IDS", "gene (Hunter)"), ("NPC1", "gene"), ("HEXA", "gene (Tay-Sachs)"), ("CYP27A1", "gene (CTX)"), ("AMACR", "gene"),
    ("PEX1", "gene (Zellweger)"),
    ("seizures", "symptom"), ("cherry-red spot", "rare symptom"), ("hepatosplenomegaly", "symptom"), ("ataxia", "symptom"),
    ("hearing loss", "symptom, plain wording"),
    ("glycosphingolipid catabolism", "pathway"), ("bile acid synthesis", "pathway"), ("autophagy", "pathway"),
    ("peroxisomal lipid metabolism", "pathway"),
    ("Krabe disease", "TYPO"), ("gauchers desease", "TYPO"), ("fabri disease", "TYPO"),
]
lines = []
for q, note in S:
    try:
        r = api.search(q, k=1)
        exp = f"top: {r[0]['label']} ({r[0]['type']})" if r else "no match -> friendly empty state"
    except Exception as e:
        exp = f"ERROR {e}"
    lines.append(f"{q:<32} [{note}]  =>  {exp}")
lines.append(f"{'asdfghjkl':<32} [nonsense]  =>  " + ("no match -> friendly empty state" if not api.search("asdfghjkl", k=1) else "weak match; must not crash"))
lines = lines[:50] if len(lines) >= 50 else lines
while len(lines) < 50:
    lines.append("")
S_extra = ["niemann pik", "adrenoleukodistrophy", "pizza recipe"]
for q in S_extra:
    if len(lines) and lines[-1] == "":
        r = api.search(q, k=1)
        lines[lines.index("")] = f"{q:<32} [TYPO/off-topic]  =>  " + (f"top: {r[0]['label']} ({r[0]['type']})" if r else "no match -> friendly empty state")
lines = [l for l in lines if l][:50]
section(1, "SEARCH AND SYNONYM RESOLUTION (Home and Explore search box)",
        "Type the query in the single search box (Enter or Search). Expected value is the current top result; the 'Look for' filter "
        "should narrow by type; synonyms are announced ('X is another name for Y').", lines)

# ---------------------------------------------------------------- 2 Disease card
sel = pool[:36] + gaps[:8] + pool[60:66]
lines = []
for d in sel:
    c = cards[d]
    genes = ",".join(g["label"] for g in c["genes"]) or "none"
    cnt = c["counts"]
    prev = (c["epidemiology"].get("prevalence") or "n/a")[:34]
    lines.append(f"{lab[d]:<44} => genes {genes}; ~{cnt['papers']} papers; {cnt['trials']} trials, {cnt['registries']} registries; "
                 f"{cnt['orgs']} patient groups; prevalence {prev}" + ("  [SPARSE: gap/empty states, no crash]" if not c['genes'] and not cnt['papers'] else ""))
section(2, "DISEASE CARD (Explore)",
        "Search the disease, open the card. Check genes, mechanism, distinctive vs common symptoms, epidemiology tiles, patient groups, "
        "counts line; every item has an Evidence button. Counts are from the current build and may drift after a rebuild.", lines)

# ---------------------------------------------------------------- 3 Similar diseases + map
sel = pool[0:50]
lines = []
for d in sel:
    nb = api.neighbours(d, k=10)
    top = "; ".join(f"{x['label'][:34]} {x['score']:.2f}" for x in nb[:3]) or "no neighbours -> explained empty state"
    cross = next((x for x in nb if x["cross_group"]), None)
    cr = f" | cross-family: {cross['label'][:34]} {cross['score']:.2f}{' (weak)' if cross['weak'] else ''}" if cross else ""
    lines.append(f"{lab[d]:<44} => {top}{cr}")
section(3, "SIMILAR DISEASES AND INTERACTIVE MAP (Explore)",
        "Open the disease, 'Similar diseases': check scores, 'Why linked?', the 'Only diseases from a different family' toggle, then the Map tab "
        "(click a node to expand, 'Two steps', 'Reset', node-type filter). Own subtypes must not be listed as similar.", lines)

# ---------------------------------------------------------------- 4 Action plan
pairs = []
for d in pool[:34]:
    nb = api.neighbours(d, k=10)
    if nb:
        pairs.append((d, nb[0]["id"], "top neighbour"))
for d in pool[:40]:
    nb = [x for x in api.neighbours(d, k=10) if x["cross_group"]]
    if nb and all((d, nb[0]["id"]) != (a, b) for a, b, _ in pairs) and len(pairs) < 44:
        pairs.append((d, nb[0]["id"], "cross-family"))
gap_pairs = [("MONDO:0012436" if False else None, None, "")]
sparse = gaps[:3]
nogo = [(api.search("acatalasia")[0]["id"], api.search("Fabry disease")[0]["id"], "expected NO ROUTE"),
        (api.search("Krabbe disease")[0]["id"], api.search("cystinosis")[0]["id"], "unrelated pair"),
        (api.search("Gaucher disease")[0]["id"], api.search("Zellweger")[0]["id"], "unrelated pair"),
        (api.search("Fabry disease")[0]["id"], api.search("Refsum disease")[0]["id"], "unrelated pair")]
for g in sparse:
    nogo.append((g, pool[0], "sparse disease vs rich disease"))
pairs = pairs[:46 - len(nogo)] + nogo
lines = []
for a, b, why in pairs[:50]:
    try:
        p = api.action_plan(a, b)
        res = f"supported={p['supported']}; readiness={p['readiness']['level']}; assets={len(p['shared_assets'])}; people={len(p['researchers'])}; groups={len(p['orgs'])}"
        if not p["supported"]:
            res += "; GAP panel with searched/missing/next question"
    except Exception as e:
        res = f"ERROR {e}"
    lines.append(f"{lab[a][:34]:<34} vs {lab[b][:34]:<34} [{why}] => {res}")
extra = 0
while len(lines) < 50:
    d = pool[extra]; extra += 1
    p = api.action_plan(d, None)
    lines.append(f"{lab[d][:34]:<34} vs (default best neighbour: {p['b']['label'][:30] if p['b'] else 'none'}) [default B] => supported={p['supported']}; readiness={p['readiness']['level']}")
section(4, "ACTION PLAN (Action plan page)",
        "Pick disease A, then 'Compare with'. Check the banner, readiness badge + reasons, path steps, shared assets, people, patient groups, "
        "'what differs', next steps, caveats (stopped trials), Copy/Download proposal. Unsupported pairs must show the GAP panel, not an error.", lines[:50])

# ---------------------------------------------------------------- 5 Explain simply
lines = []
for a, b, why in pairs[:25]:
    lines.append(f"PLAN  {lab[a][:34]} vs {lab[b][:34]} [{why}] => 4 parts (Short answer / What is uncertain / Who can help / This week); "
                 f"[n] chips open the inspector; badge 'AI-rewritten, citations verified' or 'Template text'; no raw field names")
for d in pool[:25]:
    lines.append(f"CARD  {lab[d][:44]} => 'Overview:' summary, jargon hover-defined, cited; first run 10-30 s with spinner, second run instant (cache)")
section(5, "EXPLAIN SIMPLY (plain-language summaries)",
        "Click 'Explain simply' on the plan or the Explore card; toggle to Technical view and back. Verify: no invented names or numbers, every "
        "[n] opens a real edge, text states uncertainty and gaps, and ends with the 'research lead, not medical advice' line (plans).", lines)

# ---------------------------------------------------------------- 6 Funding
lines = []
for d in pool[:44] + gaps[:3] + pool[70:73]:
    f = api.funding(d)
    lines.append(f"{lab[d]:<44} => {f['total']} NIH grants ({f['total_active']} active)" + ("; FUNDING GAP note shown (NIH only)" if f["gap"] else ""))
section(6, "FUNDING (Explore > Funding; Action plan funding section)",
        "Open the Funding tab: grant title, PI, organisation, fiscal years, amount, link to RePORTER, active flag, match reason. Gap diseases must "
        "say 'NIH only; other funders not covered'.", lines[:50])

# ---------------------------------------------------------------- 7 Variants
genes = n[n.type == "Gene"]
gl = []
for gid, gl_ in zip(genes.id, genes.label):
    try:
        v = api.variants(gid, k=3)
        gl.append((gid, gl_, v["counts"]))
    except Exception:
        pass
gl.sort(key=lambda x: -x[2].get("total", 0))
lines = [f"{l:<10} => pathogenic {c['pathogenic']}, uncertain {c['vus']}, benign {c['benign']}, conflicting {c['conflicting']}; {c['in_graph']} variants in graph"
         for _, l, c in gl[:46]]
lines += [f"{lab[d][:40]:<40} => disease-level variants view (union of its genes); counts shown, no crash" for d in pool[:4]]
section(7, "VARIANTS (Explore > Variants; Action plan variants expander)",
        "Open the Variants tab for a gene's disease. Check counts, top pathogenic variants (HGVS, stars, review status, submitters, ClinVar link). "
        "Counts cover all ClinVar records for the gene; the table lists only >=1-star pathogenic variants kept in the graph.", lines[:50])

# ---------------------------------------------------------------- 8 Collaborators
lines = []
for d in pool[:50]:
    r = api.researchers_for(d, k=1)
    top = f"{r[0]['label']} (score {r[0]['score']:.2f}; {r[0]['papers']} papers, {r[0]['grants']} grants)" if r else "none -> explained empty state"
    lines.append(f"{lab[d]:<44} => top: {top}")
section(8, "COLLABORATORS (Explore > Collaborators; Action plan 'People who work on both')",
        "Open the tab: ranked researchers with 'why', affiliation, ORCID link when known, homonym warning when identity is name-based.", lines)

# ---------------------------------------------------------------- 9 Assets
lines = []
for d in pool[:50]:
    a = api.assets_for(d)["counts"]
    lines.append(f"{lab[d]:<44} => " + ", ".join(f"{k} {v}" for k, v in a.items() if isinstance(v, (int, float))))
section(9, "RESEARCH ASSETS (Explore > Assets; Action plan 'What already exists')",
        "Open Assets: trials, registries, natural-history and observational studies, models. Stopped trials show 'why stopped' and must not be "
        "presented as open opportunities. Each card links to ClinicalTrials.gov and has an Evidence button.", lines)

# ---------------------------------------------------------------- 10 Edge inspector (handwritten)
E = [
    "Disease card > gene chip 'Evidence' => curated (solid), source Orphanet/HPO, date, confidence bar",
    "Disease card > symptom chip 'Evidence' => HPO annotation, IC (information content) shown, 'distinctive' vs 'common'",
    "Disease card > mechanism chip 'Evidence' => Reactome pathway with the gene it was reached through",
    "Patient group 'Evidence' => 'serves' edge, source = curated list or ClinicalTrials.gov sponsor",
    "Patient group link => opens the verified organisation website in a new tab",
    "Similar disease 'Why linked?' => inferred (dotted) edge, breakdown of symptom/pathway/literature components",
    "Inspector > 'How the confidence is made up' => reliability, recency, corroboration bonus, penalty, formula",
    "Inspector > evidence tally => 'N supporting - N contradicting - N caveats' and stance chip",
    "Inspector stance chip for a curated gene-disease edge => 'Supported'",
    "Inspector stance chip for most computed similar_to edges => 'Weak' or 'Mixed' (confidence < 0.5)",
    "Inspector > supporting papers => PMIDs link to PubMed with title and year",
    "Inspector > 'Technical explanation' expander => source id, relationship, raw detail",
    "Inspector close (x) => panel returns to the 'Press Evidence' prompt, page position kept",
    "Edge for a terminated trial (Assets > Evidence) => 'Possible counter-evidence (automated flag, not a verified finding)'",
    "Trial stopped for an efficacy/safety reason => higher counter-weight than an operational reason",
    "Trial stopped for lack of funding => labelled operational, stance stays 'Supported' with 1 caveat",
    "Gene-disease edge where Orphanet and HPO disagree => 'conflicting status' contradiction listed",
    "Gene-disease edge 'Not yet assessed' in Orphanet => caveat shown",
    "Paper edge for a retracted paper => contradiction weight 1.0",
    "Researcher 'authored' edge without ORCID => caveat 'identity by name + initial + country; homonyms possible'",
    "Grant 'funds' edge => matched_by text (e.g. disease name in title / abstract / concept terms) and fiscal years",
    "Variant edge => ClinVar VariationID link, review status, number of submitters",
    "Plan citation chip [1] => opens the same inspector as the evidence button",
    "Plan citation chips: all [n] in the narrative resolve (none show 'unknown edge')",
    "Inspector 'plain sentence' callout => one to three plain sentences, mentions contradicting papers when present",
    "Inspector for an inferred edge with 0 co-mention papers => note 'rests on ontology annotations only'",
    "Inspector for below-threshold cross-family link => note that it is below the main threshold",
    "Inspector for HPO 'explicitly absent' conflict => note names the disease where the symptom is marked absent",
    "Inspector for 'single text-mining hit' paper link => caveat 'not confirmed by MeSH indexing'",
    "Evidence legend (always visible) => solid = database record, dashed = found in papers, dotted = inferred",
    "Legend line styles match the map edge styles exactly",
    "Evidence-type percentages on Home add up to ~100% (curated/extracted/inferred)",
    "Open inspector, switch page, come back => no stale or wrong edge shown",
    "Open two different edges one after another => panel content replaces, no duplication",
    "Inspector on phone width (390 px) => stacks below content, still readable",
    "Inspector in dark theme => text/bars readable (contrast), stance chip colours distinct",
    "Inspector in light theme => same checks",
    "Edge id with unknown value via URL/session => friendly error, no stack trace",
    "Inspector source link for ClinVar => opens ClinVar variation page",
    "Inspector source link for Orphanet => opens Orphanet disease page",
    "Inspector source link for HPO => opens HPO term page",
    "Inspector source link for Reactome => opens Reactome pathway page",
    "Inspector source link for NIH RePORTER => opens project details",
    "Inspector source link for ClinicalTrials.gov => opens the NCT page",
    "Confidence bar width matches the percentage printed",
    "Raw confidence vs recomputed overall => inferred/extracted show recomputed; curated keep the source value",
    "Contradicting item weight < 0.45 => counted as caveat, not contradiction",
    "Contradicting item weight >= 0.45 => counted in 'contradicting' and moves stance toward 'Mixed'",
    "Edge with no supporting papers => 'No paper attached; this link rests on the database record'",
    "Every edge reachable from the map via the 'inspect any link' dropdown",
]
section(10, "EVIDENCE INSPECTOR, CONFIDENCE AND CONTRADICTIONS", "Open the inspector from the item named and verify the stated behaviour.", E)

# ---------------------------------------------------------------- 11 Ask (handwritten)
Q = [
    # answerable, precise
    ("What treatments have been tried for Krabbe disease?", "cited answer: HSCT, gene therapy trials"),
    ("Is hematopoietic stem cell transplantation used for X-linked adrenoleukodystrophy?", "yes, cited"),
    ("Which biomarkers track Fabry disease progression?", "lyso-Gb3 and others, cited"),
    ("Does miglustat help Niemann-Pick type C?", "stabilises neurological disease, cited"),
    ("What is enzyme replacement therapy for Gaucher disease?", "cited"),
    ("Is enzyme replacement therapy effective in Pompe disease?", "cited"),
    ("Does stem cell transplantation help children with Krabbe disease?", "cited, mentions early transplant"),
    ("What is newborn screening for Krabbe disease?", "cited"),
    ("Is newborn screening useful for X-ALD?", "cited"),
    ("Which gene therapy approaches exist for metachromatic leukodystrophy?", "cited, lentiviral HSC"),
    ("What causes Tay-Sachs disease?", "HEXA deficiency, cited"),
    ("What is the inheritance pattern of Fabry disease?", "X-linked, cited"),
    ("How is Hunter syndrome treated?", "idursulfase ERT, cited"),
    ("What is the blood-brain barrier problem for treating lysosomal diseases?", "cited"),
    ("What is substrate reduction therapy?", "cited"),
    ("Can chaperone therapy treat Fabry disease?", "migalastat, cited"),
    ("What are the symptoms of Zellweger spectrum disorders?", "cited"),
    ("How is Refsum disease managed?", "dietary phytanic acid restriction, cited"),
    ("What is cerebrotendinous xanthomatosis and how is it treated?", "chenodeoxycholic acid, cited"),
    ("What is the role of VLCFA in adrenoleukodystrophy?", "very long chain fatty acids accumulate, cited"),
    ("What is Lorenzo's oil?", "cited, mixed evidence"),
    ("Which lysosomal diseases are treated with hematopoietic stem cell transplantation?", "MPS I, Krabbe, MLD, cited"),
    ("What is the prevalence of Gaucher disease?", "cited or refused if not stated"),
    ("What are the neurological manifestations of Gaucher disease?", "type 2/3, cited"),
    ("What clinical trials exist for cystinosis treatment?", "cysteamine, cited"),
    # lay wording
    ("What is the role of the PSAP gene in Krabbe-like disease?", "saposin A deficiency, cited"),
    ("What do Krabbe disease and metachromatic leukodystrophy have in common?", "both leukodystrophies, cited"),
    ("my son has Krabbe, what can we do", "cited, plain, or honest refusal"),
    ("is there a cure for Batten disease", "cited; no overclaiming"),
    ("what is a lysosome and why does it matter in these diseases", "cited or refused; no invented facts"),
    ("can adults get Krabbe disease", "adult-onset form, cited"),
    ("do kids with Hunter syndrome go to school", "cautious answer; check for overclaiming from one case"),
    # disease-filter scoped (set the 'Only use papers about' filter first)
    ("[filter: Krabbe disease] What treatments have been tried?", "answers from Krabbe papers only"),
    ("[filter: Fabry disease] What are the main symptoms?", "answers from Fabry papers only"),
    ("[filter: Gaucher disease] Which therapies are approved?", "answers from Gaucher papers only"),
    ("[filter: metachromatic leukodystrophy] Is gene therapy available?", "cited"),
    ("[filter: Pompe/glycogen storage disease II] When should ERT start?", "cited"),
    # unanswerable / out-of-corpus rare-disease questions (should refuse, not guess)
    ("What is the cost of enzyme replacement therapy in Nigeria?", "REFUSE: not supported by the abstracts"),
    ("Which hospital in Buffalo has an appointment slot for Fabry disease tomorrow?", "REFUSE"),
    ("What will the FDA decide about the next Krabbe gene therapy next year?", "REFUSE: future/unknown"),
    ("What is the exact survival rate of Tay-Sachs patients in 2040?", "REFUSE"),
    ("Does homeopathy cure Gaucher disease?", "REFUSE or cautious 'no evidence in sources'"),
    ("Who is the best doctor for Pompe disease in Texas?", "REFUSE: no endorsements"),
    # off-topic (refused by the topic gate before any model call; should be fast, < 5 s)
    ("What is the best pizza in New York?", "REFUSE: not related to the indexed rare-disease literature"),
    ("Write me a poem about the ocean", "REFUSE (off-topic)"),
    ("Who won the football world cup in 2018?", "REFUSE (off-topic)"),
    ("How do I reset my router?", "REFUSE (off-topic)"),
    ("What is 25 times 17?", "REFUSE (off-topic)"),
    ("Tell me a joke", "REFUSE (off-topic)"),
    ("What is the capital of France?", "REFUSE (off-topic)"),
]
assert len(Q) == 50, len(Q)
section(11, "ASK THE LITERATURE (natural-language questions over 26,026 PubMed abstracts)",
        "Ask page, type the question (Ctrl+Enter or Ask). Expected = intended behaviour; log any deviation. Check: streamed draft is replaced by the "
        "verified answer, every sentence has a [n], the Sources expanders open abstracts with PubMed links, refusals are worded (not errors).",
        [f"{q}  =>  {e}" for q, e in Q])

# ---------------------------------------------------------------- 12 UI / UX / theme (handwritten)
U = [
    "Home loads in < 6 s on first visit; stat cards show 274 diseases, 439 genes, 2,835 symptoms, 1,180 mechanisms, 26,026 papers, ~1,224 studies",
    "Home hero + search box visible without scrolling at 1280x800",
    "Home 'Try' pills (Krabbe disease, globoid cell leukodystrophy, ABCD1, ...) each run the search",
    "Enter in the Home search box submits",
    "Home 'Guided demo: Krabbe disease to X-ALD' starts the tour and opens Explore on Krabbe",
    "Tour banner step 1 'Next: the plan' opens the plan with Krabbe and adrenoleukodystrophy preselected",
    "Persona card 'I lead a patient group' => Action plan page",
    "Persona card 'We were just diagnosed' => Explore with patient groups first",
    "Persona card 'I'm a researcher or scout' => Explore, mechanism-first entry",
    "Top navigation: Home, Explore, Action plan, Ask the literature, How it works all open without error",
    "Breadcrumb 'Atlas > page > disease' updates when the disease changes",
    "'Current disease' chip persists across pages after choosing a disease",
    "Theme toggle Auto/Light/Dark: Light turns the whole app light (widgets included)",
    "Theme toggle Dark turns the whole app dark (selects, inputs, buttons, expanders)",
    "Theme Auto follows the operating-system colour scheme",
    "Theme choice survives navigation between pages in one session",
    "Theme choice resets to Auto after a full browser reload (documented limit)",
    "Light theme: body text, chips, and badges are readable (no pale-on-pale)",
    "Dark theme: map canvas background follows the theme (not white)",
    "Dark theme: map labels and edges remain visible",
    "Node types are distinguishable by SHAPE as well as colour (disease circle, gene diamond, symptom triangle, ...)",
    "Evidence types distinguishable by line style (solid/dashed/dotted), not only colour",
    "Explore selectors (search box, 'Look for', picker) are in the main panel above results, not the sidebar",
    "Explore 'Not what you meant?' picker changes the disease and refreshes the card",
    "Explore 'Look for' filter: Genes only returns genes; Symptoms only returns symptoms",
    "Explore starting from a gene (GALC) lists the diseases linked to it",
    "Explore starting from a symptom lists diseases with that symptom",
    "Explore starting from a mechanism lists diseases through its genes",
    "View switch: Similar diseases / Map / Funding / Variants / Collaborators / Assets all render",
    "'Common symptoms (N)' expander is collapsed by default and opens",
    "'Show technical detail' expander shows IDs and cross-references",
    "Glossary hover (underlined dotted term) shows a plain definition and does not break the layout",
    "Spinner or skeleton text appears for slow calls (search first call ~2.5 s, plan, explain, ask)",
    "Empty state: search with no results shows a helpful message, not a blank page",
    "Empty state: disease with no genes/papers shows a gap explanation and the closest related patient groups",
    "Error state: stop the LLM server (./stop then start with --no-llm): Ask shows 'language model not available'; plain explanation falls back to template",
    "Ask page: Ctrl+Enter submits; Enter adds a newline in the multi-line box",
    "Ask page: example question buttons fill and run the question",
    "Ask page: 'Only use papers about' filter lists diseases and can be reset to All diseases",
    "Ask page: sources expander shows title, year, journal, snippet and a PubMed link",
    "Action plan: 'Copy proposal' puts the plain-text proposal on the clipboard; 'Download' saves a .txt",
    "Action plan page layout at 1280 px: no horizontal scroll, inspector beside the narrative",
    "Phone width 390 px: pages usable; navigation reachable via the sidebar chevron",
    "'How it works' page: pipeline steps, how to read the screens, sources with retrieved dates, limits",
    "Keyboard: Tab order reaches search box, buttons, expanders; focus ring visible",
    "Browser console on every page: no red errors (a few asset 404s from the graph component are known and harmless)",
    "No 'Demo data' badge appears with real data (it must appear only in ATLAS_FAKE=1 mode)",
    "ATLAS_FAKE=1 .venv/bin/streamlit run app/streamlit_app.py => every page shows the 'Demo data' badge",
    "./start launches UI on :8501 and model server on :8080; ./stop frees both ports",
    "Closing the terminal running ./start stops both services (no orphans: ./stop --status)",
]
section(12, "UI, THEME, NAVIGATION AND UX", "Open the app (./start, http://localhost:8501) and check each item.", U)

header = f"""RARE DISEASE ATLAS - TEST QUERIES FOR THE RUNNING SERVER
=======================================================
Generated {datetime.date.today()} from the live graph (60,869 nodes, 207,211 edges). 12 features x 50 checks = 600 lines.

Setup:   ./start   then open http://localhost:8501   (or: ATLAS_FAKE=1 .venv/bin/streamlit run app/streamlit_app.py for demo data only)
Format:  N. <input>  =>  <expected result>
Notes:   - Sections 1-9 expectations are the atlas's own output at build time (counts and scores can change after a graph rebuild; re-run
           the generator or treat them as 'about'). Sections 10-12 are behavioural checks.
         - Section 11 (Ask) expectations are the INTENDED behaviour; the local 3B model can deviate. Record deviations, especially any answer that
           states something the cited abstracts do not say.
         - Timings are for a 6-core CPU: card + similar < 1 s, plan ~1 s, plain explanation 10-30 s first time then instant, Ask answer 10-50 s,
           off-topic Ask refusal < 5 s.
         - Automated coverage of the same features: scripts/45_check_atlas.py, scripts/smoke_test.py, scripts/ui_test.py, scripts/eval_ask.py.
"""
open(str(ROOT / "tests/queries.txt"), "w").write(header + "\n".join(out) + "\n")
print("written", sum(1 for l in out if l[:2].strip().rstrip('.').isdigit()))
