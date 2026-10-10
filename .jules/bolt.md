## 2023-10-10 - SQLite Bulk IN clause vs Temp Table JOIN
**Learning:** In `bin/process_verify.py` and `bin/store.py`, looking up thousands of records by doing chunked `SELECT ... WHERE email IN (...)` queries (with e.g. chunks of 900 to avoid `SQLITE_MAX_VARIABLE_NUMBER`) is significantly slower than bulk inserting the IDs into a TEMPORARY TABLE and doing a single `JOIN`.
**Action:** Always prefer `CREATE TEMPORARY TABLE temp (id TEXT) ... INSERT ... JOIN` over N+1 chunked `IN` queries when working with large bulk operations in SQLite.
