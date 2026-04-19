const auth = window.ErgoPilotAuth;
const API_BASE_URL = auth ? auth.getApiBaseUrl() : "http://localhost:8000";
const DB_VERSION = 3;
const CLIP_DURATION_MS = 5000;
const CLIP_COOLDOWN_MS = 15000;
const PRESTART_COUNTDOWN_SECONDS = 20;
const PRESTART_COUNTDOWN_INCREMENT_SECONDS = 20;
const ACTIVE_WORKER_ID = auth ? auth.getActiveWorkerId() : "worker-self";

const videoEl = document.querySelector(".input-video");
const canvasEl = document.querySelector(".output-canvas");
const canvasCtx = canvasEl.getContext("2d");
const privacyCanvas = document.createElement("canvas");
const privacyCtx = privacyCanvas.getContext("2d");

const startBtn = document.getElementById("start-btn");
const refreshClipsBtn = document.getElementById("refresh-clips-btn");
const clearAllDataBtn = document.getElementById("clear-all-data-btn");
const notesEl = document.getElementById("notes");
const statusPill = document.getElementById("status-pill");
const countdownOverlayEl = document.getElementById("countdown-overlay");
const countdownValueEl = document.getElementById("countdown-value");
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
let pose = null;
let isCameraRunning = false;
let isAnalysisActive = false;
let captureSessionToken = 0;
let clipInProgress = false;
let lastClipAt = 0;
let countdownIntervalId = null;
let countdownRemaining = 0;

function getJsonHeaders() {
  if (!auth) {
    return { "Content-Type": "application/json" };
  }
  return Object.assign({ "Content-Type": "application/json" }, auth.authHeaders());
}

function handleAuthFailure(statusCode) {
  if (statusCode !== 401 || !auth) {
    return false;
  }
  auth.clearSession();
  auth.redirectToSignIn("index.html");
  return true;
}

function getMaxClips() {
  const value = Number(maxClipsEl.value || 10);
  if (!Number.isFinite(value)) return 10;
  return Math.max(1, Math.min(100, Math.floor(value)));
}

function openErgoDb(version) {
  return new Promise((resolve, reject) => {
    const request =
      typeof version === "number"
        ? indexedDB.open("ergopilot-db", version)
        : indexedDB.open("ergopilot-db");
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

function isIndexedDbVersionTooLow(error) {
  if (!error) return false;
  const msg = String(error.message || "").toLowerCase();
  return error.name === "VersionError" || msg.includes("less than the existing version");
}

async function openPreferredDb() {
  try {
    return await openErgoDb(DB_VERSION);
  } catch (error) {
    if (!isIndexedDbVersionTooLow(error)) {
      throw error;
    }
    return openErgoDb();
  }
}

async function ensureStoresReady() {
  if (!idb) {
    idb = await openPreferredDb();
  }
  const hasRiskClips = idb.objectStoreNames.contains("risk_clips");
  const hasRiskEvents = idb.objectStoreNames.contains("risk_events");
  if (hasRiskClips && hasRiskEvents) {
    return;
  }
  const nextVersion = idb.version + 1;
  try {
    idb.close();
  } catch (_) {
    /* no-op */
  }
  idb = await openErgoDb(nextVersion);
  if (!idb.objectStoreNames.contains("risk_clips") || !idb.objectStoreNames.contains("risk_events")) {
    throw new Error("IndexedDB migration failed: required stores missing.");
  }
}

async function initIndexedDb() {
  idb = await openPreferredDb();
  await ensureStoresReady();
  return idb;
}

function txPromise(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("IndexedDB transaction failed"));
    tx.onabort = () => reject(tx.error || new Error("IndexedDB transaction aborted"));
  });
}

function addRiskEvent(event) {
  if (!idb || !idb.objectStoreNames.contains("risk_events")) return;
  const tx = idb.transaction("risk_events", "readwrite");
  tx.objectStore("risk_events").add({ ...event, createdAt: new Date().toISOString() });
}

async function addRiskClip(clip) {
  await ensureStoresReady();
  const tx = idb.transaction("risk_clips", "readwrite");
  tx.objectStore("risk_clips").add({
    ...clip,
    createdAt: new Date().toISOString()
  });
  await txPromise(tx);
}

async function getAllRiskClips() {
  await ensureStoresReady();
  return new Promise((resolve, reject) => {
    const tx = idb.transaction("risk_clips", "readonly");
    const req = tx.objectStore("risk_clips").getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

async function deleteRiskClipById(id) {
  await ensureStoresReady();
  const tx = idb.transaction("risk_clips", "readwrite");
  tx.objectStore("risk_clips").delete(id);
  await txPromise(tx);
}

async function clearAllRiskClips() {
  await ensureStoresReady();
  const tx = idb.transaction("risk_clips", "readwrite");
  tx.objectStore("risk_clips").clear();
  await txPromise(tx);
}

function clipsForActiveWorker(clips) {
  return (Array.isArray(clips) ? clips : []).filter(
    (clip) => String(clip && clip.workerId ? clip.workerId : "") === ACTIVE_WORKER_ID
  );
}

async function clearRiskClipsForActiveWorker() {
  const clips = clipsForActiveWorker(await getAllRiskClips());
  for (const clip of clips) {
    if (Number.isFinite(Number(clip.id))) {
      await deleteRiskClipById(Number(clip.id));
    }
  }
}

async function deleteBackendEventById(eventId) {
  if (!Number.isFinite(eventId)) return true;
  const res = await fetch(`${API_BASE_URL}/api/events/${eventId}`, {
    method: "DELETE",
    headers: auth ? auth.authHeaders() : {}
  });
  if (handleAuthFailure(res.status)) return false;
  if (!res.ok) {
    throw new Error(`Delete event failed: ${res.status}`);
  }
  return true;
}

async function deleteBackendSampleById(sampleId) {
  if (!Number.isFinite(sampleId)) return true;
  const res = await fetch(`${API_BASE_URL}/api/session-samples/${sampleId}`, {
    method: "DELETE",
    headers: auth ? auth.authHeaders() : {}
  });
  if (handleAuthFailure(res.status)) return false;
  if (!res.ok) {
    throw new Error(`Delete sample failed: ${res.status}`);
  }
  return true;
}

async function deleteBackendSamplesWindow(workerId, startMs, endMs) {
  if (!workerId || !Number.isFinite(startMs) || !Number.isFinite(endMs)) return true;
  const params = new URLSearchParams({
    worker_id: String(workerId),
    start_ms: String(startMs),
    end_ms: String(endMs)
  });
  const res = await fetch(`${API_BASE_URL}/api/session-samples-window?${params.toString()}`, {
    method: "DELETE",
    headers: auth ? auth.authHeaders() : {}
  });
  if (handleAuthFailure(res.status)) return false;
  if (!res.ok) {
    throw new Error(`Delete sample window failed: ${res.status}`);
  }
  return true;
}

async function clearBackendEvents() {
  const res = await fetch(`${API_BASE_URL}/api/events`, {
    method: "DELETE",
    headers: auth ? auth.authHeaders() : {}
  });
  if (handleAuthFailure(res.status)) return false;
  if (!res.ok) {
    throw new Error(`Clear events failed: ${res.status}`);
  }
  return true;
}

async function enforceClipRetention() {
  const maxClips = getMaxClips();
  const clips = clipsForActiveWorker(await getAllRiskClips());
  clips.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
  if (clips.length <= maxClips) return;
  const toDelete = clips.slice(0, clips.length - maxClips);
  for (const clip of toDelete) {
    await deleteRiskClipById(clip.id);
  }
}

async function renderClipsList() {
  const clips = clipsForActiveWorker(await getAllRiskClips());
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
            <button class="clip-delete-btn" type="button" data-clip-id="${clip.id}" data-event-id="${clip.eventId ?? ""}" data-sample-id="${clip.postureSampleId ?? ""}" data-worker-id="${clip.workerId ?? ""}" data-start-ms="${clip.clipStartMs ?? ""}" data-end-ms="${clip.clipEndMs ?? ""}">
              Delete
            </button>
          </div>
        </article>
      `;
    })
    .join("");
  clipsListEl.innerHTML = itemsHtml;
}

function setRiskUi(level) {
  statusPill.classList.remove("status-pill--hidden");
  statusPill.classList.remove("safe", "warning", "danger");
  statusPill.classList.add(level);
  statusPill.textContent = level.toUpperCase();
}

function hideCountdownOverlay() {
  if (countdownOverlayEl) {
    countdownOverlayEl.classList.add("status-pill--hidden");
    countdownOverlayEl.classList.remove("countdown-overlay--interactive");
  }
  if (countdownValueEl) {
    countdownValueEl.textContent = String(PRESTART_COUNTDOWN_SECONDS);
  }
}

function updateCountdownText() {
  notesEl.textContent =
    "Camera live. Capturing baseline in " +
    String(countdownRemaining) +
    " seconds. Tap the countdown screen to add 20s.";
}

function canExtendCountdown() {
  return isCameraRunning && !isAnalysisActive && Boolean(countdownIntervalId) && countdownRemaining > 0;
}

function extendCountdown() {
  if (!canExtendCountdown()) return;
  countdownRemaining += PRESTART_COUNTDOWN_INCREMENT_SECONDS;
  if (countdownValueEl) {
    countdownValueEl.textContent = String(countdownRemaining);
  }
  updateCountdownText();
}

function beginPrestartCountdown() {
  if (!countdownOverlayEl || !countdownValueEl) {
    isAnalysisActive = true;
    setRiskUi("safe");
    notesEl.textContent = "Analysis started. Move naturally and monitor feedback.";
    return;
  }
  const sessionToken = captureSessionToken;
  if (countdownIntervalId) {
    clearInterval(countdownIntervalId);
    countdownIntervalId = null;
  }
  countdownRemaining = PRESTART_COUNTDOWN_SECONDS;
  countdownValueEl.textContent = String(countdownRemaining);
  countdownOverlayEl.classList.remove("status-pill--hidden");
  countdownOverlayEl.classList.add("countdown-overlay--interactive");
  updateCountdownText();

  countdownIntervalId = setInterval(async () => {
    if (!isCameraRunning || sessionToken !== captureSessionToken) {
      clearInterval(countdownIntervalId);
      countdownIntervalId = null;
      return;
    }
    countdownRemaining -= 1;
    if (countdownRemaining <= 0) {
      clearInterval(countdownIntervalId);
      countdownIntervalId = null;
      countdownRemaining = 0;
      hideCountdownOverlay();
      notesEl.textContent = "Capturing baseline posture...";
      let didCalibrate = false;
      try {
        didCalibrate = await callCalibrate();
      } catch (error) {
        notesEl.textContent = `Baseline capture failed: ${error.message}`;
        return;
      }
      if (!isCameraRunning || sessionToken !== captureSessionToken) return;
      if (!didCalibrate) {
        notesEl.textContent =
          "Could not capture baseline posture. Keep your body in frame and restart camera to try again.";
        return;
      }
      isAnalysisActive = true;
      lastAnalyzeAt = 0;
      setRiskUi("safe");
      notesEl.textContent = "Baseline captured. Analysis started. Move naturally and monitor feedback.";
      return;
    }
    countdownValueEl.textContent = String(countdownRemaining);
    updateCountdownText();
  }, 1000);
}

function getPoseBoundsPx(poseLandmarks, width, height) {
  if (!poseLandmarks || !poseLandmarks.length) return null;
  let minX = width;
  let minY = height;
  let maxX = 0;
  let maxY = 0;

  for (const lm of poseLandmarks) {
    if (!Number.isFinite(lm.x) || !Number.isFinite(lm.y)) continue;
    const x = Math.max(0, Math.min(width, lm.x * width));
    const y = Math.max(0, Math.min(height, lm.y * height));
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }

  if (maxX <= minX || maxY <= minY) return null;

  const padX = (maxX - minX) * 0.35;
  const padY = (maxY - minY) * 0.25;

  return {
    x: Math.max(0, minX - padX),
    y: Math.max(0, minY - padY),
    w: Math.min(width, maxX + padX) - Math.max(0, minX - padX),
    h: Math.min(height, maxY + padY) - Math.max(0, minY - padY)
  };
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
  const body = {
    worker_id: ACTIVE_WORKER_ID,
    landmarks: landmarksPayload,
    load_kg: Number(loadKgEl.value || 0),
    frequency_lifts_per_min: Number(freqEl.value || 0),
    frame_ts: Date.now()
  };
  const res = await fetch(`${API_BASE_URL}/api/analyze`, {
    method: "POST",
    headers: getJsonHeaders(),
    body: JSON.stringify(body)
  });
  if (handleAuthFailure(res.status)) {
    throw new Error("Session expired.");
  }
  if (!res.ok) {
    throw new Error(`Analyze failed: ${res.status}`);
  }
  return res.json();
}

async function callCalibrate() {
  if (!latestLandmarks) {
    return false;
  }
  const payload = {
    worker_id: ACTIVE_WORKER_ID,
    landmarks: toPayloadLandmarks(latestLandmarks)
  };
  const res = await fetch(`${API_BASE_URL}/api/calibrate`, {
    method: "POST",
    headers: getJsonHeaders(),
    body: JSON.stringify(payload)
  });
  if (handleAuthFailure(res.status)) {
    return false;
  }
  if (!res.ok) {
    return false;
  }
  return true;
}

async function captureRiskClip(analysis) {
  const eventId = Number(analysis.risk_event_id);
  if (!Number.isFinite(eventId)) {
    notesEl.textContent = "Clip skipped: backend event link missing.";
    return;
  }
  if (!privacyCanvas.width || !privacyCanvas.height) {
    notesEl.textContent = "Clip capture skipped: anonymized stream not ready.";
    return;
  }
  if (clipInProgress) return;
  if (!window.MediaRecorder) {
    notesEl.textContent = "Clip capture unsupported in this browser. Try Chrome/Edge.";
    return;
  }
  const now = Date.now();
  if (now - lastClipAt < CLIP_COOLDOWN_MS) return;
  const clipStartMs = Date.now();
  const riskDescriptions = Array.isArray(analysis.notes)
    ? analysis.notes
        .map((note) => String(note || "").trim())
        .filter((note) => note.length > 0)
    : [];
  const primaryRiskDescription = riskDescriptions.length > 0 ? riskDescriptions[0] : "";

  clipInProgress = true;
  lastClipAt = now;

  try {
    // Privacy-first clip capture records scene context with worker body masked.
    const stream = privacyCanvas.captureStream(20);
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
      riskDescriptions: riskDescriptions,
      primaryRiskDescription: primaryRiskDescription,
      eventId: eventId,
      postureSampleId: Number.isFinite(Number(analysis.posture_sample_id))
        ? Number(analysis.posture_sample_id)
        : null,
      clipStartMs: clipStartMs,
      clipEndMs: Date.now(),
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
  privacyCanvas.width = width;
  privacyCanvas.height = height;
  canvasCtx.save();
  canvasCtx.clearRect(0, 0, width, height);
  canvasCtx.drawImage(results.image, 0, 0, width, height);
  privacyCtx.save();
  privacyCtx.clearRect(0, 0, width, height);
  privacyCtx.drawImage(results.image, 0, 0, width, height);

  if (results.poseLandmarks) {
    const bounds = getPoseBoundsPx(results.poseLandmarks, width, height);
    if (bounds) {
      // Blur and darken only the worker region; keep environment context unchanged.
      privacyCtx.save();
      privacyCtx.filter = "blur(18px)";
      privacyCtx.drawImage(
        privacyCanvas,
        bounds.x,
        bounds.y,
        bounds.w,
        bounds.h,
        bounds.x,
        bounds.y,
        bounds.w,
        bounds.h
      );
      privacyCtx.restore();
      privacyCtx.fillStyle = "rgba(2, 6, 23, 0.55)";
      privacyCtx.fillRect(bounds.x, bounds.y, bounds.w, bounds.h);
    }

    drawConnectors(canvasCtx, results.poseLandmarks, POSE_CONNECTIONS, {
      color: "#60a5fa",
      lineWidth: 3
    });
    drawLandmarks(canvasCtx, results.poseLandmarks, {
      color: "#22c55e",
      lineWidth: 1,
      radius: 3
    });
    drawConnectors(privacyCtx, results.poseLandmarks, POSE_CONNECTIONS, {
      color: "#93c5fd",
      lineWidth: 4
    });
    drawLandmarks(privacyCtx, results.poseLandmarks, {
      color: "#22c55e",
      lineWidth: 1,
      radius: 4
    });
  }
  privacyCtx.restore();
  canvasCtx.restore();
}

async function onResults(results) {
  if (!isCameraRunning) return;
  drawPose(results);
  if (!results.poseLandmarks) return;

  latestLandmarks = results.poseLandmarks;
  if (!isAnalysisActive) return;
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
      const linkedEventId = Number(analysis.risk_event_id);
      if (Number.isFinite(linkedEventId)) {
        captureRiskClip(analysis);
      }
    }
  } catch (error) {
    notesEl.textContent = `Backend unavailable: ${error.message}`;
  }
}

async function startCameraAndPose() {
  const localPose = new Pose({
    locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${file}`
  });

  localPose.setOptions({
    modelComplexity: 1,
    smoothLandmarks: true,
    minDetectionConfidence: 0.5,
    minTrackingConfidence: 0.5
  });
  localPose.onResults(onResults);
  const sessionToken = ++captureSessionToken;

  camera = new Camera(videoEl, {
    onFrame: async () => {
      if (!isCameraRunning || sessionToken !== captureSessionToken) return;
      await localPose.send({ image: videoEl });
    },
    width: 1280,
    height: 720
  });
  pose = localPose;
  await camera.start();
  isCameraRunning = true;
  isAnalysisActive = false;
  startBtn.textContent = "Stop Camera";
  statusPill.classList.add("status-pill--hidden");
  beginPrestartCountdown();
}

async function stopCameraAndPose() {
  isCameraRunning = false;
  isAnalysisActive = false;
  captureSessionToken += 1;
  if (countdownIntervalId) {
    clearInterval(countdownIntervalId);
    countdownIntervalId = null;
  }
  countdownRemaining = 0;
  hideCountdownOverlay();
  if (camera && typeof camera.stop === "function") {
    await camera.stop();
  }
  if (pose && typeof pose.close === "function") {
    await pose.close();
  }
  if (videoEl && typeof videoEl.pause === "function") {
    videoEl.pause();
    videoEl.removeAttribute("src");
    videoEl.srcObject = null;
  }
  const width = canvasEl.width || videoEl.videoWidth || 1280;
  const height = canvasEl.height || videoEl.videoHeight || 720;
  canvasCtx.clearRect(0, 0, width, height);
  privacyCtx.clearRect(0, 0, width, height);
  canvasEl.width = 0;
  canvasEl.height = 0;
  privacyCanvas.width = 0;
  privacyCanvas.height = 0;
  camera = null;
  pose = null;
  latestLandmarks = null;
  lastAnalyzeAt = 0;
  startBtn.textContent = "Start Camera";
  statusPill.classList.add("status-pill--hidden");
  notesEl.textContent = "Camera stopped.";
}

startBtn.addEventListener("click", () => {
  if (isCameraRunning) {
    stopCameraAndPose().catch((error) => {
      notesEl.textContent = `Camera stop failed: ${error.message}`;
    });
    return;
  }
  startCameraAndPose().catch((error) => {
    notesEl.textContent = `Camera start failed: ${error.message}`;
  });
});

if (countdownOverlayEl) {
  countdownOverlayEl.addEventListener("click", () => {
    extendCountdown();
  });
}

refreshClipsBtn.addEventListener("click", () => {
  renderClipsList().catch((error) => {
    notesEl.textContent = `Clip refresh failed: ${error.message}`;
  });
});

clipsListEl.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("clip-delete-btn")) return;

  const clipId = Number(target.getAttribute("data-clip-id"));
  const eventIdRaw = target.getAttribute("data-event-id");
  const eventId = eventIdRaw && eventIdRaw !== "" ? Number(eventIdRaw) : NaN;
  const sampleIdRaw = target.getAttribute("data-sample-id");
  const sampleId = sampleIdRaw && sampleIdRaw !== "" ? Number(sampleIdRaw) : NaN;
  const workerId = target.getAttribute("data-worker-id") || "";
  const startMsRaw = target.getAttribute("data-start-ms");
  const endMsRaw = target.getAttribute("data-end-ms");
  const startMs = startMsRaw && startMsRaw !== "" ? Number(startMsRaw) : NaN;
  const endMs = endMsRaw && endMsRaw !== "" ? Number(endMsRaw) : NaN;
  const confirmed = window.confirm("Delete this clip and remove its linked backend data from averages?");
  if (!confirmed) return;
  if (workerId && workerId !== ACTIVE_WORKER_ID) {
    notesEl.textContent = "Blocked: clip account mismatch for this browser lock.";
    return;
  }

  Promise.resolve()
    .then(() => deleteRiskClipById(clipId))
    .then(() => deleteBackendEventById(eventId))
    .then(() => deleteBackendSamplesWindow(workerId, startMs, endMs))
    .then(() => deleteBackendSampleById(sampleId))
    .then(() => renderClipsList())
    .then(() => {
      notesEl.textContent = "Clip deleted. Linked backend event removed when available.";
    })
    .catch((error) => {
      notesEl.textContent = `Delete failed: ${error.message}`;
    });
});

if (clearAllDataBtn) {
  clearAllDataBtn.addEventListener("click", () => {
    const confirmed = window.confirm(
      "Clear all local clips and all backend risk events? This cannot be undone."
    );
    if (!confirmed) return;

    Promise.resolve()
      .then(() => getAllRiskClips())
      .then((clips) => clipsForActiveWorker(clips))
      .then(async (clips) => {
        for (const clip of clips) {
          await deleteBackendEventById(Number(clip.eventId));
          await deleteBackendSamplesWindow(
            ACTIVE_WORKER_ID,
            Number(clip.clipStartMs),
            Number(clip.clipEndMs)
          );
          await deleteBackendSampleById(Number(clip.postureSampleId));
        }
      })
      .then(() => clearRiskClipsForActiveWorker())
      .then(() => renderClipsList())
      .then(() => {
        rulaEl.textContent = "-";
        rebaEl.textContent = "-";
        rwlEl.textContent = "-";
        nioshEl.textContent = "-";
        notesEl.textContent = "Cleared local clips and backend events.";
      })
      .catch((error) => {
        notesEl.textContent = `Clear failed: ${error.message}`;
      });
  });
}

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

hideCountdownOverlay();
