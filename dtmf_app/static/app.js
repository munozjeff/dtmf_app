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
