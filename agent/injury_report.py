"""The NBA's official injury report (PDF, posted through the day). More authoritative and usually
fresher than ESPN's list, so it overrides it. Needs pypdf; without it this quietly returns nothing."""
import re
import urllib.request
from datetime import timedelta

from . import store
from . import nba_data as nd

URL = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{d}_{hh}_{mm}{ap}.pdf"
NAME = r"[A-Z][A-Za-z'.\-]+"
PAT = re.compile(rf"({NAME}(?: {NAME})?(?:, (?:Jr\.|Sr\.|II|III|IV))?), ({NAME}(?: {NAME})?) (Out|Questionable|Doubtful|Probable|Available)\b")


def latest_pdf(hours_back=14):
    now = store.now().astimezone(nd.ET)
    t = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    for i in range(hours_back * 4):
        x = t - timedelta(minutes=15 * i)
        url = URL.format(d=x.strftime("%Y-%m-%d"), hh=x.strftime("%I"), mm=x.strftime("%M"), ap=x.strftime("%p"))
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            if data[:4] == b"%PDF":
                return x, data
        except Exception:
            continue
    return None, None


def parse(data):
    import io
    from pypdf import PdfReader
    text = " ".join(" ".join((p.extract_text() or "").split()) for p in PdfReader(io.BytesIO(data)).pages)
    return [(last, first, status) for last, first, status in PAT.findall(text)]


def fetch(players, norm):
    """{pid: status} for players on the official report, matched to the model's roster by name."""
    try:
        when, data = latest_pdf()
        if not data:
            return {}, None
        rows = parse(data)
    except ImportError:
        print("[injury] pypdf not installed; using ESPN's list only")
        return {}, None
    except Exception as e:
        print(f"[injury] official report failed: {e}")
        return {}, None
    by_name = {}
    for pid, p in players.items():
        by_name.setdefault(norm(p["name"]), pid)
    out = {}
    for last, first, status in rows:
        last = re.sub(r",\s*(Jr\.|Sr\.|II|III|IV)$", "", last)
        cands = [f"{first} {last}"]
        if " " in last:                                   # the team name can run into the last name
            cands.append(f"{first} {last.split(' ', 1)[1]}")
        for c in cands:
            pid = by_name.get(norm(c))
            if pid:
                out[pid] = "" if status == "Available" else status
                break
    return out, store.iso(when.astimezone(store.now().tzinfo)) if when else None
