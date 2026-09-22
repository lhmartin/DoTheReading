// The only bridge between the renderer and the pipeline.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("study", {
  library: () => ipcRenderer.invoke("library"),
  record: (payload) => ipcRenderer.invoke("record", payload),
  grade: (payload) => ipcRenderer.invoke("grade", payload),
  processInbox: () => ipcRenderer.invoke("process-inbox"),
  openExternal: (target) => ipcRenderer.invoke("open-external", target),
  onProcessLog: (callback) => ipcRenderer.on("process-log", (_event, line) => callback(line)),
});
