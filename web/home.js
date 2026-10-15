// Title screen: featured players, counts, the window's biggest moves.
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v == null ? "–" : v >= 1e6 ? `€${(v / 1e6).toFixed(v >= 1e7 ? 0 : 1)}m` : `€${Math.round(v / 1e3)}k`);
// Portraits come from FotMob's image CDN at runtime; nothing is stored in the repo.
const portrait = (id) => `https://images.fotmob.com/image_resources/playerimages/${id}.png`;

// The player stands on his card: a cut-out photo above a kit-coloured plate (FotMob's portrait inside the plate if there is no cut-out).
function card(p, place) {
  const parts = p.name.split(" "), last = parts.pop(), first = parts.join(" ");
  return `<a class="feat kit-${esc(p.kit)} ${place} ${p.render ? "" : "no-render"}" href="/scout#p=${p.ea_id}" aria-label="${esc(p.name)}, ${p.overall}, ${esc(p.club)}">
    <span class="floor" aria-hidden="true"></span>
    ${p.render ? `<img class="render" src="/media/player/${p.ea_id}.webp" alt="" decoding="async" onerror="this.closest('.feat').classList.add('no-render'); this.remove()">` : ""}
    <span class="card">
      <span class="top-row"><span class="ov">${p.overall}</span><span class="pos">${esc(p.position)}</span><span class="club">${esc(p.club)}</span></span>
      <img class="portrait" src="${portrait(p.fotmob_id)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">
      <span class="plate">
        ${first ? `<span class="first">${esc(first)}</span>` : ""}<span class="pname">${esc(last)}</span>
        <span class="line"><span>${p.age} · ${esc(p.nationality)}</span><span>peak <b>${Math.round(p.peak_p50)}</b>–${Math.round(p.peak_p90)}</span></span>
        <span class="line"><span>${esc(p.best_role || "")}</span><span>${eur(p.value_eur)}</span></span>
        ${p.plays_like ? `<span class="like">Plays like ${esc(p.plays_like)} at his peak</span>` : ""}
      </span></span></a>`;
}

async function load() {
  const d = await fetch("/api/home").then((r) => r.json());
  const [l, c, r] = d.featured;
  $("#featured").innerHTML = card(l, "left") + card(c, "centre") + card(r, "right");
  $("#stat-players").textContent = `${d.players.toLocaleString()} players · ${d.leagues} leagues`;
  $("#stat-moves").textContent = `${d.moves.toLocaleString()} moves this summer`;
  const items = d.ticker.map((t) => `<span><b>${esc(t.player)}</b> ${esc(t.from)} → ${esc(t.to)} <span class="fee">${eur(t.fee)}</span></span>`).join("");
  $("#ticker").innerHTML = items + items;      // twice, so the loop is seamless
  if (d.credits && d.credits.length) $("#credits").textContent = `Photos: ${d.credits.join("; ")}.`;
}

// Arrow keys move along the menu like a game menu.
document.addEventListener("keydown", (e) => {
  const tiles = [...document.querySelectorAll(".tile, .feat")];
  const i = tiles.indexOf(document.activeElement);
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    const next = i < 0 ? 0 : (i + (e.key === "ArrowRight" ? 1 : -1) + tiles.length) % tiles.length;
    tiles[next].focus();
    e.preventDefault();
  }
});
load();
