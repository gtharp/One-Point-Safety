#!/usr/bin/env python3
"""
Computes real "how rare is this" numbers from historical nflverse
play-by-play and writes data/rarity.json. This is separate from
build_data.py's daily run -- it downloads a decade of pbp data, which is
slow and doesn't need to happen often. Run it by hand occasionally, or via
the (manual/yearly) update-rarity.yml workflow.

build_data.py reads data/rarity.json automatically on its next run and
merges the numbers into data/oddities.json -- no other wiring needed.

Usage:
    python3 scripts/build_rarity.py [--seasons 10] [--end-season 2025] [--out data]
"""

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_data import current_season, load_pbp, load_roster, position_lookup, build_oddity_masks
from oddity_rules import ODDITIES


def season_counts(season):
    """Returns (oddity_id -> count, penalty_type -> count) for one season, or None if unavailable."""
    pbp = load_pbp(season)
    if pbp is None or pbp.empty:
        return None
    pbp = pbp[pbp["season_type"].isin(["REG", "POST"])]

    roster = load_roster(season)
    position_of = position_lookup(roster)
    masks = build_oddity_masks(pbp, position_of)
    oddity_counts = {oid: int(mask.sum()) for oid, mask in masks.items()}

    pen_counts = pbp[pbp["penalty"] == 1]["penalty_type"].value_counts().to_dict()
    pen_counts = {k: int(v) for k, v in pen_counts.items()}

    return oddity_counts, pen_counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, default=10, help="how many completed seasons to look back")
    ap.add_argument("--end-season", type=int, default=None, help="most recent completed season (default: last year)")
    ap.add_argument("--out", type=str, default="data")
    args = ap.parse_args()

    end_season = args.end_season or (current_season() - 1)
    seasons = list(range(end_season - args.seasons + 1, end_season + 1))

    oddity_totals = {o["id"]: 0 for o in ODDITIES}
    oddity_by_season = {o["id"]: {} for o in ODDITIES}
    penalty_totals = {}
    penalty_by_season = {}
    seasons_used = []

    for season in seasons:
        print(f"Fetching {season}...")
        result = season_counts(season)
        if result is None:
            print(f"  no data for {season}, skipping")
            continue
        odd_counts, pen_counts = result
        seasons_used.append(season)
        for oid, n in odd_counts.items():
            oddity_totals[oid] += n
            oddity_by_season[oid][str(season)] = n
        for pt, n in pen_counts.items():
            penalty_totals[pt] = penalty_totals.get(pt, 0) + n
            penalty_by_season.setdefault(pt, {})[str(season)] = n

    n_seasons = len(seasons_used) or 1

    oddity_rarity = {
        oid: {
            "avg_per_season": round(total / n_seasons, 1),
            "seasons": len(seasons_used),
            "total": total,
            "by_season": oddity_by_season[oid],
        }
        for oid, total in oddity_totals.items()
    }
    penalty_rarity = {
        pt: {
            "avg_per_season": round(total / n_seasons, 1),
            "seasons": len(seasons_used),
            "total": total,
            "by_season": penalty_by_season[pt],
        }
        for pt, total in penalty_totals.items()
    }

    out = {
        "computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seasons_used": seasons_used,
        "oddities": oddity_rarity,
        "penalties": penalty_rarity,
    }

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rarity.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"Done. Used {len(seasons_used)}/{len(seasons)} seasons: {seasons_used}")
    print("Wrote", out_dir / "rarity.json")


if __name__ == "__main__":
    main()
