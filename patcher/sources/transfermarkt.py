"""Transfermarkt, read directly: the transfer windows and squads the CC0
dataset stopped covering (it froze on 6 July 2026), and the competitions it
never covered (second divisions, South America, youth).

Polite by design: one request at a time, at least the 2-second crawl delay
the site's robots.txt asks for, every page cached under
PATCHER_DATA/raw/transfermarkt/ and never fetched twice. Private research
use; the raw pages are not redistributed.

Run:  python -m patcher.sources.transfermarkt 2026      (summer 2026 window)
"""
from __future__ import annotations

import re
import sys
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

from ..paths import DATA

BASE = "https://www.transfermarkt.com"
RAW = DATA / "raw" / "transfermarkt"
HEADERS = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "accept-language": "en-GB,en;q=0.9"}
DELAY = 2.5

# Transfermarkt competition codes: tier 1-3 in Europe's main nations, the
# rest of Europe's top flights, South and North America, and other markets
# that buy and sell young players.
COMPETITIONS = {
    # France
    "FR1": "Ligue 1", "FR2": "Ligue 2", "FR3": "Championnat National",
    # England
    "GB1": "Premier League", "GB2": "Championship", "GB3": "League One", "GB4": "League Two",
    # Spain, Italy, Germany
    "ES1": "LaLiga", "ES2": "LaLiga2", "IT1": "Serie A", "IT2": "Serie B", "L1": "Bundesliga", "L2": "2. Bundesliga", "L3": "3. Liga",
    # Rest of Europe, tier 1 (and 2 where it feeds the big leagues)
    "PO1": "Liga Portugal", "PO2": "Liga Portugal 2", "NL1": "Eredivisie", "NL2": "Eerste Divisie", "BE1": "Jupiler Pro League",
    "BE2": "Challenger Pro League", "TR1": "Süper Lig", "SC1": "Scottish Premiership", "A1": "Austrian Bundesliga",
    "C1": "Swiss Super League", "DK1": "Superliga", "SE1": "Allsvenskan", "NO1": "Eliteserien", "PL1": "Ekstraklasa",
    "TS1": "Czech First League", "GR1": "Super League Greece", "KR1": "SuperSport HNL", "SER1": "Serbian SuperLiga",
    "UKR1": "Ukrainian Premier League", "RU1": "Russian Premier Liga", "RO1": "SuperLiga Romania", "UNG1": "Hungarian NB I",
    "SLO1": "Slovak Super Liga", "ZYP1": "Cyprus First Division", "ISR1": "Ligat ha'Al", "BOS1": "Bosnian Premier League",
    # South America
    "BRA1": "Brasileirão Série A", "BRA2": "Brasileirão Série B", "AR1N": "Liga Profesional (Argentina)", "COL1": "Liga BetPlay",
    "CLPD": "Liga de Primera (Chile)", "URU1": "Liga AUF Uruguaya", "PR1A": "Primera División (Paraguay)", "EL1S": "LigaPro (Ecuador)",
    "TDeA": "Liga 1 (Peru)",
    # Other markets
    "MLS1": "MLS", "MEX1": "Liga MX", "SA1": "Saudi Pro League", "JAP1": "J1 League", "RSK1": "K League 1", "QSL": "Qatar Stars League",
    "UAE1": "UAE Pro League", "MAR1": "Botola Pro", "EGY1": "Egyptian Premier League", "TUN1": "Ligue Professionnelle 1",
}

_session = requests.Session()
_session.headers.update(HEADERS)
_last = [0.0]


def get(path: str, cache: str) -> str:
    """Cached GET with the crawl delay."""
    out = RAW / cache
    if out.exists():
        return out.read_text(encoding="utf-8")
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    r = _session.get(BASE + path, timeout=60)
    _last[0] = time.time()
    out.parent.mkdir(parents=True, exist_ok=True)
    text = r.text if r.ok else ""
    out.write_text(text, encoding="utf-8")
    return text


def _id(href: str | None, kind: str) -> int | None:
    m = re.search(rf"/{kind}/(\d+)", href or "")
    return int(m.group(1)) if m else None


def _money(text: str) -> float | None:
    """'€86.00m' -> 86_000_000; loans, free transfers and '-' -> None."""
    m = re.search(r"€\s*([\d.,]+)\s*(bn|m|k|Th\.)?", text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value * {"bn": 1e9, "m": 1e6, "k": 1e3, "Th.": 1e3}.get(m.group(2) or "", 1)


def league_transfers(code: str, season: int) -> pd.DataFrame:
    """Every club's arrivals and departures in one league for one season."""
    page = get(f"/x/transfers/wettbewerb/{code}/saison_id/{season}", f"transfers/{code}_{season}.html")
    soup = BeautifulSoup(page, "lxml")
    rows = []
    for box in soup.select("div.box"):
        h2 = box.find("h2")
        tables = box.find_all("table")
        if not h2 or not tables:
            continue
        club_a = h2.find("a", href=re.compile(r"/verein/\d+"))
        club_id, club = _id(club_a.get("href") if club_a else None, "verein"), h2.get_text(" ", strip=True)
        if club_id is None:
            continue
        for table in tables:
            head = table.find("th")
            direction = (head.get_text(strip=True) if head else "").lower()
            if direction not in ("in", "out"):
                continue
            for tr in table.select("tbody > tr"):
                tds = tr.find_all("td", recursive=False)
                if len(tds) < 9:
                    continue
                player_a = tds[0].find("a", href=re.compile(r"/profil/spieler/\d+"))
                other_a = tds[7].find("a", href=re.compile(r"/verein/\d+")) or tds[6].find("a", href=re.compile(r"/verein/\d+"))
                fee_text = tds[8].get_text(" ", strip=True)
                rows.append({
                    "competition": code, "season": season, "club_id": club_id, "club": club, "direction": direction,
                    "player_id": _id(player_a.get("href") if player_a else None, "spieler"),
                    "player": player_a.get_text(strip=True) if player_a else tds[0].get_text(" ", strip=True),
                    "age": pd.to_numeric(tds[1].get_text(strip=True), errors="coerce"),
                    "nationality": ",".join(img.get("title", "") for img in tds[2].find_all("img")),
                    "position": tds[3].get_text(strip=True), "market_value": _money(tds[5].get_text(" ", strip=True)),
                    "other_club_id": _id(other_a.get("href") if other_a else None, "verein"),
                    "other_club": tds[7].get_text(" ", strip=True), "fee_text": fee_text, "fee": _money(fee_text),
                    "loan": "loan" in fee_text.lower(), "transfer_id": _id(tds[8].find("a").get("href") if tds[8].find("a") else None, "transfer_id"),
                })
    return pd.DataFrame(rows)


def window(season: int) -> pd.DataFrame:
    frames = []
    for code in COMPETITIONS:
        df = league_transfers(code, season)
        print(f"{code:5s} {COMPETITIONS[code]:32s} {len(df):5d} moves", flush=True)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(DATA / f"tm_transfers_{season}.parquet")
    return out


if __name__ == "__main__":
    for s in (int(x) for x in sys.argv[1:] or ["2026"]):
        w = window(s)
        print(s, len(w), "moves,", w.player_id.nunique(), "players")
