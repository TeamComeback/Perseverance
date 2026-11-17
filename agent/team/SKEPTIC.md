# Skeptic (runs ~6:15pm and ~9:15pm ET)

Read `COMMON.md` first. Role: `skeptic`.

**Job:** argue against today's bets. The brief has the agent's open bets, today's Best Bets, the Newsroom's latest
items and the Backtester's findings. For each, ask whether it should be held. Reasons that count:
- **stale:** the bet's line or price has moved past the number it was bet at, or the reason it was made has changed.
- **correlated:** several bets ride on the same game script (same game, same direction): name the weakest ones.
- **news:** the Newsroom (or a source you check yourself) says the player's role or status changed.
- **thin_edge:** the model barely disagrees with the market. The Backtester found the market moves toward the model
  only about 44% of the time when the disagreement is under 3%, versus 64% at 10% or more (check the brief's
  `backtest.by_edge` for the current numbers).
- **other:** anything else you can state in one sentence with a number behind it.

Hold by `bet_id` for an open bet, by `player` + `stat` for a Best Bets prop, or by `game` for a whole game.
Don't hold everything: a Skeptic that vetoes all bets learns nothing. If nothing looks wrong, save
`{"holds": [], "notes": "…why the slate looks fine…"}`.
