/* India Flight Status + live map */
"use strict";

const el = (id) => document.getElementById(id);

/* ================================================================
 *  STATUS BOARD  (default screen: live status of every domestic flight)
 * ================================================================ */

const PHASE_COLORS = {
  "On ground": "#94a3b8",
  Departed: "#fb923c",
  "On approach": "#38bdf8",
  "En route": "#4ade80",
  Airborne: "#4ade80",
};
const PHASE_ORDER = { "On approach": 0, Departed: 1, "En route": 2, Airborne: 2, "On ground": 3 };

const boardExpanded = new Set();
let feedTs = 0;
let feedLive = false;
let statusFallback = null; // {query, data} from /api/status when the filter matched no live row

function fmt(n, s = "") {
  return n == null ? "—" : Number(n).toLocaleString() + s;
}
function ago(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  return Math.round(s / 3600) + " h ago";
}
function since(ts) {
  if (!ts) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 3600) return Math.round(s / 60) + " min";
  return Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}

function boardMatch(f, q) {
  if (!q) return true;
  return [f.flight_no, f.callsign, f.airline, f.registration, f.dep, f.arr, f.phase]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
    .includes(q);
}

function sortBoard(list, mode) {
  const fno = (a, b) => (a.flight_no || "").localeCompare(b.flight_no || "", undefined, { numeric: true });
  const cmp = {
    status: (a, b) => ((PHASE_ORDER[a.phase] ?? 9) - (PHASE_ORDER[b.phase] ?? 9)) || fno(a, b),
    flight: fno,
    alt: (a, b) => (b.alt_ft || 0) - (a.alt_ft || 0),
    airline: (a, b) => (a.airline || "").localeCompare(b.airline || "") || fno(a, b),
  };
  return list.slice().sort(cmp[mode] || cmp.status);
}

function bxCell(k, v) {
  return `<div><span class="k">${k}</span><span class="v mono">${v}</span></div>`;
}

function boardRow(f) {
  const c = PHASE_COLORS[f.phase] || "#4ade80";
  const dep = f.dep || "•••";
  const arr = f.arr || "•••";
  const open = boardExpanded.has(f.hex);
  const exp = open
    ? `<div class="brow-exp">
         <div class="bx-grid">
           ${bxCell("Altitude", fmt(f.alt_ft, " ft"))}
           ${bxCell("Ground speed", fmt(f.gs_kt, " kt"))}
           ${bxCell("Vertical rate", fmt(f.vs_fpm, " fpm"))}
           ${bxCell("Heading", f.track_deg == null ? "—" : f.track_deg + "°")}
           ${bxCell("Position", f.lat != null ? f.lat + ", " + f.lon : "—")}
           ${bxCell("Callsign", f.callsign || "—")}
           ${bxCell("Registration", f.registration || "—")}
           ${bxCell("Tracked for", since(f.first_seen))}
         </div>
         <button class="sv-track" data-track="${f.hex}">Track on map →</button>
       </div>`
    : "";
  return `<div class="brow${open ? " x" : ""}" data-hex="${f.hex}">
    <button class="brow-main">
      <span class="bf">
        <span class="bf-no">${f.flight_no || f.callsign || f.hex}</span>
        <span class="bf-sub">${[f.airline, f.type].filter(Boolean).join(" · ")}</span>
      </span>
      <span class="bbadge" style="--c:${c}">${f.phase}</span>
      <span class="broute mono">${dep}<span class="ar">→</span>${arr}</span>
      <span class="bpos">${f.phase_detail}${f.near ? " · " + f.near : ""}</span>
    </button>${exp}
  </div>`;
}

function renderBoard() {
  const listEl = el("board-list");
  if (!listEl) return;
  const q = (el("board-filter").value || "").trim().toLowerCase();
  const rows = sortBoard(state.flights.filter((f) => boardMatch(f, q)), el("board-sort").value);

  el("board-count").textContent = state.flights.length;
  el("board-air").textContent = state.flights.filter((f) => (f.alt_ft || 0) > 1000).length;
  updateBoardAge();
  const conn = el("board-conn");
  conn.textContent = feedLive ? "● live" : "connecting…";
  conn.className = "board-conn" + (feedLive ? " ok" : "");

  const keep = listEl.scrollTop;
  if (rows.length) {
    listEl.innerHTML = rows.map(boardRow).join("");
    statusFallback = null;
  } else if (q) {
    const canLookup = /^[0-9a-z]{2}[0-9a-z]*\d/i.test(q);
    listEl.innerHTML =
      `<div class="board-empty">No flight in the live list matches “${q}”.` +
      (canLookup ? ` <button id="board-lookup">Look it up anyway &rarr;</button>` : "") +
      (statusFallback && statusFallback.query === q ? renderFallback(statusFallback.data) : "") +
      `</div>`;
    const lk = el("board-lookup");
    if (lk) lk.addEventListener("click", () => lookupOne(q));
  } else {
    listEl.innerHTML = `<div class="board-empty">Waiting for the feed…</div>`;
  }
  listEl.scrollTop = keep;
}

function updateBoardAge() {
  const e = el("board-upd");
  if (e) e.textContent = feedTs ? "updated " + ago(Math.round(feedTs / 1000)) : "—";
}
setInterval(updateBoardAge, 1000);

async function lookupOne(q) {
  try {
    const d = await (await fetch("/api/status/" + encodeURIComponent(q))).json();
    statusFallback = { query: q.toLowerCase(), data: d };
    renderBoard();
  } catch {}
}
function renderFallback(d) {
  if (d.found) {
    return `<div class="board-fallback">
      <b>${d.flight_no}</b> · ${d.airline || "?"} · ${d.status} · ${d.detail}${d.near ? " · " + d.near : ""}
      <button class="sv-track" onclick="trackOnMap('${d.hex}')">Track on map &rarr;</button>
    </div>`;
  }
  return `<div class="board-fallback err">${d.reason}</div>`;
}

el("board-filter").addEventListener("input", renderBoard);
el("board-sort").addEventListener("change", renderBoard);
el("board-list").addEventListener("click", (e) => {
  const track = e.target.closest("[data-track]");
  if (track) {
    trackOnMap(track.dataset.track);
    return;
  }
  const row = e.target.closest(".brow");
  if (!row) return;
  const hex = row.dataset.hex;
  boardExpanded.has(hex) ? boardExpanded.delete(hex) : boardExpanded.add(hex);
  renderBoard();
});

/* view switching */
function showView(name) {
  const status = name !== "map";
  el("status-view").classList.toggle("hidden", !status);
  el("map-view").classList.toggle("hidden", status);
  if (!status) {
    ensureMap();
    if (map) setTimeout(() => map.resize(), 40);
  }
}
document.querySelectorAll(".view-switch").forEach((b) =>
  b.addEventListener("click", () => showView(b.dataset.goto))
);

async function trackOnMap(hex) {
  showView("map");
  ensureMap();
  // wait until the map has drawn this aircraft, then select + fly to it
  for (let i = 0; i < 40 && !(planes && planes.get(hex)); i++) {
    await new Promise((r) => setTimeout(r, 250));
  }
  if (planes && planes.get(hex)) selectFlight(hex);
}

/* ================================================================
 *  MAP VIEW  (lazy: only built the first time it is shown)
 * ================================================================ */

const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const STADIA = "https://tiles.stadiamaps.com/tiles";
const OMT_ATTR =
  '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> ' +
  '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> ' +
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

function rasterStyle(layers, bg) {
  const sources = {};
  const lyrs = [{ id: "bg", type: "background", paint: { "background-color": bg } }];
  layers.forEach((l, i) => {
    sources["s" + i] = {
      type: "raster",
      tiles: [l.url],
      tileSize: l.tileSize || 256,
      maxzoom: l.maxzoom || 19,
      attribution: l.attribution || "",
    };
    lyrs.push({ id: "s" + i, type: "raster", source: "s" + i });
  });
  return { version: 8, sources, layers: lyrs };
}

const STYLES = {
  dark: rasterStyle(
    [{ url: STADIA + "/alidade_smooth_dark/{z}/{x}/{y}@2x.png", tileSize: 256, maxzoom: 20, attribution: OMT_ATTR }],
    "#0a0c12"
  ),
  light: rasterStyle(
    [{ url: STADIA + "/alidade_smooth/{z}/{x}/{y}@2x.png", tileSize: 256, maxzoom: 20, attribution: OMT_ATTR }],
    "#eceff3"
  ),
  satellite: rasterStyle(
    [
      { url: ESRI + "/World_Imagery/MapServer/tile/{z}/{y}/{x}", maxzoom: 19, attribution: "Imagery &copy; Esri" },
      { url: ESRI + "/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", maxzoom: 19 },
    ],
    "#0a0c12"
  ),
};

const ALT_BANDS = [
  [0, "#94a3b8"], [3000, "#f87171"], [10000, "#fb923c"],
  [20000, "#fbbf24"], [30000, "#4ade80"], [38000, "#38bdf8"],
];
function bandColor(alt) {
  let c = ALT_BANDS[0][1];
  for (const [floor, col] of ALT_BANDS) if ((alt || 0) >= floor) c = col;
  return c;
}

const SILHOUETTE = {
  jet:
    "M16 1.5 C17 1.7 17.6 3.3 17.7 5.6 L17.9 12.8 L30.6 19.2 L31 20.6 L18 18.8 " +
    "L18.1 25.4 L21.8 28.4 L22.1 29.5 L17.2 29.4 L16 30.8 L14.8 29.4 L9.9 29.5 " +
    "L10.2 28.4 L13.9 25.4 L14 18.8 L1 20.6 L1.4 19.2 L14.1 12.8 L14.3 5.6 " +
    "C14.4 3.3 15 1.7 16 1.5 Z",
  prop:
    "M16 3 C16.8 3.2 17.3 4.4 17.4 6.2 L17.5 13 L29 14.6 L29.3 16 L17.5 16.2 " +
    "L17.6 24.2 L20.9 27 L21.1 28.1 L16 27.7 L10.9 28.1 L11.1 27 L14.4 24.2 " +
    "L14.5 16.2 L2.7 16 L3 14.6 L14.5 13 L14.6 6.2 C14.7 4.4 15.2 3.2 16 3 Z",
};
const CAT_SIZE = { prop: 22, jet: 27, heavy: 33 };
function category(type) {
  const t = (type || "").toUpperCase();
  if (/^(AT[0-9]|AT4|AT7|DH8|SF3|J41|E12|E19|B19|C208|D22|L410|BE[0-9])/.test(t)) return "prop";
  if (/^(B74|B75|B76|B77|B78|A30|A31|A33|A34|A35|A38|IL9|MD1|DC1)/.test(t)) return "heavy";
  return "jet";
}

const state = { flights: [], selected: null, detailTimer: null, airlineKeys: "", basemap: "dark" };
const planes = new Map();
let map = null;
let mapReady = false;
let mapInited = false;

function ensureMap() {
  if (mapReady) return;
  mapReady = true;

  map = new maplibregl.Map({
    container: "map",
    style: STYLES.dark,
    center: [80.9, 22.6],
    zoom: 4.4,
    minZoom: 3,
    maxBounds: [[58, 2], [102, 40]],
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  map.on("error", (e) => console.warn("map error:", e && e.error));

  document.querySelectorAll("#basemap button").forEach((b) =>
    b.addEventListener("click", () => setBasemap(b.dataset.style))
  );

  const start = async () => {
    if (mapInited) return;
    mapInited = true;
    initOverlay();
    map.on("click", closeDetail);
    try {
      const a = await (await fetch("/api/airports")).json();
      airports = a.airports || [];
    } catch {}
    render(); // draw markers from whatever the feed already has
    drawOverlay();
  };
  // don't gate on the basemap style loading -- markers, the overlay and the feed
  // work from the map's initial transform alone.
  map.on("load", start);
  setTimeout(start, 400);
}

/* ---- plane markers with smooth dead-reckoned motion ---- */

function planeElement(cat) {
  const d = document.createElement("div");
  d.className = "plane";
  const s = CAT_SIZE[cat];
  d.innerHTML =
    `<svg viewBox="0 0 32 32" width="${s}" height="${s}" aria-hidden="true">` +
    `<path d="${SILHOUETTE[cat === "prop" ? "prop" : "jet"]}" fill="currentColor" ` +
    `stroke="#fff" stroke-opacity="0.92" stroke-width="1.15" ` +
    `stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  return d;
}

function upsertPlane(f) {
  const now = Date.now();
  let p = planes.get(f.hex);
  const cat = category(f.type);
  if (!p) {
    const elm = planeElement(cat);
    elm.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectFlight(f.hex);
    });
    const marker = new maplibregl.Marker({ element: elm, rotationAlignment: "map" })
      .setLngLat([f.lon, f.lat])
      .addTo(map);
    p = { marker, cat, disp: [f.lat, f.lon] };
    planes.set(f.hex, p);
  } else if (cat !== p.cat) {
    p.cat = cat;
    const s = CAT_SIZE[cat];
    const svg = p.marker.getElement().querySelector("svg");
    svg.setAttribute("width", s);
    svg.setAttribute("height", s);
    svg.querySelector("path").setAttribute("d", SILHOUETTE[cat === "prop" ? "prop" : "jet"]);
  }
  p.prevDisp = p.disp ? p.disp.slice() : null;
  p.easeStart = now;
  p.anchorTs = now;
  p.f = f;
  p.marker.setRotation(f.track_deg == null ? 0 : f.track_deg);
  const elm = p.marker.getElement();
  elm.style.color = bandColor(f.alt_ft);
  elm.classList.toggle("sel", f.hex === state.selected);
}

function deadReckon(f, ageSec) {
  const gs = f.gs_kt || 0;
  if (!gs || f.track_deg == null) return [f.lat, f.lon];
  const trk = (f.track_deg * Math.PI) / 180;
  const nm = (gs * ageSec) / 3600;
  const dLat = (nm / 60) * Math.cos(trk);
  const dLon = ((nm / 60) * Math.sin(trk)) / Math.cos((f.lat * Math.PI) / 180);
  return [f.lat + dLat, f.lon + dLon];
}

function tick() {
  if (!map) return;
  const now = Date.now();
  for (const p of planes.values()) {
    if (!p.f) continue;
    const age = Math.min(25, (now - p.anchorTs) / 1000);
    let [la, lo] = deadReckon(p.f, age);
    if (p.prevDisp) {
      const k = Math.min(1, (now - p.easeStart) / 700);
      la = p.prevDisp[0] + (la - p.prevDisp[0]) * k;
      lo = p.prevDisp[1] + (lo - p.prevDisp[1]) * k;
      if (k >= 1) p.prevDisp = null;
    }
    p.disp = [la, lo];
    p.marker.setLngLat([lo, la]);
  }
}
setInterval(tick, 50);

/* ---- canvas overlay: airports + selected-flight trail ---- */

const overlay = { canvas: null, ctx: null };
let airports = [];
const trailCoords = { list: [] };

function initOverlay() {
  const c = document.createElement("canvas");
  c.id = "overlay-canvas";
  el("map").appendChild(c);
  overlay.canvas = c;
  overlay.ctx = c.getContext("2d");
  sizeOverlay();
  map.on("move", drawOverlay);
  map.on("resize", () => {
    sizeOverlay();
    drawOverlay();
  });
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      sizeOverlay();
      drawOverlay();
    }).observe(el("map"));
  }
}

function sizeOverlay() {
  const r = el("map").getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  overlay.canvas.width = r.width * dpr;
  overlay.canvas.height = r.height * dpr;
  overlay.canvas.style.width = r.width + "px";
  overlay.canvas.style.height = r.height + "px";
  overlay.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawOverlay() {
  const ctx = overlay.ctx;
  if (!ctx) return;
  const r = el("map").getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return;
  ctx.clearRect(0, 0, r.width, r.height);
  const z = map.getZoom();
  const dark = state.basemap !== "light";

  const dot = dark ? "rgba(255,255,255,0.30)" : "rgba(20,30,50,0.45)";
  ctx.fillStyle = dot;
  for (const a of airports) {
    const p = map.project([a.lon, a.lat]);
    if (p.x < -30 || p.y < -30 || p.x > r.width + 30 || p.y > r.height + 30) continue;
    ctx.beginPath();
    ctx.arc(p.x, p.y, z >= 6 ? 3 : 2, 0, Math.PI * 2);
    ctx.fill();
    if (z >= 6.5) {
      ctx.fillStyle = dark ? "rgba(226,232,246,0.65)" : "rgba(20,30,50,0.7)";
      ctx.font = "10px ui-monospace, Consolas, monospace";
      ctx.fillText(a.iata, p.x + 5, p.y + 3);
      ctx.fillStyle = dot;
    }
  }

  const c = trailCoords.list;
  if (c.length >= 2) {
    for (let i = 1; i < c.length; i++) {
      const p0 = map.project(c[i - 1]);
      const p1 = map.project(c[i]);
      const t = i / c.length;
      ctx.beginPath();
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
      ctx.strokeStyle = `rgba(56,189,248,${(0.08 + 0.85 * t).toFixed(3)})`;
      ctx.lineWidth = 1.3 + 1.6 * t;
      ctx.lineCap = "round";
      ctx.stroke();
    }
  }
}

/* ---- basemap switcher ---- */
function setBasemap(name) {
  if (!STYLES[name] || name === state.basemap) return;
  state.basemap = name;
  map.setStyle(STYLES[name]);
  document.querySelectorAll("#basemap button").forEach((b) =>
    b.classList.toggle("active", b.dataset.style === name)
  );
  map.once("styledata", () => setTimeout(drawOverlay, 60));
}

/* ---- live feed (runs on page load; the board and the map both consume it) ---- */

async function connectFeed() {
  try {
    const j = await (await fetch("/api/flights")).json();
    onFlights(j.flights || []);
  } catch {}
  connectWS();
}

let ws, wsPing;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    feedLive = true;
    setStatus("live", "ok");
    renderBoard();
    clearInterval(wsPing);
    wsPing = setInterval(() => ws.readyState === 1 && ws.send("ping"), 25000);
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "flights") onFlights(m.flights || []);
  };
  ws.onclose = () => {
    feedLive = false;
    setStatus("reconnecting…", "bad");
    renderBoard();
    clearInterval(wsPing);
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}
function setStatus(text, cls) {
  const s = el("c-status");
  if (s) {
    s.textContent = text;
    s.className = cls || "";
  }
}

function onFlights(list) {
  state.flights = list;
  feedTs = Date.now();
  rebuildAirlineFilter(list);
  renderBoard();
  render();
}

/* ---- filters ---- */
const fAirline = el("f-airline");
const fAlt = el("f-alt");
const fAltVal = el("f-alt-val");
const fSearch = el("f-search");
fAirline.addEventListener("change", render);
fSearch.addEventListener("input", render);
fAlt.addEventListener("input", () => {
  fAltVal.textContent = Number(fAlt.value).toLocaleString() + " ft";
  render();
});

function rebuildAirlineFilter(list) {
  const names = [...new Set(list.map((f) => f.airline).filter(Boolean))].sort();
  const key = names.join("|");
  if (key === state.airlineKeys) return;
  state.airlineKeys = key;
  const cur = fAirline.value;
  fAirline.innerHTML = '<option value="">All airlines</option>';
  for (const n of names) {
    const o = document.createElement("option");
    o.value = o.textContent = n;
    fAirline.appendChild(o);
  }
  fAirline.value = names.includes(cur) ? cur : "";
}

function passesFilters(f) {
  if (fAirline.value && f.airline !== fAirline.value) return false;
  if ((f.alt_ft || 0) < Number(fAlt.value)) return false;
  const q = fSearch.value.trim().toLowerCase();
  if (q) {
    const hay = [f.flight_no, f.callsign, f.registration, f.hex].join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

/* ---- render ---- */
function render() {
  if (!map) return;
  const shown = state.flights.filter(passesFilters);
  const seen = new Set();
  for (const f of shown) {
    seen.add(f.hex);
    upsertPlane(f);
  }
  for (const [hex, p] of planes) {
    if (!seen.has(hex)) {
      p.marker.remove();
      planes.delete(hex);
    }
  }
  const cl = el("c-live");
  if (cl) cl.textContent = shown.length;
  const ca = el("c-air");
  if (ca) ca.textContent = shown.filter((f) => (f.alt_ft || 0) > 1000).length;
}

/* ---- detail panel ---- */
el("detail-close").addEventListener("click", closeDetail);

function closeDetail() {
  if (!state.selected) return;
  const prev = planes.get(state.selected);
  if (prev) prev.marker.getElement().classList.remove("sel");
  state.selected = null;
  clearInterval(state.detailTimer);
  el("detail").classList.add("hidden");
  trailCoords.list = [];
  drawOverlay();
}

async function selectFlight(hex) {
  if (state.selected && planes.get(state.selected)) {
    planes.get(state.selected).marker.getElement().classList.remove("sel");
  }
  state.selected = hex;
  const p = planes.get(hex);
  if (p) {
    p.marker.getElement().classList.add("sel");
    map.flyTo({ center: p.marker.getLngLat(), zoom: Math.max(map.getZoom(), 6.5), duration: 900 });
  }
  el("detail").classList.remove("hidden");
  await refreshDetail();
  clearInterval(state.detailTimer);
  state.detailTimer = setInterval(refreshDetail, 5000);
}

async function refreshDetail() {
  if (!state.selected) return;
  let d;
  try {
    const r = await fetch("/api/flights/" + state.selected);
    if (!r.ok) throw new Error(r.status);
    d = await r.json();
  } catch {
    el("detail-body").innerHTML = '<p class="muted">Flight no longer tracked.</p>';
    clearInterval(state.detailTimer);
    return;
  }
  const route =
    `<span>${d.dep || '<span class="unk">???</span>'}</span>` +
    `<span class="arrow">&rarr;</span>` +
    `<span>${d.arr || '<span class="unk">???</span>'}</span>`;
  const row = (k, v) => `<div><div class="k">${k}</div><div class="v mono">${v}</div></div>`;
  const f2 = (n, s = "") => (n == null ? "&mdash;" : Number(n).toLocaleString() + s);
  el("detail-body").innerHTML =
    `<div class="d-head"><span class="d-flightno">${d.flight_no || d.callsign || d.hex}</span></div>` +
    `<div class="d-airline">${d.airline || ""} &middot; ${d.type || "?"} &middot; ${d.registration || "?"}</div>` +
    `<div class="d-route">${route}</div>` +
    `<div class="d-grid">` +
      row("Altitude", f2(d.alt_ft, " ft")) +
      row("Ground speed", f2(d.gs_kt, " kt")) +
      row("Heading", f2(d.track_deg, "&deg;")) +
      row("Vert. rate", f2(d.vs_fpm, " fpm")) +
      row("Callsign", d.callsign || "&mdash;") +
      row("ICAO hex", d.hex) +
      row("Source", d.source || "&mdash;") +
      row("Tracked", ago(d.first_seen)) +
    `</div>`;
  trailCoords.list = (d.track || []).map((p) => [p[2], p[1]]);
  drawOverlay();
}

/* ---- go ---- */
renderBoard();
connectFeed();
