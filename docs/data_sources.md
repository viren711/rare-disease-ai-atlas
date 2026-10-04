# Data sources and run order

Rebuild from scratch (CPU only, about 3 minutes plus network):

```
python scripts/10_download_sources.py   # MONDO, HPO, Orphanet product 6, Reactome
python scripts/download_pubmed.py ...   # PubMed abstracts (both MeSH groups)
python scripts/11_pubtator.py           # PubTator3 annotations
python scripts/12_ctgov.py              # ClinicalTrials.gov v2 (now keeps whyStopped, hasResults)
python scripts/13_reporter.py           # NIH RePORTER grants (resumable, cached per query in data/raw/reporter/q/)
python scripts/14_clinvar.py            # ClinVar variant_summary.txt.gz (8 parallel range requests), filtered to atlas genes
python scripts/15_orphanet_epi.py       # Orphanet prevalence + age of onset / inheritance
python scripts/20_ontology.py           # diseases, xrefs, papers (+ pub types, negative-result flag), synonyms   (~10 s)
python scripts/30_build_graph.py        # nodes + edges, atomic parquet writes                                    (~20 s)
python scripts/40_cluster.py            # similar_to edges + Louvain clusters                                      (~2 s)
python scripts/45_check_atlas.py        # smoke checks incl. round-2 checks
```

Parquet files are written to `*.tmp` and renamed, so a running app never reads a half-written file. Edge ids (`e#`) are
re-assigned on every rebuild, so cached text that cites `[e#]` must be regenerated after a rebuild.

| Source | URL | Terms | Retrieved | Records | Feeds |
|---|---|---|---|---|---|
| MONDO | https://purl.obolibrary.org/obo/mondo.json | CC-BY-4.0 | 2026-10-04 | 274 atlas diseases | Disease nodes, `subclass_of`, xref map |
| HPO + annotations | https://purl.obolibrary.org/obo/hp.json, `hpoa/phenotype.hpoa`, `genes_to_*.txt` | HPO licence (free, attribution) | 2026-10-04 | 6,794 disease-phenotype edges | `has_phenotype`, `causes`, HPO NOT annotations (counter-evidence) |
| Orphanet product 6 | https://www.orphadata.com/data/xml/en_product6.xml | CC-BY-4.0 | 2026-10-04 | 204 gene-disease edges | `causes`, `associated_with` |
| Orphanet product 9 (prevalence, ages) | https://www.orphadata.com/data/xml/en_product9_prev.xml, `en_product9_ages.xml` | CC-BY-4.0 | 2026-10-04 (Orphadata 2026-06-23) | 171 atlas diseases with prevalence, onset or inheritance | Disease attrs `epidemiology`; `disease_card()["epidemiology"]`, `action_plan()["epidemiology"]` |
| Reactome | https://reactome.org/download/current/ | CC0 | 2026-10-04 | 2,789 gene-pathway edges | `in_pathway` |
| PubMed (MeSH groups) | NCBI E-utilities | NLM public data; abstracts remain publisher copyright | 2026-10-04 | 26,026 papers | Paper nodes, `mentions`, `authored`, Researcher nodes; pub types + negative-result flag feed contradiction handling |
| PubTator3 | https://www.ncbi.nlm.nih.gov/research/pubtator3/ | NCBI public data | 2026-10-04 | 26,026 annotated papers | paper-gene / paper-disease `mentions` |
| ClinicalTrials.gov v2 | https://clinicaltrials.gov/api/v2/studies | public domain | 2026-10-04 | 1,303 studies, 1,224 linked, 161 with whyStopped | Trial nodes, `studies`, `cites`, `investigates`, `runs`; whyStopped drives counter-evidence |
| NIH RePORTER v2 | https://api.reporter.nih.gov/v2/projects/search | US government public domain | 2026-10-04 | 139 phrase queries, 5,875 project-years, 1,779 projects, 892 linked | Grant nodes, `funds` (Grant to Disease, extracted, 0.6-0.9), `leads` (PI to Grant, curated); 416 new PI Researcher nodes, 437 PIs linked to existing researchers |
| ClinVar variant_summary | https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz | NCBI public domain | 2026-10-04 (file of 2026-09-29) | 9.22 M rows scanned, 85,989 GRCh38 rows for 94 genes, 16,137 pathogenic / likely pathogenic | Variant nodes (3,890: P/LP, 1 star or more, top 50 per gene), `variant_of`, `pathogenic_for`; gene attrs `n_pathogenic`, `n_vus`, `n_benign`, `n_conflicting` |
| Patient organisations | `config/patient_orgs.csv` (HTTP-verified by hand) + CT.gov sponsors | n/a | 2026-10-04 | 31 verified | PatientOrg nodes, `serves`, `runs` |

## Matching notes (RePORTER)
A grant is linked to a disease when the disease name or exact synonym (6 or more characters) occurs in the project
title (confidence 0.9), abstract (0.7) or NIH concept terms (0.6). If no disease name matches, a causal gene symbol in the
project title links the grant to that gene's most general diseases (0.6). All `funds` edges are `extracted` and say how they
matched. PIs are linked to an existing researcher only on name plus affiliation overlap (0.8) or a single unambiguous
candidate (0.6), otherwise a new `AUTH:nih|<profile id>` node is created. "Funding gap" means no ACTIVE NIH grant: other
funders are not covered.

## Not included
Third patient-organisation source (Orphadata expert centres / EURORDIS): not added, no verified patient-group URL list
was available from these sources in the time box (expert centres are clinical centres, not patient organisations).
