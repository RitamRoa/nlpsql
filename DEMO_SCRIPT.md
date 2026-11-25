# Short demo script

Use the local app after configuring a real provider; the checked workspace currently has only `MockProvider`, so generated answers are not yet demo-ready.

1. **Normal question:** select the `company` schema and ask, “List all employees in the Sales department.” Show the generated SQL, returned rows, and validation/execution stages. Confirm the result against the seed data rather than presenting the example as a benchmark score.
2. **Abstention:** explain that confidence compares candidate query results and is not a calibrated probability. For a deterministic demonstration of abstention, run:

   ```powershell
   py -m pytest -q tests/test_product_features.py::test_confidence_abstains_when_candidate_results_disagree
   ```

   The test supplies candidates that return different rows and verifies that the pipeline abstains instead of returning a result.
3. **CTE RBAC bypass blocked:** with role policy enabled, try to shadow a real table using `WITH employees AS (SELECT * FROM employees) SELECT name, salary FROM employees`. The policy/validator must reject it. Run the cross-engine regression suite to show the SQLite and DuckDB coverage:

   ```powershell
   py -m pytest -q tests/test_rbac_regressions.py
   ```

Close by stating the limits plainly: four tiny sample databases, 80 held-out questions, broad confidence intervals, and a small model. Do not claim benchmark accuracy until a real provider has completed the run.
