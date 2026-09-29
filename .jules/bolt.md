## 2026-09-29 - SQLite LIMIT behavior in cache lookup

**Learning:** When retrieving many rows at once, using a naive batch cache lookup (like an `IN` clause with thousands of bindings) can be restricted by SQLite limits `SQLITE_MAX_VARIABLE_NUMBER` (typically 999 or 32766). Grouping the bindings into chunks (e.g. 900) yields better performance than N+1 queries. However, creating a temporary table and joining against it is typically faster for SQLite than chunked `IN` queries and allows arbitrary scaling for large datasets.

**Action:** Replace `for` loops making individual database queries with chunked queries or `TEMP TABLE` joins when inserting or checking against existing tables.
