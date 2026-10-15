"""SP Football Life (PES 2021 engine): the database the user plays with, and
the Master League careers played on it. Read-only, local, never shipped.

Two formats, both decoded empirically (no public spec covers PES 2021's
Player.bin), and validated three ways:
  * against EA FC 27 for 10,571 players linked by exact name,
  * against pesdb's PES 2021 pages for 113 players with the same PES ids,
  * against the Master League save records, whose ability block lists the
    same attributes in the game's own order (the three "awareness" stats
    together, then the keeper set in menu order, the two 2020 additions last).

Player.bin (pesdb, inside the season CPKs): 312 bytes per player. Abilities
are 6-bit fields storing value - 40, packed five per 32-bit word. Bit
positions below count from byte 0x0C of the record, little-endian.
Master League saves: a 156-byte record per player inside the save's world
blob; the evolved abilities sit at fixed bit
offsets before the player id.

Run:  python -m patcher.sources.fl
"""
from __future__ import annotations

import re
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from ..paths import DATA
from . import cpk

INSTALL = Path("C:/Program Files (x86)/SP Football Life 2026")
SAVES = Path.home() / "OneDrive/Documents/KONAMI/eFootball PES 2021 SEASON UPDATE/2026/save"
OUT = DATA / "fl"
# Season data CPKs in load order, with the date each was published.
SNAPSHOTS = [("data_s2526.cpk", "2025-10-12"), ("data_s2526a.cpk", "2025-10-22"), ("data_s2526b.cpk", "2025-11-12"),
             ("data_s2526c.cpk", "2025-11-15"), ("data_s2526d.cpk", "2025-12-01"), ("data_s25262.cpk", "2026-03-07"),
             ("data_s25262a.cpk", "2026-03-26"), ("data_s25262b.cpk", "2026-05-12")]
AGE_BASE = 16          # stored value + 16 = 2026 - birth year (FL counts age by calendar year from its 2026 base)

# Player.bin: bit offset (from record byte 0x0C) of each 6-bit ability.
ABILITY_BITS = {
    "offensive_awareness": 274, "ball_control": 185, "dribbling": 256, "tight_possession": 320, "low_pass": 167,
    "lofted_pass": 306, "finishing": 300, "heading": 192, "place_kicking": 154, "curl": 236, "speed": 210,
    "acceleration": 248, "kicking_power": 262, "jump": 198, "physical_contact": 294, "balance": 280, "stamina": 242,
    "defensive_awareness": 179, "ball_winning": 216, "aggression": 288, "gk_awareness": 230, "gk_catching": 268,
    "gk_clearing": 173, "gk_reflexes": 224, "gk_reach": 204,
}
ABILITIES = list(ABILITY_BITS)
# Other Player.bin fields: (bit from record byte 0x00, width, offset added).
FIELDS = {"height": (0x1B * 8, 8, 100), "age": (0x33 * 8, 6, AGE_BASE), "registered_position": (434, 4, 0), "form": (438, 3, 1),
          "injury_resistance": (458, 2, 1), "weak_foot_usage": (454, 2, 1), "weak_foot_accuracy": (462, 2, 1),
          "left_footed": (514, 1, 0), "playing_style": (155, 5, 0)}
POSITIONS = ["GK", "CB", "LB", "RB", "DMF", "CMF", "LMF", "RMF", "AMF", "LWF", "RWF", "SS", "CF"]
# pesdb's playing style numbering, which matches FL's field.
STYLES = {0: None, 1: "Goal Poacher", 2: "Dummy Runner", 3: "Fox in the Box", 4: "Prolific Winger", 5: "Classic No. 10",
          6: "Hole Player", 7: "Box-to-Box", 8: "Anchor Man", 9: "The Destroyer", 10: "Extra Frontman", 11: "Offensive Full-back",
          12: "Defensive Full-back", 13: "Target Man", 14: "Creative Playmaker", 15: "Build Up", 16: "Offensive Goalkeeper",
          17: "Defensive Goalkeeper", 18: "Roaming Flank", 19: "Cross Specialist", 20: "Orchestrator", 21: "Full-back Finisher"}
# Master League record: bit offset of each ability relative to the player id's position (negative = before it).
ML_ABILITY_BITS = {
    "offensive_awareness": -288, "defensive_awareness": -282, "gk_awareness": -276, "dribbling": -270, "ball_control": -264,
    "finishing": -256, "low_pass": -250, "lofted_pass": -244, "heading": -238, "ball_winning": -232, "place_kicking": -224,
    "curl": -218, "gk_catching": -212, "gk_clearing": -206, "gk_reflexes": -200, "gk_reach": -192, "speed": -186,
    "physical_contact": -180, "balance": -174, "kicking_power": -168, "acceleration": -160, "jump": -154, "stamina": -148,
    "tight_possession": -136, "aggression": -128,
}
ML_RECORD = 156


def _pesdb(cpk_name: str, table: str) -> bytes | None:
    """One decompressed pesdb table from a season CPK, or None if that CPK doesn't carry it."""
    try:
        raw = cpk.read(INSTALL / "download" / cpk_name, f"common/etc/pesdb/{table}.bin")
    except FileNotFoundError:
        return None
    i = raw.find(b"\xff\x10\x81WESYS")
    return zlib.decompress(raw[i + 16:]) if i >= 0 else raw


def _bits(block: np.ndarray) -> np.ndarray:
    return np.unpackbits(block, axis=1, bitorder="little").astype(np.int64)


def _field(bits: np.ndarray, start: int, width: int) -> np.ndarray:
    return (bits[:, start:start + width] * (1 << np.arange(width))).sum(1)


def decode_players(data: bytes) -> pd.DataFrame:
    rec = np.frombuffer(data, dtype=np.uint8).reshape(-1, 312)
    df = pd.DataFrame({
        "fl_id": rec[:, 8:12].copy().view("<u4").ravel().astype(np.int64),
        "name": [bytes(r[0x44:0x81]).split(b"\0")[0].decode("utf-8", "replace") for r in rec],
        "shirt_name": [bytes(r[0x81:0xBE]).split(b"\0")[0].decode("utf-8", "replace") for r in rec],
    })
    gb = _bits(rec[:, 0x0C:0x44])
    for name, start in ABILITY_BITS.items():
        df[name] = _field(gb, start, 6) + 40
    ab = _bits(rec[:, 0x00:0x44])
    for name, (start, width, add) in FIELDS.items():
        df[name] = _field(ab, start, width) + add
    df["registered_position"] = df.registered_position.map(lambda i: POSITIONS[i] if i < len(POSITIONS) else None)
    df["playing_style"] = df.playing_style.map(STYLES)
    df["skills_raw"] = [bytes(r[0x3C:0x44]).hex() for r in rec]          # skill + COM style bitmask, decoded later
    return df


def decode_teams(data: bytes) -> pd.DataFrame:
    rec = np.frombuffer(data, dtype=np.uint8).reshape(-1, 1532)
    return pd.DataFrame({
        "team_id": rec[:, 8:12].copy().view("<u4").ravel().astype(np.int64),
        "team": [bytes(r[0x170:0x170 + 70]).split(b"\0")[0].decode("utf-8", "replace") for r in rec],
        "abbr": [bytes(r[0x392:0x392 + 4]).split(b"\0")[0].decode("utf-8", "replace") for r in rec],
    })


def decode_assignments(data: bytes) -> pd.DataFrame:
    rec = np.frombuffer(data, dtype=np.uint8).reshape(-1, 16)
    w = rec.copy().view("<u4")
    return pd.DataFrame({"player_id": w[:, 1].astype(np.int64), "team_id": w[:, 2].astype(np.int64),
                         "shirt": rec[:, 12].astype(int), "order": (rec[:, 13] >> 2).astype(int)})


def snapshots() -> pd.DataFrame:
    """Every FL database snapshot, one row per player per snapshot, with his team."""
    frames, teams, assign = [], None, None
    for name, date in SNAPSHOTS:
        players = _pesdb(name, "Player")
        if players is None:
            continue
        t, a = _pesdb(name, "Team"), _pesdb(name, "PlayerAssignment")
        teams = decode_teams(t) if t is not None else teams              # carry the last published table forward
        assign = decode_assignments(a) if a is not None else assign
        df = decode_players(players).assign(snapshot=name, snapshot_date=pd.Timestamp(date))
        club = assign.merge(teams, on="team_id", how="left")
        club = club[club.team_id >= 100]                                 # low ids are national teams
        df = df.merge(club.drop_duplicates("player_id").rename(columns={"player_id": "fl_id"}), on="fl_id", how="left")
        frames.append(df)
        print(f"{name}: {len(df)} players, {df.team.notna().sum()} at clubs", flush=True)
    return pd.concat(frames, ignore_index=True)


def ml_players(path: Path) -> pd.DataFrame:
    """Evolved abilities of every player in one Master League save.

    The save reader itself is a private module, not published with this repo; what the saves taught (how the game
    grows players) ships as the measured curve in fl26_ml_saves.parquet.
    """
    try:
        from .fl_save import open_save
    except ImportError as e:
        raise RuntimeError("reading Master League saves needs the private save reader (not part of this repo)") from e
    desc, blob = open_save(path.read_bytes())                          # the save's caption and its world state
    date = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", desc)              # in-game date, day first
    club = re.search(r"Master League \d+\s+(.+?)\s*\n", desc)
    ids = set(pd.read_parquet(OUT / "fl26_players.parquet", columns=["fl_id"]).fl_id.unique().tolist())
    a = np.frombuffer(blob[: len(blob) // 4 * 4], dtype="<u4")
    hit = np.where(np.isin(a, list(ids)) & (a > 1000))[0] * 4
    run = hit[1:][np.diff(hit) == ML_RECORD]                             # ids inside the 156-byte player table
    run = run[(run >= 64) & (run + 4 <= len(blob))]
    recs = np.stack([np.frombuffer(blob[p - 64:p], dtype=np.uint8) for p in run])
    bits = _bits(recs)
    df = pd.DataFrame({"fl_id": a[run // 4].astype(np.int64)})
    for name, rel in ML_ABILITY_BITS.items():
        df[name] = _field(bits, 64 * 8 + rel, 6) + 40
    df["save"], df["club"] = path.name, club.group(1).strip() if club else None
    df["save_date"] = pd.Timestamp(int(date.group(3)), int(date.group(2)), int(date.group(1))) if date else pd.NaT
    return df.drop_duplicates("fl_id")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    snaps = snapshots()
    snaps.to_parquet(OUT / "fl26_snapshots.parquet")
    latest = snaps[snaps.snapshot == snaps.snapshot.iloc[-1]]
    latest.to_parquet(OUT / "fl26_players.parquet")
    print("players in the latest snapshot:", len(latest))
    if SAVES.exists() and (Path(__file__).parent / "fl_save.py").exists():
        ml = pd.concat([ml_players(p) for p in sorted(SAVES.glob("ML0*"))], ignore_index=True)
        ml.to_parquet(OUT / "fl26_ml_saves.parquet")
        print(ml.groupby(["save", "club", "save_date"], dropna=False).size())
