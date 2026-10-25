"""YouTube controls, through the Jarvis Bridge Chrome extension: skip ahead or back, jump to a time,
next and previous video, skip ads when YouTube allows it, speed and captions."""
import re
import time

import browser_bridge as bridge
from skills import tool

OFFLINE = "I can't reach your browser, sir. Is the Jarvis Bridge extension switched on in Chrome?"

_auto_skip_on_at = 0.0           # when auto skip was last switched on, so we don't announce it twice in a row

_FAILURES = {
    "no_tab": "I don't see a YouTube video open, sir.",
    "no_video": "I don't see a video on that page, sir.",
    "ad": "An ad is playing, sir, so I can't do that yet.",
    "no_ad": "There's no ad playing, sir.",
    "no_next": "I couldn't find a next video, sir.",
    "no_captions": "This video has no captions, sir.",
    "bad_time": "I didn't catch the time, sir. Say it like one thirty.",
    "bad_speed": "What speed, sir?",
    "no_button": "I can't find the Skip button, sir.",
    "ignored": "I pressed Skip, sir, but YouTube didn't accept it, even with a real click.",
    "click_failed": "I couldn't make a real click, sir. The extension may be missing its debugging permission.",
}


def enable_auto_skip():
    """Press Skip on YouTube ads whenever YouTube allows it. Returns True if the browser extension answered."""
    global _auto_skip_on_at
    try:
        bridge.request("yt_auto_skip", on=True)
    except Exception:
        return False
    _auto_skip_on_at = time.monotonic()
    return True


def _seconds(text, default=None):
    """'30', '1:30', '1:02:03', '2 minutes', '90 seconds' -> seconds."""
    text = (text or "").strip().lower()
    clock = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", text)
    if clock:
        parts = [int(p) for p in clock.groups() if p is not None]
        total = 0
        for part in parts:
            total = total * 60 + part
        return float(total)
    number = re.search(r"\d+(?:\.\d+)?", text)
    if not number:
        return default
    value = float(number.group())
    if re.search(r"\bh(our|r)s?\b", text):
        return value * 3600
    if re.search(r"\bm(in|inute)s?\b", text):
        return value * 60
    return value


def _say_seconds(total):
    total = int(round(total))
    minutes, seconds = divmod(total, 60)
    if minutes and seconds:
        return f"{minutes} minute{'s' if minutes != 1 else ''} {seconds} seconds"
    if minutes:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} second{'s' if seconds != 1 else ''}"


def _reply(command, r):
    if not r.get("ok"):
        return _FAILURES.get(r.get("reason"), "YouTube didn't answer that, sir.")
    if command in ("forward", "back"):
        way = "ahead" if command == "forward" else "back"
        return f"Skipped {way} {_say_seconds(r.get('seconds', 10))}, sir."
    if command == "go_to":
        return f"Jumped to {r.get('at', '')}, sir."
    if command == "next":
        return "Next video, sir."
    if command == "previous":
        return "Back to the start, sir." if r.get("detail") == "restart" else "Previous video, sir."
    if command == "skip_ad":
        if r.get("detail") == "waiting":
            return "I'll skip it the moment YouTube allows, sir."
        return "Ad skipped, sir."
    if command in ("speed", "faster", "slower"):
        return f"Speed set to {r.get('rate', 1):g} times, sir."
    if command == "captions":
        return "Captions on, sir." if r.get("on") else "Captions off, sir."
    return "Done, sir."


def youtube_control(action, amount=""):
    action = (action or "").strip().lower()
    try:
        if action == "auto_skip_on":
            if not enable_auto_skip():
                return OFFLINE
            return "I'll press Skip on ads whenever YouTube allows it, sir."
        if action == "auto_skip_off":
            bridge.request("yt_auto_skip", on=False)
            return "Auto skip is off, sir."

        if action in ("forward", "back"):
            reply = bridge.request("youtube", command=action, amount=_seconds(amount, 10))
        elif action == "go_to":
            seconds = _seconds(amount)
            if seconds is None:
                return _FAILURES["bad_time"]
            reply = bridge.request("youtube", command="go_to", amount=seconds)
        elif action == "skip_ad":
            reply = bridge.request("youtube", command="skip_ad", amount="")
            if reply.get("reason") in ("no_tab", "no_ad"):
                # nothing to skip yet (like in "play something and skip ads"): skip the ones that come next
                if time.monotonic() - _auto_skip_on_at < 30:
                    return "Ads will be skipped as they appear, sir."
                if not enable_auto_skip():
                    return OFFLINE
                return "No ad is playing right now, sir, so I'll skip the next ones as they appear. Say turn off auto skip to stop."
        elif action in ("next", "previous", "faster", "slower"):
            reply = bridge.request("youtube", command=action, amount="")
        elif action == "speed":
            reply = bridge.request("youtube", command="speed", amount=_seconds(amount))
            if reply.get("reason") == "bad_speed":
                return _FAILURES["bad_speed"]
        elif action == "captions":
            want = (amount or "").strip().lower()
            reply = bridge.request("youtube", command="captions", amount=want if want in ("on", "off") else "")
        else:
            return "I don't know that YouTube command, sir."
    except Exception:
        return OFFLINE
    if reply.get("error"):
        return f"I couldn't do that, sir: {reply['error']}"
    return _reply(action, reply)


HINT = ("YouTube: when a YouTube video is playing, use youtube_control to skip ahead or back by some seconds, "
        "jump to a time, go to the next or previous video, change the speed, or turn captions on or off. "
        "To skip ads use action skip_ad: it skips an ad that is playing, or switches on auto skip so the next ads are "
        "skipped as they appear. When the user says 'play something and skip ads', just call play_youtube, it handles the ads. "
        "Use media_control only to pause, resume, or change song, and for Spotify.")

SCHEMAS = [
    tool("youtube_control",
         "Control the YouTube video that is playing: skip ahead or back, jump to a time, next or previous video, "
         "skip an ad (skips a playing ad, or switches on auto skip), auto skip ads on or off, playback speed, or captions.",
         {"action": "forward, back, go_to, next, previous, skip_ad, auto_skip_on, auto_skip_off, speed, faster, slower or captions",
          "amount": "forward and back: how many seconds, default 10 (60 for a minute). go_to: a time like 1:30. "
                    "speed: the speed, like 1.5. captions: on or off. Leave empty otherwise."},
         optional=("amount",),
         enums={"action": ["forward", "back", "go_to", "next", "previous", "skip_ad", "auto_skip_on",
                           "auto_skip_off", "speed", "faster", "slower", "captions"]}),
]

TOOLS = {
    "youtube_control": youtube_control,
}