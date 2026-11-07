"""
Oddity rules for the college football (NCAA FBS) side of the site -- the
counterpart of oddity_rules.py, rewritten for CollegeFootballData.com
(CFBD) play-by-play.

CFBD plays don't carry nflverse's boolean columns (touchdown, safety,
extra_point_attempt, ...). What they do have is a `playType` string
("Rushing Touchdown", "Field Goal Good", "Safety", ...), the free-text
`playText`, `yardsGained`, `period`/`clock`, and the offense/defense score
*after* the play. build_ncaa_data.py turns those into a dataframe with a
few derived columns every rule below can use:

    ptype        playType ("" if missing)
    text         playText ("" if missing)
    yards        yardsGained (int)
    period       1-4, 5+ = overtime periods (each OT is its own period)
    secs         game clock at the snap, in seconds
    end_secs     clock after the play, parsed from "clock MM:SS" in the
                 text (falls back to secs; None for summary-style rows)
    off_final / def_final   each side's final score in the game
    off_delta    points the offense gained on this play (from score deltas)
    def_delta    points the defense gained on this play
    off_pre / def_pre / off_post / def_post   scores before/after the play
    is_td        a touchdown was scored on the play (either team)
    try_text     the part of playText after "TOUCHDOWN" (the PAT/2-pt try)
    passer / receiver / rusher   names parsed from playText ("" if none)
    order        play order within the game
    game_id, offense, defense

`ctx` holds per-week lookups built from the same plays:
    specialists  {(game_id, team): set of punter/kicker names}
    pass_atts    {(game_id, team, name): pass attempts by that player}
    next_offense Series aligned to df.index: offense on the next real play

Tiers: "wild" (rarest), "rare", "uncommon". Tiers start from the NFL
side's values; there's no college rarity script yet (see README).
Rules marked HEURISTIC in their comment lean on play-text patterns rather
than a clean field and are listed in README.md.
"""

import re

import pandas as pd


def _t(df):
    return df["text"]


def _ci(df, pattern):
    return _t(df).str.contains(pattern, case=False, regex=True)


SCRIMMAGE_TD = {"Rushing Touchdown", "Passing Touchdown"}


def detect_one_pt_safety(df, ctx):
    # HEURISTIC. The try after a touchdown ends in a safety: worth 1 point
    # to whichever side didn't get trapped in its own end zone. CFBD folds
    # the try into the TD play's text, so look for "safety" in the try
    # segment, backed up by a 1-point score delta (offense +7 on the play =
    # 6 + 1, or the defense +1).
    safety_in_try = df["try_text"].str.contains("safety", case=False)
    one_point = (df["off_delta"] == 7) | (df["def_delta"] == 1)
    standalone = df["ptype"].str.contains("Safety", case=False) & (
        (df["off_delta"] == 1) | (df["def_delta"] == 1)
    )
    return (safety_in_try & one_point) | standalone


def detect_kick_six(df, ctx):
    # A missed (not blocked) field goal returned for a touchdown.
    missed = df["ptype"].str.contains("Missed Field Goal Return", case=False) | (
        df["ptype"] == "Field Goal Missed"
    )
    blocked = _ci(df, r"block")
    return missed & ~blocked & df["is_td"]


def detect_td_99(df, ctx):
    # Scrimmage plays only, matching the NFL rule (nflverse's yards_gained
    # is 0 on returns, so the NFL version is effectively scrimmage-only too).
    return df["ptype"].isin(SCRIMMAGE_TD) & (df["yards"] >= 99)


def detect_def_2pt(df, ctx):
    # HEURISTIC when it isn't tagged. CFBD sometimes has a dedicated
    # "Defensive 2pt Conversion" playType; otherwise the try is folded into
    # the TD play, so look for the defense gaining exactly 2 on a play where
    # the offense scored a TD, with a return/turnover word in the try text.
    tagged = df["ptype"].str.contains(r"Defensive 2pt", case=False)
    returned = df["try_text"].str.contains(
        r"return|intercept|fumble|defensive|two-point|2pt|2-pt", case=False
    )
    folded = (df["def_delta"] == 2) & (df["off_delta"] >= 6) & returned & ~df[
        "try_text"
    ].str.contains("safety", case=False)
    return tagged | folded


def detect_lateral_td(df, ctx):
    # HEURISTIC: 2+ "lateral" mentions on a touchdown play.
    return df["is_td"] & (_t(df).str.count(r"(?i)\blateral") >= 2)


def detect_walkoff(df, ctx):
    # Game-winning score in regulation with no time left. College overtime
    # has no game clock, so OT winners are covered by "Overtime score"
    # instead. Uses whichever side's score went up, so a walk-off pick-six
    # or fumble return counts too.
    reg = df["period"] == 4
    no_time = df["end_secs"] == 0
    # ...and nobody scored after it (guards against rows with a bad clock).
    last = (df["off_post"] == df["off_final"]) & (df["def_post"] == df["def_final"])
    off_took_lead = (df["off_delta"] > 0) & (df["off_pre"] <= df["def_pre"]) & (
        df["off_post"] > df["def_post"]
    )
    def_took_lead = (df["def_delta"] > 0) & (df["def_pre"] <= df["off_pre"]) & (
        df["def_post"] > df["off_post"]
    )
    return reg & no_time & last & (off_took_lead | def_took_lead)


def detect_blocked_kick_td(df, ctx):
    # A blocked punt or field goal, with a touchdown on the same play.
    kick = df["ptype"].str.contains(r"Punt|Field Goal", case=False)
    blocked = df["ptype"].str.contains("Blocked", case=False) | _ci(df, r"\bblocked by\b")
    return kick & blocked & df["is_td"]


def detect_fake_special_teams_td(df, ctx):
    # HEURISTIC. CFBD text doesn't note the formation, so: a rushing or
    # passing TD where the ball-carrier/passer is someone who punted or
    # kicked for that team in the same game -- or the text says "fake"
    # outright. Holders are deliberately left out: they're often backup or
    # even starting QBs, so "holder throws a TD" is usually just a QB.
    spec = ctx["specialists"]

    def by_specialist(row):
        who = row["passer"] or row["rusher"]
        return bool(who) and who in spec.get((row["game_id"], row["offense"]), ())

    scrim_td = df["ptype"].isin(SCRIMMAGE_TD)
    if not scrim_td.any():
        return pd.Series(False, index=df.index)
    flagged = df[scrim_td].apply(by_specialist, axis=1).reindex(df.index, fill_value=False)
    return scrim_td & (flagged | _ci(df, r"\bfake\b"))


def detect_fg_60(df, ctx):
    return (df["ptype"] == "Field Goal Good") & (df["yards"] >= 60)


def detect_onside_recovered(df, ctx):
    # HEURISTIC. An onside kick where the kicking team (CFBD's "offense" on
    # a kickoff) still has the ball on the next real play. Kicks wiped out
    # by a penalty ("NO PLAY") are skipped.
    onside = df["ptype"].str.contains("Kickoff", case=False) & _ci(df, r"onside")
    no_play = _ci(df, r"NO PLAY")
    kept = ctx["next_offense"] == df["offense"]
    return onside & ~no_play & kept


def detect_off_fumble_rec_td(df, ctx):
    # The offense fumbles and recovers its own ball for a touchdown.
    return (df["ptype"] == "Fumble Recovery (Own)") & (df["off_delta"] >= 6)


def detect_non_qb_td_pass(df, ctx):
    # HEURISTIC. No roster positions in CFBD plays without extra API calls,
    # so: a passing TD thrown by a player with at most 2 pass attempts in
    # that game who also caught/ran the ball or punted/kicked/held that game.
    atts = ctx["pass_atts"]
    touches = ctx["non_passing_touch"]
    spec = ctx["specialists"]

    def trick(row):
        p = row["passer"]
        if not p:
            return False
        key = (row["game_id"], row["offense"])
        return atts.get(key + (p,), 0) <= 2 and (p in touches.get(key, ()) or p in spec.get(key, ()))

    ptd = df["ptype"] == "Passing Touchdown"
    if not ptd.any():
        return pd.Series(False, index=df.index)
    return ptd & df[ptd].apply(trick, axis=1).reindex(df.index, fill_value=False)


def detect_qb_reception(df, ctx):
    # HEURISTIC. A completed pass caught by someone who threw 5+ passes for
    # the same team in that game (i.e. that game's quarterback), and who
    # isn't the passer on this play.
    atts = ctx["pass_atts"]

    def qb_catch(row):
        r = row["receiver"]
        return bool(r) and r != row["passer"] and atts.get((row["game_id"], row["offense"], r), 0) >= 5

    comp = df["ptype"].isin({"Pass Reception", "Passing Touchdown"}) & (df["receiver"] != "")
    if not comp.any():
        return pd.Series(False, index=df.index)
    return comp & df[comp].apply(qb_catch, axis=1).reindex(df.index, fill_value=False)


def detect_safety(df, ctx):
    two = (df["def_delta"] == 2) & _ci(df, r"\bsafety\b")
    return (df["ptype"] == "Safety") | two


def detect_pick_six(df, ctx):
    return df["ptype"].str.contains("Interception Return Touchdown", case=False) | (
        df["ptype"].str.contains("Interception", case=False) & df["is_td"] & (df["def_delta"] >= 6)
    )


def detect_ot_score(df, ctx):
    # Any points in overtime -- including the 2-point tries of the shootout
    # periods (3rd OT on).
    return (df["period"] >= 5) & ((df["off_delta"] > 0) | (df["def_delta"] > 0))


def detect_targeting(df, ctx):
    # HEURISTIC (text). A Targeting foul that stood: not declined, not
    # offset, and not overturned on review. In college it carries an
    # automatic ejection.
    flagged = _ci(df, r"\bTargeting\b")
    voided = _ci(df, r"Targeting\W{0,3}\s*declined|Targeting.{0,40}OVERTURNED|OVERTURNED.{0,80}Targeting|offsetting")
    return flagged & ~voided


def detect_ot_shootout(df, ctx):
    # From the 3rd overtime on (period 7+), college overtime is a 2-point
    # shootout. Flag the first play of period 7 in each game, so each
    # shootout game counts once.
    snap = ~df["ptype"].isin({"Timeout", "End Period", "End of Half", "End of Game", "End of Regulation"})
    in_shootout = (df["period"] >= 7) & snap
    if not in_shootout.any():
        return pd.Series(False, index=df.index)
    first = df[in_shootout].groupby("game_id")["order"].transform("min")
    return (in_shootout & (df["order"] == first.reindex(df.index))).fillna(False)


ODDITIES = [
    # ---- Wild ----
    {
        "id": "one_pt_safety",
        "tier": "wild",
        "label": "One-point safety",
        "why": "The try after a touchdown ends with the ball dead in somebody's own end zone, worth 1 point. "
        "Unlike the NFL, it has actually happened in major college football, just a handful of times "
        "across many decades.",
        "detect": detect_one_pt_safety,
    },
    {
        "id": "kick_six",
        "tier": "wild",
        "label": "Kick-six",
        "why": "A missed field goal returned the length of the field for a touchdown. "
        "The kick has to fall short in the field of play, and the returner has to go 100+ yards.",
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
        "why": "The defense takes a failed PAT or two-point try back to the other end zone for 2 points of its own. "
        "College adopted the rule in 1988; the NFL didn't add it until 2015.",
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
        "why": "The go-ahead score comes with no time left in regulation. College overtime has no clock, "
        "so OT winners show up as overtime scores instead.",
        "detect": detect_walkoff,
    },
    {
        "id": "fake_special_teams_td",
        "tier": "wild",
        "label": "Fake punt/FG touchdown",
        "why": "A fake punt or field goal that goes all the way for a touchdown, usually with the punter, "
        "holder or kicker making the play.",
        "detect": detect_fake_special_teams_td,
    },
    # ---- Rare ----
    {
        "id": "ot_shootout",
        "tier": "rare",
        "label": "Overtime 2-point shootout",
        "why": "Since 2021, college games still tied after two overtimes go to alternating two-point tries, "
        "with no more drives. It takes a third overtime to get there.",
        "detect": detect_ot_shootout,
    },
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
        "why": "Even rarer in college than the NFL. Most college kickers top out in the low 50s, "
        "so a 60-yarder may happen only a few times across all of FBS in a season.",
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
        "why": "The offense fumbles and recovers its own ball for a touchdown.",
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
        "why": "A quarterback lines up as a receiver (often on a trick play) and actually catches the ball.",
        "detect": detect_qb_reception,
    },
    {
        "id": "targeting",
        "tier": "uncommon",
        "label": "Targeting ejection",
        "why": "Forcible contact to the head or neck of a defenseless player. In college it's a 15-yard foul plus "
        "an automatic ejection, unless replay overturns it. A foul in the second half also costs the player the first half of the next game.",
        "detect": detect_targeting,
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
        "why": "Points scored in overtime. Each college OT period gives both teams a possession from the 25, "
        "so OT games rack these up fast.",
        "detect": detect_ot_score,
    },
]

# Rare penalties are picked dynamically, as on the NFL side, but FBS plays
# roughly 7x as many games, so a fixed "5 a season" cutoff would flag
# almost nothing. Instead a penalty type is rare when it's been called at
# most this many times per 100 FBS games played so far (with a floor, so
# the first weeks of the season don't flag everything).
RARE_PENALTY_PER_100_GAMES = 1.0
RARE_PENALTY_MIN_COUNT = 3

# Flavor text for rare penalties we know well. CFBD's penalty names are
# shorter/more generic than nflverse's (e.g. "Personal Foul" covers a lot).
RARE_PENALTY_WHY = {
    "Leaping": "A defender runs and jumps over the line to block a kick. Outlawed in college since 2017.",
    "Leverage": "Using a teammate's body to gain height while trying to block a kick. It almost never gets called.",
    "Illegal Wedge": "Two or more kick-return players forming a shoulder-to-shoulder wedge. Banned on college kickoffs since 2018.",
    "Illegal Bat": "Intentionally batting a loose ball forward, or batting a pass in flight on offense.",
    "Illegal Contact With Snapper": "Contacting the long snapper before he can protect himself on a punt or kick, a protection college added for snappers.",
    "Roughing Holder": "Running into or roughing the holder on a placekick. Holders get the same protection as kickers.",
    "Disconcerting Signals": "A defender calls out sounds or signals meant to make the offense think the snap is coming.",
    "Helping the Runner": "A teammate pushes or pulls the ball carrier forward. Long illegal, almost never flagged.",
    "Return From Out of Bounds": "A kicking-team player who went out of bounds comes back in and is first to touch the kick.",
    "Blocking Out of Bounds": "A player who has gone out of bounds blocks an opponent anyway.",
    "Illegal Touch of Kick": "A kicking-team player illegally touches a punt or kick before the receiving team does.",
    "12 Men on the Field": "Twelve players on the field at the snap, written up under this name rather than the usual illegal substitution.",
    "Equipment Violation": "A player is on the field missing required equipment, or wearing something illegal.",
    "Sideline Interference": "Coaches or players crowding the sideline restricted area, getting in the officials' way.",
    "Kick Catch Interference": "Getting in the returner's way before they have a chance to catch a punt or kickoff.",
    "Fair Catch Interference": "Getting in the returner's way after they signal for a fair catch.",
    "Running Into the Kicker": "Incidental contact with the kicker that falls short of roughing. Rarely flagged.",
    "Illegal Forward Pass": "A second forward pass, or a pass thrown from beyond the line of scrimmage.",
    "Illegal Touch-Pass": "An ineligible player is the first to touch a forward pass.",
    "Illegal Participation": "Too many players, or a player who left the field coming back into the play.",
    "Disqualification": "A player is ejected from the game outright, rather than just penalized.",
    "Horse Collar": "A tackler grabs the inside back of the collar or shoulder pads and pulls the runner down.",
    "Chop Block": "A high-low double-team block on the same defender. Rarely called in the modern game.",
}
