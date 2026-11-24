"""One-time backfill of line history (Nov 24 2026). Every agent refresh was committed, so the movement of each
DraftKings line is in git: for each docs/data/dk/<day>.json, replay its commits oldest first and keep a point
[time, line, over, under] (games: [time, sp, sp_h, sp_a, tot, ov, un, ml_h, ml_a]) whenever the numbers changed.
Writes docs/data/dkh/<day>.json (the agent keeps adding to it from then on). Safe to re-run: it rebuilds from scratch.   python3 tools/dk_history.py [day ...]"""
from datetime import datetime, timezone
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_KEYS = ("sp", "sp_h", "sp_a", "tot", "ov", "un", "ml_h", "ml_a")


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def add(h, vals, ts):
    if not h or h[-1][1:] != list(vals):
        h.append([ts, *vals])


def rebuild(path):
    rel = os.path.relpath(path, ROOT)
    commits = [x.split("\t") for x in git("log", "--reverse", "--format=%H\t%cI", "--", rel).splitlines() if x]
    cur = json.load(open(path))
    out = {"props": {}, "games": {}}
    for sha, ts in commits:
        try:
            snap = json.loads(git("show", f"{sha}:{rel}"))
        except Exception:
            continue
        t = datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for k, sp in (snap.get("props") or {}).items():
            if k in cur["props"] and sp.get("c") and sp["c"][0] is not None:
                add(out["props"].setdefault(k, []), sp["c"][:3], t)
        for k, sg in (snap.get("games") or {}).items():
            if k in cur["games"] and sg.get("close"):
                add(out["games"].setdefault(k, []), [sg["close"].get(x) for x in GAME_KEYS], t)
    dst = os.path.join(ROOT, "docs/data/dkh", os.path.basename(path))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(out, open(dst, "w"), separators=(",", ":"))
    pts = sum(len(v) for v in out["props"].values())
    print(f"{os.path.basename(path)}: {len(commits)} snapshots -> {pts} prop points over {len(out['props'])} props, {os.path.getsize(dst) // 1024} KB")


if __name__ == "__main__":
    days = sys.argv[1:] or [os.path.basename(f)[:-5] for f in sorted(glob.glob(os.path.join(ROOT, "docs/data/dk/*.json")))]
    for d in days:
        rebuild(os.path.join(ROOT, f"docs/data/dk/{d}.json"))
