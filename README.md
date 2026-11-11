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

NBA: **rebounds + assists, assists, threes, pts + reb + ast** player props, plus **moneylines, spreads, totals and team totals**. Moneylines, spreads and totals come from the regular scans. Props and team totals are bought per game in the afternoon props run (team totals ride along when there's a credit for them) and bet against the multi-book consensus like the main board.

## Daily research (free, ~9:40am and ~4:45pm ET)

Every morning, and again before the afternoon props run, the agent refreshes everything the dashboard reads, using only free sources:

- **Box scores** from ESPN (three seasons kept in `data/nba/`, including linescores, ESPN's pregame line, and this season's shot locations).
- **Projected and confirmed lineups from RotoWire** (starters, "may not play" list with injury status, profile links). RotoWire's projected minutes and stat projections are subscriber-only, so projections come from this model and each player card links to his RotoWire page.
- **Projections** for every rotation player on the slate in every stat, with matchup signals.
- **Defense vs position**, recomputed from every box score: how each team's opponents at each position do against their own per-minute norms.
- **League trends**: hot and cold players, minutes risers, pace changes, over/under and ATS records, second-half teams, softest and toughest matchups, back-to-backs, and how teams trailing by 10+ at halftime do in the second half.

The props run refreshes lineups again before pricing. If GitHub delays the ~2pm run, a backup at ~3:15pm runs it instead (and does nothing if the first one happened). Times are winter (EST); add an hour in summer.

## Prop Lab and player cards

The **Prop Lab** tab lists every projected player and stat, filterable by game, stat, over/under lean and priced lines, sortable by matchup, edge, projection or softest defense. Tap a player for the full card:

- Set any line (or start from the market line) and see hit rates for **L5, L10, L15, this season, H2H and home/away**.
- Filter the game log by window (L5 / L10 / L15 / this season / last season / all three seasons), **only vs tonight's opponent**, home/away, and **with or without** each of his top five teammates.
- **Defense vs position** for tonight's opponent, **head-to-head** games over three seasons, **similar players** (closest per-36 profiles) and how they did against this opponent, a **shot chart** with zone breakdown, RotoWire **lineup status** and profile link.
- **Save this prop**: opens a prefilled `Save:` issue with a snapshot of the card. Saved props grade themselves from the box score after the game, and the **Saved props** panel tallies the hit rate by stat. Comment `/remove` on the issue to delete one.

## Game stories

Every settled bet gets a game story from ESPN's free play-by-play, alongside the post-mortem: score by quarter, largest leads, lead changes, **scoring runs**, **momentum swings** (the biggest margin change inside any 8-minute stretch), the **closing stretch** (scoring plays in the last five minutes while within six), key players, and for props the player's line **quarter by quarter** (when he crossed the number, foul trouble, when his threes fell). Other sports get the line score, scoring plays, the go-ahead score that held, and leaders.

## NBA matchup props (afternoon run, ~2pm ET)

The agent hunts player props where the matchup moves a player away from his own norm: a sharpshooter against a team that leaks threes to his position (over), or a big against an elite interior defense (under).

- **Data (free, ESPN):** every box score from 2025-26 and this season, kept in `data/nba/`, refreshed nightly. Injury report checked each run; anyone listed Out or Doubtful is skipped.
- **Signals**, all measured as how much players beat or miss their *own* per-minute norm, shrunk toward neutral when the sample is small:
  - **Defense vs position**: how the opponent treats point guards, wings, bigs, etc. (positions derived from each player's stats).
  - **Similar players**: how players with his statistical profile have done against this opponent.
  - **His own history** against this opponent.
  - **Home/away split**, plus team defense, offense and pace ranks shown for context.
- **Projection** = per-minute rate × projected minutes × matchup multiplier, turned into an over/under probability.
- **Credits:** the model ranks the whole slate for free, then buys prop odds (1 credit per stat per game) only for the games holding the strongest spots. The props run gets 3 scans' worth of the day's credits.
- **Betting:** model probability is blended with the market's no-vig price (`prop_model_weight`, starts at 40%); the bet must point the same way as the matchup and clear `prop_min_edge` (starts at 4%). Playbooks `matchup_over` and `matchup_under` are tracked separately so the agent learns which side works.
- **Grading** comes from the ESPN box score; a player who doesn't play is voided, as books do.
- The agent and the overnight strategist can retune every weight (`w_dvp`, `w_sim`, `w_h2h`, `matchup_strength`).

**Tested walk-forward on 2025-26** (`tests/backtest_matchups.py`, `tests/calibration.py`): in the softest fifth of matchups players beat their norms by roughly 4-11%, in the toughest fifth they fell short in rebounds, assists and threes. With lines set at a player's norm, unders the model flagged hit 62% vs a 57% base rate, overs 50% vs 43%. Probabilities were calibrated so a predicted 45% came in at 45%. That's a modest edge, and books already price some of it, which is why the model is blended with the market rather than trusted alone.

The **NBA matchups** panel on the dashboard lists tonight's strongest spots with the reasons, and a Bet this button for any line the agent priced.

**Live tracking:** every open bet shows when it was placed, a game-progress bar, the current score, time remaining and whether it is winning, covering or on pace. Props also show the player's live stat against the line. It comes from ESPN's free feed, fetched by your browser every 30 seconds, so it costs no Odds API credits. The **Time log** panel lists every bet placed and settled, newest first.

**Post-mortems:** every settled bet records the final score, the margin against the line, and why it won or lost. Props add the player's box-score line, minutes played vs projected, the model's projection and error in standard deviations, and whether the matchup read was right. Each review carries tags such as near miss, blowout, minutes short, ordinary variance, model miss and beat/lost closing line. The tags are tallied per playbook so patterns show up over time.

**Line history:** every time the agent prices NBA props it saves the line closest to a coin flip for each player and stat (first seen, and the last one before tip-off), then fills in what he scored after the game (`docs/data/lines.json`, kept 90 days). It shows on each player card under Line history, and in the Similar players table when a comp has a saved line for that game. History builds from the first priced slate on.

A run ends at $1,000 (won) or below $5 (busted). A new $100 run then starts automatically, and everything the agent has learned carries over.

## Analytics on the dashboard

- **Conviction (1-5 dots)** on every play adds up: matchup size, whether the separate signals agree, secure minutes, rest (back-to-backs and 3-in-4 nights hurt overs), blowout risk (spreads of 11+ cut starters' minutes), the Vegas implied team total vs the model, usage bumps when a teammate is out, and for priced lines the edge and whether the line has moved toward or against the lean. The reasons are listed under each play.
- **Conviction track record** (Trends): every night's leans are saved with their conviction and graded after the games, against each player's usual output and against the closing line for priced players. If 5s don't hit more than 3s, the rating gets retuned.
- **Your CLV** (Bets): each bet you log is compared with the closing line (game lines from the agent's scans, props from line history).
- **Injury opportunities** (Today): when a rotation player is out, the teammates who produced the most extra in the games he missed over two seasons.
- **Vegas and rest** on each game card: implied team totals vs the model, blowout risk, back-to-backs.
- **Alt-line ladder** on each player card: the model's probability and fair odds at nearby lines, so you can tell when an alt line at your book is worth it.
- **Phone alerts**: the agent comments on a "Perseverance alerts" issue (GitHub's app turns that into a notification) when a 4-5 conviction play has a real edge, or when an injury opens up a big opportunity. For alerts without GitHub, install the free ntfy app, subscribe to a hard-to-guess topic name, and save that name as the repo secret `NTFY_TOPIC`.

## Games tab

Tonight's NBA scoreboard, ESPN / The Real App style: each game shows the score and clock (refreshing every 30 seconds), DraftKings' spread and total, and the sim's projected score and win chance. Below it is the **Board · bet it yourself** (moved here from Bets): best prices across your books for every sport the agent scans, tap a price to log the bet.

Tap a game for its own page: score, quarter-by-quarter linescore, records and DraftKings' live or closing line up top, then four tabs:

- **Game**: before tip-off, the sim's projection (score, win %, total with its 80% range) next to DraftKings' open and current lines, rest notes, and the top plays in the game. While live, the **Live value** box and last plays. After the final, how the total and spread closed out and how far off the sim was.
- **Box score**: the projected box (average of 10,000 sims) before the game, ESPN's live box score during it, and the final box after.
- **Lines**: DraftKings open, close and live, the saved live snapshots, the best price at your books for this game, and Sim vs DraftKings.
- **Props**: every projected player in the game, by team, with his strongest stat and conviction. Tap a player for his card.

**Watch the sim of this game** opens the Sim tab on it. Game cards on Today open the game page too.

## Sim tab

The court is laid out like a TV broadcast: team-color scoreboard with the clock, shot clock and each team's FG and 3P lines, a win-probability line that draws itself across the game, players as jersey-number chips in team colors (numbers from ESPN's rosters) that keep moving between plays, a callout over the player on every shot (number, name, shot type), a ticker with who has the ball next and the play, and box scores split into starters and bench with the five on the floor lit up. Tap **⤢** for full screen and turn the phone sideways for the broadcast layout: box score, court, box score.


Pick a game and **Watch the average game**: the sim runs 2,000 seeded simulations, finds the one closest to the averages (final score first, then every player's line) and replays exactly that game, so it ends near the average result. **Random game** plays a fresh one. The box score can switch between **This game**, **Average** (every player's average across all simulations, using the overnight 10,000 when they match today's inputs) and **Most common** (each player's most frequent line, with how often it happened, plus the most common margin and total). Otherwise it's a possession-by-possession simulation on a tilted court with the score, clock, win probability, play-by-play and both box scores updating live (1×, 4×, 16×, or skip to the final). **Run 5,000 sims** plays the game thousands of times and shows each player's average, 10th to 90th percentile range, and how often he clears the line (the book's when priced, otherwise an estimate), plus win %, average total vs the Vegas total, and spread cover %.

How it works: each possession picks the shooter by usage, the shot type, make or miss, assists, rebounds and turnovers from each player's own rates. Rotations follow projected minutes (starters sit in blowouts). Before running, the sim calibrates itself so the average simulated box score matches the matchup model's projections, with team scoring anchored most of the way to the Vegas implied totals. When the Vegas total differs from the model's, about 60% of the gap is treated as pace (more or fewer possessions, so rebounds, assists and threes move with it) and the rest as shooting efficiency. It runs on your phone and costs no credits.

## DraftKings lines and live tracking (free, from ESPN's odds feed)

- **Opening and closing lines for everything**: every research run saves DraftKings' opening and closing spread, total and moneyline for each game, and DraftKings' opening and closing line for every player prop the model projects (points, rebounds, assists, threes, PRA, reb+ast), with the model's own probability at the closing line. Stored per day in `docs/data/dk/`. After the games, each prop is graded from the box score; these feed the model-vs-market scoreboard (model vs DraftKings' closing no-vig price), your CLV on props, and a DraftKings line history on every player card.
- **Live lines**: every 15 minutes from about 7pm to 1am ET, the agent saves DraftKings' live spread, total and moneyline with the score and clock, then simulates the rest of the game from that exact spot (`tools/live_sim.js`) for the sim's chance of each live bet. While you have the Sim tab open on a live game, the live section moves to the top and refreshes every 30 seconds (or tap Refresh): the score and clock, ESPN's **live box score** (minutes, points, rebounds, assists, threes, field goals, fouls, +/-) and the **last plays**, and a **Live value** box. Live value shows the close → live move on the total and spread, the pace the game is on, and for each live bet (over, under, both spreads, both moneylines) the points better or worse than the close (live CLV), the sim's chance from that exact score and clock, what the price needs to break even, the EV, and a verdict (Value at 5%+ EV, Lean at 2-5%). The sim re-runs on its own (1,500 games) whenever the score, clock or line changes. It doesn't know about foul trouble or in-game injuries.
- **Would a live bet have paid?** The next morning every live snapshot is graded: did each live bet win, and did the sim see an edge? Results build up by situation in Trends (sim saw a 5%+ edge, team trailing by 10+, by checkpoint: after Q1, halftime, after Q3). The opening-to-closing move on each game is saved too.

## Best bets (Today tab)
The front page leads with **Best bets today**: five player props and five game lines (spreads or totals), built by `tools/sim_all.js` after every sim run and saved to `docs/data/best.json`.
- **Pricing:** every DraftKings spread, total and prop on the slate (free ESPN odds feed, `dk/YYYY-MM-DD.json`) is priced from the 10,000-game sims.
- **What the agent has learned:** the sim's average misses on totals and on each player stat (graded sims, `sims_log.json`) are taken off, shrunk until the sample is large. Each probability is then blended with DraftKings' no-vig price: props by the per-stat model-vs-market weight the scoreboard learns (`scoreboard.json`, starting from `prop_model_weight`), game lines by how the sim's margin errors compare with Vegas' (35% until 20+ games are graded on both).
- **Checks on props:** 18+ projected minutes, no injury tag, and the sim can't be far off both the DraftKings line and the matchup model (big gaps usually mean news the inputs don't have).
- **Selection:** ranked by expected value (conviction breaks ties), one game line per game, at most two props per game and one per player. Picks lock at tip-off. Each is graded the next day from the DraftKings day file, and the card shows the running record and units by type. Picks without a positive EV are shown as "no edge at this price".
- **Market anchor fallback:** when the paid board has no line for a game, research and the sims anchor to DraftKings' free line instead of the model alone.

## Sim vs DraftKings

Every sim run also prices DraftKings' own lines: spreads, totals and moneylines from the agent's game-line scans, and player props from the afternoon props run (DraftKings' main line for each player and stat, kept in line history). The Sim tab shows, for each bet, the sim's chance of hitting, DraftKings' no-vig chance, the DraftKings price and the expected value if the sim is right. Game lines usually land close because the sim leans on the Vegas total and spread on purpose; props are where real gaps show up.

## Measuring the model against the books

- **Model vs market** (Trends): every priced line saves the model's own over probability next to the market's no-vig closing probability. After the game all three (model, market, blend) are scored with log loss and Brier. Once 150 lines are graded, the nightly run moves `prop_model_weight` 30% of the way toward the blend that would have scored best, and writes why in the journal.
- **Official injury report**: the NBA's own PDF (posted through the day) overrides ESPN's injury list. Needs `pypdf`, which the workflow installs.
- **Minutes model**: tonight's minutes start from recent minutes, then adjust for teammates who are newly out (from each player's with/without history, capped at what the missing players actually played), a change in starting role (RotoWire lineups), back-to-backs and blowout risk. The reasons show on each player card. Projections and the sim both use these minutes.
- **Nightly sims**: after every research and props run (about 8:40am, 2:05pm, 3:45pm, 6:15pm and 8:45pm ET in winter), `tools/sim_all.js` simulates every upcoming game 10,000 times with the dashboard's own engine and saves the averages and ranges (`sims.json`, shown instantly in the Sim tab). Each run is seeded, so identical inputs give identical results, and every run of the day is kept in `sims_day.json`: the Sim tab's **Today's simulations** report shows each run side by side, the average of the runs and how much each number moved, so you can see which projections hold steady as news comes in. Each game also saves the single most typical simulated game (the one closest to all the averages) as a full box score. The next morning each game is graded against the real box score (`sims_log.json`): total, margin and winner against Vegas, and every player's minutes and stats against both the sim and the projection alone.

## Your lineup in the Sim (Nov 10)

Sim tab → **👥 Lineup**: for the game you're looking at, mark any player **Out** or **Playing** and pick the **Starters**. The sim re-calibrates right away: a ruled-out player's minutes are re-shared to the rest (to 240), the team's scoring stays anchored to its implied total so his shots go to who's left, a player moved into the starting five gets starter minutes (26-30) and a starter moved to the bench loses about 20%. It's saved on your device for that game and also drives the live sim and the Games page. The overnight 10,000-sim averages are hidden while your lineup is set (they used the official one); Run 5,000 sims gives fresh numbers. **Reset** goes back to the official lineup. Best bets and Prop Lab projections still come from the agent's refreshes.

## Model upgrades (Nov 9): lineups, schedule, minutes, focus

- **Lineup on/off data (pbpstats, free).** When a rotation player is out, the agent pulls what each teammate does in the possessions he is off the floor (this season and last) and moves tonight's projections. Checked out of sample on 73 absences (`tests/onoff.py`): the raw on/off numbers point the right way but are about three times too big, so the adjustment is scaled to what actually happened (points and assists rise most). The reason shows on the player card ("Grimes out: points +2% tonight…").
- **Rebound chances.** A new team-level signal: how many rebounds (missed shots, extra possessions) an opponent's games produce vs the league. In the walk-forward test it improved every stat's correlation with results (`tests/environment.py`).
- **Minutes rebuilt around who actually plays.** Each player gets a chance of playing from his team's last five games (a healthy player the coach stopped using no longer keeps a full projection), a team's minutes are fitted to 240 (a returning starter takes minutes back from his fill-ins), and players cleared on the official report count as back. In the walk-forward test the number of players projected for 8+ minutes who then didn't play fell by more than half, and team totals went from about 330 projected minutes to about 276 (`tests/minutes.py`).
- **Schedule spots.** Miles flown, time zones, back-to-backs, three-in-four and altitude for both teams, measured on 2,500 games: a back-to-back costs about 2.8 points of margin, flying east on short rest about 0.9 per time zone, altitude almost nothing. The model uses conservative versions; each game page shows both teams' spots.
- **Referees.** Tonight's crew (NBA's official assignments, posted about 9am ET) with how their games have run. Shown for context only: a referee's free-throw and scoring tendencies one season had no relationship to the next (correlation about 0), so they don't move projections.
- **NBA.com tracking is blocked.** NBA.com's stats site times out from GitHub's servers (and from here), so potential assists and rebound chances can't be pulled. The stand-in is the **Opportunity profile** on each player card from pbpstats: usage, rebound chances, where his assists go, shot quality.
- **Focus on what beats the market.** Every night the scoreboard grades the model against the market's closing probabilities stat by stat. A stat is **Bet** when the best mix leans on the model 25%+ and beats the market alone, **Skip** when the market alone wins on 150+ lines, **Watching** otherwise. The agent no longer bets Skip stats, spends prop credits on Bet stats first and uses each stat's own model weight; best bets and the play rankings follow the same map (Trends → Where the model has an edge). First read (Nov 9, about 2,000 graded lines): assists and threes are Bet; points and PRA are Skip; rebounds and R+A are Watching.
- **Game dates fixed.** Box scores were stamped with the UTC date, so late tips landed on the next day; back-to-backs and rest were sometimes wrong. They now use the Eastern game day (history repaired).
- **Catch-up refresh.** GitHub sometimes starts scheduled runs very late or skips them. Any light run (scan, live, injury watch) now checks whether the last full refresh is 4+ hours old with games still to tip and does it itself.
- **Next-gen look.** Same family as Team Comeback HQ: display type, a command hero on Today (next-run countdown, both bankrolls, tonight's slate, where the model has an edge), glowing data, glass tab bar.

## Live lines: stale-line guard, price your own line, one-tap game-line bets (Nov 6)

- **ESPN's copy of DraftKings' live line can lag the real one** (on Nov 6, Wizards @ Hawks at 3:35 of the 4th showed Hawks -10.5 and -5000 with the Hawks up 4, when DraftKings' own app had the Wizards +5.5). Live books keep spreads and totals near 50/50, so when the sim makes one side of the live spread or total 78%+ (or the line hasn't moved while the margin swung 8+ points), the Live value box now says the line looks out of date, hides EV and verdicts, and the saved snapshot is marked stale and left out of the morning grading.
- **Price the line you see:** under the live table, pick a side, type the line and price from your DraftKings app, and the sim prices it from the same live simulations (chance, break-even, EV, verdict), with **+ Log it**.
- **+ on every live bet row**, and **Bet DraftKings' line** buttons (both spreads, over/under, both moneylines) on each game's page before tip-off and in its Lines tab, open Log a bet prefilled. Game-line bets grade themselves from the final score.

## Sharper inputs (Nov 6)

- **Real free throws, turnovers and offensive rebounds in the sim.** Each player's free-throw attempts and turnovers come from his own box scores (they were estimated before), and each team's offensive rebound rate, turnovers per possession and free-throw rate come from this season and last, matched against tonight's opponent's defense and shrunk toward the league when the sample is small. They're in each game's `rates` in `players.json`.
- **Recent games count more in the sim.** Each player's rates weight recent games more (0.96 per game back, like the projections) and last season less, with shooting percentages shrunk toward the league on small samples.
- **Coach rotation habits.** The minutes model learns how many minutes each team's starters lose in blowouts and gain in close games compared with the league, and uses that team's own habit for blowout risk and tight spreads (the reason shows on the player card).
- **Injury watch.** Every 30 minutes from about 1pm to 7pm ET, and with every 15-minute live run after that, the agent re-reads ESPN's injury list and the league's official report. If a rotation player (12+ projected minutes or a listed starter) in a game that hasn't tipped is ruled out or cleared, it sends an alert and re-prices the whole slate (projections, minutes, sims) right away.
- **Minutes accuracy** (Trends, top): projected vs actual minutes over the last 7 and 30 days, how often within 4 minutes, starter and bench bias, players projected for 10+ minutes who didn't play, and last night's biggest misses.

## The learning loop (nightly, after grading)

The agent stores every graded result and reads it back each night to refine itself (`agent/learn.py`, saved in `docs/data/tuning.json`, shown in Agent → What the agent has learned):

- **Projection level by stat**: if points (or any stat) projections run high or low against real box scores over the last 30 days, the level is nudged the other way.
- **Minutes**: separate corrections for starters and bench players.
- **Sim anchoring**: if Vegas totals beat the sim's totals, the sim leans harder on the Vegas team total (and less if the sim is doing better).
- **Conviction factors**: each factor (signals agree, secure minutes, rest, blowout, Vegas, usage bump, injury tag) is re-weighted by how much it actually changed the hit rate in the conviction track record.
- **Model vs market blend**: tuned from the scoreboard.

Every change needs a minimum sample, moves in small steps, stays inside hard limits, and is written to the journal with the numbers behind it. A permanent monthly archive of every graded sim and line goes to `data/archive/`.

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

| Time (EST, winter) | What runs |
|---|---|
| ~4:10am | Grading, box scores, and the overnight strategist |
| ~8:40am | Research refresh (free) |
| ~10:05am | Scan |
| ~2:05pm | NBA props + team totals (refreshes lineups first) |
| ~3:05pm | Scan |
| ~3:15pm | Props backup (only if the 2pm run didn't happen) |
| ~3:45pm | Research refresh with updated lineups (free) |
| ~6:15pm | Pre-game refresh: late injuries and lineups, all sims re-run (free) |
| ~8:45pm | Late-game refresh for 10pm+ tips: only games not yet started (free) |
| Every 30 min, ~1pm-7pm | Injury watch: re-price right away when a rotation player is ruled out or cleared (free) |
| Every 15 min, ~7pm-1am | DraftKings live lines, score and clock, sim from the live score, plus the injury watch for games not yet started (free) |
| ~6:35pm | Scan |

Add an hour from March to November (daylight saving time). GitHub can start runs 5-30 minutes late.

## Test offline

```
python tests/simulate.py 30     # 30 simulated days on a fake market, no API key needed
```

## Files

- `agent/`: the agent (Python standard library only). `research.py` daily research, `rotowire.py` lineups, `recap.py` game stories, `saved.py` saved props
- `docs/`: the dashboard (tabs: Today, Games, Prop Lab, Sim, Bets, Trends, Agent); `games.js` is the Games tab, `sim.js` the simulator and live panel
- `docs/data/`: the books and research files (JSON); `docs/data/games/` holds one detail file per game on the slate
- `data/nba/`: box scores (three seasons) and this season's shot locations, one file per day

## Team model and Matchup Builder (Nov 10)
- `agent/team_model.py`: opponent-adjusted team ratings (ridge regression over every team at once, recent games weighted more, half-life 60 days), points luck-adjusted for three-point shooting, back-to-backs and home court fitted. Pace is rated the same way.
- Walk-forward backtest against ESPN's pregame DraftKings line (2025-26 plus this season, 1,213 games): the model misses the margin by 11.75 points on average vs the line's 11.17, and the total by 15.2 vs 14.5. The best blend weight is 0, so the line already knows everything this model does. It is **tracked only**: the agent never bets game lines from it.
- Runs: ratings with every research refresh; the backtest nightly. Output `docs/data/team_model.json`.
- Games tab → any game → **Matchup**: projected score with an 80% range, model vs DraftKings with the backtest record at that size of disagreement, net/offense/defense/pace with ranks, four factors offense vs defense (filters: Model, Last 5, Last 10, Tonight's venue), three-point luck notes, shot zones (current season's shot charts), and what it means for props (pace, blowout risk, team totals).
- Next (phase 2): who's playing (player impact × projected minutes), Sim model mode, team scoreboard. That is where a team model can add information the box score alone can't.

## Conviction best bets, the case for and against, and team model phase 2 (Nov 11)
- **Best bets are ranked by conviction (0-100)**: how far the chance of winning sits above what the price needs. The chance averages two reads: the sim blended with DraftKings' price, and the conviction model (`agent/convmodel.py`, refit nightly into `scoreboard.json` → `conviction`). The conviction model starts from DraftKings' no-vig price for the model's lean and moves it for conviction tier, unders, whether the last-10 hit rate at the line agrees, and model edge. Leave-one-day-out on 2,368 graded props (Nov 2-9): log loss 0.6850 vs the market's 0.6878; its top calls won 65% (176), next tier 61% (398). Seven days only.
- **Findings behind it:** hit rates at the line add nothing to the price by themselves; matchup conviction does (lean wins 54% at 2-3, 57% at 4, 63% at 5); unders won 54% of all graded props; conviction 3+ with an agreeing L10 hit rate won 62%.
- **The case** (`docs/case.js`, shared by `tools/sim_all.js` and the player card): why it works, why it won't hit, and the hit-rate ladder at this line and the next ones out. Sources: form, cushion, thin margins, tonight's splits (venue, opponent, rest, teammates out, blowouts), how the misses happen (minutes, shot volume), matchup signals, sim, price, line move, record against posted lines, league under rate, blowout risk.
- **Learning loop:** every priced prop's case is logged in `case_log.json` and graded; `best.json` → `patterns` counts how each reason and each conviction tier has done. Record by tier is in `best.json` → `record.tiers`.
- **Roster layer** (`agent/roster.py`): tonight's lineup vs the team the ratings saw (missing and returning players, box-score value per minute × usual minutes), 0.4 points per unit, capped at 8. Backtest margin miss 11.60 (ratings alone 11.77, DraftKings 11.18); in 268 games with 3+ points of lineup news 11.2 vs DraftKings 11.0, 90-81 against the spread at 2+ points of disagreement.
- **Team scoreboard** (`team_track.json`, summary in `team_model.json` → `live`): model vs closing line, against the spread, and whether the line moved the model's way. Game lines stay tracked-only until 150 graded games and 55%+.
- **Sim → "Team scoring from: Vegas line / Team model"**: model mode uses the team model and no market input (saved per device).

## Live-line accuracy, clearer labels, run reasons, script explorer (Nov 11)
- **Live lines:** every live fetch bypasses caches; once a game tips, the pregame provider is never used as a fallback. Each line shows its source and how long it has been unchanged in game time. The agent's 15-minute GitHub snapshot (`dk/<date>.json`) is used when it is from later in the game than the phone's copy and differs. Stale guard: the pregame close still showing 4+ minutes into a game, an unchanged line for 5+ minutes of game time with 12+ points scored, the sim at 70%+ on one side of a line unchanged for 2+ minutes, or our own live total 6+ away. "Use the line in your app as the live line" re-prices everything from your numbers for 3 minutes of game time.
- **Our live total:** from the box score alone: possessions so far blended with the pregame pace, points per 100 with three-point and free-throw luck removed, blended with pregame by how much of the game is played.
- **Four factors:** each row names the team it favors in its color, with approximate points; on the Model view the points are scaled to add up to the team model's margin before home court.
- **Win probability chart:** home team on top, away below, 50-50 midline, shaded halves, and a live tag ("OKC 63%").
- **Sim modes:** "What's the difference?" explains Vegas line vs Team model. Games → Matchup → "Vegas vs our model" shows spread, total and each team's points side by side, which players the gap moves, and how past gaps did.
- **Run log:** each run records why it ran (`SIM_REASON`: scheduled, refresh age, injury news, line moved) and what changed per game (line, players in/out or ±4 minutes, sim total). `SIM_TEST=1` keeps test runs out of the log. The 15-minute timer re-sims when DraftKings moves a spread 1+ point or a total 1.5+ (`run.line_moves`).
- **Game-script explorer** (Sim → Run 5,000 sims): slice the sims by script (team by 10+, within 5, blowout, over/under) to see each prop's hit rate inside that script and which props move together.

## Streamlined dashboard, confidence levels, Best Bets refresh, self-checks (Nov 11)
- **Confidence levels:** Confident (conviction 60+: the chance of winning sits 3+ points above what the price needs), Consider (45-59), Pass. Game lines top out at Consider until the team scoreboard shows an edge. Records by level in `best.json` → `record.tiers`. The live value box uses the same words (Confident 5%+ EV, Consider 2-5%).
- **Five tabs:** Tonight, Games (the Sim opens from each game: "Sim ▶"), Players, My bets, Model (Health, Trends, Agent). Old links (#trends, #agent, #sim) still work.
- **Tonight:** status strip (self-checks, lineup age, next run), "What changed since you last looked" (line moves, players ruled in or out, picks new, dropped or changing level; per device), Best Bets, Live now, your watchlist, then bankroll and the older sections collapsed.
- **Filter bar** on Tonight, Games, Players and Sim: game (focus mode), stat, side, confidence level, watchlist, signal only. Remembered per device. Signal only hides even four-factor rows and "no change" run notes.
- **Watchlist:** ☆ on any player card or game page.
- **Best Bets:** depth 1 is the play, level, price and verdict; "The case" holds the numbers, reasons and ladder. Dropped picks stay listed with the reason and are graded (`record.dropped`).
- **Re-price now** (`docs/refresh.js`): on the phone, pulls DraftKings' current lines and props from ESPN and re-prices against the saved sim distributions (`sims.json` now stores full margin, total and per-player stat distributions), with the agent's learned weights, checks, case and conviction model. Shows what moved. Not recorded.
- **Full refresh:** opens a prefilled "Refresh:" GitHub issue; `manual-bets.yml` runs `agent.run manual-issue` → `refresh_issue`: a full research run (one per 10 minutes), then comments with the new top props and closes the issue.
- **Self-checks** (`agent/checks.py` → `checks.json`, Model → Health): research freshness, team minutes, sims for every game, sim totals near the line, DraftKings lines, best-bet players still available, run-log bursts, credits, nightly pieces. A failure is noted in the journal.
- **Pre-push check:** `node tools/check.js --ui` (scripts parse, data shapes, a 300-sim engine test, every tab and game sub-page rendered headless at phone width).
