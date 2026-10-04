1. **Optimize `process_verify.py`**: Replace chunked `IN` queries with a temporary table `JOIN` for looking up accepted emails in `contacts.db`. This provides a ~2x speedup and prevents `SQLITE_MAX_VARIABLE_NUMBER` limits.
2. **Pre-commit Steps**: Ensure proper testing, verification, review, and reflection are done by calling the necessary instructions.
3. **Submit**: Create PR for the performance improvement with title "⚡ Bolt: [performance improvement]" and details.
