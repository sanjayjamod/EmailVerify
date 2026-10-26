
## 2026-10-26 - [SQLite Performance: GROUP BY vs Conditional SUM]
**Learning:** Using conditional `SUM(condition)` statements (e.g., `SUM(state='pending')`) forces SQLite to evaluate complex logic for every single row in the dataset, which becomes a bottleneck on tables with hundreds of thousands of rows.
**Action:** When aggregating multiple states or values from a large table, it is significantly faster (~4.4x speedup) to use a simple `GROUP BY` query to get raw counts and perform the conditional aggregation/bucketing in Python. Note that SQL handles NULLs distinctly in logic conditions compared to standard Python comparisons, so be careful to replicate NULL handling correctly.
