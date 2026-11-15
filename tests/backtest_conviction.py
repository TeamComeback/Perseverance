"""Conviction model: current (trained on the matchup side) vs line-side, leave-one-day-out on graded DraftKings props.
    python -m tests.backtest_conviction
The same test runs every night (agent/convswitch.py) and is shown on the dashboard under Model > Health."""
import json
from agent import convmodel, store

from agent import lines as linedb
res = convmodel.compare(linedb.load()["entries"], log=lambda m: None)
print(json.dumps({k: res[k] for k in ("n", "days", "from", "to", "ll", "top", "days_better", "pass")}, indent=1))
for c in res["checks"]:
    print(("PASS " if c["ok"] else "FAIL ") + c["t"])
print("line-side coefficients:", res["coef_line"])
