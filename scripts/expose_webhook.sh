#!/usr/bin/env bash
# Expose ONLY POST /webhooks/retell on its own hostname.
#
#   ./scripts/expose_webhook.sh caller.legaltoolsgpt.com
#
# Prerequisite: a DNS A record for that hostname pointing at this box,
# Cloudflare proxy OFF (grey / "DNS only"). Orange-cloud would put a bot
# challenge in front of the webhook; a challenge page is a non-2xx, and Retell
# reads non-2xx as failure and retries - so the symptom is duplicate
# deliveries, not an obvious DNS problem.
#
# Every step is verified before the next one runs.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:?usage: expose_webhook.sh <hostname>}"
BOX_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')

echo "==> 1/6  DNS"
RESOLVED=$(getent hosts "$HOST" | awk '{print $1}' | head -1 || true)
if [[ -z "$RESOLVED" ]]; then
  echo "FATAL: $HOST does not resolve. Create the A record first." >&2; exit 1
fi
if [[ "$RESOLVED" != "$BOX_IP" ]]; then
  echo "FATAL: $HOST -> $RESOLVED but this box is $BOX_IP." >&2
  echo "       If that is a Cloudflare edge IP, set the record to DNS only (grey)." >&2
  exit 1
fi
echo "    $HOST -> $RESOLVED (this box)"

echo "==> 2/6  nginx vhost (port 80 only; certbot adds TLS)"
cat > "/etc/nginx/sites-available/$HOST" <<NGINX
# caller - Retell webhook only.
#
# This hostname exposes exactly ONE path. Everything else 404s: the CRM, the
# health endpoint and the database stay reachable only through an SSH tunnel.
server {
    listen 80;
    server_name $HOST;

    # The one public route in the whole service.
    location = /webhooks/retell {
        proxy_pass http://127.0.0.1:4100/webhooks/retell;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # Retell gives up at 10s. Fail fast rather than holding the
        # connection open past their timeout and collecting a retry.
        proxy_connect_timeout 5s;
        proxy_read_timeout 15s;

        # The body is signed. Buffering or rewriting it breaks verification.
        proxy_request_buffering off;
        client_max_body_size 2m;
    }

    # The click redirect. Public because a RECIPIENT'S BROWSER hits it.
    # It returns a 302 and nothing else - see api/webhooks.py.
    location ^~ /c/ {
        proxy_pass http://127.0.0.1:4100;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / { return 404; }
}
NGINX
ln -sf "/etc/nginx/sites-available/$HOST" "/etc/nginx/sites-enabled/$HOST"
nginx -t
systemctl reload nginx
echo "    vhost live on :80"

echo "==> 3/6  certificate"
certbot --nginx -d "$HOST" --non-interactive --agree-tos \
        -m sean.sharefi@gmail.com --redirect
echo "    cert installed"

echo "==> 4/6  reachability from the public internet"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST \
       -H 'Content-Type: application/json' -d '{}' "https://$HOST/webhooks/retell" || true)
# 401 is the CORRECT answer here: unsigned request, rejected. A 000/502 means
# it never reached us.
if [[ "$code" == "401" ]]; then
  echo "    https://$HOST/webhooks/retell -> 401 (unsigned rejected: correct)"
else
  echo "FATAL: expected 401 from an unsigned POST, got '$code'." >&2; exit 1
fi
echo "    and a non-webhook path:"
curl -s -o /dev/null -w "      %{url_effective} -> %{http_code} (404 expected)\n" \
     -m 10 "https://$HOST/" || true

echo "==> 5/6  point the agent at it"
docker compose exec -T caller-worker python - <<PY
from api.config import load_config
from retell import Retell
cfg = load_config()
c = Retell(api_key=cfg.RETELL_API_KEY)
c.agent.update(cfg.AGENT_L1, webhook_url="https://$HOST/webhooks/retell")
print('    webhook_url set')
PY

echo "==> 6/6  read it back from Retell"
docker compose exec -T caller-worker python -c "
from api.config import load_config
from retell import Retell
cfg = load_config()
a = Retell(api_key=cfg.RETELL_API_KEY).agent.retrieve(cfg.AGENT_L1)
print('    agent webhook_url =', a.webhook_url)
"
echo
echo "DONE. Retell can now deliver to https://$HOST/webhooks/retell"
