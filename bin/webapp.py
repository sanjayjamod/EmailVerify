#!/usr/bin/env python3
"""
Web front end for the email verifier - email.sanjayjamod.cloud

Binds to the traefik_default gateway only (172.16.5.1), same as emails-api,
so nothing is exposed to the internet except through Traefik, which adds TLS
and the password prompt.

    /                     dashboard: single check, upload, job list
    /upload               CSV / XLSX / TXT -> a new job
    /job/<id>             live progress
    /job/<id>/download    email,code,message  (what process_verify.py eats)
    /job/<id>/process     hand it to process_verify.py -> MailWizz-ready
    /job/<id>/mailwizz    the accepted-only CSV that MailWizz eats
    /job/<id>/mailwizz.zip  every folder process_verify.py wrote, zipped
"""
import csv, datetime, io, os, re, json, subprocess, sys, zipfile
from flask import (Flask, request, render_template, redirect, url_for,
                   jsonify, send_file, flash, Response)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

BASE = os.environ.get("EMAILS_BASE", "/opt/emails")
PYBIN = BASE + "/venv/bin/python"
UPLOADS = BASE + "/uploads"
RESULTS = BASE + "/results"
API = "https://happy.mailtester.ninja/ninja"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
SAFE = re.compile(r"^[A-Za-z0-9._ -]+$")

app = Flask(__name__, template_folder=BASE + "/web/templates")
app.secret_key = os.urandom(24)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024      # 512 MB uploads


# ---------------------------------------------------------------- helpers

def extract_emails(filename, raw):
    """Pull addresses out of CSV, TXT or XLSX. Order kept, duplicates dropped."""
    found, seen = [], set()

    def add(text):
        for m in EMAIL_RE.findall(text or ""):
            e = m.strip().lower()
            if e not in seen:
                seen.add(e)
                found.append(e)

    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell:
                        add(str(cell))
        wb.close()
    else:
        add(raw.decode("utf-8-sig", errors="replace"))
    return found


def clients():
    d = BASE + "/clients"
    if not os.path.isdir(d):
        return []
    return sorted(x for x in os.listdir(d) if os.path.isdir(d + "/" + x))


def conf():
    p = BASE + "/verify.conf.json"
    try:
        return json.load(open(p))
    except Exception:
        return {}


def api_key():
    """The real key, or '' while the file still holds the placeholder."""
    p = BASE + "/mailtester.key"
    if not os.path.isfile(p):
        return ""
    k = open(p).read().strip()
    return "" if k.startswith("PUT-YOUR") else k


# ---------------------------------------------------------------- pages

@app.route("/health")
def health():
    return jsonify(ok=True)


@app.route("/")
def index():
    con = store.connect()
    jobs = con.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
    rows = []
    for j in jobs:
        c = store.job_counts(con, j["id"])
        t = store.job_timing(con, j["id"])
        speed, elapsed = measured_speed(t, c["done"])
        c.update(id=j["id"], name=j["name"], client=j["client"],
                 created_at=j["created_at"], status=j["status"],
                 elapsed=elapsed, speed=round(speed, 2) if speed else 0)
        c["pct"] = round(100.0 * (c["done"] + c["failed"]) / c["total"], 1) if c["total"] else 0
        rows.append(c)
    cfg = conf()
    rate = cfg.get("rate_limit", 11), cfg.get("rate_period", 10)
    return render_template("index.html", jobs=rows, clients=clients(),
                           used=store.used_today(con),
                           cap=cfg.get("daily_cap", 0), rate=rate,
                           has_key=bool(api_key()))


@app.route("/check", methods=["POST"])
def check_one():
    """Single address. Answers from cache when we already know it."""
    import requests
    email = (request.form.get("email") or "").strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return jsonify(ok=False, error="that does not look like an email address")

    con = store.connect()
    hit = con.execute("SELECT * FROM cache WHERE email=?", (email,)).fetchone()
    if hit:
        return jsonify(ok=True, cached=True, email=email,
                       code=hit["code"], message=hit["message"],
                       checked_at=hit["checked_at"])

    key = api_key()
    if not key:
        return jsonify(ok=False, error="no API key on the server yet")

    try:
        r = requests.get(API, params={"email": email, "key": key}, timeout=30)
    except Exception as e:
        return jsonify(ok=False, error="API call failed: %s" % e)

    # Rate limiting comes back as plain text, not JSON.
    if r.status_code == 429:
        return jsonify(ok=False, error="Rate limited - the worker is using the "
                                       "quota right now. Try again in a moment.")
    try:
        d = r.json()
    except Exception:
        return jsonify(ok=False, error="API said (http %d): %s"
                                       % (r.status_code, r.text[:200]))

    code, message = d.get("code", "") or "", d.get("message", "") or ""
    if code not in store.VERDICTS:
        return jsonify(ok=False, error="API said %s / %s - not a verdict, nothing saved"
                                       % (code, message))
    store.bump_usage(con)
    # Only final answers are cached. A Timeout or SPAM Block kept here would be
    # handed back for that address on every later upload, never re-checked.
    transient = {m.lower() for m in conf().get("transient", store.TRANSIENT)}
    if message.lower() not in transient:
        con.execute("INSERT INTO cache (email, code, message, checked_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(email) DO UPDATE SET code=excluded.code,"
                    " message=excluded.message, checked_at=excluded.checked_at",
                    (email, code, message, store.now_iso()))
    con.commit()
    return jsonify(ok=True, cached=False, email=email, code=code, message=message)


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    client = (request.form.get("client") or "").strip()
    if not f or not f.filename:
        flash("no file chosen")
        return redirect(url_for("index"))
    if client and not SAFE.match(client):
        flash("bad client name")
        return redirect(url_for("index"))

    raw = f.read()
    emails = extract_emails(f.filename, raw)
    if not emails:
        flash("no email addresses found in %s" % f.filename)
        return redirect(url_for("index"))

    os.makedirs(UPLOADS, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", f.filename)[:80]
    open(os.path.join(UPLOADS, name), "wb").write(raw)

    con = store.connect()
    job_id, queued, cached = store.create_job(con, name, client, emails)
    flash("%s: %d addresses - %d queued, %d already known"
          % (name, len(emails), queued, cached))
    return redirect(url_for("job", job_id=job_id))


def job_stem(name):
    """The folder name process_verify.py derives from the uploaded file."""
    stem = re.sub(r"\.(csv|xlsx|txt)$", "", name or "", flags=re.I)
    return re.sub(r"\.csv$", "", stem, flags=re.I)


def output_dir(client, name):
    """The MailWizz-ready folder for this job, or None if it was never made."""
    if not client or not SAFE.match(client):
        return None
    d = "%s/output/%s/%s" % (BASE, client, job_stem(name))
    return d if os.path.isdir(d) else None


def upload_csv(out):
    """The '1 - UPLOAD to MailWizz' CSV inside an output folder."""
    up = os.path.join(out, "1 - UPLOAD to MailWizz")
    if not os.path.isdir(up):
        return None
    hits = sorted(f for f in os.listdir(up) if f.lower().endswith(".csv"))
    return os.path.join(up, hits[0]) if hits else None


@app.route("/job/<int:job_id>")
def job(job_id):
    con = store.connect()
    j = con.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        return "no such job", 404
    breakdown = con.execute(
        "SELECT code, message, COUNT(*) n FROM emails WHERE job_id=? AND state='done'"
        " GROUP BY code, message ORDER BY n DESC", (job_id,)).fetchall()
    cfg = conf()
    out = output_dir(j["client"], j["name"])
    up = upload_csv(out) if out else None
    return render_template("job.html", job=j, counts=store.job_counts(con, job_id),
                           breakdown=breakdown, clients=clients(),
                           recent=store.recent_checks(con, job_id, 12),
                           connections=cfg.get("workers", 2),
                           daily_cap=cfg.get("daily_cap", 0),
                           has_output=bool(out),
                           upload_name=os.path.basename(up) if up else None)


def span_seconds(first, last):
    try:
        return (datetime.datetime.fromisoformat(last)
                - datetime.datetime.fromisoformat(first)).total_seconds()
    except (ValueError, TypeError):
        return 0


def measured_speed(timing, done):
    """Total elapsed for this job, and its overall average speed."""
    if not (timing["first"] and timing["last"] and done > 1):
        return None, 0
    elapsed = span_seconds(timing["first"], timing["last"])
    if elapsed <= 0:
        return None, 0
    return done / elapsed, int(elapsed)


def live_speed(con, job_id):
    """Checks per second right now, measured over the last couple of hundred
    answers. This is the number worth showing - the whole-job average drags a
    bad patch along for ever."""
    first, last, n = store.recent_span(con, job_id, 200)
    if n < 2:
        return None
    elapsed = span_seconds(first, last)
    return (n / elapsed) if elapsed > 0 else None


@app.route("/api/job/<int:job_id>")
def job_api(job_id):
    con = store.connect()
    c = store.job_counts(con, job_id)
    j = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    cfg = conf()
    configured = float(cfg.get("rate_limit", 11)) / float(cfg.get("rate_period", 10))

    timing = store.job_timing(con, job_id)
    avg, elapsed = measured_speed(timing, c["done"])
    now_speed = live_speed(con, job_id)
    rate = now_speed or avg or configured

    c["status"] = j["status"] if j else "?"
    c["pct"] = round(100.0 * (c["done"] + c["failed"]) / c["total"], 1) if c["total"] else 0
    c["eta_min"] = round(c["pending"] / rate / 60) if rate else None
    c["elapsed_sec"] = elapsed
    c["speed"] = round(rate, 2)
    c["avg_speed"] = round(avg, 2) if avg else 0
    c["used_today"] = store.used_today(con)
    c["daily_cap"] = cfg.get("daily_cap", 0)
    c["connections"] = cfg.get("workers", 2)
    c["recent"] = store.recent_checks(con, job_id, 12)
    c["in_flight"] = store.in_flight(con, job_id, 3)
    return jsonify(c)


@app.route("/job/<int:job_id>/pause", methods=["POST"])
def pause(job_id):
    con = store.connect()
    j = con.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    new = "queued" if j and j["status"] == "paused" else "paused"
    con.execute("UPDATE jobs SET status=? WHERE id=?", (new, job_id))
    con.commit()
    return redirect(url_for("job", job_id=job_id))


@app.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    """Throw the whole job away — its rows and the job itself. The cached
    verdicts in `cache` are deliberately left alone, so re-uploading the same
    addresses later still costs no credit."""
    con = store.connect()
    j = con.execute("SELECT name FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        flash("job %d is already gone" % job_id)
        return redirect(url_for("index"))
    n = con.execute("DELETE FROM emails WHERE job_id=?", (job_id,)).rowcount
    con.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    con.commit()
    flash("deleted %s — %d address%s removed from the queue"
          % (j["name"], n, "" if n == 1 else "es"))
    return redirect(url_for("index"))


@app.route("/job/<int:job_id>/retry-failed", methods=["POST"])
def retry_failed(job_id):
    """Put the failed rows back in the queue. A row only fails after the
    transport gave up three times - a slow MX, not a verdict - so it deserves
    another go rather than being written off for good."""
    con = store.connect()
    n = con.execute(
        "UPDATE emails SET state='pending', attempts=0, next_try=0"
        " WHERE job_id=? AND state='failed'", (job_id,)).rowcount
    con.execute("UPDATE jobs SET status='queued' WHERE id=? AND status='done'", (job_id,))
    con.commit()
    flash("%d failed address%s put back in the queue" % (n, "" if n == 1 else "es"))
    return redirect(url_for("job", job_id=job_id))


@app.route("/job/<int:job_id>/download")
def download(job_id):
    """email,code,message - exactly the shape process_verify.py reads.
    Failed rows are in it too, so none of the job goes missing."""
    con = store.connect()
    j = con.execute("SELECT name FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        return "no such job", 404
    rows = con.execute(
        "SELECT email, code, message FROM emails WHERE job_id=?"
        " AND state IN ('done','failed') ORDER BY id", (job_id,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow([r["email"], r["code"], r["message"]])
    data = buf.getvalue().encode()
    fname = re.sub(r"\.(csv|xlsx|txt)$", "", j["name"], flags=re.I) + "-verified.csv"
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % fname})


@app.route("/job/<int:job_id>/mailwizz")
def mailwizz_csv(job_id):
    """The accepted-addresses CSV, straight to the browser - the one file that
    actually gets uploaded into MailWizz."""
    con = store.connect()
    j = con.execute("SELECT name, client FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        return "no such job", 404
    out = output_dir(j["client"], j["name"])
    if not out:
        flash("not MailWizz-ready yet - press 'Make MailWizz-ready' first")
        return redirect(url_for("job", job_id=job_id))
    path = upload_csv(out)
    if not path:
        flash("no upload CSV in %s" % out)
        return redirect(url_for("job", job_id=job_id))
    return send_file(path, mimetype="text/csv", as_attachment=True,
                     download_name=os.path.basename(path))


@app.route("/job/<int:job_id>/mailwizz.zip")
def mailwizz_zip(job_id):
    """Every folder process_verify.py wrote for this job, zipped."""
    con = store.connect()
    j = con.execute("SELECT name, client FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not j:
        return "no such job", 404
    out = output_dir(j["client"], j["name"])
    if not out:
        flash("not MailWizz-ready yet - press 'Make MailWizz-ready' first")
        return redirect(url_for("job", job_id=job_id))

    buf = io.BytesIO()
    stem = job_stem(j["name"])
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(out):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.join(stem, os.path.relpath(full, out)))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="%s-mailwizz.zip" % stem)


@app.route("/job/<int:job_id>/process", methods=["POST"])
def process(job_id):
    """Write the result CSV to disk, then let process_verify.py split it into
    the MailWizz-ready folders using that client's contact index."""
    client = (request.form.get("client") or "").strip()
    if not client or not SAFE.match(client):
        flash("pick a client first")
        return redirect(url_for("job", job_id=job_id))
    if not os.path.isfile("%s/clients/%s/contacts.db" % (BASE, client)):
        flash("no contact index for %s - run build_index.py first" % client)
        return redirect(url_for("job", job_id=job_id))

    con = store.connect()
    j = con.execute("SELECT name FROM jobs WHERE id=?", (job_id,)).fetchone()
    # Failed rows go along too - process_verify.py files them under "needs
    # re-verify". Left out, they vanished from every folder of the output.
    rows = con.execute(
        "SELECT email, code, message FROM emails WHERE job_id=?"
        " AND state IN ('done','failed') ORDER BY id", (job_id,)).fetchall()
    if not rows:
        flash("nothing verified yet")
        return redirect(url_for("job", job_id=job_id))

    os.makedirs(RESULTS, exist_ok=True)
    stem = re.sub(r"\.(csv|xlsx|txt)$", "", j["name"], flags=re.I)
    path = "%s/%s.csv" % (RESULTS, stem)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        for r in rows:
            w.writerow([r["email"], r["code"], r["message"]])

    p = subprocess.run([PYBIN, BASE + "/bin/process_verify.py", path, client],
                       capture_output=True, text=True, timeout=1800, check=False)
    if p.returncode != 0:
        flash("process_verify failed: %s" % (p.stderr or p.stdout)[-400:])
    else:
        flash("processed into /opt/emails/output/%s/%s" % (client, stem))
    return redirect(url_for("job", job_id=job_id))


# ---------------------------------------------------------------- settings

PROBE = "postmaster@gmail.com"

# Published MailTester Ninja tiers, checks per 10 seconds: Pro, then Ultimate.
# A key bought with extra rate lands above both and is used as reported.
PLAN_TIERS = [11, 57]

def mask(key):
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def restart_worker():
    try:
        p = subprocess.run(["systemctl", "restart", "email-verify-worker"],
                           capture_output=True, text=True, timeout=60, check=False)
        return p.returncode == 0, (p.stderr or p.stdout).strip()
    except Exception as e:
        return False, str(e)


@app.route("/settings")
def settings():
    cfg = conf()
    key = api_key()
    per_day = 0
    if cfg.get("rate_limit") and cfg.get("rate_period"):
        per_day = int(cfg["rate_limit"] / cfg["rate_period"] * 86400)
    return render_template("settings.html", key_set=bool(key),
                           key_masked=mask(key) if key else "",
                           cfg=cfg, per_day=per_day)


@app.route("/settings/key", methods=["POST"])
def settings_key():
    """Try the key against the live API before writing it. A typo must never
    end up on disk with the worker spinning against a dead key."""
    import requests
    key = (request.form.get("key") or "").strip()
    if not key:
        flash("no key entered")
        return redirect(url_for("settings"))

    try:
        r = requests.get(API, params={"email": PROBE, "key": key}, timeout=30)
        raw = r.text[:500]
        d = r.json()
    except Exception as e:
        app.logger.warning("key test: no usable answer: %s", e)
        flash("key NOT saved - the API did not answer properly: %s" % e)
        return redirect(url_for("settings"))

    app.logger.warning("key test -> http %d %s", r.status_code, raw)

    # A bad key still comes back HTTP 200 with a "code" field, so status alone
    # proves nothing. The one unambiguous rejection is the Invalid Key verdict.
    if d.get("message") == "Invalid Key" or d.get("code") in ("--", "", None):
        flash("key NOT saved - the API rejected it. It replied: %s" % raw)
        return redirect(url_for("settings"))

    path = BASE + "/mailtester.key"
    with open(path, "w") as fh:
        fh.write(key)
    os.chmod(path, 0o600)

    note = ""
    # The key states its own ceilings, but the two fields mean different things:
    #   "limit" = the daily quota                    (100,000 on Pro)
    #   "rate"  = capacity left in the current window, NOT the plan ceiling.
    #             It drops as calls are made (observed 11 -> 7 -> 5 in a row),
    #             so taking it at face value would peg the worker at whatever
    #             happened to be left over at that second.
    # Snapping up to the nearest published tier gives the real ceiling.
    try:
        rate = int(d.get("rate") or 0)
    except (TypeError, ValueError):
        rate = 0
    try:
        daily = int(d.get("limit") or 0)
    except (TypeError, ValueError):
        daily = 0

    if daily > 0:
        # The daily quota IS the rate limit: the API's own 429 text spells out
        # "max rate per 10 secs ... is 11.574074074074" for a 100,000 key, and
        # 100000 / 8640 ten-second windows = 11.574. So derive it, and floor it
        # so we always sit just under the ceiling rather than on it.
        ceiling = int(daily / 8640.0)
        cfg = conf()
        cfg["rate_limit"] = max(1, ceiling)
        cfg["rate_period"] = 10.0
        cfg["daily_cap"] = daily
        cfg["reported_rate"] = rate
        cfg["reported_daily"] = daily
        cfg["key_saved_at"] = store.now_iso()
        cpath = BASE + "/verify.conf.json"
        with open(cpath, "w") as fh:
            json.dump(cfg, fh, indent=2)
        os.chmod(cpath, 0o600)
        note = (" Your key's daily quota is %s, which works out to %d checks"
                " every 10s - the worker was set to that."
                % ("{:,}".format(daily), cfg["rate_limit"]))

    ok, err = restart_worker()
    flash("Key accepted - the test answered %s / %s - and saved.%s %s"
          % (d.get("code"), d.get("message"), note,
             "Worker restarted." if ok else "Worker restart FAILED: " + err))
    return redirect(url_for("settings"))


if __name__ == "__main__":
    store.init()
    host = sys.argv[1] if len(sys.argv) > 1 else "172.16.5.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8901
    app.run(host=host, port=port, threaded=True)
