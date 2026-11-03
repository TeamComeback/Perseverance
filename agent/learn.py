"""The learning loop. Every night, after grading, the agent reads its own track record and refines the
inputs it controls, in small steps with a minimum sample and hard bounds, and writes every change to the
journal. What it learns is saved in tuning.json and used by the next research run, projections and sims.

  stat_factor   projections running high/low by stat (projection vs actual box score, last 30 days)
  min_delta     minutes running high/low for starters and for bench players
  sim_anchor    how hard the sim leans on the Vegas team total (more if Vegas totals beat the sim's)
  factor_w      how much each conviction factor counts (by its lift in the conviction track record)
  (the model-vs-market blend weight is tuned in scoreboard.py)

It also files a permanent monthly archive (data/archive/YYYY-MM.json) of every graded sim and line,
so nothing ages out of the working files."""
import json
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

    # 1) projection level by stat
    for s in STATS:
        n, sp, sa = (W.get("proj_" + s) or [0, 0, 0])[:3]
        if n < 150 or sp <= 0:
            continue
        raw = sa / sp
        step = (raw - 1) * n / (n + 600) * 0.5
        prev = tune["stat_factor"].get(s, 1.0)
        new = round(max(0.85, min(1.15, prev * (1 + step))), 4)
        if abs(new - prev) >= 0.003:
            tune["stat_factor"][s] = new
            changes.append(f"{s.upper()} projections ran {(1 / raw - 1) * 100:+.1f}% vs actual over {int(n)} player-games: level x{new:.3f}")

    # 2) minutes for starters and bench
    for grp in ("starter", "bench"):
        n, sp, sa = (W.get("min_" + grp) or [0, 0, 0])[:3]
        if n < 120:
            continue
        d = (sa - sp) / n
        prev = tune["min_delta"].get(grp, 0.0)
        new = round(max(-3.0, min(3.0, prev + d * n / (n + 400) * 0.5)), 2)
        if abs(new - prev) >= 0.1:
            tune["min_delta"][grp] = new
            changes.append(f"{grp} minutes were off by {-d:+.1f} on average over {int(n)} player-games: correction {new:+.1f} min")

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
