## YYYY-MM-DD - [Title]
**Learning:** [Insight]
**Action:** [How to apply next time]

## 2024-10-28 - [Performance Boost: Live Feed Queries]
**Learning:** Queries that look up the most recent updates for a specific parent record (e.g. `job_id`) using `ORDER BY timestamp DESC` can result in full table scans and expensive temporary B-Tree sorts if they lack a covering index.
**Action:** Always consider compound indexes covering the filtering criteria and sorting column (e.g. `(job_id, state, checked_at)`) to turn `O(N)` sorts into `O(1)` or `O(LIMIT)` index scans.
