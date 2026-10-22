import { createOrb, STATES, THEMES } from "./orb.js";

const api = window.jarvis; // set up by preload.js
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

const stage = $("stage"),
  card = $("card"),
  orbBox = $("orbBox");
const cardSlot = $("cardOrbSlot"),
  dockSlot = $("dockSlot");
const bubble = $("bubble"),
  bubbleInner = $("bubbleInner");
const userEl = $("userText"),
  jarvisEl = $("jarvisText"),
  stateEl = $("stateLabel");

const orb = createOrb($("orbCanvas"));
const STATE_NAMES = Object.keys(STATES);
const CORNERS = ["bottom_right", "bottom_left", "top_right", "top_left"];

// ---------- messages to and from Jarvis (Step 3 connects a WebSocket here) ----------
let socket = null;
function send(msg) {
  if (socket && socket.readyState === 1) socket.send(JSON.stringify(msg));
  else console.log("[to Jarvis]", msg);
}

// ---------- settings ----------
const DEFAULTS = {
  alpha: 0.9,
  corner: "bottom_right",
  anchor: "bottom_right",
  x: null,
  y: null,
  orb_size: 96,
  theme: "arc",
  start_minimized: false,
  talk_mode: "bubble",
  bubble_seconds: 4,
  tuck_seconds: 20,
  always_on_top: true,
};
let S = { ...DEFAULTS };

function sanitize(raw) {
  const s = { ...DEFAULTS, ...raw };
  s.alpha = clamp(Number(s.alpha) || DEFAULTS.alpha, 0.4, 1);
  s.orb_size = [72, 96, 130].includes(Number(s.orb_size))
    ? Number(s.orb_size)
    : 96;
  if (!THEMES[s.theme]) s.theme = "arc";
  if (!["bubble", "open", "none"].includes(s.talk_mode)) s.talk_mode = "bubble";
  s.bubble_seconds = clamp(Math.round(Number(s.bubble_seconds) || 4), 1, 15);
  s.tuck_seconds = clamp(Math.round(Number(s.tuck_seconds) || 0), 0, 120);
  if (!CORNERS.includes(s.corner) && s.corner !== "custom")
    s.corner = "bottom_right";
  if (!CORNERS.includes(s.anchor))
    s.anchor = CORNERS.includes(s.corner) ? s.corner : "bottom_right";
  s.start_minimized = !!s.start_minimized;
  s.always_on_top = s.always_on_top !== false;
  return s;
}

let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => api.saveSettings(S), 250);
}

function applySettings() {
  stage.style.opacity = S.alpha;
  document.documentElement.style.setProperty("--orb-min", S.orb_size + "px");
  stage.dataset.corner = S.anchor;
  orb.setTheme(S.theme);
  applyAccent();
  api.alwaysOnTop(S.always_on_top);
}

function applyAccent() {
  document.documentElement.style.setProperty(
    "--accent",
    orb.colorOf(state).join(","),
  );
}

function placeCorner(corner) {
  S.corner = corner;
  S.anchor = corner;
  S.x = S.y = null;
  stage.dataset.corner = corner;
  api.placeAt(corner);
  save();
}

// After you drag him somewhere: decide which side of the screen he is on, so the bubble faces the middle,
// and shift the window so the orb itself doesn't move.
async function reanchor() {
  const b = await api.getBounds();
  if (!b) return;
  const r0 = orbBox.getBoundingClientRect();
  const cx = b.x + r0.left + r0.width / 2;
  const cy = b.y + r0.top + r0.height / 2;
  const left = cx < (screen.availLeft || 0) + screen.availWidth / 2;
  const top = cy < (screen.availTop || 0) + screen.availHeight / 2;
  stage.dataset.corner = `${top ? "top" : "bottom"}_${left ? "left" : "right"}`;
  const r1 = orbBox.getBoundingClientRect();
  const nx = b.x + r0.left - r1.left;
  const ny = b.y + r0.top - r1.top;
  api.moveTo(nx, ny);
  S.corner = "custom";
  S.anchor = stage.dataset.corner;
  S.x = Math.round(nx);
  S.y = Math.round(ny);
  save();
}

// ---------- typing text ----------
class Typer {
  constructor() {
    this.text = "";
    this.pos = 0;
    this.cps = 45;
  }
  set(text, cps) {
    this.text = text;
    this.pos = 0;
    this.cps = cps;
  }
  step(dt) {
    if (this.pos < this.text.length)
      this.pos = Math.min(this.text.length, this.pos + this.cps * dt);
  }
  get shown() {
    return this.text.slice(0, Math.floor(this.pos));
  }
  get typing() {
    return this.pos < this.text.length;
  }
}
const userT = new Typer();
const jarvisT = new Typer();
let lastUser = null,
  lastJarvis = null;

function renderTexts() {
  const u = userT.shown,
    j = jarvisT.shown;
  if (u !== lastUser) {
    userEl.textContent = u || "...";
    userEl.classList.toggle("empty", !u);
    lastUser = u;
  }
  if (j !== lastJarvis) {
    jarvisEl.textContent = j || "...";
    jarvisEl.classList.toggle("empty", !j);
    bubbleInner.textContent = j;
    lastJarvis = j;
    if (jarvisT.typing) jarvisEl.scrollTop = jarvisEl.scrollHeight;
  }
  userEl.classList.toggle("typing", userT.typing);
  jarvisEl.classList.toggle("typing", jarvisT.typing);
}

// ---------- state ----------
let state = "idle";
let minimized = false;
let autoOpened = false; // the panel opened by itself
let popBlocked = false; // you minimized it while he was talking: don't open it again for this reply
let dismissed = false; // you touched the orb: hide the bubble for this reply
let toggledAt = 0;
let bubbleUntil = 0;
let lastActive = performance.now();
let flashState = null,
  flashUntil = 0;
let externalLevel = 0,
  lastLevelAt = -1e9;

function setState(name) {
  if (!STATES[name]) return;
  state = name;
  orb.setState(name);
  stateEl.textContent = name.toUpperCase();
  applyAccent();
  if (name === "idle") {
    popBlocked = false;
    dismissed = false;
  }
}

function flash(name, seconds) {
  setState(name);
  flashState = name;
  flashUntil = performance.now() + seconds * 1000;
}

// Move an element to a new place and animate it there, instead of jumping
function flip(el, change) {
  const first = el.getBoundingClientRect();
  change();
  const last = el.getBoundingClientRect();
  if (!last.width) return;
  const scale = first.width / last.width;
  el.animate(
    [
      {
        transformOrigin: "top left",
        transform: `translate(${first.left - last.left}px, ${first.top - last.top}px) scale(${scale})`,
      },
      { transformOrigin: "top left", transform: "none" },
    ],
    { duration: 420, easing: "cubic-bezier(.2, .8, .2, 1)" },
  );
}

function setMinimized(flag, { byUser = true, animate = true } = {}) {
  if (flag === minimized) return;
  minimized = flag;
  toggledAt = performance.now();
  lastActive = toggledAt;
  autoOpened = !flag && !byUser;
  if (flag && byUser) popBlocked = true;
  closeMenu();
  const change = () => {
    stage.dataset.mode = flag ? "minimized" : "expanded";
    (flag ? dockSlot : cardSlot).appendChild(orbBox);
  };
  if (animate) flip(orbBox, change);
  else change();
}

function openSettings() {
  if (minimized) setMinimized(false);
  syncSettingsUI();
  card.classList.add("settingsOpen");
}
function closeSettings() {
  card.classList.remove("settingsOpen");
}

// ---------- what Jarvis can tell the body ----------
function handleMessage(m) {
  switch (m.type) {
    case "state":
      setState(m.value);
      break;
    case "flash":
      flash(m.value, m.seconds ?? 2);
      break;
    case "user":
      userT.set(m.text || "", 70);
      break;
    case "jarvis": {
      const text = m.text || "";
      const cps = m.duration
        ? clamp(text.length / Math.max(0.5, m.duration * 0.85), 15, 120)
        : 45;
      jarvisT.set(text, cps);
      break;
    }
    case "level":
      externalLevel = clamp(Number(m.value) || 0, 0, 1);
      lastLevelAt = performance.now();
      break;
    case "panel":
      if (m.action === "minimize") setMinimized(true);
      else if (m.action === "expand") setMinimized(false);
      else if (m.action === "toggle") setMinimized(!minimized);
      else if (m.action === "settings") openSettings();
      break;
  }
}
window.handleMessage = handleMessage;

// ---------- the main loop ----------
let last = performance.now();
function loop(now) {
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;

  userT.step(dt);
  jarvisT.step(dt);
  renderTexts();

  if (flashState && now >= flashUntil) {
    if (state === flashState) setState("idle");
    flashState = null;
  }

  const talking = state === "speaking" || state === "error";
  if (state !== "idle" || userT.typing || jarvisT.typing) lastActive = now;
  if (talking && jarvisT.text) bubbleUntil = now + S.bubble_seconds * 1000;

  if (
    minimized &&
    !popBlocked &&
    (state === "alarm" || (talking && S.talk_mode === "open"))
  ) {
    setMinimized(false, { byUser: false });
  } else if (
    !minimized &&
    autoOpened &&
    S.tuck_seconds > 0 &&
    !card.classList.contains("settingsOpen") &&
    now - lastActive > S.tuck_seconds * 1000
  ) {
    setMinimized(true, { byUser: false });
  }

  bubble.classList.toggle(
    "show",
    minimized &&
      S.talk_mode === "bubble" &&
      !!jarvisT.text &&
      now < bubbleUntil &&
      !dismissed,
  );

  // How loud he is: real levels from Jarvis if they arrive, otherwise a made-up wave while speaking
  let level = 0;
  if (now - lastLevelAt < 300) level = externalLevel;
  else if (state === "speaking")
    level = clamp(
      0.55 + 0.45 * Math.sin((now / 1000) * 11) * Math.sin((now / 1000) * 2.7),
      0,
      1,
    );
  orb.setLevel(level);
  orb.setFps(minimized && state === "idle" ? 24 : 60);

  requestAnimationFrame(loop);
}

// ---------- mouse: click-through, dragging, clicking ----------
let interactive = false;
let drag = null;
function setInteractive(on) {
  if (on !== interactive) {
    interactive = on;
    api.ignoreMouse(!on);
  }
}
document.addEventListener("mousemove", (e) => {
  if (drag) setInteractive(true);
  else setInteractive(!!e.target.closest(".hit"));
  if (!drag) return;
  const dx = e.screenX - drag.sx,
    dy = e.screenY - drag.sy;
  if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
  if (drag.moved) api.moveTo(drag.bx + dx, drag.by + dy);
});

document.addEventListener("mousedown", async (e) => {
  if (e.button !== 0) return;
  const zone = e.target.closest("[data-drag]");
  if (!zone) return;
  if (zone === orbBox && minimized) dismissed = true; // touching the orb hides the bubble
  const b = await api.getBounds();
  if (b)
    drag = {
      sx: e.screenX,
      sy: e.screenY,
      bx: b.x,
      by: b.y,
      moved: false,
      zone,
    };
});

document.addEventListener("mouseup", () => {
  if (!drag) return;
  const d = drag;
  drag = null;
  if (d.moved) reanchor();
  else if (d.zone === orbBox && minimized) setMinimized(false);
});

orbBox.addEventListener("dblclick", () => {
  if (!minimized && performance.now() - toggledAt > 600) send({ type: "stop" }); // double-click = stop him
});
bubble.addEventListener("click", () => setMinimized(false));
$("btnMin").addEventListener("click", () => setMinimized(true));
$("btnSettings").addEventListener("click", () =>
  card.classList.contains("settingsOpen") ? closeSettings() : openSettings(),
);

// ---------- right-click menu ----------
const menu = $("menu"),
  shield = $("menuShield");
function openMenu(x, y) {
  $("menuToggle").textContent = minimized ? "Expand" : "Minimize";
  menu.classList.add("open");
  shield.classList.add("open");
  menu.style.left =
    clamp(x, 0, window.innerWidth - menu.offsetWidth - 4) + "px";
  menu.style.top =
    clamp(y, 0, window.innerHeight - menu.offsetHeight - 4) + "px";
}
function closeMenu() {
  menu.classList.remove("open");
  shield.classList.remove("open");
}
document.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (e.target.closest(".hit")) openMenu(e.clientX, e.clientY);
});
shield.addEventListener("mousedown", closeMenu);
window.addEventListener("blur", closeMenu);
menu.addEventListener("click", (e) => {
  const act = e.target.dataset && e.target.dataset.act;
  closeMenu();
  if (act === "toggle") setMinimized(!minimized);
  else if (act === "settings") openSettings();
  else if (act === "stop") send({ type: "stop" });
  else if (act === "quit") api.quit();
});

// ---------- settings sheet ----------
function syncSettingsUI() {
  $("setAlpha").value = Math.round(S.alpha * 100);
  $("setCorner").value = S.corner;
  $("setOrb").value = String(S.orb_size);
  $("setTheme").value = S.theme;
  $("setTalk").value = S.talk_mode;
  $("setBubble").value = S.bubble_seconds;
  $("valBubble").textContent = S.bubble_seconds;
  $("setTuck").value = S.tuck_seconds;
  $("valTuck").textContent = S.tuck_seconds;
  $("setStartMin").checked = S.start_minimized;
  $("setTop").checked = S.always_on_top;
}

$("setAlpha").addEventListener("input", (e) => {
  S.alpha = e.target.value / 100;
  applySettings();
  save();
});
$("setCorner").addEventListener("change", (e) => placeCorner(e.target.value));
$("setOrb").addEventListener("change", (e) => {
  S.orb_size = Number(e.target.value);
  flip(orbBox, applySettings);
  save();
});
$("setTheme").addEventListener("change", (e) => {
  S.theme = e.target.value;
  applySettings();
  save();
});
$("setTalk").addEventListener("change", (e) => {
  S.talk_mode = e.target.value;
  save();
});
$("setBubble").addEventListener("input", (e) => {
  S.bubble_seconds = Number(e.target.value);
  $("valBubble").textContent = S.bubble_seconds;
  save();
});
$("setTuck").addEventListener("input", (e) => {
  S.tuck_seconds = Number(e.target.value);
  $("valTuck").textContent = S.tuck_seconds;
  save();
});
$("setStartMin").addEventListener("change", (e) => {
  S.start_minimized = e.target.checked;
  save();
});
$("setTop").addEventListener("change", (e) => {
  S.always_on_top = e.target.checked;
  applySettings();
  save();
});
$("btnDone").addEventListener("click", closeSettings);
$("btnReset").addEventListener("click", () => {
  S = { ...DEFAULTS };
  applySettings();
  placeCorner(S.corner);
  syncSettingsUI();
  save();
});

// ---------- a demo to look at while we build the rest ----------
const DEMO = [
  { s: "idle", wait: 3 },
  { s: "listening", wait: 3 },
  { s: "thinking", user: "Jarvis, open docker desktop", wait: 3 },
  {
    s: "speaking",
    user: "Jarvis, open docker desktop",
    jarvis: "Opening docker desktop, sir.",
    wait: 4,
  },
  {
    s: "idle",
    user: "Jarvis, open docker desktop",
    jarvis: "Opening docker desktop, sir.",
    panel: "minimize",
    wait: 4,
  },
  {
    s: "speaking",
    user: "Jarvis, what time is it?",
    jarvis: "It's 3:45 PM, sir.",
    wait: 6,
  },
  { s: "idle", wait: 5 },
  {
    s: "speaking",
    user: "Jarvis, open my project",
    jarvis:
      "Opening Docker, then Ubuntu, then your project, sir. All three should be ready in a moment.",
    wait: 9,
  },
  { s: "idle", wait: 5 },
  {
    s: "ignored",
    flash: 2.5,
    user: "what is the weather like",
    jarvis: "Ignored: I didn't hear my name.",
    panel: "expand",
    wait: 4,
  },
  {
    s: "error",
    flash: 2.5,
    user: "Jarvis, open docker desktop",
    jarvis: "I can't reach my brain, sir. Is LM Studio's server running?",
    wait: 5,
  },
  {
    s: "alarm",
    user: "Alarm",
    jarvis: "Time to wake up. Say stop to dismiss it.",
    wait: 6,
  },
  { s: "idle", wait: 4 },
];
let demoRunning = false;
const sleep = (seconds) =>
  new Promise((resolve) => setTimeout(resolve, seconds * 1000));

async function toggleDemo() {
  if (demoRunning) {
    demoRunning = false;
    return;
  }
  demoRunning = true;
  while (demoRunning) {
    for (const step of DEMO) {
      if (!demoRunning) break;
      handleMessage(
        step.flash
          ? { type: "flash", value: step.s, seconds: step.flash }
          : { type: "state", value: step.s },
      );
      handleMessage({ type: "user", text: step.user || "" });
      handleMessage({
        type: "jarvis",
        text: step.jarvis || "",
        duration: step.jarvis ? step.jarvis.length / 14 : 0,
      });
      if (step.panel) handleMessage({ type: "panel", action: step.panel });
      await sleep(step.wait);
    }
  }
}

// Keys for trying things out (click the orb first so the window has focus)
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeMenu();
    closeSettings();
    return;
  }
  if (e.target.matches("input, select")) return;
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= STATE_NAMES.length) setState(STATE_NAMES[n - 1]);
  if (e.key === "m") setMinimized(!minimized);
  if (e.key === "d") toggleDemo();
});

// ---------- start ----------
(async function init() {
  S = sanitize(await api.loadSettings());
  applySettings();
  syncSettingsUI();
  setState("idle");
  if (S.start_minimized) setMinimized(true, { animate: false });
  requestAnimationFrame(loop);
})();
