#!/usr/bin/env python3
"""
Builds the static JSON files the college football (NCAA FBS) page loads,
from CollegeFootballData.com (CFBD) play-by-play -- the counterpart of
build_data.py for the NFL side.

Usage:
    CFBD_API_KEY=... python3 scripts/build_ncaa_data.py [--season 2026]
        [--out data/ncaa] [--mode daily|weekend] [--refetch-all]

The API key is read only from the CFBD_API_KEY environment variable (a
GitHub Actions secret in CI) and is never written anywhere.

CFBD's free tier is 1,000 calls per calendar month, so this script is
built around a call budget rather than "download everything":

  * /info (free) is checked first; under 100 remaining calls the run
    keeps the existing data and exits without spending anything.
  * /games (1 call) gives the whole season's schedule and completion status.
  * /plays is fetched one call per week (never per game), and only for
    weeks whose set of completed games grew since the last fetch. Raw
    plays are never stored: the per-week event files (<out>/events/
    week-NN.json) are the event store (weeks not refetched keep last run's
    events), and <out>/cache/weeks.json keeps only per-week fetch state
    plus per-game counts for totals.json.
  * /rankings (1 call, every week's polls at once) only when a newer AP
    poll than the cached one should exist -- about once a week.
  * /teams/fbs (1 call) only when the committed team cache is missing or
    from another season.

A typical daily run spends 1-2 calls (3 on the day a new poll is due);
a weekend (--mode weekend) run spends 1-2. See README.md ("College football") for the details.
"""

import argparse
import collections
import datetime
import json
import os
import pathlib
import re
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from ncaa_oddity_rules import (
    ODDITIES,
    RARE_PENALTY_MIN_COUNT,
    RARE_PENALTY_PER_100_GAMES,
    RARE_PENALTY_WHY,
)

API = "https://api.collegefootballdata.com"
TIER_RANK = {"wild": 3, "rare": 2, "uncommon": 1}
CENTRAL = ZoneInfo("America/Chicago")
SOURCE = "CollegeFootballData.com API"
MIN_REMAINING = 100          # below this, keep existing data and stop
MAX_CALLS_PER_RUN = 10       # hard ceiling, whatever the mode
DAILY_WEEK_FETCH_CAP = 4     # /plays calls per daily run (unless --refetch-all)
MISSING_PLAYS_RETRY_DAYS = 5 # keep retrying a week whose final games lack plays for this long


# ---------------------------------------------------------------- API client

class CFBD:
    def __init__(self, max_calls):
        key = os.environ.get("CFBD_API_KEY", "").strip()
        if not key:
            sys.exit(
                "CFBD_API_KEY is not set. Export it locally (export CFBD_API_KEY=...) "
                "or add it as a repository secret for GitHub Actions."
            )
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {key}"
        self._session.headers["Accept"] = "application/json"
        self.calls = 0
        self.max_calls = max_calls
        self.remaining = None

    def info(self):
        """The /info endpoint is free (doesn't count against the quota)."""
        try:
            resp = self._session.get(f"{API}/info", timeout=30)
            resp.raise_for_status()
            data = resp.json()
            self.remaining = data.get("remainingCalls")
            return data
        except (requests.RequestException, ValueError) as exc:
            print(f"warning: /info failed ({exc.__class__.__name__}); continuing", file=sys.stderr)
            return None

    def get(self, path, params, retries=3):
        if self.calls >= self.max_calls:
            raise RuntimeError(f"call budget for this run ({self.max_calls}) exhausted before {path}")
        for attempt in range(retries):
            try:
                resp = self._session.get(f"{API}{path}", params=params, timeout=120)
            except requests.RequestException as exc:
                if attempt == retries - 1:
                    raise RuntimeError(f"{path} failed: {exc.__class__.__name__}") from None
                time.sleep(2 ** (attempt + 1))
                continue
            remaining = resp.headers.get("X-CallLimit-Remaining")
            # 5xx from the CDN in front of the API doesn't carry the header
            # and (as far as we've seen) doesn't count against the quota.
            if resp.status_code >= 500 and attempt < retries - 1:
                print(f"  {path} {params} -> {resp.status_code}, retrying")
                time.sleep(2 ** (attempt + 1))
                continue
            if remaining is not None:
                self.calls += 1
                self.remaining = int(remaining)
            print(f"  GET {path} {params} -> {resp.status_code}; X-CallLimit-Remaining: {remaining}")
            if resp.status_code == 401:
                sys.exit("CFBD rejected the API key (401). Check the CFBD_API_KEY secret.")
            resp.raise_for_status()
            return resp.json()


# ------------------------------------------------------------- season / time

def current_season(today=None):
    today = today or datetime.date.today()
    # The CFB season named "2026" runs late Aug 2026 - mid Jan 2027.
    # January (and the rest of the offseason before August) belongs to the
    # season that started the previous calendar year.
    return today.year if today.month >= 8 else today.year - 1


def parse_utc(s):
    if not s:
        return None
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def kickoff_fields(game):
    start = parse_utc(game.get("startDate"))
    if start is None:
        return None, None, None, ""
    local = start.astimezone(CENTRAL)
    tbd = bool(game.get("startTimeTBD"))
    h12 = local.hour % 12 or 12
    kickoff = None if tbd else f"{h12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} CT"
    return local.date().isoformat(), local.strftime("%A"), kickoff, local.strftime("%H:%M")


# --------------------------------------------------------------- text parsing

# "D.Lagway", "K. Osborne", "T.Williams Jr.", "M.Ta'ase", "T. Hickman-Collins".
# The initial is 1-3 letters and the surname needs a lowercase letter, so
# all-caps text like "1ST DOWN. NO PLAY" doesn't parse as a name.
NAME = r"[A-Z][a-z]{0,2}\.\s?[A-Z][A-Za-z'\-]*[a-z][A-Za-z'\-]*(?:\s(?:Jr\.|Sr\.|II|III|IV|V)(?![A-Za-z]))?"
NAME_RE = re.compile(rf"(?<![A-Za-z])({NAME})")
HOLDER_LS_RE = re.compile(r"\((?:H|LS):[^)]*\)")
PASSER_RE = re.compile(rf"(?:#\d+\s+)?({NAME})\s+pass\b")
RECEIVER_RE = re.compile(rf"pass complete\b[^#]*?\bto\s+(?:#\d+\s+)?({NAME})")
RUSHER_RE = re.compile(rf"(?:#\d+\s+)?({NAME})\s+(?:rush|run)\b")
SPECIALIST_RE = re.compile(rf"(?:#\d+\s+)?({NAME})\s+(?:punt|onside kickoff|kickoff|field goal attempt|kick attempt)\b")
END_CLOCK_RE = re.compile(r"clock (\d{1,2}):(\d{2})")
FG_DIST_RE = re.compile(r"from (\d+) (?:yards|yds|yard)", re.I)

# Modern CFBD text: "PENALTY ISU Pass Interference (#9 J.Singletary) 15 yards ..."
NEW_PEN_RE = re.compile(
    r"PENALTY\s+([^\s#]\S*)\s+(.+?)(?=\s+\(#|\s+\(\d|\s+\d+\s+yards?|\s+declined|\s+offsetting|\s+off-setting|\s+enforced|\s{2,}|[.,;]|$)"
)
# Older terse text: "Iowa State Penalty, False Start (5 Yards) to the ISU 20"
OLD_PEN_RE = re.compile(r"([A-Z][\w&'.\- ]*?)\s+Penalty,\s*(.+?)(?=\s*\(|\s+declined|\s+offsetting|[,;]|$)")

# Phase 1 injury flag: same idea as the NFL side's INJURY_RE. CFBD writes
# "#9 J.Singletary injured on the play".
INJURY_RE = re.compile(r"\binjured\b", re.I)


def parse_penalty(text):
    """First penalty on the play that wasn't declined/offset: (team_abbr, type)."""
    for m in NEW_PEN_RE.finditer(text):
        tail = text[m.end(): m.end() + 40].lower()
        if "declined" in tail[:12] or "offsetting" in tail[:14] or "off-setting" in tail[:14]:
            continue
        return m.group(1).strip(), clean_penalty(m.group(2))
    for m in OLD_PEN_RE.finditer(text):
        tail = text[m.end(): m.end() + 30].lower()
        if "declined" in tail or "offsetting" in tail:
            continue
        return m.group(1).strip(), clean_penalty(m.group(2))
    return None, None


# Same foul, different spellings across CFBD's text formats.
PENALTY_ALIASES = {
    "holding": "Holding",
    "passinterference": "Pass Interference",
    "Offensive Holding": "Holding",
    "Defensive Holding": "Holding",
    "Defensive Offside": "Offside",
    "Roughing Passer": "Roughing the Passer",
    "Sideline Inteference": "Sideline Interference",
}


def clean_penalty(name):
    """Normalize a parsed penalty name: drop the "ST " (special teams) and
    "UNS:"/"UNR:" prefixes and a trailing player fragment ("Face Mask #12 B",
    "Intentional Grounding on LUSTER"), and tidy capitalization."""
    name = name.strip()
    name = re.sub(r"^(?:ST|St)\s+", "", name)
    name = re.sub(r"^[A-Z]{2,4}:\s*", "", name)
    name = re.sub(r"\s+#\d+.*$", "", name)
    name = re.sub(r"\s+on\s+[A-Z][A-Z'\-.]+$", "", name)
    small = {"of", "the", "in", "on", "to", "a"}
    words = name.split()
    name = " ".join(w.lower() if i and w.lower() in small else w for i, w in enumerate(words))
    return PENALTY_ALIASES.get(name, name)


def first(regex, text):
    m = regex.search(text)
    return m.group(1).strip() if m else ""


def play_names(text):
    clean = HOLDER_LS_RE.sub("", text)
    names = []
    for m in NAME_RE.finditer(clean):
        n = m.group(1).strip()
        if n not in names:
            names.append(n)
        if len(names) >= 4:
            break
    return names


def team_matcher(teams):
    """{school: set of uppercase tokens a play's text might use for it}."""
    lookup = {}
    for t in teams:
        tokens = {t["school"].upper(), t["school"].upper().replace(" ", "")}
        if t.get("abbreviation"):
            tokens.add(t["abbreviation"].upper())
        for alt in t.get("alternateNames") or []:
            tokens.add(alt.upper())
            tokens.add(alt.upper().replace(" ", ""))
        lookup[t["school"]] = tokens
    return lookup


def _subseq(short, long):
    it = iter(long)
    return all(ch in it for ch in short)


def abbr_matches(abbr, school, lookup):
    a = abbr.upper()
    if a in lookup.get(school, set()):
        return True
    full = re.sub(r"[^A-Z]", "", school.upper())
    return bool(full) and a[0] == full[0] and _subseq(a, full)


YARDLINE_TOKEN_RE = re.compile(r"\b(?:the|at|from|to)\s+([A-Z][A-Z&]{1,9})\s?\d{2}\b")


def game_abbr_map(df, lookup):
    """{game_id: {text_abbr: school}}. Play text uses ESPN-style abbreviations
    (MIZZOU, KSTATE) that don't always match CFBD's team data. Each game's
    yard-line markers use exactly two of them; if one matches a team
    directly, the other must be the opponent."""
    out = {}
    for gid, grp in df.groupby("game_id"):
        home, away = grp["home"].iloc[0], grp["away"].iloc[0]
        tokens = collections.Counter()
        for s in grp["text"]:
            tokens.update(YARDLINE_TOKEN_RE.findall(s))
        top = [t for t, n in tokens.most_common(2) if n >= 3]
        m = {}
        for tok in top:
            for school in (home, away):
                if school and abbr_matches(tok, school, lookup):
                    m.setdefault(tok, school)
        if len(top) == 2 and len(m) == 1:
            known_tok, known_school = next(iter(m.items()))
            other_tok = top[1] if top[0] == known_tok else top[0]
            m[other_tok] = away if known_school == home else home
        out[gid] = m
    return out


def penalty_team(abbr, offense, defense, lookup, per_game):
    if not isinstance(abbr, str) or not abbr:
        return None
    if abbr.upper() in per_game:
        return per_game[abbr.upper()]
    for school in (offense, defense):
        if school and abbr_matches(abbr, school, lookup):
            return school
    return None


# ------------------------------------------------------------ play processing

NON_PLAYS = {"Timeout", "End Period", "End of Half", "End of Game", "End of Regulation", "Kickoff", "Penalty"}
# Rows that aren't snaps at all; they sometimes carry an already-updated
# score, which would otherwise steal the points from the scoring play.
NO_SNAP = {"Timeout", "End Period", "End of Half", "End of Game", "End of Regulation"}


def plays_frame(plays):
    """Raw CFBD plays -> dataframe with the derived columns ncaa_oddity_rules expects."""
    rows = []
    for p in plays:
        text = (p.get("playText") or "").strip()
        clock = p.get("clock") or {}
        secs = None
        if clock.get("minutes") is not None:
            secs = int(clock.get("minutes") or 0) * 60 + int(clock.get("seconds") or 0)
        try:
            pid = int(p.get("id"))
        except (TypeError, ValueError):
            pid = 0
        home_is_off = p.get("offense") == p.get("home")
        off_s, def_s = p.get("offenseScore") or 0, p.get("defenseScore") or 0
        rows.append(
            {
                "game_id": str(p.get("gameId")),
                "pid": pid,
                "drive": p.get("driveNumber") or 0,
                "playnum": p.get("playNumber") or 0,
                "offense": p.get("offense") or "",
                "defense": p.get("defense") or "",
                "home": p.get("home") or "",
                "away": p.get("away") or "",
                "home_post": off_s if home_is_off else def_s,
                "away_post": def_s if home_is_off else off_s,
                "period": int(p.get("period") or 0),
                "secs": secs,
                "ptype": p.get("playType") or "",
                "text": text,
                "yards": int(p.get("yardsGained") or 0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["game_id", "drive", "playnum", "pid"]).reset_index(drop=True)
    df["order"] = df.groupby("game_id").cumcount()

    # Score deltas. CFBD scores are post-play; a stray row showing an older
    # score shouldn't register as points, so compare against the running
    # max rather than the previous row.
    no_snap = df["ptype"].isin(NO_SNAP)
    for side in ("home", "away"):
        post = df[f"{side}_post"].where(~no_snap)
        run_max = post.groupby(df["game_id"]).cummax().groupby(df["game_id"]).ffill().fillna(0)
        prev = run_max.groupby(df["game_id"]).shift(1).fillna(0)
        df[f"{side}_delta"] = (post - prev).clip(lower=0).fillna(0).astype(int)
        df[f"{side}_pre"] = prev.astype(int)
        df[f"{side}_post_clean"] = run_max.astype(int)
    off_home = df["offense"] == df["home"]
    pick = lambda h, a: h.where(off_home, a)
    df["off_delta"] = pick(df["home_delta"], df["away_delta"])
    df["def_delta"] = pick(df["away_delta"], df["home_delta"])
    df["off_pre"] = pick(df["home_pre"], df["away_pre"])
    df["def_pre"] = pick(df["away_pre"], df["home_pre"])
    df["off_post"] = pick(df["home_post_clean"], df["away_post_clean"])
    df["def_post"] = pick(df["away_post_clean"], df["home_post_clean"])
    home_final = df.groupby("game_id")["home_post_clean"].transform("max")
    away_final = df.groupby("game_id")["away_post_clean"].transform("max")
    df["off_final"] = pick(home_final, away_final)
    df["def_final"] = pick(away_final, home_final)

    t = df["text"]
    # Clock after the play: "clock MM:SS" in the text, else the snap clock.
    end = t.str.findall(END_CLOCK_RE).map(lambda ms: int(ms[-1][0]) * 60 + int(ms[-1][1]) if ms else None)
    df["end_secs"] = end.where(end.notna(), df["secs"])
    fg_dist = t.str.extract(FG_DIST_RE)[0].astype(float)
    is_fg = df["ptype"].str.contains("Field Goal", case=False)
    df.loc[is_fg & fg_dist.notna(), "yards"] = fg_dist[is_fg & fg_dist.notna()].astype(int)

    no_play = t.str.contains("NO PLAY", regex=False)
    big_delta = (df["off_delta"] >= 6) | (df["def_delta"] >= 6)
    tagged_td = df["ptype"].str.contains("Touchdown", case=False) & ~(no_play & ~big_delta)
    df["is_td"] = tagged_td | (t.str.contains("TOUCHDOWN", regex=False) & big_delta)
    df["try_text"] = t.map(lambda s: s.rsplit("TOUCHDOWN", 1)[1] if "TOUCHDOWN" in s else "")
    df["passer"] = t.map(lambda s: first(PASSER_RE, s))
    df["receiver"] = t.map(lambda s: first(RECEIVER_RE, s))
    df["rusher"] = t.map(lambda s: first(RUSHER_RE, s))
    return df


def build_ctx(df):
    specialists = collections.defaultdict(set)
    pass_atts = collections.Counter()
    touches = collections.defaultdict(set)
    kick_rows = df["ptype"].str.contains(r"Punt|Kickoff|Field Goal", case=False)
    for _, r in df[kick_rows].iterrows():
        key = (r["game_id"], r["offense"])
        for m in SPECIALIST_RE.finditer(r["text"]):
            specialists[key].add(m.group(1).strip())
    for _, r in df[(df["passer"] != "") | (df["receiver"] != "") | (df["rusher"] != "")].iterrows():
        key = (r["game_id"], r["offense"])
        if r["passer"]:
            pass_atts[key + (r["passer"],)] += 1
        for n in (r["receiver"], r["rusher"]):
            if n:
                touches[key].add(n)

    # For each row, the offense on the next *real* play strictly after it
    # (skipping timeouts, period ends, kickoffs/re-kicks and penalty-only
    # rows): reverse, shift by one, forward-fill, reverse.
    real = ~df["ptype"].isin(NON_PLAYS)
    next_offense = df["offense"].where(real).groupby(df["game_id"]).transform(
        lambda s: s.iloc[::-1].shift(1).ffill().iloc[::-1]
    )
    return {
        "specialists": dict(specialists),
        "pass_atts": dict(pass_atts),
        "non_passing_touch": dict(touches),
        "next_offense": next_offense,
    }


def oddity_masks(df, ctx):
    masks = {}
    for rule in ODDITIES:
        try:
            masks[rule["id"]] = rule["detect"](df, ctx).fillna(False).astype(bool)
        except Exception as exc:  # keep one bad rule from killing the whole run
            print(f"warning: oddity rule {rule['id']} failed: {exc!r}", file=sys.stderr)
            masks[rule["id"]] = pd.Series(False, index=df.index)
    return masks


def process_week(plays, team_lookup, ap_games):
    """Raw plays for one week -> notable events (already in events.json
    shape, minus id/tier/rare flags which depend on the whole season) plus
    per-game counts for totals.json. Injury mentions are only kept for
    games involving an AP Top 25 team (`ap_games`, a set of game ids)."""
    df = plays_frame(plays)
    if df.empty:
        return {"events": [], "counts": {}, "penalties": {}, "games_with_plays": []}
    ctx = build_ctx(df)
    masks = oddity_masks(df, ctx)
    abbr_map = game_abbr_map(df, team_lookup)

    t = df["text"]
    pen_parsed = t.map(parse_penalty)
    pen_abbr = pen_parsed.map(lambda x: x[0])
    pen_type = pen_parsed.map(lambda x: x[1])
    is_pen = pen_type.notna() | ((df["ptype"] == "Penalty") & ~t.str.contains(r"declined|offsetting", case=False))
    is_fg = df["ptype"] == "Field Goal Good"
    is_safety = masks["safety"] | masks["one_pt_safety"]
    is_int = df["ptype"].str.contains("Interception", case=False)
    is_fum = df["ptype"].isin(["Fumble Recovery (Opponent)", "Fumble Return Touchdown"])
    is_sack = (df["ptype"] == "Sack") | (df["ptype"].str.contains("Fumble") & t.str.contains(r"\bsacked\b"))
    is_injury = t.str.contains(INJURY_RE) & df["game_id"].isin(ap_games)
    is_oddity = pd.Series(False, index=df.index)
    for m in masks.values():
        is_oddity |= m

    def base_type(i):
        if df.at[i, "is_td"]:
            return "td"
        if is_fg.at[i]:
            return "fg"
        if is_safety.at[i]:
            return "safety"
        if is_pen.at[i]:
            return "pen"
        if is_int.at[i]:
            return "int"
        if is_fum.at[i]:
            return "fum"
        if is_sack.at[i]:
            return "sack"
        return None

    notable = df["is_td"] | is_fg | is_safety | is_pen | is_int | is_fum | is_sack | is_injury | is_oddity
    oid_rows = collections.defaultdict(list)
    for oid, m in masks.items():
        for i in df.index[m]:
            oid_rows[i].append(oid)

    events = []
    for i in df.index[notable]:
        r = df.loc[i]
        btype = base_type(i)
        off, de = r["offense"], r["defense"]
        if btype == "pen":
            team = penalty_team(pen_abbr.at[i], off, de, team_lookup, abbr_map.get(r["game_id"], {}))
        elif r["def_delta"] > 0 and r["def_delta"] >= r["off_delta"]:
            team = de
        elif r["off_delta"] > 0:
            team = off
        elif btype in ("sack", "int", "fum"):
            team = de
        else:
            team = off
        injured = bool(is_injury.at[i])
        events.append(
            {
                "id": 0,
                "game_id": r["game_id"],
                "week": None,
                "type": btype or ("inj" if injured else "odd"),
                "penalty_type": pen_type.at[i] if btype == "pen" and isinstance(pen_type.at[i], str) else None,
                "rare_penalty": False,
                "oddity_ids": oid_rows.get(i, []),
                "tier": None,
                "detail": HOLDER_LS_RE.sub("", r["text"]).replace("  ", " ").strip(),
                "team": team or None,
                "qtr": int(r["period"]) or None,
                "clock_secs": None if pd.isna(r["secs"]) else int(r["secs"]),
                "players": play_names(r["text"]),
                "injury": injured,
                "ap_top25": r["game_id"] in ap_games,
                "_order": int(r["order"]),
            }
        )

    counts, penalties = {}, {}
    for gid, idx in df.groupby("game_id").groups.items():
        counts[gid] = {
            "td": int(df.loc[idx, "is_td"].sum()),
            "fg": int(is_fg[idx].sum()),
            "safety": int(is_safety[idx].sum()),
            "pen": int(is_pen[idx].sum()),
            "inj": int(is_injury[idx].sum()),
            "int": int(is_int[idx].sum()),
            "fum": int(is_fum[idx].sum()),
            "sack": int(is_sack[idx].sum()),
        }
        pc = pen_type[idx][is_pen[idx]].fillna("Penalty (unspecified)").value_counts().to_dict()
        penalties[gid] = {k: int(v) for k, v in pc.items()}
    return {
        "events": events,
        "counts": counts,
        "penalties": penalties,
        "games_with_plays": sorted(df["game_id"].unique().tolist()),
    }


# -------------------------------------------------------------------- caches

def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, obj, pretty=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n")
    else:
        path.write_text(json.dumps(obj, separators=(",", ":"), default=str, allow_nan=False))


def week_key(stype, week):
    return f"{'post' if stype == 'postseason' else 'reg'}_{week:02d}"


def slim_team(t):
    loc = t.get("location") or {}
    return {
        "id": t.get("id"),
        "school": t.get("school"),
        "abbreviation": t.get("abbreviation"),
        "alternateNames": t.get("alternateNames") or [],
        "mascot": t.get("mascot"),
        "conference": t.get("conference"),
        "venue": loc.get("name"),
        "city": loc.get("city"),
        "state": loc.get("state"),
        "timezone": loc.get("timezone"),
    }


def ap_by_week(rankings):
    polls = {}
    for wk in rankings or []:
        for poll in wk.get("polls") or []:
            if poll.get("poll") == "AP Top 25":
                key = f"{wk.get('seasonType')}-{wk.get('week')}"
                polls[key] = {r["school"]: r["rank"] for r in poll.get("ranks") or [] if r.get("school")}
    return polls


def ap_for_game(polls, stype, week):
    """The AP poll in force for a game: the latest regular-season poll whose
    week <= the game's week (CFBD's week-N poll is the one released before
    week-N games; week 1 is the preseason poll). Bowls/CFP use the last
    regular-season poll."""
    reg = sorted(int(k.split("-")[1]) for k in polls if k.startswith("regular-"))
    if not reg:
        return {}
    if stype == "postseason":
        return polls[f"regular-{reg[-1]}"]
    usable = [w for w in reg if w <= week]
    return polls[f"regular-{usable[-1] if usable else reg[0]}"]


# ---------------------------------------------------------------------- main

def offseason_meta(out_dir, season, reason):
    print(reason)
    meta = {
        "season": season,
        "has_data": False,
        "league": "ncaa",
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": SOURCE,
    }
    write_json(out_dir / "meta.json", meta, pretty=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--out", type=str, default="data/ncaa")
    ap.add_argument("--mode", choices=["daily", "weekend"], default="daily",
                    help="weekend: only refetch the current week, skip rankings")
    ap.add_argument("--refetch-all", action="store_true",
                    help="refetch every week with completed games (after changing a rule); ~1 call per week")
    args = ap.parse_args()

    season = args.season or current_season()
    out_dir = pathlib.Path(args.out)
    cache_dir = out_dir / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    max_calls = 40 if args.refetch_all else MAX_CALLS_PER_RUN
    api = CFBD(max_calls)
    info = api.info()
    if info is not None:
        print(f"CFBD quota: {info.get('remainingCalls')} of {info.get('monthlyLimit')} calls left "
              f"this month (resets {info.get('resetAt')})")
        if (info.get("remainingCalls") or 0) < MIN_REMAINING:
            print(f"Fewer than {MIN_REMAINING} calls left; keeping existing data and skipping this run.")
            return

    # --- teams (cached in the repo, refreshed once a season) ---
    teams_path = cache_dir / "teams.json"
    teams_cache = read_json(teams_path, {})
    if teams_cache.get("season") != season or not teams_cache.get("teams"):
        print("Fetching FBS team info (once per season)...")
        teams = api.get("/teams/fbs", {"year": season})
        teams_cache = {"season": season, "teams": [slim_team(t) for t in teams]}
        write_json(teams_path, teams_cache, pretty=True)
    lookup = team_matcher(teams_cache["teams"])
    fbs = {t["school"] for t in teams_cache["teams"]}

    # --- schedule ---
    print("Fetching games...")
    games_raw = api.get("/games", {"year": season, "classification": "fbs"})
    games_raw = [g for g in games_raw
                 if g.get("homeClassification") == "fbs" or g.get("awayClassification") == "fbs"
                 or g.get("homeTeam") in fbs or g.get("awayTeam") in fbs]
    if not games_raw:
        offseason_meta(out_dir, season, f"No FBS games listed for {season}; writing offseason meta.json")
        return

    reg_max = max((g["week"] for g in games_raw if g.get("seasonType") == "regular"), default=0)

    def site_week(g):
        return g["week"] if g.get("seasonType") == "regular" else reg_max + g["week"]

    # --- AP poll: only fetched when a newer poll than the cached one should
    # exist. CFBD's week-N poll comes out before week-N games, so once any
    # week-N game has kicked off we expect to have it; until then the cache
    # is current and the call is skipped. Weekend runs never fetch it. ---
    now = datetime.datetime.now(datetime.timezone.utc)
    polls_path = cache_dir / "ap_polls.json"
    polls_cache = read_json(polls_path, {})
    fetch_polls, why = polls_cache.get("season") != season, "no poll cached for this season"
    if not fetch_polls and args.mode == "daily":
        cached_weeks = [int(k.split("-")[1]) for k in polls_cache.get("polls", {}) if k.startswith("regular-")]
        cached_max = max(cached_weeks, default=0)
        started = [g for g in games_raw if (parse_utc(g.get("startDate")) or now) <= now]
        latest_reg = max((g["week"] for g in started if g.get("seasonType") == "regular"), default=0)
        post_started = any(g.get("seasonType") == "postseason" for g in started)
        last_fetch = parse_utc(polls_cache.get("fetched_at")) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        if cached_max < latest_reg:
            fetch_polls, why = True, f"week {latest_reg} has started, cached poll is week {cached_max}"
        elif post_started and cached_max <= reg_max and (now - last_fetch).days >= 3:
            # Bowls use the final regular-season poll, released after the
            # conference championships. Check for it every few days.
            fetch_polls, why = True, "postseason started; checking for the final regular-season poll"
    if fetch_polls:
        print(f"Fetching AP poll ({why})...")
        try:
            rankings = api.get("/rankings", {"year": season})
            polls_cache = {"season": season, "fetched_at": now.isoformat(), "polls": ap_by_week(rankings)}
            write_json(polls_path, polls_cache, pretty=True)
        except (RuntimeError, requests.HTTPError) as exc:
            print(f"warning: rankings fetch failed ({exc}); using cached poll", file=sys.stderr)
    else:
        print("AP poll cache is current; skipping /rankings.")
    polls = polls_cache.get("polls", {}) if polls_cache.get("season") == season else {}
    game_rank = {}
    for g in games_raw:
        poll = ap_for_game(polls, g.get("seasonType"), g["week"])
        game_rank[str(g["id"])] = (poll.get(g.get("awayTeam")), poll.get(g.get("homeTeam")))
    ap_games = {gid for gid, (a, h) in game_rank.items() if a is not None or h is not None}

    # --- per-week fetch state + counts, and the existing event store ---
    # The per-week event files (events/week-NN.json) are themselves the
    # event store: weeks that aren't refetched keep their events from the
    # last run, so nothing large is duplicated in cache/. cache/weeks.json
    # holds only what decides refetching plus the per-game counts behind
    # totals.json.
    events_dir = out_dir / "events"
    state_path = cache_dir / "weeks.json"
    state = read_json(state_path, {})
    old_meta = read_json(out_dir / "meta.json", {})
    old_events = []
    if state.get("season") != season or old_meta.get("season") != season:
        state = {"season": season, "weeks": {}}
    else:
        for rel in old_meta.get("event_files", []):
            old_events.extend(read_json(out_dir / rel, []))
        legacy = out_dir / "events.json"  # pre-split single file
        if not old_events and legacy.exists():
            old_events = read_json(legacy, [])

    # --- decide which weeks' plays to (re)fetch ---
    by_week = collections.defaultdict(list)
    for g in games_raw:
        by_week[(g.get("seasonType"), g["week"])].append(g)

    stale = []
    for (stype, wk), gs in by_week.items():
        completed = {str(g["id"]) for g in gs if g.get("completed")}
        started = [g for g in gs if (parse_utc(g.get("startDate")) or now) <= now]
        if not completed and not started:
            continue
        ws = state["weeks"].get(week_key(stype, wk))
        last_start = max((parse_utc(g.get("startDate")) for g in gs if g.get("startDate")), default=now)
        if ws is None:
            reason = "not fetched yet"
        elif not completed <= set(ws.get("completed_ids", [])):
            reason = "new final games"
        elif (completed - set(ws.get("games_with_plays", []))) and \
                (now - last_start).days < MISSING_PLAYS_RETRY_DAYS and \
                (now - parse_utc(ws["fetched_at"])).total_seconds() > 6 * 3600:
            reason = "final games still missing plays"
        elif args.refetch_all and completed:
            reason = "refetch-all"
        else:
            continue
        stale.append((last_start, stype, wk, reason))
    stale.sort(reverse=True)  # most recent week first

    if args.mode == "weekend":
        stale = stale[:1]  # only the current (latest started) week
    elif not args.refetch_all:
        stale = stale[:DAILY_WEEK_FETCH_CAP]

    new_events, refetched_ids = [], set()
    for _, stype, wk, reason in stale:
        if api.calls >= api.max_calls:
            print("Run call ceiling reached; remaining weeks wait for the next run.")
            break
        if api.remaining is not None and api.remaining < MIN_REMAINING:
            print("Quota running low; not fetching more weeks this run.")
            break
        print(f"Fetching plays: {stype} week {wk} ({reason})...")
        params = {"year": season, "week": wk, "classification": "fbs"}
        if stype == "postseason":
            params["seasonType"] = "postseason"
        try:
            plays = api.get("/plays", params)
        except (RuntimeError, requests.HTTPError) as exc:
            print(f"warning: plays fetch failed for {stype} week {wk}: {exc}", file=sys.stderr)
            continue
        processed = process_week(plays, lookup, ap_games)
        week_ids = {str(g["id"]) for g in by_week[(stype, wk)]} | set(processed["games_with_plays"])
        refetched_ids |= week_ids
        new_events.extend(processed["events"])
        state["weeks"][week_key(stype, wk)] = {
            "fetched_at": now.isoformat(),
            "completed_ids": sorted(str(g["id"]) for g in by_week[(stype, wk)] if g.get("completed")),
            "games_with_plays": processed["games_with_plays"],
            "counts": processed["counts"],
            "penalties": processed["penalties"],
        }

    print(f"API calls this run: {api.calls} (X-CallLimit-Remaining now {api.remaining})")
    if not state["weeks"]:
        offseason_meta(out_dir, season, f"No play-by-play fetched yet for {season}; writing offseason meta.json")
        return
    write_json(state_path, state, pretty=True)

    with_plays = set()
    for ws in state["weeks"].values():
        with_plays.update(ws.get("games_with_plays", []))

    games = []
    for g in games_raw:
        gid = str(g["id"])
        if not (g.get("completed") or gid in with_plays):
            continue
        date, weekday, kickoff, sort_time = kickoff_fields(g)
        if date is None:
            continue
        away_rank, home_rank = game_rank[gid]
        games.append(
            {
                "id": gid,
                "week": site_week(g),
                "game_type": "POST" if g.get("seasonType") == "postseason" else "REG",
                "weekday": weekday,
                "date": date,
                "kickoff_ct": kickoff,
                "_sort_time": sort_time,
                "away": g.get("awayTeam"),
                "home": g.get("homeTeam"),
                "away_score": g.get("awayPoints"),
                "home_score": g.get("homePoints"),
                "location": "Neutral" if g.get("neutralSite") else "Home",
                "stadium": g.get("venue"),
                "roof": None,
                "final": bool(g.get("completed")),
                "away_conf": g.get("awayConference"),
                "home_conf": g.get("homeConference"),
                "away_rank": away_rank,
                "home_rank": home_rank,
                "ap_top25": gid in ap_games,
                "notes": g.get("notes") or None,
            }
        )
    games.sort(key=lambda g: (g["week"], g["date"], g.pop("_sort_time"), g["id"]))
    games_by_id = {g["id"]: g for g in games}

    # Merge: last run's events for weeks not refetched + this run's.
    for i, e in enumerate(old_events):
        e["_order"] = -i  # old files are already newest-first within a game
    events = [e for e in old_events if e["game_id"] not in refetched_ids] + new_events
    events = [e for e in events if e["game_id"] in games_by_id]

    # Rare penalties: per-100-games rate over the whole season so far.
    pen_counts = collections.Counter()
    for ws in state["weeks"].values():
        for gid, pc in ws.get("penalties", {}).items():
            if gid in games_by_id:
                pen_counts.update(pc)
    threshold = max(RARE_PENALTY_MIN_COUNT, round(RARE_PENALTY_PER_100_GAMES * len(games) / 100))
    rare_penalty_types = {k for k, v in pen_counts.items() if v <= threshold and k != "Penalty (unspecified)"}

    odd_tier = {o["id"]: TIER_RANK[o["tier"]] for o in ODDITIES}
    for e in events:
        g = games_by_id[e["game_id"]]
        e["week"] = g["week"]
        e["ap_top25"] = g["ap_top25"]
        e["oddity_ids"] = [o for o in e["oddity_ids"] if o in odd_tier]
        e["rare_penalty"] = e["type"] == "pen" and e["penalty_type"] in rare_penalty_types
        tier = max((odd_tier[i] for i in e["oddity_ids"]), default=None)
        e["tier"] = TIER_RANK["rare"] if tier is None and e["rare_penalty"] else tier
        e["_date"] = g["date"]
    events.sort(key=lambda e: (e["week"], e["_date"], e["game_id"], -e["_order"]))
    # One file per week, ids numbered within the file (the page renumbers
    # after concatenating), so a run that only touches this week rewrites
    # only this week's file and every other file stays byte-identical.
    events_by_week = collections.defaultdict(list)
    for e in events:
        e.pop("_order", None)
        e.pop("_date", None)
        events_by_week[e["week"]].append(e)
    for wk_events in events_by_week.values():
        for i, e in enumerate(wk_events):
            e["id"] = i

    # Totals (reference/debugging, same shape as the NFL file). Injuries
    # count AP Top 25 games only, matching what the page shows.
    def empty():
        return {"td": 0, "fg": 0, "safety": 0, "pen": 0, "inj": 0, "int": 0, "fum": 0, "sack": 0, "games": 0}

    season_t, week_t = empty(), collections.defaultdict(empty)
    season_pen, week_pen = collections.Counter(), collections.defaultdict(collections.Counter)
    for ws in state["weeks"].values():
        for gid, cnt in ws.get("counts", {}).items():
            g = games_by_id.get(gid)
            if not g:
                continue
            for bucket in (season_t, week_t[g["week"]]):
                for k, v in cnt.items():
                    bucket[k] += v
                bucket["games"] += 1
            season_pen.update(ws["penalties"].get(gid, {}))
            week_pen[g["week"]].update(ws["penalties"].get(gid, {}))

    def with_pens(t, pc):
        t = dict(t)
        t["penalties_by_type"] = {
            k: {"count": int(v), "rare": k in rare_penalty_types} for k, v in pc.most_common()
        }
        return t

    totals = {
        "season": with_pens(season_t, season_pen),
        "weeks": {str(w): with_pens(week_t[w], week_pen[w]) for w in sorted(week_t)},
    }

    oddity_counts = collections.Counter()
    for e in events:
        oddity_counts.update(e["oddity_ids"])
    oddity_catalog = [
        {
            "id": r["id"],
            "tier": r["tier"],
            "label": r["label"],
            "why": r["why"],
            "count": oddity_counts.get(r["id"], 0),
            "rarity": None,  # no college rarity script yet
        }
        for r in ODDITIES
    ]
    rare_penalty_catalog = {
        pt: {"why": RARE_PENALTY_WHY.get(pt, "Called only a handful of times across FBS this season."), "rarity": None}
        for pt in sorted(rare_penalty_types)
    }

    weeks = sorted({g["week"] for g in games})
    week_labels = {}
    post_weeks = sorted({g["week"] for g in games if g["game_type"] == "POST"})
    for i, w in enumerate(post_weeks):
        week_labels[str(w)] = "Postseason" if len(post_weeks) == 1 else f"Postseason {i + 1}"
    current_week = max((g["week"] for g in games if g["final"]), default=max(weeks, default=1))

    meta = {
        "season": season,
        "has_data": True,
        "league": "ncaa",
        "current_week": current_week,
        "weeks": weeks,
        "week_labels": week_labels,
        "last_updated": now.isoformat(),
        "source": SOURCE,
        "games_count": len(games),
        "rare_penalty_threshold": threshold,
        "event_files": [f"events/week-{w:02d}.json" for w in sorted(events_by_week)],
    }

    write_json(out_dir / "games.json", games)
    events_dir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for w, wk_events in events_by_week.items():
        path = events_dir / f"week-{w:02d}.json"
        keep.add(path.name)
        write_json(path, wk_events)
    for stale_file in events_dir.glob("week-*.json"):
        if stale_file.name not in keep:
            stale_file.unlink()
    (out_dir / "events.json").unlink(missing_ok=True)
    write_json(out_dir / "totals.json", totals)
    write_json(out_dir / "oddities.json", {"oddities": oddity_catalog, "rare_penalties": rare_penalty_catalog})
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"Done. {len(games)} games, {len(events)} notable plays, "
          f"{len(rare_penalty_types)} rare penalty types (<= {threshold} calls).")
    print("Oddities found:", json.dumps({k: v for k, v in oddity_counts.items() if v}, indent=2))


if __name__ == "__main__":
    main()
