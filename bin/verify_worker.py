#!/usr/bin/env python3
"""
The grinder. Runs for ever under systemd, works through whatever is queued in
verify.db, and never goes faster than the plan's rate limit.

    MailTester Ninja:  GET https://happy.mailtester.ninja/ninja?email=X&key=KEY
    -> {"email": ..., "code": "ok|ko|mb", "message": "Accepted|Rejected|..."}

Rate limits published by MailTester Ninja:
    Pro       11 emails / 10 s   (~95,000 a day)
    Ultimate  57 emails / 10 s   (~490,000 a day)

Design notes
  * A token bucket is shared by every thread, so the whole process obeys one
    limit no matter how many requests are in flight.
  * Work is claimed in batches and parked for 15 minutes, so a crash or a
    reboot loses nothing - the rows simply become claimable again.
  * Every answer is written to the cache table, so the same address is never
    paid for twice.
  * A daily cap stops a runaway loop from burning the whole balance overnight.
"""
import json, os, signal, sys, threading, time
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

BASE = os.environ.get("EMAILS_BASE", "/opt/emails")
CONF = BASE + "/verify.conf.json"
KEYF = BASE + "/mailtester.key"
API = "https://happy.mailtester.ninja/ninja"

DEFAULTS = {
    "rate_limit": 11,        # requests ...
    "rate_period": 10.0,     # ... per this many seconds
    "workers": 2,            # connections in flight - the official
                             # MailTester Ninja app uses 2 ("1/2cnx"), and the
                             # API counts connections as well as rate
    "daily_cap": 90000,      # hard stop per calendar day
    "max_attempts": 3,
    "http_timeout": 30,
    "transient": ["Timeout", "Mx Error", "SPAM Block"],
    "retry_delay": 300,
    "cooldown": 20,          # seconds every thread waits after a 429
}

stop = threading.Event()


def log(msg):
    print("%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def load_conf():
    conf = dict(DEFAULTS)
    if os.path.isfile(CONF):
        try:
            conf.update(json.load(open(CONF)))
        except Exception as e:
            log("bad %s (%s) - using defaults" % (CONF, e))
    return conf


def load_key():
    if not os.path.isfile(KEYF):
        return ""
    k = open(KEYF).read().strip()
    return "" if k.startswith("PUT-YOUR") else k


class Bucket:
    """Token bucket. take() blocks until this process is allowed one request.

    Deliberately near-burstless. A bucket that starts full with `limit` tokens
    fires all of them in the first instant and then keeps drip-feeding, which
    puts twice the limit into the API's first 10-second window - it answers 429
    and the whole run stalls. So it starts empty and holds only a couple of
    tokens, making the output a steady drip at exactly limit/period.
    """

    def __init__(self, limit, period, burst=2):
        self.capacity = float(max(1, burst))
        self.tokens = 0.0
        self.rate = float(limit) / float(period)
        self.stamp = time.monotonic()
        self.cooldown_until = 0.0
        self.lock = threading.Lock()

    def penalise(self, seconds):
        """One thread hit a 429, so every thread stands down. A 429 is itself a
        request as far as the API's counter is concerned, so letting the other
        five threads carry on would keep the rate pinned above the ceiling and
        the run would never recover."""
        with self.lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + seconds)
            self.tokens = 0.0

    def take(self):
        while not stop.is_set():
            with self.lock:
                now = time.monotonic()
                if now < self.cooldown_until:
                    wait = self.cooldown_until - now
                else:
                    self.tokens = min(self.capacity,
                                      self.tokens + (now - self.stamp) * self.rate)
                    self.stamp = now
                    if self.tokens >= 1:
                        self.tokens -= 1
                        return True
                    wait = (1 - self.tokens) / self.rate
            stop.wait(min(wait, 1.0))
        return False


RATE_LIMITED = "rate-limited"


def check(session, key, email, timeout):
    """One API call. Returns ((code, message), "") on success, (None, reason)
    on failure, or (RATE_LIMITED, text) when the API tells us to slow down."""
    try:
        r = session.get(API, params={"email": email, "key": key}, timeout=timeout)
    except Exception as e:
        return None, "request failed: %s" % e
    # Rate limiting arrives as HTTP 429 with a plain-text body, not JSON.
    if r.status_code == 429:
        return RATE_LIMITED, r.text[:200]
    if r.status_code != 200:
        return None, "http %d: %s" % (r.status_code, r.text[:120])
    try:
        d = r.json()
    except Exception:
        return None, "not json: %s" % r.text[:120]
    return (d.get("code", "") or "", d.get("message", "") or ""), ""


def worker(name, conf, key, bucket, counter):
    session = requests.Session()
    con = store.connect()
    transient = set(conf["transient"])

    while not stop.is_set():
        # daily cap
        if store.used_today(con) >= conf["daily_cap"]:
            stop.wait(60)
            continue

        batch = store.claim_batch(con, 1)
        if not batch:
            stop.wait(5)
            continue
        row_id, email = batch[0]

        if not bucket.take():
            break

        result, err = check(session, key, email, conf["http_timeout"])

        if result is RATE_LIMITED:
            # Not this address's fault - put it straight back, no attempt spent,
            # and ease off for a moment so the window can refill.
            store.release(con, row_id)
            con.commit()
            bucket.penalise(conf.get("cooldown", 20))
            log("%s  rate limited - all threads standing down: %s" % (name, err))
            continue

        if result is None:
            log("%s  %s  RETRY  %s" % (name, email, err))
            store.defer(con, row_id, conf["retry_delay"], conf["max_attempts"])
            con.commit()
            continue

        code, message = result
        store.bump_usage(con)

        if message in transient:
            store.defer(con, row_id, conf["retry_delay"], conf["max_attempts"])
            con.commit()
            continue

        store.save_result(con, row_id, email, code, message)
        con.commit()
        with counter["lock"]:
            counter["n"] += 1


def main():
    store.init()
    conf = load_conf()
    key = load_key()

    log("worker starting - %d req / %.0f s, %d threads, daily cap %d"
        % (conf["rate_limit"], conf["rate_period"], conf["workers"], conf["daily_cap"]))

    if not key:
        log("NO API KEY at %s - idling. Put the key there and restart." % KEYF)
        while not stop.is_set():
            stop.wait(30)
            key = load_key()
            if key:
                log("key appeared - starting work")
                break
        if not key:
            return

    bucket = Bucket(conf["rate_limit"], conf["rate_period"])
    counter = {"n": 0, "lock": threading.Lock()}

    threads = []
    for i in range(conf["workers"]):
        t = threading.Thread(target=worker, args=("w%d" % i, conf, key, bucket, counter),
                             daemon=True, name="w%d" % i)
        t.start()
        threads.append(t)

    con = store.connect()
    last = 0
    while not stop.is_set():
        stop.wait(30)
        store.refresh_job_status(con)
        with counter["lock"]:
            n = counter["n"]
        if n != last:
            pend = con.execute("SELECT COUNT(*) c FROM emails WHERE state='pending'").fetchone()["c"]
            log("checked %d this run, %d used today, %d still pending"
                % (n, store.used_today(con), pend))
            last = n

    for t in threads:
        t.join(timeout=5)
    log("worker stopped")


def bye(signum, frame):
    log("signal %d - finishing current work" % signum)
    stop.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    main()
