![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue)
![MikroTik](https://img.shields.io/badge/RouterOS-7.x-green)

🇬🇧 **English** | 🇷🇺 [Русский](README.md)

# CrowdSec → MikroTik bouncer

Ban scanners and web attackers **right on the MikroTik router**: CrowdSec reads
firewall and reverse-proxy logs, detects attacks, and a small custom bouncer
pushes the attackers into `address-list`s that the router drops at the WAN edge.
No external backends — everything runs on your own gear.

This is the working code for a series of articles (in Russian) on forum.samohosting.ru:

1. **Local detection** — MikroTik firewall logs → CrowdSec → bouncer → `address-list`.
2. **Community blocklist** — CrowdSec global reputation, in a separate container so the huge list doesn't starve the fast local loop.
3. **Web detection via Traefik** — Traefik access log → CrowdSec → web list.
4. **Observability** — Prometheus + Grafana.
5. **Phone alerts** — ntfy (bonus).

> ⚠️ **Who it's for.** This makes sense mainly with a **public (white) IP** — that's
> what scanners hammer from all over the internet. Behind CGNAT/"grey" IP there are
> almost no inbound scans, so the benefit is much smaller.

> 🌐 **Note on language.** This English README is a condensed overview. Inline
> comments inside the code and config files are in Russian — the logic is simple,
> but keep a translator handy if you read them. The full write-up lives in the
> Russian article series (link in the repo description).

---

## What's inside

```
.
├── docker-compose.yml          # default: crowdsec + bouncer-local; profiles: community, monitoring, full
├── .env.example                # secrets (bouncer key, router creds)
├── bouncer/
│   ├── Dockerfile
│   └── bouncer.py              # role-aware, multi-list, TTL via native timeout, sentinel, self-healing
├── crowdsec/
│   ├── config.yaml.example     # use_wal + prometheus
│   ├── acquis.d/               # mikrotik.yaml (+ traefik.yaml.example)
│   ├── parsers/                # MikroTik grok + own-infra whitelist
│   └── scenarios/              # leaky port-scan scenario
├── mikrotik/setup.rsc          # firewall rules, logging, API user, address-list drops
├── host/                       # rsyslog + logrotate to receive router logs
├── monitoring/                 # prometheus.yml + grafana provisioning
└── ntfy/                       # helper + watch script + systemd (bonus alerts)
```

### Bouncer — key ideas

- **One fixed list per role**, no "rule-per-entry by number" fragility.
- **TTL via RouterOS native `timeout`** — bans self-expire even if the bouncer dies. No separate "unbanner" needed.
- **Diff against the REAL router** (not against own state) — drift heals itself.
- **Sentinel marker** (`192.0.2.1`, TEST-NET-1) — survives a router reboot: marker gone and list empty → re-pour the whole set.
- **Two containers from one image**: `ROLE=local` (fast 15 s loop, multi-list scan/web) and `ROLE=community` (slow 12 h loop + refresh-ahead), so the huge community list never slows down the fast local one.

> ℹ️ **The community container is optional.** The list is huge (tens of thousands of IPs,
> ~25–30k in my case) and **a weak router can choke on it**: the first pour takes minutes
> (RouterOS API caps at ~10 IP/s), and a large `address-list` loads the router's CPU/RAM.
> On modest hardware (hAP lite/mini, older models) — or if you just don't need global
> reputation — run only `bouncer-local` and skip `bouncer-community`. Local detection
> (Parts 1 & 3) is self-sufficient. Watch the router (`/system resource print`, CPU/free-memory) after enabling.

---

## Run groups (compose profiles)

| Command | What comes up |
|---|---|
| `docker compose up -d` | **default**: `crowdsec` + `bouncer-local` (no community, no monitoring) |
| `docker compose --profile full up -d` | **everything**: + `bouncer-community` + Prometheus/Grafana/exporters |
| `docker compose --profile community up -d` | default + community bouncer |
| `docker compose --profile monitoring up -d` | default + monitoring |

> **community is not started by default** — the list is large and weak routers may choke (see above). Enable it deliberately via the `community` or `full` profile.

---

## Quick start

Assumes: a Docker host on the LAN, MikroTik RouterOS 7.x with a public IP, `interface-list` **WAN** already configured.

```bash
git clone https://github.com/fenixvd/crowdsec-mikrotik-bouncer.git
cd crowdsec-mikrotik-bouncer

# 1. configs from examples
cp .env.example .env
cp crowdsec/config.yaml.example crowdsec/config.yaml
cp crowdsec/parsers/s02-enrich/my-whitelist.yaml.example crowdsec/parsers/s02-enrich/my-whitelist.yaml
#   -> put YOUR addresses into my-whitelist.yaml (home WAN, VPN exit, etc.)

# 2. host: receive router logs
sudo cp host/rsyslog/50-mikrotik.conf /etc/rsyslog.d/   # set ROUTER_IP
sudo cp host/logrotate/mikrotik /etc/logrotate.d/
sudo systemctl restart rsyslog

# 3. router: apply mikrotik/setup.rsc (READ IT FIRST and set SERVER_IP / password!)

# 4. bring up CrowdSec, issue a bouncer key, put it in .env
docker compose up -d crowdsec
docker exec crowdsec cscli bouncers add mikrotik-bouncer   # key -> BOUNCER_KEY in .env

# 5. bring up the default group: crowdsec + local bouncer (NO community)
docker compose up -d --build
#   ...or everything at once (incl. community + monitoring):
#   docker compose --profile full up -d --build

# check
docker logs -f cs-bouncer-local
docker exec crowdsec cscli decisions list --limit 0
# on the router: /ip firewall address-list print where list~"crowdsec"
```

### Web detection (Part 3) — optional

```bash
# enable file json access log in Traefik (accessLog: filePath/format: json),
# uncomment the log mount in docker-compose.yml (crowdsec -> /logs/traefik:ro),
cp crowdsec/acquis.d/traefik.yaml.example crowdsec/acquis.d/traefik.yaml
docker exec crowdsec cscli collections install crowdsecurity/traefik
docker restart crowdsec
```

### Monitoring (Part 4) — optional

```bash
docker compose --profile monitoring up -d
# Grafana: http://SERVER_IP:3001  (CrowdSec dashboard: grafana.com id 13927)
```
Already running your own Prometheus/Grafana? Don't redeploy the stack — just add a single
`crowdsec:6060` scrape job to your Prometheus. **Never expose port 6060 to the internet.**

### ntfy alerts (Part 5) — optional

```bash
cp ntfy/notify.env.example ntfy/notify.env      # set URL/topic/token
sudo cp ntfy/systemd/* /etc/systemd/system/      # fix the script path in the .service
sudo systemctl enable --now crowdsec-watch.timer
```

---

### How to remove / stop

```bash
# stop and remove containers (data volumes are kept)
docker compose down
# ...completely, including CrowdSec data / bouncer state:
docker compose down -v
```

Roll back what `mikrotik/setup.rsc` created, on the router itself (RouterOS):

```rsc
# 1. remove addresses from all crowdsec lists (bans + sentinel markers)
/ip firewall address-list remove [find list~"crowdsec"]

# 2. remove drop rules for crowdsec lists
/ip firewall filter remove [find comment~"crowdsec"]

# 3. remove the bouncer API user and its group
/user remove [find name="crowdsec-bouncer-user"]
/user group remove [find name="crowdsec"]

# 4. (opt.) stop shipping firewall logs to the server
/system logging remove [find action="to-crowdsec"]
/system logging action remove [find name="to-crowdsec"]

# 5. (opt.) remove the WAN-drop logging rules if no longer needed
/ip firewall filter remove [find log-prefix~"wan_drop" or log-prefix~"wan_fwd_drop"]
```

> ⚠️ Verify the `find` selections before applying (`/ip firewall filter print where comment~"crowdsec"`)
> so you don't accidentally nuke your own rules with similar comments.

---

## Gotchas (short — details in the articles)

- **Mount the log directory, not the file** — otherwise nightly rotation detaches the container from the live log and detection dies silently.
- **`use_wal: true`** in CrowdSec — without WAL, pouring the community list locks SQLite.
- **Whitelist your own infra** — CrowdSec will happily ban your VPN exit / CDN / peers.
- **Drop BT/P2P ports on WAN without logging** — otherwise port-scan bans legit peers and your own CGNAT addresses.
- **Real client IP behind Traefik** — if a front proxies (Cloudflare orange), set `forwardedHeaders.trustedIPs`, or you'll ban the proxy.

## Security / privacy

All values in the repo are **placeholders** (`SERVER_IP`, `ROUTER_IP`, `tk_...`).
Real secrets live in `.env` / `notify.env`, which are **not** committed (see `.gitignore`).
LAPI (8080), metrics (6060) and Prometheus (9090) are never published externally — docker network / LAN only.

## License

MIT — see [LICENSE](LICENSE).
