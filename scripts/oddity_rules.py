"""
One config file for every oddity the site tracks.

Each entry in ODDITIES is a plain filter over nflverse play-by-play columns.
To add a new oddity: add an entry with a unique id, a tier, display copy,
and a `detect(df, ctx)` function that returns a boolean Series aligned to
`df.index` (True = this play is that oddity). `ctx` is a dict of extra
lookups build_data.py hands every rule — currently just `position_of`, a
dict mapping a player's gsis_id to their roster position for that season.

Tiers: "wild" (rarest), "rare", "uncommon".

Column names were verified against the real 2026-season release at
github.com/nflverse/nflverse-data (releases/download/pbp/play_by_play_2026.parquet)
and cross-checked against the 2013 release to confirm the schema is stable
across seasons. See README.md for notes on which rules are heuristic
(text-pattern based) rather than clean field filters.
"""

import re


def _desc(df):
    return df["desc"].fillna("")


def _lateral_count(df):
    # pbp's lateral_* flags are binary per play (max 1), so they can't tell
    # a single lateral from a multi-lateral chain. Count "Lateral to"
    # occurrences in the description text instead.
    return _desc(df).str.count(r"[Ll]ateral to")


def detect_one_pt_safety(df, ctx):
    # A PAT attempt that ends with the ball dead in the attempting team's
    # own end zone. No confirmed example exists in NFL history (as far as
    # we know) -- this rule is defined correctly but untested against a
    # real play.
    return (df["extra_point_attempt"] == 1) & (df["safety"] == 1)


def detect_kick_six(df, ctx):
    # A missed (not blocked) field goal returned the other way for a TD.
    return (
        (df["field_goal_result"] == "missed")
        & (df["touchdown"] == 1)
        & (df["return_touchdown"] == 1)
    )


def detect_td_99(df, ctx):
    return (df["touchdown"] == 1) & (df["yards_gained"] >= 99)


def detect_def_2pt(df, ctx):
    return df["defensive_two_point_conv"] == 1


def detect_lateral_td(df, ctx):
    # 2+ laterals on the same play, ending in a touchdown.
    return (df["touchdown"] == 1) & (_lateral_count(df) >= 2)


def detect_qb_reception(df, ctx):
    # nflverse occasionally encodes a batted/deflected pass the passer ends
    # up with himself as passer_player_id == receiver_player_id on the same
    # row -- exclude those, they're not a receiver trick play.
    pos = ctx["position_of"]
    receiver_pos = df["receiver_player_id"].map(pos)
    different_player = df["receiver_player_id"] != df["passer_player_id"]
    return (df["complete_pass"] == 1) & (receiver_pos == "QB") & different_player


def detect_walkoff(df, ctx):
    # Game-winning score with no time left. Restricted to TD/made-FG --
    # a walk-off safety would require flipping the score_differential sign
    # convention (points go to the defense) and is rare enough to not be
    # worth the edge case; it still gets caught by the "Safety" oddity.
    scored = (df["touchdown"] == 1) | (df["field_goal_result"] == "made")
    time_expired = (df["quarter_seconds_remaining"] == 0) & (df["qtr"] >= 4)
    took_lead = (df["score_differential"] <= 0) & (df["score_differential_post"] > 0)
    return scored & time_expired & took_lead


def detect_blocked_kick_td(df, ctx):
    # Blocked punt OR blocked FG, scored on the same play.
    blocked = (df["punt_blocked"] == 1) | (df["field_goal_result"] == "blocked")
    return blocked & (df["touchdown"] == 1)


def detect_fake_special_teams_td(df, ctx):
    # Heuristic: no clean flag for this. A punt/FG-formation snap that
    # turned into a pass or run (not an actual punt/FG attempt) and scored.
    formation = _desc(df).str.contains(r"Punt formation|Field Goal formation", regex=True)
    not_real_kick = (df["punt_attempt"] != 1) & (df["field_goal_attempt"] != 1)
    ran_or_threw = (df["rush_attempt"] == 1) | (df["pass_attempt"] == 1)
    return formation & not_real_kick & ran_or_threw & (df["touchdown"] == 1)


def detect_fg_60(df, ctx):
    return (df["field_goal_result"] == "made") & (df["kick_distance"] >= 60)


def detect_onside_recovered(df, ctx):
    return df["own_kickoff_recovery"] == 1


def detect_off_fumble_rec_td(df, ctx):
    # Offense fumbles and recovers its own ball in the end zone for a TD.
    return (
        (df["fumble"] == 1)
        & (df["touchdown"] == 1)
        & (df["fumble_recovery_1_team"] == df["posteam"])
    )


def detect_non_qb_td_pass(df, ctx):
    pos = ctx["position_of"]
    passer_pos = df["passer_player_id"].map(pos)
    return (df["pass_touchdown"] == 1) & passer_pos.notna() & (passer_pos != "QB")


def detect_safety(df, ctx):
    return df["safety"] == 1


def detect_pick_six(df, ctx):
    return (df["interception"] == 1) & (df["touchdown"] == 1)


def detect_ot_score(df, ctx):
    return (df["qtr"] == 5) & (
        (df["touchdown"] == 1) | (df["field_goal_result"] == "made") | (df["safety"] == 1)
    )


ODDITIES = [
    # ---- Wild ----
    {
        "id": "one_pt_safety",
        "tier": "wild",
        "label": "One-point safety",
        "why": "A blocked or botched PAT ends up dead in the kicking team's own end zone. "
        "It's the rarest scoring play in the sport -- as far as we know it has never "
        "actually happened in an NFL game, though the rule allows it.",
        "detect": detect_one_pt_safety,
    },
    {
        "id": "kick_six",
        "tier": "wild",
        "label": "Kick-six",
        "why": "A missed field goal returned the length of the field for a touchdown. "
        "The kick has to land short in the field of play, and the returner has to go 100+ yards.",
        "detect": detect_kick_six,
    },
    {
        "id": "td_99",
        "tier": "wild",
        "label": "99-yard touchdown",
        "why": "The play starts at the offense's own 1-yard line and goes the entire field. "
        "It's the longest scrimmage play possible.",
        "detect": detect_td_99,
    },
    {
        "id": "def_2pt",
        "tier": "wild",
        "label": "Defensive 2-point return",
        "why": "The defense takes a failed two-point try back to the other end zone and scores 2 points of its own.",
        "detect": detect_def_2pt,
    },
    {
        "id": "lateral_td",
        "tier": "wild",
        "label": "Multi-lateral touchdown",
        "why": "Several backward passes on one play that end in a touchdown. Usually a last-gasp "
        "desperation play that almost never works.",
        "detect": detect_lateral_td,
    },
    {
        "id": "walkoff",
        "tier": "wild",
        "label": "Walk-off score",
        "why": "The game-winning score comes with no time left on the clock.",
        "detect": detect_walkoff,
    },
    {
        "id": "fake_special_teams_td",
        "tier": "wild",
        "label": "Fake punt/FG touchdown",
        "why": "A fake punt or field goal that goes all the way for a touchdown, often with a punter, "
        "holder or kicker making the play.",
        "detect": detect_fake_special_teams_td,
    },
    # ---- Rare ----
    {
        "id": "blocked_kick_td",
        "tier": "rare",
        "label": "Blocked punt/FG touchdown",
        "why": "The defense blocks a punt or field goal and scores on the same play.",
        "detect": detect_blocked_kick_td,
    },
    {
        "id": "fg_60",
        "tier": "rare",
        "label": "60+ yard field goal",
        "why": "Field goals from 60+ yards are made only a handful of times each season, though they're getting more common.",
        "detect": detect_fg_60,
    },
    {
        "id": "onside_recovered",
        "tier": "rare",
        "label": "Onside kick recovered",
        "why": "The kicking team recovers its own short kickoff. Success rates are low, especially when the other team is expecting it.",
        "detect": detect_onside_recovered,
    },
    {
        "id": "off_fumble_rec_td",
        "tier": "rare",
        "label": "Offensive fumble recovery TD",
        "why": "The offense fumbles and recovers its own ball in the end zone for a touchdown.",
        "detect": detect_off_fumble_rec_td,
    },
    {
        "id": "non_qb_td_pass",
        "tier": "rare",
        "label": "Non-QB touchdown pass",
        "why": "A trick-play pass thrown by someone other than the quarterback that goes for a touchdown.",
        "detect": detect_non_qb_td_pass,
    },
    # ---- Uncommon ----
    {
        "id": "qb_reception",
        "tier": "uncommon",
        "label": "QB catches a pass",
        "why": "A quarterback lines up as a receiver (often a trick play) and actually catches the ball.",
        "detect": detect_qb_reception,
    },
    {
        "id": "safety",
        "tier": "uncommon",
        "label": "Safety",
        "why": "The offense is downed or commits a foul in its own end zone. The defense gets 2 points and the ball back.",
        "detect": detect_safety,
    },
    {
        "id": "pick_six",
        "tier": "uncommon",
        "label": "Pick-six",
        "why": "An interception returned for a touchdown.",
        "detect": detect_pick_six,
    },
    {
        "id": "ot_score",
        "tier": "uncommon",
        "label": "Overtime score",
        "why": "A touchdown, field goal, or safety scored in overtime.",
        "detect": detect_ot_score,
    },
]

# Penalties are classified "rare" dynamically rather than off a fixed list,
# so a type that's barely ever called this season gets flagged even if it's
# not one we thought to name ahead of time. build_data.py computes each
# penalty_type's count for the current scope and flags anything at or below
# this threshold.
RARE_PENALTY_MAX_SEASON_COUNT = 5

# Flavor text for the rare penalties we know well. Anything rare but not
# listed here gets a generic "called only a handful of times" line.
RARE_PENALTY_WHY = {
    "Leverage": "Using a teammate's body to gain height while trying to block a kick. It almost never gets called.",
    "Illegal Bat": "Intentionally batting a loose ball forward, or batting any loose ball out of the end zone.",
    "Kick Catch Interference": "Getting in the returner's way before they have a chance to catch a punt or kickoff.",
    "Running Into the Kicker": "Incidental contact with the kicker that falls short of roughing. Rarely flagged.",
    "Ineligible Downfield Pass": "An offensive lineman is too far downfield when a forward pass is thrown.",
    "Invalid Fair Catch Signal": "A returner signals for a fair catch without clearly and unmistakably raising an arm.",
    "Illegal Forward Handoff": "A backward pass (lateral) that actually went forward, ruled an illegal handoff.",
    "Illegal Touch Pass": "An ineligible receiver, or a receiver out of bounds, touches a forward pass first.",
    "Illegal Touch Kick": "A kicking-team player illegally touches a punt or kick before the receiving team does.",
    "Disqualification": "A player is ejected from the game outright, rather than just penalized.",
    "Horse Collar Tackle": "A tackler grabs the inside back of the collar or shoulder pads and pulls the runner down.",
    "Chop Block": "Two offensive players block the same defender at different levels (one high, one low) simultaneously.",
}
