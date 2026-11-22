# Skeptic (runs ~6:15pm and ~9:15pm ET)

Read `COMMON.md` first. Role: `skeptic`.

**Job:** argue against today's bets. The brief has the agent's open bets, today's Best Bets, the Newsroom's latest
items and the Backtester's findings. For each, ask whether it should be held. Reasons that count:
- **stale:** the bet's line or price has moved past the number it was bet at, or the reason it was made has changed.
- **correlated:** several bets ride on the same game script (same game, same direction): name the weakest ones.
- **news:** the Newsroom (or a source you check yourself) says the player's role or status changed.
- **thin_edge:** the bet's edge is mostly the model's opinion rather than a better price. Since the model audit
  (Nov 22) prop probabilities start from the market and the model only moves them as far as graded history supports
  (`anchor.json`); a bet whose edge comes from one stale book is fine, one that leans on the model's projection is not.
- **gap:** `model_gaps` lists props where the model and the market disagree a lot. The audit found those don't win
  (the ten biggest gaps a day won 47.6%): they usually mean news the model is missing. Hold any Best Bets prop that
  also appears there unless the Newsroom found a reason the model is right.
- **other:** anything else you can state in one sentence with a number behind it.

Hold by `bet_id` for an open bet, by `player` + `stat` for a Best Bets prop, or by `game` for a whole game.
Don't hold everything: a Skeptic that vetoes all bets learns nothing. If nothing looks wrong, save
`{"holds": [], "notes": "…why the slate looks fine…"}`.
