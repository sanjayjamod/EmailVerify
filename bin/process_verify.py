#!/opt/tpl/venv/bin/python
"""
Turn an email-verifier result file into a MailWizz-ready list.

    process_verify.py <verify.csv> <client>

Names and company detail are looked up in that client's index
(/opt/emails/clients/<client>/contacts.db), built by build_index.py.

Writes into /opt/emails/output/<client>/<job>/:
    1 - UPLOAD to MailWizz/<job> (N accepted).csv
    2 - not used (baad ke liye)/bad - do not send (N).csv
    2 - not used (baad ke liye)/unknown - risky (N).csv
    2 - not used (baad ke liye)/needs re-verify (N).csv
    summary.json      machine readable, for n8n
    README.md

Prints the summary as JSON on the last line so a workflow can parse it.
"""
import csv, os, sys, json, sqlite3, collections, datetime

if len(sys.argv) < 3:
    sys.exit("usage: process_verify.py <verify.csv> <client>")
SRC    = sys.argv[1]
CLIENT = sys.argv[2]
ROOT   = os.environ.get("EMAILS_BASE", "/opt/emails")
DB     = "%s/clients/%s/contacts.db" % (ROOT, CLIENT)
OUT    = ROOT + "/output/" + CLIENT

if not os.path.isfile(SRC): sys.exit("no such file: " + SRC)
if not os.path.isfile(DB):
    sys.exit("no index for client '%s' — run: build_index.py %s" % (CLIENT, CLIENT))

name = os.path.basename(SRC)
for suffix in (".csv.csv", ".csv"):
    if name.endswith(suffix): name = name[: -len(suffix)]; break
job = OUT + "/" + name
up  = job + "/1 - UPLOAD to MailWizz"
rest= job + "/2 - not used (baad ke liye)"
os.makedirs(up, exist_ok=True); os.makedirs(rest, exist_ok=True)

COLS = ["EMAIL","GREETING","FNAME","FIRSTNAME","LASTNAME","COMPANY","POSITION",
        "CITY","COUNTRY","COMPANYCOUNTRY","INDUSTRY","HAS_NAME","SOURCE"]

accepted, other, statuses = [], {}, collections.Counter()
seen = set()
for r in csv.reader(open(SRC, encoding="utf-8-sig", errors="replace")):
    if len(r) < 3: continue
    e, code, st = r[0].strip().lower(), r[1], r[2]
    if not e or e in seen: continue
    seen.add(e)
    statuses[(code, st[:40])] += 1
    if code == "ok" and st == "Accepted": accepted.append(e)
    else: other[e] = (code, st)

# look the accepted ones up in the index
con = sqlite3.connect(DB)
lookup = {}
CH = 900
for i in range(0, len(accepted), CH):
    chunk = accepted[i:i+CH]
    q = "SELECT * FROM contacts WHERE email IN ({0})".format(",".join("?"*len(chunk)))  # nosec
    cur = con.execute(q, chunk)
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        d = dict(zip(cols, row)); lookup[d["email"]] = d
con.close()

rows = []
for e in accepted:
    d  = lookup.get(e, {})
    fn = (d.get("FNAME") or "").strip()
    rows.append({
        "EMAIL": e,
        "GREETING": fn if fn else "there",
        "FNAME": fn,
        "FIRSTNAME": d.get("FIRSTNAME",""), "LASTNAME": d.get("LASTNAME",""),
        "COMPANY": d.get("COMPANY",""),     "POSITION": d.get("POSITION",""),
        "CITY": d.get("CITY",""),           "COUNTRY": d.get("COUNTRY",""),
        "COMPANYCOUNTRY": d.get("COMPANYCOUNTRY",""), "INDUSTRY": d.get("INDUSTRY",""),
        "HAS_NAME": "yes" if fn else "no",
        "SOURCE": d.get("SOURCE","(not matched)"),
    })
rows.sort(key=lambda r: (r["HAS_NAME"] != "yes", r["EMAIL"]))   # named first

upload_file = f"{up}/{name} ({len(rows)} accepted).csv"
with open(upload_file, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=COLS); w.writeheader(); w.writerows(rows)

# No final verdict yet: the API timed out, was blocked, hit a greylist or an MX
# it could not reach, or never answered at all (blank - a failed row). About a
# third of these come good on a later run, most of them as Accepted.
RETRY = ("", "timeout", "mx error", "spam block", "greylisted",
         "disabled key", "invalid key")

buckets = collections.defaultdict(list)
for e, (code, st) in other.items():
    if st.lower() in RETRY or "Too Many Requests" in st: buckets["needs re-verify"].append((e, st[:60] or "(no answer)"))
    elif code == "ko": buckets["bad - do not send"].append((e, st))
    else: buckets["unknown - risky"].append((e, st or "(blank)"))
bucket_counts = {}
for b, items in buckets.items():
    bucket_counts[b] = len(items)
    with open(f"{rest}/{b} ({len(items)}).csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh); w.writerow(["EMAIL","VERIFY_STATUS"]); w.writerows(sorted(set(items)))

named   = sum(1 for r in rows if r["HAS_NAME"] == "yes")
checked = len(seen)
by_src  = collections.Counter(r["SOURCE"] for r in rows)

summary = {
    "job": name,
    "client": CLIENT,
    "checked": checked,
    "accepted": len(rows),
    "accept_rate": round(len(rows) * 100 / max(checked, 1), 1),
    "with_name": named,
    "without_name": len(rows) - named,
    "buckets": bucket_counts,
    "upload_file": upload_file,
    "top_sources": by_src.most_common(6),
    "built": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
}
json.dump(summary, open(f"{job}/summary.json", "w"), indent=2)

with open(f"{job}/README.md", "w") as fh:
    fh.write(f"""# {name} — ready for MailWizz

Built {summary['built']} from `{os.path.basename(SRC)}` for client **{CLIENT}**.

## Upload this

`1 - UPLOAD to MailWizz/{os.path.basename(upload_file)}` — **{len(rows):,} rows**

Only `ok / Accepted`. Acceptance rate **{summary['accept_rate']}%** of {checked:,} checked.

## Verifier results

| Result | Count |
|---|---|
""")
    for (code, st), n in statuses.most_common(12):
        fh.write(f"| `{code}` {st} | {n:,} |\n")
    fh.write(f"""
## Names

| | |
|---|---|
| Have a real name | **{named:,}** |
| No name | **{len(rows)-named:,}** |

`GREETING` is never empty — a real name, or `there`.
**Use `[GREETING]` in the template, not `[FNAME]`**, or segment on `HAS_NAME`.
Named rows are sorted to the top.

## Where the accepted came from

| Source | Accepted |
|---|---|
""")
    for s, n in by_src.most_common(10): fh.write(f"| {s} | {n:,} |\n")
    fh.write(f"""
## Not used

""")
    for b, n in sorted(bucket_counts.items()): fh.write(f"- `{b}` — {n:,}\n")
    fh.write("""
`bad` is permanently dead. `unknown - risky` is catch-all — only in small watched
batches. `needs re-verify` never got a final verdict (timeout, spam block, MX
error, greylisted, no answer); run those again.

## Columns

`EMAIL`, `GREETING`, `FNAME`, `FIRSTNAME`, `LASTNAME`, `COMPANY`, `POSITION`,
`CITY`, `COUNTRY`, `COMPANYCOUNTRY`, `INDUSTRY`, `HAS_NAME`, `SOURCE`

Identical across every part, so they can share one MailWizz list.
""")

print(json.dumps(summary))
