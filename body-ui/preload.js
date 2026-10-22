const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvis", {
  loadSettings: () => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings) => ipcRenderer.send("settings:save", settings),
  ignoreMouse: (ignore) => ipcRenderer.send("mouse:ignore", ignore),
  getBounds: () => ipcRenderer.invoke("win:bounds"),
  moveTo: (x, y) => ipcRenderer.send("win:moveTo", x, y),
  placeAt: (corner) => ipcRenderer.send("win:corner", corner),
  alwaysOnTop: (on) => ipcRenderer.send("win:top", on),
  quit: () => ipcRenderer.send("app:quit"),
});
