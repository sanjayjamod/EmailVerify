## 2026-10-19 - Bulk SQLite cache lookups

**Learning:** When inserting large lists of emails and checking if they exist in a cache table, running `con.execute("SELECT ... WHERE email=?", (e,))` in a Python for-loop creates an N+1 query problem that gets very slow (e.g. 1+ second for 100,000 rows).
**Action:** Replace the Python loop with a temporary table for the input list, bulk insert into the temp table using `executemany`, and then perform a single `LEFT JOIN` between the temp table and the cache table directly in SQLite. This prevents `SQLITE_MAX_VARIABLE_NUMBER` limits from a giant `IN` clause and is 2-3x faster.
