#!/usr/bin/env python3
"""
Computes real coldest/hottest/muggiest/driest/windiest/rainiest/snowiest
NFL games from nflverse's own per-game weather data, across every season
it's available for (2002 onward -- earlier seasons don't carry a weather
field at all). Writes data/weather_records.json.

This is separate from build_data.py's daily run for the same reason
build_rarity.py is: it downloads two dozen seasons of pbp, which is slow
and doesn't need to happen often. Run it by hand occasionally, or via
the (manual/yearly) update-weather-records.yml workflow.

Note: this only covers 2002+, since that's as far back as nflverse's
weather field goes. The sport's actual all-time weather extremes (the Ice
Bowl, the Freezer Bowl, etc.) predate this data entirely and are far more
extreme than anything found here -- they're hand-researched entries in
data/history.json instead. This file's records are explicitly "since
2002," not "ever," and the front end should say so.

Usage:
    python3 scripts/build_weather_records.py [--start-season 2002] [--end-season 2025] [--out data]
"""

import argparse
import datetime
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_data import current_season, load_pbp, load_schedules, build_games, parse_weather_text


def season_weather_rows(season, schedules):
    pbp = load_pbp(season)
    if pbp is None or pbp.empty:
        return []
    pbp = pbp[pbp["season_type"].isin(["REG", "POST"])]
    games = build_games(pbp, schedules, season)
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
                "season": season,
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
    return rows


# Keep `condition` and `snow_inches` -- the front end needs them to show
# an actual rain/snow description instead of a meaningless temperature for
# those two records. `rain_rank`/`snow_rank` are pure internal sort keys.
def strip(r):
    return {k: v for k, v in r.items() if k not in ("rain_rank", "snow_rank")}


def pick(rows, key, reverse=False):
    candidates = [r for r in rows if r.get(key) is not None]
    if not candidates:
        return None
    return strip(sorted(candidates, key=lambda r: r[key], reverse=reverse)[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-season", type=int, default=2002, help="first season with nflverse weather data")
    ap.add_argument("--end-season", type=int, default=None, help="default: last completed season")
    ap.add_argument("--out", type=str, default="data")
    args = ap.parse_args()

    end_season = args.end_season or (current_season() - 1)
    seasons = list(range(args.start_season, end_season + 1))

    print("Fetching schedules...")
    schedules = load_schedules()

    all_rows = []
    seasons_used = []
    for season in seasons:
        print(f"Fetching {season}...")
        rows = season_weather_rows(season, schedules)
        if not rows:
            print(f"  no usable data for {season}, skipping")
            continue
        seasons_used.append(season)
        all_rows.extend(rows)

    rain_candidates = [r for r in all_rows if r["rain_rank"] is not None]
    rainiest = None
    if rain_candidates:
        rainiest = strip(sorted(rain_candidates, key=lambda r: (r["rain_rank"], -(r["humidity"] or 0)))[0])

    snow_candidates = [r for r in all_rows if r["snow_rank"] is not None]
    snowiest = None
    if snow_candidates:
        snowiest = strip(sorted(snow_candidates, key=lambda r: (-(r["snow_inches"] or -1), r["snow_rank"]))[0])

    records = {
        "coldest": pick(all_rows, "temp"),
        "hottest": pick(all_rows, "temp", reverse=True),
        "windiest": pick(all_rows, "wind", reverse=True),
        "muggiest": pick(all_rows, "humidity", reverse=True),
        "driest": pick(all_rows, "humidity"),
        "rainiest": rainiest,
        "snowiest": snowiest,
    }

    # Sustained wind over ~50mph during a played NFL game is implausible
    # (that's low-end hurricane force) -- nflverse's source weather text
    # does contain occasional transcription glitches (confirmed by hand
    # against the raw field for at least one case: a 71mph reading for a
    # 2016 Steelers-Giants game that's otherwise well documented as merely
    # "very windy," not literally hurricane-force). Flag rather than quietly
    # trust or drop it.
    if records["windiest"] and records["windiest"]["wind"] > 50:
        records["windiest"]["caveat"] = (
            "This wind reading is unusually high for sustained wind during a played NFL "
            "game and may reflect a transcription error in the upstream source data rather "
            "than reality -- shown as recorded, not independently verified."
        )

    out = {
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seasons_used": seasons_used,
        "eligible_games": len(all_rows),
        "note": "Covers every outdoor game since nflverse's weather data starts (2002) -- not the sport's all-time records, which predate this data and live in data/history.json instead.",
        "records": records,
    }

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "weather_records.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"Done. Used {len(seasons_used)}/{len(seasons)} seasons, {len(all_rows)} eligible games.")
    print(json.dumps(out["records"], indent=2))


if __name__ == "__main__":
    main()
