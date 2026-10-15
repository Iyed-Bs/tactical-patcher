"""Tactics board: the football layer of the squad lab.

A game plan the way PES writes one (attacking and defensive style, build-up,
attacking area, containment, pressuring, defensive line, compactness,
support range, advanced instructions) or a coach's style that sets all of it.
From the plan and the eleven:

- shapes: where each role goes in possession and out of it, the in-possession
  and defensive shapes that come out of it (a 4-3-3 with false full-backs
  attacks as a 3-2-5), movement arrows, the pressing zone and the line;
- demands against capability: what the plan asks of the players (cover in
  behind for a high line, an engine for gegenpressing, press resistance for
  a short build-up) against what this eleven's attributes can give;
- load: how hard the plan runs the starters, which sets how many minutes
  they can carry and so who else plays.

This layer is football knowledge written down as rules, not learned from
data; the growth it feeds into is learned (patcher.growth2).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .paths import DATA

# Slot types, base positions (x across, y up the pitch: 0 own goal line, 100 the opponent's).
FORMATIONS = {
    "4-3-3": [("GK", 50, 5), ("CB", 37, 22), ("CB", 63, 22), ("FB", 13, 28), ("FB", 87, 28), ("DM", 50, 40),
              ("CM", 31, 54), ("CM", 69, 54), ("W", 13, 76), ("W", 87, 76), ("ST", 50, 86)],
    "4-2-3-1": [("GK", 50, 5), ("CB", 37, 22), ("CB", 63, 22), ("FB", 13, 28), ("FB", 87, 28), ("DM", 38, 42),
                ("DM", 62, 42), ("AM", 50, 64), ("W", 14, 72), ("W", 86, 72), ("ST", 50, 86)],
    "4-1-4-1": [("GK", 50, 5), ("CB", 37, 22), ("CB", 63, 22), ("FB", 13, 28), ("FB", 87, 28), ("DM", 50, 38),
                ("CM", 35, 56), ("CM", 65, 56), ("WM", 12, 62), ("WM", 88, 62), ("ST", 50, 84)],
    "4-4-2": [("GK", 50, 5), ("CB", 37, 22), ("CB", 63, 22), ("FB", 13, 28), ("FB", 87, 28), ("CM", 38, 50),
              ("CM", 62, 50), ("WM", 12, 58), ("WM", 88, 58), ("ST", 40, 84), ("ST", 60, 84)],
    "4-3-1-2": [("GK", 50, 5), ("CB", 37, 22), ("CB", 63, 22), ("FB", 13, 28), ("FB", 87, 28), ("DM", 50, 40),
                ("CM", 30, 52), ("CM", 70, 52), ("AM", 50, 66), ("ST", 40, 84), ("ST", 60, 84)],
    "3-5-2": [("GK", 50, 5), ("CB", 28, 22), ("CB", 50, 20), ("CB", 72, 22), ("WB", 8, 50), ("WB", 92, 50),
              ("DM", 50, 40), ("CM", 33, 58), ("CM", 67, 58), ("ST", 40, 84), ("ST", 60, 84)],
    "3-4-3": [("GK", 50, 5), ("CB", 28, 22), ("CB", 50, 20), ("CB", 72, 22), ("WB", 8, 52), ("WB", 92, 52),
              ("CM", 38, 46), ("CM", 62, 46), ("W", 15, 78), ("W", 85, 78), ("ST", 50, 86)],
    "3-4-2-1": [("GK", 50, 5), ("CB", 28, 22), ("CB", 50, 20), ("CB", 72, 22), ("WB", 8, 52), ("WB", 92, 52),
                ("CM", 38, 44), ("CM", 62, 44), ("IW", 33, 68), ("IW", 67, 68), ("ST", 50, 86)],
}
# Positions that can fill a slot type naturally (EA positions, main or alternative).
ELIGIBLE = {"GK": {"GK"}, "CB": {"CB"}, "FB": {"LB", "RB", "LWB", "RWB"}, "WB": {"LB", "RB", "LWB", "RWB", "LM", "RM"},
            "DM": {"CDM", "CM"}, "CM": {"CM", "CDM", "CAM"}, "AM": {"CAM", "CM", "CF"}, "IW": {"CAM", "LW", "RW", "LM", "RM", "CF"},
            "W": {"LW", "RW", "LM", "RM"}, "WM": {"LM", "RM", "LW", "RW"}, "ST": {"ST", "CF"}}
# Roles a slot can be asked to play.
SLOT_ROLES = {
    "GK": ["Sweeper keeper", "Shot-stopper"],
    "CB": ["Ball-playing centre-back", "Covering centre-back", "Stopper"],
    "FB": ["Inverted full-back", "Overlapping wing-back", "Defensive full-back"],
    "WB": ["Overlapping wing-back", "Inverted full-back", "Defensive full-back", "Classic winger"],
    "DM": ["Regista", "Anchor", "Ball-winning midfielder", "Tempo controller"],
    "CM": ["Box-to-box", "Mezzala", "Tempo controller", "Ball-winning midfielder", "Regista"],
    "AM": ["Classic 10", "Shadow striker", "Mezzala"],
    "IW": ["Inside forward", "Shadow striker", "Classic 10", "Wide playmaker"],
    "W": ["Inside forward", "Classic winger", "Two-way winger", "Wide playmaker", "Wide target"],
    "WM": ["Classic winger", "Two-way winger", "Wide playmaker", "Inside forward"],
    "ST": ["Pressing forward", "False 9", "Target man", "Poacher", "Channel runner", "Complete forward", "Finisher"],
}

# The game plan, in PES's own terms.
INSTRUCTIONS = {
    "attack": {"possession": "Possession game", "counter": "Quick counter"},
    "build_up": {"short": "Short pass", "long": "Long pass"},
    "area": {"centre": "Centre", "wide": "Wide"},
    "positioning": {"maintain": "Maintain formation", "flexible": "Flexible"},
    "defence": {"frontline": "Frontline pressure", "all_out": "All-out defence"},
    "containment": {"middle": "Middle", "flanks": "Flanks"},
    "pressing": {"aggressive": "Aggressive", "conservative": "Conservative"},
}
ADVANCED = {
    "attack": {
        "false_fullbacks": ("False full-backs", "Full-backs step inside into midfield when we have the ball."),
        "attacking_fullbacks": ("Attacking full-backs", "Full-backs push up and join the attack."),
        "centre_back_build_up": ("Centre-back build-up", "Centre-backs split wide; the keeper and the holding midfielder start the attack."),
        "tiki_taka": ("Tiki-taka", "Short passes and constant movement to offer the ball-carrier options."),
        "wing_rotation": ("Wing rotation", "Wide players and the nearest midfielder swap positions to drag markers."),
        "false_9": ("False 9", "The striker drops off the line to link play; the wide players run beyond."),
        "hug_touchline": ("Hug the touchline", "Wingers stay on the line to stretch the back four."),
        "anchoring": ("Anchoring", "The holding midfielder stays in front of the back line whatever happens."),
    },
    "defence": {
        "gegenpress": ("Gegenpress", "Win the ball back within seconds of losing it."),
        "man_marking": ("Man marking", "Each defender follows his opponent across the pitch."),
        "tight_marking": ("Tight marking", "Get tight to the receivers around the ball."),
        "counter_target": ("Counter target", "One forward stays high to launch the counter."),
        "deep_line": ("Deep defensive line", "The back line drops towards the box when defending."),
        "swarm_box": ("Swarm the box", "Bodies in the box to block shots and crosses."),
    },
}

# Coach styles: a whole plan at once. References are illustrations of the style, not models of those coaches.
STYLES = {
    "positional": {"label": "Positional play", "ref": "Guardiola's City", "formation": "4-3-3",
                   "plan": {"attack": "possession", "build_up": "short", "area": "centre", "positioning": "flexible", "support": 3,
                            "defence": "frontline", "containment": "middle", "pressing": "aggressive", "line": 8, "compact": 8},
                   "advanced": ["false_fullbacks", "tiki_taka", "gegenpress"],
                   "roles": {"GK": "Sweeper keeper", "CB": "Ball-playing centre-back", "FB": "Inverted full-back", "DM": "Tempo controller",
                             "CM": "Mezzala", "W": "Classic winger", "ST": "False 9"},
                   "idea": "Occupy the five lanes, outnumber the press in the first line, wingers wide to isolate their full-back."},
    "gegenpress": {"label": "Gegenpress", "ref": "Klopp's Liverpool", "formation": "4-3-3",
                   "plan": {"attack": "counter", "build_up": "short", "area": "wide", "positioning": "flexible", "support": 5,
                            "defence": "frontline", "containment": "middle", "pressing": "aggressive", "line": 8, "compact": 8},
                   "advanced": ["attacking_fullbacks", "gegenpress"],
                   "roles": {"GK": "Sweeper keeper", "CB": "Covering centre-back", "FB": "Overlapping wing-back", "DM": "Anchor",
                             "CM": "Box-to-box", "W": "Inside forward", "ST": "Pressing forward"},
                   "idea": "The press is the playmaker: win it high, attack before the opponent is set, full-backs provide the width."},
    "build_up_bait": {"label": "Build-up bait", "ref": "De Zerbi's Brighton", "formation": "4-2-3-1",
                      "plan": {"attack": "possession", "build_up": "short", "area": "centre", "positioning": "flexible", "support": 2,
                               "defence": "frontline", "containment": "middle", "pressing": "aggressive", "line": 7, "compact": 8},
                      "advanced": ["centre_back_build_up", "tiki_taka", "gegenpress"],
                      "roles": {"GK": "Sweeper keeper", "CB": "Ball-playing centre-back", "FB": "Inverted full-back",
                                "DM": ["Regista", "Tempo controller"], "AM": "Classic 10", "W": "Inside forward", "ST": "False 9"},
                      "idea": "Invite the press onto the ball at the back, then play through it into the space it leaves."},
    "wingbacks": {"label": "Wing-back possession", "ref": "Xabi Alonso's Leverkusen", "formation": "3-4-2-1",
                  "plan": {"attack": "possession", "build_up": "short", "area": "wide", "positioning": "flexible", "support": 4,
                           "defence": "frontline", "containment": "middle", "pressing": "aggressive", "line": 7, "compact": 8},
                  "advanced": ["attacking_fullbacks", "tiki_taka", "gegenpress"],
                  "roles": {"GK": "Sweeper keeper", "CB": "Ball-playing centre-back", "WB": "Overlapping wing-back",
                            "CM": ["Regista", "Box-to-box"], "IW": ["Classic 10", "Shadow striker"], "ST": "Complete forward"},
                  "idea": "Three at the back to build, wing-backs as wingers, two 10s between the lines."},
    "man_to_man": {"label": "Man-to-man", "ref": "Gasperini's Atalanta", "formation": "3-4-2-1",
                   "plan": {"attack": "counter", "build_up": "short", "area": "wide", "positioning": "flexible", "support": 6,
                            "defence": "frontline", "containment": "middle", "pressing": "aggressive", "line": 7, "compact": 6},
                   "advanced": ["attacking_fullbacks", "man_marking"],
                   "roles": {"GK": "Shot-stopper", "CB": "Stopper", "WB": "Overlapping wing-back", "CM": ["Ball-winning midfielder", "Box-to-box"],
                             "IW": ["Shadow striker", "Inside forward"], "ST": "Target man"},
                   "idea": "Duels everywhere: every defender follows his man, the centre-backs step into midfield."},
    "low_block": {"label": "Low block and counter", "ref": "Simeone's Atlético", "formation": "4-4-2",
                  "plan": {"attack": "counter", "build_up": "long", "area": "wide", "positioning": "maintain", "support": 6,
                           "defence": "all_out", "containment": "middle", "pressing": "conservative", "line": 3, "compact": 9},
                  "advanced": ["deep_line", "counter_target"],
                  "roles": {"GK": "Shot-stopper", "CB": "Stopper", "FB": "Defensive full-back", "CM": ["Ball-winning midfielder", "Box-to-box"],
                            "WM": "Two-way winger", "ST": ["Channel runner", "Target man"]},
                  "idea": "Two banks of four, ten metres apart; close the middle, win it, hit the space behind."},
    "wing_play": {"label": "Wing play", "ref": "Ferguson's United", "formation": "4-4-2",
                  "plan": {"attack": "possession", "build_up": "long", "area": "wide", "positioning": "maintain", "support": 5,
                           "defence": "frontline", "containment": "flanks", "pressing": "conservative", "line": 5, "compact": 6},
                  "advanced": ["hug_touchline", "wing_rotation"],
                  "roles": {"GK": "Shot-stopper", "CB": ["Stopper", "Covering centre-back"], "FB": "Overlapping wing-back",
                            "CM": ["Regista", "Box-to-box"], "WM": "Classic winger", "ST": ["Target man", "Poacher"]},
                  "idea": "Wide men beat their full-back and cross; a target and a poacher attack the near and far post."},
    "direct": {"label": "Direct", "ref": "Dyche's Burnley", "formation": "4-4-2",
               "plan": {"attack": "counter", "build_up": "long", "area": "wide", "positioning": "maintain", "support": 7,
                        "defence": "all_out", "containment": "flanks", "pressing": "conservative", "line": 4, "compact": 9},
               "advanced": ["counter_target", "swarm_box"],
               "roles": {"GK": "Shot-stopper", "CB": "Stopper", "FB": "Defensive full-back", "CM": ["Ball-winning midfielder", "Box-to-box"],
                         "WM": "Two-way winger", "ST": ["Target man", "Channel runner"]},
               "idea": "Win the first ball and the second ball; the box is defended with bodies."},
}
DEFAULT_PLAN = STYLES["positional"]["plan"]


def default_roles(formation: str, style: str) -> list[str]:
    """The role each slot plays under a coach style (a style written for another formation falls back to the slot's first role)."""
    want, seen, out = STYLES[style]["roles"], {}, []
    for slot, _, _ in FORMATIONS[formation]:
        r = want.get(slot)
        if isinstance(r, list):
            r = r[seen.get(slot, 0) % len(r)]
            seen[slot] = seen.get(slot, 0) + 1
        out.append(r if r in SLOT_ROLES[slot] else SLOT_ROLES[slot][0])
    return out


# ── Demands against capability ───────────────────────────────────────────────
# Each indicator: which slots carry it (weight), which attributes make it, and what the plan asks.
INDICATORS = {
    "build_up": ("Build-up under pressure", {"GK": 0.4, "CB": 1, "DM": 1, "CM": 0.8, "FB": 0.5, "WB": 0.4},
                 {"shortPassing": 0.3, "ballControl": 0.2, "composure": 0.25, "fl_tight_possession": 0.25}),
    "creation": ("Chance creation", {"AM": 1, "IW": 1, "CM": 0.7, "W": 0.8, "WM": 0.6, "ST": 0.3},
                 {"vision": 0.35, "shortPassing": 0.25, "dribbling": 0.2, "ballControl": 0.2}),
    "delivery": ("Width and delivery", {"W": 1, "WM": 1, "FB": 0.7, "WB": 1},
                 {"crossing": 0.45, "sprintSpeed": 0.25, "stamina": 0.3}),
    "finishing": ("Finishing", {"ST": 1, "W": 0.6, "IW": 0.7, "AM": 0.5, "WM": 0.4},
                  {"finishing": 0.45, "positioning": 0.25, "composure": 0.15, "shotPower": 0.15}),
    "transition": ("Transition speed", {"ST": 1, "W": 1, "WM": 1, "IW": 0.8, "FB": 0.3, "WB": 0.5},
                   {"sprintSpeed": 0.35, "acceleration": 0.3, "dribbling": 0.2, "reactions": 0.15}),
    "pressing": ("Pressing power", {"ST": 1, "W": 1, "WM": 0.8, "IW": 1, "AM": 1, "CM": 0.8, "DM": 0.4},
                 {"stamina": 0.3, "aggression": 0.2, "acceleration": 0.2, "defensiveAwareness": 0.15, "standingTackle": 0.15}),
    "cover": ("Cover in behind", {"CB": 1, "FB": 0.6, "WB": 0.4, "DM": 0.3},
              {"sprintSpeed": 0.45, "acceleration": 0.3, "defensiveAwareness": 0.25}),
    "box": ("Box defending", {"CB": 1, "FB": 0.4, "DM": 0.5, "WB": 0.3},
            {"headingAccuracy": 0.3, "jumping": 0.2, "strength": 0.25, "standingTackle": 0.25}),
    "engine": ("Engine", {"FB": 1, "WB": 1, "DM": 1, "CM": 1, "AM": 1, "IW": 1, "W": 1, "WM": 1, "ST": 1, "CB": 0.5},
               {"stamina": 1.0}),
}
FALLBACK = {"fl_tight_possession": "ballControl"}


# What each indicator means on the pitch, for the board's explanations.
INFO = {
    "build_up": "Can the first line receive under pressure and turn: keeper, centre-backs, holding and central midfielders on the ball.",
    "creation": "Who finds the pass between the lines and the final ball.",
    "delivery": "Crosses that arrive, from wide players with the legs to get there again and again.",
    "finishing": "Turning chances into goals: finishing, movement in the box, composure, power.",
    "transition": "How fast the ball goes from winning it to the opponent's box.",
    "pressing": "Legs and bite in the front six to win the ball back high.",
    "cover": "Recovery pace behind the defensive line when a ball goes over the top.",
    "box": "Winning headers and duels in the team's own box.",
    "engine": "Running at the plan's intensity for 90 minutes, all season.",
}
ATTR_LABEL = {"shortPassing": "short passing", "ballControl": "ball control", "composure": "composure", "fl_tight_possession": "tight possession (FL26)",
              "vision": "vision", "dribbling": "dribbling", "crossing": "crossing", "sprintSpeed": "sprint speed", "stamina": "stamina",
              "finishing": "finishing", "positioning": "attacking positioning", "shotPower": "shot power", "acceleration": "acceleration",
              "reactions": "reactions", "aggression": "aggression", "defensiveAwareness": "defensive awareness", "standingTackle": "standing tackle",
              "headingAccuracy": "heading", "jumping": "jumping", "strength": "strength"}


def intensity_terms(plan: dict, adv: set[str]) -> dict[str, list[tuple[str, float]]]:
    """Each demand's distance from a neutral plan, as the named settings that push it (0 = what a balanced side needs)."""
    p, a = plan, adv
    poss, cnt = p["attack"] == "possession", p["attack"] == "counter"
    front, allout = p["defence"] == "frontline", p["defence"] == "all_out"
    aggr = p["pressing"] == "aggressive"
    short, long_ = p["build_up"] == "short", p["build_up"] == "long"
    wide, centre = p["area"] == "wide", p["area"] == "centre"
    line, compact = p["line"], p["compact"]
    adv_label = {k: v[0] for group in ADVANCED.values() for k, v in group.items()}
    A = lambda k, w: (adv_label[k], w * (k in a))
    terms = {
        "build_up": [("Possession game", 0.45 * poss), ("Short build-up", 0.3 * short), ("Long build-up", -0.5 * long_),
                     A("tiki_taka", 0.3), A("centre_back_build_up", 0.35), A("false_fullbacks", 0.2), ("Quick counter", -0.2 * cnt)],
        "creation": [("Possession game", 0.3 * poss), ("Central attacking area", 0.2 * centre), A("false_9", 0.25), A("tiki_taka", 0.15),
                     ("Quick counter", -0.15 * cnt)],
        "delivery": [("Wide attacking area", 0.6 * wide), ("Central attacking area", -0.4 * centre), A("hug_touchline", 0.35),
                     A("attacking_fullbacks", 0.3), A("false_fullbacks", -0.3), A("wing_rotation", 0.15)],
        "finishing": [("Every plan needs goals", 0.2), ("Quick counter: fewer, better chances", 0.3 * cnt)],
        "transition": [("Quick counter", 0.75 * cnt), A("counter_target", 0.3), ("Long build-up", 0.25 * long_), ("Possession game", -0.3 * poss)],
        "pressing": [("Frontline pressure", 0.55 * front), ("Aggressive pressuring", 0.35 * aggr), A("gegenpress", 0.4), A("man_marking", 0.25),
                     ("All-out defence", -0.8 * allout), ("Conservative pressuring", -0.25 * (not aggr))],
        "cover": [(f"Defensive line {line}/10", (line - 5) / 4), A("deep_line", -0.4), A("man_marking", 0.2)],
        "box": [("All-out defence", 0.4 * allout), A("deep_line", 0.3), A("swarm_box", 0.3), (f"Low line ({line}/10)", 0.2 * (line <= 4)),
                ("Frontline pressure", -0.2 * front)],
        "engine": [("Aggressive pressuring", 0.4 * aggr), A("gegenpress", 0.35), A("attacking_fullbacks", 0.2), A("man_marking", 0.15),
                   (f"Compactness {compact}/10", 0.1 * (compact - 5)), (f"Defensive line {line}/10", 0.08 * (line - 5)),
                   ("Flexible positioning", 0.1 * (p["positioning"] == "flexible")), ("All-out defence", -0.3 * allout)],
    }
    return {k: [(l, round(float(v), 2)) for l, v in ts if abs(v) > 1e-9] for k, ts in terms.items()}


def intensity(plan: dict, adv: set[str]) -> dict[str, float]:
    """How far each demand sits from a neutral plan: 0 = what a balanced side needs, +1 = an extreme version of it."""
    return {k: float(np.clip(sum(v for _, v in ts), -1.5, 1.5)) for k, ts in intensity_terms(plan, adv).items()}


# Each indicator's spread across the big five's elevens (P10, P50, P90), measured by promesse.squad.calibrate();
# rough values until it has run.
CALIBRATION = {k: (60.0, 68.0, 76.0) for k in INDICATORS}
CALIBRATION_FILE = DATA / "scout" / "tactics_calibration.json"
if CALIBRATION_FILE.exists():
    CALIBRATION.update({k: tuple(v) for k, v in json.loads(CALIBRATION_FILE.read_text()).items()})


def demands(plan: dict, adv: set[str]) -> dict[str, float]:
    """What the plan asks, on the attribute scale: a neutral plan asks what the median big-five eleven has,
    an extreme one what only the top tenth have."""
    out = {}
    for k, z in intensity(plan, adv).items():
        p10, p50, p90 = CALIBRATION[k]
        out[k] = round(p50 + z * ((p90 - p50) if z >= 0 else (p50 - p10)), 1)
    return out


def player_scores(df: pd.DataFrame, key: str) -> pd.Series:
    """Each player's score on one indicator: the weighted attributes that make it (PES abilities fall back to EA's)."""
    _, _, attrs = INDICATORS[key]
    score = pd.Series(0.0, index=df.index)
    for a, w in attrs.items():
        v = df[a].astype(float) if a in df else pd.Series(np.nan, index=df.index)
        if FALLBACK.get(a) in df:
            v = v.fillna(df[FALLBACK[a]].astype(float))
        score += w * v.fillna(50)
    return score / sum(attrs.values())


def capability(xi: pd.DataFrame, key: str) -> tuple[float, list[tuple[str, float]]]:
    """The eleven's level on one indicator, and each contributing player's score (worst first)."""
    _, slots, attrs = INDICATORS[key]
    rows = xi[xi.slot.isin(slots)]
    if rows.empty:
        return float("nan"), []
    score = pd.Series(0.0, index=rows.index)
    for a, w in attrs.items():
        v = rows[a].astype(float) if a in rows else pd.Series(np.nan, index=rows.index)
        if FALLBACK.get(a) in rows:                     # a PES ability the player lacks: his EA equivalent
            v = v.fillna(rows[FALLBACK[a]].astype(float))
        score += w * v.fillna(50)
    score /= sum(attrs.values())
    w = rows.slot.map(slots).astype(float)
    value = float((score * w).sum() / w.sum())
    return value, sorted(zip(rows["name"], score.round(0)), key=lambda t: t[1])


def assess(xi: pd.DataFrame, plan: dict, adv: set[str]) -> dict:
    """Demand against capability for every indicator, then the risks and strengths that follow."""
    need = demands(plan, adv)
    rows, risks, strengths = [], [], []
    for key, (label, _, _) in INDICATORS.items():
        have, who = capability(xi, key)
        if np.isnan(have):
            continue
        gap = have - need[key]
        _, slots, attrs = INDICATORS[key]
        part = xi[xi.slot.isin(slots)]
        scores = player_scores(part, key)
        carriers = sorted([{"name": n, "ea_id": int(e), "slot": s, "weight": slots[s], "score": round(float(v), 1)}
                           for n, e, s, v in zip(part["name"], part.ea_id, part.slot, scores)], key=lambda d: d["score"])
        rows.append({"key": key, "label": label, "value": round(have, 1), "demand": round(need[key], 1), "gap": round(gap, 1),
                     "info": INFO[key], "slots": slots, "attrs": {ATTR_LABEL.get(a, a): w for a, w in attrs.items()},
                     "scale": list(CALIBRATION[key]), "z": round(intensity(plan, adv)[key], 2), "terms": intensity_terms(plan, adv)[key],
                     "who": carriers})
        weakest = ", ".join(f"{n} ({int(v)})" for n, v in who[:2])
        best = ", ".join(f"{n} ({int(v)})" for n, v in who[::-1][:2])
        if gap < -4:
            risks.append(RISK[key](plan, adv, have, weakest))
            rows[-1]["note"], rows[-1]["kind"] = risks[-1], "risk"
        elif gap > 7 and need[key] >= CALIBRATION[key][1]:          # only what the plan asks more of than a balanced side
            strengths.append(STRENGTH[key](plan, adv, have, best))
            rows[-1]["note"], rows[-1]["kind"] = strengths[-1], "strength"
    fit = float(np.mean([np.clip(1 + r["gap"] / 15, 0, 1) for r in rows])) if rows else 0.0
    return {"indicators": rows, "risks": risks, "strengths": strengths, "plan_fit": round(min(fit, 1.0), 2)}


def fit_of(values: dict, need: dict) -> float:
    return float(np.mean([np.clip(1 + (values[k] - need[k]) / 15, 0, 1) for k in values])) if values else 0.0


def tactical_fixes(plan: dict, adv: set[str], rows: list[dict], top: int = 2) -> list[dict]:
    """For each shortfall, the one-setting changes that close it most without opening others, best first."""
    values = {r["key"]: r["value"] for r in rows}
    need0 = demands(plan, adv)
    fit0 = fit_of(values, need0)
    adv_label = {k: v[0] for group in ADVANCED.values() for k, v in group.items()}
    cands = []
    for d in (-3, -2, -1, 1, 2):
        if 1 <= plan["line"] + d <= 10:
            cands.append(({**plan, "line": plan["line"] + d}, adv, {"type": "plan", "key": "line", "value": plan["line"] + d},
                          f"{'Drop' if d < 0 else 'Push up'} the defensive line from {plan['line']} to {plan['line'] + d}"))
    for d in (-2, 2):
        if 1 <= plan["compact"] + d <= 10:
            cands.append(({**plan, "compact": plan["compact"] + d}, adv, {"type": "plan", "key": "compact", "value": plan["compact"] + d},
                          f"Compactness from {plan['compact']} to {plan['compact'] + d}"))
    for k, opts in INSTRUCTIONS.items():
        for v, label in opts.items():
            if plan[k] != v:
                cands.append(({**plan, k: v}, adv, {"type": "plan", "key": k, "value": v}, f"{label} instead of {opts[plan[k]].lower()}"))
    for a in adv:
        cands.append((plan, adv - {a}, {"type": "adv", "remove": a}, f"Drop {adv_label[a].lower()}"))
    out = []
    for r in rows:
        if r["gap"] >= -1.5:
            continue
        best = []
        for pl, ad, change, text in cands:
            need = demands(pl, ad)
            closed = min(0.0, values[r["key"]] - need[r["key"]]) - min(0.0, r["gap"])
            if closed < 1:
                continue
            fit1 = fit_of(values, need)
            side = [{"label": x["label"], "from": need0[x["key"]], "to": need[x["key"]]} for x in rows
                    if x["key"] != r["key"] and abs(need[x["key"]] - need0[x["key"]]) >= 1]
            best.append({"target": r["key"], "change": change, "text": text, "demand_from": need0[r["key"]], "demand_to": need[r["key"]],
                         "fit_from": round(fit0, 3), "fit_to": round(fit1, 3), "side": side})
        best.sort(key=lambda b: (-(b["fit_to"] - b["fit_from"]), b["demand_to"]))
        out += best[:top]
    return out


def style_fits(values: dict) -> list[dict]:
    """How well each coach style's plan suits this eleven's capabilities (same eleven, each style's demands)."""
    out = [{"style": k, "label": s["label"], "fit": round(fit_of(values, demands(s["plan"], set(s["advanced"]))), 3)} for k, s in STYLES.items()]
    return sorted(out, key=lambda d: -d["fit"])


RISK = {
    "build_up": lambda p, a, v, w: f"Short build-up against a press with a first line at {v:.0f} in press resistance: turnovers in the defensive third. Weakest on the ball: {w}.",
    "creation": lambda p, a, v, w: f"Few players who find the final pass ({v:.0f}): the plan keeps the ball but may not break the block. Least creative: {w}.",
    "delivery": lambda p, a, v, w: f"The plan goes wide but the delivery is {v:.0f}: crosses that don't arrive. Weakest wide: {w}.",
    "finishing": lambda p, a, v, w: f"Chances will come; finishing at {v:.0f} wastes them. Least clinical: {w}.",
    "transition": lambda p, a, v, w: f"A counter-attacking plan with a front line at {v:.0f} in speed: the counter dies after two passes. Slowest: {w}.",
    "pressing": lambda p, a, v, w: f"The press asks for legs and bite the front players don't have ({v:.0f}): it gets played through. Weakest pressers: {w}.",
    "cover": lambda p, a, v, w: f"Line at {p['line']}/10 with a back line at {v:.0f} in recovery pace: space in behind for every ball over the top. Slowest: {w}.",
    "box": lambda p, a, v, w: f"Deep block, but the box defending is {v:.0f}: crosses and second balls will hurt. Weakest in the air: {w}.",
    "engine": lambda p, a, v, w: f"This intensity across a season needs an engine the eleven doesn't have ({v:.0f} stamina): starters fade, rotation is forced. Least stamina: {w}.",
}
STRENGTH = {
    "build_up": lambda p, a, v, w: f"Press-resistant first line ({v:.0f}): {w} can receive under pressure and turn.",
    "creation": lambda p, a, v, w: f"Creators between the lines ({v:.0f}): {w}.",
    "delivery": lambda p, a, v, w: f"Real width and delivery ({v:.0f}): {w}.",
    "finishing": lambda p, a, v, w: f"Clinical up front ({v:.0f}): {w}.",
    "transition": lambda p, a, v, w: f"Pace to kill teams on the break ({v:.0f}): {w}.",
    "pressing": lambda p, a, v, w: f"The legs and the bite to win it back high ({v:.0f}): {w}.",
    "cover": lambda p, a, v, w: f"Recovery pace to hold a high line ({v:.0f}): {w}.",
    "box": lambda p, a, v, w: f"Dominant in the box ({v:.0f}): {w}.",
    "engine": lambda p, a, v, w: f"An engine for the intensity ({v:.0f}): {w}.",
}


def load(plan: dict, adv: set[str]) -> float:
    """How hard the plan runs the starters, 0 (sit and wait) to 1 (press everything for 90 minutes)."""
    return float(np.clip(intensity(plan, adv)["engine"] / 1.2, 0, 1))


# How much of the plan's running each slot does: keepers none, centre-backs some, everyone ahead of them all of it.
RUNNING = {"GK": 0.0, "CB": 0.35, "FB": 1.0, "WB": 1.0, "DM": 0.9, "CM": 1.0, "AM": 0.9, "IW": 1.0, "W": 1.0, "WM": 1.0, "ST": 0.9}


def availability(stamina: np.ndarray, slots: list[str], plan: dict, adv: set[str]) -> np.ndarray:
    """Share of a starter's minutes he can carry under this plan: below the engine it asks, he needs rotating."""
    need = demands(plan, adv)["engine"]
    short = np.clip(need - np.nan_to_num(np.asarray(stamina, dtype=float), nan=65), 0, None)
    run = np.array([RUNNING.get(s, 1.0) for s in slots])
    return np.clip(1 - load(plan, adv) * run * short / 40, 0.6, 1.0)


# ── Shapes ───────────────────────────────────────────────────────────────────
def _side(x: float) -> int:
    return -1 if x < 45 else 1 if x > 55 else 0


def in_possession(slot: str, role: str, x: float, y: float, plan: dict, adv: set[str]) -> tuple[float, float, str | None]:
    """Where a role goes when we have the ball, and the name of the movement."""
    s = _side(x)
    lift = 1.6 * (plan["line"] - 5)
    if role == "Sweeper keeper":
        return x, y + 5, None
    if slot == "CB":
        if "centre_back_build_up" in adv or role == "Ball-playing centre-back" and plan["build_up"] == "short":
            return x + s * 9, y + lift - 2, "splits wide to build"
        if role == "Stopper" and "man_marking" in adv:
            return x, y + 14 + lift, "steps into midfield"
        return x, y + 3 + lift, None
    if role == "Inverted full-back" or (slot == "FB" and "false_fullbacks" in adv):
        return 50 + s * 13, y + 18 + lift, "inverts into midfield"
    if role == "Overlapping wing-back":
        extra = 6 if "attacking_fullbacks" in adv else 0
        return (4 if s < 0 else 96), min(y + 32 + extra + lift, 82), "overlaps"
    if role == "Defensive full-back":
        return x, y + 6 + lift, None
    if role == "Regista" and ("centre_back_build_up" in adv or plan["build_up"] == "short"):
        return 50, y - 15, "drops between the centre-backs"
    if role in ("Anchor",) or "anchoring" in adv and slot == "DM":
        return x, y + 2, "holds the zone"
    if role == "Mezzala":
        return 50 + (s or -1) * 20, y + 20, "attacks the half-space"
    if role == "Box-to-box":
        return x, y + 12, "late run into the box"
    if role == "Ball-winning midfielder":
        return x, y + 6, None
    if role == "Tempo controller":
        return x, y + 4, None
    if role == "Classic 10":
        return x, y + 6, "between the lines"
    if role == "Shadow striker":
        return x, y + 16, "runs beyond the striker"
    if role == "Inside forward":
        dx = (50 + s * 20) - x
        return x + dx, y + 6 + (8 if "false_9" in adv else 0), "cuts inside"
    if role == "Classic winger" or "hug_touchline" in adv and slot in ("W", "WM"):
        return (4 if s < 0 else 96), y + 4, "holds the touchline"
    if role == "Wide playmaker":
        return 50 + s * 26, y + 2, "drifts inside to create"
    if role == "Two-way winger":
        return x, y + 5, None
    if role == "Wide target":
        return x - s * 6, y + 12, "attacks the far post"
    if role == "False 9" or slot == "ST" and "false_9" in adv:
        return x, y - 12, "drops off the line"
    if role == "Channel runner":
        return x + (s or 1) * 10, y + 5, "runs the channel"
    if role in ("Poacher", "Target man", "Finisher", "Complete forward", "Pressing forward"):
        return x, y + 4, None
    return x, y + 4, None


def out_of_possession(slot: str, role: str, x: float, y: float, plan: dict, adv: set[str]) -> tuple[float, float]:
    """The defensive block: line height, distance between the lines, width, who presses and who drops."""
    line, compact = plan["line"], plan["compact"]
    y_def = 12 + 2.3 * line - (4 if "deep_line" in adv else 0)
    depth = 52 - 3.1 * compact
    narrow = 0.72 if plan["containment"] == "middle" else 0.92
    press_high = plan["defence"] == "frontline"
    nx = 50 + (x - 50) * narrow
    if slot == "GK":
        return 50, 4 + 0.6 * line
    if slot in ("CB", "FB"):
        return nx if slot == "CB" else 50 + (x - 50) * (narrow + 0.08), y_def
    if slot == "WB":
        return (50 + (x - 50) * 0.9, y_def) if not (press_high and plan["pressing"] == "aggressive" and "man_marking" in adv) else (x, y_def + depth * 0.45)
    if slot == "DM":
        return nx, y_def + max(10, depth * 0.42) - (4 if "swarm_box" in adv else 0)
    if slot == "CM":
        return nx, y_def + max(11, depth * 0.5) - (5 if "swarm_box" in adv else 0)
    if slot in ("W", "WM", "IW"):
        if press_high and plan["pressing"] == "aggressive":
            return 50 + (x - 50) * 0.7, y_def + depth + 4           # wingers jump onto the centre-backs
        return 50 + (x - 50) * (narrow + 0.12), y_def + depth * 0.5  # drop into the midfield line
    if slot == "AM":
        return nx, y_def + depth * 0.8 + (6 if press_high else 0)
    if slot == "ST":
        top = y_def + depth + (6 if press_high else 0) + (2 if plan["pressing"] == "aggressive" else 0) + (2 if "gegenpress" in adv else 0)
        if "counter_target" in adv:
            top += 6
        return nx, min(top, 90)
    return nx, y_def + depth * 0.5


def shape_label(points: list[tuple[float, float]]) -> str:
    """Outfield players grouped into lines by height, at most four lines, the way a coach would say it: '2-3-5', '4-4-2'."""
    ys = sorted(y for _, y in points)
    lines = [[ys[0]]]
    for v in ys[1:]:
        if v - lines[-1][-1] > 9:
            lines.append([v])
        else:
            lines[-1].append(v)
    while len(lines) > 4:                       # merge the two closest neighbouring lines
        gaps = [lines[i + 1][0] - lines[i][-1] for i in range(len(lines) - 1)]
        i = int(np.argmin(gaps))
        lines[i:i + 2] = [lines[i] + lines[i + 1]]
    return "-".join(str(len(l)) for l in lines)


def board(xi: list[dict], plan: dict, adv: set[str]) -> dict:
    """Positions in both phases, the shapes, movement arrows, rotations, the line and the pressing zone."""
    for p in xi:
        if not p:
            continue
        px, py, move = in_possession(p["slot"], p["role"], p["x"], p["y"], plan, adv)
        ox, oy = out_of_possession(p["slot"], p["role"], p["x"], p["y"], plan, adv)
        p.update({"ip": [round(float(np.clip(px, 3, 97)), 1), round(float(np.clip(py, 3, 95)), 1)], "move": move,
                  "op": [round(float(np.clip(ox, 3, 97)), 1), round(float(np.clip(oy, 3, 95)), 1)]})
    field = [p for p in xi if p and p["slot"] != "GK"]
    rotations = []
    if "wing_rotation" in adv:
        wide = [p for p in field if p["slot"] in ("W", "WM", "IW")]
        mids = [p for p in field if p["slot"] in ("CM", "AM")]
        for w in wide:
            if mids:
                m = min(mids, key=lambda q: abs(q["x"] - w["x"]))
                rotations.append([w["ea_id"], m["ea_id"]])
    # Where the team starts to engage: just under its front line out of possession.
    press_from = max(p["op"][1] for p in field) - 6
    y_def = 12 + 2.3 * plan["line"] - (4 if "deep_line" in adv else 0)
    return {"shape_in": shape_label([tuple(p["ip"]) for p in field]), "shape_out": shape_label([tuple(p["op"]) for p in field]),
            "rotations": rotations, "line_y": round(y_def, 1), "press_from": round(float(np.clip(press_from, 25, 85)), 1),
            "containment": plan["containment"], "long_ball": plan["build_up"] == "long" or "counter_target" in adv}


def options() -> dict:
    """Everything the board's controls need."""
    return {"formations": list(FORMATIONS), "slot_roles": SLOT_ROLES, "instructions": INSTRUCTIONS,
            "advanced": {k: {a: {"label": l, "tip": t} for a, (l, t) in v.items()} for k, v in ADVANCED.items()},
            "styles": {k: {"label": s["label"], "ref": s["ref"], "formation": s["formation"], "plan": s["plan"],
                           "advanced": s["advanced"], "idea": s["idea"]} for k, s in STYLES.items()},
            "indicators": {k: v[0] for k, v in INDICATORS.items()}}
