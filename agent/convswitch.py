"""Owner-approved switch for the conviction model (framework Nov 15, part A).

Every night convmodel.compare() tests the line-side model against the current one on days neither has seen. The result
is saved to conv_switch.json and shown on the dashboard (Model > Health, and the Tonight status strip once it passes).
When the test has passed GATE["streak"] nights in a row, the agent sends one phone alert and the dashboard shows a
"Switch to the line-side model" button. The button opens a prefilled "Switch:" issue; the issue workflow runs
apply_issue(), which switches only if the latest test still passes. "Switch: back" returns to the current model.
Nothing switches on its own."""
from . import store

FILE = "conv_switch.json"


def load():
    return store.load(FILE, {"mode": "matchup", "streak": 0, "history": [], "switched": None}) or {}


def mode():
    return load().get("mode", "matchup")


def nightly(entries, log=print):
    from . import convmodel
    s = load()
    res = convmodel.compare(entries, log=log)
    today = store.now().date().isoformat()
    hist = [h for h in s.get("history", []) if h.get("d") != today]
    hist.append({"d": today, "pass": res.get("pass"), "n": res.get("n"), "ll": res.get("ll")})
    s["history"] = hist[-30:]
    streak = 0
    for h in reversed(s["history"]):
        if not h.get("pass"):
            break
        streak += 1
    s.update({"latest": res, "streak": streak, "updated": store.iso(store.now())})
    need = convmodel.GATE["streak"]
    s["ready"] = streak >= need
    if s["ready"] and s.get("mode") != "line" and not s.get("alerted"):
        try:
            from . import alerts
            ll = res.get("ll") or {}
            alerts.send([("conv-switch-ready", f"The line-side conviction model has passed its test {streak} nights running "
                          f"(log loss {ll.get('line')} vs {ll.get('current')} now, {res.get('n'):,} props). "
                          "Open Model > Health on the dashboard to review it and switch.")], title="Conviction model ready")
            s["alerted"] = store.iso(store.now())
        except Exception as e:
            log(f"[convswitch] alert failed: {e}")
    if not s["ready"]:
        s.pop("alerted", None)
    store.save(FILE, s)
    return s


def apply_issue(num, title):
    s = load()
    back = "back" in title.lower()
    if back:
        s["mode"] = "matchup"
        s["switched"] = {"to": "matchup", "at": store.iso(store.now()), "issue": num}
        store.save(FILE, s)
        store.gh_comment(num, "Switched back to the current conviction model. It takes effect from the next sims run.")
        store.gh_close(num)
        return
    res = s.get("latest") or {}
    if not s.get("ready") or not res.get("pass"):
        why = "; ".join(c["t"] for c in res.get("checks", []) if not c["ok"]) or "no test result yet"
        store.gh_comment(num, f"Not switched: the line-side model hasn't passed {s.get('streak', 0)}/{__import__('agent.convmodel', fromlist=['x']).GATE['streak']} "
                              f"nights in a row yet ({why}). You'll get an alert when it has.")
        store.gh_close(num)
        return
    s["mode"] = "line"
    s["switched"] = {"to": "line", "at": store.iso(store.now()), "issue": num, "ll": res.get("ll"), "n": res.get("n")}
    store.save(FILE, s)
    store.gh_comment(num, f"Switched to the line-side conviction model (log loss {res['ll']['line']} vs {res['ll']['current']}, "
                          f"{res['n']:,} props). Conviction scores and Best Bets use it from the next sims run; picks from before "
                          "today stay graded under the old model. Open a \"Switch: back\" issue to undo.")
    store.gh_close(num)
