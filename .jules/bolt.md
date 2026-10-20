## 2024-05-18 - [Bulk lookup temp table optimization]
**Learning:** When optimizing SQLite queries for bulk operations or lookups, replacing N+1 Python-level SELECT queries with a temporary table and a JOIN is significantly faster (approx 2x-3x) and prevents SQLITE_MAX_VARIABLE_NUMBER limits. However, for simple bulk lookups, chunked IN clauses can actually outperform temporary tables (as proven by benchmarks in process_verify.py) and should not necessarily be replaced.
**Action:** Use chunked IN clauses for simple lookups if limits might be hit, but prefer TEMP TABLE + JOIN for complex multi-table bulk operations.
## 2024-10-20 - [Bulk insert optimization for job creation]
**Learning:** When ingesting a large list of items into the database where each item needs to be checked against a cache, replacing the N+1 Python-level SELECT queries with a TEMP TABLE and an INSERT ... SELECT ... LEFT JOIN is significantly faster (approx 2x faster). This completely eliminates the need for loop-based SELECT queries over thousands of items.
**Action:** Prefer TEMP TABLE + LEFT JOIN when combining bulk insertions with cache lookups, to avoid N+1 query bottlenecks in job ingestion.
