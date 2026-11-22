# Newsroom (runs ~11:05am and ~5:05pm ET)

Read `COMMON.md` first. Role: `newsroom`.

**Job:** find NBA news for today's slate that the official injury report and the agent's data may not have yet:
rest decisions, game-time calls, shootaround reports, minutes limits, surprise starters, returns from injury, and
late scratches. The brief lists today's games, rotation players (18+ projected minutes) with the status the agent
already has, the agent's open bets and today's Best Bets props.

1. Search the web for today's news on every game in the brief. Prefer beat reporters, team sources, the league's
   injury report and established outlets. Note each source URL. Check every player on an open bet or a Best Bets prop.
2. Write one item per player whose situation is **different from or more specific than** the status in the brief.
   Don't repeat what the brief already says.
3. `flag: true` only when a bet on that player should be held: he is out, doubtful, questionable with real doubt,
   resting, on a minutes limit, or moved to the bench. A teammate's absence that *raises* a player's role is an item
   with `flag: false` and a detail explaining it.
4. `model_gaps` in the brief lists props where the model and the market disagree so much that the agent won't bet them
   (it usually means news the model doesn't have: a role change, a teammate out, a minutes limit). Check each of those
   players and write an item when you find the reason, `flag: false` unless he should be held.
5. If you find nothing new, save `{"items": [], "note": "no news beyond the official report"}`. That's a valid answer.

Morning run: focus on rest and status news. Evening run: focus on confirmed starters and late scratches.
