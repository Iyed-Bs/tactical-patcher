// Tactical Patcher, the scout's app. Vanilla JS, one page: search rail,
// player desk, scenario chart, market; the tactics board lives in board.js.
// Every dynamic string goes through esc().

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = (path, opts) => fetch(path, opts).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
const eur = (v) => {
  if (v == null) return "–";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  return a >= 1e6 ? `${s}€${(a / 1e6).toFixed(a >= 1e7 ? 0 : 1)}m` : `${s}€${Math.round(a / 1e3)}k`;
};
const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

// Requests are cached as promises: a player opened twice, or hovered then opened, is fetched once.
const cache = new Map();
const cached = (url) => {
  if (!cache.has(url)) cache.set(url, api(url).catch((e) => { cache.delete(url); throw e; }));
  return cache.get(url);
};
const cardUrl = (id) => `/api/scout/player/${id}`;
const judgeUrl = (id, club) => `/api/scout/player/${id}/judge?our_club=${encodeURIComponent(club)}`;
function prefetch(id) { cached(cardUrl(id)); cached(judgeUrl(id, $("#our-club").value)); }

// The address keeps the mode and the open player, so a refresh lands where you were.
function readHash() { return Object.fromEntries(new URLSearchParams(location.hash.slice(1))); }
function writeHash(patch) {
  const h = { ...readHash(), ...patch };
  for (const k of Object.keys(h)) if (h[k] == null || h[k] === "") delete h[k];
  history.replaceState(null, "", "#" + new URLSearchParams(h).toString());
}
const currentMode = () => document.querySelector(".mode.on")?.dataset.mode || "search";

let CLUBS = [];
let current = null;             // the player desk's data
const ROLE_LABEL = { starter: "starter", rotation: "rotation", bench: "bench", loan_starter: "starter on loan" };

// ── Clubs and "your club" ────────────────────────────────────────────────────
async function loadClubs() {
  CLUBS = await api("/api/scout/clubs");
  const byLeague = {};
  CLUBS.forEach((c) => (byLeague[c.league] ??= []).push(c));
  const opts = Object.entries(byLeague).map(([lg, cs]) => `<optgroup label="${esc(lg)}">${cs.map((c) =>
    `<option value="${esc(c.team)}">${esc(c.team)} (${c.team_overall})</option>`).join("")}</optgroup>`).join("");
  const sel = $("#our-club");
  sel.innerHTML = opts;
  const saved = store.get("ourClub");
  const fallback = CLUBS.find((c) => c.team === "Stade Rennais FC") || CLUBS.find((c) => /Ligue 1/.test(c.league)) || CLUBS[0];
  sel.value = saved && CLUBS.some((c) => c.team === saved) ? saved : fallback.team;
  // A club in the address (from the analysis pages) becomes your club.
  const asked = readHash().club;
  if (asked && CLUBS.some((c) => c.team === asked)) { sel.value = asked; store.set("ourClub", asked); writeHash({ club: null }); }
  sel.addEventListener("change", () => {
    store.set("ourClub", sel.value);
    // The lab plays the new club (its sales belonged to the old squad); the desk re-judges the open player for the new club.
    if (currentMode() === "lab") boardClubChanged();
    else if (readHash().cmp && $("#desk .cmp, #cmp-squad")) openCompare(readHash().cmp.split(",").map(Number).filter(Boolean));
    else if (current && $("#desk .verdict")) openPlayer(current.player.ea_id);
  });
}

// ── Search ───────────────────────────────────────────────────────────────────
let timer;
function search(q) {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const list = $("#results");
    if (q.trim().length < 2) { list.innerHTML = ""; return; }
    const rows = await api(`/api/scout/search?q=${encodeURIComponent(q)}`);
    list.innerHTML = rows.length ? rows.map((r) => `<li data-id="${r.ea_id}" class="${current && current.player.ea_id === r.ea_id ? "on" : ""}">
        <strong>${esc(r.name)}</strong><span class="ovr">${r.overall}</span>
        <small>${r.age} · ${esc(r.position)} · ${esc(r.club_now)}</small></li>`).join("")
      : `<li><small>No player by that name in FC 27.</small></li>`;
  }, 180);
}

// ── Charts ───────────────────────────────────────────────────────────────────
function fanChart(series, seasons) {
  const W = 640, H = 260, L = 38, R = 34, T = 14, B = 30;
  const all = series.flatMap((s) => [...s.p50, ...(s.p10 || []), ...(s.p90 || [])]).filter((v) => v != null);
  const lo = Math.floor(Math.min(...all) - 1), hi = Math.ceil(Math.max(...all) + 1);
  const x = (i) => L + (i / (seasons.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const line = (vals) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const ticks = [];
  for (let v = Math.ceil(lo); v <= hi; v += Math.max(1, Math.round((hi - lo) / 5))) ticks.push(v);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Projected rating by season">`;
  svg += ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  svg += seasons.map((s, i) => `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(s)}</text>`).join("");
  series.forEach((s) => {
    if (s.p10 && s.p90) {
      const band = s.p90.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ") + " " +
        s.p10.map((_, i) => `L${x(s.p10.length - 1 - i)},${y(s.p10[s.p10.length - 1 - i])}`).join(" ") + " Z";
      svg += `<path d="${band}" fill="${s.color}" opacity="0.14"/>`;
    }
  });
  series.forEach((s) => {
    svg += `<path d="${line(s.p50)}" fill="none" stroke="${s.color}" stroke-width="${s.main ? 3 : 2}" ${s.dash ? 'stroke-dasharray="5 5"' : ""}/>`;
    svg += s.p50.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="${s.main ? 3.5 : 2.5}" fill="${s.color}"><title>${esc(s.label)} ${esc(seasons[i])}: ${v}</title></circle>`).join("");
  });
  return svg + "</svg>";
}

function historyChart(hist) {
  if (!hist.length) return `<p class="muted">No rating history before FC 27.</p>`;
  const W = 420, H = 150, L = 30, R = 24, T = 10, B = 26;
  const vals = hist.map((h) => h.overall);
  const lo = Math.min(...vals) - 2, hi = Math.max(...vals) + 2;
  const x = (i) => L + (hist.length === 1 ? 0.5 : i / (hist.length - 1)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="EA rating history">`;
  svg += `<path d="${hist.map((h, i) => `${i ? "L" : "M"}${x(i)},${y(h.overall)}`).join(" ")}" fill="none" stroke="var(--rosewood)" stroke-width="2"/>`;
  svg += hist.map((h, i) => `<circle cx="${x(i)}" cy="${y(h.overall)}" r="3" fill="${i === hist.length - 1 ? "var(--crimson)" : "var(--rosewood)"}"><title>${esc(h.season)}: ${h.overall}${h.club ? " · " + esc(h.club) : ""}</title></circle>`).join("");
  const every = Math.ceil(hist.length / 6);
  svg += hist.map((h, i) => (i % every === 0 || i === hist.length - 1) ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(h.season.slice(2))}</text>` : "").join("");
  svg += `<text x="${L - 6}" y="${y(hi - 2) + 4}" text-anchor="end">${hi - 2}</text><text x="${L - 6}" y="${y(lo + 2) + 4}" text-anchor="end">${lo + 2}</text>`;
  return svg + "</svg>";
}

// ── Desk ─────────────────────────────────────────────────────────────────────
const FACE = [["pace", "pac", "Pace"], ["shooting", "sho", "Shooting"], ["passing", "pas", "Passing"], ["dribbling", "dri", "Dribbling"], ["defending", "def", "Defending"], ["physic", "phy", "Physical"]];
const MENTAL = [["reactions", "Reactions"], ["composure", "Composure"], ["vision", "Vision"], ["positioning", "Positioning"], ["interceptions", "Interceptions"]];
const PES = {
  Attack: ["offensive_awareness", "finishing", "heading", "kicking_power", "place_kicking", "curl"],
  "On the ball": ["ball_control", "dribbling", "tight_possession", "low_pass", "lofted_pass"],
  Physical: ["speed", "acceleration", "balance", "physical_contact", "jump", "stamina"],
  Defence: ["defensive_awareness", "ball_winning", "aggression"],
  Goalkeeping: ["gk_awareness", "gk_catching", "gk_clearing", "gk_reflexes", "gk_reach"],
};
const label = (k) => k.replace(/^gk_/, "GK ").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const bar = (v, max = 99) => `<span class="bar"><span class="${v >= 80 ? "hi" : ""}" style="width:${Math.max(4, Math.min(100, ((v - 30) / (max - 30)) * 100))}%"></span></span>`;

function timingBlock(t) {
  if (!t) return `<p class="muted">Select a club to time the move.</p>`;
  const rows = Object.entries(t.policy);
  return `<div class="policy">${rows.map(([w, s]) => `<div class="pol"><span>${esc(w)}</span><span class="bar"><span class="${w === t.best ? "hi" : ""}" style="width:${Math.max(2, s * 100)}%"></span></span><span class="n">${Math.round(s * 100)}%</span></div>`).join("")}</div>
    <p>Worth about <b>${eur(t.worth_to_us_now)}</b> to ${esc($("#our-club").value)} now, against a fee near <b>${eur(t.price_now)}</b>. Most futures say: <b>${esc(t.best)}</b>.</p>
    <p class="muted">Share of 300 simulated futures in which each window is the best moment to buy. Each future plays his development where he is, prices him with the market model at every window (plus the seller's premium), and weighs what he adds above the selected club's starters and his value as he develops there. Least-squares Monte Carlo, backwards from 2030. Decision support, not an oracle: the euros per rating point are still being calibrated.</p>`;
}

function callClass(c) { return /^Buy below/.test(c) ? "wait" : /^Buy/.test(c) ? "buy" : /Pass/.test(c) ? "pass" : /Wait|Monitor|Squad/.test(c) ? "wait" : ""; }

let openSeq = 0;
async function openPlayer(id) {
  const seq = ++openSeq;
  const desk = $("#desk");
  const ours = $("#our-club").value;
  document.querySelectorAll(".results li").forEach((li) => li.classList.toggle("on", +li.dataset.id === +id));
  writeHash({ p: id });
  const cardP = cached(cardUrl(id)), judgeP = cached(judgeUrl(id, ours));
  const slow = setTimeout(() => { if (seq === openSeq) desk.innerHTML = `<div class="skeleton desk-skel"><div></div><div></div><div></div></div>`; }, 120);
  try {
    const card = await cardP;
    clearTimeout(slow);
    if (seq !== openSeq) return;
    current = card;
    renderDesk(card, ours);
    desk.scrollTop = 0;
    const j = await judgeP;
    if (seq !== openSeq) return;
    const d = { ...card, verdict: j.verdict, timing: j.timing, profile: { ...card.profile, grows_into: j.grows_into } };
    current = d;
    const top = desk.scrollTop;
    renderDesk(d, ours);
    desk.scrollTop = top;
  } catch (e) {
    clearTimeout(slow);
    if (seq === openSeq) desk.innerHTML = `<p class="loading">Couldn't load this player (${esc(e.message)}).</p>`;
  }
}

function renderDesk(d, ours) {
  const desk = $("#desk");
  const p = d.player, v = d.verdict || null;
  const id = p.ea_id;
  const delta = p.overall_fc26 != null ? p.overall - p.overall_fc26 : null;
  const series = v ? [
    { label: `Stays at ${p.club_now}`, p50: v.stay.overall.p50, color: "var(--dim)", dash: true },
    { label: `With ${ours} (${ROLE_LABEL[v.ours.role]})`, p50: v.ours.overall.p50, p10: v.ours.overall.p10, p90: v.ours.overall.p90, color: "var(--crimson)", main: true },
  ] : [];
  if (v && v.loan) series.push({ label: "A season as a starter on loan", p50: v.loan.overall.p50, color: "var(--gold)" });
  const wait = (h) => `<div class="sk-block" style="height:${h}px"></div>`;
  const ml = d.master_league;
  const pr = d.profile;
  desk.innerHTML = `
    <section class="head">
      <div>
        <h1>${esc(p.name)}</h1>
        <div class="facts">
          <span><b>${p.age}</b> in 2026/27</span><span><b>${esc(p.position)}</b>${p.alt_positions ? ` · ${esc(p.alt_positions)}` : ""}</span>
          <span><b>${esc(p.club_now)}</b> <span class="muted">(${esc(p.club_source)})</span></span>
          <span>${esc(p.nationality)}</span><span>Value <b>${eur(p.value_eur)}</b></span>
          <span>2025/26 league minutes <b>${pct(p.minutes_last)}</b></span>
          ${p.fl_playing_style ? `<span>FL26 <b>${esc(p.fl_playing_style)}</b></span>` : ""}
        </div>
        <div class="listbar" id="listbar" data-id="${id}"></div>
      </div>
      <div class="badges">
        <div class="badge"><div class="v">${p.overall}</div><div class="k">FC 27</div><div class="d ${delta > 0 ? "up" : delta < 0 ? "down" : ""}">${delta == null ? "&nbsp;" : (delta > 0 ? "+" : "") + delta + " on FC 26"}</div></div>
        <div class="badge"><div class="v">${v ? Math.round(v.ours.peak_p50) : "··"}</div><div class="k">Projected peak</div><div class="d muted">${v ? `up to ${Math.round(v.ours.peak_p90)}` : "playing it…"}</div></div>
      </div>
    </section>

    ${v ? `<section class="verdict">
      <div><div class="call ${callClass(v.call)}">${esc(v.call)}</div><div class="for">for ${esc(v.our_club)}</div></div>
      <div>${v.reasons.map((r) => `<p>${esc(r)}</p>`).join("")}</div>
    </section>` : `<section class="verdict pending"><div><div class="call">Judging…</div><div class="for">for ${esc(ours)}</div></div>
      <div><p class="muted">Four seasons three ways, his price at every window and the call: about a second the first time.</p></div></section>`}

    <div class="cols">
      <section class="card">
        <h3>How he plays</h3>
        <p class="arch"><b>${esc(pr.archetype.name || "–")}</b> <span class="muted">· family of ${pr.archetype.size} players, e.g. ${esc((pr.archetype.examples || []).slice(0, 3).join(", "))}</span></p>
        <div class="roles">${pr.roles.map((r) => `<div class="role"><span>${esc(r.role)}</span>
          <span class="bar fit"><span style="width:${Math.max(4, Math.round(Math.max(0, r.fit) * 220))}%"></span></span>
          <span class="n">${Math.round(r.level)}</span><span class="muted n">${Math.round(r.percentile)}th</span></div>`).join("")}</div>
        <p class="muted">Ranked on fit and level together. The bar is fit: how closely the shape of his attributes (each one against what his level predicts) matches the role's signature. The number is the level he'd bring in it, then its percentile in his line.</p>
        ${pr.grows_into && pr.grows_into.length ? `<p><b>Growing into:</b> ${pr.grows_into.map((g) => `${esc(g.role)} <span class="muted">(${g.now} → ${g.later})</span>`).join(", ")}</p>` : ""}
      </section>
      <section class="card">
        <h3>When to buy</h3>
        ${v ? timingBlock(d.timing) : wait(180)}
      </section>
    </div>

    <div class="cols">
      <section class="card">
        <h3>Four seasons, three ways</h3>
        ${v ? `<div class="scenario">
          <span class="muted">Try another move:</span>
          <select id="sc-club" aria-label="Club">${CLUBS.map((c) => `<option ${c.team === ours ? "selected" : ""}>${esc(c.team)}</option>`).join("")}</select>
          <select id="sc-role" aria-label="Role"><option value="">role from the depth chart</option><option value="starter">starter</option><option value="rotation">rotation</option><option value="bench">bench</option><option value="loan_starter">starter on loan</option></select>
          <button id="sc-run">Play it</button>
        </div>
        <div id="fan">${fanChart(series, v.ours.seasons)}</div>
        <div class="legend" id="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>
        <p class="muted">Median path with the 10–90% band for the move to ${esc(ours)}. Minutes from the role in that club's FC 27 depth chart (starter 72%, rotation 40%, bench 12%). ${esc(ours)}'s starters in his line: ${Math.round(v.starter_bar)}.</p>` : wait(300)}
      </section>
      <section class="card">
        <h3>His ratings so far</h3>
        ${historyChart(d.history)}
        ${d.youth.length ? `<h3 style="margin-top:14px">The price of waiting</h3><table><thead><tr><th>Season</th><th>Age</th><th>Rating</th><th>Potential</th><th>EA value</th></tr></thead><tbody>
          ${d.youth.map((y) => `<tr><td class="num">${2000 + y.fifa_version - 1}/${String(2000 + y.fifa_version).slice(2)}</td><td class="num">${y.age}</td><td class="num">${y.overall}</td><td class="num">${y.potential}</td><td class="num">${eur(y.value_eur)}</td></tr>`).join("")}
          <tr><td class="num">today</td><td class="num">${p.age}</td><td class="num">${p.overall}</td><td class="num">–</td><td class="num">${eur(p.value_eur)}</td></tr></tbody></table>` : ""}
      </section>
    </div>

    <div class="cols">
      <section class="card">
        <h3>FC 27 profile, now → in two seasons at ${esc(ours)}</h3>
        <div class="attrs">
          <div class="group-title">Face stats</div>
          ${FACE.map(([k, ea, name]) => { const now = p[ea], next = v ? v.ours.face[k]?.[2] : null; return `<div class="attr"><span>${name}</span><span class="n">${now ?? "–"}${next != null ? ` <span class="${next - now >= 0.5 ? "up" : next - now <= -0.5 ? "down" : "muted"}">→${Math.round(next)}</span>` : ""}</span>${bar(now)}</div>`; }).join("")}
          <div class="group-title">Reading the game</div>
          ${MENTAL.map(([k, name]) => `<div class="attr"><span>${name}</span><span class="n">${p[k] ?? "–"}</span>${bar(p[k])}</div>`).join("")}
        </div>
      </section>
      <section class="card">
        <h3>In Master League (Football Life 26)</h3>
        ${ml ? `<p class="muted" style="margin-top:0">${esc(p.fl_registered_position || "")}${p.fl_playing_style ? " · " + esc(p.fl_playing_style) : ""} · form ${p.fl_form ?? "–"}/8 · injury resistance ${p.fl_injury_resistance ?? "–"}/3 · weak foot ${p.fl_weak_foot_usage ?? "–"}/${p.fl_weak_foot_accuracy ?? "–"}</p>
          <div class="attrs ml-attrs">${Object.entries(PES).filter(([g]) => g !== "Goalkeeping" || p.position === "GK").map(([g, ks]) =>
            `<div class="group-title">${g}</div>` + ks.map((k) => `<div class="attr"><span>${label(k)}</span><span class="n">${ml.abilities[k] ?? "–"}</span>${bar(ml.abilities[k])}</div>`).join("")).join("")}</div>
          <p class="muted">Based on the FL engine, measured on saved Master League careers: a player his age moves by ${(ml.ability_mean[1] - ml.ability_mean[0] >= 0 ? "+" : "")}${(ml.ability_mean[1] - ml.ability_mean[0]).toFixed(1)} a season on average: ability mean ${ml.ability_mean.join(" → ")}.</p>`
        : `<p class="muted">Not found in the Football Life 26 database.</p>`}
      </section>
    </div>

    <section class="card" style="margin-bottom:16px">
      <h3>Plays like</h3>
      <div class="cols" style="margin:0">
        <div><div class="group-title">Today</div>${pr.plays_like.today.map((t) => `<div class="like"><div><b>${esc(t.name)}</b> <span class="muted">${t.age} · ${t.overall} · ${esc(t.club_now)}</span></div><span class="n">${Math.round(t.sim * 100)}%</span></div>`).join("")}</div>
        <div><div class="group-title">Elite peaks of the last decade</div>${pr.plays_like.references.map((t) => `<div class="like"><div><b>${esc(t.name)}</b> <span class="muted">${esc(t.season)} · ${t.overall} at ${t.age} · ${esc(t.club)}</span></div><span class="n">${Math.round(t.sim * 100)}%</span></div>`).join("")}</div>
      </div>
      <p class="muted">Similarity of profile shape (attributes relative to level): who plays the way he plays. Today: players at his level or above.</p>
    </section>

    <section class="card">
      <h3>Players who looked like him at ${p.age}, and what they became</h3>
      <div class="comps">${d.comparables.map((c) => `<div class="comp"><strong>${esc(c.name)}</strong><small>${esc(c.season)} · ${esc(c.club)}</small>
        <div class="path">${c.overall} at ${c.age}${c.overall_3y != null ? ` → ${c.overall_3y} three seasons on` : ""}${c.now_fc27 != null ? ` → ${c.now_fc27} today` : ""}</div>
        ${c.club_3y ? `<small>then ${esc(c.club_3y)}</small>` : ""}</div>`).join("") || `<p class="muted">No close match at his age.</p>`}</div>
    </section>`;
  if (v) $("#sc-run").addEventListener("click", () => runScenario(id, series));
  renderListbar(id);
}

async function runScenario(id, base) {
  const club = $("#sc-club").value, role = $("#sc-role").value || null;
  const r = await api("/api/scout/project", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ea_id: id, club, role }) });
  const series = [...base.filter((s) => !s.custom), { label: `${club} (${ROLE_LABEL[r.role]})`, p50: r.overall.p50, p10: r.overall.p10, p90: r.overall.p90, color: "var(--green)", custom: true }];
  series.forEach((s) => { if (!s.custom) { delete s.p10; delete s.p90; } });
  $("#fan").innerHTML = fanChart(series, r.seasons);
  $("#legend").innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("");
}

// ── Market search ────────────────────────────────────────────────────────────
const POS = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "ST"];
const ATTR_LABEL = { pac: "Pace", sho: "Shooting", pas: "Passing", dri: "Dribbling", def: "Defending", phy: "Physical", reactions: "Reactions",
  composure: "Composure", vision: "Vision", positioning: "Attacking runs", interceptions: "Interceptions" };
const PRESETS = {
  wonderkids: { age_max: 21, peak_min: 80, sort: "peak_p50" },
  gems: { age_max: 23, value_max_m: 5, peak_min: 72, sort: "peak_per_million" },
  ready: { age_min: 24, age_max: 29, overall_min: 80, sort: "overall" },
  u19: { age_max: 19, sort: "peak_p90" },
};
let MARKET_OPTS = null;
let marketTotal = 0;

async function loadMarketOptions() {
  if (MARKET_OPTS) return;
  MARKET_OPTS = await api("/api/scout/market/options");
  const f = $("#mode-market");
  $("#f-pos").innerHTML = POS.map((p) => `<label class="pchip"><input type="checkbox" name="pos" value="${p}"><span>${p}</span></label>`).join("");
  const fill = (name, items) => { f.elements[name].insertAdjacentHTML("beforeend", items.map((x) => `<option>${esc(x)}</option>`).join("")); };
  fill("league", MARKET_OPTS.leagues); fill("role", MARKET_OPTS.roles); fill("archetype", MARKET_OPTS.archetypes); fill("fl_style", MARKET_OPTS.fl_styles);
  $("#nations").innerHTML = MARKET_OPTS.nations.map((n) => `<option value="${esc(n)}">`).join("");
  $("#playstyles").innerHTML = MARKET_OPTS.playstyles.map((n) => `<option value="${esc(n)}">`).join("");
  f.elements.role_fit.addEventListener("input", (e) => ($("#fitv").textContent = (+e.target.value).toFixed(2)));
}

function attrRow() {
  const opts = [...MARKET_OPTS.ea_attributes.map((a) => [a, `FC 27 · ${ATTR_LABEL[a] || a}`]),
                ...MARKET_OPTS.fl_abilities.map((a) => [a, `FL26 · ${label(a.replace(/^fl_/, ""))}`])];
  const row = document.createElement("div");
  row.className = "attr-row";
  row.innerHTML = `<select aria-label="Attribute">${opts.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join("")}</select>
    <input type="number" min="1" max="99" value="75" aria-label="Minimum"><button type="button" class="ghost x" aria-label="Remove">×</button>`;
  row.querySelector(".x").addEventListener("click", () => row.remove());
  $("#f-attrs").appendChild(row);
}

function marketQuery(offset = 0) {
  const f = $("#mode-market"), q = new URLSearchParams();
  const pos = [...f.querySelectorAll("input[name=pos]:checked")].map((i) => i.value);
  if (pos.length) q.set("position", pos.join(","));
  for (const n of ["age_min", "age_max", "overall_min", "overall_max", "peak_min", "peak_high_min", "room_min", "height_min", "weak_foot_min",
                   "league", "nation", "club", "role", "archetype", "fl_style", "playstyle", "foot", "sort", "dir"]) {
    const v = f.elements[n]?.value;
    if (v) q.set(n, v);
  }
  if (f.elements.role.value) q.set("role_fit", f.elements.role_fit.value);
  if (f.elements.value_min_m.value) q.set("value_min", f.elements.value_min_m.value * 1e6);
  if (f.elements.value_max_m.value) q.set("value_max", f.elements.value_max_m.value * 1e6);
  if (f.elements.minutes_min_pct.value) q.set("minutes_min", f.elements.minutes_min_pct.value / 100);
  if (f.elements.minutes_max_pct.value) q.set("minutes_max", f.elements.minutes_max_pct.value / 100);
  if (f.elements.moved.checked) q.set("moved", "true");
  const attrs = [...$("#f-attrs").querySelectorAll(".attr-row")].map((r) => `${r.querySelector("select").value}:${r.querySelector("input").value}`);
  if (attrs.length) q.set("attrs", attrs.join(","));
  q.set("offset", offset); q.set("limit", 100);
  return q;
}

async function runMarket(offset = 0) {
  const desk = $("#desk");
  if (!offset) desk.innerHTML = `<p class="loading">Searching the market…</p>`;
  const role = $("#mode-market").elements.role.value;
  const res = await api(`/api/scout/market?${marketQuery(offset)}`);
  marketTotal = res.total;
  const rows = res.rows.map((r) => `<tr class="row" data-id="${r.ea_id}">
      <td class="who"><strong>${esc(r.name)}</strong><small>${esc(r.position)} · ${esc(r.club_now)}${r.moved_this_summer ? " · moved" : ""}</small></td>
      <td class="num">${r.age}</td><td class="num">${r.overall}</td>
      <td class="num"><b>${Math.round(r.peak_p50)}</b> <span class="muted">→ ${Math.round(r.peak_p90)}</span></td>
      <td class="num">${r.growth_room > 0 ? "+" : ""}${r.growth_room}</td>
      <td class="num">${eur(r.value_eur)}</td><td class="num">${pct(r.minutes_last)}</td>
      ${role ? `<td class="num">${Math.round(r["level_" + role] ?? 0)} <span class="muted">${(r["fit_" + role] ?? 0).toFixed(2)}</span></td>` : ""}
      <td>${esc(r.best_role || "–")}<small class="muted block">${esc(r.archetype || "")}</small></td></tr>`).join("");
  const table = `<table class="market"><thead><tr><th>Player</th><th>Age</th><th>Now</th><th>Peak</th><th>Room</th><th>Value</th><th>Mins</th>
      ${role ? `<th>${esc(role)}</th>` : ""}<th>Best role · archetype</th></tr></thead><tbody id="mk-rows">${rows}</tbody></table>`;
  if (!offset) {
    desk.innerHTML = `<section class="head"><div><h1>Market</h1><div class="facts"><span><b>${res.total.toLocaleString()}</b> players match</span>
      <span class="muted">Peak = likely best rating in the next four seasons where he is (and the high road), from the calibrated simulator.</span></div></div></section>
      <section class="card">${table}${res.total > 100 ? `<button id="mk-more" class="ghost">Show more</button>` : ""}</section>`;
  } else {
    $("#mk-rows").insertAdjacentHTML("beforeend", rows);
  }
  const more = $("#mk-more");
  if (more) { more.onclick = () => runMarket(offset + 100); if (offset + 100 >= marketTotal) more.remove(); }
}

// ── Lists: the shortlist and the players under the radar ─────────────────────
// Kept by the server (PATCHER_DATA/scout/lists.json). A player sits in one list at a time; ticks choose who to compare.
let LISTS = null;
const ticked = new Set();
const LIST_NAMES = ["shortlist", "radar"];

async function loadLists(force = false) {
  if (!LISTS || force) LISTS = await api("/api/scout/lists");
  renderLists();
  return LISTS;
}

function listOf(id) {
  if (!LISTS) return null;
  return LIST_NAMES.find((k) => LISTS[k].some((x) => x.ea_id === id)) || null;
}

async function listCall(method, name, id, note = "") {
  LISTS = await api(`/api/scout/lists/${name}/${id}`, method === "PUT"
    ? { method, headers: { "content-type": "application/json" }, body: JSON.stringify({ note }) } : { method });
  renderLists();
  const bar = $("#listbar");
  if (bar && +bar.dataset.id === id) renderListbar(id);
}

function renderLists() {
  if (!LISTS) return;
  for (const k of LIST_NAMES) {
    const rows = LISTS[k];
    const other = k === "shortlist" ? "radar" : "shortlist";
    document.querySelector(`[data-count=${k}]`).textContent = rows.length || "";
    $(`#list-${k}`).innerHTML = rows.length ? rows.map((r) => `<li data-id="${r.ea_id}" class="${current && current.player.ea_id === r.ea_id ? "on" : ""}">
        <input type="checkbox" class="tick" aria-label="Compare ${esc(r.name)}" ${ticked.has(r.ea_id) ? "checked" : ""}>
        <div class="who"><strong>${esc(r.name)}</strong><small>${r.age} · ${esc(r.position)} · ${esc(r.club_now)} · ${eur(r.value_eur)}</small></div>
        <span class="lvl">${r.overall} <span class="muted">→ ${r.peak_p50 != null ? Math.round(r.peak_p50) : "–"}</span></span>
        <span class="acts"><button type="button" class="ghost mv" data-to="${other}" title="Move to ${esc(LISTS.labels[other])}" aria-label="Move to ${esc(LISTS.labels[other])}">⇄</button>
          <button type="button" class="ghost rm" title="Remove" aria-label="Remove ${esc(r.name)}">×</button></span>
        ${r.note ? `<span class="note">${esc(r.note)}</span>` : ""}</li>`).join("")
      : `<li class="empty-row"><small class="muted">${k === "shortlist" ? "Open a player and add him from his desk." : "Nobody yet. The market's hidden gems are a good place to start."}</small></li>`;
  }
  const n = ticked.size;
  $("#cmp-go").disabled = n < 1 || n > 4;
  $("#cmp-go").textContent = n > 1 ? `Compare ${n}` : n === 1 ? "Compare with the squad" : "Compare";
}

function renderListbar(id) {
  const bar = $("#listbar");
  if (!bar) return;
  if (!LISTS) { loadLists().then(() => renderListbar(id)); return; }
  const inList = listOf(id);
  const entry = inList ? LISTS[inList].find((x) => x.ea_id === id) : null;
  bar.innerHTML = `${LIST_NAMES.map((k) => `<button type="button" class="ghost ${inList === k ? "on" : ""}" data-list="${k}" aria-pressed="${inList === k}">
      ${inList === k ? "✓ " : "+ "}${esc(LISTS.labels[k])}</button>`).join("")}
    <button type="button" class="ghost" data-cmp="${id}">Compare with the squad</button>
    ${inList ? `<textarea id="list-note" rows="1" maxlength="500" placeholder="Notes: what was seen, what to check…" aria-label="Note">${esc(entry?.note || "")}</textarea>` : ""}`;
  bar.querySelectorAll("[data-list]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.list;
    if (inList === k) listCall("DELETE", k, id); else listCall("PUT", k, id, entry?.note || "");
  }));
  bar.querySelector("[data-cmp]").addEventListener("click", () => openCompare([id]));
  const note = $("#list-note");
  if (note) note.addEventListener("change", () => listCall("PUT", inList, id, note.value.trim()));
}

// ── Compare ──────────────────────────────────────────────────────────────────
// Two to four players side by side, every one projected at your club, so the numbers compare like for like.
const CMP_COLORS = ["var(--crimson)", "var(--gold)", "var(--green)", "var(--rosewood)"];
const ROLE_SHORT = { starter: "starter", rotation: "rotation", bench: "bench", loan_starter: "starter on loan" };

async function openCompare(ids) {
  ids = [...new Set(ids)].slice(0, 4);
  writeHash({ cmp: ids.join(","), p: null });
  const desk = $("#desk"), ours = $("#our-club").value;
  const squad = await cached(`/api/scout/squad?club=${encodeURIComponent(ours)}`);
  const picker = `<label class="muted">Add from ${esc(ours)}: <select id="cmp-squad" aria-label="Add a player from the squad">
      <option value="">a player from the squad…</option>${["GK", "DEF", "MID", "ATT"].map((ln) => `<optgroup label="${ln}">${squad.filter((s) => s.line === ln)
        .map((s) => `<option value="${s.ea_id}" ${ids.includes(s.ea_id) ? "disabled" : ""}>${esc(s.name)} · ${esc(s.position)} · ${s.overall}</option>`).join("")}</optgroup>`).join("")}
    </select></label>`;
  if (ids.length < 2) {
    const one = ids[0] ? (await cached(cardUrl(ids[0]))).player : null;
    desk.innerHTML = `<section class="head"><div><h1>Compare</h1><div class="facts"><span>${one ? `<b>${esc(one.name)}</b> against a player from <b>${esc(ours)}</b>` : "Tick players in the lists"}</span></div></div></section>
      <section class="card"><div class="cmp-tools">${picker}</div><p class="muted">Pick the squad player he would compete with: both get played forward at ${esc(ours)}.</p></section>`;
    wirePicker(ids);
    return;
  }
  desk.innerHTML = `<p class="loading">Playing ${ids.length} players forward at ${esc(ours)}…</p>`;
  let res;
  try { res = await api(`/api/scout/compare?ids=${ids.join(",")}&our_club=${encodeURIComponent(ours)}`); }
  catch (e) { desk.innerHTML = `<p class="loading">Couldn't compare these players (${esc(e.message)}).</p>`; return; }
  const P = res.players;
  // One row of the table; `best` marks the highest value when the players differ.
  const row = (k, vals, fmt = (v) => v ?? "–", best = false) => {
    const nums = vals.map((v) => (typeof v === "number" ? v : null));
    const hi = best && nums.every((v) => v != null) && new Set(nums).size > 1 ? Math.max(...nums) : null;
    return `<tr><td class="k">${k}</td>${vals.map((v) => `<td class="${typeof v === "number" ? "n" : ""} ${hi != null && v === hi ? "best" : ""}">${fmt(v)}</td>`).join("")}</tr>`;
  };
  const group = (t) => `<tr class="group"><td colspan="${P.length + 1}">${t}</td></tr>`;
  const r1 = (v) => (v == null ? "–" : Math.round(v));
  // Medians only on the chart (overlapping bands turn to mud); each player's range is a row of the table.
  const series = P.map((p, i) => ({ label: p.name, p50: p.with_you.overall.p50, color: CMP_COLORS[i], main: i === 0 }));
  desk.innerHTML = `
    <section class="head"><div><h1>Compare</h1><div class="facts"><span>All played forward at <b>${esc(ours)}</b>, in the role its depth chart gives them</span></div></div></section>
    <section class="card" style="margin-bottom:16px">
      <div class="cmp-tools">${P.length < 4 ? picker : ""}</div>
      <div id="cmp-fan">${fanChart(series, P[0].with_you.seasons)}</div>
      <div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.label)}</span>`).join("")}</div>
      <p class="muted">Median path for each, from the same calibrated simulator as the player desk; the 10–90% range is in the table.</p>
    </section>
    <section class="card">
      <table class="cmp"><thead><tr><th class="k"></th>${P.map((p, i) => `<th><span class="swatch" style="background:${CMP_COLORS[i]}"></span>${esc(p.name)}
          ${P.length > 2 ? `<button type="button" class="ghost x" data-drop="${p.ea_id}" aria-label="Remove ${esc(p.name)}">×</button>` : ""}
          <small>${esc(p.position)} · ${esc(p.club_now)}${p.in_squad ? `<span class="tag-squad">current squad</span>` : ""}</small></th>`).join("")}</tr></thead>
        <tbody>
          ${group("Who")}
          ${row("Age", P.map((p) => p.age))}
          ${row("Nationality", P.map((p) => p.nationality))}
          ${row("Value", P.map((p) => p.value_eur), eur)}
          ${row("2025/26 league minutes", P.map((p) => p.minutes_last), pct)}
          ${group(`With ${esc(ours)}`)}
          ${row("Place in the depth chart", P.map((p) => ROLE_SHORT[p.with_you.role] || p.with_you.role))}
          ${row("FC 27 now", P.map((p) => p.overall), r1, true)}
          ${row("In two seasons (likely)", P.map((p) => p.with_you.overall.p50[2]), r1, true)}
          ${row("Peak (likely)", P.map((p) => p.with_you.peak_p50), r1, true)}
          ${row("Peak (high road)", P.map((p) => p.with_you.peak_p90), r1, true)}
          ${row("In four seasons (10–90%)", P.map((p) => { const o = p.with_you.overall, n = o.p50.length - 1; return `${r1(o.p10[n])}–${r1(o.p90[n])}`; }))}
          ${row("Growth room", P.map((p) => p.growth_room), (v) => (v == null ? "–" : (v > 0 ? "+" : "") + v), true)}
          ${group("How he plays")}
          ${row("Archetype", P.map((p) => p.archetype))}
          ${row("Best roles", P.map((p) => p.roles.map((r) => `${esc(r.role)} <span class="muted">${Math.round(r.level)}</span>`).join("<br>") || "–"))}
          ${row("FL26 playing style", P.map((p) => p.fl_playing_style))}
          ${group("Face stats")}
          ${FACE.map(([k, , name]) => row(name, P.map((p) => p.face[k === "physic" ? "physical" : k]), (v) => v ?? "–", true)).join("")}
          ${group("Reading the game")}
          ${MENTAL.map(([k, name]) => row(name, P.map((p) => p.mental[k]), (v) => v ?? "–", true)).join("")}
        </tbody></table>
      <p class="muted">Highlighted: the best of the group on that line. Ratings are EA FC 27's; projections are the Patcher's.</p>
    </section>`;
  desk.querySelectorAll("[data-drop]").forEach((b) => b.addEventListener("click", () => openCompare(ids.filter((x) => x !== +b.dataset.drop))));
  wirePicker(ids);
  desk.scrollTop = 0;
}

function wirePicker(ids) {
  const sel = $("#cmp-squad");
  if (sel) sel.addEventListener("change", () => { if (sel.value) openCompare([...ids, +sel.value]); });
}

// ── Wiring ───────────────────────────────────────────────────────────────────
$("#q").addEventListener("input", (e) => search(e.target.value));
$("#results").addEventListener("click", (e) => { const li = e.target.closest("li[data-id]"); if (li) openPlayer(+li.dataset.id); });
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip[data-q]"); if (chip) { $("#q").value = chip.dataset.q; search(chip.dataset.q); }
  const row = e.target.closest("tr.row[data-id]"); if (row) openPlayer(+row.dataset.id);
});
async function setMode(m) {
  document.querySelectorAll(".mode").forEach((x) => x.classList.toggle("on", x.dataset.mode === m));
  for (const k of ["search", "market", "lab", "lists"]) $(`#mode-${k}`).hidden = k !== m;
  writeHash({ mode: m === "search" ? null : m, p: m === "search" ? readHash().p : null, cmp: m === "lists" ? readHash().cmp : null });
  if (m === "market") { await loadMarketOptions(); if (!$("#desk table.market")) runMarket(); }
  if (m === "lab") await boardEnter();
  if (m === "lists") await loadLists();
  if (m === "search" && current) openPlayer(current.player.ea_id);
}
// Lists: open a player from his row, tick him for a comparison, move him across, or remove him.
for (const k of LIST_NAMES) {
  $(`#list-${k}`).addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (!li) return;
    const id = +li.dataset.id;
    if (e.target.matches(".tick")) { e.target.checked ? ticked.add(id) : ticked.delete(id); renderLists(); return; }
    if (e.target.closest(".mv")) { listCall("PUT", e.target.closest(".mv").dataset.to, id, LISTS[k].find((x) => x.ea_id === id)?.note || ""); return; }
    if (e.target.closest(".rm")) { ticked.delete(id); listCall("DELETE", k, id); return; }
    openPlayer(id);
  });
}
$("#cmp-go").addEventListener("click", () => openCompare([...ticked]));
$("#cmp-clear").addEventListener("click", () => { ticked.clear(); renderLists(); });
$("#radar-find").addEventListener("click", async () => {
  await setMode("market");
  document.querySelector('[data-preset="gems"]').click();
});
document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
// Hovering a result for a moment starts loading it, so the click lands on a ready desk.
let hoverTimer;
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest("#results li[data-id], tr.row[data-id]");
  clearTimeout(hoverTimer);
  if (el) hoverTimer = setTimeout(() => prefetch(+el.dataset.id), 220);
});
$("#add-attr").addEventListener("click", () => attrRow());
$("#mode-market").addEventListener("submit", (e) => { e.preventDefault(); runMarket(); });
$("#mode-market").addEventListener("reset", () => setTimeout(() => { $("#f-attrs").innerHTML = ""; $("#fitv").textContent = "0.10"; }, 0));
document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
  const f = $("#mode-market"); f.reset(); $("#f-attrs").innerHTML = "";
  for (const [k, v] of Object.entries(PRESETS[b.dataset.preset])) if (f.elements[k]) f.elements[k].value = v;
  runMarket();
}));
loadClubs().then(async () => {
  const h = readHash();
  if (h.mode === "market" || h.mode === "lab" || h.mode === "lists") await setMode(h.mode);
  if (h.mode === "lists" && h.cmp) openCompare(h.cmp.split(",").map(Number).filter(Boolean));
  if (h.p && (!h.mode || h.mode === "search")) openPlayer(+h.p);
});
