#!/usr/bin/env bash
# ntfy-notify.sh "Заголовок" "Текст" [приоритет] [теги]
#   приоритет: min|low|default|high|urgent
#   теги:      warning,skull,rotating_light,white_check_mark ...
# Секреты держите в notify.env рядом (см. notify.env.example), а не в этом файле.
set -euo pipefail

[ -f "$(dirname "$0")/notify.env" ] && . "$(dirname "$0")/notify.env"

NTFY_URL="${NTFY_URL:-http://SERVER_IP:8090}"   # ВАЖНО: с самого сервера шлите по ЛОКАЛЬНОМУ адресу (hairpin-NAT)
NTFY_TOPIC="${NTFY_TOPIC:-security}"
NTFY_TOKEN="${NTFY_TOKEN:-tk_ВСТАВЬТЕ_ТОКЕН}"

title="${1:?title}"; body="${2:?body}"; prio="${3:-default}"; tags="${4:-}"

curl -sf -X POST "${NTFY_URL}/${NTFY_TOPIC}" \
  -H "Authorization: Bearer ${NTFY_TOKEN}" \
  -H "Title: ${title}" \
  -H "Priority: ${prio}" \
  ${tags:+-H "Tags: ${tags}"} \
  --data-binary "${body}" >/dev/null
