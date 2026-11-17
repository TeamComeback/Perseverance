"""Perseverance City's live status (docs/data/city_now.json): which desk did what, in order, during this run.

The Dispatcher marks each step as the run reaches it (`mark(desk, text)`), so the city page can show the run desk by
desk as soon as the run's files land, and say when the last run started and finished.

Quiet steps (Nov 17, owner: "the only time things are firing are when changes/updates or actions are being taken"):
a step that only checked something and found nothing to do is marked `quiet=True`. The city shows it in the log but
doesn't fire it, and a run whose steps are all quiet is logged as one "checked in, nothing changed" line. Every run
is also appended to docs/data/city_log.json (the city's activity log, the last 36 hours). Local file only: no network, no
credits, no extra pushes (it is saved with the rest of the run's data). It must never break a run: every call swallows
its own errors. Desk ids are the ones in docs/city/cast.json."""
import json
import os
import time
from datetime import datetime, timezone

from . import config

SKIP = ("probe", "manual-issue")          # not city work worth showing: a signal probe, the Chairman's own bet form
_S = {"on": False, "t0": 0.0, "doc": None}


def _iso(t=None):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write():
    try:
        p = config.DATA / "city_now.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_S["doc"], separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass


def start(mode):
    try:
        if mode in SKIP:
            return
        _S.update(on=True, t0=time.time())
        _S["doc"] = {"mode": mode, "started": _iso(_S["t0"]), "finished": None, "secs": None,
                     "gh_run": os.environ.get("GITHUB_RUN_ID"), "steps": []}
        mark("dispatcher", quiet=True, text={"auto": "Woke on the timer: working out what is due",
                            "live": "Woke for live lines", "injwatch": "Woke for the injury watch",
                            "research": "Woke for a research refresh", "props": "Woke for the props run",
                            "props-backup": "Woke for the props backup", "nightly": "Woke for overnight grading",
                            "briefing": "Woke for the 7am Daily Briefing", "cycle": "Woke for a market cycle"}.get(mode, f"Woke for {mode}"))
    except Exception:
        pass


def mark(desk, text, quiet=False):
    """This desk starts working now. Repeats of the same desk and text in a row are ignored.
    quiet: it only checked and found nothing to act on (logged, not fired)."""
    try:
        if not _S["on"]:
            return
        st = _S["doc"]["steps"]
        if st and st[-1]["desk"] == desk and st[-1]["text"] == text:
            return
        if len(st) >= 40:
            return
        step = {"desk": desk, "text": str(text)[:160], "at": round(time.time() - _S["t0"], 1)}
        if quiet:
            step["q"] = 1
        st.append(step)
        _write()
    except Exception:
        pass


def finish():
    try:
        if not _S["on"]:
            return
        now = time.time()
        _S["doc"]["finished"] = _iso(now)
        _S["doc"]["secs"] = round(now - _S["t0"], 1)
        _S["doc"]["idle"] = not any(not x.get("q") for x in _S["doc"]["steps"])
        _write()
        _log(_S["doc"])
        _S["on"] = False
    except Exception:
        pass


LOG_H = 36            # hours of runs kept in city_log.json
LOG_MAX = 300


def _log(doc):
    """Append this run to the city's activity log. Idle runs keep only their first line."""
    try:
        p = config.DATA / "city_log.json"
        try:
            lg = json.loads(p.read_text())
        except Exception:
            lg = {"runs": []}
        run = {k: doc.get(k) for k in ("mode", "started", "finished", "secs", "gh_run", "idle")}
        run["steps"] = doc["steps"][:1] if doc.get("idle") else doc["steps"]
        cut = time.time() - LOG_H * 3600
        keep = [r for r in lg.get("runs", []) if r.get("started") and
                datetime.strptime(r["started"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() > cut]
        lg = {"updated": doc.get("finished"), "runs": (keep + [run])[-LOG_MAX:]}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(lg, separators=(",", ":")))
        tmp.replace(p)
    except Exception:
        pass
