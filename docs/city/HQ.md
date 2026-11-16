# Perseverance City: how Team Comeback HQ changes it

The city page (`docs/city.html`) shows the model as companies and desks. Most of it is measured from the code and data
by `tools/orgmap.js`, which runs after every agent run. The parts people decide live in one file, `docs/city/cast.json`,
and the HQ may change that file.

## What the HQ can change (docs/city/cast.json)

| Field | What it controls |
|---|---|
| `city.bylaws` | The chairman's rules shown on the spire. Must stay true to `agent/config.py`; never loosen one here. |
| `bulletin` | Notices shown under the city and on the spire: `{"at": "YYYY-MM-DD", "by": "<company or HQ>", "text": "..."}`. Keep the newest 3 first. |
| `companies` | Name, subtitle, color, `about`, and `at: [col, row]` (col -1 or 1, rows 0-4; col 0 row 2 is the spire). |
| `desks` | Title, job, company, `status` (`active`, `probation`, `understudy`, `benched`, `vacant`, `planned`), `note`, `code` files (`path` or `path#function`), `reads`/`writes` documents, outside `sources`, `via` (works during another desk's shift). |
| `docs` | Friendly document names and `fresh_h`, the hours after which a document counts as overdue. |
| `metrics` | The output line shown on a desk: `{"file", "count" or "value" or "record", "label", "fmt"}`. `$today` means today's date (ET). |
| `reason_desks` | Which desk a Best Bets reason tag belongs to, for Trace a bet. |
| `check_desks` | Which desk owns each self-check, for Coherence. |

## When to change it

- A new module was added and the city shows it as a **new hire**: give it a desk (or add its file to an existing desk).
- A desk changes status: the schedule adjuster passes its test (`probation` to `active`), the conviction understudy is switched in, a playbook is benched, the Daily Briefing writer is built (`vacant` to `active`).
- Coherence shows **map vs code** drift: the code mentions a document the desk does not list. Add it to `reads` or `writes`.
- The chairman approves a new bylaw or bulletin notice.

## Rules

- Never change a bylaw's meaning; the bylaws mirror the owner's fixed rules ($5 minimum, the ten approved books, paper money).
- Do not edit `docs/city/org.json`, `docs/data/city_live.json` or `docs/data/city_trace.json` by hand; they are regenerated.
- After any edit, run both and commit only if they pass:

```
node tools/orgmap.js --check
node tools/orgmap.js
```

`node tools/check.js` also runs the cast check, and `--ui` opens the city headless and traces a bet to its end.

## Later stages (not built)

Growth (the city growing from Oct 26, from `org.json` `git.born` and `git.days`), Replay the day, and a full 3D city using
the same `org.json`. See `claude/perseverance-city-visual-model-framework.md` in the Basketball Analysis project.
