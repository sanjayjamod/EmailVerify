#!/opt/tpl/venv/bin/python
"""
A narrow HTTP endpoint so n8n can run the email pipeline without shell access.

    GET  /health                      -> {"ok": true}
    GET  /clients                     -> {"clients": ["acme", ...]}
    POST /process  {"file": "...", "client": "..."}
                                      -> the summary.json from process_verify.py
    GET  /summary?client=&job=        -> that job's summary.json
    POST /reindex  {"client": "..."}  -> rebuild that client's contact index

Binds to the Docker bridge only, so it is not reachable from the internet.
Every request needs  X-Token: <token from /opt/emails/api.token>
"""
import json, os, re, subprocess, sys
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE   = "/opt/emails"
PYBIN  = "/opt/tpl/venv/bin/python"
TOKEN  = open(BASE + "/api.token").read().strip()
SAFE   = re.compile(r"^[A-Za-z0-9._ -]+$")      # no slashes, no traversal

def run(cmd, timeout):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return p.returncode, p.stdout, p.stderr

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        if self.headers.get("X-Token", "") == TOKEN:
            return True
        self.reply(401, {"ok": False, "error": "bad or missing X-Token"})
        return False

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if not self.authed(): return
        if self.path == "/clients":
            d = BASE + "/clients"
            cl = sorted(x for x in os.listdir(d) if os.path.isdir(d + "/" + x)) if os.path.isdir(d) else []
            out = []
            for c in cl:
                db = f"{BASE}/clients/{c}/contacts.db"
                out.append({"client": c, "indexed": os.path.isfile(db),
                            "index_mb": round(os.path.getsize(db)/1048576, 1) if os.path.isfile(db) else 0})
            return self.reply(200, {"ok": True, "clients": out})
        if self.path.startswith("/summary"):
            q = parse_qs(urlparse(self.path).query)
            client = (q.get("client", [""])[0] or "").strip()
            job    = (q.get("job",    [""])[0] or "").strip()
            if not SAFE.match(client) or not SAFE.match(job):
                return self.reply(400, {"ok": False, "error": "missing or unsafe client/job"})
            path = f"{BASE}/output/{client}/{job}/summary.json"
            if not os.path.isfile(path):
                return self.reply(404, {"ok": False, "error": "no summary for %s/%s" % (client, job)})
            try:
                return self.reply(200, {"ok": True, **json.load(open(path))})
            except Exception as e:
                return self.reply(500, {"ok": False, "error": "unreadable summary: %s" % e})

        self.reply(404, {"ok": False, "error": "unknown path"})

    def do_POST(self):
        if not self.authed(): return
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self.reply(400, {"ok": False, "error": "bad JSON: %s" % e})

        client = str(body.get("client", "")).strip()
        if not client or not SAFE.match(client):
            return self.reply(400, {"ok": False, "error": "missing or unsafe 'client'"})
        if not os.path.isdir(f"{BASE}/clients/{client}"):
            return self.reply(404, {"ok": False, "error": "no such client: %s" % client})

        if self.path == "/process":
            fname = str(body.get("file", "")).strip()
            if not fname or not SAFE.match(fname):
                return self.reply(400, {"ok": False, "error": "missing or unsafe 'file'"})
            path = f"{BASE}/inbox/{fname}"
            if not os.path.isfile(path):
                return self.reply(404, {"ok": False, "error": "not in inbox: %s" % fname})
            try:
                rc, out, err = run([PYBIN, f"{BASE}/bin/process_verify.py", path, client], 900)
            except subprocess.TimeoutExpired:
                return self.reply(504, {"ok": False, "error": "processing timed out"})
            if rc != 0:
                return self.reply(500, {"ok": False, "error": (err or out)[-600:]})
            try:
                summary = json.loads(out.strip().splitlines()[-1])
            except Exception:
                return self.reply(500, {"ok": False, "error": "could not parse summary", "raw": out[-600:]})
            return self.reply(200, {"ok": True, **summary})

        if self.path == "/reindex":
            try:
                rc, out, err = run([PYBIN, f"{BASE}/bin/build_index.py", client], 900)
            except subprocess.TimeoutExpired:
                return self.reply(504, {"ok": False, "error": "indexing timed out"})
            if rc != 0:
                return self.reply(500, {"ok": False, "error": (err or out)[-600:]})
            return self.reply(200, {"ok": True, "client": client, "log": out.strip().splitlines()[-4:]})

        self.reply(404, {"ok": False, "error": "unknown path"})

if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "172.16.5.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8899
    print("listening on %s:%d" % (host, port), flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
