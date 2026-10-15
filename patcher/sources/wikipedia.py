"""Wikipedia: what the press says about a player, and what happened to him.

From each player's English Wikipedia page (CC BY-SA, attributed wherever it
is shown):
  * "Style of play" and similar sections -> trait tags (vision, reading the
    game, press resistance, work rate, leadership, temperament, injuries...)
    each with the sentence it came from, and the players he's compared to;
  * the career narrative -> dated events: loans, injuries, disputes with a
    coach, breakthroughs, the coaches named around his development.
Polite: one request at a time, a pause, every page cached.

Run:  python -m patcher.sources.wikipedia young
"""
from __future__ import annotations

import json
import re
import sys
import time

import pandas as pd
import requests
from unidecode import unidecode

from ..paths import DATA

API = "https://en.wikipedia.org/w/api.php"
RAW = DATA / "raw" / "wikipedia"
HEADERS = {"user-agent": "TacticalPatcherResearch/0.1 (football development research; github.com/Iyed-Bs)"}
PAUSE = 1.5
_session = requests.Session()
_session.headers.update(HEADERS)
_last = [0.0]

# Trait lexicon: each tag with the phrases that signal it in football writing. Negative tags are risks.
TRAITS = {
    "vision": ["vision", "sees the pass", "creative", "playmak", "range of passing", "incisive pass", "line-breaking"],
    "reading the game": ["reads the game", "reading of the game", "positional sense", "positioning", "anticipat", "intelligen", "tactical awareness", "game intelligence", "football iq"],
    "press resistance": ["press resist", "press-resist", "under pressure", "tight spaces", "escape pressure", "evade pressure", "composure on the ball", "never loses the ball", "retain possession", "ball retention"],
    "scanning": ["scans", "scanning", "awareness of his surroundings", "checks his shoulder", "head up"],
    "ball carrying": ["ball-carrying", "carries the ball", "progressive carr", "drive forward", "dribbling ability", "close control"],
    "work rate": ["work rate", "work-rate", "tireless", "stamina", "energetic", "box-to-box", "pressing"],
    "leadership": ["leader", "captain", "vocal", "leadership", "mature beyond his years", "maturity"],
    "composure": ["composure", "calm", "composed", "cool-headed", "poise"],
    "technique": ["technique", "technical ability", "first touch", "elegant", "ball control"],
    "physical": ["physical", "strength", "athletic", "pace", "quick", "powerful", "aerial ability"],
    "defensive": ["tackl", "interception", "ball-winning", "ball winning", "defensive awareness", "recover the ball"],
    "versatility": ["versatil", "can play as", "can also play", "utility"],
    # Risks and conditions
    "injuries": ["injury-prone", "injury prone", "injuries", "anterior cruciate", "acl", "ruptured", "fractured", "torn", "sidelined", "surgery"],
    "temperament": ["temperament", "disciplin", "red card", "hot-headed", "fell out", "dispute", "attitude", "suspended", "controversy"],
    "inconsistency": ["inconsisten", "struggled", "lack of playing time", "failed to", "dropped", "out of favour", "out of favor"],
}
EVENT_PATTERNS = {
    "loan": r"\b(loaned|on loan|loan spell|loan move|sent on loan)\b",
    "injury": r"\b(injur\w*|anterior cruciate|acl|ruptured|fractur\w*|surgery|sidelined)\b",
    "dispute": r"\b(fell out|dispute|disciplinary|suspended by the club|dropped from)\b",
    "debut": r"\b(made his (professional|senior|first[- ]team|league) debut|debuted)\b",
    "breakthrough": r"\b(breakthrough|established himself|became a regular|first-choice|nailed down)\b",
    "transfer": r"\b(signed for|joined|transferred to|completed a move|signed a contract)\b",
}
COACH = re.compile(r"\b(?:under|by) (?:manager|coach|head coach)? ?([A-Z][a-zà-ÿ]+(?: [A-Z][a-zà-ÿ\-]+){0,2})")
COMPARED = re.compile(r"(?:compared (?:to|with)|likened to|dubbed (?:the )?(?:new|next)|nicknamed (?:the )?(?:new|next)) ([A-Z][\w\-\. ]{2,40}?)(?=[,\.;]| and | due| for| because)")
YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")


def _get(params: dict, cache: str, tries: int = 6) -> dict | None:
    """Cached API call. Only successful answers are cached; when Wikipedia says slow down (429/503),
    wait as long as it asks (or back off exponentially) and try again."""
    out = RAW / cache
    if out.exists() and out.stat().st_size:
        return json.loads(out.read_text(encoding="utf-8"))
    for attempt in range(tries):
        wait = PAUSE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        r = _session.get(API, params={"format": "json", "formatversion": 2, "maxlag": 5, **params}, timeout=60)
        _last[0] = time.time()
        if r.status_code in (429, 503) or "maxlag" in r.text[:200]:
            time.sleep(float(r.headers.get("Retry-After", 0) or 0) or min(60, 5 * 2 ** attempt))
            continue
        if not r.ok or not r.text.lstrip().startswith("{"):
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(r.text, encoding="utf-8")
        return r.json()
    return None


def wikitext(title: str) -> str | None:
    res = _get({"action": "parse", "page": title, "prop": "wikitext", "redirects": 1}, f"parse/{_key(title)}.json") or {}
    return (res.get("parse") or {}).get("wikitext")


def _strip(w: str) -> str:
    """Readable text from wikitext: links to their labels, templates and refs removed."""
    w = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", w, flags=re.S)
    for _ in range(3):
        w = re.sub(r"\{\{[^{}]*\}\}", "", w)
    w = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", w)
    w = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", w)
    w = re.sub(r"'{2,}", "", w)
    return re.sub(r"<[^>]+>", "", w)


def infobox(w: str) -> dict:
    """Youth clubs, senior clubs and national teams (with years) from the football biography infobox."""
    def grab(prefix: str) -> list[tuple[str | None, str | None]]:
        out = []
        for i in range(1, 25):
            years = re.search(rf"\|\s*{prefix}years{i}\s*=\s*([^\n|]*)", w)
            team = re.search(rf"\|\s*{prefix}(?:clubs|teams?){i}\s*=\s*([^\n]*)", w)
            if not years and not team:
                if i > 2:
                    break
                continue
            out.append(((years.group(1).strip() if years else None), (_strip(team.group(1)).split("|")[0].strip() if team else None)))
        return out
    return {"youth": grab("youth"), "senior": grab(""), "national": grab("national")}


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", unidecode(s).lower())[:90]


def find_page(name: str, birth_year: int | None) -> str | None:
    """The footballer's page: search '<name> footballer', confirmed by his birth year in the intro."""
    res = _get({"action": "query", "list": "search", "srsearch": f"{name} footballer", "srlimit": 5}, f"search/{_key(name)}.json") or {}
    for hit in res.get("query", {}).get("search", []):
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        title = hit["title"]
        if birth_year and str(birth_year) not in snippet and str(birth_year) not in title:
            page = page_text(title)
            if not page or str(birth_year) not in page[:1500]:
                continue
        return title
    return None


def page_text(title: str) -> str | None:
    """Readable page text with its == Sections == kept (from the same wikitext request as the infobox)."""
    w = wikitext(title)
    return _strip(w) if w else None


def sections(text: str) -> dict:
    """Split a plaintext extract into its == Sections ==."""
    parts = re.split(r"\n(={2,4}) ?([^=\n]+?) ?\1\n", "\n" + text)
    out, current = {"Intro": parts[0]}, None
    for i in range(1, len(parts) - 2, 3):
        out[parts[i + 1].strip()] = parts[i + 2]
    return out


def analyse(text: str) -> dict:
    sec = sections(text)
    style = " ".join(v for k, v in sec.items() if re.search(r"style|player profile|playing style|scouting|description", k, re.I))
    career = " ".join(v for k, v in sec.items() if re.search(r"career|club|early life|youth|international", k, re.I))
    sentences = lambda t: [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", t) if len(s.strip()) > 20]
    traits = []
    for s in sentences(style or sec.get("Intro", "")):
        low = s.lower()
        for tag, words in TRAITS.items():
            if any(w in low for w in words):
                traits.append({"tag": tag, "sentence": s[:400]})
    events = []
    for s in sentences(career):
        low = s.lower()
        years = YEAR.findall(s)
        for kind, pat in EVENT_PATTERNS.items():
            if re.search(pat, low):
                events.append({"kind": kind, "year": int(years[0]) if years else None, "sentence": s[:400]})
    coaches = sorted({m.strip() for m in COACH.findall(career + " " + style)})[:12]
    compared = sorted({m.strip() for m in COMPARED.findall(style + " " + sec.get("Intro", ""))})[:8]
    return {"has_style_section": bool(style), "traits": traits, "events": events, "coaches": coaches, "compared_to": compared,
            "style_text": style[:4000]}


def run(players: pd.DataFrame, out_name: str) -> pd.DataFrame:
    rows = []
    out = DATA / f"{out_name}.parquet"
    for k, r in enumerate(players.itertuples()):
        try:
            title = find_page(r.name, getattr(r, "birth_year", None))
            text = page_text(title) if title else None
        except requests.RequestException:
            continue
        a = analyse(text) if text else {}
        if title:
            a["infobox"] = infobox(wikitext(title) or "")
        rows.append({"ea_id": getattr(r, "ea_id", None), "name": r.name, "wiki_title": title, **{k2: json.dumps(v) if isinstance(v, (list, dict)) else v for k2, v in a.items()}})
        if k % 200 == 0:
            pd.DataFrame(rows).to_parquet(out)
            print(f"{k}/{len(players)} players, {sum(1 for x in rows if x.get('wiki_title'))} pages", flush=True)
    df = pd.DataFrame(rows)
    df.to_parquet(out)
    print("done", len(df), flush=True)
    return df


if __name__ == "__main__":
    if sys.argv[1:] == ["young"]:
        p = pd.read_parquet(DATA / "scout" / "players.parquet")
        p = p[p.age <= 23].sort_values("overall", ascending=False)
        p["birth_year"] = pd.to_datetime(p.dob).dt.year
        run(p[["ea_id", "name", "birth_year"]], "wikipedia_young")
