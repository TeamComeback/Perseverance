"""Offline end-to-end run on the mock market: python tests/simulate.py [days]"""
import os, subprocess, sys, json
from datetime import datetime, timedelta, timezone
days = int(sys.argv[1]) if len(sys.argv) > 1 else 10
data = os.environ.get("PERSEVERANCE_DATA", "/tmp/perseverance-sim")
os.makedirs(data, exist_ok=True)
for f in os.listdir(data): os.remove(os.path.join(data, f))
env = {**os.environ, "ODDS_MOCK": "1", "PERSEVERANCE_DATA": data}
t0 = datetime(2026, 10, 27, tzinfo=timezone.utc)
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in range(days):
    for hh, cmd in ((9, "nightly"), (15, "cycle"), (20, "cycle"), (23, "cycle")):
        env["PERSEVERANCE_NOW"] = (t0 + timedelta(days=d, hours=hh, minutes=5)).isoformat()
        r = subprocess.run([sys.executable, "-m", "agent.run", cmd], cwd=root, env=env, capture_output=True, text=True)
        if r.returncode: print(r.stderr); sys.exit(1)
s = json.load(open(f"{data}/state.json")); b = json.load(open(f"{data}/budget.json"))
print(json.dumps(s["stats"], indent=1)); print("params", s["params"]); print("weights", s["weights"])
print("runs", s["book"]["runs"]); print("credits left", b["remaining"], "allowance", b.get("allowance"))
for j in s["journal"][-8:]: print("-", j["kind"], j["text"][:200])
