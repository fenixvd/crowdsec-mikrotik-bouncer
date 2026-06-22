#!/usr/bin/env bash
# crowdsec-watch.sh — гоняется systemd-таймером (раз в ~5 мин).
# Шлёт алерт при СМЕНЕ состояния (детект умер / восстановлен) и при самобане своей инфры.
# Требует ntfy-notify.sh рядом. Подставьте SERVER_IP и путь к my-whitelist.yaml.
set -euo pipefail

HERE="$(dirname "$0")"
NOTIFY="$HERE/ntfy-notify.sh"
STATE=/var/lib/crowdsec-watch ; mkdir -p "$STATE"
PROM="http://SERVER_IP:9090"                       # ваш Prometheus
WHITELIST="/path/to/crowdsec/parsers/s02-enrich/my-whitelist.yaml"

# 1) Детект жив? Были ли строки syslog от роутера за 10 минут.
q='sum(increase(cs_syslogsource_hits_total[10m]))'
hits=$(curl -sf "${PROM}/api/v1/query?query=${q}" \
        | grep -oE '"value":\[[0-9.]+,"[0-9.]+"' | grep -oE '"[0-9.]+"$' | tr -d '"' || echo 0)
if awk "BEGIN{exit !(${hits:-0}==0)}"; then
  [ -f "$STATE/syslog_dead" ] || "$NOTIFY" "CrowdSec: детект умер" \
     "Нет строк syslog от роутера за 10 мин. Проверь rsyslog/ротацию/роутер." urgent rotating_light
  touch "$STATE/syslog_dead"
else
  [ -f "$STATE/syslog_dead" ] && "$NOTIFY" "CrowdSec: детект восстановлен" "syslog снова идёт" default white_check_mark
  rm -f "$STATE/syslog_dead"
fi

# 2) Не забанили ли СВОИХ? known-IP берём динамически из вайтлиста.
known=$(grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' "$WHITELIST" 2>/dev/null || true)
banned=$(docker exec crowdsec cscli decisions list -o json --limit 0 \
         | grep -oE '"value":"[0-9.]+"' | grep -oE '[0-9.]+' || true)
for ip in $known; do
  if grep -qx "$ip" <<<"$banned"; then
    "$NOTIFY" "CrowdSec: самобан!" "Своя инфра в бане: $ip -> cscli decisions delete --ip $ip" urgent skull
  fi
done
