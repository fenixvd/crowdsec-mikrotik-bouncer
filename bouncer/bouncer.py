#!/usr/bin/env python3
"""CrowdSec -> MikroTik bouncer (role-aware, multi-list).

Кладёт решения CrowdSec в RouterOS address-list с нативным TTL (timeout),
диффит против РЕАЛЬНОГО содержимого роутера (само-лечение) и держит
sentinel-маркер, чтобы переждать ребут роутера.

ROLE=local      origins=crowdsec,cscli  -> маршрутизация по сценарию:
                    scan  -> {PREFIX}-mikrotik-ff
                    web   -> {PREFIX}-traefik-http
                    else  -> {PREFIX}-local
                быстрый цикл (15s), TTL 6h.
ROLE=community  origins=CAPI,lists       -> {PREFIX}-community,
                медленный цикл (12h) + refresh-ahead, TTL 72h.

Запускайте ДВА контейнера из одного образа: ROLE=local и ROLE=community.
У каждого свой список, свой sentinel и свой state-файл — они не мешают друг другу.
"""
import os, time, json, random, urllib.request, urllib.error
import librouteros
from librouteros.query import Key
from librouteros.exceptions import TrapError, ConnectionClosed

ROLE   = os.environ.get("ROLE", "local").lower()
LAPI   = os.environ["CROWDSEC_URL"].rstrip("/")
KEY    = os.environ["CROWDSEC_BOUNCER_API_KEY"]
MT_HOST, MT_PORT = os.environ["MIKROTIK_HOST"].split(":")
MT_USER = os.environ["MIKROTIK_USER"]
MT_PASS = os.environ["MIKROTIK_PASS"]
PREFIX  = os.environ.get("LIST_PREFIX", "crowdsec")
RETRY   = int(os.environ.get("RETRY_INTERVAL", "60"))
SENTINEL_ADDR = "192.0.2.1"        # TEST-NET-1, никогда не маршрутизируется

SCAN     = f"{PREFIX}-mikrotik-ff"
HTTP     = f"{PREFIX}-traefik-http"
FALLBACK = f"{PREFIX}-local"
COMMUNITY = f"{PREFIX}-community"

if ROLE == "community":
    ORIGINS  = os.environ.get("COMMUNITY_ORIGINS", "CAPI,lists")
    OWNED    = {COMMUNITY}
    SENTINEL = f"{PREFIX}-meta-community"
    INTERVAL = int(os.environ.get("COMMUNITY_INTERVAL", str(12 * 3600)))
    TTL      = int(os.environ.get("TTL_REMOTE_SECONDS", str(72 * 3600)))
    REFRESH  = int(os.environ.get("REFRESH_AHEAD_SECONDS", str(14 * 3600)))  # >= INTERVAL!
    STATE    = "/app/state/state-community.json"
    LOOP     = 60                   # дешёвый heartbeat (проверка ребута); reconcile раз в INTERVAL
else:
    ROLE     = "local"
    ORIGINS  = os.environ.get("LOCAL_ORIGINS", "crowdsec,cscli")
    OWNED    = {SCAN, HTTP, FALLBACK}
    SENTINEL = f"{PREFIX}-meta-local"
    INTERVAL = int(os.environ.get("LOCAL_INTERVAL", "15"))
    TTL      = int(os.environ.get("TTL_LOCAL_SECONDS", str(6 * 3600)))
    REFRESH  = int(os.environ.get("REFRESH_AHEAD_SECONDS", "1800"))
    STATE    = "/app/state/state-local.json"
    LOOP     = INTERVAL


def log(*a): print(time.strftime("%H:%M:%S"), f"[{ROLE}]", *a, flush=True)
def jittered(t): j = t // 10; return t + random.randint(-j, j)   # +/-10%, размазать всплески рефреша


def list_for(scenario):
    """В какой список положить IP. Для community — всегда один список."""
    if ROLE == "community":
        return COMMUNITY
    s = (scenario or "").lower()
    if "mikrotik" in s:                               return SCAN
    if "http" in s or "nginx" in s or "traefik" in s: return HTTP
    return FALLBACK


def fetch():
    """Полный активный набор решений нужного origin -> {addr: list}."""
    url = f"{LAPI}/v1/decisions/stream?startup=true&origins={ORIGINS}"
    req = urllib.request.Request(url, headers={"X-Api-Key": KEY})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r) or {}
    desired = {}
    for d in data.get("new") or []:
        v = d.get("value")
        if d.get("scope", "Ip") in ("Ip", "Range") and v and ":" not in v:   # только IPv4
            desired[v] = list_for(d.get("scenario"))
    return desired


def load_state():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return {}

def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"; json.dump(st, open(tmp, "w")); os.replace(tmp, STATE)


def connect():
    return librouteros.connect(host=MT_HOST, port=int(MT_PORT),
                               username=MT_USER, password=MT_PASS, timeout=30)

def present(al):
    """addr -> list, только по НАШИМ спискам (по фильтру, не сканируя весь роутер)."""
    out = {}
    for lst in OWNED:
        for e in al.select("address").where(Key("list") == lst):
            out[str(e["address"])] = lst
    return out

def add(al, lst, addr, ttl):
    try: al.add(list=lst, address=addr, timeout=str(ttl), comment="crowdsec")
    except TrapError: set_to(al, lst, addr, ttl)        # уже есть -> просто обновим timeout

def set_to(al, lst, addr, ttl):
    for e in al.select(".id").where(Key("list") == lst, Key("address") == addr):
        al.update(**{".id": e[".id"], "timeout": str(ttl)})

def remove(al, lst, addr):
    for e in al.select(".id").where(Key("list") == lst, Key("address") == addr):
        al.remove(e[".id"])

def sentinel_ok(al):
    for _ in al.select(".id").where(Key("list") == SENTINEL, Key("address") == SENTINEL_ADDR):
        return True
    return False


def reconcile(al, state, now):
    desired = fetch()
    cur = present(al)                       # router truth -> само-лечение
    added = moved = refreshed = removed = 0
    for addr, lst in desired.items():
        c = cur.get(addr)
        if c is None:                       # нет на роутере -> добавить
            t = jittered(TTL); add(al, lst, addr, t)
            state[addr] = {"list": lst, "exp": now + t}; added += 1
        elif c != lst:                      # сменился сценарий -> переложить в нужный список
            remove(al, c, addr); t = jittered(TTL); add(al, lst, addr, t)
            state[addr] = {"list": lst, "exp": now + t}; moved += 1
        else:                               # на месте -> рефреш только если скоро протухнет
            exp = state.get(addr, {}).get("exp")
            if exp is None or exp - now <= REFRESH:
                t = jittered(TTL); set_to(al, lst, addr, t)
                state[addr] = {"list": lst, "exp": now + t}; refreshed += 1
    for addr, lst in cur.items():           # на роутере, но уже не желателен -> убрать
        if addr not in desired: remove(al, lst, addr); removed += 1
    for addr in [a for a in state if a not in desired]: state.pop(addr, None)
    return len(desired), added, refreshed, moved, removed


def main():
    log(f"start origins={ORIGINS} lists={sorted(OWNED)} interval={INTERVAL}s "
        f"ttl={TTL}s refresh={REFRESH}s")
    state = load_state(); api = None; last = 0.0; first = True
    while True:
        try:
            if api is None: api = connect(); log("connected to mikrotik")
            al = api.path("ip", "firewall", "address-list"); now = time.time()

            if not sentinel_ok(al):
                try: al.add(list=SENTINEL, address=SENTINEL_ADDR,
                            comment=f"crowdsec {ROLE} marker (do not delete)")
                except TrapError: pass
                if not present(al):
                    state = {}; log("router wiped -> re-pour full set")

            if first or now - last >= INTERVAL:
                d, a, r, mv, rm = reconcile(al, state, now); last = now
                if a or r or mv or rm or first:
                    log(f"desired={d} +{a} ~{r} move={mv} -{rm} tracked={len(state)}")
                save_state(state)
            first = False
        except (ConnectionClosed, OSError) as e:
            log("mikrotik unreachable, retry:", e); api = None; time.sleep(RETRY); continue
        except urllib.error.URLError as e:
            log("LAPI unreachable, retry:", e); time.sleep(RETRY); continue
        except Exception as e:
            log("error:", repr(e)); api = None; time.sleep(RETRY); continue
        time.sleep(LOOP)


if __name__ == "__main__":
    main()
