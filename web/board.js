// Tactics board: the squad lab as a game plan. Controls in the rail (coach
// style, formation, PES instructions and advanced instructions, squad
// settings, transfers), the board on the desk (both phases on the pitch,
// what the plan asks against what the eleven has, seasons played forward).
// Loaded after scout.js and uses its helpers ($, esc, api, pct, store).

const SEG_LABEL = { attack: "Attacking style", build_up: "Build-up", area: "Attacking area", positioning: "Positioning",
  defence: "Defensive style", containment: "Containment area", pressing: "Pressuring" };
const board = { opts: null, style: "positional", formation: "4-3-3", plan: {}, advanced: [], roles: [], pins: {},
  add: new Map(), remove: new Map(), result: null, season: 0, phase: "in", edited: false, seq: 0, open: null, openInd: new Set() };

async function boardOptions() {
  if (!board.opts) board.opts = await api("/api/patcher/lab/options");
  return board.opts;
}

function boardSave() {
  const f = $("#mode-lab");
  store.set("board", JSON.stringify({ style: board.style, formation: board.formation, plan: board.plan, advanced: board.advanced,
    roles: board.roles, pins: board.pins, edited: board.edited, youth_trust: f.elements.youth_trust.value,
    rotation: f.elements.rotation.value, seasons: f.elements.seasons.value }));
}

function boardRestore() {
  let s = null;
  try { s = JSON.parse(store.get("board") || "null"); } catch { s = null; }
  if (!s || !board.opts.styles[s.style]) { applyStyle("positional"); return; }
  Object.assign(board, { style: s.style, formation: s.formation, plan: s.plan, advanced: s.advanced, roles: s.roles || [], pins: s.pins || {}, edited: !!s.edited });
  const f = $("#mode-lab");
  for (const k of ["youth_trust", "rotation", "seasons"]) if (s[k] != null) f.elements[k].value = s[k];
}

function applyStyle(key) {
  const s = board.opts.styles[key];
  Object.assign(board, { style: key, formation: s.formation, plan: { ...s.plan }, advanced: [...s.advanced], roles: [], pins: {}, edited: false });
}

function setOut(name, v) { const o = document.querySelector(`[data-out="${name}"]`); if (o) o.textContent = v; }

function renderControls() {
  const o = board.opts, f = $("#mode-lab");
  $("#lab-style").innerHTML = Object.entries(o.styles).map(([k, s]) =>
    `<option value="${k}" ${k === board.style ? "selected" : ""}>${esc(s.label)} · ${esc(s.ref)}</option>`).join("");
  $("#lab-idea").textContent = board.edited ? `Custom plan, starting from ${o.styles[board.style].label.toLowerCase()}.` : o.styles[board.style].idea;
  $("#lab-formation").innerHTML = o.formations.map((x) => `<option ${x === board.formation ? "selected" : ""}>${x}</option>`).join("");
  document.querySelectorAll("#mode-lab .seg[data-k]").forEach((el) => {
    const k = el.dataset.k;
    el.innerHTML = `<span class="seg-l">${esc(SEG_LABEL[k])}</span><span class="seg-b">${Object.entries(o.instructions[k]).map(([v, l]) =>
      `<button type="button" data-seg="${k}" data-v="${v}" class="${board.plan[k] === v ? "on" : ""}">${esc(l)}</button>`).join("")}</span>`;
  });
  for (const k of ["support", "line", "compact"]) { f.elements[k].value = board.plan[k]; setOut(k, board.plan[k]); }
  for (const k of ["youth_trust", "rotation"]) setOut(k, f.elements[k].value);
  for (const ph of ["attack", "defence"]) {
    $(`#adv-${ph}`).innerHTML = `<span class="seg-l">${ph === "attack" ? "Attacking" : "Defending"}</span>` + Object.entries(o.advanced[ph]).map(([k, a]) =>
      `<button type="button" class="advchip ${board.advanced.includes(k) ? "on" : ""}" data-adv="${k}" data-ph="${ph}" title="${esc(a.tip)}">${esc(a.label)}</button>`).join("");
  }
}

function labMoves() {
  const chip = (id, name, kind) => `<span class="move ${kind}">${kind === "in" ? "+" : "−"} ${esc(name)} <button type="button" data-undo="${kind}:${id}" aria-label="Undo">×</button></span>`;
  $("#lab-moves").innerHTML = [...[...board.add].map(([id, n]) => chip(id, n, "in")), ...[...board.remove].map(([id, n]) => chip(id, n, "out"))].join("")
    || `<span class="muted">No moves yet.</span>`;
}

let boardTimer;
function boardChanged(now = false) {
  board.edited = true;
  $("#lab-idea").textContent = `Custom plan, starting from ${board.opts.styles[board.style].label.toLowerCase()}.`;
  boardSave();
  clearTimeout(boardTimer);
  boardTimer = setTimeout(runBoard, now ? 0 : 220);
}

async function runBoard() {
  const f = $("#mode-lab");
  const seq = ++board.seq;
  const desk = $("#desk");
  if (board.result && desk.querySelector(".board-grid")) desk.classList.add("busy");
  else desk.innerHTML = `<div class="skeleton board-skel"><div></div><div></div></div>`;
  const body = { club: $("#our-club").value, formation: board.formation, style: board.style, plan: board.plan, advanced: board.advanced,
    roles: board.roles.length ? board.roles : null, pins: board.pins, youth_trust: +f.elements.youth_trust.value,
    rotation: +f.elements.rotation.value, seasons: +f.elements.seasons.value, add: [...board.add.keys()], remove: [...board.remove.keys()] };
  try {
    const r = await api("/api/patcher/lab", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (seq !== board.seq) return;                       // a newer run is on its way
    board.result = r;
    board.roles = r.roles;
    board.season = Math.min(board.season, r.seasons.length - 1);
    boardSave();
    renderBoard();
  } catch (e) {
    if (seq === board.seq) desk.innerHTML = `<p class="loading">The board couldn't run (${esc(e.message)}). Try another setting.</p>`;
  } finally {
    if (seq === board.seq) desk.classList.remove("busy");
  }
}

// ── The pitch ────────────────────────────────────────────────────────────────
// Board coordinates: x 0-100 across, y 0-100 up the pitch. SVG viewBox 68 x 100 (a pitch's proportions), so arrows keep their shape.
const PX = (x) => (x * 0.68).toFixed(2), PY = (y) => (100 - y).toFixed(2);

function markings() {
  return `<rect x="0.4" y="0.4" width="67.2" height="99.2" rx="1.5" class="turf"/>
    ${[...Array(10)].map((_, i) => `<rect x="0" y="${i * 10}" width="68" height="5" class="stripe"/>`).join("")}
    <g class="mk"><line x1="0.4" y1="50" x2="67.6" y2="50"/><circle cx="34" cy="50" r="8.7"/><circle cx="34" cy="50" r="0.5" class="dotm"/>
    <rect x="13.85" y="0.4" width="40.3" height="15.7"/><rect x="24.85" y="0.4" width="18.3" height="5.2"/><circle cx="34" cy="10.5" r="0.45" class="dotm"/>
    <rect x="13.85" y="83.9" width="40.3" height="15.7"/><rect x="24.85" y="94.4" width="18.3" height="5.2"/><circle cx="34" cy="89.5" r="0.45" class="dotm"/>
    <path d="M26.8 15.7 A8.7 8.7 0 0 0 41.2 15.7"/><path d="M26.8 84.3 A8.7 8.7 0 0 1 41.2 84.3"/></g>`;
}

function inPossessionLayer(s, xi) {
  let g = "";
  // Passing network: each player to his two nearest team-mates; denser when the plan plays short.
  const pts = xi.map((x) => x.ip), seen = new Set();
  const strong = board.plan.build_up === "short" || board.advanced.includes("tiki_taka");
  pts.forEach((p, i) => {
    pts.map((q, j) => [j, Math.hypot(p[0] - q[0], p[1] - q[1])]).filter(([j]) => j !== i).sort((a, b) => a[1] - b[1]).slice(0, strong ? 3 : 2)
      .forEach(([j]) => { const key = [i, j].sort().join("-"); if (!seen.has(key)) { seen.add(key);
        g += `<line x1="${PX(p[0])}" y1="${PY(p[1])}" x2="${PX(pts[j][0])}" y2="${PY(pts[j][1])}" class="net ${strong ? "strong" : ""}"/>`; } });
  });
  // Movements: from the formation's spot to where the role takes him.
  xi.forEach((x) => {
    if (Math.hypot(x.ip[0] - x.x, x.ip[1] - x.y) > 4)
      g += `<line x1="${PX(x.x)}" y1="${PY(x.y)}" x2="${PX(x.ip[0])}" y2="${PY(x.ip[1])}" class="move-arrow" marker-end="url(#arr)"><title>${esc(x.name)}: ${esc(x.move || "")}</title></line>
            <circle cx="${PX(x.x)}" cy="${PY(x.y)}" r="1.1" class="ghost"/>`;
  });
  // Rotations and the long ball.
  const byId = Object.fromEntries(xi.map((x) => [x.ea_id, x]));
  (s.rotations || []).forEach(([a, b]) => { const p = byId[a]?.ip, q = byId[b]?.ip; if (p && q)
    g += `<path d="M${PX(p[0])} ${PY(p[1])} Q ${PX((p[0] + q[0]) / 2 + 6)} ${PY((p[1] + q[1]) / 2 + 6)} ${PX(q[0])} ${PY(q[1])}" class="rot" marker-end="url(#arr-g)" marker-start="url(#arr-g-s)"/>`; });
  if (s.long_ball) {
    const gk = xi.find((x) => x.slot === "GK"), st = xi.filter((x) => x.slot === "ST").sort((a, b) => b.ip[1] - a.ip[1])[0];
    if (gk && st) g += `<path d="M${PX(gk.ip[0])} ${PY(gk.ip[1])} Q ${PX(gk.ip[0] + 22)} ${PY(50)} ${PX(st.ip[0])} ${PY(st.ip[1] - 3)}" class="longball" marker-end="url(#arr-w)"/>`;
  }
  return g;
}

function outOfPossessionLayer(s, xi) {
  const field = xi.filter((x) => x.slot !== "GK");
  const front = Math.max(...field.map((x) => x.op[1])), back = s.line_y;
  const metres = Math.round((front - back) * 1.05);
  let g = `<rect x="0.4" y="0.4" width="67.2" height="${PY(s.press_from)}" class="press-zone"/>
    <text x="-1.2" y="${(+PY(s.press_from) + 0.7).toFixed(2)}" class="zlabel right">press</text>
    <line x1="0.4" y1="${PY(back)}" x2="67.6" y2="${PY(back)}" class="def-line"/>
    <text x="-1.2" y="${(+PY(back) + 0.7).toFixed(2)}" class="zlabel right">line ${board.plan.line}/10</text>
    <path d="M68.8 ${PY(front)} h1.2 V ${PY(back)} h-1.2" class="bracket"/>
    <text x="72.6" y="${PY((front + back) / 2)}" class="zlabel end" transform="rotate(-90 72.6 ${PY((front + back) / 2)})">block ${metres} m deep</text>`;
  const mid = (front + back) / 2;
  if (s.containment === "middle") {
    g += `<rect x="20.4" y="${PY(front)}" width="27.2" height="${(front - back).toFixed(2)}" class="closed"/>
      <line x1="30" y1="${PY(mid)}" x2="18" y2="${PY(mid)}" class="show" marker-end="url(#arr-w)"/><line x1="38" y1="${PY(mid)}" x2="50" y2="${PY(mid)}" class="show" marker-end="url(#arr-w)"/>
      <text x="34" y="${(+PY(mid) - 2).toFixed(2)}" class="zlabel mid">show them wide</text>`;
  } else {
    g += `<rect x="0.4" y="${PY(front)}" width="14" height="${(front - back).toFixed(2)}" class="closed"/><rect x="53.6" y="${PY(front)}" width="14" height="${(front - back).toFixed(2)}" class="closed"/>
      <line x1="10" y1="${PY(mid)}" x2="22" y2="${PY(mid)}" class="show" marker-end="url(#arr-w)"/><line x1="58" y1="${PY(mid)}" x2="46" y2="${PY(mid)}" class="show" marker-end="url(#arr-w)"/>
      <text x="34" y="${(+PY(mid) - 2).toFixed(2)}" class="zlabel mid">show them inside</text>`;
  }
  return g;
}

function pitchBoard(s) {
  const ph = board.phase;
  const xi = s.xi.map((x, k) => (x ? { ...x, k } : null)).filter(Boolean);
  const fit = (f) => (f == null ? "f-lo" : f >= 0.3 ? "f-hi" : f >= 0.15 ? "f-mid" : "f-lo");
  const nodes = xi.map((x) => {
    const [px, py] = ph === "in" ? x.ip : x.op;
    const tired = x.availability != null && x.availability < 0.9;
    const tip = `${x.name} · ${x.role} · level ${x.role_level}${x.fit != null ? " · fit " + x.fit : ""}${x.natural ? "" : " · out of position"}` +
      `${ph === "in" && x.move ? " · " + x.move : ""}${tired ? ` · carries ${Math.round(x.availability * 100)}% of a starter's minutes under this load` : ""}`;
    return `<button type="button" class="bnode ${board.open === x.k ? "open" : ""}" data-slot="${x.k}" style="left:${px}%;top:${100 - py}%" title="${esc(tip)}">
      <span class="dot ${fit(x.fit)} ${x.natural ? "" : "oop"} ${x.pinned ? "pin" : ""}">${Math.round(x.rating)}</span>
      ${tired ? `<span class="tired" style="--a:${Math.round(x.availability * 100)}%"></span>` : ""}
      <span class="nm">${esc(x.name.split(" ").slice(-1)[0])}</span><span class="rl">${esc(x.role)}</span></button>`;
  }).join("");
  return `<div class="bpitch ${ph}">
    <svg viewBox="0 0 68 100" aria-hidden="true">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" class="ah-gold"/></marker>
        <marker id="arr-w" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" class="ah-white"/></marker>
        <marker id="arr-g" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0 L10 5 L0 10z" class="ah-green"/></marker>
        <marker id="arr-g-s" viewBox="0 0 10 10" refX="2" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z" class="ah-green"/></marker>
      </defs>
      ${markings()}
      ${ph === "in" ? inPossessionLayer(s, xi) : outOfPossessionLayer(s, xi)}
    </svg>
    ${nodes}
    ${board.open != null ? slotEditor(s) : ""}
  </div>`;
}

function slotEditor(s) {
  const x = s.xi[board.open];
  if (!x) return "";
  const [px, py] = board.phase === "in" ? x.ip : x.op;
  const roles = board.opts.slot_roles[x.slot];
  const pinned = board.pins[board.open];
  const squad = board.result.squad;
  const left = Math.min(Math.max(px, 22), 78), top = Math.min(100 - py + 6, 70);
  return `<div class="slot-pop" style="left:${left}%;top:${top}%">
    <div class="sp-head"><b>${esc(x.slot)}</b> <span class="muted">${esc(x.name)}</span><button type="button" class="ghost x" data-close-slot aria-label="Close">×</button></div>
    <label>Role <select data-slot-role="${board.open}">${roles.map((r) => `<option ${r === x.role ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></label>
    <label>Player <select data-slot-pin="${board.open}"><option value="">coach's choice</option>${squad.map((p) =>
      `<option value="${p.ea_id}" ${+pinned === p.ea_id ? "selected" : ""}>${esc(p.name)} · ${esc(p.position)} · ${Math.round(p.rating)}</option>`).join("")}</select></label>
  </div>`;
}

// ── The desk ─────────────────────────────────────────────────────────────────
const SLOT_NAME = { GK: "keeper", CB: "centre-backs", FB: "full-backs", WB: "wing-backs", DM: "holding midfielders", CM: "central midfielders",
  AM: "number 10", IW: "inside 10s", W: "wingers", WM: "wide midfielders", ST: "strikers" };
const sign = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(Math.abs(v) < 10 && v % 1 ? 1 : 0);
const pctFit = (f) => `${Math.round(f * 100)}%`;
const clamp = (v) => Math.max(0, Math.min(100, v));
const status = (gap) => (gap >= -1 ? "ok" : gap >= -4 ? "near" : "short");
const applyBtn = (payload, label = "Apply") => `<button type="button" class="apply" data-apply="${esc(JSON.stringify(payload))}">${label}</button>`;

function fixesFor(key) {
  const f = board.result.fixes || {};
  return { tac: (f.tactical || []).filter((x) => x.target === key), per: (f.personnel || []).filter((x) => x.target === key),
           mkt: (f.market || []).filter((x) => x.target === key) };
}

// One scale for every indicator: the big five's spread (band), its median (line), your eleven (bar) and the plan's ask (tick).
function scaleBar(i) {
  const lo = 55, hi = 92, x = (v) => clamp(((v - lo) / (hi - lo)) * 100);
  const [p10, p50, p90] = i.scale;
  return `<span class="scale" aria-hidden="true"><i class="band" style="left:${x(p10)}%;width:${x(p90) - x(p10)}%"></i><i class="med" style="left:${x(p50)}%"></i>
    <span class="have ${status(i.gap)}" style="width:${x(i.value)}%"></span><i class="need" style="left:${x(i.demand)}%"></i></span>`;
}

function indicatorDetail(i, first, present) {
  const fx = fixesFor(i.key);
  const slots = Object.entries(i.slots).filter(([k]) => present.has(k)).map(([k, w]) => `<span class="chip-s">${esc(SLOT_NAME[k] || k)} ×${w}</span>`).join("");
  const attrs = Object.entries(i.attrs).map(([k, w]) => `<span class="chip-s">${esc(k)} ${Math.round(w * 100)}%</span>`).join("");
  const [p10, p50, p90] = i.scale;
  const terms = i.terms.length ? i.terms.map(([l, v]) => `<span class="chip-s ${v > 0 ? "up" : "down"}">${esc(l)} ${sign(v)}</span>`).join("") : `<span class="muted">nothing: a balanced plan</span>`;
  const maxS = Math.max(...i.who.map((w) => w.score), 1);
  const who = i.who.map((w, n) => `<div class="carrier ${n === 0 && i.gap < -1 ? "weak" : ""}"><span>${esc(w.name)} <small>${esc(w.slot)}${w.weight !== 1 ? " ×" + w.weight : ""}</small></span>
    <span class="cbar"><span style="width:${clamp(((w.score - 40) / (maxS - 40)) * 100)}%"></span></span><span class="n">${Math.round(w.score)}</span></div>`).join("");
  const tac = fx.tac.map((f) => `<div class="fix"><div><b>${esc(f.text)}</b><small>asks ${f.demand_from} → ${f.demand_to} · plan fit ${pctFit(f.fit_from)} → ${pctFit(f.fit_to)}${f.side.length ? ` · also ${f.side.map((s) => `${esc(s.label.toLowerCase())} ${s.from} → ${s.to}`).join(", ")}` : ""}</small></div>${applyBtn(f.change)}</div>`).join("");
  const per = fx.per.map((f) => `<div class="fix"><div><b>Start ${esc(f.in)} for ${esc(f.out)} (${esc(f.slot_type)})</b><small>${Math.round(f.in_score)} vs ${Math.round(f.out_score)} here · the eleven ${f.before} → ${f.after}${f.in_level != null ? ` · as ${esc(f.role.toLowerCase())}: ${f.in_level} vs ${f.out_level}` : ""}</small></div>${applyBtn({ type: "pin", slot: f.slot, id: f.in_id })}</div>`).join("");
  const mkt = fx.mkt.map((f) => `<div class="fix market-fix"><div><b>On the market, for ${esc(f.replace)}'s place (${esc(f.slot_type)}, ${Math.round(f.replace_score)} here)</b><small>players who would come: clubs at this level or below, value within the squad's top fee (${eur(f.budget)})</small>
      ${f.players.map((q) => `<div class="target"><span><a href="/scout#p=${q.ea_id}" target="_blank" rel="noopener">${esc(q.name)}</a> <small>${q.age} · ${esc(q.club)} · ${q.overall} · ${eur(q.value)}</small></span><span class="n">${Math.round(q.score)}</span>${applyBtn({ type: "sign", id: q.ea_id, name: q.name, slot: f.slot }, "Sign")}</div>`).join("")}</div></div>`).join("");
  const fixes = tac + per + mkt;
  return `<div class="ind-body">
    ${i.note ? `<p class="note ${i.kind}">${esc(i.note)}</p>` : ""}
    <div class="ib-grid">
      <div><h4>What it measures</h4><p>${esc(i.info)}</p><div class="chips">${slots}</div><div class="chips">${attrs}</div></div>
      <div><h4>Why the plan asks ${Math.round(i.demand)}</h4>
        <p>The big five's first elevens run from <b>${p10}</b> (bottom tenth) to <b>${p90}</b> (top tenth); the median eleven has <b>${p50}</b>, and that is what a balanced plan asks. Each +1 below moves the ask from the median to the top tenth.</p>
        <div class="chips">${terms}</div></div>
      <div><h4>Who carries it</h4>${who}</div>
      <div><h4>How to fix it</h4>${first ? (fixes || `<p class="muted">${i.gap >= -1.5 ? "Nothing to fix: this eleven carries it." : "No single change closes it; look at the style list above."}</p>`) : `<p class="muted">Fixes are worked out for ${esc(board.result.seasons[0].season)}, the season still open to changes.</p>`}</div>
    </div></div>`;
}

function indicatorRows(s) {
  const first = board.season === 0;
  const present = new Set(s.xi.filter(Boolean).map((x) => x.slot));
  return s.indicators.map((i) => `<details class="ind2 ${status(i.gap)}" data-key="${i.key}" ${board.openInd.has(i.key) ? "open" : ""}>
    <summary><span class="lbl">${esc(i.label)}</span>${scaleBar(i)}<span class="n">${Math.round(i.value)}<small> / ${Math.round(i.demand)}</small></span>
      <span class="gap ${status(i.gap)}">${i.gap >= 0 ? "+" : "−"}${Math.abs(Math.round(i.gap))}</span></summary>
    ${indicatorDetail(i, first, present)}</details>`).join("");
}

// The coach's notes: the biggest shortfalls first, each with the change that helps most, and the styles that suit this eleven.
function coachNotes(s) {
  const r = board.result, f = r.fixes || {};
  if (board.season !== 0) return "";
  const n = s.indicators.length;
  const short = [...s.indicators].filter((i) => i.gap < -1.5).sort((a, b) => a.gap - b.gap).slice(0, 3);
  const items = short.map((i) => {
    const fx = fixesFor(i.key);
    const tac = fx.tac[0], per = fx.per[0];
    const perGain = per ? (Math.min(1, Math.max(0, 1 + (per.after - i.demand) / 15)) - Math.min(1, Math.max(0, 1 + (per.before - i.demand) / 15))) / n : 0;
    const tacGain = tac ? tac.fit_to - tac.fit_from : 0;
    let action = `<span class="muted">No single change closes it.</span>`;
    if (tac && tacGain >= perGain) action = `<span>${esc(tac.text)} <small>plan fit ${pctFit(tac.fit_from)} → ${pctFit(tac.fit_to)}</small></span>${applyBtn(tac.change)}`;
    else if (per) action = `<span>Start ${esc(per.in)} for ${esc(per.out)} <small>${esc(i.label.toLowerCase())} ${per.before} → ${per.after}</small></span>${applyBtn({ type: "pin", slot: per.slot, id: per.in_id })}`;
    return `<li><div class="nt"><b>${esc(i.label)}</b> <span class="gap short">${Math.round(i.gap)}</span></div><p>${esc(i.note || "")}</p><div class="na">${action}</div></li>`;
  }).join("");
  const styles = (f.styles || []).slice(0, 3).map((x) => `<button type="button" class="stylechip ${x.style === r.style ? "on" : ""}" data-style="${x.style}">${esc(x.label)} <b>${pctFit(x.fit)}</b></button>`).join("");
  const strengths = s.indicators.filter((i) => i.kind === "strength");
  return `<section class="card notes-card">
    <h3>Coach's notes</h3>
    ${items ? `<ol class="notes">${items}</ol>` : `<p class="good">This eleven can carry the plan: no shortfall over 1.5 points.</p>`}
    <div class="suits"><span class="muted">Against this eleven's attributes, the styles that ask least of what it lacks:</span>${styles}</div>
    ${strengths.length ? `<p class="plays-to"><b>It plays to</b> ${strengths.map((i) => esc(i.label.toLowerCase())).join(", ")}.</p>` : ""}
  </section>`;
}

function renderBoard() {
  const r = board.result, s = r.seasons[board.season], o = board.opts;
  const st = o.styles[r.style];
  const cell = (p, t) => { const a = p.ratings[t], b = p.ratings[t + 1]; return `<td class="num">${Math.round(b)} <span class="${b - a >= 0.5 ? "up" : b - a <= -0.5 ? "down" : "muted"}">${b - a >= 0 ? "+" : ""}${(b - a).toFixed(1)}</span></td>`; };
  const advLabels = r.advanced.map((a) => (o.advanced.attack[a] || o.advanced.defence[a] || {}).label).filter(Boolean);
  $("#desk").innerHTML = `
    <section class="head board-head">
      <div>
        <h1>${esc(r.club)}</h1>
        <div class="facts">
          <span><b>${esc(st.label)}</b> <span class="muted">e.g. ${esc(st.ref)}${board.edited ? ", adapted" : ""}</span></span>
          <span><b>${esc(r.formation)}</b> → <b>${esc(s.shape_in)}</b> with the ball · <b>${esc(s.shape_out)}</b> without it</span>
        </div>
        <p class="idea">${esc(board.edited ? "Custom plan: " + [o.instructions.attack[r.plan.attack], o.instructions.build_up[r.plan.build_up] + " build-up",
          o.instructions.defence[r.plan.defence], "line " + r.plan.line + "/10", ...advLabels].join(" · ") : st.idea)}</p>
      </div>
      <div class="badges">
        <div class="badge"><div class="v">${Math.round(s.plan_fit * 100)}<small>%</small></div><div class="k">Plan fit</div><div class="d muted">${s.risks.length} risk${s.risks.length === 1 ? "" : "s"}</div></div>
        <div class="badge"><div class="v">${Math.round(r.load * 100)}<small>%</small></div><div class="k">Load</div><div class="d muted">how hard it runs them</div></div>
        <div class="badge"><div class="v">${s.team_level.toFixed(1)}</div><div class="k">The eleven</div><div class="d muted">${esc(s.season)}</div></div>
      </div>
    </section>
    <div class="board-grid">
      <section class="card pitch-card">
        <div class="board-tabs">
          <span class="tabs">${[["in", "With the ball"], ["out", "Without it"]].map(([k, l]) => `<button type="button" class="tab ${board.phase === k ? "on" : ""}" data-phase="${k}">${l}</button>`).join("")}</span>
          <span class="tabs">${r.seasons.map((x, i) => `<button type="button" class="tab ${i === board.season ? "on" : ""}" data-season="${i}">${esc(x.season)}</button>`).join("")}</span>
        </div>
        ${pitchBoard(s)}
        <p class="muted board-legend">${board.phase === "in"
          ? "Gold arrows: where each role takes him from the formation's spot. Thin lines: his nearest passing options. Green: rotations. Click a player to change his role or who plays there."
          : "Shaded: where the team starts to press. Dashed: the defensive line. The closed zone and arrows: where the block shows the opponent. Click a player to change his role or who plays there."}
          Colour: how well his attributes fit the role. Gold ring: out of position. Amber bar: minutes he can carry under this load.</p>
      </section>
      <div class="right-col">
        ${coachNotes(s)}
        <section class="card asks">
          <h3>What the plan asks of this eleven</h3>
          <p class="legend-line"><span class="lg have"></span> this eleven <span class="lg need"></span> what the plan asks <span class="lg band"></span> the big five's elevens, bottom to top tenth <span class="lg med"></span> their median. Open a line to see how it's measured, why the plan asks it, who carries it and how to fix it.</p>
          <div class="inds2">${indicatorRows(s)}</div>
        </section>
      </div>
    </div>
    <section class="card">
      <h3>Season by season</h3>
      <table><thead><tr><th>Season</th><th>The eleven</th><th>Plan fit</th><th>With the ball</th><th>Without it</th><th>Minutes to under-21s</th><th>Out of position</th></tr></thead><tbody>
        ${r.seasons.map((x) => `<tr><td class="num">${esc(x.season)}</td><td class="num">${x.team_level}</td><td class="num">${Math.round(x.plan_fit * 100)}%</td>
          <td class="num">${esc(x.shape_in)}</td><td class="num">${esc(x.shape_out)}</td><td class="num">${pct(x.youth_minutes)}</td>
          <td>${x.out_of_position.length ? esc(x.out_of_position.join(", ")) : `<span class="muted">none</span>`}</td></tr>`).join("")}
      </tbody></table>
      <p class="muted">Each season the coach picks his eleven from the grown squad. Minutes follow his picks and what the plan's load lets the starters carry; growth follows minutes (the calibrated engine's median). The plan changes who plays and how the minutes spread; it adds no growth by decree.</p>
    </section>
    <section class="card">
      <h3>The squad, developing</h3>
      <table class="market"><thead><tr><th>Player</th><th>Age</th><th>In the plan</th><th>Stamina</th><th>Now</th>${r.seasons.map((x) => `<th>${esc(x.season)}</th>`).join("")}<th>Minutes</th><th></th></tr></thead><tbody>
        ${r.players.map((p) => `<tr><td class="who"><strong>${esc(p.name)}</strong><small>${esc(p.position)}${p.added ? " · signing" : ""}</small></td>
          <td class="num">${p.age}</td><td>${p.plan_role ? esc(p.plan_role) : `<span class="muted">squad</span>`}</td><td class="num">${p.stamina ?? "–"}</td>
          <td class="num">${Math.round(p.ratings[0])}</td>${r.seasons.map((_, t) => cell(p, t)).join("")}
          <td class="num">${p.minutes.map((m) => Math.round(m * 100)).join(" · ")}%</td>
          <td>${p.added ? "" : `<button type="button" class="ghost x" data-remove="${p.ea_id}" data-name="${esc(p.name)}" title="Sell or release">×</button>`}</td></tr>`).join("")}
      </tbody></table>
    </section>`;
}

// ── Entering the board, the club changing ────────────────────────────────────
async function boardEnter() {
  await boardOptions();
  if (!board.ready) { boardRestore(); board.ready = true; }
  renderControls();
  labMoves();
  if (board.result && board.result.club === $("#our-club").value) renderBoard();
  else runBoard();
}

function boardClubChanged() {
  board.remove.clear(); board.pins = {}; board.open = null;
  labMoves();
  runBoard();
}

// ── Wiring ───────────────────────────────────────────────────────────────────
(() => {
  const f = $("#mode-lab");
  f.addEventListener("submit", (e) => { e.preventDefault(); runBoard(); });
  $("#lab-style").addEventListener("change", (e) => { applyStyle(e.target.value); renderControls(); boardSave(); board.open = null; runBoard(); });
  $("#lab-formation").addEventListener("change", (e) => { board.formation = e.target.value; board.roles = []; board.pins = {}; board.open = null; boardChanged(true); });
  $("#lab-reset").addEventListener("click", () => { applyStyle(board.style); renderControls(); boardSave(); board.open = null; runBoard(); });
  f.addEventListener("click", (e) => {
    const seg = e.target.closest("[data-seg]");
    if (seg) {
      board.plan[seg.dataset.seg] = seg.dataset.v;
      seg.parentElement.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === seg));
      boardChanged();
    }
    const adv = e.target.closest("[data-adv]");
    if (adv) {
      const k = adv.dataset.adv, ph = adv.dataset.ph, group = Object.keys(board.opts.advanced[ph]);
      if (board.advanced.includes(k)) board.advanced = board.advanced.filter((a) => a !== k);
      else {
        const mine = board.advanced.filter((a) => group.includes(a));
        if (mine.length >= 2) board.advanced = board.advanced.filter((a) => a !== mine[0]);   // two per phase, as in PES: the oldest gives way
        board.advanced.push(k);
      }
      renderControls();
      boardChanged();
    }
  });
  f.addEventListener("input", (e) => {
    const n = e.target.name;
    if (["support", "line", "compact"].includes(n)) { board.plan[n] = +e.target.value; setOut(n, e.target.value); boardChanged(); }
    if (["youth_trust", "rotation"].includes(n)) { setOut(n, e.target.value); boardChanged(); }
    if (n === "seasons") boardChanged(true);
  });
  // Transfers
  let t;
  $("#lab-q").addEventListener("input", (e) => {
    clearTimeout(t);
    const q = e.target.value;
    t = setTimeout(async () => {
      const list = $("#lab-results");
      if (q.trim().length < 2) { list.innerHTML = ""; return; }
      const rows = await api(`/api/scout/search?q=${encodeURIComponent(q)}&limit=6`);
      list.innerHTML = rows.map((r) => `<li data-sign="${r.ea_id}" data-name="${esc(r.name)}"><strong>${esc(r.name)}</strong><span class="ovr">${r.overall}</span><small>${r.age} · ${esc(r.position)} · ${esc(r.club_now)}</small></li>`).join("");
    }, 180);
  });
  document.addEventListener("click", (e) => {
    const sign = e.target.closest("li[data-sign]");
    if (sign) { board.add.set(+sign.dataset.sign, sign.dataset.name); $("#lab-results").innerHTML = ""; $("#lab-q").value = ""; labMoves(); runBoard(); }
    const undo = e.target.closest("[data-undo]");
    if (undo) { const [kind, id] = undo.dataset.undo.split(":"); (kind === "in" ? board.add : board.remove).delete(+id); labMoves(); runBoard(); }
  });
  // The desk: phase and season tabs, slot editing, sales.
  $("#desk").addEventListener("click", (e) => {
    if (!board.result || !$("#desk .board-grid")) return;
    const ph = e.target.closest("[data-phase]");
    if (ph) { board.phase = ph.dataset.phase; renderBoard(); return; }
    const se = e.target.closest(".tab[data-season]");
    if (se) { board.season = +se.dataset.season; renderBoard(); return; }
    if (e.target.closest("[data-close-slot]")) { board.open = null; renderBoard(); return; }
    const node = e.target.closest(".bnode");
    if (node) { board.open = board.open === +node.dataset.slot ? null : +node.dataset.slot; renderBoard(); return; }
    const rm = e.target.closest("[data-remove]");
    if (rm) { board.remove.set(+rm.dataset.remove, rm.dataset.name); labMoves(); runBoard(); }
  });
  $("#desk").addEventListener("toggle", (e) => {
    const d = e.target.closest && e.target.closest("details.ind2");
    if (d) (d.open ? board.openInd.add(d.dataset.key) : board.openInd.delete(d.dataset.key));
  }, true);
  $("#desk").addEventListener("click", (e) => {
    const b = e.target.closest("[data-apply]");
    if (b) {
      e.preventDefault();
      const a = JSON.parse(b.dataset.apply);
      if (a.type === "plan") board.plan[a.key] = a.value;
      if (a.type === "adv") board.advanced = board.advanced.filter((x) => x !== a.remove);
      if (a.type === "pin") board.pins[a.slot] = a.id;
      if (a.type === "sign") { board.add.set(a.id, a.name); board.pins[a.slot] = a.id; labMoves(); }
      renderControls();
      boardChanged(true);
      return;
    }
    const st = e.target.closest("[data-style]");
    if (st) { applyStyle(st.dataset.style); renderControls(); boardSave(); board.open = null; runBoard(); }
  });
  $("#desk").addEventListener("change", (e) => {
    const role = e.target.closest("[data-slot-role]");
    if (role) { board.roles[+role.dataset.slotRole] = role.value; boardChanged(true); }
    const pin = e.target.closest("[data-slot-pin]");
    if (pin) { const k = +pin.dataset.slotPin; if (pin.value) board.pins[k] = +pin.value; else delete board.pins[k]; boardChanged(true); }
  });
})();
