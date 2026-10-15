"""pesdb.net: PES 2011-2021 and eFootball player pages in the PES attribute
schema (the one Football Life uses).

Polite by design: one request at a time, a pause between requests, every
page cached under PATCHER_DATA/raw/pesdb/<edition>/<id>.html and never
fetched twice. Parsed into one row per player with every attribute,
characteristic, skill and COM playing style.
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path

import pandas as pd
import requests

from ..paths import DATA

BASE = "https://pesdb.net"
RAW = DATA / "raw" / "pesdb"
UA = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
ABILITIES = ["Offensive Awareness", "Ball Control", "Dribbling", "Tight Possession", "Low Pass", "Lofted Pass", "Finishing",
             "Heading", "Place Kicking", "Curl", "Defensive Awareness", "Ball Winning", "Aggression", "GK Awareness",
             "GK Catching", "GK Clearing", "GK Reflexes", "GK Reach", "Speed", "Acceleration", "Kicking Power", "Jump",
             "Physical Contact", "Balance", "Stamina", "Weak Foot Usage", "Weak Foot Accuracy", "Form", "Injury Resistance"]

_session = requests.Session()
_session.headers.update(UA)


def fetch(edition: str, player_id: int, pause: float = 2.0) -> str | None:
    """Cached page for one player id, e.g. fetch("pes2021", 7511)."""
    path = RAW / edition / f"{player_id}.html"
    if path.exists() and (path.stat().st_size > 0 or path.with_suffix(".404").exists()):
        return path.read_text(encoding="utf-8") or None
    path.parent.mkdir(parents=True, exist_ok=True)
    r = _session.get(f"{BASE}/{edition}/", params={"id": player_id}, timeout=60)
    time.sleep(pause)
    if r.status_code == 404:                       # a true miss: remembered, never asked again
        path.write_text("", encoding="utf-8")
        path.with_suffix(".404").touch()
        return None
    if not r.ok or not r.text:                     # throttled or failing: back off, retry on the next pass
        time.sleep(60 if r.status_code in (429, 503) else 10)
        return None
    path.write_text(r.text, encoding="utf-8")      # only real pages are cached
    return r.text


def _lines(page: str) -> list[str]:
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return [s.strip() for s in html.unescape(re.sub(r"<[^>]+>", "\n", page)).splitlines() if s.strip()]


def parse(page: str) -> dict | None:
    """Every attribute on a classic-PES player page, plus skills and COM styles."""
    lines = _lines(page)
    if "Offensive Awareness" not in lines:
        return None
    row = {}
    for i, s in enumerate(lines[:-1]):
        if s in ABILITIES and lines[i + 1].lstrip("-").isdigit() and s not in row:
            row[s] = int(lines[i + 1])
    for key in ("Player Name", "Club", "League", "Nationality", "Primary Position", "Playing Style", "Stronger Foot"):
        if key in lines:
            row[key] = lines[lines.index(key) + 1]
    for key, label in (("Age", "Age"), ("Height", "Height"), ("Weight", "Weight"), ("OVR", "Overall Rating")):
        if label in lines and lines[lines.index(label) + 1].isdigit():
            row[key] = int(lines[lines.index(label) + 1])

    def block(start: str, stop: tuple[str, ...]) -> list[str]:
        if start not in lines:
            return []
        out = []
        for s in lines[lines.index(start) + 1:]:
            if s in stop:
                break
            out.append(s)
        return out
    row["skills"] = ",".join(block("Player Skills", ("COM Playing Styles", "Find Similar Players")))
    row["com_styles"] = ",".join(block("COM Playing Styles", ("Find Similar Players", "Advertisement")))
    return row


def players(edition: str, ids: list[int], pause: float = 2.0) -> pd.DataFrame:
    rows = []
    for pid in ids:
        page = fetch(edition, int(pid), pause)
        rec = parse(page) if page else None
        if rec:
            rows.append({"edition": edition, "pes_id": int(pid), **rec})
    return pd.DataFrame(rows)


def list_ids(edition: str, pause: float = 2.0, max_pages: int = 2000) -> list[int]:
    """Every player id in an edition, from its paginated player list (cached pages)."""
    ids, seen_pages = [], 0
    for page_no in range(1, max_pages + 1):
        path = RAW / edition / "_list" / f"{page_no}.html"
        if path.exists():
            page = path.read_text(encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            r = _session.get(f"{BASE}/{edition}/players/", params={"page": page_no}, timeout=60)
            time.sleep(pause)
            page = r.text if r.ok else ""
            path.write_text(page, encoding="utf-8")
        found = [int(x) for x in re.findall(rf'href="/{edition}/players/[a-z0-9-]+-(\d+)"', page)]
        new = [x for x in dict.fromkeys(found) if x not in set(ids)]
        if not new:
            break
        ids.extend(new)
        seen_pages = page_no
        if page_no % 50 == 0:
            print(f"{edition}: list page {page_no}, {len(ids)} ids", flush=True)
    print(f"{edition}: {len(ids)} ids over {seen_pages} list pages", flush=True)
    return ids


if __name__ == "__main__":
    # python -m patcher.sources.pesdb pes2021 [priority_ids.csv]
    #   enumerates the edition's player list, then fetches every player page,
    #   ids in the priority file first; cached, resumable, parsed table written as it goes.
    import sys
    edition = sys.argv[1]
    ids = list_ids(edition)
    if len(sys.argv) > 2:
        first = set(pd.read_csv(sys.argv[2]).iloc[:, 0].astype(int))
        ids = [x for x in ids if x in first] + [x for x in ids if x not in first]
    rows = []
    for k, pid in enumerate(ids):
        page = fetch(edition, pid)
        rec = parse(page) if page else None
        if rec:
            rows.append({"edition": edition, "pes_id": pid, **rec})
        if k % 500 == 0:
            print(f"{edition}: {k}/{len(ids)} fetched, {len(rows)} parsed", flush=True)
            pd.DataFrame(rows).to_parquet(DATA / f"pesdb_{edition}.parquet")
    pd.DataFrame(rows).to_parquet(DATA / f"pesdb_{edition}.parquet")
    print("done", len(rows))
