# Perseverance

A paper sports-betting agent that races to turn **$100 into $1,000**, plus your own $100 paper book for the bets you like. All money is fake.

Named to sit next to Curiosity and Ingenuity.

> **Owner rule (do not change):** the agent only uses the owner's book list: the New York-licensed sportsbooks The Odds API covers (**DraftKings, FanDuel, Caesars, BetMGM, BetRivers, Fanatics, theScore Bet, Hard Rock Bet**) plus the **Kalshi** and **Polymarket (US)** exchanges. Odds are requested from those books, prices are compared across them, bets are placed at them, and closing lines for CLV come from them. The list is `ALLOWED_BOOKS` in `agent/config.py`. It is a fixed constant, not a tunable setting, so neither the agent nor the overnight strategist may change it. The $5 minimum bet still applies.
>
> Notes on the list: ten books bill as one region, so a scan costs the same as before. Caesars and Fanatics only return odds on a **paid** Odds API plan. bet365 is licensed in NY but isn't in The Odds API's US list. Exchange prices are taken **net of trading fees** (Kalshi's 0.07 × p × (1 − p) per contract, assumed for Polymarket too). Polymarket's US platform blocks New York residents and New York is suing Kalshi over sports contracts, so treat both as price references for paper betting.

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

## Markets

NBA: **rebounds + assists, assists, threes, pts + reb + ast** player props, plus **moneylines, spreads, totals and team totals**. Moneylines, spreads and totals come from the regular scans. Props and team totals are bought per game in the evening run (team totals ride along when there's a credit for them) and bet against the multi-book consensus like the main board.

## Daily research (free, ~9:40am and ~4:45pm ET)

Every morning, and again before the evening run, the agent refreshes everything the dashboard reads, using only free sources:

- **Box scores** from ESPN (three seasons kept in `data/nba/`, including linescores, ESPN's pregame line, and this season's shot locations).
- **Projected and confirmed lineups from RotoWire** (starters, "may not play" list with injury status, profile links). RotoWire's projected minutes and stat projections are subscriber-only, so projections come from this model and each player card links to his RotoWire page.
- **Projections** for every rotation player on the slate in every stat, with matchup signals.
- **Defense vs position**, recomputed from every box score: how each team's opponents at each position do against their own per-minute norms.
- **League trends**: hot and cold players, minutes risers, pace changes, over/under and ATS records, second-half teams, softest and toughest matchups, back-to-backs, and how teams trailing by 10+ at halftime do in the second half.

The evening props run refreshes lineups again before pricing. If GitHub delays the 6pm run, a backup at ~7:15pm runs it instead (and does nothing if the first one happened).

## Prop Lab and player cards

The **Prop Lab** tab lists every projected player and stat, filterable by game, stat, over/under lean and priced lines, sortable by matchup, edge, projection or softest defense. Tap a player for the full card:

- Set any line (or start from the market line) and see hit rates for **L5, L10, L15, this season, H2H and home/away**.
- Filter the game log by window (L5 / L10 / L15 / this season / last season / all three seasons), **only vs tonight's opponent**, home/away, and **with or without** each of his top five teammates.
- **Defense vs position** for tonight's opponent, **head-to-head** games over three seasons, **similar players** (closest per-36 profiles) and how they did against this opponent, a **shot chart** with zone breakdown, RotoWire **lineup status** and profile link.
- **Save this prop**: opens a prefilled `Save:` issue with a snapshot of the card. Saved props grade themselves from the box score after the game, and the **Saved props** panel tallies the hit rate by stat. Comment `/remove` on the issue to delete one.

## Game stories

Every settled bet gets a game story from ESPN's free play-by-play, alongside the post-mortem: score by quarter, largest leads, lead changes, **scoring runs**, **momentum swings** (the biggest margin change inside any 8-minute stretch), the **closing stretch** (scoring plays in the last five minutes while within six), key players, and for props the player's line **quarter by quarter** (when he crossed the number, foul trouble, when his threes fell). Other sports get the line score, scoring plays, the go-ahead score that held, and leaders.

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

- Listing sports and games is free. Odds cost 1 credit per market (2 or 3 per sport). Scores cost 2 per sport. Props cost 1 per stat per game, team totals 1 per game.
- **Even spread:** what's left above the floor is divided by the days left in the month to set today's budget (New York days), and each run takes its share of what's left of today's budget. A skipped or cheap run leaves its credits for later, so nothing piles up and nothing runs dry before the reset.
- **The agent never spends below 25 credits.**
- On the free tier that's roughly 15 credits a day: a couple of sports per scan and one or two NBA games' props a night. A paid Odds API plan unlocks Caesars and Fanatics prices and props for the whole slate.

## Setup (about 5 minutes, works from an iPhone browser)

1. Create the repo `TeamComeback/Perseverance` and upload these files.
2. Get a free key at the-odds-api.com. Add it under **Settings → Secrets and variables → Actions** as `ODDS_API_KEY`.
   - Optional: add `ANTHROPIC_API_KEY` to turn on the overnight strategist.
3. **Settings → Pages**: deploy from branch `main`, folder `/docs`.
4. **Actions** tab: enable workflows, open **Agent**, then **Run workflow** to start the first scan.
5. Open `https://teamcomeback.github.io/Perseverance/`. On your phone, use Share → Add to Home Screen to save it like an app.

## Your bets (stored in the app)

Your $100 paper book and your saved props live **in the dashboard itself**, in your browser's storage on that device (the same approach as Ingenuity). No GitHub needed.

- **Log a bet:** the orange **+ Log a bet** button (any NBA player prop on the slate, or any custom bet), **+ Log this bet** on a player card, or tap any price on the board or in Team totals.
- **Grading:** props and game lines grade themselves in the app from ESPN box scores and final scores, every time you open it (and every two minutes while it's open). A player who doesn't play is voided. Custom bets get **Won / Lost / Push** buttons; any open bet can be cancelled for a refund. The $5 minimum and the $100 → $1,000 race rules apply.
- **Saved props:** **★ Save this prop** on a player card keeps it with a snapshot of the card; it grades itself after the game and the Saved props panel shows your hit rate by stat.
- **Backup:** Bets → **Your data · backup** → **Export backup**, and keep the file in iCloud Drive. iOS can clear website storage, so export now and then. **Restore from file** merges a backup back in (also how you move to a new phone). Each device keeps its own copy, so log from the same phone; Share → Add to Home Screen keeps it like an app.

The old GitHub-issue logging still works in the background but the dashboard no longer uses it.

## Schedule (ET)

| Time | What runs |
|---|---|
| ~5am | Grading, box scores, and the overnight strategist |
| ~9:40am | Research refresh (free) |
| ~11am | Scan |
| ~4pm | Scan |
| ~4:45pm | Research refresh with updated lineups (free) |
| ~6pm | NBA props + team totals (refreshes lineups first) |
| ~7:15pm | Props backup (only if the 6pm run didn't happen) |
| ~7:30pm | Scan |

These times shift by an hour when daylight saving time changes.

## Test offline

```
python tests/simulate.py 30     # 30 simulated days on a fake market, no API key needed
```

## Files

- `agent/`: the agent (Python standard library only). `research.py` daily research, `rotowire.py` lineups, `recap.py` game stories, `saved.py` saved props
- `docs/`: the dashboard (tabs: Today, Prop Lab, Bets, Trends, Agent)
- `docs/data/`: the books and research files (JSON); `docs/data/games/` holds one detail file per game on the slate
- `data/nba/`: box scores (three seasons) and this season's shot locations, one file per day
