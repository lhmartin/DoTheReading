// The only bridge between the renderer and the pipeline.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("study", {
  library: () => ipcRenderer.invoke("library"),
  record: (payload) => ipcRenderer.invoke("record", payload),
  grade: (payload) => ipcRenderer.invoke("grade", payload),
  processInbox: () => ipcRenderer.invoke("process-inbox"),
  settings: () => ipcRenderer.invoke("settings"),
  saveSettings: (payload) => ipcRenderer.invoke("save-settings", payload),
  environment: () => ipcRenderer.invoke("environment"),
  pullModel: (model) => ipcRenderer.invoke("pull-model", model),
  schedule: (payload) => ipcRenderer.invoke("schedule", payload),
  onPullLog: (callback) => ipcRenderer.on("pull-log", (_event, line) => callback(line)),
  openExternal: (target) => ipcRenderer.invoke("open-external", target),
  onProcessLog: (callback) => ipcRenderer.on("process-log", (_event, line) => callback(line)),
});
