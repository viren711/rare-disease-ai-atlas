"""Public API of the atlas: the ONLY module the Streamlit app imports from atlas/ and rag/.

Contract shared by the graph lane (implements) and the UI lane (consumes). Keep signatures and
return shapes stable; add keys freely, never rename or remove them.

Data on disk (written by scripts/20-40, read by atlas/graph.py):
  data/graph/nodes.parquet    id, type, label, group, attrs (JSON str)
      type ∈ Disease | Gene | Phenotype | Pathway | Paper | Researcher | Trial | PatientOrg
      ids: MONDO:xxxx, NCBIGene:xxx, HP:xxxxxxx, R-HSA-xxx, PMID:xxx, AUTH:<orcid or name-key>, NCT..., ORG:<slug>
  data/graph/edges.parquet    id (e0, e1, ...), src, dst, rel, source, source_id, date, confidence (0-1),
                              evidence_type ∈ curated | extracted | inferred, detail (JSON str)
      rel examples: causes (Gene->Disease), has_phenotype, in_pathway (Gene->Pathway), mentions (Paper->X),
                    authored (Researcher->Paper), studies (Trial->Disease), runs (PatientOrg->Trial|Disease),
                    similar_to (Disease~Disease, inferred), subclass_of (Disease->Disease)
  data/graph/clusters.parquet disease_id, cluster_id, cluster_label
  data/graph/synonyms.parquet text, node_id, type, is_primary

Every function returns plain dicts/lists (JSON-serialisable) so the UI can st.json() them while debugging.
"""
from __future__ import annotations

EVIDENCE_TYPES = ("curated", "extracted", "inferred")


def search(q: str, types: list[str] | None = None, k: int = 10) -> list[dict]:
    """One global search box. Resolves synonyms across disease/gene/phenotype/pathway names.

    Returns ranked: [{"id", "type", "label", "matched_text", "score", "group"}]. Empty list if nothing matches.
    """
    from atlas import resolve
    return resolve.search(q, types=types, k=k)


def disease_card(disease_id: str) -> dict:
    """Summary-first view of one disease.

    {"id", "label", "group", "synonyms": [str], "definition": str,
     "cluster": {"id", "label", "members": [{"id","label"}]},
     "genes": [{"id","label","edge_id"}],
     "phenotypes": [{"id","label","ic","informative": bool,"edge_id"}]   # sorted by IC desc
     "pathways": [{"id","label","via_gene","edge_id"}],
     "counts": {"papers","trials","registries","researchers","orgs"},
     "orgs": [{"id","label","url","edge_id"}]}
    Raises KeyError for unknown id.
    """
    from atlas import graph
    return graph.disease_card(disease_id)


def neighbours(disease_id: str, k: int = 10) -> list[dict]:
    """Mechanism/phenotype neighbours, NOT name-based.

    [{"id","label","group","score","cross_group": bool,
      "why": {"shared_phenotypes":[{"id","label","ic"}], "shared_pathways":[{"id","label"}],
              "shared_genes":[{"id","label"}], "co_mention_papers": int,
              "shared_researchers": int, "shared_trials": int},
      "edge_id"}]   # sorted by score desc
    """
    from atlas import graph
    return graph.neighbours(disease_id, k=k)


def subgraph(node_id: str, depth: int = 1, max_nodes: int = 60, types: list[str] | None = None) -> dict:
    """Ego graph for visualisation. {"nodes":[{"id","type","label","group"}],
    "edges":[{"id","src","dst","rel","evidence_type","confidence"}]}"""
    from atlas import graph
    return graph.subgraph(node_id, depth=depth, max_nodes=max_nodes, types=types)


def edge(edge_id: str) -> dict:
    """Edge inspector. {"id","src","dst","src_label","dst_label","rel","source","source_id","source_url",
    "date","confidence","evidence_type","detail": dict, "supporting_papers":[{"pmid","title","year"}],
    "contradicting": [{"pmid","title","note"}], "explanation": str}"""
    from atlas import graph
    return graph.edge(edge_id)


def action_plan(disease_a: str, disease_b: str | None = None) -> dict:
    """Patient action view for disease A, optionally against a chosen neighbour B (default: best neighbour).

    {"a": {"id","label"}, "b": {"id","label"} | None, "supported": bool,
     "path": [{"edge_id","src_label","rel","dst_label","evidence_type","source"}],
     "shared_assets": [{"id","label","kind": "trial|registry|natural_history","status","edge_ids":[...]}],
     "researchers": [{"id","label","affiliation","papers_a","papers_b","orcid"}],
     "orgs": [{"id","label","url","disease_id"}],
     "differences": [str],              # what differs between A and B (phenotypes/genes unique to each)
     "next_steps": [{"text","edge_ids":[...]}],
     "narrative": str,                  # plain-language, every sentence cites [e#]
     "gap": None | {"searched": [str], "missing": [str], "next_question": str}}
    supported=False => gap is filled and the UI must show it prominently.
    """
    from atlas import actions
    return actions.action_plan(disease_a, disease_b)


def ask(question: str, disease_id: str | None = None) -> dict:
    """Natural-language question over the PubMed abstracts (reused healthathon RAG pipeline).

    {"answer": str, "refused": bool, "reason": str | None,
     "sources": [{"n", "pmid", "title", "year", "journal", "snippet", "score"}]}
    """
    from rag import pipeline
    return pipeline.ask(question, disease_id=disease_id)


def ask_stream(question: str, disease_id: str | None = None):
    """Streaming variant: yields {"type":"token","text"} ... then one {"type":"final", **ask() result}."""
    from rag import pipeline
    yield from pipeline.ask_stream(question, disease_id=disease_id)


def stats() -> dict:
    """Corpus and graph coverage for the home page and gap reports.
    {"nodes_by_type": {type: n}, "edges_by_evidence": {etype: n}, "groups": {group: {"diseases","papers","trials"}},
     "sources": [{"name","retrieved","records"}], "index_ready": bool, "llm_ready": bool}"""
    from atlas import graph
    return graph.stats()


# ----------------------------------------------------------------------------------------------- round 2 (appended)
def funding(disease_id: str) -> dict:
    """NIH RePORTER grants naming the disease or a subtype (FY2005+).

    {"grants":[{"id","title","pi","org","location","years","fiscal_years","amount","url","edge_id","active","project_num",
                "institute","confidence","matched_by","end_date","disease_id"}],
     "total_active": int, "total": int, "gap": bool, "gap_note": str | None,
     "broader_class_grants": [... same row + "context"], "active_amount_last_fy": float, "source": str, "disease": {...}}
    gap=True means no ACTIVE NIH grant names the disease (other funders are not covered). Raises KeyError."""
    from atlas import graph
    return graph.funding(disease_id)


def variants(gene_or_disease_id: str, k: int = 20) -> dict:
    """ClinVar variants for a Gene or Disease id.

    {"id","scope":"gene|disease","label","counts":{"pathogenic","vus","benign","conflicting","total","in_graph"},
     "genes":[{"id","label","n_pathogenic","n_vus","n_benign","n_conflicting","n_variants_total"}],
     "top":[{"id","label","hgvs","gene","gene_id","significance","stars","review_status","n_submitters","type","rsid",
             "conditions","last_evaluated","diseases":[{"id","label","edge_id"}],"url","edge_id"}],
     "source": str, "has_data": bool}
    counts cover all ClinVar GRCh38 records of the gene(s); `top` is limited to >=1-star pathogenic / likely pathogenic
    variants kept in the graph (50 per gene)."""
    from atlas import graph
    return graph.variants(gene_or_disease_id, k=k)


def researchers_for(disease_id: str, k: int = 10) -> list[dict]:
    """Ranked collaborators for a disease (papers + NIH grants led + trials investigated).

    [{"id","label","score","papers","grants","active_grants","trials","affiliation","country","orcid","why":[str],
      "edge_ids":[...],"identity","homonym_risk","contact":{"affiliation","orcid_url","note"},"grant_ids","trial_ids"}]"""
    from atlas import graph
    return graph.researchers_for(disease_id, k=k)


def assets_for(disease_id: str) -> dict:
    """Research assets grouped: {"disease", "trials":[row], "registries":[row], "natural_history":[row],
    "observational":[row], "models":[{"id","label","kind":"grant|paper","organism_or_system","year","edge_id","url"}],
    "counts":{...}}; trial row = {"id","label","kind","status","start","phases","sponsor","url","edge_id","enrollment",
    "countries","interventions","why_stopped","stopped","broad"}."""
    from atlas import graph
    return graph.assets_for(disease_id)
