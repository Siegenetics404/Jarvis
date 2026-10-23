const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvis", {
  // how to reach Jarvis. Empty when you start the orb on its own.
  config: {
    port: process.env.JARVIS_BODY_PORT || "",
    token: process.env.JARVIS_BODY_TOKEN || "",
  },

  // settings storage
  loadSettings: () => ipcRenderer.invoke("settings:load"),
  saveSettings: (settings) => ipcRenderer.send("settings:save", settings),

  // the settings window
  openSettings: () => ipcRenderer.send("settings:open"),
  closeSettings: () => ipcRenderer.send("settings:close"),
  updateSettings: (changes) => ipcRenderer.send("settings:update", changes),
  resetSettings: () => ipcRenderer.send("settings:reset"),
  onSettingsChanged: (callback) =>
    ipcRenderer.on("settings:changed", (e, changes) => callback(changes)),
  onSettingsReset: (callback) =>
    ipcRenderer.on("settings:reset", () => callback()),
  onSettingsRefresh: (callback) =>
    ipcRenderer.on("settings:refresh", () => callback()),

  // the window itself
  ignoreMouse: (ignore) => ipcRenderer.send("mouse:ignore", ignore),
  getBounds: () => ipcRenderer.invoke("win:bounds"),
  startDrag: () => ipcRenderer.send("drag:start"),
  endDrag: () => ipcRenderer.invoke("drag:end"),
  moveTo: (x, y) => ipcRenderer.send("win:moveTo", x, y),
  placeAt: (corner) => ipcRenderer.send("win:corner", corner),
  alwaysOnTop: (on) => ipcRenderer.send("win:top", on),
  quit: () => ipcRenderer.send("app:quit"),
});
