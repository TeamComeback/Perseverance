"""Dry run of the evening props run against tomorrow's real slate, with a fake sportsbook
that posts lines at each player's NORMAL output (no matchup info). Any bets come from the matchup model."""
import json, os, random, sys
os.environ.setdefault("PERSEVERANCE_DATA", "/tmp/props-dry")
os.makedirs(os.environ["PERSEVERANCE_DATA"], exist_ok=True)
from agent import nba_data as nd, nba_model as nm, props, run, store
from agent.odds import Budget

class FakeClient:
    def __init__(self, budget):
        self.budget = budget
        self.m = nm.Model(nd.season_of(store.now().astimezone(nd.ET).date()))
        self.slate = nd.slate(store.now().astimezone(nd.ET).date())
    def sports(self): return []
    def events(self, sport):
        return [{"id": "fake-" + g["espn_id"], "home_team": g["home_name"], "away_team": g["away_name"],
                 "commence_time": g["start"]} for g in self.slate]
    def event_odds(self, sport, eid, markets):
        self.budget.record("event_odds", len(markets), (self.budget.b["remaining"] or 500) - len(markets), None)
        g = next(x for x in self.slate if "fake-" + x["espn_id"] == eid)
        rng = random.Random(eid)
        books = []
        for bkey in ("draftkings", "fanduel", "betmgm", "caesars"):
            mk = []
            for mkey in markets:
                s = {v: k for k, v in nm.ODDS_KEY.items()}[mkey]
                outs = []
                for pid, pl in self.m.players.items():
                    if pl["team"] not in (g["home"], g["away"]) or self.m.minutes(pid) < 18:
                        continue
                    # the fake book knows each player's norm but nothing about the matchup
                    pr = self.m.project(pid, s, g["away"] if pl["team"] == g["home"] else g["home"], pl["team"] == g["home"],
                                        {"w_dvp": 0, "w_sim": 0, "w_h2h": 0, "matchup_strength": 0})
                    line = max(0.5, round(pr["base"] - 0.5) + 0.5)
                    fair = nm.p_over(pr, line) + rng.uniform(-0.02, 0.02)
                    fair = min(0.9, max(0.1, fair))
                    outs += [{"name": "Over", "description": pl["name"], "point": line, "price": round(1 / (fair * 1.045), 3)},
                             {"name": "Under", "description": pl["name"], "point": line, "price": round(1 / ((1 - fair) * 1.045), 3)}]
                mk.append({"key": mkey, "outcomes": outs})
            books.append({"key": bkey, "markets": mk})
        return {"bookmakers": books}

budget = Budget(); budget.b["remaining"] = 400; budget.start_run(units=3)
budget.allowance = int(sys.argv[1]) if len(sys.argv) > 1 else 9
state, manual = run.load_all()
client = FakeClient(budget)
out = props.run(state, manual, client, budget)
print(f"slate {len(out['games'])} games | bought {out.get('bought')} | spent {budget.spent_this_run} | priced {out.get('cands')} lines")
print("\nTop matchup spots:")
for x in out["spots"][:8]:
    print(f"  {x['name']:24} {x['stat']:3} vs {x['opp']:4} norm {x['base']:5.1f} -> proj {x['proj']:5.1f} ({(x['mult']-1)*100:+.0f}%)")
print("\nBets placed:")
for b in out.get("placed", []):
    print(f"  ${b['stake']:.2f} {run.describe(b)}  EV {b['ev']:+.1%}\n     {b['note'][:330]}")
