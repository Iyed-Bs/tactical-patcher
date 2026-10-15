// Tactical Patcher, analysis pages: promises, clubs, managers, on the ball, the
// first Patcher. Hash-routed, plain JS, no build step. Clubs are read as of the
// summer 2026 window, the same way the scout desk reads them.
const view = document.getElementById("view");
const api = (path, opts) => fetch(path, opts).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
const pct = (p) => (p == null ? "–" : `${Math.round(p * 100)}%`);
const eur = (v) => (v == null ? "–" : v >= 1e6 ? `€${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}m` : `€${Math.round(v / 1e3)}k`);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const POS = { "": "All positions", Goalkeeper: "Goalkeepers", Defender: "Defenders", Midfield: "Midfielders", Attack: "Forwards" };
let LEAGUES = {};

const moveTag = (from, fee, loan) => (from ? `<span class="tag move" title="Moved this summer">from ${esc(from)}${loan ? " · loan" : fee ? " · " + esc(fee) : ""}</span>` : "");
const deskLink = (eaId, label = "Scout desk ↗") => (eaId ? `<a href="/scout#p=${eaId}">${label}</a>` : "");

function probBar(p) {
  return `<div class="prob"><span class="num">${pct(p)}</span><div class="bar"><span style="width:${Math.round((p || 0) * 100)}%"></span></div></div>`;
}

// ── Promises ───────────────────────────────────────────────────────────────
async function promises(params) {
  const league = params.get("league") ?? "FR1", position = params.get("position") ?? "", q = params.get("q") ?? "";
  view.innerHTML = `
    <h1>Every promise, measured</h1>
    <p class="lede">1,551 players under 21 with senior minutes, scored on their careers to the end of 2025/26, at their club after the summer 2026 window. For each one: the chance of becoming a regular in a top flight by 23–26, what the data sees against what the market prices, and the careers of the players most like him at the same age.</p>
    <div class="controls">
      <select id="league">${Object.entries({ "": "All leagues", ...LEAGUES }).map(([k, v]) => `<option value="${k}" ${k === league ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="position">${Object.entries(POS).map(([k, v]) => `<option value="${k}" ${k === position ? "selected" : ""}>${v}</option>`).join("")}</select>
      <input id="q" placeholder="Search a name" value="${esc(q)}">
    </div>
    <div class="card"><table><thead><tr><th>Player</th><th>Age</th><th>Minutes</th><th>Value</th><th>Becomes a regular</th><th>Data vs market</th><th>Peak value range</th></tr></thead><tbody id="rows"><tr><td colspan="7" class="loading">Loading…</td></tr></tbody></table></div>`;
  const go = () => {
    const p = new URLSearchParams({ league: document.getElementById("league").value, position: document.getElementById("position").value, q: document.getElementById("q").value });
    location.hash = `#/?${p}`;
  };
  ["league", "position"].forEach((id) => document.getElementById(id).addEventListener("change", go));
  document.getElementById("q").addEventListener("keydown", (e) => e.key === "Enter" && go());
  const rows = await api(`/api/promises?${new URLSearchParams({ league, position, q, limit: 80 })}`);
  document.getElementById("rows").innerHTML = rows.map((r) => {
    const gap = r.p_objective - r.p_established;
    const gapTag = Math.abs(gap) < 0.05 ? `<span class="tag">in line</span>` : gap > 0 ? `<span class="tag up">data +${Math.round(gap * 100)}</span>` : `<span class="tag down">market +${Math.round(-gap * 100)}</span>`;
    return `<tr class="row" data-id="${r.player_id}">
      <td class="who"><strong>${esc(r.name)}</strong><small>${esc(r.club)} · ${esc(r.sub_position || r.position)} ${moveTag(r.moved_from, r.move_fee, r.move_loan)}</small></td>
      <td class="num">${r.age}</td><td class="num">${r.minutes.toLocaleString()}</td><td class="num">${eur(r.value)}</td>
      <td>${probBar(r.p_established)}</td><td>${gapTag}</td>
      <td class="num">${eur(r.peak_p10)} – <strong>${eur(r.peak_p50)}</strong> – ${eur(r.peak_p90)}</td></tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">No player matches.</td></tr>`;
  document.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/player/${tr.dataset.id}`)));
}

// ── One player ─────────────────────────────────────────────────────────────
function rangeBar(lo, mid, hi, max, fmt) {
  const x = (v) => `${Math.min(100, (v / max) * 100)}%`;
  return `<div class="range"><div class="track"></div><div class="band" style="left:${x(lo)};width:calc(${x(hi)} - ${x(lo)})"></div><div class="mid" style="left:${x(mid)}"></div></div>
    <div class="num muted" style="display:flex;justify-content:space-between"><span>low ${fmt(lo)}</span><span style="color:var(--gold)">likely ${fmt(mid)}</span><span>high ${fmt(hi)}</span></div>`;
}

function minutesChart(seasons) {
  if (!seasons.length) return `<p class="muted">No league minutes in the covered competitions.</p>`;
  const w = 520, h = 150, bw = Math.min(56, (w - 40) / seasons.length - 8);
  const bars = seasons.map((s, i) => {
    const share = Math.min(1, s.share || 0), bh = share * (h - 40), x = 30 + i * (bw + 8);
    return `<rect x="${x}" y="${h - 22 - bh}" width="${bw}" height="${bh}" rx="3" fill="var(--crimson)" opacity="${0.45 + share * 0.55}"/>
      <text x="${x + bw / 2}" y="${h - 6}" text-anchor="middle">${s.season.slice(2)}/${String(+s.season.slice(2) + 1).padStart(2, "0")}</text>
      <text x="${x + bw / 2}" y="${h - 26 - bh}" text-anchor="middle">${Math.round(share * 100)}%</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%">${bars}<line x1="26" x2="${w}" y1="${h - 22}" y2="${h - 22}" stroke="var(--line-2)"/></svg>
    <p class="muted" style="margin:4px 0 0">Share of his team's league minutes, season by season.</p>`;
}

function valueChart(values) {
  if (values.length < 2) return `<p class="muted">Not enough valuations yet.</p>`;
  const w = 520, h = 150, L = 46, B = 22;
  const ts = values.map((v) => new Date(v.date).getTime()), vs = values.map((v) => v.value);
  const t0 = Math.min(...ts), t1 = Math.max(...ts), vmax = Math.max(...vs) * 1.1;
  const x = (t) => L + ((t - t0) / Math.max(t1 - t0, 1)) * (w - L - 10), y = (v) => h - B - (v / vmax) * (h - B - 12);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(ts[i]).toFixed(1)},${y(v.value).toFixed(1)}`).join(" ");
  const area = `${path} L${x(t1)},${h - B} L${x(t0)},${h - B} Z`;
  const years = [...new Set(values.map((v) => v.date.slice(0, 4)))];
  return `<svg viewBox="0 0 ${w} ${h}" width="100%"><path d="${area}" fill="var(--crimson)" opacity="0.14"/><path d="${path}" fill="none" stroke="var(--crimson)" stroke-width="2"/>
    <text x="${L - 6}" y="${y(Math.max(...vs)) + 4}" text-anchor="end">${eur(Math.max(...vs))}</text>
    ${years.map((yr) => [yr, new Date(`${yr}-01-01`).getTime()]).filter(([, tt]) => tt >= t0 && tt <= t1)
      .map(([yr, tt]) => `<text x="${x(tt)}" y="${h - 6}" text-anchor="middle">${yr}</text>`).join("")}
    <line x1="${L}" x2="${w - 10}" y1="${h - B}" y2="${h - B}" stroke="var(--line-2)"/></svg>
    <p class="muted" style="margin:4px 0 0">Market value over time (Transfermarkt).</p>`;
}

async function player(id) {
  view.innerHTML = `<p class="loading">Loading…</p>`;
  const d = await api(`/api/player/${id}`);
  const s = d.state, p = d.promise;
  const maxAbs = Math.max(...p.drivers.map((x) => Math.abs(x.value)), 0.5);
  view.innerHTML = `
    <p class="muted"><a href="#/">← all promises</a></p>
    <h1>${esc(s.name)}</h1>
    <p class="lede">${esc(d.moved ? d.moved.to_club : s.current_club_name)} · ${esc(s.sub_position || s.position)} · ${s.cutoff_age.toFixed(1)} years old · ${(s.min_total || 0).toLocaleString()} senior minutes · ${eur(d.moved && d.moved.value_window ? d.moved.value_window : s.mv_now)}
      ${d.moved ? `<br><span class="muted">This summer: ${esc(d.moved.from_club)} → ${esc(d.moved.to_club)}${d.moved.loan ? " on loan" : d.moved.fee_text ? ", " + esc(d.moved.fee_text) : ""}.</span>` : ""}
      ${d.ea_id ? `<br>${deskLink(d.ea_id, "Open him in the scout desk ↗")}` : ""}</p>
    <div class="grid g3">
      <div class="card kpi"><h3>Becomes a regular</h3><div class="big">${pct(p.p_established)}</div><small>top flight, 4,500+ league minutes between 23 and 26</small></div>
      <div class="card kpi"><h3>In the big five</h3><div class="big" style="color:var(--rosewood)">${pct(p.p_big5)}</div><small>same, in England, Spain, Italy, Germany or France</small></div>
      <div class="card kpi"><h3>Data alone</h3><div class="big" style="color:var(--gold)">${pct(p.p_objective)}</div><small>without the market's valuation: ${p.p_objective - p.p_established >= 0.05 ? "the data sees more than the market" : p.p_established - p.p_objective >= 0.05 ? "the market sees more than the data" : "data and market agree"}</small></div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>What drives it</h3><div class="drivers"><ul>${p.drivers.map((x) => {
        const w = (Math.abs(x.value) / maxAbs) * 50;
        return `<li><span>${esc(x.name)}</span><div class="dbar"><span style="${x.value >= 0 ? `left:50%;width:${w}%;background:var(--green)` : `right:50%;width:${w}%;background:var(--crimson)`}"></span></div></li>`;
      }).join("")}</ul></div><p class="muted" style="margin:8px 0 0">Green raises his chances, red lowers them, against the average promise of his age.</p></div>
      <div class="card"><h3>His pathway so far</h3>${minutesChart(d.seasons)}<h3 style="margin-top:18px">His value so far</h3>${valueChart(d.values)}</div>
    </div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>Range: league minutes at 23–26</h3>${rangeBar(p.min_p10, p.min_p50, p.min_p90, 12000, (v) => Math.round(v).toLocaleString())}
        <h3 style="margin-top:22px">Range: peak market value at 23–26</h3>${rangeBar(p.peak_p10, p.peak_p50, p.peak_p90, Math.max(p.peak_p90 * 1.1, 1e6), eur)}
        <p class="muted">From the real careers of the 25 most similar players at his age. In testing, the real outcome fell inside the low–high band 85% of the time.</p></div>
      <div class="card comps"><h3>Players like him at ${p.age_model}, and what they became</h3><ul>${p.comparables.map((c) => `<li><span><strong>${esc(c.name)}</strong> <span class="muted">(${esc(c.then_club_league || "")})</span></span><span class="num">${c.later_minutes.toLocaleString()} min · peak ${eur(c.later_peak_value)}</span></li>`).join("")}</ul>
        <p class="muted" style="margin:8px 0 0">${Math.round(p.comparables_established * 100)}% of his 25 comparables became regulars.</p>
        ${Math.abs(p.p_established - p.comparables_established) >= 0.25 ? `<p style="margin:8px 0 0">The model (${pct(p.p_established)}) and his comparables (${pct(p.comparables_established)}) disagree. ${p.p_established > p.comparables_established
          ? "He is already beyond most players the comparables can find at his age: the model extrapolates his trajectory, the comparables stay with the closest real careers."
          : "His record looks like players who made it, but the model weighs his context (level, minutes, team) more cautiously."} Both views are shown on purpose.</p>` : ""}</div>
    </div>`;
}

// ── Clubs ────────────────────────────────────────────────────────────────
async function clubs(params) {
  const league = params.get("league") ?? "FR1";
  view.innerHTML = `<h1>Who opens the door</h1>
    <p class="lede">Every club of the league's 2025/26 season: its level in FC 27, the share of league minutes it gave under-21s, the coach who finished the season and how much youth he has trusted since 2021/22, the summer 2026 window, and the promises it holds after it.</p>
    <div class="controls"><select id="league">${Object.entries(LEAGUES).map(([k, v]) => `<option value="${k}" ${k === league ? "selected" : ""}>${v}</option>`).join("")}</select></div>
    <div class="card"><table><thead><tr><th>Club</th><th>FC 27 level</th><th>Under-21 minutes 25/26</th><th>Coach</th><th>His youth trust</th><th>Summer 2026: in · out</th><th>Spent · received</th><th>Promises</th></tr></thead><tbody id="rows"><tr><td colspan="8" class="loading">Loading…</td></tr></tbody></table></div>`;
  document.getElementById("league").addEventListener("change", (e) => (location.hash = `#/clubs?league=${e.target.value}`));
  const rows = await api(`/api/clubs?league=${league}`);
  document.getElementById("rows").innerHTML = rows.map((r) => `<tr class="row" data-id="${r.club_id}">
    <td class="who"><strong>${esc(r.name)}</strong></td><td class="num">${r.level == null ? "–" : r.level.toFixed(1)}</td><td>${probBar(r.u21_share)}</td>
    <td>${esc(r.coach || "–")}</td><td class="num">${r.coach_trust == null ? '<span class="muted">too few games</span>' : pct(r.coach_trust)}</td>
    <td class="num">${r.n_in} · ${r.n_out}</td><td class="num">${eur(r.fee_in)} · ${eur(r.fee_out)}</td>
    <td class="num">${r.promises}${r.strong ? ` <span class="muted">(${r.strong} strong)</span>` : ""}</td></tr>`).join("");
  document.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/club/${tr.dataset.id}`)));
}

function moveRows(list, dir) {
  if (!list.length) return `<p class="muted">None.</p>`;
  const total = list.reduce((s, m) => s + (m.fee || 0), 0);
  return `<table><thead><tr><th>Player</th><th>Age</th><th>${dir === "in" ? "From" : "To"}</th><th>Fee</th></tr></thead><tbody>${list.map((m) => `<tr>
    <td class="who"><strong>${esc(m.player)}</strong><small>${esc(m.position || "")}</small></td><td class="num">${m.age ?? "–"}</td>
    <td>${esc(m.other_club || "–")}</td><td class="num">${m.loan ? '<span class="muted">loan</span>' : esc(m.fee_text || "–")}</td></tr>`).join("")}</tbody></table>
    <p class="muted">${list.length} move${list.length === 1 ? "" : "s"}, ${eur(total)} in fees.</p>`;
}

async function club(id) {
  view.innerHTML = `<p class="loading">Loading…</p>`;
  const d = await api(`/api/club/${id}`);
  const c = d.club;
  const w = 1040, h = 200, n = d.trend.length, bw = Math.min(56, (w - 40) / Math.max(n, 1) - 8);
  let lastCoach = null, changes = 0;
  const bars = d.trend.map((s, i) => {
    const v = s.u21_share || 0, bh = Math.min(1, v / 0.35) * (h - 76), x = 30 + i * (bw + 8);
    const change = s.manager && s.manager !== lastCoach;
    lastCoach = s.manager;
    const row = change ? (changes++ % 2) : 0;            // coach names alternate between two rows so neighbours don't collide
    return `<rect x="${x}" y="${h - 54 - bh}" width="${bw}" height="${bh}" rx="3" fill="var(--crimson)" opacity="${0.4 + Math.min(1, v / 0.3) * 0.6}"><title>${esc(s.manager || "")}</title></rect>
      <text x="${x + bw / 2}" y="${h - 38}" text-anchor="middle">${String(s.season).slice(2)}/${String(+String(s.season).slice(2) + 1).padStart(2, "0")}</text><text x="${x + bw / 2}" y="${h - 58 - bh}" text-anchor="middle">${Math.round(v * 100)}</text>
      ${change ? `<line x1="${x}" x2="${x}" y1="${h - 32}" y2="${h - 22 + row * 14}" stroke="var(--gold)" stroke-opacity="0.5"/><text x="${x + 3}" y="${h - 20 + row * 14}" style="fill:var(--gold);font-size:11px">${esc(s.manager)}</text>` : ""}`;
  }).join("");
  const young = d.squad.filter((p) => p.age <= 21);
  view.innerHTML = `<p class="muted"><a href="#/clubs?league=${esc(c.league)}">← all clubs</a></p>
    <h1>${esc(c.name)}</h1>
    <p class="lede">${c.level != null ? `FC 27 level <b>${c.level.toFixed(1)}</b> · ` : ""}Coach at the end of 2025/26: <b>${esc(c.coach || "–")}</b>${c.coach_trust != null ? ` (gives under-21s ${pct(c.coach_trust)} of his league minutes since 2021/22)` : ""}.
      ${d.squad.length} players in FC 27, ${young.length} aged 21 or under. ${d.players.length} promise${d.players.length === 1 ? "" : "s"} with senior minutes.
      ${c.ea_club ? `<br><a href="/scout#mode=lab&club=${encodeURIComponent(c.ea_club)}">Open ${esc(c.ea_club)} on the tactics board ↗</a>` : ""}</p>
    <div class="card"><h3>Share of league minutes given to under-21s, season by season (%)</h3>
      <svg viewBox="0 0 ${w} ${h}" width="100%">${bars}<line x1="26" x2="${w}" y1="${h - 54}" y2="${h - 54}" stroke="var(--line-2)"/></svg>
      <p class="muted" style="margin:4px 0 0">Gold: the coach who took most of that season's games, from the season he arrives.</p></div>
    <div class="card" style="margin-top:16px"><h3>The young players in the squad, and where they could go</h3>
      ${young.length ? `<table><thead><tr><th>Player</th><th>Age</th><th>Now</th><th>Peak (likely · high road)</th><th>Best role</th></tr></thead><tbody>${young.map((p) => `<tr class="desk" data-ea="${p.ea_id}">
        <td class="who"><strong>${esc(p.name)}</strong><small>${esc(p.position)}</small></td><td class="num">${p.age}</td><td class="num">${p.overall}</td>
        <td class="num">${p.peak_p50 == null ? "–" : `<b>${Math.round(p.peak_p50)}</b> · ${Math.round(p.peak_p90)}`}</td><td>${esc(p.best_role || "–")}</td></tr>`).join("")}</tbody></table>
        <p class="muted">FC 27 ratings; peaks from the calibrated four-season simulator. Click a player to open him in the scout desk.</p>` : `<p class="muted">No player aged 21 or under in FC 27.</p>`}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>Summer 2026: arrivals</h3>${moveRows(d.window.in, "in")}</div>
      <div class="card"><h3>Summer 2026: departures</h3>${moveRows(d.window.out, "out")}</div>
    </div>
    <div class="card" style="margin-top:16px"><h3>Promises</h3><table><thead><tr><th>Player</th><th>Age</th><th>Minutes</th><th>Value</th><th>Becomes a regular</th><th>Big five</th></tr></thead><tbody>${d.players.map((r) => `<tr class="row" data-id="${r.player_id}">
      <td class="who"><strong>${esc(r.name)}</strong><small>${esc(r.sub_position || r.position)}${r.left_for ? ` <span class="tag down">left for ${esc(r.left_for)}</span>` : ""}${r.joined_from ? ` <span class="tag up">joined from ${esc(r.joined_from)}</span>` : ""}</small></td><td class="num">${r.age}</td><td class="num">${r.minutes.toLocaleString()}</td>
      <td class="num">${eur(r.value)}</td><td>${probBar(r.p_established)}</td><td class="num">${pct(r.p_big5)}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No under-21 with senior minutes.</td></tr>`}</tbody></table></div>
    <div class="card" style="margin-top:16px"><h3>The whole squad in FC 27</h3><table><thead><tr><th>Player</th><th>Age</th><th>Rating</th><th>Peak (likely)</th><th>Best role</th></tr></thead><tbody>${d.squad.map((p) => `<tr class="desk" data-ea="${p.ea_id}">
      <td class="who"><strong>${esc(p.name)}</strong><small>${esc(p.position)}</small></td><td class="num">${p.age}</td><td class="num">${p.overall}</td>
      <td class="num">${p.peak_p50 == null ? "–" : Math.round(p.peak_p50)}</td><td>${esc(p.best_role || "–")}</td></tr>`).join("")}</tbody></table></div>`;
  document.querySelectorAll("tr.row").forEach((tr) => tr.addEventListener("click", () => (location.hash = `#/player/${tr.dataset.id}`)));
  document.querySelectorAll("tr.desk").forEach((tr) => tr.addEventListener("click", () => (location.href = `/scout#p=${tr.dataset.ea}`)));
}

// ── At 19, on the ball ────────────────────────────────────────────────────
function miniPitch(x, y) {
  // SPADL coordinates: x 0-105 (own goal to opponent's), y 0-68.
  const px = (x / 105) * 84 + 3, py = (y / 68) * 54 + 3;
  return `<svg viewBox="0 0 90 60" width="72" height="48"><rect x="3" y="3" width="84" height="54" rx="2" fill="none" stroke="var(--line-2)"/>
    <line x1="45" x2="45" y1="3" y2="57" stroke="var(--line-2)"/><circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="4" fill="var(--crimson)"/></svg>`;
}

async function actions() {
  view.innerHTML = `<h1>Young, on the ball</h1>
    <p class="lede">Every action of the 2017/18 season in the five big leagues, valued by how much it raised his team's chance to score or lowered its chance to concede (VAEP). These are the young players (21 or under that winter, 900+ minutes) whose actions were worth the most per 90, and what became of them by 2026.</p>
    <div class="card"><table><thead><tr><th>Player</th><th>Age then</th><th>Where he played</th><th>Value per 90</th><th>Attack / defence</th><th>Highest value since</th><th>Club now</th></tr></thead><tbody id="rows"><tr><td colspan="7" class="loading">Loading…</td></tr></tbody></table></div>
    <div class="card" style="margin-top:16px"><h3>What it does and doesn't tell</h3>
      <p style="margin:0">The model knows football: Messi (1.04 per 90), Neymar and Salah top the whole season. But across 181 outfield players born 1996–1999 who already played 900+ big-five minutes that season, one season of action value did not predict who became a regular better than minutes and level already did (AUC 0.62 either way). Once a big-five club plays a youngster, the selection has been made; one season is too noisy to split careers. Action value earns its place earlier (academy, Ligue 2, loans) and over several seasons.</p></div>
    <p class="muted">Action values from a model trained on one half of the matches and applied to the other. Data: Wyscout public events (CC BY 4.0).</p>`;
  const rows = await api("/api/actions/young");
  if (!rows.length) { document.getElementById("rows").innerHTML = `<tr><td colspan="7" class="muted">Action values are still being computed.</td></tr>`; return; }
  const max = Math.max(...rows.map((r) => r.vaep_p90));
  document.getElementById("rows").innerHTML = rows.map((r) => `<tr>
    <td class="who"><strong>${esc(r.name)}</strong><small>${esc(r.competition.replace(" first division", ""))} · ${r.minutes.toLocaleString()} min</small></td>
    <td class="num">${r.age_then.toFixed(1)}</td><td>${miniPitch(r.x, r.y)}</td>
    <td><div class="prob"><span class="num">${r.vaep_p90.toFixed(2)}</span><div class="bar"><span style="width:${Math.round((r.vaep_p90 / max) * 100)}%"></span></div></div></td>
    <td class="num">${r.off_p90.toFixed(2)} / ${r.def_p90.toFixed(2)}</td>
    <td class="num">${eur(r.peak_value)}</td><td>${esc(r.club_now || "–")}</td></tr>`).join("");
}

// ── Managers ───────────────────────────────────────────────────────────────
let COACHES = null;
async function managers(params) {
  const league = params.get("league") ?? "", current = params.get("current") !== "0", sort = params.get("sort") ?? "trust";
  view.innerHTML = `<h1>Who trusts youth</h1>
    <p class="lede">Every coach with 30+ league games since 2021/22 in the leagues Transfermarkt's open data covers: the share of his league minutes that went to under-21s, how many under-21s he gave 900+ minutes, and the ones he relied on most.</p>
    <div class="controls">
      <select id="m-league"><option value="">All leagues</option>${Object.entries(LEAGUES).map(([k, v]) => `<option value="${k}" ${k === league ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="m-sort"><option value="trust" ${sort === "trust" ? "selected" : ""}>Most youth minutes</option><option value="trusted" ${sort === "trusted" ? "selected" : ""}>Most youngsters trusted</option><option value="games" ${sort === "games" ? "selected" : ""}>Most games</option></select>
      <label class="muted"><input id="m-current" type="checkbox" ${current ? "checked" : ""}> in charge at the end of 2025/26</label>
    </div>
    <div class="card"><table><thead><tr><th>Coach</th><th>Club</th><th>Games</th><th>Youth minutes</th><th>Under-21s trusted</th><th>The youngsters he played most</th></tr></thead><tbody id="m-rows"><tr><td colspan="6" class="loading">Loading…</td></tr></tbody></table></div>
    <h2 style="margin-top:28px">Do their youngsters stay?</h2>
    <p class="lede">Managers with 80+ league games and 5+ young players developed from the 1996–1999 birth years, old enough to judge: giving youth minutes and graduating it run in opposite directions. Some open the door to many, some to few who almost all stay.</p>
    <div class="card"><svg id="sc" viewBox="0 0 760 420" width="100%"></svg></div>
    <p class="muted">Horizontal: share of his league minutes given to under-21s. Vertical: share of the youngsters he trusted who became top-flight regulars at 23–26. Size: number developed. Descriptive, small samples, club quality not removed.</p>`;
  const go = () => { const q = new URLSearchParams({ league: document.getElementById("m-league").value, sort: document.getElementById("m-sort").value, current: document.getElementById("m-current").checked ? "1" : "0" }); location.hash = `#/managers?${q}`; };
  ["m-league", "m-sort", "m-current"].forEach((id) => document.getElementById(id).addEventListener("change", go));
  COACHES ??= await api("/api/coaches");
  const rows = COACHES.filter((m) => (!league || m.league === league) && (!current || m.last_game >= "2026-05-01"))
    .sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0)).slice(0, 120);
  document.getElementById("m-rows").innerHTML = rows.map((m) => `<tr>
    <td class="who"><strong>${esc(m.manager)}</strong><small>${m.seasons} season${m.seasons === 1 ? "" : "s"}${m.graduation != null ? ` · ${pct(m.graduation)} of his 1996–99 youngsters became regulars` : ""}</small></td>
    <td>${esc(m.club || "–")}${m.ea_club ? ` <a class="muted" href="/scout#mode=lab&club=${encodeURIComponent(m.ea_club)}" title="Open the club on the tactics board">board ↗</a>` : ""}</td>
    <td class="num">${m.games}</td><td>${probBar(m.trust)}</td><td class="num">${m.trusted}</td>
    <td>${(m.names || []).map(esc).join(", ")}</td></tr>`).join("") || `<tr><td colspan="6" class="muted">No coach matches.</td></tr>`;
  const data = await api("/api/managers");
  const W = 760, H = 420, L = 50, B = 40;
  const maxT = Math.max(...data.map((d) => d.trust)) * 1.05;
  const x = (t) => L + (t / maxT) * (W - L - 20), y = (g) => H - B - g * (H - B - 20);
  const pts = data.map((d) => `<circle cx="${x(d.trust)}" cy="${y(d.graduation)}" r="${3 + Math.sqrt(d.developed) * 2}" fill="var(--crimson)" fill-opacity="0.45" stroke="var(--crimson)"><title>${esc(d.manager)}: ${pct(d.trust)} trust, ${pct(d.graduation)} of ${d.developed} established</title></circle>`).join("");
  // Label the most telling managers, skipping any label that would collide with one already placed.
  const placed = [];
  const labels = [...data].sort((a, b) => b.developed - a.developed || Math.abs(b.trust - 0.12) - Math.abs(a.trust - 0.12))
    .filter((d) => d.developed >= 8 || d.graduation >= 0.8 || d.trust >= 0.2)
    .map((d) => {
      const w = d.manager.length * 6.6, ly = y(d.graduation) + 4;
      const right = x(d.trust) + 9 + w <= W;                  // flip to the left near the edge
      const lx = right ? x(d.trust) + 9 : x(d.trust) - 9 - w;
      return { d, lx, ly, w, right };
    })
    .filter(({ lx, ly, w }) => {
      if (placed.some((p) => Math.abs(p.y - ly) < 13 && lx < p.x + p.w && p.x < lx + w)) return false;
      placed.push({ x: lx, y: ly, w });
      return true;
    })
    .map(({ d, ly, right }) => `<text x="${right ? x(d.trust) + 9 : x(d.trust) - 9}" y="${ly}" text-anchor="${right ? "start" : "end"}" style="fill:var(--dim)">${esc(d.manager)}</text>`).join("");
  const axes = [0, 0.25, 0.5, 0.75, 1].map((g) => `<line x1="${L}" x2="${W - 10}" y1="${y(g)}" y2="${y(g)}" stroke="var(--line)"/><text x="${L - 8}" y="${y(g) + 4}" text-anchor="end">${g * 100}%</text>`).join("")
    + [0, 0.05, 0.1, 0.15, 0.2, 0.25].filter((t) => t <= maxT).map((t) => `<text x="${x(t)}" y="${H - 14}" text-anchor="middle">${Math.round(t * 100)}%</text>`).join("");
  document.getElementById("sc").innerHTML = axes + pts + labels;
}

// ── Tactical Patcher ───────────────────────────────────────────────────────
async function patcher() {
  view.innerHTML = `<h1>Tactical Patcher</h1>
    <p class="quote">A Master League save that starts from a real FIFA 23 squad and plays three seasons with a growth engine learned from 67,000 real season-to-season rating changes.</p>
    <p class="lede">Pick a club and a manager. Youth trust is the rating bonus an under-21 gets when the manager picks his team: 0–1 is a selective developer, 7 and above a volume developer. Each run plays the seasons 120 times; the table shows the likely level after three seasons and its range, the minutes the simulated manager gave, and the minutes he really got in those three seasons (2022/23–2024/25).</p>
    <div class="controls"><select id="club"></select>
      <label class="muted">Youth trust <input id="trust" type="range" min="0" max="16" step="1" value="4" style="vertical-align:middle"> <span id="tv" class="num">3</span></label>
      <button id="run">Play three seasons</button></div>
    <div id="out"></div>`;
  const clubs = await api("/api/patcher/clubs");
  const sel = document.getElementById("club");
  sel.innerHTML = clubs.map((c) => `<option value="${esc(c.club)}" ${c.club === "Rennes" ? "selected" : ""}>${esc(c.league)} · ${esc(c.club)} (${c.level})</option>`).join("");
  const tv = document.getElementById("tv"), tr = document.getElementById("trust");
  const archetype = (v) => (v <= 1 ? "selective developer" : v <= 6 ? "balanced" : "volume developer");
  const label = () => (tv.textContent = `${tr.value} · ${archetype(+tr.value)}`);
  tr.addEventListener("input", label);
  label();
  document.getElementById("run").addEventListener("click", async () => {
    const out = document.getElementById("out");
    out.innerHTML = `<p class="loading">Playing seasons…</p>`;
    const res = await api("/api/patcher/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ club: sel.value, youth_trust: +tr.value, seasons: 3, runs: 120 }) });
    const by = {};
    res.rows.forEach((r) => ((by[r.fifa_id] ??= []).push(r)));
    const list = Object.values(by).map((rs) => rs.sort((a, b) => a.season - b.season)).sort((a, b) => a[0].age - b[0].age);
    // Where reality outran (or fell short of) the simulated manager.
    const gaps = list.map((rs) => {
      const sim = rs.slice(1).reduce((t, r) => t + (r.share_mean || 0), 0) / (rs.length - 1);
      const real = rs.slice(1).map((r) => r.real_share).filter((v) => v != null);
      return real.length ? { name: rs[0].name, age: rs[0].age, sim, real: real.reduce((t, v) => t + v, 0) / real.length } : null;
    }).filter(Boolean);
    // Only real gaps count: at least 15 points of the minutes either way.
    const young = gaps.filter((g) => g.age <= 20);   // under 21 in 2022/23, as the trust bonus
    const outran = young.filter((g) => g.real - g.sim >= 0.15).sort((a, b) => (b.real - b.sim) - (a.real - a.sim)).slice(0, 3);
    const held = young.filter((g) => g.sim - g.real >= 0.15).sort((a, b) => (b.sim - b.real) - (a.sim - a.real)).slice(0, 3);
    const who = (gs) => gs.map((g) => `<strong>${esc(g.name)}</strong> (${pct(g.real)} real vs ${pct(g.sim)} simulated)`).join(", ");
    const lines = [];
    if (outran.length) lines.push(`Young players who got far more minutes, here or at their next club, than the simulated manager gave them: ${who(outran)}.${outran[0].real - outran[0].sim > 0.2 ? " That gap is where human judgement shows: someone saw what the ratings didn't yet." : ""}`);
    if (held.length) lines.push(`And the other way: ${who(held)}. Ratings said play them; the club, or an injury, or a loan, said otherwise.`);
    const summary = lines.length ? `<div class="card" style="margin-bottom:16px"><h3>Reality against the simulation</h3>
      ${lines.map((l) => `<p style="margin:0 0 6px">${l}</p>`).join("")}</div>` : "";
    const rv = res.revealed;
    // Four readings: nothing fits, no youth bonus at all, a bonus that barely helps, a bonus that clearly fits better.
    const reading = !rv ? "" : rv.error >= 0.25
      ? `no youth-trust setting fits (the closest, ${rv.revealed_trust}, is still off by ${pct(rv.error)} of the minutes): this club chose among its young players rather than for them, and the table below shows who`
      : rv.revealed_trust === 0
      ? `its young players' minutes match a level-only manager (youth trust 0): no bonus for youth, an average gap of ${pct(rv.error)} of the minutes`
      : rv.error_level_only - rv.error < 0.02
        ? `the best fit is a youth trust of <strong>${rv.revealed_trust}</strong>, barely closer than a level-only manager (a gap of ${pct(rv.error)} of the minutes either way): young players picked on level`
        : `its young players' minutes match a manager with a youth trust of <strong>${rv.revealed_trust}</strong> (${archetype(rv.revealed_trust)}), with an average gap of ${pct(rv.error)} of the minutes, against ${pct(rv.error_level_only)} for a level-only manager`;
    const revealed = rv ? `<div class="card" style="margin-bottom:16px"><h3>Revealed youth trust</h3>
      <p style="margin:0">How ${esc(res.club)} really behaved in 2022/23–2024/25: ${reading}. Read from ${rv.young_players} young player${rv.young_players === 1 ? "" : "s"}${rv.young_players < 4 ? ", a thin sample: treat it as a hint, not a verdict" : ""}.</p></div>` : "";
    out.innerHTML = revealed + summary + `<div class="card"><table><thead><tr><th>Player</th><th>Age 22/23</th><th>Level 22/23</th><th>Level 25/26 (likely, range)</th><th>Simulated minutes share</th><th>Real share 22/23–24/25</th></tr></thead><tbody>${list.map((rs) => {
      const a = rs[0], z = rs[rs.length - 1];
      const simShare = rs.slice(1).reduce((t, r) => t + (r.share_mean || 0), 0) / (rs.length - 1);
      const real = rs.slice(1).map((r) => r.real_share).filter((v) => v != null);
      const realShare = real.length ? real.reduce((t, v) => t + v, 0) / real.length : null;
      const delta = z.level_p50 - a.level_p50;
      return `<tr><td class="who"><strong>${esc(a.name)}</strong><small>${a.line}</small></td><td class="num">${a.age}</td><td class="num">${a.level_p50}</td>
        <td class="num"><strong>${Math.round(z.level_p50)}</strong> <span class="${delta >= 0 ? "tag up" : "tag down"}">${delta >= 0 ? "+" : ""}${Math.round(delta)}</span> <span class="muted">${Math.round(z.level_p10)}–${Math.round(z.level_p90)}</span></td>
        <td class="num">${pct(simShare)}</td><td class="num">${realShare == null ? '<span class="muted">left the covered leagues</span>' : pct(realShare)}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  });
}

// ── Method ─────────────────────────────────────────────────────────────────
const MODEL_NAMES = {
  "established, objective": "Top-flight regular, data only",
  "established, with market": "Top-flight regular, data + market value",
  "big five, with market": "Big-five regular, data + market value",
};

async function method() {
  const o = await api("/api/overview");
  const rep = o.report.filter((r) => r.auc != null);
  const cov = o.report.filter((r) => r.auc == null);
  view.innerHTML = `<h1>How it is measured</h1>
    <p class="lede">The promise model behind these pages. Every model is judged on players it never saw: trained on four birth years (1995–1999), scored on the fifth, rotating. ${o.n_history.toLocaleString()} historical states, ${o.n_current.toLocaleString()} current promises.</p>
    <div class="grid g2"><div class="card"><h3>Becoming a regular, by age (AUC)</h3><table><thead><tr><th>Model</th><th>18</th><th>19</th><th>20</th><th>21</th></tr></thead><tbody>${
      [...new Set(rep.map((r) => r.model))].map((m) => `<tr><td>${esc(MODEL_NAMES[m] || m)}</td>${[18, 19, 20, 21].map((a) => { const r = rep.find((x) => x.model === m && x.age === a); return `<td class="num">${r ? r.auc.toFixed(2) : "–"}</td>`; }).join("")}</tr>`).join("")
    }</tbody></table><p class="muted">0.5 is a coin flip, 1.0 is perfect. The future is more open at 18 than at 21, and the numbers say so.</p></div>
    <div class="card"><h3>Ranges that hold</h3><table><tbody>${cov.map((r) => `<tr><td>Comparables' low–high band at ${r.age}</td><td class="num">${pct(r.base)} of real outcomes inside</td></tr>`).join("")}
      <tr><td>Growth engine, median error (rating points)</td><td class="num">${o.growth.mae.toFixed(2)} vs ${o.growth.mae_baseline.toFixed(2)} for the age average</td></tr>
      <tr><td>Growth engine, P10–P90 coverage</td><td class="num">${pct(o.growth.coverage)}</td></tr></tbody></table></div></div>
    <div class="card" style="margin-top:16px"><h3>What the data taught us</h3><ul>
      <li>The single best signal before 21 is minutes weighted by league level; raw minutes in weak leagues add nothing once level is counted.</li>
      <li>Minutes at 20 matter more than at 19, which matter more than under 19.</li>
      <li>At 19, reading the game (EA's reactions, composure) predicts careers better than pace.</li>
      <li>Public data plateaus around 0.82–0.88: the rest of the variance lives in what only a club holds (growth, load, cognition, personality, staff judgement).</li>
      <li>Managers who give youth the most minutes graduate the smallest share of it, and the reverse: volume developers and selective developers (correlation −0.42).</li>
      <li>A 19-year-old's rating rises about +1.5 a season with no minutes and +5.8 with 80% of his team's minutes (association, not proof).</li>
      <li>Cross-check: Strasbourg gives under-21s 39% of its league minutes here; the CIES Football Observatory measures 37.9% independently.</li>
      <li>Clubs reveal their manager: matched against the Tactical Patcher, Ligue 1's real under-21 minutes in 2022/23–2024/25 read as Strasbourg 12, Montpellier 10, Lyon 6, Lille and Nantes 4, Rennes, Troyes and Monaco 2, PSG, Marseille and Brest 0. At Reims, Toulouse, Lorient and Nice no single setting fits: they picked among their youngsters, not for them.</li>
      <li>One season of on-ball value (VAEP) doesn't split the careers of youngsters already playing in the big five; minutes and level hold the same information. It belongs earlier and over several seasons.</li>
    </ul></div>
    <div class="card" style="margin-top:16px"><h3>Sources and licences</h3><ul>
      <li>Transfermarkt datasets (dcaribou, CC0): players, 1.9M appearances with managers, market values, transfers, 2012–2026.</li>
      <li>FIFA 15–23 career-mode ratings (sofifa scrape, Hugging Face mirror): 161,583 player-seasons, 110 attributes.</li>
      <li>Wyscout public events (Pappalardo et al., Scientific Data 2019, CC BY 4.0): every on-ball action of 2017/18 in the five big leagues, World Cup 2018, Euro 2016.</li>
      <li>Linked by date of birth and name tokens: 24,238 FIFA players and 3,389 of 3,603 Wyscout players matched to Transfermarkt.</li>
    </ul></div>`;
}

// ── Router ─────────────────────────────────────────────────────────────────
async function route() {
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query || "");
  const parts = (path || "/").split("/").filter(Boolean);
  const name = parts[0] || "promesses";
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (name === "player" ? "promesses" : name === "club" ? "clubs" : name)));
  try {
    if (!Object.keys(LEAGUES).length) LEAGUES = (await api("/api/overview")).leagues;
    if (name === "player") await player(parts[1]);
    else if (name === "managers") await managers(params);
    else if (name === "clubs") await clubs(params);
    else if (name === "actions") await actions();
    else if (name === "club") await club(parts[1]);
    else if (name === "patcher") await patcher();
    else if (name === "method") await method();
    else await promises(params);
  } catch (e) {
    view.innerHTML = `<p class="muted">Couldn't load this page (${esc(e.message)}).</p>`;
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
route();
