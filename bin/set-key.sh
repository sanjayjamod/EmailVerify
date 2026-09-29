#!/bin/bash
# Install the MailTester Ninja API key.
#
#   /opt/emails/bin/set-key.sh YOUR-KEY-HERE
#
# The key is tried against the live API first. It is only written to disk if
# the API actually answers, so a typo can never leave the worker spinning
# against a dead key. Costs one credit.
set -u

KEY="${1:-}"
KEYF=/opt/emails/mailtester.key
PROBE=postmaster@gmail.com

if [ -z "$KEY" ]; then
    echo "usage: $0 <mailtester-ninja-api-key>" >&2
    exit 1
fi

echo "testing the key against the live API..."
RESP=$(curl -s -m 30 --get "https://happy.mailtester.ninja/ninja" \
         --data-urlencode "email=$PROBE" --data-urlencode "key=$KEY")

if [ -z "$RESP" ]; then
    echo "FAILED: no answer from the API. Key not saved." >&2
    exit 1
fi

echo "API said: $RESP"

# A bad key still comes back HTTP 200 with a "code" field, so check the
# verdict itself - an invalid key answers with code "--" / "Invalid Key".
if echo "$RESP" | grep -q '"Invalid Key"'; then
    echo "FAILED: the API says that key is invalid. Key not saved." >&2
    exit 1
fi
if ! echo "$RESP" | grep -qE '"code":[[:space:]]*"(ok|ko|mb)"'; then
    echo "FAILED: unexpected answer, key not saved." >&2
    exit 1
fi

printf '%s' "$KEY" > "$KEYF"
chmod 600 "$KEYF"
echo "key saved to $KEYF (chmod 600)"

# The key states its own ceilings: "rate" is per 10s, "limit" is per day.
RATE=$(echo "$RESP"  | sed -n 's/.*"rate":[[:space:]]*\([0-9]*\).*/\1/p'  | head -1)
DAILY=$(echo "$RESP" | sed -n 's/.*"limit":[[:space:]]*\([0-9]*\).*/\1/p' | head -1)
if [ -n "$RATE" ] && [ "$RATE" -gt 0 ] 2>/dev/null; then
    /opt/emails/venv/bin/python - "$RATE" "${DAILY:-0}" <<'PYCONF'
import json, sys, datetime
rate, daily = int(sys.argv[1]), int(sys.argv[2])
p = "/opt/emails/verify.conf.json"
cfg = json.load(open(p))
for tier in (11, 57):          # published Pro / Ultimate ceilings
    if rate <= tier:
        rate = tier
        break
cfg["rate_limit"] = rate
cfg["rate_period"] = 10.0
if daily > 0:
    cfg["daily_cap"] = daily
cfg["reported_rate"] = rate
cfg["reported_daily"] = daily
cfg["key_saved_at"] = datetime.datetime.now().isoformat(timespec="seconds")
json.dump(cfg, open(p, "w"), indent=2)
print("rate set to %d every 10s, daily cap %d" % (rate, cfg["daily_cap"]))
PYCONF
    chmod 600 /opt/emails/verify.conf.json
fi

systemctl restart email-verify-worker
sleep 3
systemctl is-active email-verify-worker >/dev/null \
    && echo "worker restarted and running" \
    || { echo "WARNING: worker did not come back up" >&2; exit 1; }

echo
echo "--- worker log ---"
tail -4 /opt/emails/logs/verify-worker.log
echo
echo "Done. Open https://email.sanjayjamod.cloud - the red warning should be gone."
