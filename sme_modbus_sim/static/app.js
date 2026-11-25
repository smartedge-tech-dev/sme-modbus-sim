"use strict";
// SME ModbusSim web UI: vanilla JS, no build step. Generic chrome (header, menus, footer, theme, i18n)
// comes from the sme-sim-common shell (/sme-sim-common/shell/shell.js); UI strings live in i18n/{en,ja,zh}.js.
// Structure (api / snapshot / trend / websocket) follows SME BACnetSim app.js @ ebd27ec.

// ------------------------------------------------------------------ product config (single spot)

const REPO_URL = "https://github.com/smartedge-tech-dev/sme-modbus-sim";
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
  // Register-grid mark; navy parts use currentColor, the accent block stays brand blue.
  logoSvg: `<svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M5 8h22v24H5zM5 16h22M5 24h22M16 8v24M4 34h32" stroke="currentColor" stroke-width="2.4"/>
    <rect x="25.5" y="11" width="10" height="10" fill="#087def"/></svg>`,
};

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
  tab: ["traffic", "stats", "log", "faults"].includes(store.get("tab", "")) ? store.get("tab") : "traffic",
  faults: [],           // fault rules (GET /api/faults, WS "faults")
  cfgApplied: null,
  yamlDev: null,        // device whose YAML the YAML dialog edits (null = whole configuration)
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
  queueMicrotask(renderFaults);
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
  const state = sim.running ? t("common.sim.running") : t("common.sim.paused");
  ind.classList.toggle("paused", !sim.running);
  ind.querySelector(".st-run-lbl").textContent = state;
  ind.title = t("common.sim.toggle", { state });
  ind.setAttribute("aria-label", ind.title);
  const sel = $("scaleSel");
  if (sim.time_scale != null) {
    const list = SPEEDS.includes(sim.time_scale) ? SPEEDS : [...SPEEDS, sim.time_scale].sort((a, b) => a - b);
    sel.innerHTML = list.map((s) => `<option value="${s}"${s === sim.time_scale ? " selected" : ""}>${s}×</option>`).join("");
  }
  Shell.updateItem("sim.run", { label: sim.running ? "common.sim.pause" : "common.sim.run" });
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
      bo: d.byte_order, wo: d.word_order, ua: d.undefined_address }))}</span>
    <button type="button" class="btn small tplsave" id="tplSave" title="${esc(t("common.device_templates.save.title"))}">${esc(t("common.device_templates.save"))}</button>
    <button type="button" class="btn small" id="devDup" title="${esc(t("dev.dup.title"))}">${esc(t("dev.dup"))}</button>
    <button type="button" class="btn small" id="devYaml" title="${esc(t("dev.yaml.tip"))}">${esc(t("dev.yaml"))}</button>
    <button type="button" class="btn small danger" id="devDel" title="${esc(t("dev.del.title"))}">${esc(t("dev.del"))}</button>`;
  $("tplSave").onclick = () => saveAsTemplate(d.name);
  $("devDup").onclick = () => duplicateDevice(d.name);
  $("devYaml").onclick = () => editDeviceYaml(d.name);
  $("devDel").onclick = () => deleteDevice(d.name);
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
  queueMicrotask(renderFaults);   // quick fault buttons follow the selected device
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
  const opts = labelOptions(p);
  if (opts) { startSelect(d, name, p, tr, opts); return; }
  S.editing = name;
  const td = tr.querySelector(".val");
  td.innerHTML = `<input type="text" spellcheck="false">`;
  const inp = td.querySelector("input");
  inp.value = p.value == null ? "" : String(p.value);
  if (p.nullable) inp.placeholder = t("val.null.hint");
  inp.focus(); inp.select();
  let done = false;
  const finish = () => { if (done) return; done = true; S.editing = null; updateRow(name, false); };
  inp.onkeydown = async (e) => {
    if (e.key === "Escape") { finish(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const txt = inp.value.trim();
    let v = txt === "" && p.nullable ? null : txt;   // nullable (SunSpec): empty clears to "not implemented"
    if (v !== null && p.datatype !== "string") {
      if (/^(true|false)$/i.test(txt)) v = txt.toLowerCase() === "true";
      else if (txt !== "" && !Number.isNaN(Number(txt))) v = Number(txt);
    }
    if (await writePoint(d, name, v)) finish();
    else { inp.focus(); inp.select(); }
  };
  inp.onblur = () => setTimeout(finish, 150);
  inp.onclick = (e) => e.stopPropagation();
}

function labelOptions(p) {
  const l = p.labels;
  if (!l || isBool(p)) return null;
  const ent = Array.isArray(l) ? l.map((v, i) => [String(i), v]) : Object.entries(l);
  return ent.length ? ent : null;
}

// enum points (labels): pick a label; nullable points also offer "—" (null)
function startSelect(d, name, p, tr, opts) {
  S.editing = name;
  const td = tr.querySelector(".val");
  const cur = p.value == null ? "" : String(p.value);
  const extra = cur !== "" && !opts.some(([k]) => k === cur) ? `<option value="${esc(cur)}">${esc(cur)}</option>` : "";
  td.innerHTML = `<select>${p.nullable ? `<option value="">${esc(t("val.null"))}</option>` : ""}${extra}${
    opts.map(([k, v]) => `<option value="${esc(k)}">${esc(k)}: ${esc(v)}</option>`).join("")}</select>`;
  const sel = td.querySelector("select");
  sel.value = cur;
  sel.focus();
  let done = false;
  const finish = () => { if (done) return; done = true; S.editing = null; updateRow(name, false); };
  sel.onchange = async () => {
    const k = sel.value;
    await writePoint(d, name, k === "" ? null : Number.isNaN(Number(k)) ? k : Number(k));
    finish();
  };
  sel.onkeydown = (e) => { if (e.key === "Escape") finish(); };
  sel.onblur = () => setTimeout(finish, 150);
  sel.onclick = (e) => e.stopPropagation();
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
    && (!exc || e.exception_code != null || !!e.error || !!e.fault);
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
  const flt = e.fault ? `⚡ ${e.fault.action} [${e.fault.rule}]` : "";
  if (flt) tr.classList.add("fault");
  const sum = [flt, e.summary, excName && `(${excName})`, e.error].filter(Boolean).join(" ");
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
  for (const p of ["traffic", "stats", "log", "faults"]) $(`pane-${p}`).hidden = p !== name;
  if (name === "stats") renderStats();
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
  S.yamlDev = null;
  $("yamlTitle").textContent = t("cfg.title");
  $("yamlHint").textContent = t("cfg.hint");
  $("yamlSave").hidden = false;
  S.cfgApplied = text;
  $("yamlText").value = text;
  $("yamlErr").textContent = "";
  $("dlgYaml").showModal();
}

async function applyConfig(text) {
  await api("PUT", "/api/config", text, true);
  S.cfgApplied = text;
  const st = await api("GET", "/api/state");   // PUT /api/config answers {"ok": true} only
  Shell.toast(t("cfg.applied", { n: st.devices.length }), "ok");
}

async function saveConfig() {
  const r = await api("POST", "/api/config/save");
  Shell.toast(t("common.config.saved", { path: r.path }), "ok");
}

$("yamlApply").onclick = async (ev) => {
  ev.preventDefault();
  try {
    if (S.yamlDev) {
      const r = await api("PUT", `/api/devices/${encodeURIComponent(S.yamlDev)}/config`, $("yamlText").value, true);
      Shell.toast(t("dev.updated", { name: r.name }), "ok");
      focusDevice(r.name);
    } else await applyConfig($("yamlText").value);
    $("dlgYaml").close();
  }
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

// ------------------------------------------------------------------ Excel point table / user templates

function showErrors(title, hint, errors) {
  $("errTitle").textContent = title;
  $("errHint").textContent = hint || "";
  $("errList").innerHTML = errors.map((e) => `<li>${esc(e)}</li>`).join("");
  $("dlgErrors").showModal();
}

function download(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function importXlsx(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/config/import", { method: "POST", body: fd });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = data.detail;
      if (d && Array.isArray(d.errors)) showErrors(t("xlsx.errors.title"), `${d.message} — ${t("xlsx.errors.hint")}`, d.errors);
      else toast(typeof d === "string" ? d : `HTTP ${r.status}`);
      return;
    }
    const st = await api("GET", "/api/state").catch(() => null);   // the import answers {ok, warnings}
    Shell.toast(t("xlsx.imported", { n: st ? st.devices.length : "?" }), "ok");
    if (data.warnings?.length) showErrors(t("xlsx.warnings.title"), "", data.warnings);
  } catch (e) { toast(e.message); }
}

$("xlsxFile").onchange = (ev) => {
  const f = ev.target.files[0];
  ev.target.value = "";
  if (f) importXlsx(f);
};

async function reloadTemplates() {
  try {
    const r = await api("POST", "/api/templates/reload");
    Shell.toast(t("common.device_templates.reloaded", { n: r.loaded.length, dir: r.dir }), "ok");
    if (r.errors.length) showErrors(t("common.device_templates.errors.title"), r.dir, r.errors.map((e) => `${e.path}: ${e.error}`));
  } catch (e) { toast(e.message); }
}

async function saveAsTemplate(dev) {
  const name = prompt(t("common.device_templates.save.prompt"), dev.replace(/[^A-Za-z0-9_.-]+/g, "_").toLowerCase());
  if (!name) return;
  const url = `/api/templates/from-device/${encodeURIComponent(dev)}`;
  try {
    let r;
    try { r = await api("POST", url, { name }); }
    catch (e) {
      if (!/already exists/.test(e.message) || !confirm(t("common.device_templates.save.exists", { name }))) throw e;
      r = await api("POST", url, { name, overwrite: true });
    }
    Shell.toast(t("common.device_templates.saved", { name: r.name, path: r.path }), "ok");
  } catch (e) { toast(e.message); }
}


// ------------------------------------------------------------------ device management (add / duplicate / delete / YAML)

const DD = { templates: [], tpl: "", unitDirty: false };

function focusDevice(name) {
  S.dev = name; store.set("device", name);
  if (currentDevice()) selectDevice(name);   // otherwise the reload snapshot selects it when it arrives
}

function nextUnit(lids) {
  const used = new Set((S.snap?.devices || []).filter((d) => (d.listeners || []).some((l) => lids.includes(l))).map((d) => d.unit_id));
  for (let u = 1; u <= 247; u++) if (!used.has(u)) return u;
  return "";
}

function ddListeners() { return [...$("ddLsn").querySelectorAll("input:checked")].map((i) => i.value); }

function renderTplList() {
  const items = [{ name: "", title: t("devdlg.custom"), description: t("devdlg.custom.desc") }, ...DD.templates];
  $("ddTpls").innerHTML = items.map((x) => `<li data-name="${esc(x.name)}" class="${x.name === DD.tpl ? "sel" : ""}" tabindex="0">
    ${x.source ? `<span class="src ${esc(x.source)}">${esc(t(`devdlg.src.${x.source}`))}</span>` : ""}<span class="tt">${esc(x.title)}</span>
    ${x.name ? `<code>${esc(x.name)}</code>` : ""}${x.description ? `<small>${esc(x.description)}</small>` : ""}</li>`).join("");
  renderTplParams();
}

function paramKind(v) {
  return typeof v === "boolean" ? "bool" : typeof v === "number" ? "num" : v !== null && typeof v === "object" ? "json" : "text";
}

function renderTplParams() {
  const tp = DD.templates.find((x) => x.name === DD.tpl);
  const params = Object.entries(tp?.params || {});
  $("ddParams").innerHTML = params.length ? params.map(([k, v]) => {
    const kind = paramKind(v);
    const inp = kind === "bool" ? `<input type="checkbox" ${v ? "checked" : ""}>`
      : `<input type="${kind === "num" ? "number" : "text"}" step="any" spellcheck="false" value="${esc(kind === "json" ? JSON.stringify(v) : v ?? "")}">`;
    return `<label data-k="${esc(k)}" data-kind="${kind}"><span>${esc(k)}</span>${inp}</label>`;
  }).join("") : `<p class="hint">${esc(t("devdlg.noparams"))}</p>`;
}

function ddParams() {   // only values that differ from the template defaults (the YAML keeps overrides only)
  const tp = DD.templates.find((x) => x.name === DD.tpl);
  const out = {};
  for (const lb of $("ddParams").querySelectorAll("label[data-k]")) {
    const k = lb.dataset.k, inp = lb.querySelector("input"), def = tp.params[k];
    let v;
    if (lb.dataset.kind === "bool") v = inp.checked;
    else if (lb.dataset.kind === "num") { if (inp.value.trim() === "") continue; v = Number(inp.value); }
    else if (lb.dataset.kind === "json") v = JSON.parse(inp.value);
    else { v = inp.value; if (v === "" && def == null) continue; }
    if (JSON.stringify(v) !== JSON.stringify(def)) out[k] = v;
  }
  return out;
}

async function openAddDevice() {
  try { DD.templates = await api("GET", "/api/templates"); } catch (e) { toast(e.message); return; }
  if (!DD.templates.some((x) => x.name === DD.tpl)) DD.tpl = DD.templates[0]?.name ?? "";
  const lsn = S.snap?.listeners || [];
  $("ddLsn").innerHTML = lsn.map((l) => `<label><input type="checkbox" value="${esc(l.id)}" checked> ${esc(l.id)} <small class="muted">${esc(l.type)}</small></label>`).join("");
  DD.unitDirty = false;
  $("ddUnit").value = nextUnit(lsn.map((l) => l.id));
  $("ddName").value = "";
  $("ddErr").textContent = "";
  renderTplList();
  $("dlgDevice").showModal();
  $("ddName").focus();
}

async function submitAddDevice(ev) {
  ev.preventDefault();
  const lids = ddListeners();
  const body = { name: $("ddName").value.trim(), unit_id: Number($("ddUnit").value) };
  try {
    if (DD.tpl) {
      body.template = DD.tpl;
      const params = ddParams();
      if (Object.keys(params).length) body.params = params;
    }
    if (lids.length !== (S.snap?.listeners || []).length) body.listeners = lids;   // all checked = all listeners
    const r = await api("POST", "/api/devices", body);
    $("dlgDevice").close();
    Shell.toast(t("dev.added", { name: r.name, unit: r.device.unit_id }), "ok");
    focusDevice(r.name);
  } catch (e) { $("ddErr").textContent = e.message; }
}

async function duplicateDevice(name) {
  try {
    const r = await api("POST", `/api/devices/${encodeURIComponent(name)}/duplicate`);
    Shell.toast(t("dev.duplicated", { name: r.name, unit: r.device.unit_id }), "ok");
    focusDevice(r.name);
  } catch (e) { toast(e.message); }
}

async function deleteDevice(name) {
  if (!confirm(t("dev.del.confirm", { name }))) return;
  try {
    await api("DELETE", `/api/devices/${encodeURIComponent(name)}`);
    Shell.toast(t("dev.deleted", { name }), "ok");
  } catch (e) { toast(e.message); }
}

async function editDeviceYaml(name) {
  let text = "";
  try { text = await api("GET", `/api/devices/${encodeURIComponent(name)}/config`); } catch (e) { toast(e.message); return; }
  S.yamlDev = name;
  $("yamlTitle").textContent = t("dev.yaml.title", { name });
  $("yamlHint").textContent = t("dev.yaml.hint");
  $("yamlSave").hidden = true;
  $("yamlText").value = text;
  $("yamlErr").textContent = "";
  $("dlgYaml").showModal();
}

// ------------------------------------------------------------------ fault injection (design 5.4)

const FAULT_ACTIONS = ["exception", "delay", "no_response", "disconnect", "corrupt_crc", "truncate",
  "wrong_transaction_id", "wrong_unit_id", "busy", "freeze", "refuse_connections"];
const FAULT_PARAM_HINT = { exception: '{"code": 4}', delay: '{"ms": 2000}  |  {"min_ms": 500, "max_ms": 3000}',
  truncate: '{"bytes": 3}', busy: '{"seconds": 10}', wrong_unit_id: '{"unit": 99}' };

function ftScope(r) {
  const parts = [];
  if (r.listener) parts.push(r.listener);
  if (r.unit != null) parts.push(`${t("ft.f.unit")} ${r.unit}`);
  if (r.function_codes?.length) parts.push(`FC ${r.function_codes.join(",")}`);
  if (r.address_start != null || r.address_end != null) parts.push(`${r.address_start ?? 0}–${r.address_end ?? 65535}`);
  return parts.join(" · ") || t("ft.scope.all");
}

function ftGate(r) {
  const parts = [];
  if (r.probability < 1) parts.push(`p=${r.probability}`);
  if (r.every_n) parts.push(t("ft.every", { n: r.every_n }));
  if (r.remaining != null) parts.push(t("ft.remaining", { n: r.remaining }));
  return parts.join(" · ");
}

function renderFaults() {
  const rules = S.faults;
  $("ftBody").innerHTML = rules.map((r) => `<tr class="${r.enabled ? "" : "off"}${r.active ? " active" : ""}" data-id="${esc(r.id)}">
    <td><input type="checkbox" class="fton"${r.enabled ? " checked" : ""} aria-label="${esc(t("ft.col.on"))}"></td>
    <td>${esc(r.id)}${r.name ? ` <small>${esc(r.name)}</small>` : ""}</td>
    <td>${esc(r.action)}${r.busy_remaining != null ? ` <small>${esc(t("ft.busy.left", { s: Math.ceil(r.busy_remaining) }))}</small>` : ""}</td>
    <td>${esc(ftScope(r))}</td>
    <td class="hex">${esc(Object.keys(r.params || {}).length ? JSON.stringify(r.params) : "")}</td>
    <td>${esc(ftGate(r))}</td><td class="num">${r.triggers ?? 0}</td>
    <td><button type="button" class="btn small ftdel" title="${esc(t("ft.delete"))}" aria-label="${esc(t("ft.delete"))}">×</button></td>
  </tr>`).join("");
  $("ftEmpty").hidden = rules.length > 0;
  const n = rules.filter((r) => r.enabled).length;
  $("ftCount").textContent = n ? `(${n})` : "";
  const d = currentDevice();
  $("ftDev").textContent = d ? `${d.name} (${t("ft.f.unit")} ${d.unit_id ?? d.unit ?? "?"})` : t("ft.nodev");
  for (const id of ["ftqTimeout", "ftqExc", "ftqBusy", "ftqFreeze"]) $(id).disabled = !d;
}

async function loadFaults() {
  try { S.faults = (await api("GET", "/api/faults")).rules || []; renderFaults(); } catch (e) { toast(e.message); }
}

async function ftCall(method, url, body, raw) {
  try { const r = await api(method, url, body, raw); await loadFaults(); return r; } catch (e) { toast(e.message); return null; }
}

function quickFault(action, params, label) {
  const d = currentDevice();
  if (!d) return;
  const ls = Array.isArray(d.listeners) ? d.listeners : [];
  ftCall("POST", "/api/faults", { action, params, unit: d.unit_id ?? d.unit, listener: ls.length === 1 ? ls[0] : undefined,
    name: `${d.name}: ${label}` }).then((r) => { if (r) Shell.toast(t("ft.added", { id: r.id }), "ok"); });
}

function openFaultForm() {
  const ls = S.snap?.listeners || [];
  $("ftListener").innerHTML = `<option value="">${esc(t("ft.any"))}</option>`
    + ls.map((l) => `<option value="${esc(l.id)}">${esc(l.id)}</option>`).join("");
  if (!$("ftAction").options.length) $("ftAction").innerHTML = FAULT_ACTIONS.map((a) => `<option>${a}</option>`).join("");
  const d = currentDevice();
  if (d && $("ftUnit").value === "") $("ftUnit").value = d.unit_id ?? d.unit ?? "";
  $("ftParams").placeholder = FAULT_PARAM_HINT[$("ftAction").value] || "";
  $("ftForm").hidden = false;
}

async function submitFault(ev) {
  ev.preventDefault();
  let params = {};
  const ptxt = $("ftParams").value.trim();
  if (ptxt) {
    try { params = JSON.parse(ptxt); } catch { params = null; }
    if (!params || typeof params !== "object" || Array.isArray(params)) { toast(t("ft.params.bad")); return; }
  }
  const num = (id) => ($(id).value.trim() === "" ? undefined : Number($(id).value));
  const fcs = $("ftFcs").value.split(/[\s,]+/).filter(Boolean).map(Number);
  const r = await ftCall("POST", "/api/faults", {
    action: $("ftAction").value, params, listener: $("ftListener").value || undefined, unit: num("ftUnit"),
    function_codes: fcs.length ? fcs : undefined, address_start: num("ftStart"), address_end: num("ftEnd"),
    probability: num("ftProb") ?? 1, every_n: num("ftEvery"), remaining: num("ftRemain"),
  });
  if (r) { $("ftForm").hidden = true; Shell.toast(t("ft.added", { id: r.id }), "ok"); }
}

async function importFaults(file) {
  const r = await ftCall("POST", "/api/faults/preset", await file.text(), true);
  if (r) Shell.toast(t("ft.imported", { n: r.rules.length }), "ok");
}

// ------------------------------------------------------------------ shell

Shell.init({
  ...PRODUCT,
  vendor: Shell.vendors.smartEdge,
  about: [["about.stack", t("about.stack.value")]],
  menus: () => [
    { id: "file", label: "common.menu.file", items: [
      { id: "file.config", label: "common.config.edit", onClick: editConfig },
      { separator: true },
      { id: "file.xlsx.export", label: "menu.file.xlsx.export", onClick: () => download("/api/config/xlsx") },
      { id: "file.xlsx.blank", label: "common.config.xlsx_template", onClick: () => download("/api/config/xlsx-template") },
      { id: "file.xlsx.import", label: "common.config.import_xlsx", onClick: () => $("xlsxFile").click() },
      { separator: true },
      { id: "file.save", label: "common.config.save", title: t("menu.file.save.title"), onClick: () => saveConfig().catch((e) => toast(e.message)) },
    ] },
    { id: "sim", label: "common.menu.sim", items: [
      { id: "sim.run", label: "common.sim.pause", onClick: toggleRun },
      { id: "sim.speed", label: "common.sim.time_scale", submenu: speedItems(null) },
    ] },
    { id: "tpl", label: "common.device_templates.menu", items: [
      { id: "tpl.reload", label: "common.device_templates.reload", onClick: reloadTemplates },
    ] },
    { id: "view", label: "common.menu.view", items: [
      { id: "view.traffic", label: "menu.view.traffic", onClick: () => showTab("traffic") },
      { id: "view.stats", label: "menu.view.stats", onClick: () => showTab("stats") },
      { id: "view.log", label: "common.event_log.title", onClick: () => showTab("log") },
      { id: "view.faults", label: "menu.view.faults", onClick: () => showTab("faults") },
      { id: "view.addr", label: "menu.view.addr", submenu: addrItems() },
      { separator: true },
      Shell.themeItem(),
      Shell.langItem(),
    ] },
    Shell.helpMenu(),
  ],
});

Shell.setStatus(`
  <span class="st-clock" id="clock" title="${esc(t("common.sim.time"))}">--:--</span>
  <button type="button" class="st-run" id="runInd"><span class="st-run-lbl"></span></button>
  <select class="st-scale" id="scaleSel" title="${esc(t("common.sim.time_scale"))}" aria-label="${esc(t("common.sim.time_scale"))}"></select>
  <span class="st-net" id="netinfo">${esc(t("common.backend.connecting"))}</span>
  <span class="conn off" id="conn" role="img" title="${esc(t("common.backend.disconnected"))}" aria-label="${esc(t("common.backend.disconnected"))}"></span>`);
renderSim({ running: true, time_scale: null });

function setConn(on) {
  const c = $("conn"), lbl = t(on ? "common.backend.connected" : "common.backend.disconnected");
  c.classList.toggle("off", !on); c.title = lbl; c.setAttribute("aria-label", lbl);
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { setConn(true); loadFaults(); };
  ws.onclose = () => { setConn(false); setTimeout(connect, 2000); };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "snapshot") setSnapshot(msg.data);
    else if (msg.type === "values") applyChanges(msg);
    else if (msg.type === "trace") addTrace(msg.events || []);
    else if (msg.type === "log") addLog(msg.event);
    else if (msg.type === "faults") { S.faults = msg.rules || []; renderFaults(); }
    else if (msg.type === "simulation" && S.snap) {
      S.snap.running = msg.running; S.snap.time_scale = msg.time_scale; renderSim(S.snap);
    } else if (msg.type === "stats") {
      S.stats = msg.stats || {};
      renderListeners();
      if (S.tab === "stats") renderStats();
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
$("ftqTimeout").onclick = () => quickFault("no_response", {}, t("ft.q.timeout"));
$("ftqExc").onclick = () => quickFault("exception", { code: 4 }, t("ft.q.exc04"));
$("ftqBusy").onclick = () => quickFault("busy", { seconds: 10 }, t("ft.q.busy"));
$("ftqFreeze").onclick = () => quickFault("freeze", {}, t("ft.q.freeze"));
$("ftAdd").onclick = openFaultForm;
$("ftCancel").onclick = () => { $("ftForm").hidden = true; };
$("ftForm").onsubmit = submitFault;
$("ftAction").onchange = () => { $("ftParams").placeholder = FAULT_PARAM_HINT[$("ftAction").value] || ""; };
$("ftImport").onclick = () => $("ftFile").click();
$("ftFile").onchange = (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importFaults(f); };
$("ftExport").onclick = () => download("/api/faults/export");
$("ftClear").onclick = () => {
  if (!confirm(t("ft.clear.confirm"))) return;
  ftCall("POST", "/api/faults/clear").then((r) => { if (r) Shell.toast(t("ft.cleared"), "ok"); });
};
$("ftBody").onclick = (e) => {
  const tr = e.target.closest("tr");
  if (tr && e.target.classList.contains("ftdel")) ftCall("DELETE", `/api/faults/${encodeURIComponent(tr.dataset.id)}`);
};
$("ftBody").onchange = (e) => {
  const tr = e.target.closest("tr");
  if (tr && e.target.classList.contains("fton")) {
    ftCall("PUT", `/api/faults/${encodeURIComponent(tr.dataset.id)}`, { enabled: e.target.checked });
  }
};
window.addEventListener("resize", drawTrend);
window.addEventListener("shell:themechange", drawTrend);
$("devAdd").onclick = openAddDevice;
$("ddForm").onsubmit = submitAddDevice;
$("ddCancel").onclick = () => $("dlgDevice").close();
$("ddTpls").onclick = (e) => {
  const li = e.target.closest("li[data-name]");
  if (li) { DD.tpl = li.dataset.name; renderTplList(); }
};
$("ddTpls").onkeydown = (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.target.closest("li[data-name]")?.click(); }
};
$("ddLsn").onchange = () => { if (!DD.unitDirty) $("ddUnit").value = nextUnit(ddListeners()); };
$("ddUnit").oninput = () => { DD.unitDirty = true; };
refreshStats();   // one initial GET; afterwards /ws pushes {"type": "stats"} every 2 s
showTab(S.tab);
loadTrace().then(connect);
