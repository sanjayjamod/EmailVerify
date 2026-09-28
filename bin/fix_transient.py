#!/usr/bin/env python3
"""
One-off clean-up for answers saved before the worker learned to retry them.

Until the fix, "MX Error" (the config said "Mx Error", so it never matched)
and "Greylisted" were kept as final verdicts, and a few "--" / Disabled Key
answers were saved as results. This drops them from the cache, so a later
upload checks those addresses again, and marks their job rows failed, so each
job's "Retry failed" button covers them. Nothing is sent to the API and no
credit is spent.

    fix_transient.py            show what would change
    fix_transient.py --apply    change it
"""

import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store

verdicts_params = ",".join("?" for _ in store.VERDICTS)
transient_params = ",".join("?" for _ in store.TRANSIENT)
NOT_FINAL = f"(code NOT IN ({verdicts_params}) OR lower(message) IN ({transient_params}))"
params = tuple(store.VERDICTS) + tuple(m.lower() for m in store.TRANSIENT)

con = store.connect()
# sourcery skip: sql-injection
cached = con.execute(
    f"SELECT COUNT(*) FROM cache WHERE {NOT_FINAL}", params
).fetchone()[0]
# sourcery skip: sql-injection
rows = con.execute(f"SELECT code, message, COUNT(*) n FROM emails"
                   f" WHERE state='done' AND {NOT_FINAL}"
                   f" GROUP BY code, message ORDER BY n DESC", params).fetchall()

print("cache entries to drop    %d" % cached)
print("job rows to mark failed  %d" % sum(r["n"] for r in rows))
for r in rows:
    print("    %-3s %-14s %d" % (r["code"], r["message"], r["n"]))

if "--apply" not in sys.argv:
    print("dry run - nothing changed. Run again with --apply to do it.")
    sys.exit(0)

# sourcery skip: sql-injection
con.execute(f"DELETE FROM cache WHERE {NOT_FINAL}", params)
# sourcery skip: sql-injection
con.execute(f"UPDATE emails SET state='failed', next_try=0"
            f" WHERE state='done' AND {NOT_FINAL}", params)
con.commit()
print("done")
