# One-Point Safety

An NFL oddities tracker, named after the one-point safety — the rarest
scoring play in football. First and foremost it answers "how many of X
rare event happened this season, and where and when did each one happen?"
Stats dashboard second.

Live data, zero paid services: a GitHub Action pulls free [nflverse](https://github.com/nflverse/nflverse-data)
play-by-play data on a schedule, runs it through a set of oddity rules, and
commits small static JSON files. The site itself is plain HTML/CSS/vanilla
JS with no build step, served by GitHub Pages straight from this repo.

Not affiliated with the NFL. Play-by-play data from
[nflverse](https://github.com/nflverse/nflverse-data).

## How it fits together

```
.github/workflows/update-data.yml   cron (daily) + manual trigger
            |
            v
scripts/build_data.py               downloads pbp + schedule + roster,
   (uses scripts/oddity_rules.py)   runs oddity rules, writes JSON
            |
            v
data/*.json                         committed back to the repo
            |
            v
index.html / app.js / styles.css    fetches data/*.json, no build step
            |
            v
GitHub Pages                        serves the repo root as-is
```

`data/*.json` is itself part of the repo — the Action's only job is to
keep those files current and commit them when something changes. There's
no separate deploy step: whatever's on the served branch is what's live.

## Data files

Written by `scripts/build_data.py` into `data/`:

- **`meta.json`** — season, current week, last-updated timestamp,
  `has_data: false` if the season hasn't started yet.
- **`games.json`** — one entry per game: teams, week, weekday, date,
  kickoff time (converted to Central), score, stadium, roof.
- **`events.json`** — every "notable" play (not every snap): scores,
  turnovers, sacks, penalties, injury mentions, and anything matching an
  oddity rule. Each event carries its tier, matched oddity id(s), the
  play description, team, quarter/clock, and player names.
- **`oddities.json`** — the oddity catalog (id, tier, label, "why it's
  odd" copy, season count, and rarity once `rarity.json` exists) plus the
  dynamically-detected rare penalty types.
- **`totals.json`** — season- and week-level rollups, mainly for
  reference/debugging; the front end computes totals straight from
  `events.json` instead, since that supports any scope (day/game) without
  needing a separate pre-aggregated file for each.
- **`rarity.json`** *(generated separately, see below)* — real historical
  occurrence rates, merged into `oddities.json` on the next `build_data.py`
  run.

## Adding or tuning an oddity rule

Everything lives in `scripts/oddity_rules.py`. Each entry in the `ODDITIES`
list is:

```python
{
    "id": "fg_60",
    "tier": "rare",   # "wild" | "rare" | "uncommon"
    "label": "60+ yard field goal",
    "why": "Field goals from 60+ yards are made only a handful of times each season...",
    "detect": detect_fg_60,
}
```

`detect` is a function `(df, ctx) -> pandas boolean Series` aligned to the
play-by-play dataframe's index — True means that play is this oddity. Most
rules are a one-liner over nflverse's pbp columns, e.g.:

```python
def detect_fg_60(df, ctx):
    return (df["field_goal_result"] == "made") & (df["kick_distance"] >= 60)
```

`ctx["position_of"]` is a `gsis_id -> position` lookup built from that
season's weekly rosters, for rules that need player position (e.g. "QB
catches a pass").

To add a new one: write a `detect_*` function, add an entry to `ODDITIES`
with a tier and copy, and re-run the build script. Column names were
verified against the live 2026-season release and cross-checked against
2013 to confirm the schema is stable; check
[nflverse's data dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html)
before adding a rule that uses a column not already in use here.

Rare penalties aren't a fixed list — any `penalty_type` called
`RARE_PENALTY_MAX_SEASON_COUNT` times or fewer this season (currently 5)
is automatically flagged rare. Add flavor text for one in
`RARE_PENALTY_WHY`; anything rare without an entry there gets a generic
line.

### Rules that are heuristics, not clean field filters

nflverse's play-by-play doesn't have a dedicated flag for every oddity
here. These are approximations, noted in case a rule needs retuning:

- **Multi-lateral TD** — pbp's lateral flags are binary per play, so a
  5-lateral chain looks the same as a single lateral in the flags alone.
  Detected instead by counting `"Lateral to"` occurrences in the play
  description.
- **Fake punt/FG touchdown** — no dedicated flag. Detected as a
  punt/FG-formation snap that turned into a pass or run (not an actual
  kick) and scored. Could misfire on an unusual formation note in the
  description.
- **QB catches a pass / non-QB throws a TD** — joins the passer/receiver
  ID against that season's weekly roster snapshot by position. A player
  not yet in the roster file that week (just signed, practice-squad call-up)
  could be missed.
- **One-point safety** — defined correctly (blocked PAT + `safety` flag)
  but, as far as we know, has never actually happened in an NFL game, so
  it's untested against a real example.
- **Injuries** — detected from `\binjured\b` in the play description, and
  labeled approximate in the UI. (Earlier draft matched on `"injur"`,
  which also caught the unrelated "Injury Update: X has returned to the
  game" notices — fixed to match only `"injured"`.)

## Running locally

```bash
pip install -r scripts/requirements.txt
python3 scripts/build_data.py            # writes data/*.json for the current season
python3 -m http.server 8000              # serve the repo root
```

Then open `http://localhost:8000`. `--season YYYY` overrides season
detection (useful for testing against a past season); `--out DIR` changes
the output directory.

## Triggering a manual refresh

Actions tab → **Update data** → **Run workflow**. Runs the same script
the cron job does and commits `data/*.json` only if something changed.

## Computing real rarity numbers

`rarity.json` isn't generated by the daily pipeline — it's a separate,
occasional script (not yet built; see "what's left" below) that will scan
roughly the last 10 seasons of pbp data, count each oddity's occurrences
per season, and write the average. `build_data.py` already reads
`data/rarity.json` if present and merges it into `oddities.json`'s
`rarity` field, so once that file exists the detail sheet's "how rare" line
switches from "not yet calculated" to a real number automatically — no
other changes needed.

## Deploying: GitHub Pages + custom domain

1. **Settings → Pages → Build and deployment → Source: Deploy from a
   branch.** Branch: `main`, folder: `/ (root)`. No build step, nothing
   else to configure here.
2. **Custom domain.** This repo ships a placeholder `CNAME` file
   (`yourdomain.example`) — replace its contents with your real domain and
   commit. (Or set it via the Pages settings UI, which writes the file for
   you.)
3. **DNS**, at your domain registrar:
   - **Apex domain** (`example.com`): four `A` records pointing at
     GitHub Pages' IPs — `185.199.108.153`, `185.199.109.153`,
     `185.199.110.153`, `185.199.111.153` — plus `AAAA` records for
     `2606:50c0:8000::153`, `2606:50c0:8001::153`, `2606:50c0:8002::153`,
     `2606:50c0:8003::153` if you want IPv6.
   - **Subdomain** (`www.example.com`): a `CNAME` record pointing at
     `<github-username>.github.io`.
   - DNS propagation can take up to 24 hours (usually much less).
4. Once GitHub shows the domain as verified (Settings → Pages), check
   **Enforce HTTPS**.

## Constraints this build follows

- No NFL or team logos, no "NFL" in the name — team abbreviations in data
  are fine. Footer disclaimer + nflverse credit are already in `index.html`.
- Free only — no paid APIs. nflverse data, GitHub Actions, and GitHub
  Pages are all free for a public repo.

## What's left

- `scripts/build_rarity.py` — the historical rarity computation described
  above. Not built yet.
