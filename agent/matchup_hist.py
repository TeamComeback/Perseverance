"""Matchup history: how each graded prop looked on the matchup filters the Props tab offers, as of its own day.

The Props tab's matchup filters/sorter (docs/index.html, section "Matchup filters") let the owner cut tonight's props by

  pos   the opponent's defense vs his role in that stat      rank 1-30, 1 = gives up the most (soft)
  pace  the opponent's pace                                  rank 1-30, 1 = fastest
  def   the opponent's team defense                          rank 1-30, 1 = best defense
  line  the line vs his usual output at his normal minutes   (line - base) / base

and each cut shows how the same cut has done on every graded prop at the closing line and price. For that to be fair the
fields here are computed exactly as the live ones are, but from a model that has only seen games BEFORE the prop's day
(nba_model.Model(asof=day)), so there is no look-ahead. Closing line and price are what the bet would have faced.

Row: [day, pid, stat, pos_rank, pace_rank, def_rank, line_vs_base, over(1/0), over_decimal, under_decimal]
Pushes and DNPs are left out. Features for a past day never change, so only days not already in the file are computed.
Writes docs/data/matchup_hist.json nightly (run.py nightly -> build()). Pure Python: the runner has no numpy."""
import glob
import json
import os
from datetime import date, datetime, timezone

from . import config, store
from . import nba_data as nd
from . import nba_model as nm

FILE = "matchup_hist.json"
STATS = ("pts", "reb", "ast", "tpm", "pra", "ra")
MIN_GAMES = 5            # a player needs this many games on record before the as-of-day model knows him


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _day_rows(day, props):
    """Feature rows for one graded day."""
    d = date.fromisoformat(day)
    m = nm.Model(nd.season_of(d), asof=day)
    out = []
    for r in props.values():
        s = r.get("stat")
        c = r.get("c") or r.get("o")
        if s not in STATS or r.get("res") not in ("over", "under") or not c or len(c) < 3 or not c[1] or not c[2]:
            continue
        pid, opp = str(r.get("pid")), r.get("opp")
        pl = m.players.get(pid)
        To = m.teams.get(opp)
        if not pl or not To or len(m.by_player.get(pid, [])) < MIN_GAMES:
            continue
        line = c[0]
        base = m.baseline_rate(pid, s) * m.minutes(pid)
        lvb = round((line - base) / base, 3) if base > 0 else None
        out.append([day, pid, s, m.dvp_rank.get((opp, pl["role"], s), 0), To.get("pace_rank", 0), To.get("drtg_rank", 0), lvb,
                    1 if r["res"] == "over" else 0, round(c[1], 3), round(c[2], 3)])
    return out


def build(log=print, data_dir=None):
    d = data_dir or os.path.join(str(config.DATA), "dk")
    old = _load(os.path.join(str(config.DATA), FILE)) or {}
    rows = [r for r in old.get("rows", [])]
    have = {r[0] for r in rows}
    added = 0
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        db = _load(f)
        if not db or not db.get("graded") or db.get("date") in have:
            continue
        try:
            new = _day_rows(db["date"], db.get("props") or {})
        except Exception as e:                      # one bad day must not stop the rest
            log(f"matchup_hist: {db.get('date')} skipped: {e}")
            continue
        rows += new
        have.add(db["date"])
        added += len(new)
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "from": rows[0][0] if rows else None, "days": len({r[0] for r in rows}), "n": len(rows), "rows": rows,
           "cols": ["day", "pid", "stat", "pos", "pace", "def", "line_vs_base", "over", "over_dec", "under_dec"]}
    store.save(FILE, out, compact=True)
    log(f"matchup_hist: {len(rows):,} graded props over {out['days']} days (+{added:,} new)")
    return out
