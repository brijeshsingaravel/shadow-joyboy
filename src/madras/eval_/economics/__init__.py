"""Unit-economics / profitability engine (SP3).

Solves for break-even/target-margin price + the most profitable mechanism per
tier (FREE-OSS / PAID-frontier / BYOK), reading measured cost from the Proving
Ground spine (pg_metrics) and layering parameterized infra + usage. Pure math in
pricing.py/scaling.py; the only DB seams are cost_model.py + store.py.
"""
