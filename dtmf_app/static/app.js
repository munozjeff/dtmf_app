"use strict";

// ── Socket IVR ────────────────────────────────────────────────
const ivrSocket = io("http://localhost:5050", { transports: ["websocket"] });
ivrSocket.on("connect",       () => addLog("✅ Conectado al servidor IVR", "ok"));
ivrSocket.on("disconnect",    () => addLog("⚠️ Desconectado del servidor", "warn"));
ivrSocket.on("connect_error", e  => addLog("❌ Error conexión: " + e.message, "err"));
ivrSocket.on("ivr_log",           d => addLog(d.msg, d.level === "success" ? "ok" : d.level === "error" ? "err" : d.level));
ivrSocket.on("ivr_status",        d => onIvrStatus(d));
ivrSocket.on("ivr_call_update",   d => onCallUpdate(d));
ivrSocket.on("ivr_digit",         d => onIvrDigit(d));
ivrSocket.on("ivr_campaign_done", () => endCampaign());
ivrSocket.on("manual_state",      d => _manualSetState(d.state, d.number));
ivrSocket.on("manual_log",        d => _manualLog(d.msg, d.level));
ivrSocket.on("bridge_state",      d => _onBridgeState(d.state));
ivrSocket.on("bridge_log",        d => { addLog("🎧 " + d.msg, d.level === "error" ? "err" : d.level); });

// Prueba de entrada: nivel en tiempo real
ivrSocket.on("input_test_level", ({ level }) => {
  const bar = document.getElementById("input-level-bar");
  const txt = document.getElementById("input-level-txt");
  if (bar) bar.style.width = level + "%";
  if (txt) {
    const emoji = level > 60 ? "🔴" : level > 20 ? "🟡" : "🟢";
    txt.textContent = emoji + " Nivel: " + level + "%";
  }
});
ivrSocket.on("input_test_done", ({ peak }) => {
  const wrap = document.getElementById("input-level-wrap");
  const bar  = document.getElementById("input-level-bar");
  const txt  = document.getElementById("input-level-txt");
  const btn  = document.getElementById("btn-test-input");
  if (bar) bar.style.width = "0%";
  if (txt) txt.textContent = peak > 0.001 ? "✅ Señal OK (pico: " + peak.toFixed(4) + ")" : "⚠️ Silencio detectado";
  if (btn) { btn.disabled = false; btn.textContent = "🎤 Test"; }
  setTimeout(() => { if (wrap) wrap.style.display = "none"; }, 4000);
});

// ── Visualizador de audio en tiempo real ─────────────────────
const _VIZ = (() => {
  const COLS     = 180;
  const COLOR_IN  = "#22d3ee";
  const COLOR_OUT = "#a78bfa";
  const GLOW_IN   = "rgba(34,211,238,0.35)";
  const GLOW_OUT  = "rgba(167,139,250,0.35)";
  const state = {
    in:  { buf: new Float32Array(COLS), db: -Infinity },
    out: { buf: new Float32Array(COLS), db: -Infinity },
  };
  function _getCtx(id) {
    const c = document.getElementById(id); if (!c) return null;
    const rect = c.getBoundingClientRect();
    if (c.width !== Math.floor(rect.width) || c.height !== Math.floor(rect.height)) {
      c.width = Math.floor(rect.width) || 400; c.height = Math.floor(rect.height) || 40;
    }
    return { ctx: c.getContext("2d"), w: c.width, h: c.height };
  }
  function _draw(canvasId, buf, color, glow, dbEl, dbVal) {
    const r = _getCtx(canvasId); if (!r) return;
    const { ctx, w, h } = r;
    const barW = Math.max(1, w / COLS);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(0,0,0,0.2)"; ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, h/2); ctx.lineTo(w, h/2); ctx.stroke();
    ctx.shadowColor = glow; ctx.shadowBlur = 6; ctx.fillStyle = color;
    for (let i = 0; i < COLS; i++) {
      const amp = buf[i]; const barH = Math.max(1, amp * (h * 0.92));
      ctx.fillRect(i * barW, (h - barH) / 2, Math.max(1, barW - 1), barH);
    }
    ctx.shadowBlur = 0;
    if (dbEl) {
      const db = dbVal > -80 ? dbVal.toFixed(1) + " dB" : "—";
      dbEl.textContent = db;
      dbEl.style.color = dbVal > -20 ? "#f87171" : dbVal > -40 ? "#fbbf24" : dbVal > -60 ? color : "#334155";
    }
  }
  function _rmsToNorm(rms) {
    if (rms < 1e-9) return 0;
    const db = 20 * Math.log10(rms);
    return Math.max(0, Math.min(1, (db - (-60)) / 60));
  }
  function push(ch, rms) {
    const key = ch === "input" ? "in" : "out";
    const s = state[key];
    s.buf.copyWithin(0, 1); s.buf[COLS - 1] = _rmsToNorm(rms);
    s.db = rms > 1e-9 ? 20 * Math.log10(rms) : -120;
  }
  let _rafId = null;
  function _loop() {
    // Canales de la tab Automática
    _draw("viz-canvas-in",  state.in.buf,  COLOR_IN,  GLOW_IN,  document.getElementById("viz-db-in"),  state.in.db);
    _draw("viz-canvas-out", state.out.buf, COLOR_OUT, GLOW_OUT, document.getElementById("viz-db-out"), state.out.db);
    // Canales de la tab Manual (mismos datos)
    _draw("viz-canvas-in-m",  state.in.buf,  COLOR_IN,  GLOW_IN,  document.getElementById("viz-db-in-m"),  state.in.db);
    _draw("viz-canvas-out-m", state.out.buf, COLOR_OUT, GLOW_OUT, document.getElementById("viz-db-out-m"), state.out.db);
    _rafId = requestAnimationFrame(_loop);
  }
  return { start() { if (!_rafId) _loop(); }, push };
})();

ivrSocket.on("audio_viz", ({ ch, rms }) => { _VIZ.push(ch, rms); });

// ── Viz monitors (audio en tiempo real) ───────────────────────
function _getSelIdx(id) {
  const el = document.getElementById(id);
  return el && el.value !== "" ? parseInt(el.value, 10) : null;
}
function startVizMonitors() {
  const inIdx  = _getSelIdx("ivr-audio-device");
  const outIdx = _getSelIdx("ivr-output-device");
  if (inIdx === null && outIdx === null) return;
  fetch("/ivr/viz/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input: inIdx, output: outIdx }),
  }).catch(() => {});
}
window.addEventListener("load", () => {
  _VIZ.start();
  setTimeout(startVizMonitors, 800);
});
["ivr-audio-device", "ivr-output-device"].forEach(id => {
  document.addEventListener("DOMContentLoaded", () => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => setTimeout(startVizMonitors, 300));
  });
});

// ══════════════════════════════════════════════════════════════
//  LOG
// ══════════════════════════════════════════════════════════════

function addLog(msg, cls) {
  ["ivr-log", "ivr-log-m"].forEach(id => {
    const el = document.getElementById(id); if (!el) return;
    const d = document.createElement("div");
    d.className = "log-line " + (cls || "info");
    d.textContent = msg;
    el.appendChild(d);
    el.scrollTop = el.scrollHeight;
  });
}

// ══════════════════════════════════════════════════════════════
//  TABS PRINCIPALES
// ══════════════════════════════════════════════════════════════

let ivrRunning  = false;
let _manualActive = false;

function switchMainTab(tab) {
  if (tab === "manual" && ivrRunning) {
    addLog("⚠️ Detén la campaña antes de cambiar a Marcación Manual.", "warn");
    return;
  }
  if (tab === "auto" && _manualActive) {
    addLog("⚠️ Cuelga la llamada manual antes de volver a Campaña Automática.", "warn");
    return;
  }
  ["auto", "manual"].forEach(t => {
    document.getElementById("tab-" + t)?.classList.toggle("active", t === tab);
    document.getElementById("tc-" + t)?.classList.toggle("active", t === tab);
    if (t === "manual") {
      document.getElementById("tab-" + t)?.classList.toggle("manual", true);
    }
  });
}

// ══════════════════════════════════════════════════════════════
//  MODO DE LLAMADA (IVR / Bridge / IVR+Bridge)
// ══════════════════════════════════════════════════════════════

let _callMode = "ivr";

const CALL_MODE_DESCS = {
  ivr:        "IVR automático: reproduce audios y detecta DTMF",
  bridge:     "Puente de audio: el agente habla directamente con el destinatario",
  ivr_bridge: "IVR + Puente: el IVR corre hasta el dígito trigger, luego activa el puente",
};

function setCallMode(mode) {
  _callMode = mode;

  // Actualizar botones
  document.querySelectorAll(".call-mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });

  // Actualizar descripción
  const desc = document.getElementById("call-mode-desc");
  if (desc) desc.textContent = CALL_MODE_DESCS[mode] || "";

  // Mostrar/ocultar secciones de config
  const secIvr    = document.getElementById("sec-ivr-config");
  const secBridge = document.getElementById("sec-bridge-config");
  const trigWrap  = document.getElementById("bridge-trigger-wrap");

  if (secIvr)    secIvr.classList.toggle("visible",    mode === "ivr" || mode === "ivr_bridge");
  if (secBridge) secBridge.classList.toggle("visible", mode === "bridge" || mode === "ivr_bridge");
  if (trigWrap)  trigWrap.style.display = mode === "ivr_bridge" ? "flex" : "none";
}

document.querySelectorAll(".call-mode-btn").forEach(btn => {
  btn.addEventListener("click", () => setCallMode(btn.dataset.mode));
});

// ══════════════════════════════════════════════════════════════
//  EVENTOS DE CAMPAÑA
// ══════════════════════════════════════════════════════════════

function onIvrStatus({ processed, total, running }) {
  const sp = document.getElementById("ivr-stat-processed");
  const st = document.getElementById("ivr-stat-total");
  const pb = document.getElementById("ivr-progress-bar");
  if (sp) sp.textContent = processed;
  if (st) st.textContent = total;
  if (pb && total > 0) pb.style.width = ((processed / total) * 100) + "%";
  if (running === false && ivrRunning) endCampaign();
}

function onCallUpdate({ number, status }) {
  const cn = document.getElementById("ivr-call-number");
  const cc = document.getElementById("ivr-current-call");
  const cs = document.getElementById("ivr-campaign-status");
  if (cn) cn.textContent = number || "";
  if (cc) cc.hidden = !number;
  if (cs) cs.textContent = status || "";
  if (number) {
    const pill = document.getElementById("ivr-pill-" + number);
    if (pill) {
      const cls = {
        CALLING: "p-call", ACTIVE: "p-act", ANSWERED_TONE: "p-ok",
        ANSWERED_NO_TONE: "p-warn", NO_ANSWER: "p-warn", DISCONNECTED: "p-warn",
        DISCONNECTED_DURING_CALL: "p-disc", UNAVAILABLE: "p-off", ERROR: "p-err",
        BRIDGE_ACTIVE: "p-brdg",
      };
      const labels = {
        CALLING: "📞 Marcando", ACTIVE: "🟢 Activa", ANSWERED_TONE: "✅ Tono",
        ANSWERED_NO_TONE: "⚠️ Sin tono", NO_ANSWER: "📭 No contestó",
        DISCONNECTED: "❌ Desconect.", DISCONNECTED_DURING_CALL: "📵 Colgó",
        UNAVAILABLE: "⛔ No disponible", ERROR: "❌ Error",
        BRIDGE_ACTIVE: "🎧 Puente",
      };
      pill.className = "pill " + (cls[status] || "p-pend");
      pill.textContent = labels[status] || status;
    }
    if (status === "DISCONNECTED_DURING_CALL")
      addLog("📵 " + number + " — colgó durante la llamada", "warn");
    else if (status === "UNAVAILABLE")
      addLog("⛔ " + number + " — apagado o no disponible", "warn");
  }
}

function onIvrDigit({ number, digit, option }) {
  addLog("🎯 " + number + " → Tono " + digit + ": " + option, "ok");
  const el = document.getElementById("ivr-digit-" + number);
  if (el) el.innerHTML = '<span class="pill p-ok">' + digit + '</span>';
}

function endCampaign() {
  ivrRunning = false;
  const lb = document.getElementById("ivr-launch-btn"); if (lb) lb.disabled = false;
  const sb = document.getElementById("ivr-stop-btn");   if (sb) sb.disabled = true;
  const pb = document.getElementById("ivr-pause-btn");  if (pb) pb.disabled = true;
  const cs = document.getElementById("ivr-campaign-status"); if (cs) cs.textContent = "Finalizada";
  const dl = document.getElementById("ivr-download-btn");    if (dl) dl.style.display = "inline-flex";
  addLog("✅ Campaña finalizada", "ok");
}

// ══════════════════════════════════════════════════════════════
//  ESTADO MANUAL
// ══════════════════════════════════════════════════════════════

const MS_LABELS = { IDLE: "Inactivo", DIALING: "Marcando…", ACTIVE: "En llamada", ENDED: "Finalizada", ERROR: "Error" };

function _manualSetState(state, number) {
  const dot = document.getElementById("manual-dot");
  const lbl = document.getElementById("manual-state-lbl");
  const num = document.getElementById("manual-state-num");
  const bd  = document.getElementById("btn-manual-dial");
  const bh  = document.getElementById("btn-manual-hangup");
  if (dot) dot.className = "msdot " + state.toLowerCase();
  if (lbl) lbl.textContent = MS_LABELS[state] || state;
  const showNum = number && state !== "IDLE" && state !== "ENDED" && state !== "ERROR";
  if (num) { num.hidden = !showNum; if (showNum) num.textContent = number; }
  const calling = state === "DIALING" || state === "ACTIVE";
  _manualActive = calling;
  if (bd) bd.disabled = calling;
  if (bh) bh.disabled = !calling;
  const lb = document.getElementById("ivr-launch-btn");
  if (lb && !ivrRunning) lb.disabled = calling;
}

function _manualLog(msg, level) {
  const el = document.getElementById("manual-log"); if (!el) return;
  const d = document.createElement("div");
  d.className = "ml-line " + (level === "success" ? "success" : level === "error" ? "error" : level || "info");
  const ts = new Date().toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  d.textContent = "[" + ts + "] " + msg;
  el.appendChild(d); el.scrollTop = el.scrollHeight;
  // Espejo en log principal
  addLog("📞 [Manual] " + msg, level === "success" ? "ok" : level === "error" ? "err" : level);
}

// ══════════════════════════════════════════════════════════════
//  BRIDGE STATE
// ══════════════════════════════════════════════════════════════

function _onBridgeState(state) {
  addLog("🎧 Puente: " + state, state === "ACTIVE" ? "ok" : "info");
}

// ══════════════════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════════════════

(function init() {
  const E = id => document.getElementById(id);

  function debounce(fn, ms) {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  // ── Dispositivos de audio ─────────────────────────────────────
  let _audioLoaded = false;
  async function _loadAudioDevices() {
    try {
      const r = await fetch("/ivr/audio_devices");
      const d = await r.json();

      const fillSel = (selId, list, showDefault, defaultTxt) => {
        const sel = E(selId); if (!sel) return;
        const prev = sel.value;
        sel.innerHTML = '<option value="">' + defaultTxt + '</option>';
        (list || []).forEach(dev => {
          const o = document.createElement("option");
          o.value = dev.index;
          o.textContent = (dev.is_default ? "⭐ " : "") + dev.name + " (" + dev.samplerate + " Hz)";
          sel.appendChild(o);
        });
        if (prev) sel.value = prev;
      };

      fillSel("ivr-audio-device",   d.inputs,  true, "🖥️ Predeterminado");
      fillSel("ivr-output-device",  d.outputs, true, "🔊 Predeterminado");
      fillSel("bridge-phone-in",    d.inputs,  false, "🎤 Sin seleccionar");
      fillSel("bridge-phone-out",   d.outputs, false, "🔊 Sin seleccionar");
      fillSel("bridge-pc-mic",      d.inputs,  true,  "🖥️ Predeterminado");
      fillSel("bridge-pc-spk",      d.outputs, true,  "🔊 Predeterminado");

      if (d.ok) {
        addLog("🎤 " + (d.inputs||[]).length + " entrada(s) · 🔊 " + (d.outputs||[]).length + " salida(s)", "ok");
      } else {
        addLog("⚠️ " + (d.error || "Error cargando dispositivos"), "warn");
      }
    } catch(e) { addLog("❌ Error dispositivos audio: " + e.message, "err"); }
  }
  const loadAudioDevices = debounce(_loadAudioDevices, 500);
  E("ivr-refresh-audio")?.addEventListener("click", _loadAudioDevices);
  loadAudioDevices();

  E("ivr-audio-device")?.addEventListener("change", () => {
    fetch("/ivr/monitor/stop", { method: "POST" });
  });

  // ── Test entrada ──────────────────────────────────────────────
  E("btn-test-input")?.addEventListener("click", async () => {
    const btn = E("btn-test-input");
    const wrap = E("input-level-wrap");
    const bar  = E("input-level-bar");
    const txt  = E("input-level-txt");
    const devIdx = E("ivr-audio-device")?.value;
    const deviceIndex = (devIdx !== "" && devIdx != null) ? parseInt(devIdx) : null;
    if (btn)  { btn.disabled = true; btn.textContent = "⏳ 3s…"; }
    if (wrap) wrap.style.display = "block";
    if (bar)  bar.style.width = "0%";
    if (txt)  txt.textContent = "🎤 Habla cerca del micrófono…";
    addLog("🎤 Prueba de entrada (3 segundos)…", "info");
    try {
      await fetch("/ivr/test_input", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_index: deviceIndex })
      });
    } catch(e) {
      addLog("❌ Error: " + e.message, "err");
      if (btn) { btn.disabled = false; btn.textContent = "🎤 Test"; }
    }
  });

  // ── Test salida ───────────────────────────────────────────────
  E("btn-test-output")?.addEventListener("click", async () => {
    const btn = E("btn-test-output");
    const devIdx = E("ivr-output-device")?.value;
    const deviceIndex = (devIdx !== "" && devIdx != null) ? parseInt(devIdx) : null;
    if (btn) { btn.disabled = true; btn.textContent = "🔊 Reproduciendo…"; }
    addLog("🔊 Reproduciendo pitido de prueba…", "info");
    try {
      await fetch("/ivr/test_output", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_index: deviceIndex })
      });
    } catch(e) { addLog("❌ Error: " + e.message, "err"); }
    finally { setTimeout(() => { if (btn) { btn.disabled = false; btn.textContent = "🔊 Test"; } }, 1600); }
  });

  // ── Dispositivos ADB ──────────────────────────────────────────
  async function _loadADB() {
    try {
      const r = await fetch("/ivr/devices"); const d = await r.json();
      const sel = E("ivr-device"); if (!sel) return;
      sel.innerHTML = '<option value="">— Seleccionar —</option>';
      (d.devices || []).forEach(dev => {
        const o = document.createElement("option"); o.value = dev; o.textContent = dev; sel.appendChild(o);
      });
      if (d.devices?.length) addLog("📱 " + d.devices.length + " dispositivo(s) ADB", "ok");
      else addLog("⚠️ Sin dispositivos ADB", "warn");
    } catch(e) { addLog("❌ Error ADB: " + e.message, "err"); }
  }
  E("ivr-refresh-devices")?.addEventListener("click", _loadADB);
  _loadADB();

  // ADB status dot
  function _adbUpdateDot(connected, deviceId) {
    const dot = E("adb-status-dot"); const txt = E("adb-status-txt");
    if (!dot || !txt) return;
    if (!deviceId) { dot.className = "dot"; txt.textContent = "Sin dispositivo"; return; }
    dot.className = connected ? "dot on" : "dot err";
    txt.textContent = connected ? "Conectado: " + deviceId : "⚠ DESCONECTADO…";
  }
  ivrSocket.on("adb_status", ({ connected, device_id }) => {
    _adbUpdateDot(connected, device_id);
    if (!connected && ivrRunning) addLog("⚠️ ADB desconectado — campaña pausada", "warn");
    else if (connected && ivrRunning) addLog("✅ ADB reconectado", "ok");
  });
  async function _pollADBStatus() {
    const sel = E("ivr-device");
    const deviceId = sel?.value?.trim();
    if (!deviceId || ivrRunning) return;
    try {
      const r = await fetch("/ivr/adb/status?device_id=" + encodeURIComponent(deviceId));
      const d = await r.json();
      _adbUpdateDot(d.connected, deviceId);
    } catch (e) {}
  }
  setInterval(_pollADBStatus, 5000);
  E("ivr-device")?.addEventListener("change", () => {
    const v = E("ivr-device")?.value;
    if (!v) { _adbUpdateDot(false, null); return; }
    fetch("/ivr/adb/status?device_id=" + encodeURIComponent(v))
      .then(r => r.json()).then(d => _adbUpdateDot(d.connected, v)).catch(() => {});
  });

  // ── Excel ─────────────────────────────────────────────────────
  let ivrNumbers = [];
  E("ivr-excel-input")?.addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    const nm = E("ivr-excel-name"); if (nm) nm.textContent = f.name;
    const reader = new FileReader();
    reader.onload = ev => {
      try {
        const wb = XLSX.read(new Uint8Array(ev.target.result), { type: "array" });
        const ws = wb.Sheets[wb.SheetNames[0]];
        ivrNumbers = XLSX.utils.sheet_to_json(ws).map(r =>
          String(r.Celular||r.celular||r.CELULAR||r.Numero||r.numero||r.NUMERO||Object.values(r)[0]||"")
            .replace(/[\s\-\(\)]/g, "")).filter(Boolean);
        const badge = E("ivr-numbers-badge"); if (badge) badge.hidden = false;
        const cnt   = E("ivr-numbers-count"); if (cnt) cnt.textContent = ivrNumbers.length + " números";
        addLog("📋 " + ivrNumbers.length + " números cargados de " + f.name, "ok");
        buildQueue(ivrNumbers);
        const lb = E("ivr-launch-btn"); if (lb) lb.disabled = false;
      } catch(err) { addLog("❌ Error Excel: " + err.message, "err"); }
    };
    reader.readAsArrayBuffer(f);
  });

  // ── Audios IVR ────────────────────────────────────────────────
  const audioPaths = { welcome: null, menu: null, no_tone: null };
  async function uploadAudio(file, type) {
    addLog("⏳ Subiendo audio " + type + "…", "info");
    const fd = new FormData(); fd.append("file", file); fd.append("type", type);
    try {
      const r = await fetch("/ivr/upload_audio", { method: "POST", body: fd });
      const d = await r.json();
      if (d.ok) { audioPaths[type] = d.path; addLog("✅ Audio " + type + " listo: " + file.name, "ok"); }
      else addLog("❌ " + (d.error || "Error subiendo audio"), "err");
    } catch(e) { addLog("❌ Red: " + e.message, "err"); }
  }
  E("ivr-audio-welcome-input")?.addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    E("ivr-audio-welcome-name").textContent = f.name; uploadAudio(f, "welcome");
  });
  E("ivr-audio-menu-input")?.addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    E("ivr-audio-menu-name").textContent = f.name; uploadAudio(f, "menu");
  });
  E("ivr-audio-notone-input")?.addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    E("ivr-audio-notone-name").textContent = f.name; uploadAudio(f, "no_tone");
  });

  // ── Opciones IVR ──────────────────────────────────────────────
  const optByePaths = {};
  let optCounter = 0;
  E("ivr-add-option")?.addEventListener("click", () => {
    const list = E("ivr-options-list"); if (!list) return;
    const rid = "opt-" + (++optCounter);
    const row = document.createElement("div");
    row.className = "opt-row"; row.dataset.rid = rid;
    row.innerHTML = `
      <div class="opt-row-top">
        <input type="text" class="finp opt-digit" placeholder="1" maxlength="1" style="width:28px;text-align:center;flex-shrink:0;padding:4px">
        <input type="text" class="finp opt-desc" placeholder="Descripción" style="flex:1;padding:4px">
        <button class="xbtn xr" style="padding:2px 7px">✕</button>
      </div>
      <div class="opt-row-bye">
        <label>🎵 Despedida:</label>
        <label class="xbtn xg" for="bye-${rid}" style="padding:2px 6px;font-size:10px">Audio</label>
        <input type="file" id="bye-${rid}" class="opt-bye-input" accept="audio/*,video/*" hidden>
        <span class="fname opt-bye-name">Global (predeterminada)</span>
      </div>`;
    row.querySelector("button").addEventListener("click", () => { delete optByePaths[rid]; row.remove(); });
    const fi = row.querySelector(".opt-bye-input");
    fi.addEventListener("change", async e => {
      const f = e.target.files[0]; if (!f) return;
      const nm = row.querySelector(".opt-bye-name"); if (nm) nm.textContent = "⏳ " + f.name;
      const fd = new FormData(); fd.append("file", f); fd.append("type", "bye_" + rid);
      try {
        const r = await fetch("/ivr/upload_audio", { method: "POST", body: fd });
        const d = await r.json();
        if (d.ok) { optByePaths[rid] = d.path; if (nm) nm.textContent = "✅ " + f.name; }
        else if (nm) nm.textContent = "❌ Error";
      } catch { if (row.querySelector(".opt-bye-name")) row.querySelector(".opt-bye-name").textContent = "❌ Red"; }
    });
    list.appendChild(row);
  });

  // ── Cola de llamadas ──────────────────────────────────────────
  function buildQueue(numbers) {
    const tb = E("ivr-queue-tbody"); if (!tb) return;
    tb.innerHTML = "";
    numbers.forEach((num, i) => {
      const tr = document.createElement("tr"); tr.id = "ivr-row-" + num;
      tr.innerHTML = `<td>${i+1}</td><td>${num}</td>
        <td><span class="pill p-pend" id="ivr-pill-${num}">Pendiente</span></td>
        <td id="ivr-digit-${num}">—</td>`;
      tb.appendChild(tr);
    });
  }

  // ── Lanzar campaña ────────────────────────────────────────────
  async function startCampaign(numbers, isTest) {
    const devSel = E("ivr-device");
    if (!devSel?.value) return addLog("⚠️ Selecciona un dispositivo ADB", "warn");
    if (!numbers.length)  return addLog("⚠️ Sin números en la lista", "warn");

    // Guard: llamada manual activa
    if (_manualActive) return addLog("⚠️ Hay una llamada manual activa. Cuélgala primero.", "warn");

    const inVal  = E("ivr-audio-device")?.value;
    const outVal = E("ivr-output-device")?.value;
    const audioInIndex  = (inVal  !== "" && inVal  != null) ? parseInt(inVal)  : null;
    const audioOutIndex = (outVal !== "" && outVal != null) ? parseInt(outVal) : null;

    // Configuración del puente
    const bridgeConfig = {
      phone_in_idx:   _getSelIdx("bridge-phone-in")  ,
      phone_out_idx:  _getSelIdx("bridge-phone-out") ,
      pc_mic_idx:     _getSelIdx("bridge-pc-mic")    ,
      pc_speaker_idx: _getSelIdx("bridge-pc-spk")    ,
      trigger_digit:  E("bridge-trigger-digit")?.value || "0",
    };

    const config = {
      numbers,
      device_id:           devSel.value,
      audio_device:        audioInIndex,
      audio_output_device: audioOutIndex,
      delay_seconds:  parseInt(E("ivr-delay")?.value) || 5,
      tone_timeout:   parseInt(E("ivr-tone-timeout")?.value) || 10,
      menu_repeats:   parseInt(E("ivr-menu-repeats")?.value) || 2,
      audio_welcome:  audioPaths.welcome,
      audio_menu:     audioPaths.menu,
      audio_no_tone:  audioPaths.no_tone,
      record_calls:   E("ivr-record-calls")?.checked || false,
      call_mode:      _callMode,
      bridge_config:  bridgeConfig,
      ivr_options:    {},
      is_test:        isTest,
    };
    E("ivr-options-list")?.querySelectorAll(".opt-row").forEach(r => {
      const d  = r.querySelector(".opt-digit")?.value?.trim();
      const de = r.querySelector(".opt-desc")?.value?.trim();
      const rid = r.dataset.rid;
      if (d && de) {
        const byePath = rid && optByePaths[rid] ? optByePaths[rid] : null;
        config.ivr_options[d] = byePath ? { desc: de, audio_bye: byePath } : de;
      }
    });

    ivrRunning = true;
    E("ivr-stop-btn").disabled  = false;
    E("ivr-pause-btn").disabled = false;
    E("ivr-launch-btn").disabled = true;
    addLog("🚀 Iniciando campaña (" + _callMode + ") con " + numbers.length + " número(s)…", "ok");

    try {
      const r = await fetch("/ivr/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config)
      });
      const d = await r.json();
      if (d.ok) addLog("✅ " + d.msg, "ok");
      else { addLog("❌ " + (d.error || "Error iniciando"), "err"); endCampaign(); }
    } catch(e) { addLog("❌ Red: " + e.message, "err"); endCampaign(); }
  }

  // ── Botones campaña ───────────────────────────────────────────
  E("ivr-test-btn")?.addEventListener("click", () => { E("ivr-test-modal").hidden = false; });
  E("ivr-test-cancel")?.addEventListener("click", () => { E("ivr-test-modal").hidden = true; });
  E("ivr-test-confirm")?.addEventListener("click", () => {
    const num = E("ivr-test-number")?.value.trim(); if (!num) return;
    E("ivr-test-modal").hidden = true;
    startCampaign([num], true);
  });
  E("ivr-launch-btn")?.addEventListener("click", () => startCampaign(ivrNumbers, false));
  E("ivr-stop-btn")?.addEventListener("click", () => {
    addLog("⏹ Deteniendo campaña…", "warn");
    fetch("/ivr/stop", { method: "POST" });
    fetch("/ivr/monitor/stop", { method: "POST" });
  });
  E("ivr-pause-btn")?.addEventListener("click", () => {
    addLog("⏸ Pausar campaña (función próximamente)…", "info");
  });
  E("ivr-clear-log")?.addEventListener("click",   () => { const l = E("ivr-log");   if (l) l.innerHTML = ""; });
  E("ivr-clear-log-m")?.addEventListener("click", () => { const l = E("ivr-log-m"); if (l) l.innerHTML = ""; });

  // ══════════════════════════════════════════════════════════════
  //  MARCACIÓN MANUAL
  // ══════════════════════════════════════════════════════════════

  // Numpad
  document.querySelectorAll(".np-btn[data-digit]").forEach(btn => {
    btn.addEventListener("click", () => {
      const inp = E("manual-number");
      if (!inp || _manualActive) return;
      if (inp.value.length < 20) inp.value += btn.dataset.digit;
      inp.focus();
    });
  });
  E("numpad-backspace")?.addEventListener("click", () => {
    const inp = E("manual-number");
    if (inp && !_manualActive) { inp.value = inp.value.slice(0, -1); inp.focus(); }
  });
  E("manual-num-clear")?.addEventListener("click", () => {
    const inp = E("manual-number");
    if (inp && !_manualActive) { inp.value = ""; inp.focus(); }
  });
  E("manual-number")?.addEventListener("keydown", e => {
    if (e.key === "Enter") E("btn-manual-dial")?.click();
  });

  // Botón LLAMAR
  E("btn-manual-dial")?.addEventListener("click", async () => {
    const inp    = E("manual-number");
    const number = inp?.value.trim();
    if (!number) { _manualLog("⚠️ Ingresa un número.", "warn"); inp?.focus(); return; }
    const digits = number.replace(/[+\-\s]/g, "");
    if (!/^\d+$/.test(digits) || digits.length < 6) {
      _manualLog("❌ Número inválido (mínimo 6 dígitos).", "error"); return;
    }
    const deviceId = E("ivr-device")?.value?.trim();
    if (!deviceId) {
      _manualLog("❌ Selecciona un dispositivo ADB en Configuración.", "error"); return;
    }
    if (ivrRunning) { _manualLog("❌ Hay una campaña activa. Detenla primero.", "error"); return; }

    const inVal  = E("ivr-audio-device")?.value;
    const outVal = E("ivr-output-device")?.value;
    const audioIn  = (inVal  !== "" && inVal  != null) ? parseInt(inVal)  : null;
    const audioOut = (outVal !== "" && outVal != null) ? parseInt(outVal) : null;

    _manualLog("📞 Marcando " + number + "…", "info");
    _manualSetState("DIALING", number);

    try {
      const r = await fetch("/ivr/manual/dial", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          number, device_id: deviceId,
          audio_device: audioIn, audio_output_device: audioOut,
        }),
      });
      const d = await r.json();
      if (!d.ok) {
        _manualLog("❌ " + (d.error || "Error al marcar"), "error");
        _manualSetState("ERROR", null);
      }
    } catch(e) {
      _manualLog("❌ Error de red: " + e.message, "error");
      _manualSetState("ERROR", null);
    }
  });

  // Botón COLGAR
  E("btn-manual-hangup")?.addEventListener("click", async () => {
    _manualLog("🔴 Colgando…", "warn");
    try {
      const r = await fetch("/ivr/manual/hangup", { method: "POST" });
      const d = await r.json();
      if (!d.ok) _manualLog("⚠️ " + (d.error || "Sin llamada activa"), "warn");
    } catch(e) { _manualLog("❌ Error de red: " + e.message, "error"); }
  });

  // Restaurar estado manual al recargar
  fetch("/ivr/manual/status").then(r => r.json()).then(d => {
    if (d.active) {
      _manualSetState(d.state, d.number);
      _manualLog("ℹ️ Llamada recuperada: " + d.number, "info");
      switchMainTab("manual");
    }
  }).catch(() => {});

  // ══════════════════════════════════════════════════════════════
  //  PLANTILLAS
  // ══════════════════════════════════════════════════════════════

  async function _loadTemplates() {
    try {
      const r = await fetch("/ivr/templates");
      const d = await r.json();
      const sel = E("tmpl-select"); if (!sel) return;
      const prev = sel.value;
      sel.innerHTML = '<option value="">— Sin plantilla —</option>';
      (d.templates || []).forEach(t => {
        const o = document.createElement("option");
        o.value = t.slug;
        o.textContent = t.name + "  [" + t.call_mode + "]";
        sel.appendChild(o);
      });
      if (prev) sel.value = prev;
    } catch(e) { addLog("❌ Error cargando plantillas: " + e.message, "err"); }
  }

  // Cargar plantilla
  E("tmpl-load-btn")?.addEventListener("click", async () => {
    const slug = E("tmpl-select")?.value;
    if (!slug) { addLog("⚠️ Selecciona una plantilla primero.", "warn"); return; }
    try {
      const r = await fetch("/ivr/templates/" + encodeURIComponent(slug));
      const d = await r.json();
      if (!d.ok) { addLog("❌ " + (d.error || "Error cargando"), "err"); return; }
      const t = d.template;

      // Aplicar modo de llamada
      if (t.call_mode) setCallMode(t.call_mode);

      // Aplicar timers
      if (t.delay_seconds  !== undefined && E("ivr-delay"))        E("ivr-delay").value = t.delay_seconds;
      if (t.tone_timeout   !== undefined && E("ivr-tone-timeout")) E("ivr-tone-timeout").value = t.tone_timeout;
      if (t.menu_repeats   !== undefined && E("ivr-menu-repeats")) E("ivr-menu-repeats").value = t.menu_repeats;

      // Grabación
      if (t.record_calls !== undefined && E("ivr-record-calls")) E("ivr-record-calls").checked = t.record_calls;

      // Trigger digit
      if (t.bridge_trigger_digit && E("bridge-trigger-digit"))
        E("bridge-trigger-digit").value = t.bridge_trigger_digit;

      // Nombres de audios (solo info)
      const showAudioName = (key, nameId) => {
        const path = t[key];
        if (path) {
          const fn = path.split(/[/\\]/).pop();
          const el = E(nameId); if (el) el.textContent = "📂 " + fn;
          audioPaths[key === "audio_welcome" ? "welcome" : key === "audio_menu" ? "menu" : "no_tone"] = path;
        }
      };
      showAudioName("audio_welcome", "ivr-audio-welcome-name");
      showAudioName("audio_menu",    "ivr-audio-menu-name");
      showAudioName("audio_no_tone", "ivr-audio-notone-name");

      // WA config
      if (t.wa_contact !== undefined && E("wa-contact")) E("wa-contact").value = t.wa_contact || "";
      if (t.wa_backup  !== undefined && E("wa-backup"))  E("wa-backup").value  = t.wa_backup  || "";
      if (t.wa_enabled !== undefined && E("wa-enabled")) E("wa-enabled").checked = !!t.wa_enabled;

      const missingAudio = t._missing_audio;
      if (missingAudio?.length) {
        addLog("⚠️ Plantilla cargada (audios faltantes: " + missingAudio.join(", ") + ")", "warn");
      } else {
        addLog("✅ Plantilla '" + t.name + "' cargada", "ok");
      }
    } catch(e) { addLog("❌ Error de red: " + e.message, "err"); }
  });

  // Guardar plantilla (modal)
  E("tmpl-save-btn")?.addEventListener("click", () => {
    const name = E("tmpl-name")?.value.trim() || "";
    E("tmpl-save-name").value = name;
    E("tmpl-save-modal").hidden = false;
  });
  E("tmpl-save-cancel")?.addEventListener("click", () => { E("tmpl-save-modal").hidden = true; });
  E("tmpl-save-confirm")?.addEventListener("click", async () => {
    const name = E("tmpl-save-name")?.value.trim();
    if (!name) { addLog("⚠️ El nombre de la plantilla es obligatorio.", "warn"); return; }
    E("tmpl-save-modal").hidden = true;

    const payload = {
      name,
      call_mode:           _callMode,
      delay_seconds:       parseInt(E("ivr-delay")?.value) || 5,
      tone_timeout:        parseInt(E("ivr-tone-timeout")?.value) || 10,
      menu_repeats:        parseInt(E("ivr-menu-repeats")?.value) || 2,
      record_calls:        E("ivr-record-calls")?.checked || false,
      audio_welcome:       audioPaths.welcome,
      audio_menu:          audioPaths.menu,
      audio_no_tone:       audioPaths.no_tone,
      bridge_trigger_digit: E("bridge-trigger-digit")?.value || "0",
      wa_enabled:          E("wa-enabled")?.checked || false,
      wa_contact:          E("wa-contact")?.value || "",
      wa_backup:           E("wa-backup")?.value  || "",
    };

    try {
      const r = await fetch("/ivr/templates", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (d.ok) {
        addLog("✅ Plantilla '" + name + "' guardada (slug: " + d.slug + ")", "ok");
        await _loadTemplates();
        const sel = E("tmpl-select"); if (sel) sel.value = d.slug;
        if (E("tmpl-name")) E("tmpl-name").value = name;
      } else { addLog("❌ " + (d.error || "Error guardando"), "err"); }
    } catch(e) { addLog("❌ Error de red: " + e.message, "err"); }
  });

  // Eliminar plantilla
  E("tmpl-del-btn")?.addEventListener("click", async () => {
    const slug = E("tmpl-select")?.value;
    if (!slug) { addLog("⚠️ Selecciona una plantilla para eliminar.", "warn"); return; }
    if (!confirm("¿Eliminar la plantilla seleccionada?")) return;
    try {
      const r = await fetch("/ivr/templates/" + encodeURIComponent(slug), { method: "DELETE" });
      const d = await r.json();
      if (d.ok) { addLog("🗑 " + d.msg, "info"); await _loadTemplates(); }
      else addLog("❌ " + (d.error || "Error"), "err");
    } catch(e) { addLog("❌ Error de red: " + e.message, "err"); }
  });

  _loadTemplates();

  // ══════════════════════════════════════════════════════════════
  //  NOTIFICACIONES WHATSAPP
  // ══════════════════════════════════════════════════════════════

  const WA_DOTS = { closed: [], opening: ["wa-open"], ready: ["wa-ready"], error: ["wa-err"], unavailable: ["wa-err"] };
  const WA_LBLS = { closed: "Navegador cerrado", opening: "Abriendo…", ready: "WhatsApp listo ✓", error: "Error", unavailable: "selenium no instalado" };

  function _waUpdateDot(status, message, queueSize) {
    const dot = E("wa-status-dot"); const txt = E("wa-status-txt"); const badge = E("wa-queue-badge");
    if (dot) dot.className = "dot " + (WA_DOTS[status] || []).join(" ");
    if (txt) txt.textContent = message || WA_LBLS[status] || status;
    if (badge) { badge.hidden = !queueSize; if (queueSize) badge.textContent = queueSize + " en cola"; }
  }

  async function _waLoadConfig() {
    try {
      const r = await fetch("/ivr/wa/config"); const d = await r.json(); if (!d.ok) return;
      const cfg = d.config || {};
      if (E("wa-enabled")) E("wa-enabled").checked = !!cfg.enabled;
      if (E("wa-contact")) E("wa-contact").value = cfg.contact || "";
      if (E("wa-backup"))  E("wa-backup").value  = cfg.backup  || "";
      const br = d.browser || {};
      _waUpdateDot(br.status || "closed", br.message, br.queue_size || 0);
      if (!d.available) _waUpdateDot("unavailable", "⚠ pip install selenium webdriver-manager", 0);
    } catch {}
  }

  async function _waSaveConfig() {
    try {
      await fetch("/ivr/wa/config", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: E("wa-enabled")?.checked || false, contact: E("wa-contact")?.value?.trim() || "", backup: E("wa-backup")?.value?.trim() || "" })
      });
    } catch {}
  }

  E("wa-enabled")?.addEventListener("change", _waSaveConfig);
  E("wa-contact")?.addEventListener("input",  debounce(_waSaveConfig, 800));
  E("wa-backup")?.addEventListener("input",   debounce(_waSaveConfig, 800));

  E("wa-open-browser")?.addEventListener("click", async () => {
    const btn = E("wa-open-browser");
    if (btn) { btn.disabled = true; btn.textContent = "⏳ Abriendo…"; }
    _waUpdateDot("opening", "Iniciando Chrome…", 0); addLog("🌐 Abriendo Chrome…", "info");
    try {
      const r = await fetch("/ivr/wa/open_browser", { method: "POST" }); const d = await r.json();
      if (d.ok) { addLog("✅ " + d.msg, "ok"); _waUpdateDot("opening", "Escanea el QR…", 0); }
      else { addLog("❌ " + (d.error || d.msg || "Error"), "err"); _waUpdateDot("error", d.error || "Error", 0); }
    } catch(e) { addLog("❌ " + e.message, "err"); _waUpdateDot("error", "Error de red", 0); }
    finally { if (btn) { btn.disabled = false; btn.innerHTML = "🌐 Abrir WhatsApp"; } }
  });

  E("wa-close-browser")?.addEventListener("click", async () => {
    try {
      const r = await fetch("/ivr/wa/close_browser", { method: "POST" }); const d = await r.json();
      if (d.ok) { addLog("🔴 Navegador cerrado", "warn"); _waUpdateDot("closed", "Navegador cerrado", 0); }
    } catch {}
  });

  setInterval(async () => {
    try {
      const r = await fetch("/ivr/wa/status"); const d = await r.json();
      _waUpdateDot(d.status || "closed", d.message, d.queue_size || 0);
    } catch {}
  }, 4000);

  _waLoadConfig();

})();
