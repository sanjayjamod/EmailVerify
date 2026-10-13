## 2024-05-18 - Optimized SQLite caching using TEMP TABLE + JOIN
**Learning:** When optimizing SQLite queries for bulk operations, using a temporary table with a JOIN is significantly faster (approx 2x) and more scalable than chunking variable bindings for IN clauses, while also preventing SQLITE_MAX_VARIABLE_NUMBER limits.
**Action:** Use TEMP TABLE JOIN for bulk operations involving many rows/values.
