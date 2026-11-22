"""The learning loop. Every night, after grading, the agent reads its own track record and refines the
inputs it controls, in small steps with a minimum sample and hard bounds, and writes every change to the
journal. What it learns is saved in tuning.json and used by the next research run, projections and sims.

  stat_factor   projections running high/low by stat (projection vs actual box score, last 30 days)
  min_delta     minutes running high/low for starters and for bench players
                (model audit Nov 22: both are now estimated from the RAW projections, i.e. the shipped number with the
                correction that was in force that day taken back out, so a correction is never multiplied onto itself.
                Before, each night multiplied yesterday's factor by the same 30-day error, which kept pushing after the
                error was fixed: points went from +6% high to 6-13% low and starter minutes sat at the -3.0 clamp while
                starters played ~2 minutes MORE than projected.)
  sim_anchor    how hard the sim leans on the Vegas team total (more if Vegas totals beat the sim's)
  factor_w      how much each conviction factor counts (by its lift in the conviction track record)
  (the model-vs-market blend weight is tuned in scoreboard.py)

It also files a permanent monthly archive (data/archive/YYYY-MM.json) of every graded sim and line,
so nothing ages out of the working files."""
import json
import re
from datetime import timedelta

from . import config, store

TUNE = "tuning.json"
STATS = ("pts", "reb", "ast", "tpm", "pra", "ra")


def _window(sdb, days=30):
    tot = {}
    for d in sorted(sdb.get("daily", {}))[-days:]:
        for k, v in sdb["daily"][d].items():
            t = tot.setdefault(k, [0] + [0.0] * (len(v) - 1))
            for i, x in enumerate(v):
                t[i] += x
    return tot


def run(state, note):
    from . import simtrack, track
    sdb, tdb = simtrack.load(), track.load()
    tune = store.load(TUNE, {"stat_factor": {}, "min_delta": {}, "sim_anchor": 0.75, "factor_w": {}, "history": []})
    W = _window(sdb)
    changes = []

    days = sorted(sdb.get("daily", {}))[-30:]
    applied = tune.setdefault("applied", {})

    def in_force(d):
        """The corrections the projections shipped with on day d: recorded since Nov 22, rebuilt from the change log before."""
        if d in applied:
            return applied[d]
        sf, md = {}, {}
        for h in sorted(tune.get("history", []), key=lambda h: h["ts"]):
            if h["ts"][:10] > d:
                break
            for c in h["changes"]:
                m = re.match(r"(\w+) projections ran .* level x([\d.]+)", c)
                if m:
                    sf[m.group(1).lower()] = float(m.group(2))
                m = re.match(r"(starter|bench) minutes were off .* correction ([+-][\d.]+) min", c)
                if m:
                    md[m.group(1)] = float(m.group(2))
        return {"stat_factor": sf, "min_delta": md}

    def wt(d):                                   # recent days count more (half-life 10 days)
        return 0.5 ** ((len(days) - 1 - days.index(d)) / 10)

    # 1) projection level by stat: actual / raw projection, recency-weighted, shrunk toward 1 by sample size
    for s in STATS:
        n = sw_a = sw_r = 0.0
        for d in days:
            v = sdb["daily"][d].get("proj_" + s)
            if not v or v[0] <= 0 or v[1] <= 0:
                continue
            f = (in_force(d).get("stat_factor") or {}).get(s, 1.0) or 1.0
            w = wt(d)
            n += v[0]
            sw_a += w * v[2]
            sw_r += w * v[1] / f
        if n < 150 or sw_r <= 0:
            continue
        ratio = sw_a / sw_r
        prev = tune["stat_factor"].get(s, 1.0)
        new = round(max(0.85, min(1.15, 1 + (ratio - 1) * n / (n + 600))), 4)
        if abs(new - prev) >= 0.003:
            tune["stat_factor"][s] = new
            changes.append(f"{s.upper()} raw projections ran {(1 / ratio - 1) * 100:+.1f}% vs actual over {int(n)} player-games: "
                           f"level x{new:.3f} (was x{prev:.3f})")

    # 2) minutes for starters and bench: actual minus raw projected minutes
    for grp in ("starter", "bench"):
        n = sw_n = sw_d = 0.0
        for d in days:
            v = sdb["daily"][d].get("min_" + grp)
            if not v or v[0] <= 0:
                continue
            md = (in_force(d).get("min_delta") or {}).get(grp, 0.0) or 0.0
            w = wt(d)
            n += v[0]
            sw_n += w * v[0]
            sw_d += w * (v[2] - (v[1] - md * v[0]))
        if n < 120 or sw_n <= 0:
            continue
        d_ = sw_d / sw_n
        prev = tune["min_delta"].get(grp, 0.0)
        new = round(max(-3.0, min(3.0, d_ * n / (n + 400))), 2)
        if abs(new - prev) >= 0.1:
            tune["min_delta"][grp] = new
            changes.append(f"{grp} minutes (raw) were off by {-d_:+.1f} on average over {int(n)} player-games: "
                           f"correction {new:+.1f} min (was {prev:+.1f})")

    # what ships from today on (so the next fit can take it back out exactly)
    try:
        from zoneinfo import ZoneInfo
        today = store.now().astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        today = store.now().date().isoformat()
    applied[today] = {"stat_factor": dict(tune["stat_factor"]), "min_delta": dict(tune["min_delta"])}
    tune["applied"] = {d: applied[d] for d in sorted(applied)[-60:]}

    # 3) how hard the sim leans on Vegas team totals
    n, sim_abs, veg_abs = (W.get("totals") or [0, 0, 0])[:3]
    if n >= 20:
        prev = tune.get("sim_anchor", 0.75)
        gap = (sim_abs - veg_abs) / n
        new = prev
        if gap > 1.0:
            new = min(1.0, prev + 0.05)
        elif gap < -1.0:
            new = max(0.4, prev - 0.05)
        if new != prev:
            tune["sim_anchor"] = round(new, 2)
            changes.append(f"game totals missed by {sim_abs / n:.1f} (sim) vs {veg_abs / n:.1f} (Vegas) over {int(n)} games: "
                           f"Vegas anchoring {prev:.2f} -> {new:.2f}")

    # 4) conviction factor weights from their lift in the track record
    fa = tdb.get("factors", {})
    base = fa.get("_all")
    if base and base[0] >= 500:
        b = base[1] / base[0]
        per = {}
        for code, (n, h) in fa.items():
            if code == "_all" or n < 50:
                continue
            f, sign = code.split(":")
            per.setdefault(f, []).append((n, (h / n - b) * int(sign)))
        for f, xs in per.items():
            N = sum(n for n, _ in xs)
            if N < 200:
                continue
            useful = sum(n * u for n, u in xs) / N
            target = max(0.0, min(2.0, 1 + useful * 10))
            new = round(1 + (target - 1) * N / (N + 600), 2)
            prev = tune["factor_w"].get(f, 1.0)
            if abs(new - prev) >= 0.05:
                tune["factor_w"][f] = new
                changes.append(f"conviction factor '{f}' {'helped' if useful > 0 else 'hurt'} by {useful * 100:+.1f} pts of hit rate "
                               f"over {N} plays: weight {prev:.2f} -> {new:.2f}")

    tune["updated"] = store.iso(store.now())
    if changes:
        tune["history"] = (tune.get("history", []) + [{"ts": tune["updated"], "changes": changes}])[-60:]
        note(state, "adapt", "Learning loop: " + "; ".join(changes) + ".")
    store.save(TUNE, tune)
    archive(sdb)
    return changes


def archive(sdb):
    """Permanent monthly record: every graded sim game and every graded line."""
    from . import lines as linedb
    root = config.ROOT / "data" / "archive"
    root.mkdir(parents=True, exist_ok=True)
    months = {}
    for day, games in sdb.get("graded", {}).items():
        for gid, rec in games.items():
            months.setdefault(day[:7], {}).setdefault("sims", {})[f"{day}|{gid}"] = rec
    for k, e in linedb.load()["entries"].items():
        if e.get("result"):
            months.setdefault(e["date"][:7], {}).setdefault("lines", {})[k] = e
    for m, add in months.items():
        f = root / f"{m}.json"
        cur = json.loads(f.read_text()) if f.exists() else {"sims": {}, "lines": {}}
        for sec, items in add.items():
            cur.setdefault(sec, {}).update(items)
        f.write_text(json.dumps(cur, separators=(",", ":")))
