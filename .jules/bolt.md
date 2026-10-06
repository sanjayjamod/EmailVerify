## 2026-10-06 - SQLite bulk lookups

**Learning:** When optimizing SQLite queries for bulk operations, using a temporary table with a JOIN is significantly faster (approx 2x in benchmarks) and more scalable than chunking variable bindings for IN clauses, while also preventing SQLITE_MAX_VARIABLE_NUMBER limits. Using a loop with single SELECTs (N+1 problem) is the slowest and should be avoided.

**Action:** Whenever implementing bulk operations or lookups in SQLite, especially when data size can exceed the variable limit, default to creating a temporary table and using a JOIN rather than looping or batching IN queries.
