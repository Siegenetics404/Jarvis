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

// Tabs that were already open before the extension was reloaded don't have the YouTube script yet, so add it
async function sendToYouTube(tabId, payload) {
  try {
    return await chrome.tabs.sendMessage(tabId, payload);
  } catch (err) {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["yt_content.js"],
    });
    return await chrome.tabs.sendMessage(tabId, payload);
  }
}

async function handle(msg) {
  if (msg.action === "list_tabs") {
    const tabs = await chrome.tabs.query({});
    return {
      tabs: tabs.map((t) => ({ id: t.id, title: t.title, url: t.url })),
    };
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

// If Jarvis wasn't running yet, try again every 30 seconds
chrome.alarms.create("reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
