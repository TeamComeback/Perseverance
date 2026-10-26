"""Perseverance — paper sports-betting agent. All money here is fake."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("PERSEVERANCE_DATA", ROOT / "docs" / "data"))

# ---- The race ---------------------------------------------------------------
START_BANKROLL = 100.0
MIN_STAKE = 5.0            # the one fixed rule: every bet is at least $5 (agent and you)
GOAL = 1000.0
BUST_LINE = 5.0           # cash below this with nothing open = run over
AUTO_RESTART = True        # after a bust or a win, start a new run (lessons carry over)

# ---- The Odds API budget (free tier) ---------------------------------------
API_BASE = "https://api.the-odds-api.com/v4"
API_KEY = os.environ.get("ODDS_API_KEY", "")
MOCK = os.environ.get("ODDS_MOCK") == "1"
MONTHLY_CREDITS = int(os.environ.get("ODDS_MONTHLY_CREDITS", "500"))
RESERVE_CREDITS = 25       # never spend below this — the hard floor
RUNS_PER_DAY = 3           # scheduled scans; used to spread credits across the month
REGIONS = "us"             # one region = 1 credit per market per sport

# ---- Market reading ---------------------------------------------------------
MIN_BOOKS = 4              # books needed before we trust a consensus price
MAX_EV = 0.20              # an "edge" bigger than this is a stale/bad line, skip it
MAX_ARB = 0.05             # real arbs are small; a bigger one is almost always a bad line
WINDOW_MIN_H = 0.25        # only bet games starting 15 min ...
WINDOW_MAX_H = 36          # ... to 36 h from now
GRADE_AFTER_H = 3.5        # look for a final score this long after start
VOID_AFTER_D = 4           # no final after this many days = void (stake back)

SPORT_PRIORITY = {
    "americanfootball_nfl": 1.0, "basketball_nba": 1.0, "icehockey_nhl": 0.9,
    "baseball_mlb": 0.9, "americanfootball_ncaaf": 0.8, "basketball_ncaab": 0.7,
    "soccer_epl": 0.7, "soccer_uefa_champs_league": 0.7, "soccer_spain_la_liga": 0.6,
    "soccer_italy_serie_a": 0.6, "soccer_germany_bundesliga": 0.6, "soccer_usa_mls": 0.5,
    "mma_mixed_martial_arts": 0.5, "basketball_wnba": 0.6, "americanfootball_cfl": 0.4,
}
DEFAULT_PRIORITY = 0.3

def markets_for(sport_key: str) -> str:
    if sport_key.startswith("mma") or sport_key.startswith("boxing"):
        return "h2h"
    if sport_key.startswith("soccer"):
        return "h2h,totals"
    return "h2h,spreads,totals"

# ---- Starting strategy (the agent rewrites these as it learns) -------------
DEFAULT_PARAMS = {
    "min_edge": 0.03,          # minimum expected value to take a bet
    "kelly_mult": 0.25,        # fraction of full Kelly
    "max_bet_pct": 0.08,       # largest single stake as % of equity
    "max_exposure_pct": 0.45,  # most of the bankroll that can be riding at once
    "max_bets_per_run": 6,
    "max_dec": 8.0,            # longest odds it will take on a single (+700)
    "arb_min": 0.005,          # minimum locked-in profit for an arbitrage
    "arb_pct": 0.20,           # bankroll share put into one arb
    "parlay_enabled": True,
}
# Hard guardrails: the agent (or the overnight strategist) can't go outside these.
# Wide on purpose: the strategy is the agent's to reshape. These only stop nonsense values.
BOUNDS = {
    "min_edge": (0.0, 0.15), "kelly_mult": (0.05, 1.0), "max_bet_pct": (0.05, 0.60),
    "max_exposure_pct": (0.10, 1.0), "max_bets_per_run": (1, 25), "max_dec": (1.5, 51.0),
    "arb_min": (0.0, 0.05), "arb_pct": (0.05, 0.60),
}

PLAYBOOKS = {
    "value_fav": "Favorite priced above the market's fair odds",
    "value_dog": "Underdog priced above the market's fair odds",
    "longshot": "Big underdog with a pricing gap",
    "arb": "Opposite sides at different books that lock a profit",
    "parlay_ev": "Two +EV legs from different games, edges compounded",
}

# ---- Optional overnight strategist (Claude) ---------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
STRATEGIST_MODEL = os.environ.get("STRATEGIST_MODEL", "claude-sonnet-5-5")
