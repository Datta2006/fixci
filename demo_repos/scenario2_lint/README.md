# FixCI demo — ruff lint violation

`ruff check .` reports an unused import (`F401`) and an unused variable
(`F841`), so the Lint step fails. FixCI should classify this as **lint** and
propose removing the offending lines.
