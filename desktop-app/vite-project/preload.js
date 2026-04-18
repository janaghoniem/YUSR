import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  closeWindow:     () => ipcRenderer.invoke('window:close'),
  minimizeWindow:  () => ipcRenderer.invoke('window:minimize'),
  maximizeWindow:  () => ipcRenderer.invoke('window:maximize'),
  enterWidgetMode: () => ipcRenderer.invoke('widget:enter'),
  exitWidgetMode:  () => ipcRenderer.invoke('widget:exit'),
  initAura:        (options) => ipcRenderer.invoke('aura:init', options),
  transcribeOnce:  (options) => ipcRenderer.invoke('aura:transcribe-once', options),
  onAuraWakeWord:  (callback) => {
    const handler = (_event, payload) => callback?.(payload);
    ipcRenderer.on('aura-wake-word', handler);
    return () => ipcRenderer.removeListener('aura-wake-word', handler);
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
});