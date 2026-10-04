#!/usr/bin/env bash
# Rebuild everything from public sources: downloads (parallel lanes) -> graph -> search index.
# Every step skips or resumes work already on disk, so rerunning after a failure is safe.
#
#   bash scripts/run_all.sh              # full rebuild (~10 min network + ~35 s graph + ~30 min index on CPU)
#   SKIP_INDEX=1 bash scripts/run_all.sh # graph only
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python; [[ -x .venv/bin/python ]] && PY=.venv/bin/python
mkdir -p logs

lane() { local name=$1; shift; echo "[start] $name"; "$@" > "logs/$name.log" 2>&1 && echo "[ok]    $name" || { echo "[FAIL]  $name -- see logs/$name.log"; return 1; }; }

# Lane A: models (independent of data). Lane B: bulk biology files. Lane C: PubMed then PubTator.
# Lanes D-F: trials, grants, variants, epidemiology (independent APIs).
lane fetch_models bash scripts/00_fetch_models.sh & A=$!
lane sources      $PY scripts/10_download_sources.py & B=$!
( lane pubmed_lysosomal  $PY scripts/download_pubmed.py \
  && lane pubmed_peroxisomal $PY scripts/download_pubmed.py --slice peroxisomal \
         --term '"Peroxisomal Disorders"[MeSH] AND hasabstract' \
  && lane pubtator $PY scripts/11_pubtator.py ) & C=$!
lane ctgov        $PY scripts/12_ctgov.py & D=$!
lane reporter     $PY scripts/13_reporter.py & E=$!
( lane clinvar $PY scripts/14_clinvar.py && lane orphanet_epi $PY scripts/15_orphanet_epi.py ) & F=$!

status=0
for p in $B $C $D $E $F; do wait "$p" || status=1; done
[[ $status -eq 0 ]] || { echo "a download lane failed; rerun to resume"; exit 1; }

lane ontology    $PY scripts/20_ontology.py
lane build_graph $PY scripts/30_build_graph.py
lane cluster     $PY scripts/40_cluster.py
rm -rf data/cache/plain           # cached explanations cite edge ids, which a rebuild reassigns
lane check_atlas $PY scripts/45_check_atlas.py

[[ "${SKIP_INDEX:-0}" == "1" ]] || lane build_index $PY scripts/50_build_index.py
wait "$A" || echo "model fetch failed; see logs/fetch_models.log"
echo "done. Start with ./start"
