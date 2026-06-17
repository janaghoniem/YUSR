import { contextBridge, ipcRenderer } from 'electron';

// ── Early-event queue ────────────────────────────────────────────────────────
// aura-wake-word can arrive before React has mounted and called onAuraWakeWord().
// We register the IPC listener immediately at module load (before contextBridge)
// and replay any queued events the moment the renderer registers a callback.
let _pendingWakeEvents = [];
let _wakeWordCallback  = null;

ipcRenderer.on('aura-wake-word', (_event, payload) => {
  if (_wakeWordCallback) {
    _wakeWordCallback(payload);
  } else {
    // Keep only the most recent pending event to avoid double-firing.
    _pendingWakeEvents = [payload];
    console.log("[PRELOAD] aura-wake-word queued (renderer not ready):", payload);
  }
});

contextBridge.exposeInMainWorld('electronAPI', {
  // ── Window controls ──────────────────────────────────────────────────────
  closeWindow:      () => ipcRenderer.invoke('window:close'),
  minimizeWindow:   () => ipcRenderer.invoke('window:minimize'),
  maximizeWindow:   () => ipcRenderer.invoke('window:maximize'),
  openExternalUrl:  (url) => ipcRenderer.invoke('shell:openExternal', url),

  // ── Widget mode ──────────────────────────────────────────────────────────
  enterWidgetMode: () => ipcRenderer.invoke('widget:enter'),
  exitWidgetMode:  () => ipcRenderer.invoke('widget:exit'),

  // ── Aura sidecar control ─────────────────────────────────────────────────
  initAura:             (options) => ipcRenderer.invoke('aura:init', options),
  transcribeAudio:      (payload) => ipcRenderer.invoke('stt:transcribe', payload),
  disarmAura:           () => ipcRenderer.invoke('aura:disarm'),
  notifyRecordingStart: () => ipcRenderer.invoke('aura:recordingStart'),
  notifyRecordingEnd:   () => ipcRenderer.invoke('aura:recordingEnd'),

  // ── IPC → renderer event subscriptions ──────────────────────────────────

  // Wake-word: uses the early-event queue so events arriving before React
  // mounts are not silently dropped.
  onAuraWakeWord: (callback) => {
    _wakeWordCallback = callback;
    if (_pendingWakeEvents.length > 0) {
      console.log("[PRELOAD] Replaying", _pendingWakeEvents.length, "queued wake event(s)");
      _pendingWakeEvents.forEach(payload => callback?.(payload));
      _pendingWakeEvents = [];
    }
    return () => {
      if (_wakeWordCallback === callback) _wakeWordCallback = null;
    };
  },

  onAppWake: (callback) => {
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on('app-wake', handler);
    return () => ipcRenderer.removeListener('app-wake', handler);
  },

  onAuraLog: (callback) => {
    const handler = (_event, payload) => callback?.(payload);
    ipcRenderer.on('aura-log', handler);
    return () => ipcRenderer.removeListener('aura-log', handler);
  },

  onAuraPartialText: (callback) => {
    const handler = (_event, payload) => callback?.(payload);
    ipcRenderer.on('aura-partial-text', handler);
    return () => ipcRenderer.removeListener('aura-partial-text', handler);
  },

  onAuraFinalCommand: (callback) => {
    const handler = (_event, payload) => callback?.(payload);
    ipcRenderer.on('aura-final-command', handler);
    return () => ipcRenderer.removeListener('aura-final-command', handler);
  },

  onAuraStatus: (callback) => {
    const handler = (_event, payload) => callback?.(payload);
    ipcRenderer.on('aura-status', handler);
    return () => ipcRenderer.removeListener('aura-status', handler);
  },

  openExternal: (url) => ipcRenderer.invoke('open-external', url),
});