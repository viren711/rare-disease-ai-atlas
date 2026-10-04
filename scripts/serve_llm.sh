#!/usr/bin/env bash
# Start the local model server: llama.cpp's OpenAI-compatible llama-server in ROUTER mode,
# holding at most one model at a time (adapted from healthathon).
#
#   bash scripts/serve_llm.sh          # foreground, port 8080
#   PORT=8081 bash scripts/serve_llm.sh
#
# Models are declared in config/models.ini; a request names one ("model": "qwen2.5-3b")
# and the router loads it on demand. rag/llm.py speaks /v1/chat/completions to it.
# Which model the atlas asks for is llm.model in config/atlas.yaml (or LLM_MODEL).
set -euo pipefail
cd "$(dirname "$0")/.."

MODELS_DIR="${MODELS_DIR:-models}"
PRESET="${PRESET:-config/models.ini}"
PORT="${PORT:-8080}"
HOST="${HOST:-127.0.0.1}"
MODELS_MAX="${MODELS_MAX:-1}"

BIN="$MODELS_DIR/llama.cpp/llama-server"
if [[ ! -x "$BIN" ]]; then
    echo "llama-server not found at $BIN -- models/ should link to the healthathon models dir" >&2
    exit 1
fi
if [[ ! -f "$PRESET" ]]; then
    echo "model presets not found at $PRESET" >&2
    exit 1
fi

echo "model presets ($PRESET):"
current=""
while IFS= read -r line; do
    if [[ "$line" =~ ^\[([^]]+)\] ]]; then current="${BASH_REMATCH[1]}"; fi
    if [[ -n "$current" && "$line" =~ ^model[[:space:]]*=[[:space:]]*(.+)$ ]]; then
        path="${BASH_REMATCH[1]}"
        if [[ -f "$path" ]]; then echo "  ok       $current"; else echo "  MISSING  $current  ($path)"; fi
    fi
done < "$PRESET"

# The router also lists any GGUF in the Hugging Face cache; point its cache lookups at an
# empty directory so only the presets above are served.
EMPTY_CACHE="$MODELS_DIR/.router-cache"
mkdir -p "$EMPTY_CACHE"

echo "router on http://$HOST:$PORT/v1  (max $MODELS_MAX model resident)"
exec env HF_HUB_CACHE="$EMPTY_CACHE" LLAMA_CACHE="$EMPTY_CACHE" \
    "$BIN" \
    --models-preset "$PRESET" \
    --models-max "$MODELS_MAX" \
    --host "$HOST" \
    --port "$PORT"
