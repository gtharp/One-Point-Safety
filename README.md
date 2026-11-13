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

A second league, college football (NCAA FBS), lives at `college.html`,
built from the [CollegeFootballData.com](https://collegefootballdata.com) API.
See [College football](#college-football-ncaa-fbs) below.

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
- **`weather.json`** — this season's coldest/hottest/muggiest/driest/
  windiest/rainiest/snowiest outdoor games, parsed from nflverse's
  per-game weather text (temperature, humidity, wind, conditions).
- **`rarity.json`** *(generated separately, see below)* — real historical
  occurrence rates, merged into `oddities.json` on the next `build_data.py`
  run.
- **`weather_records.json`** *(generated separately, see below)* — the
  same records as `weather.json`, but across every season nflverse has
  weather data for (2002 onward), not just the current one.

### Weather

`weather.json` comes from a field most of this README doesn't otherwise
mention: nflverse's pbp `weather` column is a per-game free-text string
like `"Snow Showers, 3 to 5 inches expected. Temp: 33° F, Humidity: 91%,
Wind: SE 5 mph"`, present for every *outdoor* game back to the 2002
season (domes/closed roofs leave it blank — there's no "weather" to
record). `parse_weather_text()` in `build_data.py` pulls humidity out
with a regex, splits off the condition description, and ranks rain/snow
severity by keyword (`RAIN_SEVERITY`/`SNOW_SEVERITY`) since the field
almost never gives a quantitative rain amount and only sometimes gives
one for snow (`SNOW_INCHES_RE`). `build_weather()` then picks a single
record holder per category among that scope's *outdoor* games only (domes
would make "coldest game" meaningless).

`scripts/build_weather_records.py` reuses that same parser against every
season since 2002 to compute the data-verified "since 2002" records
(`data/weather_records.json`) — a separate, occasional script for the
same reason `build_rarity.py` is: it downloads two dozen seasons of pbp,
which doesn't need to happen on a daily cron
(`.github/workflows/update-weather-records.yml` runs it yearly plus
manual dispatch). Run it by hand with:

```bash
python3 scripts/build_weather_records.py               # 2002 through last season
```

Nothing here reaches further back than 2002, since that's as far as
nflverse's weather field goes — nowhere close to the sport's actual
all-time extremes (the Ice Bowl, the Freezer Bowl, both well before even
nflverse's 1999 play-by-play starts). Those live in `data/history.json`'s
`weather_extremes` category instead, hand-researched the same way the
rest of that file is — see the History appendix section below. Any event
there can carry an optional `"record"` field (`{label, value}`, e.g.
`{"label": "Coldest game ever", "value": "-13°F"}`); `history.js` pulls
every event with one into a prominent "All-time weather records" panel
at the top of the page, intentionally placed *above* the "since 2002"
panel — the hand-researched records are the real headline numbers, the
computed ones are a supplement for the shorter, data-verified era, not
a replacement.

One parsed value got flagged rather than trusted outright: a 2016
Steelers-Giants game's weather text reads "71 mph" wind, which is
low-end-hurricane-force and implausible for a game that was actually
played — almost certainly a transcription error somewhere upstream of
nflverse, not reality. Rather than silently drop or silently trust it,
`build_weather_records.py` attaches a `caveat` field when windiest exceeds
50mph, and the front end shows it as a hoverable "Flagged" pill. If you
tune the plausibility threshold, keep that spirit: surface it, don't hide
it.

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

## College football (NCAA FBS)

`college.html` is the college counterpart of the main page: same `app.js`
and `styles.css`, but `<body data-league="ncaa">` makes `app.js` load
`data/ncaa/*.json` instead of `data/*.json`, and a `body[data-league="ncaa"]`
block in `styles.css` swaps the green theme for light blue (light-blue
fills; a mid blue, `--accent-strong`, for selected buttons/chips: white
text on it is 5.97:1 in light mode and 5.65:1 in dark, both WCAG AA).
Both pages have an **NFL | College** toggle in the header. The last choice
is remembered in `localStorage`, and a fresh visit to the NFL page by
someone who last picked College goes straight to `college.html`. The
History page stays NFL-only and isn't linked from the college page.

Not affiliated with the NCAA or any school. College data from
[CollegeFootballData.com](https://collegefootballdata.com) (CFBD), used
under its free tier. FBS games only (games with at least one FBS team).

```
.github/workflows/update-ncaa-data.yml   cron (daily + Sat-night/Sun in season)
            |
            v
scripts/build_ncaa_data.py               CFBD API -> oddity rules -> JSON
   (uses scripts/ncaa_oddity_rules.py)
            |
            v
data/ncaa/*.json                         committed back to the repo
            |
            v
college.html / app.js / styles.css       fetches data/ncaa/*.json
```

The browser never calls the CFBD API; the page only reads the committed
JSON.

### Data files

`data/ncaa/` uses the same file names and JSON shapes as the NFL side
(`meta.json`, `games.json`, `oddities.json`, `totals.json`), with a few
differences:

- **events are split by week**: `data/ncaa/events/week-NN.json`, each a
  list in the same event shape as the NFL's `events.json`, listed in
  `meta.json`'s `event_files`. The page loads them all and concatenates
  them (and numbers `id`s by position), so the front end works the same
  either way. Split because FBS plays about 7x as many games: a full
  season is ~10 MB of events, and a single file would be rewritten in
  full by every daily commit, bloating git history. Now a run that only
  refetches this week rewrites only this week's file (older weeks change
  only in the rare case that a penalty type's "rare" flag flips).

- every game and event carries **`ap_top25`**: true if either team was in
  that week's AP Top 25 (the poll released before the game; bowls and the
  CFP use the last regular-season poll). Games also have `away_rank` /
  `home_rank`, conferences, and `notes` (bowl names etc.).
- `meta.json` has `week_labels` (postseason weeks show as "Postseason"
  rather than a week number) and `rare_penalty_threshold`.
- no `weather.json`. CFBD's weather data is Patreon-only, so the weather
  panel isn't on the college page.
- `data/ncaa/cache/` holds what the next run needs so it doesn't spend
  API calls refetching it: `teams.json` (FBS team/venue info, fetched once
  a season), `ap_polls.json` (every week's AP Top 25), and `weeks.json`
  (per-week fetch state plus per-game counts behind `totals.json`). Raw
  plays are never stored: the per-week event files double as the event
  store, so a run that only refetches this week keeps every earlier
  week's events from the previous run.

Size: events grow through the season (about 7 MB raw at week 10, ~10 MB
by the end of the bowls; GitHub Pages serves it gzip-compressed, ~1/7 of
that) and start over each new season.

### The API key

The CFBD API needs a key. **It is never written into this repo**: not in
code, config, `.env` files or commit messages. `build_ncaa_data.py` reads
it only from the `CFBD_API_KEY` environment variable and exits with a clear
message if it's missing.

- In GitHub Actions it comes from the repository secret `CFBD_API_KEY`
  (Settings → Secrets and variables → Actions), passed to the build step
  as `env: CFBD_API_KEY: ${{ secrets.CFBD_API_KEY }}`.
- Locally: `export CFBD_API_KEY=...` in your shell (get a free key at
  collegefootballdata.com), then run the script. Don't put it in a file
  inside the repo.

### Call budget

The free tier is **1,000 calls per calendar month**, shared with
CollegeBasketballData (which this site doesn't use). The pipeline is built
around that:

| Run | Calls |
| --- | --- |
| `/info` quota check (start of every run) | free |
| `/games?year&classification=fbs` (whole season's schedule + results) | 1 |
| `/plays?year&week` (one call per *week*, never per game), only for weeks whose completed games changed; daily cap 4 | 0–1 |
| `/rankings?year` (every week's polls), only on daily runs when a newer AP poll than the cached one is due (a week-N game has kicked off but the cache only has polls through N-1; in the postseason, a check every 3 days for the final regular-season poll) | ~1 a week |
| `/teams/fbs` only when `cache/teams.json` is missing or from another season | ~1 a year |

A daily run is typically 1–2 calls (3 on the day a new poll is due); the
in-season weekend runs (Sun 04:30 and 08:30 UTC, `--mode weekend`)
refetch only the current week and never fetch rankings, so 1–2 calls each.
That's roughly 70–80 calls a month in season. The daily schedule only runs
August through January, so the offseason costs nothing (use **Run
workflow** if something needs refreshing then).
Every response's `X-CallLimit-Remaining` header is logged, there's a hard
ceiling of 10 calls per run, and if `/info` reports fewer than 100 calls
left the run keeps the existing data and exits without spending anything.

The daily job runs at 13:30 UTC (Aug–Jan), 30 minutes after the NFL job, so the two
never race on `git push`. It has its own concurrency group and does a
`git pull --rebase` before pushing.

```bash
export CFBD_API_KEY=...                       # never commit this
python3 scripts/build_ncaa_data.py            # daily mode (default)
python3 scripts/build_ncaa_data.py --mode weekend
python3 scripts/build_ncaa_data.py --refetch-all   # after changing a rule: ~1 call per played week
```

Season detection: the "2026" season runs late August 2026 to mid-January
2027, so August–December is that year and January–July belongs to the
previous one. Regular season and postseason (bowls/CFP) are both included.

### How the college rules differ

CFBD plays don't have nflverse's boolean columns (`touchdown`, `safety`,
`extra_point_attempt`, ...), so every rule in `scripts/ncaa_oddity_rules.py`
is rewritten on what CFBD does have: `playType` ("Rushing Touchdown",
"Field Goal Good", "Safety", ...), `playText`, `yardsGained`,
`period`/`clock`, and the score *after* each play. `build_ncaa_data.py`
derives per-play score deltas from those scores, which is what catches
things folded into one play's text (e.g. a TD and its try).
Ids, tiers and copy match the NFL rules where the play means the same
thing; the "why it's odd" copy is rewritten for college (one-point safeties
*have* happened in major college football, and the defensive 2-point return
has been legal there since 1988). Two college-only oddities:

- **Targeting ejection**: a Targeting foul that wasn't declined, offset
  or overturned on review.
- **Overtime 2-point shootout**: the game reached a third overtime (period
  7+), where college OT turns into alternating two-point tries. Counted
  once per game.

Player names are parsed from `playText` ("#2 D.Lagway", "K. Osborne");
no API calls are spent on per-play stats.

Rare penalties use the same dynamic idea as the NFL side, but FBS plays
about 7× as many games, so a penalty type is rare when it's been called at
most `RARE_PENALTY_PER_100_GAMES` (1.0) times per 100 games played so far,
minimum 3. Penalty names are normalized across CFBD's text formats
("ST Holding", "Offensive Holding" and "holding" all become "Holding").

Tiers start from the NFL values. There's no college rarity script yet, so
the detail sheet says "Historical rarity not calculated yet".

### Heuristic college rules

These rely on text patterns or inference rather than a dedicated field:

- **One-point safety**: "safety" in the try portion of a TD play's text
  plus a 1-point score delta. Untested against a real example so far.
- **Defensive 2-point return**: CFBD's "Defensive 2pt Conversion"
  playType, or the defense gaining exactly 2 on a TD play with a
  return/turnover word in the try text.
- **Walk-off score**: go-ahead score in the 4th quarter with 0:00 on the
  clock ("clock 00:00" in the text, else the clock field), and no later
  scoring in the game. College OT has no clock, so OT winners count as
  overtime scores instead.
- **Fake punt/FG TD**: the passer/rusher on a TD punted or kicked for that
  team in the same game, or the text says "fake". Holders are excluded
  because they're often quarterbacks.
- **Non-QB TD pass / QB catches a pass**: no roster positions without
  extra calls, so a "QB" is whoever threw 5+ passes for that team in that
  game. A non-QB passer has ≤2 attempts in the game and also caught or ran
  the ball, or punted/kicked.
- **Onside kick recovered**: the kicking team has the ball on the next
  real play.
- **Multi-lateral TD**: 2+ "lateral" mentions on a TD play.
- **Targeting ejection**: text match on "Targeting", minus
  declined/offsetting/overturned.
- **Penalty team**: play text uses ESPN-style abbreviations (MIZZOU,
  KSTATE) that don't always match CFBD's team data. Matched by abbreviation,
  alternate names and letter order, then by elimination using each game's
  yard-line markers. A few games' abbreviations still don't resolve, and
  those penalties show no team.

### Injuries (phase 1)

Plays whose text says a player was "injured" (CFBD writes "#9 J.Smith
injured on the play") are flagged the same way as the NFL side's
`INJURY_RE`, but **only for games involving an AP Top 25 team**. The page
labels the count "Injuries (AP Top 25 games)". Pre-game availability
reports are a planned **phase 2**.

The college page also has an **AP Top 25 only** filter chip that narrows
the oddities board/feed and the play log to games involving a ranked team.

## The History appendix

`history.html` (linked from the main page's header) is a separate page: a
hand-researched index of deaths, banned plays, equipment changes, and
other historic firsts from football's past — most of it from well before
nflverse's play-by-play data starts, so it can't be generated the way the
season data is. It's static content, not a pipeline.

Its data lives in `data/history.json`: a `categories` list (id, label,
blurb) and an `events` list. Each event is:

```json
{
  "id": "2005-horse-collar-ban",
  "year": 2005,
  "date": null,
  "category": "outlawed_plays",
  "title": "The horse-collar tackle banned — the \"Roy Williams Rule\"",
  "summary": "...",
  "people": ["Roy Williams"],
  "teams": ["Dallas Cowboys"],
  "disputed": false,
  "note": "...",
  "sources": [{ "label": "Wikipedia", "url": "https://..." }]
}
```

`date` is `null` when only the year is known. `disputed` + `note` are for
facts that vary across sources (a death toll reported differently by
different outlets, a story that's popular legend more than documented
rule history, etc.) — mark it rather than silently picking one number.
Every entry needs at least one real source in `sources`.

Death entries additionally carry `"is_death": true` and a `"cause"` tag --
`"direct"` (on-field trauma), `"indirect"` (cardiac, heat stroke, other
medical), `"long_term"` (CTE/brain-trauma-linked, occurring years after
playing), or `"mixed"` (an aggregate entry spanning more than one cause).
The front end shows a second row of filter chips for these, cutting across
categories (e.g. "Indirect" pulls in Chuck Hughes from "Deaths & safety
reform" alongside anything similarly tagged elsewhere). `cause` requires
`is_death: true` and vice versa -- the validator checks this pairing.

To add an entry: add it to `data/history.json` by hand, then run
`python3 scripts/validate_history.py` to check required fields, duplicate
ids, valid `cause` values, and that every entry (especially disputed ones)
is sourced. The current entries were researched via web search with
citations, not pulled from memory — do the same for new ones rather than
trusting recalled dates, especially death tolls and "first ever" claims,
which are exactly the kind of thing that turns out to be disputed across
sources. Note: this sandbox's network policy blocks youtube.com and
wikipedia.org entirely, so a source video or Wikipedia page sometimes has
to be verified indirectly (search results, mirrors, other sites citing
it) rather than fetched directly -- say so in the entry's `note` if that's
what happened.

### The Flying Wedge animation

`flying-wedge.js` draws the animated diagram at the top of `history.html`
(an inline SVG, animated with the Web Animations API — not a literal GIF;
smaller, crisper, and automatically theme-aware via the site's existing
CSS variables, at the cost of needing a modern browser). Formation
geometry (wing positions, convergence point, which defenders get knocked
back) is hardcoded as coordinate tables at the top of the file rather than
pulled from `history.json`, since it's a one-off bespoke reconstruction,
not list data. The "Read more" button opens the corresponding
`data/history.json` entry (`1892-flying-wedge-invented`) via
`window.openHistoryEntry(id)`, a small bridge `history.js` exposes for
this purpose.

One SVG gotcha worth knowing if you touch this file: animating the CSS
`transform` property via `.animate()` on an element that *also* carries a
`transform` XML attribute replaces that attribute's value instead of
composing with it, so an element positioned only via `transform="translate(x,y)"`
snaps to the SVG origin the instant it's animated. The fix used here is to
wrap each such element in an outer `<g transform="...">` that sets the
base position and is never animated, with an inner, attribute-free `<g>`
as the actual animation target. (Elements positioned via real geometry
attributes -- a `<circle>`'s `cx`/`cy` -- don't have this problem, since
those aren't in the same "transform" namespace CSS competes with.)

## Story pages and the college spotlight

Hand-written story pages for one-off oddities live in the site root as
plain HTML (no `app.js`), so search engines index the full text without
running JavaScript. The first is
`miami-ohio-video-board-goalposts-penalty.html`, about Miami (OH)'s video
board showing fake goalposts during an Ohio field goal try (Nov. 10, 2026),
which drew an unsportsmanlike conduct flag under NCAA Rule 9-2-1.

Each story page has:

- an entry in `PAGES` in `scripts/build_seo.py` (title, 140-160 char
  description, `og_type: "article"`), so it gets head tags and a sitemap
  entry like every other page;
- hand-written `NewsArticle`, `BreadcrumbList` and `FAQPage` JSON-LD
  after the `<!-- seo:end -->` marker (keep the FAQ JSON-LD in sync with
  the visible FAQ on the page);
- a link from the static `<aside class="spotlight">` banner near the top
  of `college.html`. Swap or remove the banner when the story goes stale;
  the story page itself stays up.

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
For college: **Update college data** → **Run workflow** (mode `daily` or
`weekend`), which commits `data/ncaa/`.

## Computing real rarity numbers

`rarity.json` isn't generated by the daily pipeline — `scripts/build_rarity.py`
is a separate, occasional script that scans the last ~10 completed seasons
of pbp data, counts each oddity's (and each penalty type's) occurrences
per season, and writes the average:

```bash
python3 scripts/build_rarity.py               # last 10 completed seasons
python3 scripts/build_rarity.py --seasons 5    # shorter window
```

`build_data.py` reads `data/rarity.json` if present and merges it into
`oddities.json`'s `rarity` field automatically on its next run — the
detail sheet's "how rare" line goes from "not yet calculated" to a real
number with no other changes needed. `.github/workflows/update-rarity.yml`
runs this once a year (plus manual dispatch) since historical rarity
barely moves mid-season.

Running this once against the real 2016–2025 seasons reshuffled a few tier
guesses that turned out wrong: "QB catches a pass" looked Wild by
intuition but actually happens ~12×/season league-wide, so it moved to
Uncommon; "walk-off score" and "fake punt/FG touchdown" turned out rarer
than several things tiered Rare, so they moved to Wild. Tiers in
`oddity_rules.py` now reflect the real 10-season averages, not guesses.

## Deploying: GitHub Pages + custom domain

Live at [one-pointsafety.com](https://one-pointsafety.com), served from
`main` via Settings → Pages. For reference, or if setting this up again
elsewhere:

1. **Settings → Pages → Build and deployment → Source: Deploy from a
   branch.** Branch: `main`, folder: `/ (root)`. No build step, nothing
   else to configure here.
2. **Custom domain.** Set it in the Pages settings UI (which writes the
   `CNAME` file for you), or edit `CNAME` directly and commit.
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
- Free only — no paid APIs. nflverse data, CFBD's free tier, GitHub
  Actions, and GitHub Pages are all free for a public repo.
- No school or conference logos on the college page either (CFBD's team
  data includes logo URLs; the team cache deliberately drops them).

## What's left

- Nothing blocking — phases (a) through (d) from the original plan are
  all built and verified against real data. Remaining work is operational:
  merge to `main`, enable Pages, and set the real custom domain.

## SEO: head tags, sitemap.xml, robots.txt

**Cache-busting.** `build_seo.py` also appends `?v=<8-char content hash>`
to every local `.css`/`.js` link (e.g. `styles.css?v=ce150953`). Browsers
cache these files, so without the suffix visitors can see new HTML with an
old stylesheet. After editing `styles.css` or any `.js` file, run
`python3 scripts/build_seo.py` and commit; `--check` fails if a hash is stale.

`scripts/build_seo.py` is the single source of truth for each page's
`<title>`, meta description, canonical URL, Open Graph and Twitter tags
(the `PAGES` dict), and for `sitemap.xml`. The canonical domain comes from
`CNAME` (`https://one-pointsafety.com`, no www).

- After editing `PAGES` or adding a page: `python3 scripts/build_seo.py`,
  then `python3 scripts/build_seo.py --check`, and commit. Don't hand-edit
  between the `<!-- seo:start -->` / `<!-- seo:end -->` markers.
- Every root `*.html` is added to the sitemap automatically. A new page
  without a `PAGES` entry still gets listed, but `--check` fails until you
  give it a title and description.
- `<lastmod>` is per page. Each page lists the files its content comes from,
  and their hash is stored in `scripts/sitemap_state.json`. The daily
  `update-data.yml` run calls `build_seo.py --sitemap-only` and commits the
  sitemap with the data. `meta.json`'s `last_updated` timestamp is ignored.
- `og-image.png` (1200×630) is a placeholder social preview image. Replace
  it with any 1200×630 PNG at the same path.
