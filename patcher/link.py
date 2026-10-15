"""Linking players across sources.

Transfermarkt is the master record. FIFA ratings and Wyscout events are
attached to it by date of birth (exact) and name tokens: every source spells
names differently ("L. Messi", "Lionel Andrés Messi Cuccittini",
"Lionel Messi"), but none of them disagrees on a birthday.

A candidate pair is accepted when the birthdays match and at least one
meaningful name token (3+ letters) is shared; among several candidates with
the same birthday, the one with the highest token overlap wins, and ties are
dropped rather than guessed.
"""
from __future__ import annotations

import re

import pandas as pd
from unidecode import unidecode

STOP = {"de", "da", "do", "dos", "das", "del", "van", "von", "der", "den", "le", "la", "el", "al", "bin", "ben", "jr", "junior"}


def tokens(*names: str | None) -> frozenset[str]:
    out: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        for t in re.split(r"[^a-z]+", unidecode(name).lower()):
            if len(t) >= 3 and t not in STOP:
                out.add(t)
    return frozenset(out)


def link(master: pd.DataFrame, other: pd.DataFrame, other_id: str, on: str | list[str] = "dob",
         min_overlap: int = 1, min_score: float = 0.0) -> pd.DataFrame:
    """master: player_id, <on>, tok. other: <other_id>, <on>, tok. Returns player_id <-> other_id.

    `on` is the blocking key: the exact birth date by default; a birth year
    (Football Life stores only that) with stricter name thresholds.
    """
    cand = master.merge(other, on=on, suffixes=("_m", "_o"))
    cand["overlap"] = [len(a & b) for a, b in zip(cand.tok_m, cand.tok_o)]
    cand["union"] = [len(a | b) or 1 for a, b in zip(cand.tok_m, cand.tok_o)]
    cand = cand[cand.overlap >= min_overlap].copy()
    cand["score"] = cand.overlap / cand["union"]
    cand = cand[cand.score >= min_score]
    # Best master for each other id; drop exact ties.
    cand = cand.sort_values("score", ascending=False)
    best = cand.groupby(other_id).head(2)
    tie = best.groupby(other_id).score.transform(lambda s: len(s) > 1 and s.iloc[0] == s.iloc[1])
    best = best[~tie].groupby(other_id).head(1)
    # And keep each master once (its best match).
    best = best.sort_values("score", ascending=False).groupby("player_id").head(1)
    return best[["player_id", other_id, "score"]]
