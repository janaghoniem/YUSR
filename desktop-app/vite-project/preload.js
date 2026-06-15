import { contextBridge, ipcRenderer } from 'electron';

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
    console.log("[PRELOAD] aura-wake-word event received");
    const handler = (_event, payload) => callback?.(payload);
    ipcRenderer.on('aura-wake-word', handler);
    return () => ipcRenderer.removeListener('aura-wake-word', handler);
  },
  // In preload.js, after existing exposures
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
