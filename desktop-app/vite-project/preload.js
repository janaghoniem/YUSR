import { contextBridge, ipcRenderer } from 'electron';

// Early-event queue: capture aura-wake-word events that arrive before the
// renderer's React useEffect has had a chance to register a callback.
// The queue is drained as soon as onAuraWakeWord() is first called.
let _pendingWakeEvents = [];
let _wakeWordCallback = null;

ipcRenderer.on('aura-wake-word', (_event, payload) => {
  if (_wakeWordCallback) {
    _wakeWordCallback(payload);
  } else {
    // Renderer not ready yet — queue it (keep only the most recent one)
    _pendingWakeEvents = [payload];
    console.log("[PRELOAD] aura-wake-word queued (renderer not ready yet):", payload);
  }
});

contextBridge.exposeInMainWorld('electronAPI', {
  closeWindow: () => ipcRenderer.invoke('window:close'),
  minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
  maximizeWindow: () => ipcRenderer.invoke('window:maximize'),
  openExternalUrl: (url) => ipcRenderer.invoke('shell:openExternal', url),
  enterWidgetMode: () => ipcRenderer.invoke('widget:enter'),
  exitWidgetMode: () => ipcRenderer.invoke('widget:exit'),
  initAura: (options) => ipcRenderer.invoke('aura:init', options),
  transcribeAudio: (payload) => ipcRenderer.invoke('stt:transcribe', payload),
  disarmAura: () => ipcRenderer.invoke('aura:disarm'),
  onAuraWakeWord: (callback) => {
    _wakeWordCallback = callback;
    // Drain any events that arrived before the renderer registered
    if (_pendingWakeEvents.length > 0) {
      console.log("[PRELOAD] Replaying", _pendingWakeEvents.length, "queued wake event(s)");
      _pendingWakeEvents.forEach(payload => callback?.(payload));
      _pendingWakeEvents = [];
    }
    // Return unsubscribe function
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