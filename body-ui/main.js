const { app, BrowserWindow, ipcMain, screen } = require("electron");
const fs = require("fs");
const path = require("path");

const DATA_DIR = path.join(__dirname, "..", "data");
const SETTINGS_FILE = path.join(DATA_DIR, "orb_settings.json");
const WIN_W = 520;
const WIN_H = 760;
const MARGIN = 12;

let win = null;

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(SETTINGS_FILE, "utf8"));
  } catch {
    return {};
  }
}

function writeSettings(settings) {
  try {
    fs.mkdirSync(DATA_DIR, { recursive: true });
    fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2));
  } catch {
    /* not worth crashing for */
  }
}

function cornerXY(corner, w, h) {
  const wa = screen.getPrimaryDisplay().workArea; // the screen minus the taskbar
  return {
    x: corner.endsWith("left") ? wa.x + MARGIN : wa.x + wa.width - w - MARGIN,
    y: corner.startsWith("top") ? wa.y + MARGIN : wa.y + wa.height - h - MARGIN,
  };
}

function createWindow() {
  const settings = readSettings();
  const wa = screen.getPrimaryDisplay().workArea;
  const w = Math.min(WIN_W, wa.width);
  const h = Math.min(WIN_H, wa.height);

  let pos = cornerXY(
    settings.corner && settings.corner !== "custom"
      ? settings.corner
      : "bottom_right",
    w,
    h,
  );
  if (
    settings.corner === "custom" &&
    Number.isFinite(settings.x) &&
    Number.isFinite(settings.y)
  ) {
    pos = { x: settings.x, y: settings.y };
  }

  win = new BrowserWindow({
    width: w,
    height: h,
    x: pos.x,
    y: pos.y,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    resizable: false,
    hasShadow: false,
    skipTaskbar: true,
    alwaysOnTop: settings.always_on_top !== false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });

  win.loadFile("index.html");
  // Start click-through: the page switches this off while the mouse is over the orb, card or bubble
  win.webContents.once("did-finish-load", () =>
    win.setIgnoreMouseEvents(true, { forward: true }),
  );
}

ipcMain.handle("settings:load", () => readSettings());
ipcMain.on("settings:save", (e, settings) => writeSettings(settings));
ipcMain.on("mouse:ignore", (e, ignore) => {
  if (win)
    win.setIgnoreMouseEvents(!!ignore, ignore ? { forward: true } : undefined);
});
ipcMain.handle("win:bounds", () => (win ? win.getBounds() : null));
ipcMain.on("win:moveTo", (e, x, y) => {
  if (win) win.setPosition(Math.round(x), Math.round(y));
});
ipcMain.on("win:corner", (e, corner) => {
  if (!win) return;
  const b = win.getBounds();
  const p = cornerXY(corner, b.width, b.height);
  win.setPosition(p.x, p.y);
});
ipcMain.on("win:top", (e, on) => {
  if (win) win.setAlwaysOnTop(!!on, "floating");
});
ipcMain.on("app:quit", () => app.quit());

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
