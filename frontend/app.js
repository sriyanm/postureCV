const API_BASE_URL = "http://localhost:8000";
const DB_VERSION = 2;
const CLIP_DURATION_MS = 5000;
const CLIP_COOLDOWN_MS = 15000;

const videoEl = document.querySelector(".input-video");
const canvasEl = document.querySelector(".output-canvas");
const canvasCtx = canvasEl.getContext("2d");

const startBtn = document.getElementById("start-btn");
const calibrateBtn = document.getElementById("calibrate-btn");
const refreshClipsBtn = document.getElementById("refresh-clips-btn");
const notesEl = document.getElementById("notes");
const statusPill = document.getElementById("status-pill");
const workerIdEl = document.getElementById("worker-id");
const loadKgEl = document.getElementById("load-kg");
const freqEl = document.getElementById("freq");
const maxClipsEl = document.getElementById("max-clips");
const clipsListEl = document.getElementById("clips-list");

const rulaEl = document.getElementById("rula");
const rebaEl = document.getElementById("reba");
const rwlEl = document.getElementById("rwl");
const nioshEl = document.getElementById("niosh");

let latestLandmarks = null;
let lastAnalyzeAt = 0;
let idb = null;
let camera = null;
let clipInProgress = false;
let lastClipAt = 0;

function getMaxClips() {
  const value = Number(maxClipsEl.value || 10);
  if (!Number.isFinite(value)) return 10;
  return Math.max(1, Math.min(100, Math.floor(value)));
}

function initIndexedDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("ergopilot-db", DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("risk_events")) {
        db.createObjectStore("risk_events", { keyPath: "id", autoIncrement: true });
      }
      if (!db.objectStoreNames.contains("risk_clips")) {
        const clipsStore = db.createObjectStore("risk_clips", { keyPath: "id", autoIncrement: true });
        clipsStore.createIndex("createdAt", "createdAt");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function txPromise(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
  });
}

function addRiskEvent(event) {
  if (!idb) return;
  const tx = idb.transaction("risk_events", "readwrite");
  tx.objectStore("risk_events").add({ ...event, createdAt: new Date().toISOString() });
}

async function addRiskClip(clip) {
  if (!idb) return;
  const tx = idb.transaction("risk_clips", "readwrite");
  tx.objectStore("risk_clips").add({
    ...clip,
    createdAt: new Date().toISOString()
  });
  await txPromise(tx);
}

async function getAllRiskClips() {
  if (!idb) return [];
  return new Promise((resolve, reject) => {
    const tx = idb.transaction("risk_clips", "readonly");
    const req = tx.objectStore("risk_clips").getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function deleteRiskClipById(id) {
  if (!idb) return;
  const tx = idb.transaction("risk_clips", "readwrite");
  tx.objectStore("risk_clips").delete(id);
  await txPromise(tx);
}

async function enforceClipRetention() {
  const maxClips = getMaxClips();
  const clips = await getAllRiskClips();
  clips.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
  if (clips.length <= maxClips) return;
  const toDelete = clips.slice(0, clips.length - maxClips);
  for (const clip of toDelete) {
    await deleteRiskClipById(clip.id);
  }
}

async function renderClipsList() {
  const clips = await getAllRiskClips();
  clips.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  if (!clips.length) {
    clipsListEl.innerHTML = '<p class="clip-empty">No clips recorded yet.</p>';
    return;
  }
  const itemsHtml = clips
    .map((clip) => {
      const url = URL.createObjectURL(clip.videoBlob);
      const date = new Date(clip.createdAt).toLocaleString();
      return `
        <article class="clip-item">
          <div class="clip-meta">${date} | ${clip.riskLevel.toUpperCase()} | RULA ${clip.rulaScore} | REBA ${clip.rebaScore}</div>
          <video controls preload="metadata" src="${url}"></video>
          <div class="clip-actions">
            <a href="${url}" download="risk-clip-${clip.id}.webm">Download</a>
          </div>
        </article>
      `;
    })
    .join("");
  clipsListEl.innerHTML = itemsHtml;
}

function setRiskUi(level) {
  statusPill.classList.remove("safe", "warning", "danger");
  statusPill.classList.add(level);
  statusPill.textContent = level.toUpperCase();
}

function toPayloadLandmarks(mpLandmarks) {
  return mpLandmarks.map((lm, idx) => ({
    id: idx,
    x: lm.x,
    y: lm.y,
    z: lm.z ?? 0,
    visibility: lm.visibility
  }));
}

async function callAnalyze(landmarksPayload) {
  const workerId = workerIdEl.value.trim() || "worker-001";
  const body = {
    worker_id: workerId,
    landmarks: landmarksPayload,
    load_kg: Number(loadKgEl.value || 0),
    frequency_lifts_per_min: Number(freqEl.value || 0),
    frame_ts: Date.now()
  };
  const res = await fetch(`${API_BASE_URL}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!res.ok) {
    throw new Error(`Analyze failed: ${res.status}`);
  }
  return res.json();
}

async function callCalibrate() {
  if (!latestLandmarks) {
    notesEl.textContent = "No landmarks available yet. Start camera first.";
    return;
  }
  const payload = {
    worker_id: workerIdEl.value.trim() || "worker-001",
    landmarks: toPayloadLandmarks(latestLandmarks)
  };
  const res = await fetch(`${API_BASE_URL}/api/calibrate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok) {
    notesEl.textContent = "Calibration request failed.";
    return;
  }
  notesEl.textContent = "Calibration saved. Hold this as neutral posture baseline.";
}

async function captureRiskClip(analysis) {
  if (!videoEl.srcObject) {
    notesEl.textContent = "Clip capture skipped: camera stream not ready.";
    return;
  }
  if (clipInProgress) return;
  if (!window.MediaRecorder) {
    notesEl.textContent = "Clip capture unsupported in this browser. Try Chrome/Edge.";
    return;
  }
  const now = Date.now();
  if (now - lastClipAt < CLIP_COOLDOWN_MS) return;

  clipInProgress = true;
  lastClipAt = now;

  try {
    const stream = videoEl.srcObject;
    let recorder = null;
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported("video/webm;codecs=vp8")) {
      recorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8" });
    } else {
      recorder = new MediaRecorder(stream);
    }
    const chunks = [];
    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data);
      }
    };

    const done = new Promise((resolve) => {
      recorder.onstop = () => resolve();
    });

    recorder.start(500);
    notesEl.textContent = "Recording risk clip (5s)...";
    await new Promise((resolve) => setTimeout(resolve, CLIP_DURATION_MS));
    recorder.stop();
    await done;

    const blob = new Blob(chunks, { type: "video/webm" });
    if (blob.size === 0) return;
    await addRiskClip({
      workerId: analysis.worker_id,
      riskLevel: analysis.risk_level,
      rulaScore: analysis.rula_score,
      rebaScore: analysis.reba_score,
      nioshRatio: analysis.niosh_ratio,
      videoBlob: blob
    });
    await enforceClipRetention();
    await renderClipsList();
    notesEl.textContent = "Risk clip saved to local IndexedDB.";
  } catch (error) {
    notesEl.textContent = `Clip capture failed: ${error.message}`;
  } finally {
    clipInProgress = false;
  }
}

function drawPose(results) {
  const width = videoEl.videoWidth || 1280;
  const height = videoEl.videoHeight || 720;
  canvasEl.width = width;
  canvasEl.height = height;
  canvasCtx.save();
  canvasCtx.clearRect(0, 0, width, height);
  canvasCtx.drawImage(results.image, 0, 0, width, height);

  if (results.poseLandmarks) {
    drawConnectors(canvasCtx, results.poseLandmarks, POSE_CONNECTIONS, {
      color: "#60a5fa",
      lineWidth: 3
    });
    drawLandmarks(canvasCtx, results.poseLandmarks, {
      color: "#22c55e",
      lineWidth: 1,
      radius: 3
    });
  }
  canvasCtx.restore();
}

async function onResults(results) {
  drawPose(results);
  if (!results.poseLandmarks) return;

  latestLandmarks = results.poseLandmarks;
  const now = Date.now();
  if (now - lastAnalyzeAt < 250) return;
  lastAnalyzeAt = now;

  try {
    const landmarksPayload = toPayloadLandmarks(results.poseLandmarks);
    const analysis = await callAnalyze(landmarksPayload);
    setRiskUi(analysis.risk_level);
    rulaEl.textContent = analysis.rula_score;
    rebaEl.textContent = analysis.reba_score;
    rwlEl.textContent = analysis.rwl_kg.toFixed(2);
    nioshEl.textContent = analysis.niosh_ratio.toFixed(2);
    notesEl.textContent = analysis.notes.length
      ? analysis.notes.join(" ")
      : "Posture currently within safe thresholds.";

    if (analysis.risk_level !== "safe") {
      addRiskEvent({
        workerId: analysis.worker_id,
        riskLevel: analysis.risk_level,
        rulaScore: analysis.rula_score,
        rebaScore: analysis.reba_score,
        rwlKg: analysis.rwl_kg,
        nioshRatio: analysis.niosh_ratio,
        landmarks: landmarksPayload
      });
      captureRiskClip(analysis);
    }
  } catch (error) {
    notesEl.textContent = `Backend unavailable: ${error.message}`;
  }
}

async function startCameraAndPose() {
  const pose = new Pose({
    locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${file}`
  });

  pose.setOptions({
    modelComplexity: 1,
    smoothLandmarks: true,
    minDetectionConfidence: 0.5,
    minTrackingConfidence: 0.5
  });
  pose.onResults(onResults);

  camera = new Camera(videoEl, {
    onFrame: async () => {
      await pose.send({ image: videoEl });
    },
    width: 1280,
    height: 720
  });
  await camera.start();
  notesEl.textContent = "Camera running. Stand in frame and press calibrate.";
}

startBtn.addEventListener("click", () => {
  startCameraAndPose().catch((error) => {
    notesEl.textContent = `Camera start failed: ${error.message}`;
  });
});

calibrateBtn.addEventListener("click", () => {
  callCalibrate().catch((error) => {
    notesEl.textContent = `Calibration failed: ${error.message}`;
  });
});

refreshClipsBtn.addEventListener("click", () => {
  renderClipsList().catch((error) => {
    notesEl.textContent = `Clip refresh failed: ${error.message}`;
  });
});

maxClipsEl.addEventListener("change", () => {
  enforceClipRetention()
    .then(() => renderClipsList())
    .catch((error) => {
      notesEl.textContent = `Clip retention update failed: ${error.message}`;
    });
});

initIndexedDb()
  .then((db) => {
    idb = db;
    return renderClipsList();
  })
  .catch((error) => {
    notesEl.textContent = `IndexedDB unavailable: ${error.message}`;
  });
