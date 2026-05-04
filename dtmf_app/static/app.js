/* ═══════════════════════════════════════════════════
   DTMF Analyzer — Frontend Logic
   ═══════════════════════════════════════════════════ */

"use strict";

// ── Color map (matches backend DIGIT_COLORS) ──────────────────
const DIGIT_COLORS = {
  "1":"#6366f1","2":"#8b5cf6","3":"#a855f7","4":"#ec4899",
  "5":"#f43f5e","6":"#ef4444","7":"#f97316","8":"#eab308",
  "9":"#22c55e","0":"#06b6d4","*":"#ffe082","#":"#e879f9",
  "A":"#b39ddb","B":"#80cbc4","C":"#ffcc02","D":"#ff7043",
};

// DTMF frequency lookup (for table display)
const DTMF_FREQS = {
  "1":"697+1209","2":"697+1336","3":"697+1477","A":"697+1633",
  "4":"770+1209","5":"770+1336","6":"770+1477","B":"770+1633",
  "7":"852+1209","8":"852+1336","9":"852+1477","C":"852+1633",
  "*":"941+1209","0":"941+1336","#":"941+1477","D":"941+1633",
};

// ── DOM refs ──────────────────────────────────────────────────
const dropZone       = document.getElementById("drop-zone");
const fileInput      = document.getElementById("file-input");
const filePreview    = document.getElementById("file-preview");
const fileNameEl     = document.getElementById("file-name");
const fileSizeEl     = document.getElementById("file-size");
const removeBtn      = document.getElementById("remove-btn");
const analyzeBtn     = document.getElementById("analyze-btn");
const progressSec    = document.getElementById("progress-section");
const progressLabel  = document.getElementById("progress-label");
const errorSec       = document.getElementById("error-section");
const errorMsg       = document.getElementById("error-msg");
const retryBtn       = document.getElementById("retry-btn");
const resultsSec     = document.getElementById("results-section");
const seqDigitsEl    = document.getElementById("seq-digits");
const copyBtn        = document.getElementById("copy-btn");
const newBtn         = document.getElementById("new-btn");
const chartImg       = document.getElementById("chart-img");
const tonesTbody     = document.getElementById("tones-tbody");

const STEPS = [
  "step-upload", "step-convert", "step-noise",
  "step-amplify", "step-goertzel", "step-chart"
];

let currentFile = null;
let currentSequence = "";

// ══════════════════════════════════════════════
//  Background particles canvas
// ══════════════════════════════════════════════
(function initParticles() {
  const canvas = document.getElementById("bg-canvas");
  const ctx    = canvas.getContext("2d");
  let W, H, particles;

  const COLORS = ["#6366f1","#06b6d4","#a855f7","#22c55e","#f43f5e"];

  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function makeParticle() {
    return {
      x: Math.random() * W,
      y: Math.random() * H,
      r: Math.random() * 1.8 + 0.4,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      alpha: Math.random() * 0.5 + 0.15,
    };
  }

  function init() {
    resize();
    particles = Array.from({ length: 110 }, makeParticle);
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (const p of particles) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.color;
      ctx.globalAlpha = p.alpha;
      ctx.fill();
      ctx.globalAlpha = 1;

      p.x += p.vx;
      p.y += p.vy;
      if (p.x < -5) p.x = W + 5;
      if (p.x > W + 5) p.x = -5;
      if (p.y < -5) p.y = H + 5;
      if (p.y > H + 5) p.y = -5;
    }
    requestAnimationFrame(draw);
  }

  window.addEventListener("resize", resize);
  init();
  draw();
})();

// ══════════════════════════════════════════════
//  File handling
// ══════════════════════════════════════════════
function formatBytes(bytes) {
  if (bytes < 1024)       return `${bytes} B`;
  if (bytes < 1048576)    return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1048576).toFixed(2)} MB`;
}

function setFile(file) {
  currentFile = file;
  fileNameEl.textContent = file.name;
  fileSizeEl.textContent = formatBytes(file.size);
  filePreview.hidden = false;
  document.getElementById("upload-icon").style.opacity = "0.4";
  document.querySelector(".upload-title").style.opacity = "0.4";
  document.querySelector(".upload-sub").style.opacity = "0.4";
  document.querySelector(".upload-formats").style.opacity = "0.4";
  document.getElementById("select-btn").hidden = true;
}

function clearFile() {
  currentFile = null;
  fileInput.value = "";
  filePreview.hidden = true;
  document.getElementById("upload-icon").style.opacity = "1";
  document.querySelector(".upload-title").style.opacity = "1";
  document.querySelector(".upload-sub").style.opacity = "1";
  document.querySelector(".upload-formats").style.opacity = "1";
  document.getElementById("select-btn").hidden = false;
}

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});

removeBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  clearFile();
});

// Drag & drop
dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});
dropZone.addEventListener("click", (e) => {
  if (e.target === dropZone || e.target.closest(".upload-card") === dropZone) {
    if (!currentFile) fileInput.click();
  }
});

// ══════════════════════════════════════════════
//  UI state helpers
// ══════════════════════════════════════════════
function showOnly(sectionId) {
  ["progress-section","error-section","results-section"].forEach(id => {
    document.getElementById(id).hidden = id !== sectionId;
  });
}

function setStep(index) {
  STEPS.forEach((id, i) => {
    const el = document.getElementById(id);
    el.classList.remove("active", "done");
    if (i < index)  el.classList.add("done");
    if (i === index) el.classList.add("active");
  });
  const labels = [
    "Subiendo archivo…",
    "Convirtiendo formato con ffmpeg…",
    "Reduciendo ruido de fondo…",
    "Amplificando señal débil…",
    "Detectando tonos DTMF (Goertzel)…",
    "Generando espectrograma…",
  ];
  progressLabel.textContent = labels[index] || "Procesando…";
}

// ══════════════════════════════════════════════
//  Render results
// ══════════════════════════════════════════════
function renderResults(data) {
  // Summary bar
  document.getElementById("res-filename").textContent  = data.filename;
  document.getElementById("res-duration").textContent  = `${data.duration_s} s`;
  document.getElementById("res-count").textContent     = data.tones.length;
  currentSequence = data.sequence;

  // Sequence digits
  seqDigitsEl.innerHTML = "";
  if (data.sequence) {
    [...data.sequence].forEach((ch, i) => {
      const span = document.createElement("span");
      span.className = "seq-digit";
      span.textContent = ch;
      const bg = DIGIT_COLORS[ch] || "#6366f1";
      span.style.background = bg;
      span.style.boxShadow = `0 4px 14px ${bg}66`;
      span.style.animationDelay = `${i * 60}ms`;
      seqDigitsEl.appendChild(span);
    });
  } else {
    seqDigitsEl.innerHTML = `<span style="color:var(--text-muted);font-size:15px;">
      No se detectaron tonos DTMF en este audio.</span>`;
  }

  // Sequence card visibility
  document.getElementById("sequence-card").hidden = false;

  // Chart
  if (data.chart) {
    chartImg.src = `data:image/png;base64,${data.chart}`;
    chartImg.parentElement.hidden = false;
  } else {
    chartImg.parentElement.hidden = true;
  }

  // Table
  tonesTbody.innerHTML = "";
  data.tones.forEach((tone, i) => {
    const bg = DIGIT_COLORS[tone.digit] || "#6366f1";
    const freqs = DTMF_FREQS[tone.digit] || "—";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="color:var(--text-muted);font-family:var(--mono)">${i+1}</td>
      <td>
        <span class="digit-badge" style="background:${bg};box-shadow:0 2px 8px ${bg}66">
          ${tone.digit}
        </span>
      </td>
      <td style="font-family:var(--mono)">${tone.start_s}</td>
      <td style="font-family:var(--mono)">${tone.end_s}</td>
      <td style="font-family:var(--mono)">${tone.duration_ms} ms</td>
      <td><span class="freq-tag">${freqs} Hz</span></td>
    `;
    tonesTbody.appendChild(tr);
  });

  showOnly("results-section");
  resultsSec.classList.add("fade-in");
  resultsSec.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ══════════════════════════════════════════════
//  Analyze
// ══════════════════════════════════════════════
async function runAnalysis() {
  if (!currentFile) return;

  // Show progress
  showOnly("progress-section");
  progressSec.classList.add("fade-in");
  setStep(0);

  // Simulate step progression while waiting for server
  const stepTiming = [200, 600, 1800, 2800, 3600, 4200];
  stepTiming.forEach((ms, i) => {
    setTimeout(() => setStep(i), ms);
  });

  const formData = new FormData();
  formData.append("audio", currentFile);

  try {
    const resp = await fetch("/analyze", {
      method: "POST",
      body: formData,
    });

    const data = await resp.json();

    if (!resp.ok || data.error) {
      throw new Error(data.error || `Error del servidor (${resp.status})`);
    }

    // All steps done
    STEPS.forEach(id => {
      const el = document.getElementById(id);
      el.classList.remove("active");
      el.classList.add("done");
    });

    setTimeout(() => renderResults(data), 400);

  } catch (err) {
    showOnly("error-section");
    errorMsg.textContent = err.message || "Error desconocido al procesar el audio.";
    errorSec.classList.add("fade-in");
  }
}

analyzeBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  runAnalysis();
});

// ══════════════════════════════════════════════
//  Copy sequence
// ══════════════════════════════════════════════
copyBtn.addEventListener("click", () => {
  if (!currentSequence) return;
  navigator.clipboard.writeText(currentSequence).then(() => {
    copyBtn.classList.add("copied");
    const orig = copyBtn.innerHTML;
    copyBtn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2">
        <polyline points="20 6 9 17 4 12"/>
      </svg>
      ¡Copiado!`;
    setTimeout(() => {
      copyBtn.classList.remove("copied");
      copyBtn.innerHTML = orig;
    }, 2000);
  });
});

// ══════════════════════════════════════════════
//  Reset buttons
// ══════════════════════════════════════════════
function resetAll() {
  showOnly(null);   // hide all sections
  ["progress-section","error-section","results-section"].forEach(id => {
    document.getElementById(id).hidden = true;
  });
  clearFile();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

retryBtn.addEventListener("click", () => {
  resetAll();
});

newBtn.addEventListener("click", () => {
  resetAll();
});

// ══════════════════════════════════════════════
//  MONITOR EN TIEMPO REAL
// ══════════════════════════════════════════════
(function initMonitor() {

  // DOM refs
  const monitorBtn    = document.getElementById("monitor-btn");
  const monitorBtnLbl = monitorBtn.querySelector(".monitor-btn-label");
  const monitorBtnIco = monitorBtn.querySelector(".monitor-btn-icon");
  const statusDot     = document.getElementById("status-dot-rt");
  const statusText    = document.getElementById("status-text-rt");
  const rtDigitBg     = document.getElementById("rt-digit-bg");
  const rtDigitVal    = document.getElementById("rt-digit-value");
  const rtEnergyFill  = document.getElementById("rt-energy-fill");
  const rtSeqDigits   = document.getElementById("rt-seq-digits");
  const rtClearBtn    = document.getElementById("rt-clear-btn");

  let socket          = null;
  let audioCtx        = null;
  let workletNode     = null;
  let micStream       = null;
  let isListening     = false;
  let lastDigit       = null;
  let lastDigitTimer  = null;
  let rtSequence      = [];

  // Colores por dígito (mismo mapa que el resto de la app)
  function digitColor(d) { return DIGIT_COLORS[d] || "#6366f1"; }

  // ── Actualizar UI con el dígito recibido ──────────────────────
  function onDigit(digit, energy) {
    // Barra de energía (log scale para mejor visual)
    const logE = energy > 0 ? Math.min(1, Math.log10(energy / 1e-7) / 5) : 0;
    rtEnergyFill.style.width = `${Math.max(0, Math.min(100, logE * 100)).toFixed(1)}%`;

    if (!digit) {
      // Sin tono activo
      if (lastDigit) {
        rtDigitBg.classList.remove("active");
        // Apagar teclado después de un breve retraso
        clearTimeout(lastDigitTimer);
        lastDigitTimer = setTimeout(() => {
          unlightAll();
          if (rtDigitVal.textContent !== "—") rtDigitVal.textContent = "—";
          lastDigit = null;
        }, 300);
      }
      return;
    }

    // Nuevo dígito detectado
    clearTimeout(lastDigitTimer);

    // Actualizar display grande
    if (digit !== lastDigit) {
      rtDigitVal.textContent = digit;
      rtDigitBg.classList.remove("active");
      // Re-trigger animation
      void rtDigitBg.offsetWidth;
      rtDigitBg.classList.add("active");
      rtDigitBg.style.borderColor = digitColor(digit);
      rtDigitBg.style.boxShadow   = `0 0 28px ${digitColor(digit)}55`;

      // Iluminar tecla del grid
      unlightAll();
      const keyId = `rtk-${digit === "*" ? "star" : digit === "#" ? "hash" : digit}`;
      const keyEl = document.getElementById(keyId);
      if (keyEl) {
        keyEl.classList.add("lit");
        keyEl.style.borderColor = digitColor(digit);
        keyEl.style.background  = `${digitColor(digit)}33`;
        keyEl.style.boxShadow   = `0 0 16px ${digitColor(digit)}66`;
      }

      // Agregar a la secuencia si no es repetición
      if (rtSequence.length === 0 || rtSequence[rtSequence.length - 1] !== digit) {
        rtSequence.push(digit);
        addSeqDigit(digit);
      }

      lastDigit = digit;
    }
  }

  function unlightAll() {
    document.querySelectorAll(".rt-key.lit").forEach(k => {
      k.classList.remove("lit");
      k.style.borderColor = "";
      k.style.background  = "";
      k.style.boxShadow   = "";
    });
  }

  function addSeqDigit(digit) {
    const span = document.createElement("span");
    span.className = "rt-seq-digit";
    span.textContent = digit;
    const bg = digitColor(digit);
    span.style.background = bg;
    span.style.boxShadow  = `0 4px 12px ${bg}66`;
    rtSeqDigits.appendChild(span);
    rtSeqDigits.scrollTop = rtSeqDigits.scrollHeight;
  }

  function setStatus(state, msg) {
    statusDot.className  = `status-dot-rt ${state}`;
    statusText.textContent = msg;
  }

  // ── Iniciar captura ──────────────────────────────────────────
  async function startMonitor() {
    try {
      setStatus("", "Solicitando acceso al micrófono…");

      micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });

      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const nativeSR = audioCtx.sampleRate;

      // Registrar el AudioWorklet
      await audioCtx.audioWorklet.addModule("/static/dtmf_processor.js");

      const micSource = audioCtx.createMediaStreamSource(micStream);
      workletNode = new AudioWorkletNode(audioCtx, "dtmf-processor", {
        processorOptions: { chunkSize: Math.floor(nativeSR * 0.04) }  // 40ms chunks
      });

      // Recibir chunks del worklet → enviar por Socket.IO
      workletNode.port.onmessage = (ev) => {
        if (!socket || !socket.connected) return;
        socket.emit("audio_chunk", { pcm: ev.data.pcm, sr: nativeSR });
      };

      micSource.connect(workletNode);
      workletNode.connect(audioCtx.destination);  // necesario para que el worklet corra

      // Conectar Socket.IO
      socket = io("http://localhost:5050", { transports: ["websocket"] });

      socket.on("connect", () => {
        socket.emit("rt_config", { sampleRate: nativeSR });
        isListening = true;
        setStatus("listening", `Escuchando — ${nativeSR} Hz → 8 kHz (Goertzel activo)`);
        monitorBtn.classList.add("active");
        monitorBtnLbl.textContent = "Detener monitor";
        monitorBtnIco.textContent = "■";
      });

      socket.on("disconnect", () => {
        setStatus("", "Desconectado del servidor");
      });

      socket.on("rt_digit", (data) => {
        onDigit(data.digit, data.energy);
      });

      socket.on("connect_error", (err) => {
        setStatus("error", `Error de conexión: ${err.message}`);
      });

    } catch (err) {
      setStatus("error", `Error: ${err.message || err}`);
      stopMonitor();
    }
  }

  // ── Detener captura ──────────────────────────────────────────
  function stopMonitor() {
    isListening = false;

    if (workletNode) { workletNode.disconnect(); workletNode = null; }
    if (audioCtx)    { audioCtx.close(); audioCtx = null; }
    if (micStream)   { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
    if (socket)      { socket.disconnect(); socket = null; }

    unlightAll();
    rtDigitBg.classList.remove("active");
    rtDigitBg.style.borderColor = "";
    rtDigitBg.style.boxShadow   = "";
    rtDigitVal.textContent = "—";
    rtEnergyFill.style.width = "0%";
    lastDigit = null;

    monitorBtn.classList.remove("active");
    monitorBtnLbl.textContent = "Iniciar monitor";
    monitorBtnIco.textContent = "▶";
    setStatus("", "Inactivo — presiona \"Iniciar monitor\" para comenzar");
  }

  // ── Toggle ───────────────────────────────────────────────────
  monitorBtn.addEventListener("click", () => {
    if (isListening) {
      stopMonitor();
    } else {
      startMonitor();
    }
  });

  // Limpiar secuencia
  rtClearBtn.addEventListener("click", () => {
    rtSequence = [];
    rtSeqDigits.innerHTML = "";
  });

})();

// ══════════════════════════════════════════════
//  SISTEMA DE TABS
// ══════════════════════════════════════════════
(function initTabs() {
  // Secciones controladas por tabs
  // analyzer → hero + upload + progress + error + results
  // monitor  → monitor-section  (id="monitor")
  // ivr      → ivr-section
  const TAB_SECTIONS = {
    analyzer: ["hero", "upload-section", "progress-section", "error-section", "results-section"],
    monitor:  ["monitor"],
    ivr:      ["ivr-section"],
  };

  const tabs = document.querySelectorAll(".header-tab");

  function switchTab(tabName) {
    // Ocultar todas las secciones de todos los tabs
    Object.values(TAB_SECTIONS).flat().forEach(id => {
      const el = document.getElementById(id);
      if (el) el.hidden = true;
    });

    // Mostrar secciones del tab activo
    (TAB_SECTIONS[tabName] || []).forEach(id => {
      const el = document.getElementById(id);
      if (el) el.hidden = false;
    });

    // Actualizar clases
    tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === tabName));

    // Cuando se activa el IVR, recargar dispositivos
    if (tabName === "ivr") {
      ivrLoadDevices();
    }
  }

  tabs.forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // Arrancar en tab "analyzer"
  switchTab("analyzer");
})();

// ══════════════════════════════════════════════
//  IVR AUTOMATOR MODULE
// ══════════════════════════════════════════════
(function initIVR() {
  "use strict";

  // ── Estado local ──────────────────────────────────────────────
  let ivrNumbers      = [];       // lista cargada del Excel
  let ivrAudioPaths   = {};       // { initial, middle, bye } → rutas en servidor
  let ivrQueueRows    = {};       // número → <tr> en la tabla
  let ivrSocket       = null;     // socket compartido (si el monitor ya lo abrió)
  let ivrRunning      = false;

  // ── DOM refs ─────────────────────────────────────────────────
  const deviceSelect    = document.getElementById("ivr-device");
  const refreshDevBtn   = document.getElementById("ivr-refresh-devices");
  const excelInput      = document.getElementById("ivr-excel-input");
  const excelName       = document.getElementById("ivr-excel-name");
  const numbersBadge    = document.getElementById("ivr-numbers-badge");
  const numbersCount    = document.getElementById("ivr-numbers-count");

  const audioInitInput  = document.getElementById("ivr-audio-initial-input");
  const audioInitName   = document.getElementById("ivr-audio-initial-name");
  const audioMidInput   = document.getElementById("ivr-audio-middle-input");
  const audioMidName    = document.getElementById("ivr-audio-middle-name");
  const audioByeInput   = document.getElementById("ivr-audio-bye-input");
  const audioByeName    = document.getElementById("ivr-audio-bye-name");

  const delayInput      = document.getElementById("ivr-delay");
  const toneTimeoutInput= document.getElementById("ivr-tone-timeout");
  const optionsList     = document.getElementById("ivr-options-list");
  const addOptionBtn    = document.getElementById("ivr-add-option");

  const testBtn         = document.getElementById("ivr-test-btn");
  const launchBtn       = document.getElementById("ivr-launch-btn");
  const stopBtn         = document.getElementById("ivr-stop-btn");

  const progressBar     = document.getElementById("ivr-progress-bar");
  const statProcessed   = document.getElementById("ivr-stat-processed");
  const statTotal       = document.getElementById("ivr-stat-total");
  const campaignStatus  = document.getElementById("ivr-campaign-status");
  const currentCallDiv  = document.getElementById("ivr-current-call");
  const callNumberEl    = document.getElementById("ivr-call-number");
  const callBadgesEl    = document.getElementById("ivr-call-state-badges");
  const downloadBtn     = document.getElementById("ivr-download-btn");

  const queueTbody      = document.getElementById("ivr-queue-tbody");
  const logEl           = document.getElementById("ivr-log");

  const testModal       = document.getElementById("ivr-test-modal");
  const testNumberInput = document.getElementById("ivr-test-number");
  const testCancelBtn   = document.getElementById("ivr-test-cancel");
  const testConfirmBtn  = document.getElementById("ivr-test-confirm");
  const clearLogBtn     = document.getElementById("ivr-clear-log");

  // ── Helpers ───────────────────────────────────────────────────

  function ivrLog(msg, level = "info") {
    const now = new Date();
    const ts  = `${String(now.getHours()).padStart(2,"0")}:${String(now.getMinutes()).padStart(2,"0")}:${String(now.getSeconds()).padStart(2,"0")}`;
    const div = document.createElement("div");
    div.className = `ivr-log-entry lvl-${level}`;
    div.innerHTML = `<span class="ivr-log-time">${ts}</span><span class="ivr-log-msg">${msg}</span>`;
    logEl.appendChild(div);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function pillClass(status) {
    const map = {
      PENDING: "pill-pending", CALLING: "pill-calling",
      ANSWERED_TONE: "pill-tone", ANSWERED_NO_TONE: "pill-answered",
      NO_ANSWER: "pill-no-answer", DISCONNECTED: "pill-no-answer",
      ADB_ERROR: "pill-error", ERROR: "pill-error", STOPPED: "pill-no-answer",
    };
    return map[status] || "pill-pending";
  }

  function pillLabel(status) {
    const map = {
      PENDING: "Pendiente", CALLING: "Marcando…",
      ANSWERED_TONE: "Tono detectado", ANSWERED_NO_TONE: "Sin tono",
      NO_ANSWER: "No contestó", DISCONNECTED: "Cortó",
      ADB_ERROR: "Error ADB", ERROR: "Error", STOPPED: "Detenido",
    };
    return map[status] || status;
  }

  function buildIVROptions() {
    const opts = {};
    optionsList.querySelectorAll(".ivr-option-row").forEach(row => {
      const digit = row.querySelector(".ivr-option-digit").value.trim();
      const desc  = row.querySelector(".ivr-option-desc").value.trim();
      if (digit && desc) opts[digit] = desc;
    });
    return opts;
  }

  function buildConfig(numbers) {
    return {
      numbers,
      device_id:     deviceSelect.value || null,
      delay_seconds: parseInt(delayInput.value) || 5,
      tone_timeout:  parseInt(toneTimeoutInput.value) || 10,
      audio_initial: ivrAudioPaths.initial || null,
      audio_middle:  ivrAudioPaths.middle  || null,
      audio_bye:     ivrAudioPaths.bye     || null,
      ivr_options:   buildIVROptions(),
    };
  }

  function validateConfig() {
    if (!deviceSelect.value) {
      ivrLog("⚠️ Selecciona un dispositivo ADB primero", "warn"); return false;
    }
    if (Object.keys(buildIVROptions()).length === 0) {
      ivrLog("⚠️ Configura al menos una opción IVR (dígito → descripción)", "warn"); return false;
    }
    return true;
  }

  // ── Dispositivos ADB ─────────────────────────────────────────

  async function ivrLoadDevices() {
    deviceSelect.innerHTML = "<option value=''>Cargando…</option>";
    try {
      const r = await fetch("/ivr/devices");
      const d = await r.json();
      deviceSelect.innerHTML = "<option value=''>— Seleccionar dispositivo —</option>";
      if (d.devices && d.devices.length) {
        d.devices.forEach(dev => {
          const opt = document.createElement("option");
          opt.value = dev;
          opt.textContent = dev;
          deviceSelect.appendChild(opt);
        });
        ivrLog(`✅ ${d.devices.length} dispositivo(s) detectado(s)`, "success");
      } else {
        ivrLog("⚠️ Sin dispositivos ADB conectados", "warn");
      }
    } catch (e) {
      deviceSelect.innerHTML = "<option value=''>Error ADB</option>";
      ivrLog(`❌ Error conectando ADB: ${e.message}`, "error");
    }
  }

  refreshDevBtn.addEventListener("click", ivrLoadDevices);

  // ── Carga de Excel ───────────────────────────────────────────

  excelInput.addEventListener("change", async () => {
    const file = excelInput.files[0];
    if (!file) return;
    excelName.textContent = file.name;
    ivrLog(`📂 Cargando Excel: ${file.name}…`);

    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/ivr/upload_numbers", { method: "POST", body: fd });
      const d = await r.json();
      if (d.ok) {
        ivrNumbers = d.numbers;
        numbersCount.textContent = `${d.count} números cargados`;
        numbersBadge.hidden = false;
        ivrLog(`✅ ${d.count} números listos para marcar`, "success");
        updateLaunchBtn();
        buildQueueTable();
      } else {
        ivrLog(`❌ ${d.error}`, "error");
      }
    } catch (e) {
      ivrLog(`❌ Error subiendo Excel: ${e.message}`, "error");
    }
  });

  // ── Carga de audios ──────────────────────────────────────────

  async function uploadAudio(file, type, nameEl) {
    nameEl.textContent = "Subiendo…";
    const fd = new FormData();
    fd.append("file", file);
    fd.append("type", type);
    try {
      const r = await fetch("/ivr/upload_audio", { method: "POST", body: fd });
      const d = await r.json();
      if (d.ok) {
        ivrAudioPaths[type] = d.path;
        nameEl.textContent = file.name;
        ivrLog(`🎵 Audio ${type} cargado: ${file.name}`, "success");
      } else {
        nameEl.textContent = "Error";
        ivrLog(`❌ Error: ${d.error}`, "error");
      }
    } catch (e) {
      nameEl.textContent = "Error";
      ivrLog(`❌ ${e.message}`, "error");
    }
  }

  audioInitInput.addEventListener("change", () => {
    if (audioInitInput.files[0]) uploadAudio(audioInitInput.files[0], "initial", audioInitName);
  });
  audioMidInput.addEventListener("change", () => {
    if (audioMidInput.files[0]) uploadAudio(audioMidInput.files[0], "middle", audioMidName);
  });
  audioByeInput.addEventListener("change", () => {
    if (audioByeInput.files[0]) uploadAudio(audioByeInput.files[0], "bye", audioByeName);
  });

  // ── Opciones IVR dinámicas ───────────────────────────────────

  function addOptionRow(digit = "", desc = "") {
    const row = document.createElement("div");
    row.className = "ivr-option-row";
    row.innerHTML = `
      <input class="ivr-input ivr-option-digit" type="text" maxlength="1"
             placeholder="1" value="${digit}" title="Dígito DTMF" />
      <input class="ivr-input ivr-option-desc" type="text"
             placeholder="Descripción (ej: Interesado)" value="${desc}" />
      <button class="ivr-option-remove" title="Eliminar">✕</button>
    `;
    row.querySelector(".ivr-option-remove").addEventListener("click", () => row.remove());
    optionsList.appendChild(row);
  }

  addOptionBtn.addEventListener("click", () => addOptionRow());
  // Agregar una opción por defecto al cargar
  addOptionRow("1", "Interesado");

  // ── Cola visual ──────────────────────────────────────────────

  function buildQueueTable() {
    queueTbody.innerHTML = "";
    ivrQueueRows = {};
    ivrNumbers.forEach((num, i) => {
      const tr = document.createElement("tr");
      tr.id = `ivr-row-${num}`;
      tr.innerHTML = `
        <td style="color:var(--text-dim);font-family:var(--mono)">${i + 1}</td>
        <td style="font-family:var(--mono);font-weight:600">${num}</td>
        <td><span class="ivr-status-pill pill-pending" id="ivr-pill-${num}">Pendiente</span></td>
        <td><span class="ivr-digit-result" id="ivr-digit-${num}">—</span></td>
      `;
      queueTbody.appendChild(tr);
      ivrQueueRows[num] = tr;
    });
  }

  function updateQueueRow(number, status, digit) {
    const pill = document.getElementById(`ivr-pill-${number}`);
    const digitEl = document.getElementById(`ivr-digit-${number}`);
    const tr = document.getElementById(`ivr-row-${number}`);
    if (pill) {
      pill.className = `ivr-status-pill ${pillClass(status)}`;
      pill.textContent = pillLabel(status);
    }
    if (digitEl && digit) {
      const color = DIGIT_COLORS[digit] || "#6366f1";
      digitEl.innerHTML = `<span style="background:${color};color:#fff;padding:2px 8px;border-radius:6px;font-weight:700;font-family:var(--mono)">${digit}</span>`;
    }
    if (tr) {
      tr.className = status === "CALLING" ? "row-calling"
                   : ["ADB_ERROR","ERROR"].includes(status) ? "row-error"
                   : status !== "PENDING" ? "row-done" : "";
    }
  }

  // ── Launch / Stop ────────────────────────────────────────────

  function updateLaunchBtn() {
    launchBtn.disabled = ivrNumbers.length === 0 || ivrRunning;
  }

  async function startCampaign(numbers, isTest = false) {
    if (!validateConfig()) return;
    const config = { ...buildConfig(numbers), is_test: isTest };

    ivrRunning = true;
    updateLaunchBtn();
    stopBtn.disabled = false;
    campaignStatus.textContent = isTest ? "Prueba activa" : "En curso";
    campaignStatus.style.color = "var(--green)";
    currentCallDiv.hidden = false;
    downloadBtn.style.display = "none";

    connectIvrSocket();

    try {
      const r = await fetch("/ivr/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const d = await r.json();
      if (!d.ok) {
        ivrLog(`❌ ${d.error}`, "error");
        onCampaignEnd();
      } else {
        statTotal.textContent = d.total;
        ivrLog(`🚀 ${d.msg}`, "success");
      }
    } catch (e) {
      ivrLog(`❌ Error de red: ${e.message}`, "error");
      onCampaignEnd();
    }
  }

  stopBtn.addEventListener("click", async () => {
    try {
      await fetch("/ivr/stop", { method: "POST" });
      ivrLog("⏹ Campaña detenida por el usuario", "warn");
    } catch (e) {
      ivrLog(`❌ ${e.message}`, "error");
    }
  });

  launchBtn.addEventListener("click", () => {
    if (!validateConfig()) return;
    if (ivrNumbers.length === 0) {
      ivrLog("⚠️ Carga un Excel con números primero", "warn"); return;
    }
    statTotal.textContent = ivrNumbers.length;
    statProcessed.textContent = "0";
    progressBar.style.width = "0%";
    buildQueueTable();
    startCampaign(ivrNumbers, false);
  });

  // ── Prueba ───────────────────────────────────────────────────

  testBtn.addEventListener("click", () => {
    testNumberInput.value = "";
    testModal.hidden = false;
    setTimeout(() => testNumberInput.focus(), 50);
  });
  testCancelBtn.addEventListener("click", () => { testModal.hidden = true; });
  testConfirmBtn.addEventListener("click", () => {
    const num = testNumberInput.value.trim().replace(/\s|-/g, "");
    if (!num) { ivrLog("⚠️ Ingresa un número válido", "warn"); return; }
    testModal.hidden = true;
    ivrNumbers = [num]; // temporal para la prueba
    buildQueueTable();
    statTotal.textContent = 1;
    statProcessed.textContent = "0";
    progressBar.style.width = "0%";
    startCampaign([num], true);
  });
  testModal.addEventListener("click", e => { if (e.target === testModal) testModal.hidden = true; });

  // ── Socket.IO IVR ────────────────────────────────────────────

  function connectIvrSocket() {
    if (ivrSocket && ivrSocket.connected) return;
    ivrSocket = io("http://localhost:5050", { transports: ["websocket"] });

    ivrSocket.on("ivr_log", data => {
      ivrLog(data.msg, data.level || "info");
    });

    ivrSocket.on("ivr_status", data => {
      // Actualizar badge de estado de la llamada actual
      const stateLabel = data.state;
      const cls = stateLabel.toLowerCase();
      callBadgesEl.innerHTML = `
        <span class="ivr-state-badge ${cls}">
          ${stateLabel === "ACTIVE" ? "✅" : stateLabel === "DISCONNECTED" ? "❌" : "🔄"}
          ${stateLabel}
        </span>`;
    });

    ivrSocket.on("ivr_call_update", data => {
      const { number, status, digit, processed, total } = data;
      if (number) callNumberEl.textContent = number;
      if (processed !== undefined) {
        statProcessed.textContent = processed;
        const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
        progressBar.style.width = `${pct}%`;
      }
      if (status === "CALLING") {
        callBadgesEl.innerHTML = `<span class="ivr-state-badge calling">📞 MARCANDO</span>`;
      }
      updateQueueRow(number, status, digit);
    });

    ivrSocket.on("ivr_digit", data => {
      const { number, digit, option } = data;
      ivrLog(`🎯 ${number} → opción ${digit}: ${option}`, "success");
      updateQueueRow(number, "ANSWERED_TONE", digit);
    });

    ivrSocket.on("ivr_campaign_done", data => {
      onCampaignEnd(data);
    });
  }

  function onCampaignEnd(data) {
    ivrRunning = false;
    updateLaunchBtn();
    stopBtn.disabled = true;
    campaignStatus.textContent = "Completada";
    campaignStatus.style.color = "var(--cyan)";
    currentCallDiv.hidden = true;
    downloadBtn.style.display = "";
    if (data) {
      ivrLog(`✅ Campaña terminada — ${data.processed}/${data.total} procesados`, "success");
    }
    if (ivrSocket) { ivrSocket.disconnect(); ivrSocket = null; }
  }

  clearLogBtn.addEventListener("click", () => { logEl.innerHTML = ""; });

  // Log inicial
  ivrLog("IVR Automator listo. Configura y lanza tu campaña.", "info");

})();
