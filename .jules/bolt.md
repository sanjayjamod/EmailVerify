## 2024-03-21 - [SQLite Bulk Optimization]
**Learning:** When optimizing SQLite queries for bulk operations, using a temporary table with a JOIN is significantly faster (approx 2x) and more scalable than chunking variable bindings for IN clauses, while also preventing SQLITE_MAX_VARIABLE_NUMBER limits.
**Action:** Always prefer temporary tables + JOINs over `IN (...)` for bulk ID lookups or updates in SQLite.
