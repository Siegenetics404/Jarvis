// Jarvis Bridge: lets the local Jarvis program manage browser tabs and control YouTube.
// It only talks to your own PC (127.0.0.1), never to the internet.

const JARVIS_URL = "ws://127.0.0.1:8765";
let socket = null;
let heartbeat = null;

function connect() {
  if (
    socket &&
    (socket.readyState === WebSocket.OPEN ||
      socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }
  const ws = new WebSocket(JARVIS_URL);
  socket = ws;

  ws.onopen = () => {
    // A message every 20 seconds keeps this background worker awake.
    heartbeat = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ ping: true }));
    }, 20000);
  };

  ws.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    let reply;
    try {
      reply = await handle(msg);
    } catch (err) {
      reply = { error: String(err) };
    }
    if (ws.readyState === WebSocket.OPEN)
      ws.send(JSON.stringify({ id: msg.id, ...reply }));
  };

  ws.onclose = () => {
    clearInterval(heartbeat);
    if (socket === ws) socket = null;
  };

  ws.onerror = () => {
    try {
      ws.close();
    } catch (e) {}
  };
}

// The YouTube video to control: the one playing sound, otherwise the one you used most recently
async function youtubeTab() {
  const tabs = await chrome.tabs.query({
    url: ["*://www.youtube.com/watch*", "*://www.youtube.com/shorts/*"],
  });
  if (!tabs.length) return null;
  const playing = tabs.filter((t) => t.audible);
  const pool = playing.length ? playing : tabs;
  pool.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  return pool[0];
}

// Put the YouTube script into a tab. It does nothing if the tab already has it.
async function injectYouTubeScript(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["yt_content.js"],
    });
  } catch (e) {} // a tab that is closing or can't be scripted, skip it
}

async function injectIntoAllYouTubeTabs() {
  const tabs = await chrome.tabs.query({ url: ["*://www.youtube.com/*"] });
  for (const tab of tabs) await injectYouTubeScript(tab.id);
}

// Tabs that were already open before the extension was reloaded don't have the YouTube script yet, so add it
async function sendToYouTube(tabId, payload) {
  try {
    return await chrome.tabs.sendMessage(tabId, payload);
  } catch (err) {
    await injectYouTubeScript(tabId);
    return await chrome.tabs.sendMessage(tabId, payload);
  }
}

// A real mouse click on the Skip button, for when YouTube ignores clicks made by scripts.
// Chrome shows "started debugging this browser" for a moment while this runs.
async function realClickOnSkip(tabId) {
  const target = { tabId };
  try {
    await chrome.debugger.attach(target, "1.3");
  } catch (e) {
    if (!/already attached/i.test(String(e)))
      return { ok: false, error: String(e) };
  }
  try {
    await new Promise((resolve) => setTimeout(resolve, 350)); // the bar appears and the page settles
    // Ask the page again where the button is, because the bar can move things a little
    const [{ result }] = await chrome.scripting.executeScript({
      target,
      func: () =>
        window.__jarvisSkipPoint ? window.__jarvisSkipPoint() : null,
    });
    if (!result) return { ok: false, error: "no skip button" };
    for (const [type, buttons] of [
      ["mouseMoved", 0],
      ["mousePressed", 1],
      ["mouseReleased", 0],
    ]) {
      await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
        type,
        x: result.x,
        y: result.y,
        button: type === "mouseMoved" ? "none" : "left",
        buttons,
        clickCount: type === "mouseMoved" ? 0 : 1,
      });
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e) };
  } finally {
    try {
      await chrome.debugger.detach(target);
    } catch (e) {}
  }
}

// The YouTube page asks for a real click. Only YouTube tabs may ask.
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.jarvisClick) return;
  const tab = sender.tab;
  if (!tab || !tab.url || !tab.url.startsWith("https://www.youtube.com/")) {
    sendResponse({ ok: false, error: "not a YouTube tab" });
    return;
  }
  realClickOnSkip(tab.id).then(sendResponse);
  return true; // the answer comes a moment later
});

async function handle(msg) {
  if (msg.action === "list_tabs") {
    const tabs = await chrome.tabs.query({});
    return {
      tabs: tabs.map((t) => ({ id: t.id, title: t.title, url: t.url })),
    };
  }

  // The tab you are looking at, in the Chrome window you used last
  if (msg.action === "current_tab") {
    const [tab] = await chrome.tabs.query({
      active: true,
      lastFocusedWindow: true,
    });
    return {
      tab: tab ? { id: tab.id, title: tab.title, url: tab.url } : null,
    };
  }

  // Refresh exactly the tabs Jarvis chose, by ID (hard = skip the cache)
  if (msg.action === "reload_ids") {
    let reloaded = 0;
    for (const id of msg.ids || []) {
      try {
        await chrome.tabs.reload(id, { bypassCache: !!msg.hard });
        reloaded++;
      } catch (e) {} // already closed, skip it
    }
    return { reloaded };
  }

  // Close exactly the tabs Jarvis chose, by ID
  if (msg.action === "close_ids") {
    let closed = 0;
    for (const id of msg.ids || []) {
      try {
        await chrome.tabs.remove(id);
        closed++;
      } catch (e) {} // already closed, skip it
    }
    return { closed };
  }

  // Control the YouTube video that is playing
  if (msg.action === "youtube") {
    const tab = await youtubeTab();
    if (!tab) return { ok: false, reason: "no_tab" };
    const reply = await sendToYouTube(tab.id, {
      jarvis: "yt",
      command: msg.command,
      amount: msg.amount,
    });
    return reply || { ok: false, reason: "no_reply" };
  }

  // Turn "press Skip on ads automatically" on or off
  if (msg.action === "yt_auto_skip") {
    await chrome.storage.local.set({ autoSkip: !!msg.on });
    if (msg.on) await injectIntoAllYouTubeTabs(); // so tabs that are already open start watching right away
    return { ok: true };
  }

  // Older action, still used by bridge_test.py
  if (msg.action === "close_tabs") {
    const needle = (msg.match || "").trim().toLowerCase();
    if (needle.length < 3) return { error: "match text too short" }; // never close everything by accident
    const tabs = await chrome.tabs.query({});
    const hits = tabs.filter((t) =>
      `${t.title} ${t.url}`.toLowerCase().includes(needle),
    );
    if (hits.length) await chrome.tabs.remove(hits.map((t) => t.id));
    return { closed: hits.length };
  }

  return { error: "unknown action: " + msg.action };
}

// A YouTube page just finished loading: if auto skip is on, make sure it is being watched
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (
    info.status !== "complete" ||
    !tab.url ||
    !tab.url.startsWith("https://www.youtube.com/")
  )
    return;
  const { autoSkip } = await chrome.storage.local.get({ autoSkip: false });
  if (autoSkip) injectYouTubeScript(tabId);
});

// If Jarvis wasn't running yet, try again every 30 seconds
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
