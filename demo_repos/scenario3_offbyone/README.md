# FixCI demo — off-by-one bug

`calc.add` returns `a + b + 1`, so `test_add` fails with
`AssertionError: assert 5 == 4`. FixCI should classify this as **test_failure**
and propose the one-line fix to `calc.py`.
