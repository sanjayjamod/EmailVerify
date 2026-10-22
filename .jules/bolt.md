
## 2026-10-22 - Optimizing SQLite Bulk Lookups with Temporary Tables
**Learning:** In SQLite, when checking a large list of values against a table (like verifying if emails are in a cache), a Python loop doing individual `SELECT` queries results in a significant N+1 performance bottleneck. While a bulk `IN` clause can improve this, using `CREATE TEMP TABLE`, populating it using `executemany`, and executing a single `LEFT JOIN` provides comparable or better performance (about ~2x-3x faster than N+1) and safely avoids the SQLite `MAX_VARIABLE_NUMBER` limit.
**Action:** Always prefer temporary tables combined with `JOIN` operations over `IN` clauses with large sets of variables or individual lookup loops when dealing with bulk data matching in SQLite.
