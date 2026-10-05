#!/usr/bin/env bash
# Bring the whole deployment up in a tmux session named global_ai (idempotent: does nothing if it already exists).
#   window app    : ./start in a restart loop (model server :8080 + UI :8501)
#   window watch  : scripts/keepalive.sh, restarts everything if the UI or model hangs
#   window tunnel : Cloudflare quick tunnel to :8501 (public URL is printed in logs/cloudflared.log and changes
#                   every time the tunnel restarts; a fixed URL needs a named tunnel on your own domain)
# Used by the systemd unit deploy/global-ai.service so it all comes back after a reboot.
set -u
cd "$(dirname "$0")/.."
DIR=$PWD
CLOUDFLARED=${CLOUDFLARED:-$HOME/.local/bin/cloudflared}
mkdir -p logs
if tmux has-session -t global_ai 2>/dev/null; then
    echo "tmux session global_ai already running"
    exit 0
fi
tmux new-session -d -s global_ai -n app -c "$DIR" \
    'while true; do ./start; echo "[$(date)] ./start exited; restarting in 5 s"; sleep 5; done'
tmux new-window -t global_ai -n watch -c "$DIR" 'bash scripts/keepalive.sh'
tmux new-window -t global_ai -n tunnel -c "$DIR" \
    "while true; do $CLOUDFLARED tunnel --url http://localhost:8501 --protocol http2 --no-autoupdate 2>&1 | tee -a logs/cloudflared.log; sleep 5; done"
echo "started tmux session global_ai (windows: app, watch, tunnel)"
