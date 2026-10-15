# Tactical Patcher: devlog

## 0 · The game behind the game

Master League first, then Football Manager. Hundreds of seasons, played
with one eye on the pitch and the other on the mechanics.

- Football Manager: hidden current and potential ability (0–200), growth
  driven by the gap between them, minutes, facilities, coaching and
  personality (professionalism, ambition, pressure); mentoring moves
  personality, injury proneness never moves. A match engine where every
  player decides four times a second from his attributes.
- PES Master League and its mods (Smoke Patch, Ultimate Master League):
  growth types (early, standard, late), development tied to playing time,
  real contracts, loans with obligations, market values re-based to real
  ones.
- One gap in both: potential is a number decided once. Real careers branch.

Everything below started from that gap. Each part is a question the game
couldn't answer, the data I went looking for, how I checked that it made
the answers better and not just different, and what came out.

## 1 · A first engine (summer 2026)

World Cup summer. The one season out of four where football doesn't stop
in summer: you still watch players and talents perform and pop off while
hearing all about their transfer rumours. For me, another question rose:
could a Master League season grow players the way real seasons do?

**Growth that answers to playing time.** Master League ties growth to a
player's type and age; minutes only nudge it. I wanted the season itself
to decide. The data: 67,179 season-to-season rating changes from FIFA
15–23, each joined to the minutes the player really got that season
(Transfermarkt). The model: quantile gradient boosting that predicts the
10th, 50th and 90th percentile of the change, so each season is drawn from
a range instead of set to one number. It had to beat the simplest honest
guess, the average change for a player's age, on seasons it never saw. It
did: median error 1.49 rating points against 1.79, and the 10–90 band
holds 78 % of real changes.

**Does it replay the past?** A save starts from a real club's FIFA 23
squad. The manager hands out minutes by rating alone; the engine plays
three seasons, 120 times, and each player's minutes are set against what
really happened in 2022/23–2024/25. Most of a squad lands close. The
youngest players don't, and that was the interesting part:

- Maghnes Akliouche: 2 % of Monaco's minutes in the engine, 48 % in reality.
- Bradley Barcola: 10 % against 56 % (Lyon, then PSG).
- Xavi Simons: 18 % against 83 % (PSV, then Leipzig).
- Lucas Chevalier, Lille's keeper at 21: 21 % against 94 %.

Each time, someone at the club trusted what the ratings couldn't show yet.
The engine had no way to see it, so the next question was what it could
see.

## 2 · What might predict a career?

Master League's youth system never asks this. I asked it of real careers,
knowing public data could only take me part of the way.

**The sample.** 7,507 player states: everyone born 1995–99, seen at 18,
19, 20 and 21, with the outcome read at 23 to 26.5. To keep myself honest,
every model was trained on four birth years and tested on the fifth, the
one it never saw. One pooled, age-aware model rather than one per age,
because the younger samples are thin.

**What came out, in this sample:**
- Minutes weighted by league level carried the most signal of any single
  variable; raw minutes added nothing once level was in. Minutes at 20
  counted more than at 19, and at 19 more than before.
- At 19, the attributes that read the game (reactions, composure)
  separated careers better than pace. A hint worth following, not a law.
- Objective variables alone matched the market's own valuation; together
  they reached an AUC of 0.83 for becoming a top-flight regular at 21, and
  0.88 for the big five.
- Then it plateaued. That plateau may be the most useful result: what is
  left lives in what only clubs hold (training, physical tests, the
  coach's eye).

**A range for each player.** One probability hides how wide a future is,
so each player also gets the careers of the 25 most similar players at the
same age and position, the way baseball's PECOTA does it. Real outcomes
fell inside that band 85–87 % of the time.

### Who opens the door?
The first engine missed trust, so I measured it. A manager's youth trust
is his share of minutes to under-21s; his graduation rate is the share of
those youngsters still regulars at 23–26. Across managers the two run in
opposite directions (correlation −0.42): some give many youngsters a
chance and keep few, some pick few and keep them, a handful do both.

A club's minutes can also reveal its manager. The engine replays the
club's seasons at every youth-trust setting and keeps the one that matches
what its under-21s really got, benched seasons counted as zero. Ligue 1,
2022/23–2024/25: Strasbourg 12, Montpellier 10, Lyon 6, Lille and Nantes 4,
Rennes and Monaco 2, PSG and Marseille 0. At Reims, Toulouse, Lorient and
Nice no single setting fits, which says something too: they chose among
their youngsters rather than for them.

### Does the ball say more than the minutes?
If minutes at level carry the signal, maybe what a player does with the
ball carries more. Wyscout's public events for 2017/18 in the big five
(1,941 matches, 2.46 M actions), each action valued with VAEP: how much it
moves the chances of scoring and conceding. The valuation behaves (Messi
first at 1.04 per 90, Neymar and Salah next). But for 181 youngsters
already playing big-five minutes, one season of action value added nothing
to minutes and level (AUC 0.62 either way): the big five had already
chosen. On-ball value belongs earlier, at academy, second-division and
loan level, over several seasons. A lesson I kept for later.

## 3 · The scout's desk

What a scout actually asks: what could this kid reach in three years,
with us, and is now the right moment to bid for him?

### Fresh enough to answer
The first engine ran on FIFA 23; a scout lives in the current window. The
desk needed today's players, clubs and ratings:
- EA FC 27 (19,789 players, September 2026) and FC 26, plus every weekly
  update of FIFA 15–23 (10.0 M rows: potential, value, wage, work rates,
  traits);
- Football Life 26's database, understood field by field: 29,413 players,
  25 PES abilities packed as 6-bit values, form, injury resistance, weak
  foot, playing style, eight snapshots from October 2025 to May 2026;
- the summer 2026 window from Transfermarkt: 29,845 moves across 64
  leagues.

Five sources name the same player five ways, and joining them on names
alone would mix people up. So each player gets one key through
probabilistic record linkage (Fellegi–Sunter): weights for name, birth
date or year, height, foot, nation, club and line, fitted by EM, plus one
more test, whether his Football Life profile predicts his EA rating.
Transfermarkt's exact birth dates chain the rest. 82,933 people, expected
false matches 0.1–0.2 %.

### The growth engine
With fresher data the engine could see more: the rating trajectory over
several seasons, EA's depth-chart marker, the level of the team around
him, his real minutes (missing minutes left missing, never guessed). Each
addition stayed only if it lowered the error on a season the engine never
saw. Average error 1.42, and 1.77 for under-21s, against 1.60 and 2.06 for
the age average. EA's own potential added nothing once trajectory,
composure, minutes and context were in, so the core model leaves it out.
Jérémy Doku from FIFA 23: 79.0 by 2025 (real 80), 80.6 by 2026 (real 84).

Then a model lab put other learners on the same unseen season: CatBoost,
an MLP ensemble, a GRU reading rating sequences, all scored on pinball
loss, blend weights chosen on a separate validation season so the test
stayed clean. The MLP–LightGBM blend edges LightGBM alone by 0.9 %.

### What Master League does to a player
To patch Master League I first had to measure it. Saved careers, read
without changing a byte, set against the Football Life database: players
gain about 1.2 a season at 16–20, 0.6 at 23–24, nothing at 25–26, then
lose 0.5 at 27–28 and 1.2 past 31. Almost every 16–20-year-old gains five
or more in two seasons. Real careers split: some explode (+10 to +12),
many stall, and real ratings barely move before 30. The desk shows both
curves side by side.

### How an analyst sees a player
A rating says how good a player is. It doesn't say how he plays. 28 roles
written as attribute signatures (regista, mezzala, inverted full-back,
pressing forward, channel runner...), and fit read on the shape of his
attributes, each one against what his level predicts, so a star doesn't
fit every role just by being a star. Archetypes come from the data
(Gaussian mixtures per line, their number chosen by BIC): Rodri, Caicedo
and Tchouaméni land together; Gyökeres, Thuram and Osimhen together.

Plays like compares shapes with today's players at his level or above and
with the decade's elite peaks. Kees Smit sits with Jorginho's 2021/22,
Kovačić and Pirlo; Lennart Karl with Insigne, Mahrez and Brahimi;
Konstantinos Karetsas next to Maghnes Akliouche today.

### Four seasons, three ways
Four seasons ahead, errors compound, so the simulator had to earn its
range. It learns minutes from real careers and club moves from team
levels, draws with heavy tails (some careers do explode), and sets its
spread per talent tier on one time machine: FIFA 18 to 22. Then it is
judged on another it never saw: start from FIFA 19 with only what was
known then, play four seasons, compare with FIFA 23. 80, 78, 76 and 76 %
of real ratings land inside the band, from the under-65s to the 78+. A
simulator that keeps each player in his starting role manages 31 to 40 %.

Each player is played three ways: he stays, he joins your club in the role
its depth chart gives him, or he spends a season starting on loan at a
club of his own level. The loan sits at his own level because the data
made one thing plain: a rating is partly the club's. Kees Smit, starting
at AZ, moves from 75.0 to 75.6 in four seasons on EA's scale; on Bayern's
bench he reaches 81.2, as a Bayern starter 84.5. Likely peak and high
road are read from the median and 90th-percentile paths.

### When to buy
The other half of a scout's question is when. Buying is treated as an
option you can exercise in any window: least-squares Monte Carlo
(Longstaff–Schwartz) over 300 futures, each priced by a market-value model
(R² 0.77 in log on seasons it never saw). The call weighs what he'd be
worth to you against the fee: buy, buy below a price, wait, or pass.

### Lists and comparison
A desk needs a memory. A shortlist, and a list for the players few are
watching yet, each with notes. Then the question a scout brings to a
meeting: two to four names side by side, every one played forward at your
club in the role your depth chart gives him, including a player already in
your squad.

## 4 · The market

Master League's transfer search filters on age, position and rating. This
one keeps those and adds what a scout asks next: peak on the likely and the
high road, growth room, peak per million, level in a chosen role and how
well his attributes fit it, archetype, Football Life playing style,
PlayStyles, any attribute minimum.

### Where the stars come from
Before trusting a market, I wanted to know where its talent is made. 91
youth tournaments from Wikipedia (20,106 players with birth dates and
clubs), followed into their senior careers. UEFA cohorts up to 2017
becoming big-five regulars: France 29 %, Spain 27 %, Italy 21 %, England
16 %, Germany 14 %, Netherlands 9 % (where 45 % become regulars, mostly at
home). U19 internationals convert about two and a half times better than
U17s.

## 5 · The tactics board

Master League lets you set a plan and watch. I wanted the plan to ask
something of the players, and to say what. A game plan written the way PES
writes one: attacking and defensive style, build-up, attacking area,
containment, pressuring, defensive line, compactness, support range, two
advanced instructions per phase. Or one of eight coach styles: positional
play, gegenpress, build-up bait, wing-back possession, man-to-man, low
block, wing play, direct.

### Shapes
Each role has a movement with the ball and a place without it, and the
shapes come out of that: positional play's 4-3-3 attacks as a 2-3-5 and
defends as a 4-3-3 with a 39 m block; the low block stays a 4-4-2, 32 m
deep.

### What the plan asks
Nine demands: build-up under pressure, chance creation, width and
delivery, finishing, transition speed, pressing power, cover in behind,
box defending, engine. Each is a weighted mix of attributes in the slots
that carry it. The hard part was the scale: how much press resistance is
enough? The big five's first elevens answered it. A balanced plan asks
what the median eleven has, an extreme one what only the top tenth have,
and every instruction says how far it pushes.

Brighton's gegenpress leaves the cover in behind 14 points short, Lewis
Dunk the slowest of the back line. Leverkusen fit a low block at 95 %.
Feyenoord come up short on finishing and transition speed whatever the
style.

### How to make it work
Then the board suggests fixes, computed rather than written: one-setting
changes judged on the whole plan with their side effects, the squad player
who would lift a demand most in one slot, and market targets who would
really come (clubs at your level or below, within your means). Brighton's
gegenpress: drop the line from 8 to 5 and plan fit goes from 80 % to 84 %.
One click applies it.

The plan's load caps the minutes a starter can carry; the rest go to the
next man in his line, and minutes decide growth. That is the only way
tactics touch development here. These are rules written from football
knowledge, and the app labels them as rules.

### The squad, played forward
Each season the coach picks his eleven from the grown squad. Brighton's
pressing 4-3-3 with high youth trust: Charalampos Kostoulas (19) starts as
the pressing forward and grows from 73 to 79 in three seasons. The same
setting at Lille gives Loun Srdanovic and Isaac Cossier, both 20, about
half the league minutes; they grow from 65 to 75 and from 66 to 74.

## 6 · The analysis pages

The same data, read as a scout's reference after the summer window.

- Promises: every under-21 with senior minutes, scored on the chance of
  becoming a regular, at his club after the window.
- Clubs: FC 27 level, youth minutes under each coach since 2012, the
  squad's young players and their peaks, the window both ways, a door to
  the tactics board.
- Managers: every coach with 30+ league games since 2021/22 (447, 146 in
  charge at the end of 2025/26), with the youngsters he relied on. Flick:
  Cubarsí, Yamal, Gavi. Luis Enrique: Zaïre-Emery, Doué, Mayulu.

## 7 · Does it behave like football?

Numbers can be right on average and still wrong in ways any football
person would spot at once. So the whole app is played on cases a football
person judges at a glance: prospects grow (Ngumoha 75 → 82, a wide range),
stars plateau (Yamal 90 → 92), veterans decline (Lewandowski 84 → 76);
every coach style ranks City above Rennes above Metz; every instruction
moves its own demand the right way. 41 checks, one command.

- Veterans are held to real careers: 22 % of 33+ outfielders gain 1.5 or
  more at some point in four editions; the simulator's median path does it
  for 5 %.
- Real names throughout: EA's Lombardia FC is Inter, Milano FC is AC
  Milan, Bergamo Calcio is Atalanta; 200 names in all, from Transfermarkt.
- A title screen in the spirit of a PES menu: three featured players
  standing on their cards, the menu, the window's biggest fees on a ticker.

## 8 · Next

The Patcher reads players through ratings, because that is what a
Master League save has. A club has more: what a player does on match day
and on a Tuesday morning in training. That is a different project,
Promesse.
