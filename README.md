# Perseverance

A paper sports-betting agent that races to turn **$100 into $1,000**, plus your own $100 paper book for the bets you like. All money is fake.

Named to sit next to Curiosity and Ingenuity.

> **Owner rule (do not change):** the agent only uses **DraftKings, FanDuel and Caesars**. Odds are requested from those three books, prices are compared across them, bets are placed at them, and closing lines for CLV come from them. The list is `ALLOWED_BOOKS` in `agent/config.py`. It is a fixed constant, not a tunable setting, so neither the agent nor the overnight strategist may change it. Bets placed before this rule keep their original book and settle normally. The $5 minimum bet still applies.

## How the agent bets

1. **Picks where to look (free calls).** It lists every in-season sport and its upcoming games, then ranks them by priority, by how well it has done in that sport, and by how long since it last checked.
2. **Reads the board (paid calls).** It pulls moneylines, spreads and totals from the three allowed books for the top sports its credit budget allows.
3. **Finds edges:**
   - **Value**: it removes the vig from each book to get a fair price, averages the other books into a consensus, and bets the best price when it beats that consensus by the minimum edge.
   - **Arbitrage**: opposite sides at different books that lock in a profit no matter who wins.
   - **+EV parlays**: two value legs from different games, so the edges multiply.
4. **Sizes bets** with fractional Kelly, within caps on any single bet and on total money at risk. **Every bet is at least $5**, for the agent and for you. That's the one fixed rule; the agent and the strategist can reshape everything else.
5. **Grades** every bet from final scores and tracks **closing-line value**, which shows whether it beat the market before the game started.
6. **Adapts** after every grading pass:
   - It raises or lowers its minimum edge based on closing-line value.
   - It cuts bet size when it's in a drawdown and presses a little at new highs.
   - It leans into playbooks that are working and benches the ones that aren't, but still gives benched ones the odd exploratory bet.
   - Every change is written to the journal with the reason.
7. **Overnight strategist (optional).** With an `ANTHROPIC_API_KEY` secret set, Claude reviews the numbers each night and can retune the settings, including turning parlays on or off and shifting which sports it focuses on.

## NBA matchup props (evening run, ~6pm ET)

The agent hunts player props where the matchup moves a player away from his own norm: a sharpshooter against a team that leaks threes to his position (over), or a big against an elite interior defense (under).

- **Data (free, ESPN):** every box score from 2025-26 and this season, kept in `data/nba/`, refreshed nightly. Injury report checked each run; anyone listed Out or Doubtful is skipped.
- **Signals**, all measured as how much players beat or miss their *own* per-minute norm, shrunk toward neutral when the sample is small:
  - **Defense vs position**: how the opponent treats point guards, wings, bigs, etc. (positions derived from each player's stats).
  - **Similar players**: how players with his statistical profile have done against this opponent.
  - **His own history** against this opponent.
  - **Home/away split**, plus team defense, offense and pace ranks shown for context.
- **Projection** = per-minute rate × projected minutes × matchup multiplier, turned into an over/under probability.
- **Credits:** the model ranks the whole slate for free, then buys prop odds (1 credit per stat per game) only for the games holding the strongest spots. The evening run gets 3 scans' worth of the day's credits.
- **Betting:** model probability is blended with the market's no-vig price (`prop_model_weight`, starts at 40%); the bet must point the same way as the matchup and clear `prop_min_edge` (starts at 4%). Playbooks `matchup_over` and `matchup_under` are tracked separately so the agent learns which side works.
- **Grading** comes from the ESPN box score; a player who doesn't play is voided, as books do.
- The agent and the overnight strategist can retune every weight (`w_dvp`, `w_sim`, `w_h2h`, `matchup_strength`).

**Tested walk-forward on 2025-26** (`tests/backtest_matchups.py`, `tests/calibration.py`): in the softest fifth of matchups players beat their norms by roughly 4-11%, in the toughest fifth they fell short in rebounds, assists and threes. With lines set at a player's norm, unders the model flagged hit 62% vs a 57% base rate, overs 50% vs 43%. Probabilities were calibrated so a predicted 45% came in at 45%. That's a modest edge, and books already price some of it, which is why the model is blended with the market rather than trusted alone.

The **NBA matchups** panel on the dashboard lists tonight's strongest spots with the reasons, and a Bet this button for any line the agent priced.

**Live tracking:** every open bet shows when it was placed, a game-progress bar, the current score, time remaining and whether it is winning, covering or on pace. Props also show the player's live stat against the line. It comes from ESPN's free feed, fetched by your browser every 30 seconds, so it costs no Odds API credits. The **Time log** panel lists every bet placed and settled, newest first.

**Post-mortems:** every settled bet records the final score, the margin against the line, and why it won or lost. Props add the player's box-score line, minutes played vs projected, the model's projection and error in standard deviations, and whether the matchup read was right. Each review carries tags such as near miss, blowout, minutes short, ordinary variance, model miss and beat/lost closing line. The tags are tallied per playbook so patterns show up over time.

A run ends at $1,000 (won) or below $5 (busted). A new $100 run then starts automatically, and everything the agent has learned carries over.

## Credit budget (free tier: 500/month)

- Listing sports and games is free. Odds cost 1 credit per market (2 or 3 per sport). Scores cost 2 per sport.
- Each scan gets an equal share of what's left for the rest of the month.
- **The agent never spends below 25 credits**, so the limit can't be hit before the monthly reset.
- In a typical month that's about 5 credits per scan, which covers 1 or 2 sports. A paid Odds API tier unlocks more sports per scan and player props.

## Setup (about 5 minutes, works from an iPhone browser)

1. Create the repo `TeamComeback/Perseverance` and upload these files.
2. Get a free key at the-odds-api.com. Add it under **Settings → Secrets and variables → Actions** as `ODDS_API_KEY`.
   - Optional: add `ANTHROPIC_API_KEY` to turn on the overnight strategist.
3. **Settings → Pages**: deploy from branch `main`, folder `/docs`.
4. **Actions** tab: enable workflows, open **Agent**, then **Run workflow** to start the first scan.
5. Open `https://teamcomeback.github.io/Perseverance/`. On your phone, use Share → Add to Home Screen to save it like an app.

## Your bets

- **From the board:** tap any price on the dashboard to open a prefilled issue, then submit it. The bet is logged at that price and grades itself from the final score.
- **Custom bets** (props, same-game parlays, anything): use **+ Log a custom bet**, then comment `/settle won`, `/settle lost` or `/settle push` on the issue when it's decided. `/cancel` refunds the stake.
- Only you and other repo members can log or settle bets.

## Schedule (ET)

| Time | What runs |
|---|---|
| ~5am | Grading and the overnight strategist |
| ~11am | Scan |
| ~4pm | Scan |
| ~7:30pm | Scan |

These times shift by an hour when daylight saving time changes.

## Test offline

```
python tests/simulate.py 30     # 30 simulated days on a fake market, no API key needed
```

## Files

- `agent/`: the agent (Python standard library only)
- `docs/`: the dashboard
- `docs/data/`: the books (JSON)
