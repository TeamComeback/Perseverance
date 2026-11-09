"""Walk-forward check of the minutes model on recent days: projected vs actual minutes, by role.
(No injury report or lineups in the replay, so real runs do a little better.)
Run: python tests/minutes.py 2026-11-01 2026-11-08"""
import sys
from datetime import date, timedelta
sys.path.insert(0, ".")
from agent import nba_model as nm, nba_data as nd, research as r

OLD = None
start, end = date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])
db = nd.load(nd.season_of(end))["games"]
acc = {}
def add(k, pred, act):
    a = acc.setdefault(k, [0, 0.0, 0.0, 0]); a[0] += 1; a[1] += pred - act; a[2] += abs(pred - act); a[3] += abs(pred - act) <= 4
tot_err = []
d = start
while d <= end:
    day = d.isoformat()
    todays = [(gid, g) for gid, g in db.items() if g["d"] == day and g.get("st", 2) == 2]
    if todays:
        m = nm.Model(nd.season_of(d), asof=day)
        games = []
        for gid, g in todays:
            ab = list(g["teams"]); home = next(t for t in ab if g["teams"][t]["home"]); away = next(t for t in ab if t != home)
            games.append({"home": home, "away": away, "espn_id": gid, "start": day + "T23:00Z"})
        # stand-in for the injury report: a regular (played both of his team's last two games) who sat tonight was listed out
        playing = {p[nd.P_ID] for gid, g in todays for p in g["players"]}
        tdates = {}
        for row in m.rows:
            tdates.setdefault(row["team"], set()).add(row["d"])
        inj = {}
        for pid, pl in m.players.items():
            if pid in playing or not m.by_player[pid]:
                continue
            last2 = sorted(tdates.get(pl["team"], []))[-2:]
            mine = {x["d"] for x in m.by_player[pid] if x["team"] == pl["team"]}
            if len(last2) == 2 and all(d in mine for d in last2):
                inj[pid] = "Out"
        mm = {k: v for k, v in r.minutes_model(m, games, inj, {}, {}).items()}
        raw = {pid: m.minutes(pid) for pid in mm}
        r_old = OLD(m, games, inj) if OLD else None
        for gid, g in todays:
            act = {p[nd.P_ID]: (p[nd.P_MIN], p[nd.P_START]) for p in g["players"]}
            for t in g["teams"]:
                mine = [pid for pid in mm if m.players[pid]["team"] == t]
                tot_err.append((sum(mm[p][0] for p in mine), sum(raw[p] for p in mine)))
                for pid in mine:
                    a = act.get(pid)
                    for tag, pred in (("new", mm[pid][0]), ("old", (r_old[pid][0] if r_old and pid in r_old else raw[pid]))):
                        if pred < 8:
                            if a and tag == "new": add("new: dropped but played", pred, a[0])
                            continue
                        if a is None:
                            add(f"{tag}: projected 8+, DNP", pred, 0); continue
                        add(f"{tag}: starters" if a[1] else f"{tag}: bench", pred, a[0])
                        add(f"{tag}: all who played", pred, a[0])
    d += timedelta(days=1)
print(f"{'':30s}    n   bias   avg miss  within 4")
for k in sorted(acc):
    n, b, ae, w = acc[k]; print(f"{k:30s} {n:4d}  {b / n:+.2f}    {ae / n:.2f}     {w / n * 100:.0f}%")
print("team totals (new, old):", round(sum(a for a, _ in tot_err) / len(tot_err)), round(sum(b for _, b in tot_err) / len(tot_err)))
