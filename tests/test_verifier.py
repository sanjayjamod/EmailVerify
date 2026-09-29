"""
Tests against a fake MailTester Ninja API - no network, no credits.

    python -m unittest discover -s tests -v

Needs requests and flask (the same venv the server runs).
"""
import csv, json, os, sqlite3, subprocess, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin")
TMP = tempfile.mkdtemp(prefix="emailverify-test-")
os.environ["EMAILS_BASE"] = TMP
sys.path.insert(0, BIN)

import store            # noqa: E402  (EMAILS_BASE must be set first)
import verify_worker    # noqa: E402

# ---------------------------------------------------------------- fake API

SCRIPT = {}     # email -> list of answers; the last one repeats
CALLS = []


class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        email = parse_qs(urlparse(self.path).query)["email"][0]
        CALLS.append(email)
        answers = SCRIPT.get(email, [("ok", "Accepted")])
        code, message = answers.pop(0) if len(answers) > 1 else answers[0]
        body = json.dumps({"email": email, "code": code, "message": message}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
threading.Thread(target=server.serve_forever, daemon=True).start()
FAKE = "http://127.0.0.1:%d/ninja" % server.server_address[1]
verify_worker.API = FAKE


def fresh_db():
    store.DB = os.path.join(TMP, "verify-%f.db" % time.time())
    store.init()
    return store.connect()


def run_worker(con, conf, until, timeout=10):
    """Run one worker thread until until(con) is true."""
    cfg = dict(verify_worker.DEFAULTS, retry_delay=0, key_cooldown=0.2, **conf)
    bucket = verify_worker.Bucket(1000, 1.0)
    counter = {"n": 0, "lock": threading.Lock()}
    verify_worker.stop.clear()
    t = threading.Thread(target=verify_worker.worker,
                         args=("w0", cfg, "KEY", bucket, counter), daemon=True)
    t.start()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if until(con):
                return
            time.sleep(0.05)
        raise AssertionError("worker did not finish in time")
    finally:
        verify_worker.stop.set()
        t.join(timeout=10)


def row(con, email):
    return con.execute("SELECT * FROM emails WHERE email=?", (email,)).fetchone()


def cached(con, email):
    return con.execute("SELECT * FROM cache WHERE email=?", (email,)).fetchone()


def settled(email):
    return lambda con: row(con, email)["state"] != "pending"


# ---------------------------------------------------------------- worker

class WorkerTest(unittest.TestCase):

    def setUp(self):
        SCRIPT.clear()
        CALLS.clear()
        self.con = fresh_db()

    def test_mx_error_is_retried_whatever_the_config_case(self):
        # The live config says "Mx Error"; the API sends "MX Error".
        SCRIPT["a@x.com"] = [("mb", "MX Error"), ("ok", "Accepted")]
        store.create_job(self.con, "j", "", ["a@x.com"])
        run_worker(self.con, {"transient": ["Timeout", "Mx Error", "SPAM Block"]},
                   settled("a@x.com"))
        r = row(self.con, "a@x.com")
        self.assertEqual((r["state"], r["code"], r["message"]), ("done", "ok", "Accepted"))
        self.assertEqual(r["attempts"], 1)
        self.assertEqual(cached(self.con, "a@x.com")["message"], "Accepted")

    def test_greylisted_is_retried(self):
        SCRIPT["g@x.com"] = [("mb", "Greylisted"), ("ko", "Rejected")]
        store.create_job(self.con, "j", "", ["g@x.com"])
        run_worker(self.con, {}, settled("g@x.com"))
        self.assertEqual(row(self.con, "g@x.com")["message"], "Rejected")

    def test_out_of_tries_keeps_the_reason_and_is_not_cached(self):
        SCRIPT["s@x.com"] = [("mb", "SPAM Block")]
        store.create_job(self.con, "j", "", ["s@x.com"])
        run_worker(self.con, {}, settled("s@x.com"))
        r = row(self.con, "s@x.com")
        self.assertEqual((r["state"], r["message"], r["attempts"]), ("failed", "SPAM Block", 3))
        self.assertIsNone(cached(self.con, "s@x.com"))

    def test_key_error_is_never_saved_as_a_result(self):
        SCRIPT["k@x.com"] = [("--", "Disabled Key"), ("ok", "Accepted")]
        store.create_job(self.con, "j", "", ["k@x.com"])
        run_worker(self.con, {}, settled("k@x.com"))
        r = row(self.con, "k@x.com")
        self.assertEqual((r["state"], r["message"]), ("done", "Accepted"))
        self.assertEqual(r["attempts"], 0)         # the key's fault, not the address's
        self.assertEqual(CALLS.count("k@x.com"), 2)
        self.assertEqual(cached(self.con, "k@x.com")["message"], "Accepted")

    def test_final_answers_are_cached_and_not_bought_twice(self):
        SCRIPT["c@x.com"] = [("mb", "Catch-All")]
        store.create_job(self.con, "j1", "", ["c@x.com"])
        run_worker(self.con, {}, settled("c@x.com"))
        _, queued, from_cache = store.create_job(self.con, "j2", "", ["c@x.com"])
        self.assertEqual((queued, from_cache), (0, 1))
        self.assertEqual(CALLS.count("c@x.com"), 1)


# ---------------------------------------------------------------- store

class ClaimTest(unittest.TestCase):

    def test_no_row_is_handed_to_two_threads(self):
        con = fresh_db()
        emails = ["u%d@x.com" % i for i in range(1500)]
        store.create_job(con, "j", "", emails)
        got, lock = [], threading.Lock()

        def grab():
            c = store.connect()
            while True:
                batch = store.claim_batch(c, 1)
                if not batch:
                    return
                with lock:
                    got.extend(e for _, e in batch)

        threads = [threading.Thread(target=grab) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(got), len(emails))
        self.assertEqual(len(set(got)), len(emails))

    def test_counts_ignore_codes_left_on_failed_rows(self):
        con = fresh_db()
        job_id, _, _ = store.create_job(con, "j", "", ["f@x.com"])
        con.execute("UPDATE emails SET state='failed', code='ko', message='SPAM Block'")
        con.commit()
        c = store.job_counts(con, job_id)
        self.assertEqual((c["failed"], c["rejected"], c["unknown"]), (1, 0, 0))


class FixTransientTest(unittest.TestCase):

    def test_reopens_only_answers_that_were_never_final(self):
        store.DB = os.path.join(TMP, "verify.db")      # where the script looks
        if os.path.exists(store.DB):
            os.remove(store.DB)
        store.init()
        con = store.connect()
        answers = {"a@x.com": ("ok", "Accepted"), "c@x.com": ("mb", "Catch-All"),
                   "m@x.com": ("mb", "MX Error"), "g@x.com": ("mb", "Greylisted"),
                   "k@x.com": ("--", "Disabled Key")}
        job_id, _, _ = store.create_job(con, "j", "", list(answers))
        for e, (code, msg) in answers.items():
            store.save_result(con, row(con, e)["id"], e, code, msg)
        con.commit()

        script = [sys.executable, os.path.join(BIN, "fix_transient.py")]
        env = dict(os.environ, EMAILS_BASE=TMP)
        dry = subprocess.run(script, capture_output=True, text=True, env=env)
        self.assertIn("job rows to mark failed  3", dry.stdout)
        self.assertEqual(store.job_counts(con, job_id)["failed"], 0)   # untouched

        subprocess.run(script + ["--apply"], check=True, capture_output=True, env=env)
        states = {e: row(con, e)["state"] for e in answers}
        self.assertEqual(states, {"a@x.com": "done", "c@x.com": "done", "m@x.com": "failed",
                                  "g@x.com": "failed", "k@x.com": "failed"})
        self.assertEqual({r["email"] for r in con.execute("SELECT email FROM cache")},
                         {"a@x.com", "c@x.com"})


# ---------------------------------------------------------------- output

class ProcessVerifyTest(unittest.TestCase):

    def test_every_address_lands_in_a_folder(self):
        os.makedirs(os.path.join(TMP, "clients", "acme"), exist_ok=True)
        idx = sqlite3.connect(os.path.join(TMP, "clients", "acme", "contacts.db"))
        idx.execute("CREATE TABLE IF NOT EXISTS contacts (email TEXT PRIMARY KEY, FNAME TEXT)")
        idx.commit()
        idx.close()

        src = os.path.join(TMP, "part-x.csv")
        with open(src, "w", newline="") as fh:
            csv.writer(fh).writerows([
                ["ok@x.com", "ok", "Accepted"],
                ["bad@x.com", "ko", "Rejected"],
                ["ca@x.com", "mb", "Catch-All"],
                ["mx@x.com", "mb", "MX Error"],      # saved as final before the fix
                ["gl@x.com", "mb", "Greylisted"],
                ["key@x.com", "--", "Disabled Key"],
                ["fail@x.com", "", ""],              # a failed row, no answer
            ])
        p = subprocess.run([sys.executable, os.path.join(BIN, "process_verify.py"), src, "acme"],
                           capture_output=True, text=True, env=dict(os.environ, EMAILS_BASE=TMP))
        self.assertEqual(p.returncode, 0, p.stderr)
        summary = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["buckets"], {"bad - do not send": 1,
                                              "unknown - risky": 1,
                                              "needs re-verify": 4})


class WebappTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import webapp
        webapp.API = FAKE
        webapp.BASE = TMP
        with open(os.path.join(TMP, "mailtester.key"), "w") as fh:
            fh.write("KEY")
        cls.webapp = webapp
        cls.client = webapp.app.test_client()

    def setUp(self):
        SCRIPT.clear()
        self.con = fresh_db()

    def test_single_check_does_not_cache_key_errors_or_retryable_answers(self):
        SCRIPT["k@x.com"] = [("--", "Disabled Key")]
        SCRIPT["t@x.com"] = [("mb", "Timeout")]
        SCRIPT["a@x.com"] = [("ok", "Accepted")]
        self.assertFalse(self.client.post("/check", data={"email": "k@x.com"}).json["ok"])
        self.assertTrue(self.client.post("/check", data={"email": "t@x.com"}).json["ok"])
        self.assertTrue(self.client.post("/check", data={"email": "a@x.com"}).json["ok"])
        self.assertIsNone(cached(self.con, "k@x.com"))
        self.assertIsNone(cached(self.con, "t@x.com"))
        self.assertEqual(cached(self.con, "a@x.com")["message"], "Accepted")

    def test_download_includes_failed_rows(self):
        job_id, _, _ = store.create_job(self.con, "j.csv", "", ["a@x.com", "f@x.com"])
        self.con.execute("UPDATE emails SET state='done', code='ok', message='Accepted'"
                         " WHERE email='a@x.com'")
        self.con.execute("UPDATE emails SET state='failed' WHERE email='f@x.com'")
        self.con.commit()
        body = self.client.get("/job/%d/download" % job_id).data.decode()
        self.assertIn("a@x.com,ok,Accepted", body)
        self.assertIn("f@x.com,,", body)


if __name__ == "__main__":
    unittest.main()
