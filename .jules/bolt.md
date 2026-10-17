## 2024-05-15 - [Initial]
**Learning:** Initializing journal.
**Action:** Ready for entries.

## 2024-05-15 - [SQLite Bulk Lookups]
**Learning:** For bulk lookups in SQLite, instead of N+1 queries or chunked `IN` queries (which can hit SQLITE_MAX_VARIABLE_NUMBER limits), inserting into a temporary table and performing a JOIN is significantly faster (~2x) and more scalable.
**Action:** Always prefer `CREATE TEMP TABLE` + `JOIN` for looking up a large list of items (e.g. cache hits, contacts) against an existing SQLite table.
