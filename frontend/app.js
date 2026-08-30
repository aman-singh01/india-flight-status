/* India Domestic Flight Tracker - frontend */
"use strict";

// Self-contained raster style: no sprite / no glyph host to stall on. Swap in a
// vector style (OpenFreeMap, MapTiler, your own) for production if you want
// crisper labels. Esri's canvas basemaps need no API key.
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
const BASE_STYLE = {
  version: 8,
  sources: {
    base: {
      type: "raster",
      tiles: [ESRI + "/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 16,
      attribution: "Tiles &copy; Esri",
    },
    labels: {
      type: "raster",
      tiles: [ESRI + "/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
      tileSize: 256,
      maxzoom: 16,
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#0b1020" } },
    { id: "base", type: "raster", source: "base" },
    { id: "labels", type: "raster", source: "labels" },
  ],
};

// Altitude bands (ft) -> colour. Matches the legend in index.html.
const ALT_BANDS = [
  [0, "#94a3b8"],
  [3000, "#f87171"],
  [10000, "#fb923c"],
  [20000, "#fbbf24"],
  [30000, "#4ade80"],
  [38000, "#38bdf8"],
];

function bandColor(alt) {
  let c = ALT_BANDS[0][1];
  for (const [floor, col] of ALT_BANDS) if ((alt || 0) >= floor) c = col;
  return c;
}

const state = {
  flights: [],
  selected: null,
  detailTimer: null,
  airlineKeys: "",
};

const el = (id) => document.getElementById(id);

const map = new maplibregl.Map({
  container: "map",
  style: BASE_STYLE,
  center: [80.9, 22.6],
  zoom: 4.2,
  minZoom: 3,
  maxBounds: [[58, 2], [102, 40]],
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

/* ---------- plane markers (DOM, no GeoJSON worker) ---------- */

const markers = new Map(); // hex -> maplibregl.Marker

function planeElement() {
  const d = document.createElement("div");
  d.className = "plane";
  d.innerHTML =
    '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">' +
    '<path d="M12 2 L20 21 L12 16 L4 21 Z" fill="currentColor" ' +
    'stroke="rgba(0,0,0,0.6)" stroke-width="1"/></svg>';
  return d;
}

function upsertMarker(f) {
  let m = markers.get(f.hex);
  if (!m) {
    const elm = planeElement();
    elm.addEventListener("click", (ev) => {
      ev.stopPropagation();
      selectFlight(f.hex);
    });
    m = new maplibregl.Marker({ element: elm, rotationAlignment: "map" });
    m.setLngLat([f.lon, f.lat]).addTo(map);
    markers.set(f.hex, m);
  } else {
    m.setLngLat([f.lon, f.lat]);
  }
  m.setRotation(f.track_deg == null ? 0 : f.track_deg);
  const elm = m.getElement();
  elm.style.color = bandColor(f.alt_ft);
  elm.classList.toggle("sel", f.hex === state.selected);
}

/* ---------- trail overlay (canvas, no GeoJSON worker) ---------- */

const trail = { coords: [], canvas: null, ctx: null };

function initTrailOverlay() {
  const c = document.createElement("canvas");
  c.id = "trail-canvas";
  el("map").appendChild(c);
  trail.canvas = c;
  trail.ctx = c.getContext("2d");
  sizeTrail();
  map.on("move", drawTrail);
  map.on("resize", () => {
    sizeTrail();
    drawTrail();
  });
}

function sizeTrail() {
  const r = el("map").getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  trail.canvas.width = r.width * dpr;
  trail.canvas.height = r.height * dpr;
  trail.canvas.style.width = r.width + "px";
  trail.canvas.style.height = r.height + "px";
  trail.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawTrail() {
  const ctx = trail.ctx;
  if (!ctx) return;
  const r = el("map").getBoundingClientRect();
  ctx.clearRect(0, 0, r.width, r.height);
  if (trail.coords.length < 2) return;
  ctx.beginPath();
  trail.coords.forEach((ll, i) => {
    const p = map.project(ll);
    i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
  });
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2;
  ctx.globalAlpha = 0.8;
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/* ---------- init ---------- */

let inited = false;

function init() {
  if (inited) return;
  if (!map.isStyleLoaded()) {
    setTimeout(init, 120);
    return;
  }
  inited = true;
  initTrailOverlay();
  map.on("click", closeDetail);
  bootstrap();
}

map.on("load", init);
init();
map.on("error", (e) => console.warn("map error:", e && e.error));

/* ---------- data ---------- */

async function bootstrap() {
  try {
    const r = await fetch("/api/flights");
    const j = await r.json();
    onFlights(j.flights || []);
  } catch (err) {
    console.warn("initial fetch failed", err);
  }
  connectWS();
}

let ws;
let wsPing;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    setStatus("live", "ok");
    clearInterval(wsPing);
    wsPing = setInterval(() => ws.readyState === 1 && ws.send("ping"), 25000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "flights") onFlights(msg.flights || []);
  };
  ws.onclose = () => {
    setStatus("reconnecting…", "bad");
    clearInterval(wsPing);
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}

function setStatus(text, cls) {
  const s = el("c-status");
  s.textContent = text;
  s.className = cls || "";
}

function onFlights(list) {
  state.flights = list;
  rebuildAirlineFilter(list);
  render();
}

/* ---------- filters ---------- */

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

/* ---------- render ---------- */

function render() {
  const shown = state.flights.filter(passesFilters);
  const seen = new Set();

  for (const f of shown) {
    seen.add(f.hex);
    upsertMarker(f);
  }
  for (const [hex, m] of markers) {
    if (!seen.has(hex)) {
      m.remove();
      markers.delete(hex);
    }
  }

  el("c-live").textContent = shown.length;
  el("c-air").textContent = shown.filter((f) => (f.alt_ft || 0) > 1000).length;
}

/* ---------- detail ---------- */

el("detail-close").addEventListener("click", closeDetail);

function closeDetail() {
  if (!state.selected) return;
  const prev = markers.get(state.selected);
  if (prev) prev.getElement().classList.remove("sel");
  state.selected = null;
  clearInterval(state.detailTimer);
  el("detail").classList.add("hidden");
  trail.coords = [];
  drawTrail();
}

async function selectFlight(hex) {
  if (state.selected && markers.get(state.selected)) {
    markers.get(state.selected).getElement().classList.remove("sel");
  }
  state.selected = hex;
  if (markers.get(hex)) markers.get(hex).getElement().classList.add("sel");
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
  const fmt = (n, s = "") => (n == null ? "&mdash;" : Number(n).toLocaleString() + s);

  el("detail-body").innerHTML =
    `<div class="d-head"><span class="d-flightno">${d.flight_no || d.callsign || d.hex}</span></div>` +
    `<div class="d-airline">${d.airline || ""} &middot; ${d.type || "?"} &middot; ${d.registration || "?"}</div>` +
    `<div class="d-route">${route}</div>` +
    `<div class="d-grid">` +
      row("Altitude", fmt(d.alt_ft, " ft")) +
      row("Ground speed", fmt(d.gs_kt, " kt")) +
      row("Heading", fmt(d.track_deg, "&deg;")) +
      row("Vert. rate", fmt(d.vs_fpm, " fpm")) +
      row("Callsign", d.callsign || "&mdash;") +
      row("ICAO hex", d.hex) +
      row("Source", d.source || "&mdash;") +
      row("Tracked", timeAgo(d.first_seen)) +
    `</div>`;

  trail.coords = (d.track || []).map((p) => [p[2], p[1]]); // [lon, lat]
  drawTrail();
}

function timeAgo(ts) {
  if (!ts) return "&mdash;";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 90) return s + "s ago";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  return Math.round(s / 3600) + "h ago";
}
