// Runs inside YouTube pages. It controls the video when Jarvis asks, and presses the Skip button on ads when asked to.
// It logs what it does to the page's console, with the tag [Jarvis].
(() => {
  if (window.__jarvisYT) return; // already running in this tab
  window.__jarvisYT = true;

  // YouTube renames these now and then, so there are several, plus a text search below
  const SKIP_SELECTORS = [
    ".ytp-skip-ad-button",
    ".ytp-ad-skip-button",
    ".ytp-ad-skip-button-modern",
    ".ytp-ad-skip-button-container button",
    ".ytp-skip-ad button",
    'button[id^="skip-button"]',
    ".ytp-ad-player-overlay-layout__skip-or-preview-container button",
  ];
  const player = () => document.getElementById("movie_player");
  const video = () =>
    document.querySelector("video.html5-main-video") ||
    document.querySelector("video");
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const adShowing = () => {
    const p = player();
    return !!(
      p &&
      (p.classList.contains("ad-showing") ||
        p.classList.contains("ad-interrupting"))
    );
  };
  const clickable = (el) => {
    if (!el || el.disabled || el.getAttribute("aria-disabled") === "true")
      return false;
    const r = el.getBoundingClientRect();
    return (
      r.width > 0 &&
      r.height > 0 &&
      getComputedStyle(el).visibility !== "hidden"
    );
  };
  const fmt = (t) => {
    const s = Math.max(0, Math.floor(t));
    const h = Math.floor(s / 3600),
      m = Math.floor((s % 3600) / 60),
      r = s % 60;
    return (
      (h ? h + ":" + String(m).padStart(2, "0") : m) +
      ":" +
      String(r).padStart(2, "0")
    );
  };

  function findSkipButton() {
    for (const selector of SKIP_SELECTORS) {
      for (const el of document.querySelectorAll(selector)) {
        if (clickable(el)) return { el, how: selector };
      }
    }
    // Any visible button in the player that says "Skip"
    for (const el of document.querySelectorAll(
      "#movie_player button, .html5-video-player button",
    )) {
      const text = (
        (el.innerText || "") +
        " " +
        (el.getAttribute("aria-label") || "")
      )
        .trim()
        .toLowerCase();
      if (/^skip( ads?)?\b/.test(text) && text.length < 24 && clickable(el))
        return { el, how: 'text "' + text + '"' };
    }
    return null;
  }

  // Where the Skip button is right now, so the extension can click exactly there with a real mouse click
  window.__jarvisSkipPoint = () => {
    const found = findSkipButton();
    if (!found) return null;
    const r = found.el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };

  // A scripted click that imitates a real mouse as closely as a page script can
  function press(el) {
    const r = el.getBoundingClientRect();
    const base = {
      bubbles: true,
      cancelable: true,
      composed: true,
      view: window,
      button: 0,
      clientX: r.left + r.width / 2,
      clientY: r.top + r.height / 2,
    };
    for (const type of [
      "pointerover",
      "mouseover",
      "pointerdown",
      "mousedown",
      "pointerup",
      "mouseup",
      "click",
    ]) {
      const isPointer = type.startsWith("pointer");
      const event = isPointer
        ? new PointerEvent(type, {
            ...base,
            pointerId: 1,
            pointerType: "mouse",
            isPrimary: true,
          })
        : new MouseEvent(type, base);
      el.dispatchEvent(event);
    }
  }

  let needReal = false; // once a scripted click has been ignored, go straight to a real one
  let skipping = false;
  let cooldownUntil = 0;

  // Press Skip, check it worked, and fall back to a real click if YouTube ignored the scripted one
  async function skipAd() {
    const found = findSkipButton();
    if (!found) return "no_button";
    console.log("[Jarvis] Skip button found with", found.how);
    if (!needReal) {
      press(found.el);
      await sleep(900);
      if (!findSkipButton()) {
        console.log("[Jarvis] skipped with a scripted click");
        return "plain";
      }
      console.log(
        "[Jarvis] YouTube ignored the scripted click, trying a real mouse click",
      );
    }
    let reply;
    try {
      reply = await chrome.runtime.sendMessage({ jarvisClick: true });
    } catch (err) {
      reply = { ok: false, error: String(err) };
    }
    await sleep(900);
    if (!findSkipButton()) {
      needReal = true;
      console.log("[Jarvis] skipped with a real click");
      return "real";
    }
    console.log("[Jarvis] the real click did not remove the button:", reply);
    return reply && reply.ok ? "ignored" : "click_failed";
  }

  // Auto skip: only presses the Skip button YouTube itself shows
  let autoSkip = false;
  let skipUntil = 0; // "skip this ad as soon as you can" lasts this long
  let lastNote = 0;
  chrome.storage.local.get({ autoSkip: false }, (v) => {
    autoSkip = !!v.autoSkip;
    console.log("[Jarvis] auto skip is", autoSkip ? "ON" : "OFF");
  });
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.autoSkip) {
      autoSkip = !!changes.autoSkip.newValue;
      console.log("[Jarvis] auto skip is now", autoSkip ? "ON" : "OFF");
    }
  });

  setInterval(() => {
    const now = Date.now();
    const active = autoSkip || now < skipUntil;
    if (adShowing() && now - lastNote > 6000) {
      lastNote = now;
      console.log(
        "[Jarvis] an ad is showing. auto skip is",
        active ? "ON, waiting for the Skip button" : "OFF",
      );
    }
    if (!active || skipping || now < cooldownUntil || !findSkipButton()) return;
    skipping = true;
    skipAd()
      .then((result) => console.log("[Jarvis] auto skip result:", result))
      .finally(() => {
        skipping = false;
        cooldownUntil = Date.now() + 1500;
      });
  }, 300);

  function visibleButton(selector) {
    const el = document.querySelector(selector);
    return el && clickable(el) && getComputedStyle(el).display !== "none"
      ? el
      : null;
  }

  async function run(command, amount) {
    const v = video();
    if (!v) return { ok: false, reason: "no_video" };
    const n = Number(amount);

    if (command === "skip_ad") {
      if (!findSkipButton()) {
        if (!adShowing()) return { ok: false, reason: "no_ad" };
        skipUntil = Date.now() + 20000; // not skippable yet: do it the moment the button appears
        return { ok: true, detail: "waiting" };
      }
      skipping = true;
      let result;
      try {
        result = await skipAd();
      } finally {
        skipping = false;
        cooldownUntil = Date.now() + 1500;
      }
      return result === "plain" || result === "real"
        ? { ok: true, detail: "skipped" }
        : { ok: false, reason: result };
    }

    if (command === "captions") {
      const button = document.querySelector(".ytp-subtitles-button");
      if (
        !button ||
        button.style.display === "none" ||
        button.getAttribute("aria-disabled") === "true"
      ) {
        return { ok: false, reason: "no_captions" };
      }
      const isOn = button.getAttribute("aria-pressed") === "true";
      const want = String(amount || "").toLowerCase();
      if (want === "on" && isOn) return { ok: true, on: true };
      if (want === "off" && !isOn) return { ok: true, on: false };
      button.click();
      return { ok: true, on: !isOn };
    }

    if (command === "next") {
      if (location.pathname.startsWith("/shorts")) {
        const down = document.querySelector("#navigation-button-down button");
        if (down) {
          down.click();
          return { ok: true };
        }
        return { ok: false, reason: "no_next" };
      }
      const nextButton = visibleButton(".ytp-next-button");
      if (nextButton) {
        nextButton.click();
        return { ok: true };
      }
      const link = document.querySelector(
        'ytd-watch-next-secondary-results-renderer a[href*="/watch"], #related a[href*="/watch"]',
      );
      if (link) {
        link.click();
        return { ok: true };
      }
      return { ok: false, reason: "no_next" };
    }

    if (command === "previous") {
      if (v.currentTime > 5 && !adShowing()) {
        v.currentTime = 0;
        return { ok: true, detail: "restart" };
      }
      const prevButton = visibleButton(".ytp-prev-button");
      if (prevButton) {
        prevButton.click();
        return { ok: true };
      }
      if (history.length > 1) {
        history.back();
        return { ok: true };
      }
      v.currentTime = 0;
      return { ok: true, detail: "restart" };
    }

    // Everything below changes the video itself, which would affect an ad
    if (adShowing()) return { ok: false, reason: "ad" };

    const end = isFinite(v.duration) ? v.duration - 0.5 : Infinity;

    if (command === "forward" || command === "back") {
      const step = n > 0 ? n : 10;
      v.currentTime = Math.max(
        0,
        Math.min(
          command === "forward" ? v.currentTime + step : v.currentTime - step,
          end,
        ),
      );
      return { ok: true, seconds: step, at: fmt(v.currentTime) };
    }
    if (command === "go_to") {
      if (!(n >= 0)) return { ok: false, reason: "bad_time" };
      v.currentTime = Math.min(n, end);
      return { ok: true, at: fmt(v.currentTime) };
    }
    if (command === "speed" || command === "faster" || command === "slower") {
      let rate = v.playbackRate;
      if (command === "faster") rate += 0.25;
      else if (command === "slower") rate -= 0.25;
      else if (n > 0) rate = n;
      else return { ok: false, reason: "bad_speed" };
      v.playbackRate = Math.max(
        0.25,
        Math.min(3, Math.round(rate * 100) / 100),
      );
      return { ok: true, rate: v.playbackRate };
    }
    return { ok: false, reason: "unknown" };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || msg.jarvis !== "yt") return;
    run(msg.command, msg.amount)
      .then(sendResponse)
      .catch((err) =>
        sendResponse({ ok: false, reason: "error", detail: String(err) }),
      );
    return true; // the answer comes a moment later
  });

  console.log("[Jarvis] YouTube controls are ready in this tab");
})();
