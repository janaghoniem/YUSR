const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  closeWindow:     () => ipcRenderer.invoke('window:close'),
  minimizeWindow:  () => ipcRenderer.invoke('window:minimize'),
  maximizeWindow:  () => ipcRenderer.invoke('window:maximize'),
  enterWidgetMode: () => ipcRenderer.invoke('widget:enter'),
  exitWidgetMode:  () => ipcRenderer.invoke('widget:exit'),
});
