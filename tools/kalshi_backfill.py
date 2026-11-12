"""One-time: rebuild Kalshi's pregame lines for past days we have DraftKings files for, from Kalshi's hourly price
history (free), so the DraftKings-vs-Kalshi grading starts with a week of games instead of none.
Price used: the last hourly bid/ask before tip-off.   python3 -m tools.kalshi_backfill"""
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from agent import dk, kalshi, store, research   # noqa: E402

B = "https://api.elections.kalshi.com/trade-api/v2"


def get(u):
    for i in range(4):
        try:
            return kalshi._get(u)
        except Exception as e:
            if i == 3:
                raise
            time.sleep(2 + 3 * i)


def day_markets(ds, games):
    dc = kalshi.datecode(date.fromisoformat(ds))
    raw = {k: [] for k in kalshi.SERIES}
    for g in games:
        h, a = kalshi.CODE.get(g["home"], g["home"]), kalshi.CODE.get(g["away"], g["away"])
        for k, s in kalshi.SERIES.items():
            for code in (f"{a}{h}", f"{h}{a}"):
                d = get(f"{B}/markets?event_ticker={s}-{dc}{code}&limit=1000")
                if d.get("markets"):
                    raw[k] += d["markets"]
                    break
            time.sleep(0.08)
    return raw


def price_at_tip(raw, starts):
    """Replace each market's bid/ask with the last hourly bid/ask before its game's tip-off."""
    ms = [m for v in raw.values() for m in v]
    by_end = {}
    for m in ms:
        by_end.setdefault(starts[m["event_ticker"].split("-", 1)[1]], []).append(m)
    for st, group in by_end.items():
        end = int(st.timestamp())
        for i in range(0, len(group), 50):
            chunk = group[i:i + 50]
            d = get(f"{B}/markets/candlesticks?market_tickers={','.join(m['ticker'] for m in chunk)}"
                    f"&start_ts={end - 4 * 3600}&end_ts={end}&period_interval=60")
            cs = {x.get("market_ticker") or x.get("ticker"): x.get("candlesticks") or [] for x in d.get("markets") or []}
            for m in chunk:
                c = [x for x in cs.get(m["ticker"], []) if x["end_period_ts"] <= end]
                if c:
                    m["yes_bid_dollars"] = c[-1]["yes_bid"].get("close_dollars")
                    m["yes_ask_dollars"] = c[-1]["yes_ask"].get("close_dollars")
                else:
                    m["yes_bid_dollars"] = m["yes_ask_dollars"] = None
            time.sleep(0.1)


def main():
    today = research.et_today().isoformat()
    for f in sorted((dk.config.DATA / dk.DIR).glob("*.json")):
        ds = f.stem
        if ds >= today or (kalshi.config.DATA / kalshi.path(ds)).exists():
            continue
        db = dk.load(ds)
        games = [{"espn_id": e, "home": G["home"], "away": G["away"], "start": G["start"]} for e, G in db["games"].items()]
        raw = day_markets(ds, games)
        dc = kalshi.datecode(date.fromisoformat(ds))
        starts = {}
        for g in games:
            st = datetime.fromisoformat(g["start"].replace("Z", "+00:00"))
            h, a = kalshi.CODE.get(g["home"], g["home"]), kalshi.CODE.get(g["away"], g["away"])
            starts[f"{dc}{a}{h}"] = starts[f"{dc}{h}{a}"] = st
        price_at_tip(raw, starts)
        players = {r["pid"]: {"name": r["name"], "team": r["team"]} for r in db["props"].values()}
        G, P = kalshi.build(raw, games, players, db, date.fromisoformat(ds))
        out = {"date": ds, "backfill": "last hourly price before tip", "games": {}, "props": {}}
        for eid, rec in G.items():
            g = db["games"][eid]
            out["games"][eid] = {"home": g["home"], "away": g["away"], "live": [], "pre": rec, "open": kalshi._strip(rec)}
        out["props"] = P
        out["updated"] = store.iso(store.now())
        store.save(kalshi.path(ds), out, compact=True)
        print(f"{ds}: {sum(len(v) for v in raw.values())} markets, {len(G)} games, {len(P)} props "
              f"({sum(1 for r in P.values() if r.get('p') is not None)} at DraftKings' line)", flush=True)
    print(json.dumps(kalshi.grade(log=print), indent=1)[:3000])


if __name__ == "__main__":
    main()
