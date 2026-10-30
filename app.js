/* =========================================================
   One-Point Safety — front end
   Loads static JSON built by scripts/build_data.py from real
   nflverse play-by-play data. No build step, no frameworks.
   ========================================================= */

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const SHORT_DAY = { Sunday: "Sun", Monday: "Mon", Tuesday: "Tue", Wednesday: "Wed", Thursday: "Thu", Friday: "Fri", Saturday: "Sat" };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const TIER_RANK = { wild: 3, rare: 2, uncommon: 1 };
const TIER_NAME = { 3: "Wild", 2: "Rare", 1: "Uncommon" };
const LBL = { td: "Touchdown", fg: "Field goal", safety: "Safety", pen: "Penalty", inj: "Injury", int: "Interception", fum: "Fumble lost", sack: "Sack", odd: "Notable play" };
const METRICS = [
  ["td", "Touchdowns"], ["fg", "Field goals"], ["safety", "Safeties"], ["pen", "Penalties"],
  ["inj", "Injuries"], ["int", "Interceptions"], ["fum", "Fumbles lost"], ["sack", "Sacks"],
];

let META = null, GAMES = [], EVENTS = [], ODD = {}, RARE_PEN = {}, GAMES_BY_ID = new Map();
let FEED = [];
const S = { scope: "week", week: 1, day: "all", type: null, sub: null, odd: null, game: null, penSort: "count", logAll: false, feedAll: false };

/* ---------- Load ---------- */
async function loadJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function boot() {
  try {
    META = await loadJSON("data/meta.json");
  } catch (err) {
    $("loading").textContent = "Couldn't load season data. Try again shortly.";
    return;
  }
  if (!META.has_data) {
    $("loading").hidden = true;
    $("offseason").hidden = false;
    return;
  }
  const [games, events, oddities] = await Promise.all([
    loadJSON("data/games.json"),
    loadJSON("data/events.json"),
    loadJSON("data/oddities.json"),
  ]);
  GAMES = games;
  GAMES_BY_ID = new Map(GAMES.map(g => [g.id, g]));
  EVENTS = events;
  EVENTS.forEach(e => { e.g = GAMES_BY_ID.get(e.game_id) || null; });
  EVENTS = EVENTS.filter(e => e.g);

  oddities.oddities.forEach(o => { ODD[o.id] = o; });
  RARE_PEN = oddities.rare_penalties || {};

  S.week = META.current_week;
  $("loading").hidden = true;
  $("layout").hidden = false;
  $("updated").textContent = `Data updated ${fmtUpdated(META.last_updated)} · ${META.season} season`;

  render();
}

function fmtUpdated(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/* ---------- Helpers ---------- */
const gameName = g => `${g.away} @ ${g.home}`;
const clock = s => (s == null ? "—" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
const qShort = q => (q == null ? "" : q >= 5 ? (q === 5 ? "OT" : `OT${q - 4}`) : "Q" + q);
const qLong = q => (q == null ? "" : q >= 5 ? (q === 5 ? "Overtime" : `Overtime ${q - 4}`) : ["1st", "2nd", "3rd", "4th"][q - 1] + " quarter");

function parseDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}
function gDate(g) {
  const d = parseDate(g.date);
  return `${SHORT_DAY[g.weekday] || g.weekday} ${MON[d.getMonth()]} ${d.getDate()}`;
}
function finalScore(g) {
  return g.final ? `${g.away} ${g.away_score}, ${g.home} ${g.home_score}` : "Not yet final";
}

const gIn = g => S.scope === "season" || (g.week === S.week && (S.day === "all" || g.weekday === S.day));
const inScope = e => e.g && gIn(e.g) && (S.game === null || e.game_id === S.game);

function primaryOddityId(e) {
  if (!e.oddity_ids || !e.oddity_ids.length) return null;
  return e.oddity_ids.reduce((best, id) => {
    const t = TIER_RANK[ODD[id]?.tier] || 0;
    const bt = best == null ? -1 : TIER_RANK[ODD[best]?.tier] || 0;
    return t > bt ? id : best;
  }, null);
}
function titleOf(e) {
  const oid = primaryOddityId(e);
  if (oid && ODD[oid]) return ODD[oid].label;
  if (e.type === "pen") return e.penalty_type || "Penalty";
  return LBL[e.type] || "Notable play";
}
function whyOf(e) {
  const oid = primaryOddityId(e);
  if (oid && ODD[oid]) return ODD[oid].why;
  if (e.rare_penalty) return (RARE_PEN[e.penalty_type] && RARE_PEN[e.penalty_type].why) || "Called only a handful of times this season.";
  return "";
}
function rarityOf(e) {
  const oid = primaryOddityId(e);
  const r = oid ? ODD[oid].rarity : e.rare_penalty ? RARE_PEN[e.penalty_type]?.rarity : null;
  if (!r) return "Historical rarity not calculated yet.";
  return `About ${r.avg_per_season} per season (last ${r.seasons} seasons)`;
}

/* ---------- Header ---------- */
function renderHeader() {
  const gs = GAMES.filter(gIn);
  $("scope").innerHTML = ["season", "week"].map(k =>
    `<button data-scope="${k}" class="${S.scope === k ? "on" : ""}">${k === "season" ? "Season" : "Week"}</button>`
  ).join("");
  const weeks = META.weeks;
  const minWk = weeks[0], maxWk = weeks[weeks.length - 1];
  $("wknav").hidden = $("days").hidden = S.scope !== "week";
  $("wkLabel").textContent = `Week ${S.week}`;
  $("wkPrev").disabled = S.week <= minWk;
  $("wkNext").disabled = S.week >= maxWk;

  const daysPresent = [];
  GAMES.filter(g => g.week === S.week).forEach(g => { if (!daysPresent.includes(g.weekday)) daysPresent.push(g.weekday); });
  $("days").innerHTML = [["all", "All days"], ...daysPresent.map(w => [w, SHORT_DAY[w] || w])]
    .map(([k, l]) => `<button class="chip ${S.day === k ? "on" : ""}" data-day="${esc(k)}">${esc(l)}</button>`).join("");

  if (S.scope === "season") {
    $("subline").textContent = `${META.season} season · Weeks ${minWk}–${maxWk} · ${GAMES.length} games`;
  } else {
    const wk = GAMES.filter(g => g.week === S.week);
    const first = wk[0], last = wk[wk.length - 1];
    $("subline").textContent = first ? `Week ${S.week} · ${gDate(first)} – ${gDate(last)} · ${gs.length} games` : `Week ${S.week}`;
  }
}

/* ---------- Oddities ---------- */
function oddCard(e) {
  const t = e.tier || 0;
  return `<button class="ocard k${t}" data-e="${e.id}">
    <span class="tier t${t}">${TIER_NAME[t] || ""}</span>
    <div class="tt">${esc(titleOf(e))}</div>
    <div class="d">${esc(e.detail)}</div>
    <div class="m">${gameName(e.g)} · ${gDate(e.g)} · ${qShort(e.qtr)} ${clock(e.clock_secs)} · ${esc(e.team || "")}</div></button>`;
}

function renderOdd() {
  const ev = EVENTS.filter(e => inScope(e) && (e.oddity_ids.length || e.rare_penalty));
  const counts = {};
  ev.forEach(e => {
    e.oddity_ids.forEach(id => { counts[id] = (counts[id] || 0) + 1; });
    if (e.rare_penalty) counts.rare_pen = (counts.rare_pen || 0) + 1;
  });
  const tierOfKey = k => (k === "rare_pen" ? 2 : TIER_RANK[ODD[k].tier]);
  const labelOfKey = k => (k === "rare_pen" ? "Rare penalties" : ODD[k].label);
  const keys = Object.keys(counts).sort((a, b) => tierOfKey(b) - tierOfKey(a) || counts[a] - counts[b]);

  $("oddCount").textContent = `${ev.length} this ${S.scope === "season" ? "season" : S.day === "all" ? "week" : "day"}`;
  $("board").innerHTML = keys.map(k => {
    const t = tierOfKey(k);
    return `<button class="tile k${t} ${S.odd === k ? "on" : ""}" data-o="${k}"><span class="tier t${t}">${TIER_NAME[t]}</span>
      <div class="n">${counts[k]}</div><div class="l">${esc(labelOfKey(k))}</div></button>`;
  }).join("");

  FEED = ev.filter(e => !S.odd || (S.odd === "rare_pen" ? e.rare_penalty : e.oddity_ids.includes(S.odd)))
    .sort((a, b) => (b.tier || 0) - (a.tier || 0) || b.id - a.id);
  const lim = S.feedAll ? FEED.length : 6;
  $("feed").innerHTML = FEED.length ? FEED.slice(0, lim).map(oddCard).join("") : `<div class="empty">No oddities in this period yet.</div>`;
  $("moreFeed").hidden = FEED.length <= 6;
  $("moreFeed").textContent = S.feedAll ? "Show fewer" : `Show all ${FEED.length}`;

  renderWaiting();
}

function renderWaiting() {
  const waiting = Object.values(ODD).filter(o => o.tier === "wild" && o.count === 0).map(o => o.label);
  $("waiting").textContent = waiting.length ? `Still waiting on: ${waiting.join(", ")}` : "";
}

let tvIdx = 0;
function renderFeatured() {
  if (!FEED.length) { $("featured").innerHTML = ""; return; }
  const e = FEED[tvIdx % FEED.length], t = e.tier || 0;
  $("featured").innerHTML = `<div class="big k${t}"><span class="tier t${t}">${TIER_NAME[t]} · ${tvIdx % FEED.length + 1} of ${FEED.length}</span>
    <div class="tt">${esc(titleOf(e))}</div><div class="d">${esc(e.detail)}</div>
    <div class="m">${gameName(e.g)} · ${gDate(e.g)} · ${qLong(e.qtr)}, ${clock(e.clock_secs)} · ${esc(e.team || "")}</div></div>`;
}

/* ---------- Totals ---------- */
function renderCards() {
  const ev = EVENTS.filter(inScope);
  const nG = S.game !== null ? 1 : (GAMES.filter(gIn).length || 1);
  $("cards").innerHTML = METRICS.map(([k, label]) => {
    const n = ev.filter(e => e.type === k).length;
    return `<button class="card ${S.type === k && !S.sub ? "on" : ""}" data-t="${k}"><div class="n">${n}</div>
      <div class="l">${label}</div><div class="a">${(n / nG).toFixed(1)} per game</div></button>`;
  }).join("");
}

/* ---------- Penalties ---------- */
function renderPen() {
  const counts = {};
  EVENTS.filter(e => inScope(e) && e.type === "pen").forEach(e => {
    const p = e.penalty_type || "Penalty (unspecified)";
    counts[p] = (counts[p] || 0) + 1;
  });
  const rows = Object.entries(counts).sort((a, b) => {
    if (S.penSort !== "rare") return b[1] - a[1];
    const ra = RARE_PEN[a[0]]?.rarity?.avg_per_season, rb = RARE_PEN[b[0]]?.rarity?.avg_per_season;
    if (ra != null && rb != null) return ra - rb;
    return a[1] - b[1];
  });
  const max = Math.max(1, ...rows.map(r => r[1]));
  $("penSort").textContent = S.penSort === "rare" ? "Sort: rarest first" : "Sort: most called";
  $("penalties").innerHTML = rows.length ? rows.map(([p, n]) => `
    <div class="pen ${S.sub === p ? "on" : ""}" data-s="${esc(p)}"><div><div class="name">${esc(p)}${RARE_PEN[p] ? '<span class="rare">RARE</span>' : ""}</div>
    <div class="bar" style="width:${Math.max(3, n / max * 100)}%"></div></div><div class="ct">${n}</div></div>`).join("")
    : `<div class="empty">No penalties in this period.</div>`;
}

/* ---------- Play log ---------- */
function renderLog() {
  let ev = EVENTS.filter(inScope);
  if (S.type) ev = ev.filter(e => e.type === S.type);
  if (S.sub) ev = ev.filter(e => e.penalty_type === S.sub);
  if (S.odd) ev = ev.filter(e => (S.odd === "rare_pen" ? e.rare_penalty : e.oddity_ids.includes(S.odd)));

  const tags = [];
  if (S.odd) tags.push(S.odd === "rare_pen" ? "Rare penalties" : ODD[S.odd].label);
  else if (S.type) tags.push(S.sub || METRICS.find(m => m[0] === S.type)[1]);
  if (S.game !== null) { const g = GAMES_BY_ID.get(S.game); if (g) tags.push(gameName(g)); }

  $("filterbar").innerHTML = `${ev.length} plays${tags.map(t => ` <span class="tag">${esc(t)}</span>`).join("")}${tags.length ? ` <button class="linkbtn" id="clear">Clear</button>` : " · tap any total, oddity, penalty or game to filter"}`;
  const lim = S.logAll ? ev.length : 40;
  $("log").innerHTML = ev.slice(0, lim).map(e => {
    const t = e.tier || 0;
    return `<button class="row" data-e="${e.id}"><div class="a">${t ? `<span class="dot k${t}"></span>` : ""}<b>${esc(titleOf(e))}</b> · ${esc(e.detail)}</div>
      <div class="b">${gDate(e.g)} · ${gameName(e.g)} · ${qShort(e.qtr)} ${clock(e.clock_secs)} · ${esc(e.team || "")}${e.injury ? " · approximate" : ""}</div></button>`;
  }).join("");
  $("moreLog").hidden = ev.length <= lim;
}

/* ---------- Games ---------- */
function renderGames() {
  const weird = gid => EVENTS.filter(e => e.game_id === gid).reduce((s, e) => s + (e.tier || 0), 0);
  let gs;
  if (S.scope === "season") {
    gs = GAMES.map(g => [g, weird(g.id)]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, 10).map(x => x[0]);
    $("gamesTitle").textContent = "Weirdest games of the season";
  } else {
    gs = GAMES.filter(gIn);
    $("gamesTitle").textContent = "Games";
  }
  $("games").innerHTML = gs.map(g => {
    const ev = EVENTS.filter(e => e.game_id === g.id);
    const c = t => ev.filter(e => e.type === t).length;
    const odd = ev.filter(e => e.tier);
    const top = Math.max(0, ...odd.map(e => e.tier || 0));
    const score = g.final ? `${g.away_score}–${g.home_score}` : "—";
    return `<button class="game ${S.game === g.id ? "on" : ""}" data-g="${esc(g.id)}"><div class="teams">${gameName(g)}</div>
      <div class="score">${score}</div>
      <div class="meta">${S.scope === "season" ? `Wk ${g.week} · ` : ""}${gDate(g)} · ${g.kickoff_ct || ""} · ${c("td")} TD · ${c("pen")} flags${odd.length ? ` · <span class="tier t${top}">${odd.length} odd</span>` : ""}</div></button>`;
  }).join("");
}

function render() {
  renderHeader(); renderOdd(); renderCards(); renderPen(); renderLog(); renderGames();
  if (document.body.classList.contains("tv")) renderFeatured();
}

/* ---------- Detail sheet ---------- */
function openSheet(e) {
  const g = e.g, t = e.tier || 0, why = whyOf(e), d = parseDate(g.date);
  const title = titleOf(e);
  const q = `${g.away} vs ${g.home} ${title} ${MON[d.getMonth()]} ${d.getDate()} ${d.getFullYear()}`;
  $("sheet").innerHTML = `<button class="x" id="sheetClose" aria-label="Close">×</button>
    <span class="tier ${t ? "t" + t : ""}">${t ? TIER_NAME[t] : LBL[e.type]}</span>
    <h3>${esc(title)}</h3><div class="d">${esc(e.detail)}</div>
    <dl class="kv">
      <dt>Game</dt><dd>${gameName(g)} · final ${esc(finalScore(g))}</dd>
      <dt>Date</dt><dd>${gDate(g)} · Week ${g.week} · kickoff ${g.kickoff_ct || "TBD"}</dd>
      <dt>When</dt><dd>${qLong(e.qtr)}${e.clock_secs != null ? `, ${clock(e.clock_secs)} left` : ""}</dd>
      <dt>Team</dt><dd>${esc(e.team || "—")}</dd>
      ${e.players.length ? `<dt>Players</dt><dd>${esc(e.players.join(", "))}</dd>` : ""}
      ${t ? `<dt>How rare</dt><dd>${esc(rarityOf(e))}</dd>` : ""}
    </dl>
    ${why ? `<div class="why"><b>Why it's odd:</b> ${esc(why)}</div>` : ""}
    <div class="actions">
      <a class="btn pri" target="_blank" rel="noopener" href="https://www.google.com/search?q=${encodeURIComponent(q + " replay")}">Find the replay</a>
      <a class="btn" target="_blank" rel="noopener" href="https://www.youtube.com/results?search_query=${encodeURIComponent(q)}">Search YouTube</a>
      <button class="btn" data-gf="${esc(g.id)}">All plays this game</button>
    </div>
    ${e.injury ? `<div class="note">Injury plays are detected from play text and may be approximate.</div>` : ""}`;
  $("sheet").classList.add("open"); $("scrim").classList.add("open");
}
function closeSheet() { $("sheet").classList.remove("open"); $("scrim").classList.remove("open"); }

/* ---------- Big-screen mode ---------- */
let tvTimer = null;
function setTV(on) {
  document.body.classList.toggle("tv", on); document.documentElement.classList.toggle("tvroot", on);
  $("tvBtn").textContent = on ? "Exit big screen" : "Big screen";
  clearInterval(tvTimer);
  if (on) {
    try { const p = document.documentElement.requestFullscreen && document.documentElement.requestFullscreen(); if (p && p.catch) p.catch(() => {}); } catch (err) {}
    tvIdx = 0; renderFeatured(); tvTimer = setInterval(() => { tvIdx++; renderFeatured(); }, 8000);
  } else if (document.fullscreenElement) { document.exitFullscreen().catch(() => {}); }
}
document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && document.body.classList.contains("tv")) setTV(false); });

/* ---------- Clicks ---------- */
document.addEventListener("click", ev => {
  const t = ev.target.closest("[data-scope],[data-wk],[data-day],[data-t],[data-s],[data-g],[data-o],[data-e],[data-gf],#clear,#moreLog,#moreFeed,#penSort,#tvBtn,#sheetClose,#scrim");
  if (!t) return;
  const d = t.dataset;
  if (d.e !== undefined) { openSheet(EVENTS[+d.e]); return; }
  if (t.id === "sheetClose" || t.id === "scrim") { closeSheet(); return; }
  if (t.id === "tvBtn") { setTV(!document.body.classList.contains("tv")); return; }
  if (t.id === "moreLog") { S.logAll = true; renderLog(); return; }
  if (t.id === "moreFeed") { S.feedAll = !S.feedAll; renderOdd(); return; }
  if (t.id === "penSort") { S.penSort = S.penSort === "count" ? "rare" : "count"; renderPen(); return; }
  if (d.gf !== undefined) {
    S.game = d.gf; S.type = S.sub = S.odd = null;
    const g = GAMES_BY_ID.get(S.game);
    if (g && !gIn(g)) { S.scope = "week"; S.week = g.week; S.day = "all"; }
    closeSheet(); S.logAll = false; render(); $("logSec").scrollIntoView({ behavior: "smooth" }); return;
  }
  if (d.scope) { S.scope = d.scope; S.game = null; }
  else if (d.wk) { S.week = Math.min(META.weeks[META.weeks.length - 1], Math.max(META.weeks[0], S.week + +d.wk)); S.day = "all"; S.game = null; }
  else if (d.day) { S.day = d.day; if (S.game !== null) { const g = GAMES_BY_ID.get(S.game); if (g && !gIn(g)) S.game = null; } }
  else if (d.t) { const same = S.type === d.t && !S.sub; S.type = same ? null : d.t; S.sub = null; S.odd = null; }
  else if (d.s) { const same = S.sub === d.s; S.type = same ? null : "pen"; S.sub = same ? null : d.s; S.odd = null; }
  else if (d.o) { S.odd = S.odd === d.o ? null : d.o; S.type = S.sub = null; }
  else if (d.g !== undefined) { S.game = S.game === d.g ? null : d.g; }
  else if (t.id === "clear") { S.type = S.sub = S.odd = null; S.game = null; }
  S.logAll = false; S.feedAll = false; tvIdx = 0;
  render();
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSheet(); });

boot();
