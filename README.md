![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![MikroTik](https://img.shields.io/badge/RouterOS-7.x-green)
# CrowdSec → MikroTik bouncer

Баним сканеров и веб-злодеев **прямо на роутере MikroTik**: CrowdSec читает логи
файрвола и access-лог реверс-прокси, детектит атаки, а маленький самописный
баунсер раскладывает атакующих по `address-list`, которые роутер дропает на WAN.
Никаких внешних бэкендов — всё крутится у себя.

Это рабочий код к циклу статей на forum.samohosting.ru:

1. **Локальный детект** — логи файрвола MikroTik → CrowdSec → баунсер → `address-list`.
2. **Community-блоклист** — глобальная репутация CrowdSec, отдельным контейнером, чтобы не «голодить» быстрый локальный цикл.
3. **Веб-детект через Traefik** — access-лог Traefik → CrowdSec → веб-список.
4. **Наблюдаемость** — Prometheus + Grafana.
5. **Алерты в телефон** — ntfy (бонус).

> ⚠️ **Кому подходит.** Схема имеет смысл прежде всего при **белом IP** — именно на
> него ломятся сканеры со всего интернета. За CGNAT/«серым» IP входящих сканов
> почти нет, и пользы будет сильно меньше.

---

## Что внутри

```
.
├── docker-compose.yml          # обычная: crowdsec + bouncer-local; профили: community, monitoring, full
├── .env.example                # секреты (ключ баунсера, креды роутера)
├── bouncer/
│   ├── Dockerfile
│   └── bouncer.py              # role-aware, multi-list, TTL через native timeout, sentinel, само-лечение
├── crowdsec/
│   ├── config.yaml.example     # use_wal + prometheus
│   ├── acquis.d/               # mikrotik.yaml (+ traefik.yaml.example)
│   ├── parsers/                # грок MikroTik + вайтлист своей инфры
│   └── scenarios/              # leaky-сценарий port-scan
├── mikrotik/setup.rsc          # правила файрвола, логирование, API-юзер, address-list дропы
├── host/                       # rsyslog + logrotate для приёма логов роутера
├── monitoring/                 # prometheus.yml + grafana provisioning
└── ntfy/                       # хелпер + watch-скрипт + systemd (бонус-алерты)
```

### Баунсер — ключевые идеи

- **Один фикс-лист на роль**, никаких «правил по номерам».
- **TTL через нативный `timeout` RouterOS** — баны само-выпадают даже если баунсер умер. Отдельный «разбанер» не нужен.
- **Дифф против РЕАЛЬНОГО роутера** (а не своего стейта) — дрейф чинится сам.
- **Sentinel-маркер** (`192.0.2.1`, TEST-NET-1) — переживает ребут роутера: пропал маркер и список пуст → переливаем весь набор.
- **Два контейнера из одного образа**: `ROLE=local` (быстрый цикл 15 с, мульти-лист scan/web) и `ROLE=community` (медленный 12 ч + refresh-ahead), чтобы огромный community-список не тормозил быстрый локальный.

> ℹ️ **Community-контейнер не обязателен.** Список огромный (десятки тысяч IP, у меня ~25–30k),
> и **слабый роутер на нём может задохнуться**: первый пролив идёт минутами (ограничение
> RouterOS API ~10 IP/с), а сам `address-list` на старших объёмах грузит CPU/память роутера.
> Если железо скромное (hAP lite/mini, старые модели) или просто не нужна глобальная репутация —
> поднимайте только `bouncer-local`, а `bouncer-community` пропустите. Локальный детект (Части 1 и 3)
> самодостаточен. Проверяйте нагрузку на роутере (`/system resource print`, CPU/free-memory) после включения.

---

## Группы запуска (профили compose)

| Команда | Что поднимается |
|---|---|
| `docker compose up -d` | **обычная**: `crowdsec` + `bouncer-local` (без community, без мониторинга) |
| `docker compose --profile full up -d` | **всё**: + `bouncer-community` + Prometheus/Grafana/exporters |
| `docker compose --profile community up -d` | обычная + community-баунсер |
| `docker compose --profile monitoring up -d` | обычная + мониторинг |

> **community по умолчанию не поднимается** — список большой, слабые роутеры могут задохнуться (см. ниже). Включайте осознанно профилем `community` или `full`.

---

## Быстрый старт

Предполагается: машина с Docker в LAN, MikroTik RouterOS 7.x с белым IP, `interface-list` **WAN** уже настроен.

```bash
git clone https://github.com/fenixvd/crowdsec-mikrotik-bouncer.git
cd crowdsec-mikrotik-bouncer

# 1. конфиги из примеров
cp .env.example .env
cp crowdsec/config.yaml.example crowdsec/config.yaml
cp crowdsec/parsers/s02-enrich/my-whitelist.yaml.example crowdsec/parsers/s02-enrich/my-whitelist.yaml
#   -> впишите в my-whitelist.yaml СВОИ адреса (WAN дома, VPN-выход и т.п.)

# 2. хост: приём логов роутера
sudo cp host/rsyslog/50-mikrotik.conf /etc/rsyslog.d/   # подставьте ROUTER_IP
sudo cp host/logrotate/mikrotik /etc/logrotate.d/
sudo systemctl restart rsyslog

# 3. роутер: применить mikrotik/setup.rsc (СНАЧАЛА прочитать и подставить SERVER_IP/пароль!)

# 4. поднять CrowdSec, выпустить ключ баунсера, вписать его в .env
docker compose up -d crowdsec
docker exec crowdsec cscli bouncers add mikrotik-bouncer   # ключ -> BOUNCER_KEY в .env

# 5. поднять «обычную» группу: crowdsec + локальный баунсер (БЕЗ community)
docker compose up -d --build
#   ...или сразу всё (вкл. community + мониторинг):
#   docker compose --profile full up -d --build

# проверка
docker logs -f cs-bouncer-local
docker exec crowdsec cscli decisions list --limit 0
# на роутере: /ip firewall address-list print where list~"crowdsec"
```

### Веб-детект (Часть 3) — опционально

```bash
# включить файловый json-лог в Traefik (accessLog: filePath/format: json),
# раскомментировать монтирование лога в docker-compose.yml (crowdsec -> /logs/traefik:ro),
cp crowdsec/acquis.d/traefik.yaml.example crowdsec/acquis.d/traefik.yaml
docker exec crowdsec cscli collections install crowdsecurity/traefik
docker restart crowdsec
```

### Мониторинг (Часть 4) — опционально

```bash
docker compose --profile monitoring up -d
# Grafana: http://SERVER_IP:3001  (дашборд CrowdSec: grafana.com id 13927)
```
Уже есть свой Prometheus/Grafana? Не поднимайте стек заново — добавьте один scrape-job
`crowdsec:6060` в свой Prometheus (см. статью, Часть 4, «Шаг 2-бис»). **Порт 6060 в мир не выставлять.**

### Алерты в ntfy (Часть 5) — опционально

```bash
cp ntfy/notify.env.example ntfy/notify.env      # вписать URL/топик/токен
sudo cp ntfy/systemd/* /etc/systemd/system/      # поправьте путь к скрипту в .service
sudo systemctl enable --now crowdsec-watch.timer
```

---

### Как удалить / остановить

```bash
# остановить и удалить контейнеры (тома с данными остаются)
docker compose down
# ...полностью, вместе с данными CrowdSec/стейтом баунсера:
docker compose down -v
```

Откатить на самом роутере то, что заводил `mikrotik/setup.rsc` (RouterOS):

```rsc
# 1. удалить адреса из всех списков crowdsec (баны + sentinel-маркеры)
/ip firewall address-list remove [find list~"crowdsec"]

# 2. удалить правила-дропы по спискам crowdsec
/ip firewall filter remove [find comment~"crowdsec"]

# 3. удалить API-пользователя баунсера и его группу
/user remove [find name="crowdsec-bouncer-user"]
/user group remove [find name="crowdsec"]

# 4. (опц.) убрать отправку логов файрвола на сервер
/system logging remove [find action="to-crowdsec"]
/system logging action remove [find name="to-crowdsec"]

# 5. (опц.) удалить правила логирования дропов WAN, если больше не нужны
/ip firewall filter remove [find log-prefix~"wan_drop" or log-prefix~"wan_fwd_drop"]
```

> ⚠️ Проверьте `find`-выборки перед применением (`/ip firewall filter print where comment~"crowdsec"`),
> чтобы случайно не снести лишнего, если у вас есть свои правила с похожими комментариями.

---

## Грабли (коротко — подробности в статьях)

- **Монтируйте каталог логов, а не файл** — иначе ночная ротация отвяжет контейнер от живого лога, и детект тихо умрёт.
- **`use_wal: true`** в CrowdSec — без WAL заливка community лочит SQLite.
- **Вайтлистите свою инфру** — CrowdSec с радостью забанит ваш VPN-выход/CDN/пиров.
- **BT/P2P-порты на WAN дропайте без лога** — иначе port-scan забанит легитимных пиров и ваши CGNAT-адреса.
- **Реальный IP за Traefik** — если фронт проксирует (Cloudflare orange), настройте `forwardedHeaders.trustedIPs`, иначе забаните прокси.

## Безопасность / приватность

Все значения в репозитории — **плейсхолдеры** (`SERVER_IP`, `ROUTER_IP`, `tk_...`).
Реальные секреты живут в `.env` / `notify.env`, которые **не** коммитятся (см. `.gitignore`).
LAPI (8080), метрики (6060), Prometheus (9090) наружу не публикуются — только docker-сеть/LAN.

## Лицензия

MIT — см. [LICENSE](LICENSE).
