const {
  app,
  BrowserWindow,
  ipcMain,
  screen,
  globalShortcut,
} = require("electron");
const fs = require("fs");
const path = require("path");

const DATA_DIR = path.join(__dirname, "..", "data");
const SETTINGS_FILE = path.join(DATA_DIR, "orb_settings.json");
const WIN_W = 520;
const WIN_H = 760;
const MARGIN = 12;
const CORNERS = ["bottom_right", "bottom_left", "top_right", "top_left"];

let win = null;
let settingsWin = null;
let size = { width: WIN_W, height: WIN_H }; // the window never changes size, only position
let cache = null; // the latest settings, so the settings window never reads a stale file
let dragTimer = null;
let dragInfo = null;

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

// Is the part of the window where the orb lives (near its anchored corner) on any screen?
function orbPointVisible(x, y, w, h, anchor) {
  const px = anchor.endsWith("left") ? x + 60 : x + w - 60;
  const py = anchor.startsWith("top") ? y + 60 : y + h - 60;
  return screen.getAllDisplays().some((d) => {
    const a = d.workArea;
    return (
      px >= a.x && px <= a.x + a.width && py >= a.y && py <= a.y + a.height
    );
  });
}

// Always set the full bounds, so Windows display scaling can't make the window creep or change size
function moveTo(x, y) {
  if (!win || win.isDestroyed()) return;
  win.setBounds({
    x: Math.round(x),
    y: Math.round(y),
    width: size.width,
    height: size.height,
  });
}

function createWindow() {
  cache = readSettings();
  const wa = screen.getPrimaryDisplay().workArea;
  const w = Math.min(WIN_W, wa.width);
  const h = Math.min(WIN_H, wa.height);
  size = { width: w, height: h };

  let pos = cornerXY(
    CORNERS.includes(cache.corner) ? cache.corner : "bottom_right",
    w,
    h,
  );
  if (
    cache.corner === "custom" &&
    Number.isFinite(cache.x) &&
    Number.isFinite(cache.y)
  ) {
    const anchor = CORNERS.includes(cache.anchor)
      ? cache.anchor
      : "bottom_right";
    if (orbPointVisible(cache.x, cache.y, w, h, anchor)) {
      pos = { x: cache.x, y: cache.y };
    } else {
      // The saved position is outside every screen (for example from an old bug). Start in a safe place instead.
      console.log(
        "[body] the saved position was off screen, so the orb was moved back to the bottom-right corner",
      );
      cache.corner = "bottom_right";
      cache.anchor = "bottom_right";
      cache.x = cache.y = null;
      writeSettings(cache);
    }
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
    alwaysOnTop: cache.always_on_top !== false,
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
  win.on("closed", () => {
    win = null;
    if (settingsWin && !settingsWin.isDestroyed()) settingsWin.close();
  });
}

// Ctrl+Alt+J: bring him back to the bottom-right corner, wherever he got lost
function recover() {
  if (!win || win.isDestroyed()) return;
  const p = cornerXY("bottom_right", size.width, size.height);
  moveTo(p.x, p.y);
  win.webContents.send("settings:changed", { corner: "bottom_right" });
  win.show();
}

// ---------- the settings window ----------
function openSettingsWindow() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.focus();
    return;
  }
  const wa = screen.getPrimaryDisplay().workArea;
  const w = 460;
  const h = Math.min(720, wa.height - 20);
  settingsWin = new BrowserWindow({
    width: w,
    height: h,
    x: wa.x + Math.round((wa.width - w) / 2),
    y: wa.y + Math.round((wa.height - h) / 2),
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    title: "Jarvis panel settings",
    alwaysOnTop: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
    },
  });
  settingsWin.setAlwaysOnTop(true, "floating");
  settingsWin.loadFile("settings.html");
  settingsWin.on("closed", () => {
    settingsWin = null;
  });
}

// ---------- dragging: the main process follows the real cursor ----------
function stopDrag() {
  clearInterval(dragTimer);
  dragTimer = null;
  const moved = !!(dragInfo && dragInfo.moved);
  dragInfo = null;
  return moved;
}

ipcMain.on("drag:start", () => {
  if (!win || win.isDestroyed()) return;
  stopDrag();
  win.setIgnoreMouseEvents(false);
  const cursor = screen.getCursorScreenPoint();
  const [bx, by] = win.getPosition();
  dragInfo = { cx: cursor.x, cy: cursor.y, bx, by, moved: false };
  dragTimer = setInterval(() => {
    if (!dragInfo) return;
    const p = screen.getCursorScreenPoint();
    const dx = p.x - dragInfo.cx;
    const dy = p.y - dragInfo.cy;
    if (!dragInfo.moved && Math.abs(dx) + Math.abs(dy) > 4)
      dragInfo.moved = true; // a click is not a drag
    if (dragInfo.moved) moveTo(dragInfo.bx + dx, dragInfo.by + dy);
  }, 8);
});
ipcMain.handle("drag:end", () => stopDrag());

// ---------- settings and the rest ----------
ipcMain.handle("settings:load", () => cache || (cache = readSettings()));
ipcMain.on("settings:save", (e, settings) => {
  cache = settings;
  writeSettings(settings);
});
ipcMain.on("settings:open", () => openSettingsWindow());
ipcMain.on("settings:close", () => {
  if (settingsWin && !settingsWin.isDestroyed()) settingsWin.close();
});
ipcMain.on("settings:update", (e, changes) => {
  // from the settings window to the panel
  if (win && !win.isDestroyed())
    win.webContents.send("settings:changed", changes);
});
ipcMain.on("settings:reset", () => {
  if (win && !win.isDestroyed()) win.webContents.send("settings:reset");
  setTimeout(() => {
    // give the panel a moment to save, then refresh the form
    if (settingsWin && !settingsWin.isDestroyed())
      settingsWin.webContents.send("settings:refresh");
  }, 600);
});

ipcMain.on("mouse:ignore", (e, ignore) => {
  if (win && !win.isDestroyed())
    win.setIgnoreMouseEvents(!!ignore, ignore ? { forward: true } : undefined);
});
ipcMain.handle("win:bounds", () =>
  win && !win.isDestroyed() ? win.getBounds() : null,
);
ipcMain.on("win:moveTo", (e, x, y) => moveTo(x, y));
ipcMain.on("win:corner", (e, corner) => {
  const p = cornerXY(corner, size.width, size.height);
  moveTo(p.x, p.y);
});
ipcMain.on("win:top", (e, on) => {
  if (win && !win.isDestroyed()) win.setAlwaysOnTop(!!on, "floating");
});
ipcMain.on("app:quit", () => app.quit());

app.whenReady().then(() => {
  createWindow();
  globalShortcut.register("CommandOrControl+Alt+J", recover);
});
app.on("will-quit", () => globalShortcut.unregisterAll());
app.on("window-all-closed", () => app.quit());
