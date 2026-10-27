"""Are the model's over/under probabilities honest? Walk-forward on 2025-26.
Train on games before each cut, predict the next 3 weeks with lines set at each player's norm,
then compare predicted probability with how often it actually happened."""
import random, sys
from collections import defaultdict
from datetime import date, timedelta
from agent import nba_model as nm, nba_data as nd

W = {"w_dvp": 0.45, "w_sim": 0.40, "w_h2h": 0.25, "matchup_strength": 1.0}
bins = defaultdict(lambda: [0, 0, 0.0])
edge_bins = defaultdict(lambda: [0, 0])
games = [x for x in nd.all_games([2026]) if x[2].get("st") == 2]
for cut in ("2025-12-01", "2026-01-05", "2026-02-09", "2026-03-09"):
    m = nm.Model(2026, asof=cut)
    end = (date.fromisoformat(cut) + timedelta(21)).isoformat()
    sample = []
    for _, _, g in games:
        if not (cut <= g["d"] < end): continue
        ab = list(g["teams"])
        for p in g["players"]:
            pid = p[nd.P_ID]
            if pid in m.players and m.players[pid]["games"] >= 15 and m.minutes(pid) >= 20:
                t = p[nd.P_TEAM]; o = ab[1] if t == ab[0] else ab[0]
                sample.append((p, o, g["teams"][t]["home"]))
    random.seed(cut); random.shuffle(sample)
    for p, o, home in sample[:700]:
        for s in nm.STATS:
            pr = m.project(p[nd.P_ID], s, o, home, W)
            neutral = dict(pr); neutral["proj"] = pr["base"]
            line = max(0.5, round(pr["base"] - 0.5) + 0.5)
            po, p0 = nm.p_over(pr, line), nm.p_over(neutral, line)
            hit = nm._val(p, s) > line
            b = min(9, int(po * 10)); bins[b][0] += 1; bins[b][1] += hit; bins[b][2] += po
            d = po - p0                      # how far the matchup moved the probability
            k = "matchup says over +5pp" if d > 0.05 else "matchup says under +5pp" if d < -0.05 else "small move"
            edge_bins[k][0] += 1; edge_bins[k][1] += (hit if d > 0 else (not hit) if d < 0 else hit)
print("predicted over%   n     actual over%")
for b in sorted(bins):
    n, h, ps = bins[b]
    print(f"   {ps/n:5.1%}      {n:5}    {h/n:5.1%}")
print()
for k, (n, h) in edge_bins.items():
    print(f"{k:24} n={n:5}  matchup direction right {h/n:5.1%}")
