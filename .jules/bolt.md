## 2024-10-30 - job_counts aggregation via GROUP BY
**Learning:** When aggregating multiple states or codes over hundreds of thousands of rows in SQLite, using a `GROUP BY` query and performing the bucketing/aggregation in Python is significantly faster (~4.4x) than using conditional `SUM(condition)` statements in the SQL query, as the latter forces SQLite to evaluate complex logic for every single row.
**Action:** When calculating summary statistics on large SQLite tables, prefer `GROUP BY` + Python aggregation over complex conditional `SUM()` queries.
