# RouterOS setup для CrowdSec-баунсера.
# ВНИМАНИЕ: проверьте каждую строку под свою конфигурацию ПЕРЕД применением.
# Подставьте свои значения: SERVER_IP (машина с CrowdSec), пароль API-юзера.
# Предполагается, что interface-list "WAN" уже существует.

# ── 1. Логирование дропов файрвола (Часть 1) ───────────────────────────────
/ip firewall filter
# дроп + лог всего, что ломится на сам роутер с WAN
add chain=input action=drop in-interface-list=WAN \
    log=yes log-prefix="wan_drop" comment="drop+log WAN -> router"
# дроп + лог трафика WAN->LAN, который не был DST-NAT'нут (не наши проброшенные порты)
add chain=forward action=drop in-interface-list=WAN connection-nat-state=!dstnat \
    log=yes log-prefix="wan_fwd_drop" comment="drop+log WAN -> LAN (not forwarded)"

# ── 2. Отправка логов файрвола на сервер с CrowdSec по syslog ───────────────
/system logging action
add name=to-crowdsec target=remote remote=SERVER_IP remote-port=514
/system logging
add topics=firewall action=to-crowdsec

# ── 3. Ограниченный API-пользователь для баунсера (Часть 1) ─────────────────
/user group add name=crowdsec policy=api,read,write,test
/user add name=crowdsec-bouncer-user group=crowdsec \
    password=СГЕНЕРЬ_ДЛИННЫЙ address=SERVER_IP/32
# API 8728 (plaintext) держим только в LAN. Хотите TLS — api-ssl 8729.

# ── 4. Дроп по спискам CrowdSec (наполняет баунсер) ────────────────────────
# Списки баунсер создаёт сам; здесь — статические правила дропа. Ставим повыше.
/ip firewall filter
add chain=input   action=drop in-interface-list=WAN src-address-list=crowdsec-mikrotik-ff   comment="crowdsec scan in"   place-before=0
add chain=forward action=drop in-interface-list=WAN src-address-list=crowdsec-mikrotik-ff   comment="crowdsec scan fwd"
add chain=input   action=drop in-interface-list=WAN src-address-list=crowdsec-traefik-http  comment="crowdsec web in"    place-before=0
add chain=forward action=drop in-interface-list=WAN src-address-list=crowdsec-traefik-http  comment="crowdsec web fwd"
# fallback-список баунсера (прочие локальные сценарии, напр. ssh-bf)
add chain=input   action=drop in-interface-list=WAN src-address-list=crowdsec-local         comment="crowdsec local in"  place-before=0
add chain=forward action=drop in-interface-list=WAN src-address-list=crowdsec-local         comment="crowdsec local fwd"
add chain=input   action=drop in-interface-list=WAN src-address-list=crowdsec-community      comment="crowdsec community in"  place-before=0
add chain=forward action=drop in-interface-list=WAN src-address-list=crowdsec-community      comment="crowdsec community fwd"

# ── 5. (опционально) BT/P2P-порты своих сервисов дропать БЕЗ лога ───────────
# Иначе port-scan забанит легитимных пиров и ваши же CGNAT-адреса.
# /ip firewall raw
# add chain=prerouting action=drop in-interface-list=WAN protocol=tcp dst-port=ВАШ_P2P_ПОРТ log=no
