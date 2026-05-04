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
  if (btn) { btn.disabled = false; btn.textContent = "🎤 Probar"; }
  setTimeout(() => { if (wrap) wrap.style.display = "none"; }, 4000);
});

// ── Log ───────────────────────────────────────────────────────
function addLog(msg, cls) {
  const el = document.getElementById("ivr-log"); if (!el) return;
  const d = document.createElement("div");
  d.className = "log-line " + (cls || "info");
  d.textContent = msg;
  el.appendChild(d);
  el.scrollTop = el.scrollHeight;
}

// ── Eventos servidor ──────────────────────────────────────────
let ivrRunning = false;

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
      const cls = { CALLING:"p-call", ACTIVE:"p-act", ANSWERED_TONE:"p-ok",
                    ANSWERED_NO_TONE:"p-warn", NO_ANSWER:"p-warn", ERROR:"p-err" };
      pill.className = "pill " + (cls[status] || "p-pend");
      pill.textContent = status;
    }
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
  const cs = document.getElementById("ivr-campaign-status"); if (cs) cs.textContent = "Finalizada";
  const dl = document.getElementById("ivr-download-btn");    if (dl) dl.style.display = "inline-flex";
  addLog("✅ Campaña finalizada", "ok");
}

// ── Init ──────────────────────────────────────────────────────
(function init() {
  const E = id => document.getElementById(id);

  // ── Dispositivos de audio (Python los enumera) ──
  async function loadAudioDevices() {
    try {
      const r = await fetch("/ivr/audio_devices");
      const d = await r.json();

      // Entradas (micrófono)
      const selIn = E("ivr-audio-device");
      if (selIn) {
        const prevIn = selIn.value;
        selIn.innerHTML = '<option value="">🖥️ Predeterminado del sistema</option>';
        (d.inputs || []).forEach(dev => {
          const o = document.createElement("option");
          o.value = dev.index;
          o.textContent = (dev.is_default ? "⭐ " : "") + dev.name + " (" + dev.samplerate + " Hz)";
          selIn.appendChild(o);
        });
        if (prevIn) selIn.value = prevIn;
      }

      // Salidas (altavoz / auriculares)
      const selOut = E("ivr-output-device");
      if (selOut) {
        const prevOut = selOut.value;
        selOut.innerHTML = '<option value="">🔊 Predeterminado del sistema</option>';
        (d.outputs || []).forEach(dev => {
          const o = document.createElement("option");
          o.value = dev.index;
          o.textContent = (dev.is_default ? "⭐ " : "") + dev.name + " (" + dev.samplerate + " Hz)";
          selOut.appendChild(o);
        });
        if (prevOut) selOut.value = prevOut;
      }

      if (d.ok) {
        const ni = (d.inputs || []).length, no = (d.outputs || []).length;
        addLog("🎤 " + ni + " entrada(s) · 🔊 " + no + " salida(s) de audio detectadas", "ok");
      } else {
        addLog("⚠️ " + (d.error || "Error cargando dispositivos de audio"), "warn");
      }
    } catch(e) { addLog("❌ Error dispositivos audio: " + e.message, "err"); }
  }

  E("ivr-refresh-audio")?.addEventListener("click", loadAudioDevices);
  loadAudioDevices();

  // Cambio de dispositivo de entrada: detener monitor si estaba activo
  E("ivr-audio-device")?.addEventListener("change", () => {
    fetch("/ivr/monitor/stop", { method: "POST" });
  });

  // ── Prueba de ENTRADA (micrófono) ──
  E("btn-test-input")?.addEventListener("click", async () => {
    const btn  = E("btn-test-input");
    const wrap = E("input-level-wrap");
    const bar  = E("input-level-bar");
    const txt  = E("input-level-txt");
    const devIdx = E("ivr-audio-device")?.value;
    const deviceIndex = (devIdx !== "" && devIdx != null) ? parseInt(devIdx) : null;

    if (btn)  { btn.disabled = true; btn.textContent = "⏳ Capturando 3s..."; }
    if (wrap) wrap.style.display = "block";
    if (bar)  bar.style.width = "0%";
    if (txt)  txt.textContent = "🎤 Habla cerca del micrófono...";
    addLog("🎤 Iniciando prueba de entrada de audio (3 segundos)...", "info");
    try {
      await fetch("/ivr/test_input", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_index: deviceIndex })
      });
    } catch(e) {
      addLog("❌ Error: " + e.message, "err");
      if (btn) { btn.disabled = false; btn.textContent = "🎤 Probar"; }
    }
  });

  // ── Prueba de SALIDA (altavoz / auriculares) ──
  E("btn-test-output")?.addEventListener("click", async () => {
    const btn = E("btn-test-output");
    const devIdx = E("ivr-output-device")?.value;
    const deviceIndex = (devIdx !== "" && devIdx != null) ? parseInt(devIdx) : null;

    if (btn) { btn.disabled = true; btn.textContent = "🔊 Reproduciendo..."; }
    addLog("🔊 Reproduciendo pitido de prueba 1kHz (1 segundo)...", "info");
    try {
      await fetch("/ivr/test_output", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_index: deviceIndex })
      });
    } catch(e) { addLog("❌ Error: " + e.message, "err"); }
    finally {
      setTimeout(() => {
        if (btn) { btn.disabled = false; btn.textContent = "🔊 Probar"; }
      }, 1600);
    }
  });

  // ── Dispositivos ADB ──
  async function loadADB() {
    try {
      const r = await fetch("/ivr/devices"); const d = await r.json();
      const sel = E("ivr-device"); if (!sel) return;
      sel.innerHTML = '<option value="">— Seleccionar —</option>';
      (d.devices || []).forEach(dev => {
        const o = document.createElement("option"); o.value = dev; o.textContent = dev; sel.appendChild(o);
      });
      if (d.devices?.length) addLog("📱 " + d.devices.length + " dispositivo(s) ADB", "ok");
      else addLog("⚠️ Sin dispositivos ADB conectados", "warn");
    } catch(e) { addLog("❌ Error ADB: " + e.message, "err"); }
  }
  E("ivr-refresh-devices")?.addEventListener("click", loadADB);
  loadADB();

  // ── Excel ──
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

  // ── Audios ──
  const audioPaths = { initial: null, middle: null };
  async function uploadAudio(file, type) {
    addLog("⏳ Subiendo audio " + type + "...", "info");
    const fd = new FormData(); fd.append("file", file); fd.append("type", type);
    try {
      const r = await fetch("/ivr/upload_audio", { method: "POST", body: fd });
      const d = await r.json();
      if (d.ok) { audioPaths[type] = d.path; addLog("✅ Audio " + type + " listo: " + file.name, "ok"); }
      else addLog("❌ " + (d.error || "Error subiendo audio"), "err");
    } catch(e) { addLog("❌ Red: " + e.message, "err"); }
  }
  E("ivr-audio-initial-input")?.addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    const nm = E("ivr-audio-initial-name"); if (nm) nm.textContent = f.name;
    uploadAudio(f, "initial");
  });
  E("ivr-audio-middle-input")?.addEventListener("change", e => {
    const f = e.target.files[0]; if (!f) return;
    const nm = E("ivr-audio-middle-name"); if (nm) nm.textContent = f.name;
    uploadAudio(f, "middle");
  });

  // ── Opciones IVR (con audio de despedida por opcion) ──
  const optByePaths = {};   // { rowId: serverPath }
  let optCounter = 0;

  E("ivr-add-option")?.addEventListener("click", () => {
    const list = E("ivr-options-list"); if (!list) return;
    const rid  = "opt-" + (++optCounter);
    const row  = document.createElement("div");
    row.className = "opt-row"; row.dataset.rid = rid;
    row.innerHTML = `
      <div class="opt-row-top">
        <input type="text" class="finp opt-digit" placeholder="1" maxlength="1"
               style="width:30px;text-align:center;flex-shrink:0">
        <input type="text" class="finp opt-desc" placeholder="Descripcion" style="flex:1">
        <button class="xbtn xr" style="padding:2px 8px">✕</button>
      </div>
      <div class="opt-row-bye">
        <label>🎵 Despedida:</label>
        <label class="xbtn xg" for="bye-${rid}" style="padding:2px 7px;font-size:10px">Elegir audio</label>
        <input type="file" id="bye-${rid}" class="opt-bye-input" accept="audio/*,video/*" hidden>
        <span class="fname opt-bye-name" style="max-width:160px">Global (predeterminada)</span>
      </div>`;
    row.querySelector("button").addEventListener("click", () => {
      delete optByePaths[rid]; row.remove();
    });
    const fileInput = row.querySelector(".opt-bye-input");
    fileInput.addEventListener("change", async e => {
      const f = e.target.files[0]; if (!f) return;
      const nm = row.querySelector(".opt-bye-name");
      if (nm) nm.textContent = "⏳ " + f.name;
      const fd = new FormData(); fd.append("file", f); fd.append("type", "bye_" + rid);
      try {
        const r = await fetch("/ivr/upload_audio", { method: "POST", body: fd });
        const d = await r.json();
        if (d.ok) {
          optByePaths[rid] = d.path;
          if (nm) nm.textContent = "✅ " + f.name;
          addLog("✅ Audio despedida cargado para opción " + (row.querySelector(".opt-digit")?.value || "?"), "ok");
        } else { if (nm) nm.textContent = "❌ Error"; }
      } catch(ex) { if (row.querySelector(".opt-bye-name")) row.querySelector(".opt-bye-name").textContent = "❌ Red"; }
    });
    list.appendChild(row);
  });

  // ── Cola ──
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

  // ── Lanzar campaña ──
  async function startCampaign(numbers, isTest) {
    const devSel = E("ivr-device");
    if (!devSel?.value) return addLog("⚠️ Selecciona un dispositivo ADB", "warn");
    if (!numbers.length) return addLog("⚠️ Sin números en la lista", "warn");

    // Dispositivos de audio
    const inVal  = E("ivr-audio-device")?.value;
    const outVal = E("ivr-output-device")?.value;
    const audioInIndex  = (inVal  !== "" && inVal  != null) ? parseInt(inVal)  : null;
    const audioOutIndex = (outVal !== "" && outVal != null) ? parseInt(outVal) : null;

    const config = {
      numbers,
      device_id:           devSel.value,
      audio_device:        audioInIndex,    // micrófono de entrada
      audio_output_device: audioOutIndex,   // altavoz de salida
      delay_seconds:  parseInt(E("ivr-delay")?.value) || 5,
      tone_timeout:   parseInt(E("ivr-tone-timeout")?.value) || 10,
      audio_initial:  audioPaths.initial,
      audio_middle:   audioPaths.middle,
      ivr_options:    {},
      is_test:        isTest
    };
    E("ivr-options-list")?.querySelectorAll(".opt-row").forEach(r => {
      const d  = r.querySelector(".opt-digit")?.value?.trim();
      const de = r.querySelector(".opt-desc")?.value?.trim();
      const rid = r.dataset.rid;
      if (d && de) {
        const byePath = rid && optByePaths[rid] ? optByePaths[rid] : null;
        // Si tiene audio propio → objeto {desc, audio_bye}; si no → solo string
        config.ivr_options[d] = byePath ? { desc: de, audio_bye: byePath } : de;
      }
    });

    ivrRunning = true;
    const sb = E("ivr-stop-btn");  if (sb) sb.disabled = false;
    const lb = E("ivr-launch-btn"); if (lb) lb.disabled = true;
    addLog("🚀 Iniciando campaña con " + numbers.length + " número(s)...", "ok");
    if (audioInIndex  !== null) addLog("🎤 Entrada: índice " + audioInIndex, "info");
    if (audioOutIndex !== null) addLog("🔊 Salida:  índice " + audioOutIndex, "info");

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

  // ── Botones ──
  E("ivr-test-btn")?.addEventListener("click", () => { E("ivr-test-modal").hidden = false; });
  E("ivr-test-cancel")?.addEventListener("click", () => { E("ivr-test-modal").hidden = true; });
  E("ivr-test-confirm")?.addEventListener("click", () => {
    const num = E("ivr-test-number")?.value.trim(); if (!num) return;
    E("ivr-test-modal").hidden = true;
    startCampaign([num], true);
  });
  E("ivr-launch-btn")?.addEventListener("click", () => startCampaign(ivrNumbers, false));
  E("ivr-stop-btn")?.addEventListener("click", () => {
    addLog("⏹ Deteniendo campaña...", "warn");
    fetch("/ivr/stop", { method: "POST" });
    fetch("/ivr/monitor/stop", { method: "POST" });
  });
  E("ivr-clear-log")?.addEventListener("click", () => {
    const log = E("ivr-log"); if (log) log.innerHTML = "";
  });
})();
