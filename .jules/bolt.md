## 2023-10-24 - Initial Knowledge
**Learning:** SQLite temporary tables are safe to use for database optimizations in this codebase because they are connection-local. Chunked IN clauses can outperform temporary tables for simple bulk lookups.
**Action:** Consider temporary tables and chunked IN clauses for N+1 problems depending on complexity. Always cleanup temporary test scripts.

## 2023-10-24 - N+1 optimization with temporary table
**Learning:** Replaced N+1 loop in `create_job` with a local temporary table lookup using LEFT JOIN/JOIN. This bypasses the typical SQLITE variable limit on large IN clauses and provided a ~30% speedup for large datasets (e.g. 20,000 queries dropped from ~0.235s to ~0.178s).
**Action:** Use connection-local temporary tables + JOINs when querying against or inserting many records, especially when it replaces a loop of individual executes. Ensure a `DELETE FROM` or `DROP TABLE` is included so tables don't bloat.
