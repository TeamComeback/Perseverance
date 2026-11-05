"""Model vs market: score the matchup model's over probabilities against the market's no-vig closing
probabilities on the same lines, using what actually happened. Log loss and Brier, lower is better.
The blend weight that would have scored best is what the agent moves prop_model_weight toward."""
import math

from . import config, store

MIN_N = 150          # graded lines before the agent trusts the scoreboard enough to retune


def graded(entries, days=None):
    out = []
    for e in entries.values():
        if e.get("result") not in ("over", "under") or e.get("p_model") is None or e.get("p_over") is None:
            continue
        out.append((e["p_model"], e["p_over"], 1 if e["result"] == "over" else 0, e["stat"], e.get("cv")))
    return out


def _ll(p, y):
    p = min(max(p, 0.02), 0.98)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def score(rows, w):
    if not rows:
        return None
    n = len(rows)
    res = {}
    for name, f in (("model", lambda m, k: m), ("market", lambda m, k: k), ("blend", lambda m, k: w * m + (1 - w) * k)):
        res[name] = {"ll": sum(_ll(f(m, k), y) for m, k, y, _, _ in rows) / n,
                     "brier": sum((f(m, k) - y) ** 2 for m, k, y, _, _ in rows) / n}
    best = min((sum(_ll(b * m + (1 - b) * k, y) for m, k, y, _, _ in rows) / n, b) for b in [i / 20 for i in range(21)])
    res["best_w"], res["best_ll"], res["n"] = best[1], best[0], n
    return res


def build(entries, w):
    rows = graded(entries)
    try:
        from . import dk
        rows += dk.scoreboard_rows()
    except Exception as e:
        print(f"[scoreboard] DraftKings rows failed: {e}")
    out = {"updated": store.iso(store.now()), "w": w, "all": score(rows, w), "by_stat": {}}
    for st in sorted({r[3] for r in rows}):
        out["by_stat"][st] = score([r for r in rows if r[3] == st], w)
    return out


def retune(state, sb, note):
    """Move prop_model_weight 30% of the way toward the weight that scored best, once there's enough data."""
    a = sb.get("all")
    if not a or a["n"] < MIN_N:
        return
    p = state["params"]
    cur = p.get("prop_model_weight", 0.4)
    lo, hi = config.BOUNDS["prop_model_weight"]
    new = round(max(lo, min(hi, cur + 0.3 * (a["best_w"] - cur))), 3)
    if abs(new - cur) >= 0.01:
        p["prop_model_weight"] = new
        note(state, "adapt", f"Model vs market on {a['n']} graded lines: the {a['best_w']:.0%} model blend scored best "
                             f"(log loss model {a['model']['ll']:.3f}, market {a['market']['ll']:.3f}). "
                             f"Moving the model's weight from {cur:.0%} to {new:.0%}.")
