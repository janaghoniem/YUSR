// wakeWord.js (fully disabled to avoid conflict with Python sidecar)
/* eslint-env node */
// This module is DISABLED because the AURA engine now runs exclusively
// via the Python sidecar (aura_engine.py). Keeping this file only to
// prevent import errors in any legacy code. All functions are stubs.

export function initAura(mainWindow) {
  console.warn("[AURA] wakeWord.js is disabled. Use the Python sidecar instead.");
  return { ok: false, provider: "none", error: "wakeWord.js disabled" };
}

export function transcribeOnce(lang = "en", timeoutMs = 10000) {
  console.warn("[AURA] transcribeOnce() called but wakeWord.js is disabled.");
  return Promise.reject(new Error("wakeWord.js is disabled"));
}

export function shutdownAura() {
  console.warn("[AURA] shutdownAura() called but wakeWord.js is disabled.");
}