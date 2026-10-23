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

// ---------- the connection to Jarvis ----------
let socket = null;
let everConnected = false;
let lostAt = 0;
function send(msg) {
  if (socket && socket.readyState === 1) socket.send(JSON.stringify(msg));
  else console.log("[to Jarvis]", msg);
}

function connectToJarvis() {
  const { port, token } = api.config || {};
  if (!port) return; // started on its own, so only the demo keys work
  const ws = new WebSocket(
    `ws://127.0.0.1:${port}/?token=${encodeURIComponent(token)}`,
  );
  socket = ws;
  ws.onopen = () => {
    everConnected = true;
    lostAt = 0;
  };
  ws.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (err) {
      console.error("bad message", err);
    }
  };
  ws.onclose = () => {
    if (socket === ws) socket = null;
    if (!lostAt) lostAt = performance.now();
    // if Jarvis is gone for good, don't leave the orb floating there alone
    if (everConnected && performance.now() - lostAt > 30000) {
      api.quit();
      return;
    }
    setTimeout(connectToJarvis, 1500);
  };
  ws.onerror = () => {
    try {
      ws.close();
    } catch (err) {
      /* already closed */
    }
  };
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
  const aL = screen.availLeft ?? 0;
  const aT = screen.availTop ?? 0;
  const r0 = orbBox.getBoundingClientRect();
  const cx = b.x + r0.left + r0.width / 2;
  const cy = b.y + r0.top + r0.height / 2;
  const left = cx < aL + screen.availWidth / 2;
  const top = cy < aT + screen.availHeight / 2;
  stage.dataset.corner = `${top ? "top" : "bottom"}_${left ? "left" : "right"}`;
  const r1 = orbBox.getBoundingClientRect();
  let nx = b.x + r0.left - r1.left;
  let ny = b.y + r0.top - r1.top;
  // never leave the orb outside the screen
  nx +=
    clamp(nx + r1.left, aL + 4, aL + screen.availWidth - r1.width - 4) -
    (nx + r1.left);
  ny +=
    clamp(ny + r1.top, aT + 4, aT + screen.availHeight - r1.height - 4) -
    (ny + r1.top);
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

// ---------- what Jarvis can tell the body ----------
function handleMessage(m) {
  switch (m.type) {
    // case "state":
    //   setState(m.value);
    //   break;
    case "state":
      if (
        flashState &&
        performance.now() < flashUntil &&
        (m.value === "idle" || m.value === "listening")
      )
        break;
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
      else if (m.action === "settings") api.openSettings();
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
  if (drag) {
    setInteractive(true);
    return;
  }
  setInteractive(!!e.target.closest(".hit"));
});

// The main process follows the real cursor while you hold the button, so the window can't drift
document.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const zone = e.target.closest("[data-drag]");
  if (!zone || e.target.closest("button, #jarvisText, #bubble")) return;
  if (minimized && zone === orbBox) dismissed = true; // touching the orb hides the bubble
  drag = { zone };
  api.startDrag();
});

document.addEventListener("mouseup", async () => {
  if (!drag) return;
  const d = drag;
  drag = null;
  const moved = await api.endDrag();
  if (moved) reanchor();
  else if (d.zone === orbBox && minimized) setMinimized(false); // a click on the orb opens the panel
});

orbBox.addEventListener("dblclick", () => {
  if (!minimized && performance.now() - toggledAt > 600) send({ type: "stop" }); // double-click = stop him
});
bubble.addEventListener("click", () => setMinimized(false));
$("btnMin").addEventListener("click", () => setMinimized(true));
$("btnSettings").addEventListener("click", () => api.openSettings());

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
  else if (act === "settings") api.openSettings();
  else if (act === "stop") send({ type: "stop" });
  else if (act === "quit") api.quit();
});

// ---------- changes coming from the settings window ----------
api.onSettingsChanged((changes) => {
  const sizeChanged =
    "orb_size" in changes && Number(changes.orb_size) !== S.orb_size;
  S = sanitize({ ...S, ...changes });
  if (sizeChanged) flip(orbBox, applySettings);
  else applySettings();
  if ("corner" in changes && CORNERS.includes(changes.corner))
    placeCorner(changes.corner); // moves him and saves
  save();
});
api.onSettingsReset(() => {
  S = { ...DEFAULTS };
  flip(orbBox, applySettings);
  placeCorner(S.corner);
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
    return;
  }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= STATE_NAMES.length) setState(STATE_NAMES[n - 1]);
  if (e.key === "m") setMinimized(!minimized);
  if (e.key === "d") toggleDemo();
});

// ---------- start ----------
(async function init() {
  S = sanitize(await api.loadSettings());
  applySettings();
  setState("idle");
  //   if (S.start_minimized) setMinimized(true, { animate: false });
  //   requestAnimationFrame(loop);
  // })();
  if (S.start_minimized) setMinimized(true, { animate: false });
  requestAnimationFrame(loop);
  connectToJarvis();
})();
