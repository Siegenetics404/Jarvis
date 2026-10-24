// Runs inside YouTube pages. It controls the video when Jarvis asks, and presses the Skip button on ads when asked to.
(() => {
  if (window.__jarvisYT) return;          // already running in this tab
  window.__jarvisYT = true;

  const SKIP_BUTTONS = ['.ytp-skip-ad-button', '.ytp-ad-skip-button', '.ytp-ad-skip-button-modern',
                        '.ytp-ad-skip-button-container button'];
  const player = () => document.getElementById('movie_player');
  const video = () => document.querySelector('video.html5-main-video') || document.querySelector('video');
  const adShowing = () => { const p = player(); return !!(p && p.classList.contains('ad-showing')); };
  const visible = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const fmt = (t) => {
    const s = Math.max(0, Math.floor(t));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(r).padStart(2, '0');
  };

  function clickSkip() {
    for (const selector of SKIP_BUTTONS) {
      const el = document.querySelector(selector);
      if (visible(el)) { el.click(); return true; }
    }
    return false;
  }

  // Auto skip: only presses the Skip button YouTube itself shows
  let autoSkip = false;
  let skipUntil = 0;                       // "skip this ad as soon as you can" lasts this long
  chrome.storage.local.get({ autoSkip: false }, (v) => { autoSkip = !!v.autoSkip; });
  chrome.storage.onChanged.addListener((changes) => { if (changes.autoSkip) autoSkip = !!changes.autoSkip.newValue; });
  setInterval(() => { if ((autoSkip || Date.now() < skipUntil) && adShowing()) clickSkip(); }, 400);

  function visibleButton(selector) {
    const el = document.querySelector(selector);
    return el && visible(el) && getComputedStyle(el).display !== 'none' && el.getAttribute('aria-disabled') !== 'true' ? el : null;
  }

  function run(command, amount) {
    const v = video();
    if (!v) return { ok: false, reason: 'no_video' };
    const n = Number(amount);

    if (command === 'skip_ad') {
      if (!adShowing()) return { ok: false, reason: 'no_ad' };
      if (clickSkip()) return { ok: true, detail: 'skipped' };
      skipUntil = Date.now() + 20000;      // not skippable yet: do it the moment the button appears
      return { ok: true, detail: 'waiting' };
    }

    if (command === 'captions') {
      const button = document.querySelector('.ytp-subtitles-button');
      if (!button || button.style.display === 'none' || button.getAttribute('aria-disabled') === 'true') {
        return { ok: false, reason: 'no_captions' };
      }
      const isOn = button.getAttribute('aria-pressed') === 'true';
      const want = String(amount || '').toLowerCase();
      if (want === 'on' && isOn) return { ok: true, on: true };
      if (want === 'off' && !isOn) return { ok: true, on: false };
      button.click();
      return { ok: true, on: !isOn };
    }

    if (command === 'next') {
      if (location.pathname.startsWith('/shorts')) {
        const down = document.querySelector('#navigation-button-down button');
        if (down) { down.click(); return { ok: true }; }
        return { ok: false, reason: 'no_next' };
      }
      const nextButton = visibleButton('.ytp-next-button');
      if (nextButton) { nextButton.click(); return { ok: true }; }
      const link = document.querySelector('ytd-watch-next-secondary-results-renderer a[href*="/watch"], #related a[href*="/watch"]');
      if (link) { link.click(); return { ok: true }; }
      return { ok: false, reason: 'no_next' };
    }

    if (command === 'previous') {
      if (v.currentTime > 5 && !adShowing()) { v.currentTime = 0; return { ok: true, detail: 'restart' }; }
      const prevButton = visibleButton('.ytp-prev-button');
      if (prevButton) { prevButton.click(); return { ok: true }; }
      if (history.length > 1) { history.back(); return { ok: true }; }
      v.currentTime = 0;
      return { ok: true, detail: 'restart' };
    }

    // Everything below changes the video itself, which would affect an ad
    if (adShowing()) return { ok: false, reason: 'ad' };

    const end = isFinite(v.duration) ? v.duration - 0.5 : Infinity;

    if (command === 'forward' || command === 'back') {
      const step = n > 0 ? n : 10;
      v.currentTime = Math.max(0, Math.min(command === 'forward' ? v.currentTime + step : v.currentTime - step, end));
      return { ok: true, seconds: step, at: fmt(v.currentTime) };
    }
    if (command === 'go_to') {
      if (!(n >= 0)) return { ok: false, reason: 'bad_time' };
      v.currentTime = Math.min(n, end);
      return { ok: true, at: fmt(v.currentTime) };
    }
    if (command === 'speed' || command === 'faster' || command === 'slower') {
      let rate = v.playbackRate;
      if (command === 'faster') rate += 0.25;
      else if (command === 'slower') rate -= 0.25;
      else if (n > 0) rate = n;
      else return { ok: false, reason: 'bad_speed' };
      v.playbackRate = Math.max(0.25, Math.min(3, Math.round(rate * 100) / 100));
      return { ok: true, rate: v.playbackRate };
    }
    return { ok: false, reason: 'unknown' };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (!msg || msg.jarvis !== 'yt') return;
    try { sendResponse(run(msg.command, msg.amount)); }
    catch (err) { sendResponse({ ok: false, reason: 'error', detail: String(err) }); }
  });
})();