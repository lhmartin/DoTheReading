// The only bridge between the renderer and the pipeline.
const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("study", {
  library: () => ipcRenderer.invoke("library"),
  record: (payload) => ipcRenderer.invoke("record", payload),
  grade: (payload) => ipcRenderer.invoke("grade", payload),
  processInbox: (only) => ipcRenderer.invoke("process-inbox", only),
  shelve: (name) => ipcRenderer.invoke("shelve", name),
  notes: (paper) => ipcRenderer.invoke("notes", paper),
  saveNotes: (payload) => ipcRenderer.invoke("save-notes", payload),
  addPapers: (paths) => ipcRenderer.invoke("add-papers", paths),
  choosePapers: () => ipcRenderer.invoke("choose-papers"),
  // Electron 32+ dropped File.path; this is the supported way to learn where
  // a dropped file lives on disk.
  pathForFile: (file) => webUtils.getPathForFile(file),
  settings: () => ipcRenderer.invoke("settings"),
  saveSettings: (payload) => ipcRenderer.invoke("save-settings", payload),
  environment: () => ipcRenderer.invoke("environment"),
  pullModel: (model) => ipcRenderer.invoke("pull-model", model),
  schedule: (payload) => ipcRenderer.invoke("schedule", payload),
  ollamaMemory: (action) => ipcRenderer.invoke("ollama-memory", action),
  startOllama: () => ipcRenderer.invoke("start-ollama"),
  paperInfo: (paper) => ipcRenderer.invoke("paper-info", paper),
  addUrl: (payload) => ipcRenderer.invoke("add-url", payload),
  renderUrl: (url) => ipcRenderer.invoke("render-url", url),
  addText: (payload) => ipcRenderer.invoke("add-text", payload),
  removePaper: (name) => ipcRenderer.invoke("remove-paper", name),
  clipboardUrl: () => ipcRenderer.invoke("clipboard-url"),
  onAddUrlLog: (callback) => ipcRenderer.on("add-url-log", (_event, line) => callback(line)),
  onPullLog: (callback) => ipcRenderer.on("pull-log", (_event, line) => callback(line)),
  openExternal: (target) => ipcRenderer.invoke("open-external", target),
  onProcessLog: (callback) => ipcRenderer.on("process-log", (_event, line) => callback(line)),
});
