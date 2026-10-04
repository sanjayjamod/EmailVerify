## 2023-10-04 - SQLite Bulk IN Clause Bottleneck
**Learning:** For bulk lookup operations in SQLite (e.g., in `bin/process_verify.py`), using chunked `IN (...)` queries is slow and vulnerable to `SQLITE_MAX_VARIABLE_NUMBER` limits.
**Action:** Replace chunked `IN` queries with a single temporary table, batch-inserted via `executemany`, followed by a `JOIN`. This approach is significantly faster (approx 2x) and scales perfectly for large lists without hitting parameter limits.
