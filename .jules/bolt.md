## 2026-09-28 - SQLite N+1 Cache Lookups in `bin/store.py`
**Learning:** The initial implementation of `create_job` processed uploaded email lists by querying the SQLite `cache` table individually for every email address. This resulted in an N+1 query bottleneck which significantly slowed down large uploads.
**Action:** Replace single row `SELECT` statements inside loops with batched queries using the SQL `IN` clause. When batching parameters for SQLite, chunk them into groups of <= 900 to safely stay under SQLite's default variable limit (999).
