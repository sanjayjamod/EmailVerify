#!/opt/tpl/venv/bin/python
"""
Scan one client's source lists and build a lookup database of contact detail.

    build_index.py <client>

Reads  /opt/emails/clients/<client>/sources/
Writes /opt/emails/clients/<client>/contacts.db

Run it whenever new source lists are dropped in for that client.
"""

import openpyxl, csv, os, re, sys, glob, sqlite3, unicodedata

if len(sys.argv) < 2:
    sys.exit("usage: build_index.py <client>    e.g.  build_index.py acme")
CLIENT = sys.argv[1]
# Root defaults to the VPS layout; override with EMAILS_BASE to run anywhere
# (e.g. on the Mac when the server is unreachable).
ROOT   = os.environ.get("EMAILS_BASE", "/opt/emails")
BASE = f"{ROOT}/clients/{CLIENT}"
SRC = f"{BASE}/sources"
DB = f"{BASE}/contacts.db"
if not os.path.isdir(SRC):
    sys.exit(f"no sources folder for client '{CLIENT}' — expected {SRC}")

EMAIL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
SMALL = {"de","da","van","von","der","den","del","della","di","bin","binte","al","el","la","le"}

def strip_junk(name):
    """LinkedIn names carry fancy fonts, flags, certifications and job titles."""
    if not name: return ""
    s = unicodedata.normalize("NFKC", str(name))
    s = "".join(c for c in s if unicodedata.category(c) not in ("So","Cf","Sk"))
    s = re.split(r"[,\(\[\|/•·]", s)[0]
    s = re.split(r"\s+[-–—]\s+", s)[0]
    s = re.sub(r"[^\w\s'’.\-]", " ", s, flags=re.UNICODE)
    return " ".join(s.split()[:3])

def proper(name):
    name = strip_junk(name)
    if not name: return ""
    out = []
    for w in name.split(" "):
        if not w: continue
        if not (w.isupper() or w.islower()): out.append(w); continue
        lw = w.lower()
        if lw in SMALL and out: out.append(lw); continue
        def cap(p):
            if not p: return p
            if p.lower().startswith("mc")  and len(p) > 2:
                return f"Mc{p[2:].capitalize()}"
            if p.lower().startswith("mac") and len(p) > 3:
                return f"Mac{p[3:].capitalize()}"
            return p.capitalize()

        for sep in ("-", "'", "’", "."):
            if sep in w: w = sep.join(cap(x) for x in w.split(sep)); break
        else: w = cap(w)
        out.append(w)
    return " ".join(out)

MAP = {
    "full_name":"FNAME","fullname":"FNAME","name":"FNAME","contact name":"FNAME",
    "first_name":"FIRSTNAME","firstname":"FIRSTNAME","first name":"FIRSTNAME",
    "last_name":"LASTNAME","lastname":"LASTNAME","last name":"LASTNAME",
    "company_name":"COMPANY","company":"COMPANY","organization":"COMPANY",
    "current_position":"POSITION","position":"POSITION","title":"POSITION","job title":"POSITION",
    "person_city":"CITY","city":"CITY",
    "person_country":"COUNTRY","country":"COUNTRY",
    "company_country":"COMPANYCOUNTRY",
    "company_industry":"INDUSTRY","person_industry":"INDUSTRY","industry":"INDUSTRY",
    "person_linkedin_url":"LINKEDIN","linkedin":"LINKEDIN",
}
FIELDS = ["FNAME","FIRSTNAME","LASTNAME","COMPANY","POSITION","CITY",
          "COUNTRY","COMPANYCOUNTRY","INDUSTRY","LINKEDIN"]

def clean_email(raw):
    e = raw.strip().strip("'\"`").lstrip("/-_+?.,;:").rstrip(".,;:").lower()
    if "/" in e or "\\" in e or e.count("@") != 1: return None
    loc, dom = e.split("@")
    if not loc or not dom or len(loc) > 64 or len(e) > 254: return None
    if not loc[0].isalnum() or loc[-1] == "." or ".." in loc: return None
    if "." not in dom or ".." in dom: return None
    labels = dom.split(".")
    if any((not l) or l[0]=="-" or l[-1]=="-" or len(l)>63
           or not re.fullmatch(r"[A-Za-z0-9\-]+", l) for l in labels): return None
    return e if len(labels[-1]) >= 2 and labels[-1].isalpha() else None

def cell(v):
    return "" if v is None else " ".join(str(v).replace("_x000D_", " ").split())

con = sqlite3.connect(DB)
con.execute("DROP TABLE IF EXISTS contacts")
fields_sql = ", ".join(f"{f} TEXT" for f in FIELDS)
con.execute(f"CREATE TABLE contacts (email TEXT PRIMARY KEY, {fields_sql}, SOURCE TEXT)")

rows = {}
def take(email, data, src):
    cur = rows.setdefault(email, {"email": email, "SOURCE": src})
    for k, v in data.items():
        if v and not cur.get(k): cur[k] = v

files = sorted(glob.glob(f"{SRC}/*.xlsx")) + sorted(glob.glob(f"{SRC}/*.csv"))
print(f"client: {CLIENT}")
print(f"scanning {len(files)} files in {SRC}\n")

for path in files:
    name = os.path.basename(path)
    if name.startswith("~$"): continue
    n0 = len(rows)
    try:
        if path.endswith(".xlsx"):
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            for ws in wb.worksheets:
                it = ws.iter_rows(values_only=True)
                try: hdr = [str(h).strip().lower() if h else "" for h in next(it)]
                except StopIteration: continue
                cols = {i: MAP[h] for i, h in enumerate(hdr) if h in MAP}
                emailcols = [i for i,h in enumerate(hdr)
                             if "email" in h and "status" not in h and "type" not in h]
                for row in it:
                    if not row: continue
                    base = {cols[i]: cell(row[i]) for i in cols
                            if i < len(row) and row[i] is not None}
                    for i in (emailcols or range(len(row))):
                        if i >= len(row) or row[i] is None: continue
                        s = str(row[i])
                        if "@" not in s: continue
                        for m in EMAIL.findall(s):
                            if e := clean_email(m):
                                take(e, base, name)
            wb.close()
        else:
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                rd = csv.reader(fh)
                try: hdr = [h.strip().lower() for h in next(rd)]
                except StopIteration: continue
                cols = {i: MAP[h] for i, h in enumerate(hdr) if h in MAP}
                for row in rd:
                    base = {cols[i]: cell(row[i]) for i in cols if i < len(row) and row[i]}
                    for c in row:
                        if "@" not in str(c): continue
                        for m in EMAIL.findall(str(c)):
                            if e := clean_email(m):
                                take(e, base, name)
    except Exception as ex:
        print(f"  SKIP {name}: {str(ex)[:60]}")
        continue
    print(f"  {name[:48]:<50} +{len(rows)-n0:>7}")

for r in rows.values():
    fn = proper(r.get("FNAME","")) or " ".join(
        x for x in (proper(r.get("FIRSTNAME","")), proper(r.get("LASTNAME",""))) if x)
    r["FNAME"] = fn
    r["FIRSTNAME"] = proper(r.get("FIRSTNAME",""))
    r["LASTNAME"]  = proper(r.get("LASTNAME",""))

con.executemany(
    f'INSERT OR REPLACE INTO contacts (email, {", ".join(FIELDS)}, SOURCE) VALUES ({", ".join("?" * (len(FIELDS) + 2))})',
    [
        [r["email"]] + [r.get(f, "") for f in FIELDS] + [r.get("SOURCE", "")]
        for r in rows.values()
    ],
)
con.commit()

named = con.execute("SELECT COUNT(*) FROM contacts WHERE FNAME != ''").fetchone()[0]
print(f"\nindexed : {len(rows):,} addresses")
print(f"of those, with a name : {named:,}")
print(f"database : {DB}  ({os.path.getsize(DB)/1048576:.1f} MB)")
con.close()
