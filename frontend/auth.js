/**
 * Shared session helpers: JWT access token in sessionStorage (tab-scoped).
 * Align API base with app.js via optional window.__ERGOPILOT_API_BASE__.
 */
(function (global) {
  var AUTH_TOKEN_KEY = "ergopilot_access_token";
  var LOCKED_ACCOUNT_KEY = "ergopilot_locked_account_email";
  var LOCAL_API = "http://localhost:8000";

  function inferDefaultApiBase() {
    try {
      if (!global.location || !global.location.hostname) {
        return LOCAL_API;
      }
      var host = String(global.location.hostname).toLowerCase();
      if (host === "localhost" || host === "127.0.0.1" || host === "::1") {
        return LOCAL_API;
      }
      // For hosted/tunneled demos, use same-origin API (e.g. reverse proxy + ngrok).
      return global.location.origin;
    } catch (_) {
      return LOCAL_API;
    }
  }

  function getApiBaseUrl() {
    return global.__ERGOPILOT_API_BASE__ || inferDefaultApiBase();
  }

  function getAccessToken() {
    try {
      return sessionStorage.getItem(AUTH_TOKEN_KEY);
    } catch (_) {
      return null;
    }
  }

  function setAccessToken(token) {
    try {
      if (token) {
        sessionStorage.setItem(AUTH_TOKEN_KEY, token);
      } else {
        sessionStorage.removeItem(AUTH_TOKEN_KEY);
      }
    } catch (_) {
      /* ignore quota / private mode */
    }
  }

  function clearSession() {
    setAccessToken(null);
  }

  function normalizeEmail(value) {
    if (typeof value !== "string") {
      return "";
    }
    return value.trim().toLowerCase();
  }

  function getLockedAccountEmail() {
    try {
      return normalizeEmail(localStorage.getItem(LOCKED_ACCOUNT_KEY) || "");
    } catch (_) {
      return "";
    }
  }

  function lockAccountEmail(email) {
    var normalized = normalizeEmail(email);
    if (!normalized) {
      return "";
    }
    try {
      localStorage.setItem(LOCKED_ACCOUNT_KEY, normalized);
    } catch (_) {
      /* ignore private-mode quota/storage errors */
    }
    return normalized;
  }

  function clearLockedAccount() {
    try {
      localStorage.removeItem(LOCKED_ACCOUNT_KEY);
    } catch (_) {
      /* ignore private-mode quota/storage errors */
    }
  }

  function isLockedToDifferentAccount(email) {
    var locked = getLockedAccountEmail();
    var normalized = normalizeEmail(email);
    return Boolean(locked && normalized && locked !== normalized);
  }

  function getActiveWorkerId() {
    var locked = getLockedAccountEmail();
    if (!locked) {
      return "worker-self";
    }
    var safe = locked.replace(/[^a-z0-9._-]+/g, "_");
    return "worker:" + safe;
  }

  /** Allow only same-origin relative HTML targets after login. */
  function safeNextPath(raw) {
    var allowed = { "dashboard.html": true, "index.html": true };
    if (!raw || typeof raw !== "string") {
      return "./dashboard.html";
    }
    var trimmed = raw.trim();
    try {
      var resolved = new URL(trimmed, global.location.href);
      if (resolved.origin !== global.location.origin) {
        return "./dashboard.html";
      }
      var name = resolved.pathname.split("/").pop() || "";
      return allowed[name] ? "./" + name : "./dashboard.html";
    } catch (_) {
      return "./dashboard.html";
    }
  }

  function redirectToSignIn(nextPath) {
    var q = nextPath ? "?next=" + encodeURIComponent(nextPath) : "";
    global.location.replace("./signin.html" + q);
  }

  function authHeaders() {
    var t = getAccessToken();
    if (!t) {
      return {};
    }
    return { Authorization: "Bearer " + t };
  }

  global.ErgoPilotAuth = {
    getApiBaseUrl: getApiBaseUrl,
    getAccessToken: getAccessToken,
    setAccessToken: setAccessToken,
    clearSession: clearSession,
    getLockedAccountEmail: getLockedAccountEmail,
    lockAccountEmail: lockAccountEmail,
    clearLockedAccount: clearLockedAccount,
    isLockedToDifferentAccount: isLockedToDifferentAccount,
    getActiveWorkerId: getActiveWorkerId,
    safeNextPath: safeNextPath,
    redirectToSignIn: redirectToSignIn,
    authHeaders: authHeaders
  };
})(typeof window !== "undefined" ? window : globalThis);
