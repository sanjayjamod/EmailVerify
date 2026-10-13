#!/usr/bin/env python3
"""Queue a CSV of emails as a verifier job - same effect as the web /upload,
without going through the browser. Usage: ingest.py <client> <file.csv> [...]"""
import os, re, sys, shutil
sys.path.insert(0, "/opt/emails/bin")
import store

BASE = os.environ.get("EMAILS_BASE", "/opt/emails")
UPLOADS = BASE + "/uploads"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

client = sys.argv[1]
con = store.connect()
os.makedirs(UPLOADS, exist_ok=True)

for path in sys.argv[2:]:
    raw = open(path, "rb").read().decode("utf-8", "ignore")
    seen, emails = set(), []
    for m in EMAIL_RE.findall(raw):
        e = m.lower()
        if e not in seen:
            seen.add(e)
            emails.append(e)
    if not emails:
        print("SKIP  %s  (no emails)" % path)
        continue
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path))[:80]
    shutil.copyfile(path, os.path.join(UPLOADS, name))
    job_id, queued, cached = store.create_job(con, name, client, emails)
    print("JOB %-4s %-28s total=%-7d queued=%-7d cached=%d"
          % (job_id, name, len(emails), queued, cached))
