"""Realism audit: does the Patcher behave like football?

Runs the whole app on cases a football person can judge at a glance and
flags what doesn't hold:
- names: no EA placeholder left anywhere a club is shown;
- players: prospects grow, veterans decline, stars plateau, ranges widen
  with age and uncertainty;
- calls: the verdict and the timing agree with each other and with the club;
- market: the wonderkids list is made of wonderkids;
- tactics: elite elevens fit demanding plans better than weak ones, every
  instruction moves its demand the right way, every shape has ten outfield
  players on the pitch;
- squad lab: a squad played forward ages, its youngsters with minutes grow.

Run:  python -m patcher.audit
"""
from __future__ import annotations

import itertools

import numpy as np

from . import tactics
from .scout import Scout, verdict
from .squad import SquadLab

PLACEHOLDERS = ["Lombardia FC", "Milano FC", "Bergamo Calcio", "Piemonte Calcio", "Latium", "Paris SG", "OL", "OM", "Man Utd", "Spurs"]
PANEL = {  # name -> what football says should happen over four seasons
    "Lamine Yamal": "elite already: plateau high or small growth",
    "Ayyoub Bouaddi": "80 at 19: grows a few points",
    "Rio Ngumoha": "18, starter minutes uncertain: wide range",
    "Konstantinos Karetsas": "19, new club: grows",
    "Kenan Yıldız": "84 at 21: grows a little",
    "Ethan Mbappé": "20, few minutes: wide range",
    "Mohamed Salah": "34: declines",
    "Robert Lewandowski": "38: declines clearly",
    "Virgil van Dijk": "35: declines",
}


class Audit:
    def __init__(self):
        self.sc = Scout()
        from .profile import Profiler
        self.sc.profiler = Profiler(self.sc.players)
        self.lab = SquadLab(self.sc)
        self.problems, self.notes = [], []

    def flag(self, ok: bool, what: str):
        (self.notes if ok else self.problems).append(("ok  " if ok else "FIX ") + what)

    def find(self, name):
        hits = self.sc.search(name, 1)
        return None if hits.empty else hits.iloc[0]

    # ── names ────────────────────────────────────────────────────────────────
    def names(self):
        shown = set(self.sc.clubs.index) | set(self.sc.players.club_now.dropna()) | set(self.lab.market.club_now.dropna())
        left = [n for n in PLACEHOLDERS if n in shown]
        self.flag(not left, f"club names: placeholders left {left}" if left else "club names: no EA placeholder shown")

    # ── players ──────────────────────────────────────────────────────────────
    def players(self):
        for name, expect in PANEL.items():
            r = self.find(name)
            if r is None:
                self.flag(False, f"{name}: not found")
                continue
            p = self.sc.project(int(r.ea_id))
            p50, p10, p90 = p["overall"]["p50"], p["overall"]["p10"], p["overall"]["p90"]
            change, width = p50[-1] - p50[0], p90[-1] - p10[-1]
            line = f"{name} ({int(r.age)}, {int(r.overall)}, {r.club_now}): P50 {p50[0]:.0f} -> {p50[-1]:.0f}, P10-P90 {p10[-1]:.0f}-{p90[-1]:.0f} | expect: {expect}"
            if r.age >= 33:
                ok = change < -0.5
            elif r.overall >= 88:
                ok = -1.5 <= change <= 3
            elif r.age <= 21:
                ok = change >= 0 and width >= 4
            else:
                ok = -1 <= change <= 6
            self.flag(ok, line)

    # ── calls ────────────────────────────────────────────────────────────────
    def calls(self):
        from .decide import final_call, solve
        cases = [("Ayyoub Bouaddi", "Real Madrid"), ("Ayyoub Bouaddi", "Stade Rennais FC"), ("Rio Ngumoha", "Liverpool FC"),
                 ("Ethan Mbappé", "Stade Rennais FC"), ("Lamine Yamal", "FC Metz"), ("Konstantinos Karetsas", "Borussia Dortmund")]
        for name, club in cases:
            r = self.find(name)
            if r is None or club not in self.sc.clubs.index:
                self.flag(False, f"call {name} for {club}: player or club missing ({club in self.sc.clubs.index})")
                continue
            v = verdict(self.sc, int(r.ea_id), club)
            t = solve(self.sc, int(r.ea_id), club, runs=200)
            f = final_call(v, t)
            own = r.club_now == club
            text = f"call {name} for {club}: {f['call']} (worth {t['worth_to_us_now'] / 1e6:.0f}m vs fee {t['price_now'] / 1e6:.0f}m, best {t['best']})"
            ok = True
            if own:
                ok = "already" in f["call"].lower() or "yours" in f["call"].lower() or f["call"].lower().startswith(("keep", "your"))
            elif f["call"].startswith("Buy now") and t["worth_to_us_now"] < t["price_now"]:
                ok = False
            self.flag(ok, text)

    # ── market ───────────────────────────────────────────────────────────────
    def market(self):
        m = self.lab.market
        kids = m[(m.age <= 21)].sort_values("peak_p50", ascending=False).head(15)
        names = ", ".join(f"{n} ({int(a)}, {o:.0f}->{p:.0f})" for n, a, o, p in zip(kids.name, kids.age, kids.overall, kids.peak_p50))
        self.flag(kids.overall.min() >= 74, f"wonderkids top 15: {names}")
        old = m[(m.age >= 33) & (m.position != "GK")]                # keepers age later; outfielders shouldn't climb
        rise = (old.peak_p50 > old.overall + 1.5).mean()
        # Benchmark from real careers (FIFA 15-23): 22 % of 33+ outfielders gained 1.5+ at some point in the next four
        # editions, mostly lower-rated players re-rated in smaller leagues. The median path must stay well under that.
        self.flag(rise < 0.22 / 2, f"outfield 33+: median path rises 1.5+ for {rise:.1%} (real careers: 22 % ever do)")

    # ── tactics ──────────────────────────────────────────────────────────────
    def tactics_(self):
        clubs = ["Manchester City", "Stade Rennais FC", "FC Metz"]
        fits = {}
        for club, style in itertools.product(clubs, tactics.STYLES):
            f = tactics.STYLES[style]["formation"]
            r = self.lab.run(club, f, style, seasons=1)
            s = r["seasons"][0]
            field = [x for x in s["xi"] if x and x["slot"] != "GK"]
            on_pitch = all(3 <= x[k][i] <= 97 for x in field for k in ("ip", "op") for i in (0, 1))
            if len(field) != 10 or not on_pitch:
                self.flag(False, f"{club} {style}: {len(field)} outfielders, on pitch {on_pitch}")
            fits[(club, style)] = s["plan_fit"]
            shapes = sum(int(n) for n in s["shape_in"].split("-")), sum(int(n) for n in s["shape_out"].split("-"))
            if shapes != (10, 10):
                self.flag(False, f"{club} {style}: shapes {s['shape_in']} / {s['shape_out']}")
        for style in tactics.STYLES:
            a, b, c = (fits[(k, style)] for k in clubs)
            self.flag(a >= b - 0.03 and b >= c - 0.05, f"plan fit {style}: City {a:.2f} / Rennes {b:.2f} / Metz {c:.2f}")
        # Every instruction moves its own demand the right way.
        base = dict(tactics.DEFAULT_PLAN)
        d0 = tactics.demands(base, set())
        checks = [("line", 9, "cover", +1), ("line", 2, "cover", -1), ("pressing", "conservative", "pressing", -1),
                  ("defence", "all_out", "box", +1), ("build_up", "long", "build_up", -1), ("attack", "counter", "transition", +1),
                  ("area", "wide", "delivery", +1), ("compact", 10, "engine", +1)]
        for k, v, ind, sign in checks:
            d1 = tactics.demands({**base, k: v}, set())
            self.flag(np.sign(d1[ind] - d0[ind]) == sign, f"{k}={v} moves {ind} demand {d0[ind]} -> {d1[ind]}")
        for adv, ind in [("gegenpress", "pressing"), ("gegenpress", "engine"), ("tiki_taka", "build_up"), ("hug_touchline", "delivery"),
                         ("deep_line", "box"), ("counter_target", "transition")]:
            d1 = tactics.demands(base, {adv})
            self.flag(d1[ind] > d0[ind], f"{adv} raises {ind} demand {d0[ind]} -> {d1[ind]}")

    # ── squad lab ────────────────────────────────────────────────────────────
    def lab_(self):
        r = self.lab.run("Stade Rennais FC", "4-3-3", "gegenpress", seasons=3)
        young = [p for p in r["players"] if p["age"] <= 21 and np.mean(p["minutes"]) >= 0.3]
        old = [p for p in r["players"] if p["age"] >= 31 and np.mean(p["minutes"]) >= 0.3]
        g_young = np.mean([p["ratings"][-1] - p["ratings"][0] for p in young]) if young else float("nan")
        g_old = np.mean([p["ratings"][-1] - p["ratings"][0] for p in old]) if old else float("nan")
        self.flag(g_young > 1 and (np.isnan(g_old) or g_old < 0.5),
                  f"Rennes 3 seasons: youngsters with minutes {g_young:+.1f}, 31+ {g_old:+.1f}, eleven {r['seasons'][0]['team_level']} -> {r['seasons'][-1]['team_level']}")

    def run(self):
        for step in (self.names, self.players, self.calls, self.market, self.tactics_, self.lab_):
            try:
                step()
            except Exception as e:                         # an audit reports, it doesn't stop
                self.problems.append(f"FIX {step.__name__} crashed: {e!r}")
        return self.problems, self.notes


if __name__ == "__main__":
    problems, notes = Audit().run()
    print("\n".join(notes + [""] + problems))
    print(f"\n{len(problems)} to fix, {len(notes)} fine")
