# Perseverance

A paper sports-betting agent that races to turn **$100 into $1,000**, plus your own $100 paper book for the bets you like. All money is fake.

Named to sit next to Curiosity and Ingenuity.

## How the agent bets

1. **Picks where to look (free calls).** It lists every in-season sport and its upcoming games, then ranks them by priority, by how well it has done in that sport, and by how long since it last checked.
2. **Reads the board (paid calls).** It pulls moneylines, spreads and totals from every US book for the top sports its credit budget allows.
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
