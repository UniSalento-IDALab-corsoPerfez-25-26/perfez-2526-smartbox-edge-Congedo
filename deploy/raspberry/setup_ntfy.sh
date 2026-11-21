#!/usr/bin/env bash
# Server di notifiche ntfy sul Raspberry, per gli allarmi sul telefono del guidatore (registro D-39).
#
# Uso (dall'autore, una volta sola; chiede la password del Raspberry e poi quella scelta per il telefono):
#   sudo bash ~/perfez-2526-smartbox-edge-Congedo/deploy/raspberry/setup_ntfy.sh
#
# Cosa fa:
#  1. installa ntfy dal repository ufficiale (archive.ntfy.sh);
#  2. configura /etc/ntfy/server.yml: porta 2586, accesso NEGATO a chi non ha un account (deny-all);
#  3. crea due account:
#       smartbox-pub  può solo SCRIVERE sul topic "smartbox" (lo usa la Smartbox, con un token);
#       telefono      può solo LEGGERE il topic "smartbox" (lo usa l'app ntfy; la password la sceglie l'autore);
#  4. salva il token di smartbox-pub in ~smartbox/smartbox-dati/secrets.yaml (phone.token), senza mostrarlo.
# Le notifiche restano tra Raspberry e telefono: nessun servizio esterno.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Lanciare con sudo: sudo bash $0"; exit 1; }
UTENTE=smartbox
DATI=/home/$UTENTE/smartbox-dati
PY=/home/$UTENTE/perfez-2526-smartbox-edge-Congedo/.venv/bin/python
TOPIC=smartbox

echo "== 1/4 Installazione di ntfy"
if ! command -v ntfy >/dev/null; then
  mkdir -p /etc/apt/keyrings
  curl -fsSL -o /etc/apt/keyrings/ntfy.gpg https://archive.ntfy.sh/apt/keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/ntfy.gpg] https://archive.ntfy.sh/apt stable main" \
    > /etc/apt/sources.list.d/ntfy.list
  apt-get update -q
  apt-get install -y -q ntfy
fi
ntfy --help >/dev/null && echo "ntfy installato"

echo "== 2/4 Configurazione (accesso solo con account)"
[ -f /etc/ntfy/server.yml ] && cp -n /etc/ntfy/server.yml /etc/ntfy/server.yml.originale || true
cat > /etc/ntfy/server.yml <<'EOF'
# Smartbox (registro D-39): notifiche locali tra Raspberry e telefono del guidatore.
base-url: "http://smartbox.local:2586"
listen-http: ":2586"
cache-file: "/var/cache/ntfy/cache.db"
cache-duration: "12h"
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
enable-signup: false
enable-login: true
EOF
mkdir -p /var/cache/ntfy /var/lib/ntfy
chown ntfy:ntfy /var/cache/ntfy /var/lib/ntfy
systemctl enable --now ntfy
systemctl restart ntfy
sleep 2
systemctl is-active --quiet ntfy && echo "servizio ntfy attivo"

NT() { runuser -u ntfy -- ntfy "$@"; }

echo "== 3/4 Account"
if ! NT user list 2>&1 | grep -q "user smartbox-pub"; then
  NTFY_PASSWORD="$(head -c 24 /dev/urandom | base64)" NT user add smartbox-pub >/dev/null
fi
NT access smartbox-pub "$TOPIC" write-only >/dev/null
if ! NT user list 2>&1 | grep -q "user telefono"; then
  echo
  echo ">>> Scegli la password per l'app ntfy sul telefono (account 'telefono')."
  echo ">>> Ti viene chiesta due volte; i caratteri non compaiono mentre scrivi."
  NT user add telefono
fi
NT access telefono "$TOPIC" read-only >/dev/null
echo "account pronti: smartbox-pub (solo scrittura), telefono (solo lettura) sul topic '$TOPIC'"

echo "== 4/4 Token della Smartbox in $DATI/secrets.yaml"
TOKEN="$(NT token add --label=smartbox smartbox-pub 2>&1 | grep -o 'tk_[A-Za-z0-9]*' | head -1)"
[ -n "$TOKEN" ] || { echo "token non creato"; exit 1; }
runuser -u "$UTENTE" -- env SMARTBOX_TOKEN="$TOKEN" "$PY" - <<'EOF'
import os, yaml
p = "/home/smartbox/smartbox-dati/secrets.yaml"   # runuser non cambia HOME
sec = (yaml.safe_load(open(p)) if os.path.exists(p) else None) or {}
sec["phone"] = {"token": os.environ["SMARTBOX_TOKEN"]}
os.umask(0o077)
with open(p, "w") as f:
    yaml.safe_dump(sec, f, sort_keys=False)
print("token salvato (non mostrato)")
EOF
unset TOKEN
echo
echo "FATTO"
