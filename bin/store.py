#!/usr/bin/env python3
"""
Shared SQLite layer for the email-verify tool.

One file, /opt/emails/verify.db, used by both the worker and the web app.
WAL mode so the two processes can read and write at the same time.

Tables
    jobs            one row per uploaded list
    emails          one row per address inside a job
    cache           every address ever verified, so a repeat costs no credit
    usage           per-day counter, to enforce the daily credit cap
"""
import os, sqlite3, time, datetime

BASE = os.environ.get("EMAILS_BASE", "/opt/emails")
DB = BASE + "/verify.db"

# The only codes that are a verdict on the address. Anything else - "--" with
# Disabled Key / Invalid Key - is the API talking about our key.
VERDICTS = ("ok", "ko", "mb")

# Answers worth asking again later rather than keeping. Compared without case:
# the API docs spell it "Mx Error" but the API itself sends "MX Error".
TRANSIENT = ("Timeout", "MX Error", "SPAM Block", "Greylisted")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    client     TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    total      INTEGER DEFAULT 0,
    status     TEXT DEFAULT 'queued'      -- queued | running | done | paused
);

CREATE TABLE IF NOT EXISTS emails (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     INTEGER NOT NULL,
    email      TEXT NOT NULL,
    state      TEXT DEFAULT 'pending',    -- pending | done | failed
    code       TEXT DEFAULT '',           -- ok | ko | mb
    message    TEXT DEFAULT '',
    attempts   INTEGER DEFAULT 0,
    next_try   REAL DEFAULT 0,
    checked_at TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS emails_job_addr ON emails(job_id, email);
CREATE INDEX IF NOT EXISTS emails_ready ON emails(state, next_try);

CREATE TABLE IF NOT EXISTS cache (
    email      TEXT PRIMARY KEY,
    code       TEXT,
    message    TEXT,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS usage (
    day   TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0
);
"""


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init():
    con = connect()
    con.executescript(SCHEMA)
    con.commit()
    con.close()


def today():
    return datetime.date.today().isoformat()


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- jobs

def create_job(con, name, client, emails):
    """Insert a job plus its addresses. Cached results are filled in at once
    so they never reach the API. Returns (job_id, queued, from_cache)."""
    cur = con.execute(
        "INSERT INTO jobs (name, client, created_at, total) VALUES (?,?,?,?)",
        (name, client, now_iso(), len(emails)),
    )
    job_id = cur.lastrowid

    cached = 0
    rows = []

    # Bolt: Batch cache lookups to prevent N+1 query problem during job creation
    cache_lookup = {}
    CHUNK_SIZE = 900
    for i in range(0, len(emails), CHUNK_SIZE):
        chunk = emails[i:i+CHUNK_SIZE]
        q = "SELECT email, code, message, checked_at FROM cache WHERE email IN (%s)" % ",".join("?"*len(chunk))
        cur = con.execute(q, chunk)
        for hit in cur.fetchall():
            cache_lookup[hit["email"]] = hit

    for e in emails:
        hit = cache_lookup.get(e)
        if hit:
            rows.append((job_id, e, "done", hit["code"], hit["message"], hit["checked_at"]))
            cached += 1
        else:
            rows.append((job_id, e, "pending", "", "", ""))

    con.executemany(
        "INSERT OR IGNORE INTO emails (job_id, email, state, code, message, checked_at)"
        " VALUES (?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    return job_id, len(emails) - cached, cached


def job_counts(con, job_id):
    """The same three buckets the official MailTester app shows:
    accepted, rejected, and everything else (catch-all / risky)."""
    r = con.execute(
        "SELECT COUNT(*) total,"
        " SUM(state='pending') pending,"
        " SUM(state='done') done,"
        " SUM(state='failed') failed,"
        " SUM(state='done' AND code='ok' AND message='Accepted') accepted,"
        " SUM(state='done' AND code='ko') rejected,"
        " SUM(state='done' AND code!='ko'"
        "     AND NOT (code='ok' AND message='Accepted')) unknown"
        " FROM emails WHERE job_id=?",
        (job_id,),
    ).fetchone()
    return {k: (r[k] or 0) for k in
            ("total", "pending", "done", "failed", "accepted", "rejected", "unknown")}


def job_timing(con, job_id):
    """When this job's first and last check landed, and the address most
    recently seen - what the console app prints on its live line."""
    r = con.execute(
        "SELECT MIN(checked_at) first, MAX(checked_at) last FROM emails"
        " WHERE job_id=? AND state='done' AND checked_at!=''", (job_id,)
    ).fetchone()
    last_row = con.execute(
        "SELECT email, code, message FROM emails"
        " WHERE job_id=? AND state='done' AND checked_at!=''"
        " ORDER BY checked_at DESC, id DESC LIMIT 1", (job_id,)
    ).fetchone()
    return {
        "first": r["first"] if r else None,
        "last": r["last"] if r else None,
        "last_email": last_row["email"] if last_row else None,
        "last_code": last_row["code"] if last_row else None,
        "last_message": last_row["message"] if last_row else None,
    }


# ---------------------------------------------------------------- worker

def recent_span(con, job_id, n=200):
    """First and last timestamp among the most recent n answers, so speed can
    be measured over what is happening now rather than averaged across the
    whole job - a slow patch hours ago should not make the live figure lie."""
    rows = [r["checked_at"] for r in con.execute(
        "SELECT checked_at FROM emails WHERE job_id=? AND state='done'"
        " AND checked_at!='' ORDER BY checked_at DESC, id DESC LIMIT ?", (job_id, n))]
    if len(rows) < 2:
        return None, None, 0
    return rows[-1], rows[0], len(rows)


def recent_checks(con, job_id, n=12):
    """The last few addresses to come back, newest first - the live feed."""
    return [dict(r) for r in con.execute(
        "SELECT email, code, message, checked_at FROM emails"
        " WHERE job_id=? AND state='done' AND checked_at!=''"
        " ORDER BY checked_at DESC, id DESC LIMIT ?", (job_id, n))]


def in_flight(con, job_id, n=5):
    """Addresses a worker has claimed but not yet answered for - i.e. the ones
    being checked right this second."""
    return [r["email"] for r in con.execute(
        "SELECT email FROM emails WHERE job_id=? AND state='pending'"
        " AND next_try > ? ORDER BY next_try LIMIT ?",
        (job_id, time.time(), n))]


def claim_batch(con, limit):
    """Hand back up to `limit` addresses that are ready to be checked.
    Marks them so a second worker cannot pick up the same rows.

    The pick and the mark share one write transaction. Run apart, two threads
    could both read a row before either marked it, and that address was
    checked - and paid for - twice."""
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        now = time.time()
        rows = con.execute(
            "SELECT e.id, e.email FROM emails e"
            " JOIN jobs j ON j.id = e.job_id"
            " WHERE e.state='pending' AND e.next_try<=? AND j.status!='paused'"
            " ORDER BY e.job_id, e.id LIMIT ?",
            (now, limit),
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            con.execute(
                "UPDATE emails SET next_try=? WHERE id IN (%s)" % ",".join("?" * len(ids)),
                [now + 900] + ids,      # parked for 15 min in case we die mid-flight
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return [(r["id"], r["email"]) for r in rows]


def save_result(con, row_id, email, code, message):
    ts = now_iso()
    con.execute(
        "UPDATE emails SET state='done', code=?, message=?, checked_at=?, next_try=0"
        " WHERE id=?",
        (code, message, ts, row_id),
    )
    con.execute(
        "INSERT INTO cache (email, code, message, checked_at) VALUES (?,?,?,?)"
        " ON CONFLICT(email) DO UPDATE SET code=excluded.code,"
        " message=excluded.message, checked_at=excluded.checked_at",
        (email, code, message, ts),
    )


def defer(con, row_id, delay, max_attempts, code=None, message=None):
    """A transient failure. Push it back unless it has run out of tries.
    The last answer (Timeout, SPAM Block ...) stays on the row, so one that
    runs out of tries still says why. It never goes in the cache."""
    con.execute(
        "UPDATE emails SET attempts=attempts+1,"
        " code = COALESCE(?, code), message = COALESCE(?, message),"
        " next_try = CASE WHEN attempts+1 >= ? THEN 0 ELSE ? END,"
        " state = CASE WHEN attempts+1 >= ? THEN 'failed' ELSE 'pending' END"
        " WHERE id=?",
        (code, message, max_attempts, time.time() + delay, max_attempts, row_id),
    )


def release(con, row_id):
    """Put a row straight back in the queue without counting an attempt.
    Used when the refusal was ours (rate limit), not the address's fault."""
    con.execute("UPDATE emails SET next_try=0 WHERE id=?", (row_id,))


def bump_usage(con, n=1):
    con.execute(
        "INSERT INTO usage (day, count) VALUES (?,?)"
        " ON CONFLICT(day) DO UPDATE SET count = count + ?",
        (today(), n, n),
    )


def used_today(con):
    r = con.execute("SELECT count FROM usage WHERE day=?", (today(),)).fetchone()
    return r["count"] if r else 0


def refresh_job_status(con):
    """Any running job with nothing left to do becomes 'done'."""
    con.execute(
        "UPDATE jobs SET status='done' WHERE status IN ('queued','running')"
        " AND id NOT IN (SELECT DISTINCT job_id FROM emails WHERE state='pending')"
    )
    con.execute(
        "UPDATE jobs SET status='running' WHERE status='queued'"
        " AND id IN (SELECT DISTINCT job_id FROM emails WHERE state!='pending')"
    )
    con.commit()


if __name__ == "__main__":
    init()
    print("initialised", DB)
