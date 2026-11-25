"use strict";
// SME ModbusSim web UI: vanilla JS, no build step. Generic chrome (header, menus, footer, theme, i18n)
// lives in shell/shell.js (vendored from SME BACnetSim); this file holds the Modbus-specific UI.
// Structure (api / snapshot / trend / websocket) follows SME BACnetSim app.js @ ebd27ec.

// ------------------------------------------------------------------ product config (single spot)

const REPO_URL = "https://github.com/smartedge-tech-dev/sme-modbus-sim";
const CONTACT_URL = "https://smartedge-tech.com/contact/";
const PRODUCT = {
  product: "SME ModbusSim",
  tagline: "MODBUS DEVICE SIMULATOR",
  license: "MIT",
  year: 2026,
  links: {
    repo: REPO_URL,
    docs: `${REPO_URL}#readme`,
    issues: `${REPO_URL}/issues/new`,
    license: `${REPO_URL}/blob/main/LICENSE`,
  },
  vendor: {
    name: "smart-edge",
    nameLocal: "智届科技",
    url: "https://smartedge-tech.com",
    contactUrl: CONTACT_URL,
    email: "business@smartedge-tech.com",
    logoUrl: "/static/brand/company-logo.svg",
    blurb: "vendor.blurb",
    services: ["vendor.svc.ems", "vendor.svc.market", "vendor.svc.edge", "vendor.svc.integration",
      "vendor.svc.ai", "vendor.svc.cloud"],
  },
  // Register-grid mark; navy parts use currentColor, the accent block stays brand blue.
  logoSvg: `<svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M5 8h22v24H5zM5 16h22M5 24h22M16 8v24M4 34h32" stroke="currentColor" stroke-width="2.4"/>
    <rect x="25.5" y="11" width="10" height="10" fill="#087def"/></svg>`,
};

// ------------------------------------------------------------------ UI strings (en)

Shell.addStrings("en", {
  "vendor.blurb": "Nanjing Smart-Edge Technology Co., Ltd. (南京智届科技有限公司) has, since its founding in 2023, " +
    "developed end-to-end software for distributed energy resources such as solar, batteries and EVs — " +
    "from equipment control to the cloud and electricity markets. The team supports projects in Japanese, " +
    "from requirements definition through design, implementation, operation and maintenance.",
  "vendor.svc.ems": "Energy management systems (EMS)",
  "vendor.svc.market": "RA / AC systems for electricity markets (JEPX, EPRX, OCCTO)",
  "vendor.svc.edge": "Edge IoT and equipment-control software",
  "vendor.svc.integration": "Equipment integration: Modbus, MQTT, BACnet, ECHONET Lite, OpenADR 2.0b",
  "vendor.svc.ai": "Energy forecasting and data analytics",
  "vendor.svc.cloud": "Cloud infrastructure design, build and operation",
  "menu.file": "File",
  "menu.file.config": "Edit configuration…",
  "menu.file.save": "Save configuration",
  "menu.file.save.title": "Write the applied configuration back to the config file",
  "menu.sim": "Simulation",
  "menu.sim.run": "Run",
  "menu.sim.pause": "Pause",
  "menu.sim.speed": "Time scale",
  "menu.view": "View",
  "menu.view.traffic": "Traffic",
  "menu.view.stats": "Statistics",
  "menu.view.log": "Event log",
  "menu.view.addr": "Address notation",
  "addr.zero": "0-based (PDU address)",
  "addr.one": "1-based",
  "addr.modicon": "Reference (0xxxx / 1xxxx / 3xxxx / 4xxxx)",
  "addr.zero.short": "Addr (0-based)",
  "addr.one.short": "Addr (1-based)",
  "addr.modicon.short": "Ref (4xxxx)",
  "side.filter": "Filter devices / points",
  "side.listeners": "Listeners",
  "col.name": "Name",
  "col.table": "Table",
  "col.address.title": "Click to switch address notation (0-based / 1-based / 4xxxx)",
  "col.datatype": "Type",
  "col.order": "Order",
  "col.order.title": "Byte / word order of multi-register values (ABCD = big-endian)",
  "col.value": "Value",
  "col.raw": "Raw",
  "col.raw.title": "Register image as seen by a Modbus master (hex words / bits)",
  "col.access": "Access",
  "col.behavior": "Behavior",
  "table.coil": "Coil",
  "table.discrete": "Discrete input",
  "table.input": "Input register",
  "table.holding": "Holding register",
  "access.r": "R",
  "access.rw": "R/W",
  "access.r.title": "Read-only for Modbus masters (the UI can still write it)",
  "access.rw.title": "Writable by Modbus masters",
  "val.on": "ON",
  "val.off": "OFF",
  "val.edit": "Click to write a new value",
  "val.toggle": "Click to toggle",
  "write.ok": "{point} ← {value}",
  "dev.none": "No devices configured — use File › Edit configuration.",
  "dev.item": "unit {unit} · {tpl} · {lsn}",
  "dev.meta": "unit {unit} · template {tpl} · listeners {lsn} · byte {bo} / word {wo} · undefined address: {ua}",
  "dev.custom": "custom",
  "dev.points": "{n} points",
  "dev.errors": "{n} point(s) with errors",
  "lsn.item": "{id} · {type} · {host}:{port} · {n} client(s)",
  "lsn.down": "{id} · {type} · {host}:{port} · not bound",
  "lsn.summary": "{n} listener(s) · {c} client(s)",
  "trend.close": "Close trend",
  "trend.empty": "No history yet",
  "tab.traffic": "Traffic",
  "tab.stats": "Statistics",
  "tab.log": "Event log",
  "tab.collapse": "Collapse / expand the bottom pane",
  "tr.unit": "Unit",
  "tr.fc": "FC",
  "tr.exc": "Exceptions / errors only",
  "tr.pause": "Pause",
  "tr.resume": "Resume",
  "tr.clear": "Clear",
  "tr.export": "Export JSONL",
  "tr.export.title": "Download the buffered rows that match the current filters as JSON Lines",
  "tr.info": "{shown} / {total} rows (max {max})",
  "tr.paused": "paused · {n} new",
  "tr.col.time": "Time",
  "tr.col.listener": "Listener",
  "tr.col.peer": "Peer",
  "tr.col.dir": "Dir",
  "tr.col.unit": "Unit",
  "tr.col.fc": "FC",
  "tr.col.summary": "Summary",
  "tr.col.hex": "Hex",
  "tr.exported": "Exported {n} rows",
  "stats.refresh": "Refresh",
  "stats.requests": "Requests by function code",
  "stats.exceptions": "Exceptions by code",
  "stats.count": "Count",
  "stats.none": "none",
  "stats.meta": "{type} · {host}:{port} · clients {clients} · connections {conn} · rx {rx} · tx {tx} · dropped {dropped}",
  "stats.empty": "No listeners",
  "exc.01": "Illegal function",
  "exc.02": "Illegal data address",
  "exc.03": "Illegal data value",
  "exc.04": "Server device failure",
  "exc.05": "Acknowledge",
  "exc.06": "Server device busy",
  "exc.08": "Memory parity error",
  "exc.0A": "Gateway path unavailable",
  "exc.0B": "Gateway target device failed to respond",
  "status.running": "Running",
  "status.paused": "Paused",
  "status.toggle": "Simulation is {state} — click to toggle",
  "status.clock": "Simulation clock",
  "status.scale": "Time scale",
  "status.ws.on": "WebSocket connected",
  "status.ws.off": "WebSocket disconnected",
  "status.connecting": "connecting…",
  "cfg.title": "Configuration (YAML)",
  "cfg.hint": "Apply reloads the running simulator; Save to file writes the applied configuration back to the config file.",
  "cfg.save": "Save to file",
  "cfg.saved": "Saved to {path}",
  "cfg.applied": "Configuration applied ({n} devices)",
  "dlg.cancel": "Cancel",
  "dlg.apply": "Apply",
  "about.stack": "Protocol stack",
  "about.stack.value": "Modbus TCP / RTU over TCP server (pure Python asyncio), multiple unit IDs per listener",
});

const t = Shell.t;

// ------------------------------------------------------------------ state

const $ = (id) => document.getElementById(id);
const SPEEDS = [1, 10, 60, 600, 3600];
const ADDR_MODES = ["zero", "one", "modicon"];
const TABLE_PREFIX = { coil: 0, discrete: 1, input: 3, holding: 4 };
const MAX_TRACE = 2000;
// Function code names (Modbus Application Protocol spec; kept in English like the spec).
const FC_NAMES = {
  1: "Read Coils", 2: "Read Discrete Inputs", 3: "Read Holding Registers", 4: "Read Input Registers",
  5: "Write Single Coil", 6: "Write Single Register", 7: "Read Exception Status", 8: "Diagnostics",
  11: "Get Comm Event Counter", 15: "Write Multiple Coils", 16: "Write Multiple Registers", 17: "Report Server ID",
  22: "Mask Write Register", 23: "Read/Write Multiple Registers", 43: "Encapsulated Interface Transport",
};

const store = {
  get(k, d) { try { return localStorage.getItem(`sme-modbus-sim.${k}`) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`sme-modbus-sim.${k}`, v); } catch { /* private mode */ } },
};

const S = {
  snap: null,           // last snapshot
  dev: store.get("device", null), // selected device name
  idx: new Map(),       // "device|point" -> point info
  rows: new Map(),      // point name -> <tr> of the current device
  trend: null,          // {d, k, p, data: [[t, v]]}
  filter: "",
  addr: ADDR_MODES.includes(store.get("addr", "")) ? store.get("addr") : "zero",
  editing: null,        // point name being edited inline
  stats: {},
  tr: [],               // buffered trace events (max MAX_TRACE)
  trPaused: false,
  trPending: [],
  tab: ["traffic", "stats", "log"].includes(store.get("tab", "")) ? store.get("tab") : "traffic",
  cfgApplied: null,
};

// ------------------------------------------------------------------ api

async function api(method, url, body, raw) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (raw) { opt.body = body; opt.headers["Content-Type"] = "text/plain"; }
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === "string" ? data : (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data)));
  return data;
}

function toast(msg, kind = "error") {
  Shell.toast(msg, kind);
  if (kind === "error") addLog({ t: Date.now() / 1000, level: "error", message: msg });
}

// ------------------------------------------------------------------ formatting

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hhmm = (h) => `${String(Math.floor(h)).padStart(2, "0")}:${String(Math.floor((h % 1) * 60)).padStart(2, "0")}`;
const hex2 = (n) => n.toString(16).toUpperCase().padStart(2, "0");
const isBool = (p) => p.table === "coil" || p.table === "discrete" || p.datatype === "bool" || p.datatype === "bit";

function fmtAddr(p) {
  const a = p.address;
  if (S.addr === "one") return String(a + 1);
  if (S.addr === "modicon") {
    const n = a + 1;
    return `${TABLE_PREFIX[p.table]}${String(n).padStart(n > 9999 ? 5 : 4, "0")}`;
  }
  return String(a);
}

function fmtOrder(p) {
  if (isBool(p) && p.datatype !== "uint16") return "—";
  const n = Math.max(1, p.registers || 1);
  let words = [];
  for (let i = 0; i < n; i++) {
    const hi = String.fromCharCode(65 + 2 * i), lo = String.fromCharCode(66 + 2 * i);
    words.push(p.byte_order === "little" ? lo + hi : hi + lo);
  }
  if (p.word_order === "little") words = words.reverse();
  return words.join("");
}

function fmtRaw(p) {
  if (!Array.isArray(p.raw)) return "";
  if (p.table === "coil" || p.table === "discrete") return p.raw.map((b) => (b ? "1" : "0")).join("");
  return p.raw.map((w) => Number(w).toString(16).toUpperCase().padStart(4, "0")).join(" ");
}

function label(p) {
  const l = p.labels;
  if (!l || p.value == null) return "";
  const v = typeof p.value === "boolean" ? Number(p.value) : p.value;
  if (Array.isArray(l)) return l[v] ?? "";
  return l[String(v)] ?? l[String(p.value)] ?? "";
}

function fmtValue(p) {
  if (p.value == null) return "—";
  if (isBool(p) && typeof p.value !== "number") return p.value ? t("val.on") : t("val.off");
  if (typeof p.value === "number" && !Number.isInteger(p.value)) {
    const a = Math.abs(p.value);
    return p.value.toFixed(a >= 1000 ? 1 : a >= 1 ? 3 : 4).replace(/\.?0+$/, "");
  }
  return String(p.value);
}

// ------------------------------------------------------------------ snapshot / rendering

function setSnapshot(snap) {
  S.snap = snap;
  S.idx.clear();
  for (const d of snap.devices) for (const p of d.points) S.idx.set(`${d.name}|${p.name}`, p);
  if (!snap.devices.some((d) => d.name === S.dev)) S.dev = snap.devices[0]?.name ?? null;
  if (snap.version) Shell.setVersion(snap.version);
  S.stats = snap.stats || {};
  renderSim(snap);
  renderClock(snap.sim_hour);
  renderListeners();
  renderDevices();
  renderDevice();
  renderStats();
  const ul = $("log"); ul.innerHTML = "";
  for (const e of snap.events || []) addLog(e);
  if (S.trend) {
    const p = S.idx.get(`${S.trend.d}|${S.trend.k}`);
    if (p) openTrend(S.trend.d, p); else closeTrend();
  }
}

function speedItems(cur) {
  const list = cur == null || SPEEDS.includes(cur) ? SPEEDS : [...SPEEDS, cur].sort((a, b) => a - b);
  return list.map((s) => ({ id: `sim.speed.${s}`, label: `${s}×`, radio: true, checked: s === cur, onClick: () => setSim({ time_scale: s }) }));
}

function renderSim(sim) {
  const ind = $("runInd");
  const state = sim.running ? t("status.running") : t("status.paused");
  ind.classList.toggle("paused", !sim.running);
  ind.querySelector(".st-run-lbl").textContent = state;
  ind.title = t("status.toggle", { state: state.toLowerCase() });
  ind.setAttribute("aria-label", ind.title);
  const sel = $("scaleSel");
  if (sim.time_scale != null) {
    const list = SPEEDS.includes(sim.time_scale) ? SPEEDS : [...SPEEDS, sim.time_scale].sort((a, b) => a - b);
    sel.innerHTML = list.map((s) => `<option value="${s}"${s === sim.time_scale ? " selected" : ""}>${s}×</option>`).join("");
  }
  Shell.updateItem("sim.run", { label: sim.running ? "menu.sim.pause" : "menu.sim.run" });
  Shell.updateItem("sim.speed", { submenu: speedItems(sim.time_scale) });
}

function renderClock(h) { if (h != null) $("clock").textContent = hhmm(h); }

function listenerRows() {
  const out = [];
  for (const l of S.snap?.listeners || []) {
    const st = S.stats[l.id] || {};
    const port = st.bound_port ?? l.bound_port;
    out.push({ id: l.id, type: l.type, host: st.host ?? l.host, port: port ?? st.port ?? l.port,
      up: port != null, clients: st.clients ?? l.clients ?? 0 });
  }
  return out;
}

function renderListeners() {
  const ls = listenerRows();
  $("lsnList").innerHTML = ls.map((l) => `<li class="${l.up ? "on" : "down"}">${esc(t(l.up ? "lsn.item" : "lsn.down",
    { id: l.id, type: l.type, host: l.host, port: l.port, n: l.clients }))}</li>`).join("");
  const sum = t("lsn.summary", { n: ls.length, c: ls.reduce((a, l) => a + (l.clients || 0), 0) });
  $("netinfo").textContent = sum;
  $("netinfo").title = ls.map((l) => `${l.id} ${l.type} ${l.host}:${l.port}`).join("\n");
}

function devMatches(d, f) {
  return !f || d.name.toLowerCase().includes(f) || String(d.unit_id) === f
    || d.points.some((p) => p.name.toLowerCase().includes(f));
}

function renderDevices() {
  const f = S.filter.toLowerCase().trim();
  const ul = $("devList");
  ul.innerHTML = "";
  for (const d of S.snap.devices) {
    if (!devMatches(d, f)) continue;
    const errs = d.points.filter((p) => p.error).length;
    const li = document.createElement("li");
    li.className = d.name === S.dev ? "sel" : "";
    li.innerHTML = `<span class="dn">${esc(d.name)}</span>
      <span class="badge ${errs ? "err" : ""}" title="${esc(errs ? t("dev.errors", { n: errs }) : t("dev.points", { n: d.points.length }))}">${errs ? `⚠ ${errs}` : d.points.length}</span>
      <span class="di">${esc(t("dev.item", { unit: d.unit_id, tpl: d.template || t("dev.custom"), lsn: (d.listeners || []).join(", ") }))}</span>`;
    li.onclick = () => selectDevice(d.name);
    ul.appendChild(li);
  }
}

function currentDevice() { return S.snap?.devices.find((d) => d.name === S.dev); }

function renderDevice() {
  const d = currentDevice();
  const tb = $("points");
  tb.innerHTML = "";
  S.rows.clear();
  $("addrMode").textContent = t(`addr.${S.addr}.short`);
  if (!d) { $("devHeader").innerHTML = `<div class="empty">${esc(t("dev.none"))}</div>`; return; }
  $("devHeader").innerHTML = `<h2>${esc(d.name)}</h2>
    ${d.description ? `<span class="meta">${esc(d.description)}</span>` : ""}
    <span class="meta">${esc(t("dev.meta", { unit: d.unit_id, tpl: d.template || t("dev.custom"), lsn: (d.listeners || []).join(", "),
      bo: d.byte_order, wo: d.word_order, ua: d.undefined_address }))}</span>`;
  const f = S.filter.toLowerCase().trim();
  const showAll = !f || d.name.toLowerCase().includes(f) || String(d.unit_id) === f;
  for (const p of d.points) {
    if (!showAll && !p.name.toLowerCase().includes(f)) continue;
    const tr = document.createElement("tr");
    tr.dataset.k = p.name;
    if (S.trend && S.trend.d === d.name && S.trend.k === p.name) tr.classList.add("sel");
    tr.innerHTML = rowHtml(p);
    tr.onclick = (e) => { if (!e.target.closest(".val")) openTrend(d.name, p); };
    tr.querySelector(".val").onclick = (e) => { e.stopPropagation(); startEdit(d.name, p.name); };
    tb.appendChild(tr);
    S.rows.set(p.name, tr);
  }
}

function rowHtml(p) {
  const regs = p.registers > 1 ? ` <small class="muted">×${p.registers}</small>` : "";
  const bitn = p.bit != null ? ` <small class="muted">.${p.bit}</small>` : "";
  const err = p.error ? ` <span class="errmark" title="${esc(p.error)}">⚠</span>` : "";
  return `<td class="nm">${esc(p.name)}${err}${p.description ? `<small>${esc(p.description)}</small>` : ""}</td>
    <td>${esc(t(`table.${p.table}`))}</td>
    <td class="num mono">${fmtAddr(p)}${bitn}${regs}</td>
    <td class="mono">${esc(p.datatype)}${p.count > 1 ? `[${p.count}]` : ""}</td>
    <td class="mono">${fmtOrder(p)}</td>
    <td class="val">${valHtml(p)}</td>
    <td class="raw">${esc(fmtRaw(p))}</td>
    <td class="mono" title="${esc(t(`access.${p.access}.title`))}">${esc(t(`access.${p.access}`))}</td>
    <td class="mono muted">${esc(p.behavior || "")}</td>`;
}

function valHtml(p) {
  const lbl = label(p);
  if (isBool(p) && typeof p.value !== "number") {
    return `<span class="pill ${p.value ? "on" : ""}" title="${esc(t("val.toggle"))}">${esc(fmtValue(p))}</span>${lbl ? `<span class="lbl">${esc(lbl)}</span>` : ""}`;
  }
  return `<span class="editable" title="${esc(t("val.edit"))}">${esc(fmtValue(p))}</span>${p.units ? `<span class="un">${esc(p.units)}</span>` : ""}${lbl ? `<span class="lbl">${esc(lbl)}</span>` : ""}`;
}

function updateRow(name, flash = true) {
  const tr = S.rows.get(name);
  const p = S.idx.get(`${S.dev}|${name}`);
  if (!tr || !p) return;
  if (S.editing === name) {           // keep the editor; refresh the other cells
    tr.querySelector(".raw").textContent = fmtRaw(p);
    return;
  }
  const sel = tr.classList.contains("sel");
  tr.innerHTML = rowHtml(p);
  tr.querySelector(".val").onclick = (e) => { e.stopPropagation(); startEdit(S.dev, name); };
  if (sel) tr.classList.add("sel");
  if (flash) {
    const v = tr.querySelector(".val");
    v.classList.add("flash");
    requestAnimationFrame(() => requestAnimationFrame(() => v.classList.remove("flash")));
  }
}

function applyChanges(msg) {
  renderClock(msg.sim_hour);
  const devs = new Set();
  for (const c of msg.changes) {
    const p = S.idx.get(`${c.d}|${c.k}`);
    if (!p) continue;
    const errChanged = (p.error || null) !== (c.error || null);
    p.value = c.value; p.raw = c.raw; p.error = c.error;
    if (c.d === S.dev) updateRow(c.k);
    if (errChanged) devs.add(c.d);
    if (S.trend && S.trend.d === c.d && S.trend.k === c.k && typeof c.value !== "string" && c.value != null) {
      S.trend.data.push([msg.t, Number(c.value)]);
      if (S.trend.data.length > 5000) S.trend.data.shift();
      drawTrend();
    }
  }
  if (devs.size) renderDevices();
}

function selectDevice(name) {
  S.dev = name; store.set("device", name);
  S.editing = null;
  closeTrend();
  renderDevices();
  renderDevice();
}

function cycleAddr() {
  S.addr = ADDR_MODES[(ADDR_MODES.indexOf(S.addr) + 1) % ADDR_MODES.length];
  setAddr(S.addr);
}

function setAddr(m) {
  S.addr = m; store.set("addr", m);
  Shell.updateItem("view.addr", { submenu: addrItems() });
  renderDevice();
}

function addrItems() {
  return ADDR_MODES.map((m) => ({ id: `view.addr.${m}`, label: `addr.${m}`, radio: true, checked: S.addr === m, onClick: () => setAddr(m) }));
}

// ------------------------------------------------------------------ inline write

async function writePoint(d, name, value) {
  try {
    const r = await api("POST", `/api/devices/${encodeURIComponent(d)}/points/${encodeURIComponent(name)}/write`, { value });
    Shell.toast(t("write.ok", { point: `${d}/${name}`, value: JSON.stringify(r.value) }), "ok");
    return true;
  } catch (e) { toast(e.message); return false; }
}

function startEdit(d, name) {
  const p = S.idx.get(`${d}|${name}`);
  const tr = S.rows.get(name);
  if (!p || !tr || S.editing === name) return;
  if (isBool(p) && typeof p.value !== "number") { writePoint(d, name, !p.value); return; }
  S.editing = name;
  const td = tr.querySelector(".val");
  td.innerHTML = `<input type="text" spellcheck="false">`;
  const inp = td.querySelector("input");
  inp.value = p.value == null ? "" : String(p.value);
  inp.focus(); inp.select();
  let done = false;
  const finish = () => { if (done) return; done = true; S.editing = null; updateRow(name, false); };
  inp.onkeydown = async (e) => {
    if (e.key === "Escape") { finish(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const txt = inp.value.trim();
    let v = txt;
    if (p.datatype !== "string") {
      if (/^(true|false)$/i.test(txt)) v = txt.toLowerCase() === "true";
      else if (txt !== "" && !Number.isNaN(Number(txt))) v = Number(txt);
    }
    if (await writePoint(d, name, v)) finish();
    else { inp.focus(); inp.select(); }
  };
  inp.onblur = () => setTimeout(finish, 150);
  inp.onclick = (e) => e.stopPropagation();
}

// ------------------------------------------------------------------ trend

async function openTrend(d, p) {
  S.trend = { d, k: p.name, p, data: [] };
  $("trend").hidden = false;
  $("trendTitle").textContent = `${d} / ${p.name} (${t(`table.${p.table}`)} ${fmtAddr(p)}) ${p.units || ""}`;
  document.querySelectorAll(".points tr.sel").forEach((r) => r.classList.remove("sel"));
  S.rows.get(p.name)?.classList.add("sel");
  try {
    const r = await api("GET", `/api/history/${encodeURIComponent(d)}/${encodeURIComponent(p.name)}`);
    if (S.trend && S.trend.k === p.name && S.trend.d === d) S.trend.data = r.history || [];
  } catch (e) { toast(e.message); }
  drawTrend();
}

function closeTrend() {
  $("trend").hidden = true; S.trend = null;
  document.querySelectorAll(".points tr.sel").forEach((r) => r.classList.remove("sel"));
}

function drawTrend() {
  const tr = S.trend, cv = $("trendCanvas");
  if (!tr || $("trend").hidden) return;
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d"); g.scale(dpr, dpr); g.clearRect(0, 0, W, H);
  // Colors come from the shell theme tokens, so the chart follows light / dark.
  const css = getComputedStyle(document.documentElement);
  const tok = (name) => css.getPropertyValue(name).trim();
  const line = tok("--line"), muted = tok("--muted"), acc = tok("--accent");
  g.font = `11px ${tok("--font") || "Arial, sans-serif"}`; g.fillStyle = muted;
  const data = tr.data;
  if (data.length < 1) { g.fillText(t("trend.empty"), 8, 20); return; }
  const L = 56, R = 10, T = 8, B = 20;
  const t1 = data[data.length - 1][0], t0 = Math.min(data[0][0], t1 - 10);
  let lo = Math.min(...data.map((q) => q[1])), hi = Math.max(...data.map((q) => q[1]));
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const x = (tt) => L + (tt - t0) / (t1 - t0) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  g.strokeStyle = line; g.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, yy = y(v);
    g.beginPath(); g.moveTo(L, yy); g.lineTo(W - R, yy); g.stroke();
    g.fillText(v.toFixed(Math.abs(hi - lo) < 10 ? 2 : 0), 4, yy + 4);
  }
  const fmtT = (s) => new Date(s * 1000).toLocaleTimeString();
  g.fillText(fmtT(t0), L, H - 4);
  g.textAlign = "right"; g.fillText(fmtT(t1), W - R, H - 4); g.textAlign = "left";
  const stepped = !String(tr.p.datatype).startsWith("float");
  g.strokeStyle = acc; g.lineWidth = 1.8; g.beginPath();
  data.forEach(([tt, v], i) => {
    if (i === 0) g.moveTo(x(tt), y(v));
    else { if (stepped) g.lineTo(x(tt), y(data[i - 1][1])); g.lineTo(x(tt), y(v)); }
  });
  g.lineTo(W - R, y(data[data.length - 1][1]));
  g.stroke();
}

// ------------------------------------------------------------------ traffic

function trFilter() {
  const u = $("trUnit").value.trim(), f = $("trFc").value.trim(), exc = $("trExc").checked;
  return (e) => (u === "" || e.unit_id === Number(u))
    && (f === "" || (e.function_code != null && (e.function_code & 0x7f) === Number(f)))
    && (!exc || e.exception_code != null || !!e.error);
}

function fmtTs(ts) {
  const d = new Date(ts * 1000);
  return `${d.toLocaleTimeString()}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

function trRow(e) {
  const tr = document.createElement("tr");
  if (e.exception_code != null) tr.className = "exc";
  else if (e.error) tr.className = "bad";
  const fc = e.function_code;
  const fcTitle = fc != null ? (FC_NAMES[fc & 0x7f] || "") : "";
  const excName = e.exception_code != null ? I18N.en[`exc.${hex2(e.exception_code)}`] ? t(`exc.${hex2(e.exception_code)}`) : "" : "";
  const sum = [e.summary, excName && `(${excName})`, e.error].filter(Boolean).join(" ");
  tr.innerHTML = `<td>${fmtTs(e.ts)}</td><td>${esc(e.listener_id)}</td><td>${esc(e.peer)}</td>
    <td class="${e.direction === "rx" ? "rx" : "tx"}">${e.direction === "rx" ? "rx →" : "← tx"}</td>
    <td class="num">${e.unit_id ?? ""}</td><td class="num" title="${esc(fcTitle)}">${fc != null ? `${fc} <small>(0x${hex2(fc)})</small>` : ""}</td>
    <td class="sum">${esc(sum)}</td><td class="hex">${esc(e.raw || "")}</td>`;
  return tr;
}

function renderTraffic() {
  const ok = trFilter();
  const rows = S.tr.filter(ok);
  const tb = $("trBody");
  const frag = document.createDocumentFragment();
  for (let i = rows.length - 1; i >= 0; i--) frag.appendChild(trRow(rows[i]));   // newest first
  tb.innerHTML = "";
  tb.appendChild(frag);
  trInfo(rows.length);
}

function trInfo(shown) {
  if (shown == null) shown = $("trBody").children.length;
  $("trInfo").textContent = t("tr.info", { shown, total: S.tr.length, max: MAX_TRACE })
    + (S.trPaused ? ` · ${t("tr.paused", { n: S.trPending.length })}` : "");
  $("trCount").textContent = `(${S.tr.length})`;
}

function addTrace(events) {
  if (S.trPaused) {
    S.trPending.push(...events);
    if (S.trPending.length > MAX_TRACE) S.trPending.splice(0, S.trPending.length - MAX_TRACE);
    trInfo();
    return;
  }
  S.tr.push(...events);
  if (S.tr.length > MAX_TRACE) S.tr.splice(0, S.tr.length - MAX_TRACE);
  const ok = trFilter(), tb = $("trBody");
  for (const e of events) if (ok(e)) tb.prepend(trRow(e));
  while (tb.children.length > MAX_TRACE) tb.lastChild.remove();
  trInfo();
}

function toggleTracePause() {
  S.trPaused = !S.trPaused;
  $("trPause").textContent = t(S.trPaused ? "tr.resume" : "tr.pause");
  $("trPause").classList.toggle("on", S.trPaused);
  if (!S.trPaused && S.trPending.length) { const p = S.trPending; S.trPending = []; addTrace(p); }
  trInfo();
}

function exportTrace() {
  const rows = S.tr.filter(trFilter());
  const blob = new Blob(rows.map((e) => JSON.stringify(e) + "\n"), { type: "application/x-ndjson" });
  const a = document.createElement("a");
  const ts = new Date().toISOString().replace(/[-:]/g, "").replace(/\..*/, "").replace("T", "-");
  a.href = URL.createObjectURL(blob);
  a.download = `modbus-trace-${ts}.jsonl`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  Shell.toast(t("tr.exported", { n: rows.length }), "ok");
}

async function loadTrace() {
  try { S.tr = await api("GET", `/api/trace?limit=${MAX_TRACE}`); } catch (e) { toast(e.message); }
  renderTraffic();
}

// ------------------------------------------------------------------ statistics

function renderStats() {
  const box = $("statsBody");
  const ids = Object.keys(S.stats);
  if (!ids.length) { box.innerHTML = `<div class="empty">${esc(t("stats.empty"))}</div>`; return; }
  box.innerHTML = ids.map((id) => {
    const s = S.stats[id];
    const req = Object.entries(s.requests || {}).sort();
    const exc = Object.entries(s.exceptions || {}).sort();
    const reqRows = req.length ? req.map(([k, n]) => {
      const fc = parseInt(k, 16);
      return `<tr><td>${fc} (0x${k})</td><td>${esc(FC_NAMES[fc] || "")}</td><td class="num">${n}</td></tr>`;
    }).join("") : `<tr><td colspan="3" class="muted">${esc(t("stats.none"))}</td></tr>`;
    const excRows = exc.length ? exc.map(([k, n]) => `<tr class="bad"><td>0x${k}</td><td>${esc(I18N.en[`exc.${k}`] ? t(`exc.${k}`) : "")}</td><td class="num">${n}</td></tr>`).join("")
      : `<tr><td colspan="3" class="muted">${esc(t("stats.none"))}</td></tr>`;
    return `<div class="statcard"><h4>${esc(id)}</h4>
      <div class="meta">${esc(t("stats.meta", { type: s.type, host: s.host, port: s.bound_port ?? s.port, clients: s.clients ?? 0,
        conn: s.connections ?? 0, rx: s.rx ?? 0, tx: s.tx ?? 0, dropped: s.dropped ?? 0 }))}</div>
      <table><thead><tr><th colspan="2">${esc(t("stats.requests"))}</th><th class="num">${esc(t("stats.count"))}</th></tr></thead><tbody>${reqRows}</tbody></table>
      <table><thead><tr><th colspan="2">${esc(t("stats.exceptions"))}</th><th class="num">${esc(t("stats.count"))}</th></tr></thead><tbody>${excRows}</tbody></table>
    </div>`;
  }).join("");
}

async function refreshStats() {
  try { S.stats = await api("GET", "/api/stats"); } catch { return; }
  renderListeners();
  if (S.tab === "stats") renderStats();
}

// ------------------------------------------------------------------ log / bottom pane

function addLog(e) {
  const li = document.createElement("li");
  const lvl = e.level || e.kind || "info";
  li.innerHTML = `<span class="t">${new Date(e.t * 1000).toLocaleTimeString()}</span><span class="k-${esc(lvl)}">[${esc(lvl)}] ${esc(e.message ?? e.text)}</span>`;
  const ul = $("log");
  ul.prepend(li);
  while (ul.children.length > 500) ul.lastChild.remove();
  $("logCount").textContent = `(${ul.children.length})`;
}

function showTab(name) {
  S.tab = name; store.set("tab", name);
  $("bottom").classList.remove("collapsed");
  document.querySelectorAll(".tabs .tab").forEach((b) => {
    b.classList.toggle("sel", b.dataset.tab === name);
    b.setAttribute("aria-selected", String(b.dataset.tab === name));
  });
  for (const p of ["traffic", "stats", "log"]) $(`pane-${p}`).hidden = p !== name;
  if (name === "stats") refreshStats().then(renderStats);
}

// ------------------------------------------------------------------ simulation / config

async function setSim(body) {
  try { const r = await api("POST", "/api/simulation", body); Object.assign(S.snap, r); renderSim(S.snap); }
  catch (e) { toast(e.message); }
}
const toggleRun = () => setSim({ running: !(S.snap?.running) });

async function editConfig() {
  let text = "";
  try { text = await api("GET", "/api/config"); } catch (e) { toast(e.message); return; }
  S.cfgApplied = text;
  $("yamlText").value = text;
  $("yamlErr").textContent = "";
  $("dlgYaml").showModal();
}

async function applyConfig(text) {
  const r = await api("PUT", "/api/config", text, true);
  S.cfgApplied = text;
  Shell.toast(t("cfg.applied", { n: r.devices }), "ok");
}

async function saveConfig() {
  const r = await api("POST", "/api/config/save");
  Shell.toast(t("cfg.saved", { path: r.path }), "ok");
}

$("yamlApply").onclick = async (ev) => {
  ev.preventDefault();
  try { await applyConfig($("yamlText").value); $("dlgYaml").close(); }
  catch (e) { $("yamlErr").textContent = e.message; }
};
$("yamlSave").onclick = async () => {
  try {
    const txt = $("yamlText").value;
    if (txt !== S.cfgApplied) await applyConfig(txt);   // save what is shown, so apply it first
    await saveConfig();
    $("dlgYaml").close();
  } catch (e) { $("yamlErr").textContent = e.message; }
};

// ------------------------------------------------------------------ shell

Shell.init({
  ...PRODUCT,
  about: [["about.stack", t("about.stack.value")]],
  menus: () => [
    { id: "file", label: "menu.file", items: [
      { id: "file.config", label: "menu.file.config", onClick: editConfig },
      { separator: true },
      { id: "file.save", label: "menu.file.save", title: t("menu.file.save.title"), onClick: () => saveConfig().catch((e) => toast(e.message)) },
    ] },
    { id: "sim", label: "menu.sim", items: [
      { id: "sim.run", label: "menu.sim.pause", onClick: toggleRun },
      { id: "sim.speed", label: "menu.sim.speed", submenu: speedItems(null) },
    ] },
    { id: "view", label: "menu.view", items: [
      { id: "view.traffic", label: "menu.view.traffic", onClick: () => showTab("traffic") },
      { id: "view.stats", label: "menu.view.stats", onClick: () => showTab("stats") },
      { id: "view.log", label: "menu.view.log", onClick: () => showTab("log") },
      { id: "view.addr", label: "menu.view.addr", submenu: addrItems() },
      { separator: true },
      Shell.themeItem(),
      Shell.langItem(),
    ] },
    Shell.helpMenu(),
  ],
});

Shell.setStatus(`
  <span class="st-clock" id="clock" title="${esc(t("status.clock"))}">--:--</span>
  <button type="button" class="st-run" id="runInd"><span class="st-run-lbl"></span></button>
  <select class="st-scale" id="scaleSel" title="${esc(t("status.scale"))}" aria-label="${esc(t("status.scale"))}"></select>
  <span class="st-net" id="netinfo">${esc(t("status.connecting"))}</span>
  <span class="conn off" id="conn" role="img" title="${esc(t("status.ws.off"))}" aria-label="${esc(t("status.ws.off"))}"></span>`);
renderSim({ running: true, time_scale: null });

function setConn(on) {
  const c = $("conn"), lbl = t(on ? "status.ws.on" : "status.ws.off");
  c.classList.toggle("off", !on); c.title = lbl; c.setAttribute("aria-label", lbl);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => { setConn(false); setTimeout(connect, 2000); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "snapshot") setSnapshot(msg.data);
    else if (msg.type === "values") applyChanges(msg);
    else if (msg.type === "trace") addTrace(msg.events || []);
    else if (msg.type === "log") addLog(msg.event);
    else if (msg.type === "simulation" && S.snap) {
      S.snap.running = msg.running; S.snap.time_scale = msg.time_scale; renderSim(S.snap);
    } else if (msg.type === "reload") connectReload();
  };
}
// The server turns "reload" into a fresh snapshot; this is only a fallback.
async function connectReload() { try { setSnapshot(await api("GET", "/api/state")); } catch (e) { toast(e.message); } }

$("runInd").onclick = toggleRun;
$("scaleSel").onchange = (e) => setSim({ time_scale: Number(e.target.value) });
$("addrMode").onclick = cycleAddr;
$("search").oninput = (e) => { S.filter = e.target.value; renderDevices(); renderDevice(); };
$("trendClose").onclick = closeTrend;
document.querySelectorAll(".tabs .tab").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });
$("bottomToggle").onclick = () => $("bottom").classList.toggle("collapsed");
for (const id of ["trUnit", "trFc", "trExc"]) $(id).oninput = renderTraffic;
$("trPause").onclick = toggleTracePause;
$("trClear").onclick = () => { S.tr = []; S.trPending = []; renderTraffic(); };
$("trExport").onclick = exportTrace;
$("statsRefresh").onclick = () => refreshStats().then(renderStats);
window.addEventListener("resize", drawTrend);
window.addEventListener("shell:themechange", drawTrend);
setInterval(refreshStats, 3000);   // listener client counts / per-FC counters are not pushed over /ws
showTab(S.tab);
loadTrace().then(connect);
