/* =========================================================
   One-Point Safety — History appendix
   Loads data/history.json, a hand-curated (not auto-generated)
   index of historical football oddities. See README.md for how
   to add an entry.
   ========================================================= */

const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let CATS = [], EVENTS = [];
const S = { cat: null, sort: "asc" };

async function loadJSON(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function boot() {
  let data;
  try {
    data = await loadJSON("data/history.json");
  } catch (err) {
    $("loading").textContent = "Couldn't load history data. Try again shortly.";
    return;
  }
  CATS = data.categories;
  EVENTS = data.events.slice().sort((a, b) => (a.year || 0) - (b.year || 0) || (a.date || "").localeCompare(b.date || ""));
  EVENTS.forEach((e, i) => { e._idx = i; });

  $("loading").hidden = true;
  $("layout").hidden = false;
  render();
}

function yearLabel(e) {
  return e.date ? `${e.date}` : `${e.year}`;
}

function catLabel(id) {
  const c = CATS.find(c => c.id === id);
  return c ? c.label : id;
}

function eventCard(e) {
  const disputed = e.disputed ? `<span class="disputed">Disputed details</span>` : "";
  return `<button class="ocard" data-e="${e._idx}">
    <span class="yearpill">${esc(yearLabel(e))}</span> ${disputed}
    <div class="tt">${esc(e.title)}</div>
    <div class="d">${esc(e.summary)}</div>
    <div class="m">${esc(catLabel(e.category))}${e.people && e.people.length ? ` · ${esc(e.people.join(", "))}` : ""}</div></button>`;
}

function render() {
  const counts = {};
  EVENTS.forEach(e => { counts[e.category] = (counts[e.category] || 0) + 1; });

  $("cats").innerHTML = CATS.map(c => {
    const n = counts[c.id] || 0;
    return `<button class="chip ${S.cat === c.id ? "on" : ""}" data-cat="${c.id}">${esc(c.label)} (${n})</button>`;
  }).join("");
  $("resetAll").hidden = !S.cat;

  let ev = S.cat ? EVENTS.filter(e => e.category === S.cat) : EVENTS;
  ev = ev.slice().sort((a, b) => S.sort === "asc"
    ? (a.year || 0) - (b.year || 0)
    : (b.year || 0) - (a.year || 0));

  $("evCount").textContent = `${ev.length} of ${EVENTS.length}`;
  $("sortDir").textContent = S.sort === "asc" ? "Oldest first" : "Newest first";
  $("feed").innerHTML = ev.length ? ev.map(eventCard).join("") : `<div class="empty">No events in this category.</div>`;
}

function openSheet(e) {
  const sources = (e.sources || []).map(s => `<a class="btn" target="_blank" rel="noopener" href="${esc(s.url)}">${esc(s.label || "Source")}</a>`).join("");
  $("sheet").innerHTML = `<button class="x" id="sheetClose" aria-label="Close">&times;</button>
    <span class="tier t1">${esc(yearLabel(e))}</span>${e.disputed ? ` <span class="tier t2">Disputed details</span>` : ""}
    <h3>${esc(e.title)}</h3><div class="d">${esc(e.summary)}</div>
    <dl class="kv">
      <dt>Category</dt><dd>${esc(catLabel(e.category))}</dd>
      ${e.people && e.people.length ? `<dt>People</dt><dd>${esc(e.people.join(", "))}</dd>` : ""}
      ${e.teams && e.teams.length ? `<dt>Teams</dt><dd>${esc(e.teams.join(", "))}</dd>` : ""}
    </dl>
    ${e.note ? `<div class="why"><b>Note:</b> ${esc(e.note)}</div>` : ""}
    <div class="actions">${sources}</div>
    ${e.sources && e.sources.length ? "" : `<div class="note">No source recorded for this entry yet.</div>`}`;
  $("sheet").classList.add("open"); $("scrim").classList.add("open");
}
function closeSheet() { $("sheet").classList.remove("open"); $("scrim").classList.remove("open"); }

document.addEventListener("click", ev => {
  const t = ev.target.closest("[data-cat],[data-e],#resetAll,#sortDir,#sheetClose,#scrim");
  if (!t) return;
  if (t.dataset.e !== undefined) { openSheet(EVENTS[+t.dataset.e]); return; }
  if (t.id === "sheetClose" || t.id === "scrim") { closeSheet(); return; }
  if (t.id === "sortDir") { S.sort = S.sort === "asc" ? "desc" : "asc"; render(); return; }
  if (t.id === "resetAll") { S.cat = null; render(); return; }
  if (t.dataset.cat) { S.cat = S.cat === t.dataset.cat ? null : t.dataset.cat; render(); return; }
});
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSheet(); });

boot();
