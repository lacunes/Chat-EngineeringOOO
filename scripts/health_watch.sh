#!/usr/bin/env bash
set -u

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${BASE_DIR}/logs/health_watch.log"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"

mkdir -p "${BASE_DIR}/logs"

timestamp() {
  date -Iseconds
}

check_health() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 10 "${HEALTH_URL}" >/tmp/chat_engineering_health.$$ 2>/tmp/chat_engineering_health_err.$$
    code=$?
    if [ "$code" -eq 0 ]; then
      printf "health=ok response=%s" "$(tr -d '\n' </tmp/chat_engineering_health.$$)"
    else
      printf "health=fail error=%s" "$(tr -d '\n' </tmp/chat_engineering_health_err.$$)"
    fi
    rm -f /tmp/chat_engineering_health.$$ /tmp/chat_engineering_health_err.$$
  else
    printf "health=unknown error=curl_not_found"
  fi
}

check_process() {
  if pgrep -f "python .*main.py|python3 .*main.py|main.py" >/dev/null 2>&1; then
    printf "process=ok"
  else
    printf "process=missing"
  fi
}

check_telegram_network() {
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 10 https://api.telegram.org >/dev/null 2>&1; then
      printf "telegram_api=reachable"
    else
      printf "telegram_api=unreachable"
    fi
  else
    printf "telegram_api=unknown error=curl_not_found"
  fi
}

while true; do
  printf "%s %s %s %s\n" "$(timestamp)" "$(check_health)" "$(check_process)" "$(check_telegram_network)" >>"${LOG_FILE}"
  sleep 60
done
