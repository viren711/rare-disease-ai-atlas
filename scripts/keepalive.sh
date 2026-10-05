#!/usr/bin/env bash
# Keep the atlas running unattended. Run inside the tmux session (see README "Run it permanently"):
#
#   tmux new-session -d -s global_ai -n app   'while true; do ./start; sleep 5; done'
#   tmux new-window  -t global_ai    -n watch 'bash scripts/keepalive.sh'
#
# ./start restarts if it exits, but it does not notice a crashed UI or model server. This watchdog checks both
# health endpoints every 30 s; after two failures in a row it runs ./stop, which ends ./start so the loop
# above starts it again.
cd "$(dirname "$0")/.."
UI_PORT=${UI_PORT:-8501}
LLM_PORT=${LLM_PORT:-8080}
INTERVAL=${INTERVAL:-30}
GRACE=${GRACE:-120}     # seconds to leave a fresh start alone (the models take a while to load)
fails=0
sleep "$GRACE"
while true; do
    ui=$(curl -s -m 8 "localhost:$UI_PORT/_stcore/health")
    llm=$(curl -s -m 8 "localhost:$LLM_PORT/health")
    if [[ "$ui" == "ok" && "$llm" == *ok* ]]; then
        fails=0
    else
        fails=$((fails + 1))
        echo "[$(date '+%F %T')] unhealthy (ui='${ui:-down}' llm='${llm:-down}') strike $fails/2"
        if (( fails >= 2 )); then
            echo "[$(date '+%F %T')] restarting via ./stop"
            ./stop >/dev/null 2>&1
            fails=0
            sleep "$GRACE"
        fi
    fi
    sleep "$INTERVAL"
done
