#!/usr/bin/env python3
"""
Builds the static JSON files the One-Point Safety front end loads, from
free nflverse play-by-play data.

Usage:
    python3 scripts/build_data.py [--season 2026] [--out data]

Run with no arguments to auto-detect the current NFL season and write into
./data. See README.md for how this fits into the GitHub Action, and how to
add a new oddity rule (scripts/oddity_rules.py).
"""

import argparse
import collections
import datetime
import io
import json
import pathlib
import re
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from oddity_rules import ODDITIES, RARE_PENALTY_MAX_SEASON_COUNT, RARE_PENALTY_WHY

NFLVERSE_RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
TIER_RANK = {"wild": 3, "rare": 2, "uncommon": 1}

# Columns checked, in priority order, for player names to show on a play.
NAME_COLS = [
    "td_player_name",
    "passer_player_name",
    "receiver_player_name",
    "rusher_player_name",
    "interception_player_name",
    "sack_player_name",
    "half_sack_1_player_name",
    "half_sack_2_player_name",
    "safety_player_name",
    "kicker_player_name",
    "penalty_player_name",
    "punter_player_name",
    "punt_returner_player_name",
    "kickoff_returner_player_name",
    "fumble_recovery_1_player_name",
    "forced_fumble_player_1_player_name",
    "blocked_player_name",
]


def current_season(today=None):
    today = today or datetime.date.today()
    # The NFL season named "2026" runs ~Sep 2026 - Feb 2027. Jan/Feb belong
    # to the season that started the previous calendar year.
    return today.year - 1 if today.month <= 2 else today.year


def fetch(url, binary=True, retries=3):
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=60)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code >= 500 and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.content if binary else resp.text


def load_pbp(season):
    url = f"{NFLVERSE_RELEASES}/pbp/play_by_play_{season}.parquet"
    content = fetch(url)
    if content is None:
        return None
    return pd.read_parquet(io.BytesIO(content))


def load_schedules():
    url = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
    content = fetch(url, binary=False)
    return pd.read_csv(io.StringIO(content), low_memory=False)


def load_roster(season):
    url = f"{NFLVERSE_RELEASES}/weekly_rosters/roster_weekly_{season}.csv"
    content = fetch(url, binary=False)
    if content is None:
        return None
    return pd.read_csv(io.StringIO(content), low_memory=False)


def position_lookup(roster_df):
    if roster_df is None or roster_df.empty:
        return {}
    # A player's position rarely changes mid-season; take the most common
    # one recorded across that player's weekly snapshots.
    modes = (
        roster_df.dropna(subset=["gsis_id", "position"])
        .groupby("gsis_id")["position"]
        .agg(lambda s: s.value_counts().idxmax())
    )
    return modes.to_dict()


def et_to_ct(gametime):
    if not isinstance(gametime, str) or ":" not in gametime:
        return None
    h, m = gametime.split(":")
    h = (int(h) - 1) % 24
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12
    h12 = 12 if h12 == 0 else h12
    return f"{h12}:{m} {suffix} CT"


def build_games(pbp, schedules, season):
    sched = schedules[schedules["season"] == season].copy()
    sched_by_id = sched.set_index("game_id")

    game_ids = pbp["game_id"].dropna().unique()
    games = []
    for gid in game_ids:
        if gid not in sched_by_id.index:
            continue
        row = sched_by_id.loc[gid]
        games.append(
            {
                "id": gid,
                "week": int(row["week"]),
                "game_type": row["game_type"],
                "weekday": row["weekday"],
                "date": row["gameday"],
                "kickoff_ct": et_to_ct(row["gametime"]),
                # Raw "HH:MM" 24h ET, zero-padded -- safe to sort on directly.
                # Don't sort on kickoff_ct: its 12h "9:30 AM"/"1:00 PM" strings
                # don't compare correctly (an AM game would sort after a PM one).
                "_sort_time": row["gametime"] if isinstance(row["gametime"], str) else "",
                "away": row["away_team"],
                "home": row["home_team"],
                "away_score": None if pd.isna(row["away_score"]) else int(row["away_score"]),
                "home_score": None if pd.isna(row["home_score"]) else int(row["home_score"]),
                "location": row["location"],
                "stadium": row["stadium"] if isinstance(row["stadium"], str) else None,
                "roof": row["roof"] if isinstance(row["roof"], str) else None,
                "final": pd.notna(row["away_score"]) and pd.notna(row["home_score"]),
            }
        )
    games.sort(key=lambda g: (g["week"], g["date"], g.pop("_sort_time")))
    return games


def scoring_team(row):
    if row.get("touchdown") == 1 and isinstance(row.get("td_team"), str):
        return row["td_team"]
    if row.get("field_goal_result") == "made":
        return row.get("posteam")
    if row.get("safety") == 1:
        return row.get("defteam")
    if row.get("penalty") == 1:
        return row.get("penalty_team")
    if row.get("sack") == 1 or row.get("interception") == 1:
        return row.get("defteam")
    if row.get("fumble_lost") == 1 and isinstance(row.get("fumble_recovery_1_team"), str):
        return row.get("fumble_recovery_1_team")
    return row.get("posteam")


def row_names(row):
    names = []
    for col in NAME_COLS:
        v = row.get(col)
        if isinstance(v, str) and v not in names:
            names.append(v)
        if len(names) >= 4:
            break
    return names


def primary_type(row, is_injury=False):
    if row.get("touchdown") == 1:
        return "td"
    if row.get("field_goal_result") == "made":
        return "fg"
    if row.get("safety") == 1:
        return "safety"
    if row.get("penalty") == 1:
        return "pen"
    if row.get("interception") == 1:
        return "int"
    if row.get("fumble_lost") == 1:
        return "fum"
    if row.get("sack") == 1:
        return "sack"
    if is_injury:
        return "inj"
    return "odd"


# "injured" (not just "injur...") so this doesn't also match the unrelated
# "** Injury Update: X has returned to the game" follow-up notices.
INJURY_RE = re.compile(r"\binjured\b", re.IGNORECASE)


def build_oddity_masks(pbp, position_of):
    ctx = {"position_of": position_of}
    masks = {}
    for rule in ODDITIES:
        try:
            masks[rule["id"]] = rule["detect"](pbp, ctx).fillna(False)
        except Exception as exc:  # keep one bad rule from killing the whole run
            print(f"warning: oddity rule {rule['id']} failed: {exc}", file=sys.stderr)
            masks[rule["id"]] = pd.Series(False, index=pbp.index)
    return masks


def build_events(pbp, oddity_masks, rare_penalty_types):
    is_scoring = (
        (pbp["touchdown"] == 1)
        | (pbp["field_goal_result"] == "made")
        | (pbp["safety"] == 1)
    )
    is_turnover = (pbp["interception"] == 1) | (pbp["fumble_lost"] == 1)
    is_sack = pbp["sack"] == 1
    is_penalty = pbp["penalty"] == 1
    is_injury = pbp["desc"].fillna("").str.contains(INJURY_RE)
    is_oddity = pd.Series(False, index=pbp.index)
    for mask in oddity_masks.values():
        is_oddity = is_oddity | mask

    notable = is_scoring | is_turnover | is_sack | is_penalty | is_injury | is_oddity
    subset = pbp[notable].copy()

    oddity_ids_per_row = collections.defaultdict(list)
    for oid, mask in oddity_masks.items():
        for idx in pbp.index[mask & notable]:
            oddity_ids_per_row[idx].append(oid)

    events = []
    for idx, row in subset.iterrows():
        row_d = row.to_dict()
        injured = bool(is_injury.get(idx, False))
        ptype = primary_type(row_d, injured)
        odd_ids = oddity_ids_per_row.get(idx, [])
        is_rare_pen = ptype == "pen" and row_d.get("penalty_type") in rare_penalty_types
        tier = None
        if odd_ids:
            tier = max(TIER_RANK[next(o for o in ODDITIES if o["id"] == i)["tier"]] for i in odd_ids)
        elif is_rare_pen:
            tier = TIER_RANK["rare"]

        qtr = row_d.get("qtr")
        qtr = int(qtr) if pd.notna(qtr) else None
        secs = row_d.get("quarter_seconds_remaining")
        secs = int(secs) if pd.notna(secs) else None

        events.append(
            {
                "id": int(row_d["play_id"]) if pd.notna(row_d.get("play_id")) else idx,
                "game_id": row_d.get("game_id"),
                "week": int(row_d["week"]) if pd.notna(row_d.get("week")) else None,
                "type": ptype,
                "penalty_type": row_d.get("penalty_type") if ptype == "pen" else None,
                "rare_penalty": is_rare_pen,
                "oddity_ids": odd_ids,
                "tier": tier,
                "detail": row_d.get("desc"),
                "team": scoring_team(row_d),
                "qtr": qtr,
                "clock_secs": secs,
                "players": row_names(row_d),
                "injury": injured,
            }
        )

    events.sort(key=lambda e: (e["week"] or 0, e["game_id"] or "", -(e["qtr"] or 0), -(e["clock_secs"] or 0)))
    for i, e in enumerate(events):
        e["id"] = i
    return events


def build_totals(pbp, rare_penalty_types):
    def counts_for(df):
        out = {
            "td": int((df["touchdown"] == 1).sum()),
            "fg": int((df["field_goal_result"] == "made").sum()),
            "safety": int((df["safety"] == 1).sum()),
            "pen": int((df["penalty"] == 1).sum()),
            "inj": int(df["desc"].fillna("").str.contains(INJURY_RE).sum()),
            "int": int((df["interception"] == 1).sum()),
            "fum": int((df["fumble_lost"] == 1).sum()),
            "sack": int((df["sack"] == 1).sum()),
            "games": int(df["game_id"].nunique()),
        }
        pen_rows = df[df["penalty"] == 1]
        pen_counts = pen_rows["penalty_type"].value_counts().to_dict()
        out["penalties_by_type"] = {
            k: {"count": int(v), "rare": k in rare_penalty_types} for k, v in pen_counts.items()
        }
        return out

    totals = {"season": counts_for(pbp), "weeks": {}}
    for wk, df in pbp.groupby("week"):
        totals["weeks"][str(int(wk))] = counts_for(df)
    return totals


def compute_rare_penalty_types(pbp):
    pen_rows = pbp[pbp["penalty"] == 1]
    counts = pen_rows["penalty_type"].value_counts()
    return set(counts[counts <= RARE_PENALTY_MAX_SEASON_COUNT].index)


# Ranked most to least severe, for picking a single "rainiest"/"snowiest"
# game out of several candidates when nflverse's weather text doesn't give
# a quantitative amount for rain (it never does) or snow (sometimes does,
# handled separately via SNOW_INCHES_RE).
RAIN_SEVERITY = ["thunderstorm", "downpour", "heavy rain", "rain", "showers", "drizzle"]
SNOW_SEVERITY = ["blizzard", "heavy snow", "snow showers", "snow", "flurries"]
HUMIDITY_RE = re.compile(r"Humidity:\s*(\d+)\s*%")
SNOW_INCHES_RE = re.compile(r"(\d+)\s*(?:to\s*(\d+))?\s*inch", re.IGNORECASE)


def parse_weather_text(text):
    """nflverse's free-text per-game weather field, e.g.
    'Snow Showers, 3 to 5 inches expected. Temp: 33° F, Humidity: 91%, Wind: SE 5 mph'.
    Returns None if there's nothing usable (domes/closed roofs leave it blank)."""
    if not isinstance(text, str) or not text.strip() or "Temp: °" in text:
        return None
    condition = text.split("Temp:")[0].strip().strip(",").strip()
    cl = condition.lower()
    hm = HUMIDITY_RE.search(text)
    im = SNOW_INCHES_RE.search(cl)
    return {
        "condition": condition or None,
        "humidity": int(hm.group(1)) if hm else None,
        "rain_rank": next((i for i, kw in enumerate(RAIN_SEVERITY) if kw in cl), None),
        "snow_rank": next((i for i, kw in enumerate(SNOW_SEVERITY) if kw in cl), None),
        "snow_inches": float(im.group(2) or im.group(1)) if im else None,
    }


def build_weather(pbp, games):
    games_by_id = {g["id"]: g for g in games}
    rows = []
    for gid, grp in pbp.drop_duplicates("game_id").set_index("game_id").iterrows():
        g = games_by_id.get(gid)
        if not g or g.get("roof") != "outdoors" or pd.isna(grp.get("temp")):
            continue
        parsed = parse_weather_text(grp.get("weather")) or {}
        rows.append(
            {
                "game_id": gid,
                "week": g["week"],
                "away": g["away"],
                "home": g["home"],
                "date": g["date"],
                "stadium": g["stadium"],
                "temp": int(grp["temp"]),
                "wind": None if pd.isna(grp.get("wind")) else int(grp["wind"]),
                "humidity": parsed.get("humidity"),
                "condition": parsed.get("condition"),
                "rain_rank": parsed.get("rain_rank"),
                "snow_rank": parsed.get("snow_rank"),
                "snow_inches": parsed.get("snow_inches"),
            }
        )

    if not rows:
        return {"eligible_games": 0, "records": {}}

    # Keep `condition` and `snow_inches` in the output -- the front end
    # needs them to show an actual rain/snow description instead of a
    # meaningless temperature for those two records. `rain_rank`/`snow_rank`
    # are pure internal sort keys and aren't worth exposing.
    def strip(r):
        return {k: v for k, v in r.items() if k not in ("rain_rank", "snow_rank")}

    def pick(key, reverse=False):
        candidates = [r for r in rows if r.get(key) is not None]
        if not candidates:
            return None
        return strip(sorted(candidates, key=lambda r: r[key], reverse=reverse)[0])

    rain_candidates = [r for r in rows if r["rain_rank"] is not None]
    rainiest = None
    if rain_candidates:
        rainiest = strip(sorted(rain_candidates, key=lambda r: (r["rain_rank"], -(r["humidity"] or 0)))[0])

    snow_candidates = [r for r in rows if r["snow_rank"] is not None]
    snowiest = None
    if snow_candidates:
        snowiest = strip(sorted(snow_candidates, key=lambda r: (-(r["snow_inches"] or -1), r["snow_rank"]))[0])

    return {
        "eligible_games": len(rows),
        "records": {
            "coldest": pick("temp"),
            "hottest": pick("temp", reverse=True),
            "windiest": pick("wind", reverse=True),
            "muggiest": pick("humidity", reverse=True),
            "driest": pick("humidity"),
            "rainiest": rainiest,
            "snowiest": snowiest,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--out", type=str, default="data")
    args = ap.parse_args()

    season = args.season or current_season()
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching play-by-play for {season}...")
    pbp = load_pbp(season)
    if pbp is None or pbp.empty:
        print(f"No pbp data yet for {season}; writing offseason meta.json")
        meta = {
            "season": season,
            "has_data": False,
            "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source": "nflverse play-by-play (nflverse/nflverse-data)",
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return

    pbp = pbp[pbp["season_type"].isin(["REG", "POST"])].copy()

    print("Fetching schedules...")
    schedules = load_schedules()

    print("Fetching weekly rosters...")
    roster = load_roster(season)
    position_of = position_lookup(roster)

    print("Running oddity rules...")
    oddity_masks = build_oddity_masks(pbp, position_of)
    rare_penalty_types = compute_rare_penalty_types(pbp)

    print("Building games.json...")
    games = build_games(pbp, schedules, season)

    print("Building events.json...")
    events = build_events(pbp, oddity_masks, rare_penalty_types)

    print("Building totals.json...")
    totals = build_totals(pbp, rare_penalty_types)

    print("Building weather.json...")
    weather = build_weather(pbp, games)

    oddity_counts = {oid: int(mask.sum()) for oid, mask in oddity_masks.items()}

    rarity_path = out_dir / "rarity.json"
    rarity = json.loads(rarity_path.read_text()) if rarity_path.exists() else {}

    oddity_catalog = []
    for rule in ODDITIES:
        oddity_catalog.append(
            {
                "id": rule["id"],
                "tier": rule["tier"],
                "label": rule["label"],
                "why": rule["why"],
                "count": oddity_counts[rule["id"]],
                "rarity": rarity.get("oddities", {}).get(rule["id"]),
            }
        )

    rare_penalty_catalog = {
        pt: {
            "why": RARE_PENALTY_WHY.get(pt, "Called only a handful of times this season."),
            "rarity": rarity.get("penalties", {}).get(pt),
        }
        for pt in sorted(rare_penalty_types)
    }

    current_week = max((g["week"] for g in games if g["final"]), default=max((g["week"] for g in games), default=1))

    meta = {
        "season": season,
        "has_data": True,
        "current_week": current_week,
        "weeks": sorted({g["week"] for g in games}),
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": "nflverse play-by-play (nflverse/nflverse-data)",
        "games_count": len(games),
    }

    def dump(name, obj):
        (out_dir / name).write_text(json.dumps(obj, separators=(",", ":"), default=str))

    dump("games.json", games)
    dump("events.json", events)
    dump("totals.json", totals)
    dump("weather.json", weather)
    dump("oddities.json", {"oddities": oddity_catalog, "rare_penalties": rare_penalty_catalog})
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    print(f"Done. {len(games)} games, {len(events)} notable plays, {len(oddity_counts)} oddity rules checked.")
    nonzero = {k: v for k, v in oddity_counts.items() if v}
    print("Oddities found:", json.dumps(nonzero, indent=2))


if __name__ == "__main__":
    main()
