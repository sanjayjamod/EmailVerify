

## 2023-10-26 - [Optimize Bulk Lookup in create_job]
**Learning:** Using a temporary table with a JOIN for bulk SQLite queries is significantly faster (approx 2x) and more scalable than chunking variable bindings for IN clauses, and it avoids SQLITE_MAX_VARIABLE_NUMBER limits.
**Action:** Always prefer TEMPORARY TABLE + JOIN over N+1 loops or large IN clauses when checking large batches of values (like emails) against an SQLite database.
