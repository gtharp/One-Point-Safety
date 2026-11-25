#!/usr/bin/env python3
"""
Computes real "how rare is this" numbers for the college football (FBS)
side from past seasons of CollegeFootballData.com (CFBD) play-by-play and
writes data/ncaa/rarity.json -- the counterpart of build_rarity.py.

build_ncaa_data.py reads data/ncaa/rarity.json on its next run and merges
the numbers into data/ncaa/oddities.json; the page also loads it directly
for the penalties panel's common/rare sort.

Call budget. CFBD's free tier is 1,000 calls a month, shared with the
daily college job, so this script is built to fill in over several runs
rather than all at once:

  * a past season costs ~17 calls: 1 /games (which weeks exist, and how
    many FBS games were played) plus 1 /plays per week (never per game).
  * progress is saved after every week to data/ncaa/cache/rarity_progress.json,
    so a run that stops partway (call cap, quota reserve, an API error)
    picks up where it left off next time without refetching anything.
  * seasons already in rarity.json are never refetched. After the first
    fill, each year only costs the newly completed season.
  * each run spends at most --max-calls (default 120), and stops early if
    the month's remaining calls would drop below --reserve (default 300),
    which is left for the daily job.

Counts reuse build_ncaa_data.py's play processing and oddity rules, so a
penalty or oddity is counted exactly the way the live season counts it.
Because FBS seasons differ in size (2020 was cut short), every number also
carries a per-game rate; avg_per_season is total / seasons.

Usage:
    CFBD_API_KEY=... python3 scripts/build_ncaa_rarity.py
        [--seasons 10] [--end-season 2025] [--out data/ncaa]
        [--max-calls 120] [--reserve 300]
"""

import argparse
import collections
import datetime
import json
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_ncaa_data import CFBD, current_season, process_week, read_json, team_matcher, week_key, write_json
from ncaa_oddity_rules import ODDITIES

CALLS_PER_SEASON_ESTIMATE = 17


def fbs_games(api, season):
    games = api.get("/games", {"year": season, "classification": "fbs"})
    return [g for g in games
            if (g.get("homeClassification") == "fbs" or g.get("awayClassification") == "fbs") and g.get("completed")]


def week_counts(api, season, stype, week, lookup, valid_ids):
    """Oddity and penalty counts for one week of one season."""
    params = {"year": season, "week": week, "classification": "fbs"}
    if stype == "postseason":
        params["seasonType"] = "postseason"
    plays = api.get("/plays", params)
    processed = process_week(plays, lookup, set())
    odd = collections.Counter()
    for e in processed["events"]:
        if e["game_id"] in valid_ids:
            odd.update(e["oddity_ids"])
    pen = collections.Counter()
    for gid, pc in processed["penalties"].items():
        if gid in valid_ids:
            pen.update(pc)
    pen.pop("Penalty (unspecified)", None)
    return {"oddities": dict(odd), "penalties": dict(pen),
            "games_with_plays": len(set(processed["games_with_plays"]) & valid_ids)}


def summarize(seasons_done):
    """{season: {"games", "oddities", "penalties"}} -> the rarity.json body."""
    used = sorted(seasons_done, key=int)
    n = len(used) or 1
    games_total = sum(seasons_done[s]["games"] for s in used) or 1

    def entry(by_season):
        total = sum(by_season.values())
        return {
            "avg_per_season": round(total / n, 1),
            "per_game": round(total / games_total, 5),
            "seasons": len(used),
            "total": total,
            "by_season": by_season,
        }

    odd_ids = [o["id"] for o in ODDITIES]
    oddities = {oid: entry({s: seasons_done[s]["oddities"].get(oid, 0) for s in used}) for oid in odd_ids}
    pen_types = sorted({p for s in used for p in seasons_done[s]["penalties"]})
    penalties = {
        pt: entry({s: seasons_done[s]["penalties"][pt] for s in used if pt in seasons_done[s]["penalties"]})
        for pt in pen_types
    }
    return {
        "seasons_used": [int(s) for s in used],
        "games_by_season": {s: seasons_done[s]["games"] for s in used},
        "oddities": oddities,
        "penalties": penalties,
    }


def unsummarize(rarity):
    """rarity.json -> {season: {"games", "oddities", "penalties"}}, the
    finished seasons, so they're never refetched. (After adding an oddity
    rule, delete rarity.json to recount it in past seasons; otherwise those
    seasons count it as 0.)"""
    out = {}
    for s, games in (rarity.get("games_by_season") or {}).items():
        out[s] = {
            "games": games,
            "oddities": {k: v["by_season"][s] for k, v in rarity.get("oddities", {}).items() if s in v["by_season"]},
            "penalties": {k: v["by_season"][s] for k, v in rarity.get("penalties", {}).items() if s in v["by_season"]},
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", type=int, default=10, help="how many completed seasons to look back")
    ap.add_argument("--end-season", type=int, default=None, help="most recent completed season (default: last one)")
    ap.add_argument("--out", type=str, default="data/ncaa")
    ap.add_argument("--max-calls", type=int, default=120, help="most API calls this run may spend")
    ap.add_argument("--reserve", type=int, default=300, help="stop before the month's remaining calls drop below this")
    args = ap.parse_args()

    end_season = args.end_season or (current_season() - 1)
    wanted = [str(s) for s in range(end_season - args.seasons + 1, end_season + 1)]
    out_dir = pathlib.Path(args.out)
    rarity_path = out_dir / "rarity.json"
    progress_path = out_dir / "cache" / "rarity_progress.json"

    existing = read_json(rarity_path, {})
    done = {s: v for s, v in unsummarize(existing).items() if s in wanted}
    progress = read_json(progress_path, {})  # {season: {"games", "weeks": [...], "week_counts": {key: counts}}}
    progress = {s: v for s, v in progress.items() if s in wanted and s not in done}
    todo = [s for s in reversed(wanted) if s not in done]  # newest first
    print(f"Window {wanted[0]}-{wanted[-1]}: {len(done)} seasons done, {len(todo)} to go {todo}")

    if not todo and sorted(map(int, done)) == existing.get("seasons_used"):
        print("Nothing to fetch; rarity.json is complete for this window.")
        return

    api = CFBD(args.max_calls) if todo else None
    if api:
        info = api.info()
        if info is not None:
            print(f"CFBD quota: {info.get('remainingCalls')} of {info.get('monthlyLimit')} calls left this month")
            if (info.get("remainingCalls") or 0) < args.reserve + CALLS_PER_SEASON_ESTIMATE:
                print(f"Not enough calls above the {args.reserve}-call reserve; skipping this run.")
                todo = []
        teams = read_json(out_dir / "cache" / "teams.json", {}).get("teams", [])
        lookup = team_matcher(teams)

    def can_spend():
        if api.calls >= api.max_calls:
            print(f"Reached this run's cap of {api.max_calls} calls.")
            return False
        if api.remaining is not None and api.remaining <= args.reserve:
            print(f"Down to the {args.reserve}-call reserve for the daily job.")
            return False
        return True

    for season in todo:
        if not can_spend():
            break
        sp = progress.setdefault(season, {})
        try:
            if "weeks" not in sp:
                print(f"Fetching {season} schedule...")
                games = fbs_games(api, int(season))
                weeks = sorted({(g.get("seasonType"), g["week"]) for g in games},
                               key=lambda w: (w[0] != "regular", w[1]))
                sp.update({
                    "games": len(games),
                    "game_ids": sorted(str(g["id"]) for g in games),
                    "weeks": [[st, wk] for st, wk in weeks],
                    "week_counts": {},
                })
                write_json(progress_path, progress, pretty=True)
            valid = set(sp["game_ids"])
            for stype, wk in sp["weeks"]:
                key = week_key(stype, wk)
                if key in sp["week_counts"]:
                    continue
                if not can_spend():
                    break
                print(f"Fetching {season} {stype} week {wk}...")
                sp["week_counts"][key] = week_counts(api, int(season), stype, wk, lookup, valid)
                write_json(progress_path, progress, pretty=True)
        except (RuntimeError, requests.HTTPError) as exc:
            print(f"warning: {season} stopped early ({exc}); will resume next run", file=sys.stderr)
            break
        if "weeks" in sp and len(sp["week_counts"]) == len(sp["weeks"]):
            odd, pen, with_plays = collections.Counter(), collections.Counter(), 0
            for wc in sp["week_counts"].values():
                odd.update(wc["oddities"])
                pen.update(wc["penalties"])
                with_plays += wc["games_with_plays"]
            # Rates are per game that actually had plays, so a few games
            # missing play-by-play don't drag every rate down.
            done[season] = {"games": with_plays or sp["games"], "oddities": dict(odd), "penalties": dict(pen)}
            progress.pop(season, None)
            write_json(progress_path, progress, pretty=True)
            print(f"  {season} done: {with_plays} games with plays, {sum(pen.values())} penalties")
        else:
            break

    if api:
        print(f"API calls this run: {api.calls} (X-CallLimit-Remaining now {api.remaining})")
    if not done:
        print("No complete seasons yet; rarity.json not written.")
        return
    out = {"computed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    out.update(summarize(done))
    out["complete"] = len(done) == len(wanted)
    write_json(rarity_path, out)
    print(f"Wrote {rarity_path}: {len(done)}/{len(wanted)} seasons {out['seasons_used']}"
          + ("" if out["complete"] else " (more to fetch on later runs)"))


if __name__ == "__main__":
    main()
