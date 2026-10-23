## 2026-10-23 - Optimize bulk insert cache lookup
**Learning:** For bulk insertion of items with cache lookups, replacing N+1 Python-level SELECT queries with a SQLite TEMPORARY TABLE and JOINs significantly improves performance (approx 2x faster). Temporary tables in SQLite are connection-local, so they are safe in a connection-per-request model.
**Action:** When handling large bulk lookups before insert, use a temporary table to pull from cache via JOIN instead of a python loop with individual lookups.
