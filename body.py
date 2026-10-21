import json
import math
import queue
import random
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

W = 340                   # width of the expanded panel
CY_EXP = 170              # height of the neuron cloud's center in the expanded panel
RADIUS_EXP = 90
NODES = 46
LINK_DIST = 0.45
FRAME_MS = 33             # about 30 fps

PAD = 24                  # left/right padding of the text
TEXT_TOP = 312            # where the message area starts
MAX_USER_LINES = 4        # your message is shortened after this many lines
MAX_REPLY_LINES = 12      # Jarvis's reply scrolls after this many lines

USER_CPS = 70             # typing speed of your message, characters per second
DEFAULT_CPS = 45          # typing speed when no voice length is known

BUBBLE_W = 270            # width of the thought bubble next to the orb
BUBBLE_GAP = 8            # space between the orb and the bubble
BUBBLE_LINES = 4          # the bubble shows the newest lines of what he is saying

BG = (7, 11, 16)
KEY = "#010203"           # this exact color becomes fully transparent (the corners)

SETTINGS_FILE = Path(__file__).resolve().parent / "data" / "body_settings.json"
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 30, 30, 80     # distance from the screen edges (bottom clears the taskbar)
ICON_MIN = (W - 30, 22)   # where the minimize icon sits on the expanded panel
ICON_SET = (W - 58, 22)   # and the settings icon

STATES = {
    "idle":      dict(color=(45, 125, 155), rot=0.004, spawn=0.03, amp=0.01, freq=1.0),
    "listening": dict(color=(0, 229, 255), rot=0.008, spawn=0.12, amp=0.04, freq=2.5),
    "thinking":  dict(color=(255, 179, 0), rot=0.030, spawn=0.55, amp=0.02, freq=5.0),
    "speaking":  dict(color=(124, 255, 178), rot=0.012, spawn=0.30, amp=0.07, freq=6.0),
    "ignored":   dict(color=(176, 84, 96), rot=0.003, spawn=0.02, amp=0.00, freq=1.0),
    "error":     dict(color=(255, 72, 72), rot=0.020, spawn=0.40, amp=0.05, freq=5.0),
}

# A color theme only changes the calm states. Thinking, ignored and error keep their meaning.
THEMES = {
    "arc":     {"idle": (45, 125, 155), "listening": (0, 229, 255), "speaking": (124, 255, 178)},
    "emerald": {"idle": (40, 140, 95), "listening": (60, 235, 140), "speaking": (190, 255, 120)},
    "violet":  {"idle": (120, 95, 185), "listening": (190, 140, 255), "speaking": (255, 150, 230)},
    "silver":  {"idle": (125, 135, 145), "listening": (235, 240, 245), "speaking": (255, 255, 255)},
}

DEFAULTS = {
    "alpha": 0.85,              # 1.0 = solid, lower = more see-through
    "corner": "bottom_right",   # or "custom" once you drag the panel somewhere
    "x": None, "y": None,       # where "custom" puts it
    "orb_size": 96,
    "theme": "arc",
    "start_minimized": False,
    "talk_mode": "bubble",      # what happens when he talks while minimized: bubble, open or none
    "bubble_seconds": 4,        # how long the bubble stays after he finishes talking
    "tuck_seconds": 20,         # a panel that opened by itself shrinks back after this long (0 = never)
    "always_on_top": True,
}

CORNER_LABELS = {"bottom_right": "Bottom right", "bottom_left": "Bottom left",
                 "top_right": "Top right", "top_left": "Top left"}
SIZE_LABELS = {72: "Small", 96: "Medium", 130: "Large"}
THEME_LABELS = {"arc": "Arc blue", "emerald": "Emerald", "violet": "Violet", "silver": "Silver"}
TALK_LABELS = {"bubble": "Show a thought bubble", "open": "Open the panel", "none": "Do nothing"}


def load_settings():
    s = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        for key in DEFAULTS:
            if key in data:
                s[key] = data[key]
        s["alpha"] = max(0.4, min(1.0, float(s["alpha"])))
        s["tuck_seconds"] = max(0, min(120, int(s["tuck_seconds"])))
        s["bubble_seconds"] = max(1, min(30, int(s["bubble_seconds"])))
        s["orb_size"] = int(s["orb_size"])
    except (OSError, ValueError, TypeError, AttributeError):
        s = dict(DEFAULTS)
    if s["orb_size"] not in SIZE_LABELS:
        s["orb_size"] = DEFAULTS["orb_size"]
    if s["theme"] not in THEMES:
        s["theme"] = DEFAULTS["theme"]
    if s["talk_mode"] not in TALK_LABELS:
        s["talk_mode"] = DEFAULTS["talk_mode"]
    if s["corner"] not in CORNER_LABELS and s["corner"] != "custom":
        s["corner"] = DEFAULTS["corner"]
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.parent.mkdir(exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except OSError:
        pass


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def hexc(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in c)


def round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def wrap(text, font, width):
    """Split text into lines that fit inside `width` pixels."""
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if font.measure(trial) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        while font.measure(word) > width and len(word) > 1:   # a single very long word
            cut = len(word)
            while cut > 1 and font.measure(word[:cut]) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    lines.append(line)
    return lines


def visible_lines(lines, count):
    """The lines as they look after `count` characters have been typed."""
    out, remaining = [], count
    for line in lines:
        if remaining <= 0:
            break
        out.append(line[:remaining])
        remaining -= len(line) + 1
    return out or [""]


class Typer:
    """Reveals a text gradually, like typing."""

    def __init__(self):
        self.text = ""
        self.pos = 0.0
        self.cps = DEFAULT_CPS

    def set(self, text, cps):
        self.text = text
        self.pos = 0.0
        self.cps = cps

    def step(self, dt):
        if self.pos < len(self.text):
            self.pos = min(len(self.text), self.pos + self.cps * dt)

    @property
    def typing(self):
        return self.pos < len(self.text)

    @property
    def count(self):
        return int(self.pos)


class Body:
    def __init__(self):
        self.s = load_settings()
        self.state = "idle"
        self.flash_state = None           # a state that fades back to idle by itself (ignored, error)
        self.flash_until = 0.0
        self.user = Typer()
        self.jarvis = Typer()
        self.scroll = 0
        self.frame = 0
        self._q = queue.SimpleQueue()     # messages from the Jarvis thread arrive here
        self._user_src = None
        self._user_all = [""]
        self._jarvis_src = None
        self._jarvis_all = [""]
        self._bubble_src = None
        self._bubble_all = [""]
        self.on_stop = None               # set by jarvis.py: called when you double-click the panel

        self.minimized = bool(self.s["start_minimized"])
        self.morph = 1.0 if self.minimized else 0.0     # 0 = full panel, 1 = orb
        self._auto_opened = False         # True when the panel opened by itself
        self._pop_blocked = False         # you minimized it while he was talking: don't open again
        self.bubble_until = 0.0
        self._bubble_dismissed = False
        self.last_active = time.monotonic()
        self._toggled_at = 0.0
        self._settings_win = None
        self._start = (0, 0)
        self._moved = False
        self._dx = self._dy = 0

        first = self.cfg("idle")
        self.color = first["color"]
        self.rot = first["rot"]
        self.spawn = first["spawn"]
        self.amp = first["amp"]
        self.freq = first["freq"]
        self.phase = 0.0
        self.ay = 0.0
        self.ax = 0.0

        random.seed(7)
        self.pts = []
        while len(self.pts) < NODES:
            p = [random.uniform(-1, 1) for _ in range(3)]
            if sum(v * v for v in p) <= 1:
                self.pts.append(p)
        self.edges = []
        self.adj = [[] for _ in range(NODES)]
        for i in range(NODES):
            for j in range(i + 1, NODES):
                if math.dist(self.pts[i], self.pts[j]) < LINK_DIST:
                    self.adj[i].append(len(self.edges))
                    self.adj[j].append(len(self.edges))
                    self.edges.append((i, j))
        self.pulses = []

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.s["always_on_top"]))
        self.root.attributes("-transparentcolor", KEY)
        self.root.attributes("-alpha", self.s["alpha"])
        self.root.configure(bg=KEY)

        self.font = tkfont.Font(family="Consolas", size=10)
        self.font_small = tkfont.Font(family="Consolas", size=8, weight="bold")
        self.lh = self.font.metrics("linespace")

        self.sw, self.sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        orb = self.s["orb_size"]
        self.cur_w, self.cur_h = (orb, orb) if self.minimized else (W, 440)
        self.px, self.py = self._start_position()
        self._update_dock()
        self.root.geometry(f"{self.cur_w}x{self.cur_h}+{self.px}+{self.py}")
        self.canvas = tk.Canvas(self.root, width=self.cur_w, height=self.cur_h, bg=KEY, highlightthickness=0)
        self.canvas.pack()

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double)       # double-click = stop
        self.canvas.bind("<Button-3>", self._menu)                 # right-click = menu
        self.canvas.bind("<MouseWheel>", self._wheel)              # scroll long replies
        self.canvas.bind("<Motion>", self._hover)

    # ------------------------------------------------------- settings helpers

    def cfg(self, state):
        """The look of a state, with the chosen color theme applied."""
        c = dict(STATES[state])
        theme = THEMES[self.s["theme"]]
        if state in theme:
            c["color"] = theme[state]
        return c

    def _save(self):
        save_settings(self.s)

    def _apply_window_settings(self):
        self.root.attributes("-alpha", self.s["alpha"])
        self.root.attributes("-topmost", bool(self.s["always_on_top"]))

    def _corner_xy(self, corner, w, h):
        x = MARGIN_X if corner.endswith("left") else self.sw - w - MARGIN_X
        y = MARGIN_TOP if corner.startswith("top") else self.sh - h - MARGIN_BOTTOM
        return max(0, x), max(0, y)

    def _start_position(self):
        x, y = self.s.get("x"), self.s.get("y")
        if self.s["corner"] == "custom" and isinstance(x, int) and isinstance(y, int):
            return max(0, min(x, self.sw - self.cur_w)), max(0, min(y, self.sh - self.cur_h))
        corner = self.s["corner"] if self.s["corner"] in CORNER_LABELS else "bottom_right"
        return self._corner_xy(corner, self.cur_w, self.cur_h)

    def _update_dock(self):
        """Which screen corner he is nearest to. The window grows away from that corner, so the orb stays put."""
        self.dock_left = self.px + self.cur_w / 2 < self.sw / 2
        self.dock_top = self.py + self.cur_h / 2 < self.sh / 2

    def _snap(self, corner):
        """Move to a screen corner and remember it."""
        self.px, self.py = self._corner_xy(corner, self.cur_w, self.cur_h)
        self.s["corner"] = corner
        self.s["x"] = self.s["y"] = None
        self._update_dock()
        self.root.geometry(f"+{self.px}+{self.py}")
        self._save()

    def _remember_position(self):
        self.s["corner"] = "custom"
        self.s["x"], self.s["y"] = self.px, self.py
        self._save()

    # --------------------------------------------------------------- sizing

    @staticmethod
    def _ease(diff):
        return diff if abs(diff) <= 3 else int(diff * 0.35)

    def _apply_size(self, new_w, new_h):
        """Resize the window, keeping the edges nearest the docked corner where they are."""
        new_w, new_h = int(round(new_w)), int(round(new_h))
        if (new_w, new_h) == (self.cur_w, self.cur_h):
            return
        x = self.px if self.dock_left else self.px + self.cur_w - new_w
        y = self.py if self.dock_top else self.py + self.cur_h - new_h
        self.px = max(0, min(x, self.sw - new_w))
        self.py = max(0, min(y, self.sh - new_h))
        self.cur_w, self.cur_h = new_w, new_h
        self.root.geometry(f"{new_w}x{new_h}+{self.px}+{self.py}")
        self.canvas.config(width=new_w, height=new_h)

    def _set_minimized(self, flag, by_user=True):
        if flag == self.minimized:
            return
        self.minimized = flag
        self._toggled_at = time.monotonic()
        self.last_active = self._toggled_at
        self._auto_opened = (not flag) and (not by_user)
        if flag and by_user:
            self._pop_blocked = True

    # ------------------------------------------------------------------ mouse

    def _press(self, e):
        self._dx = e.x_root - self.px
        self._dy = e.y_root - self.py
        self._start = (e.x_root, e.y_root)
        self._moved = False
        if self.minimized:
            self._bubble_dismissed = True        # touching the orb hides the bubble

    def _drag(self, e):
        if abs(e.x_root - self._start[0]) + abs(e.y_root - self._start[1]) > 4:
            self._moved = True
        if self._moved:
            self.px = e.x_root - self._dx
            self.py = e.y_root - self._dy
            self.root.geometry(f"+{self.px}+{self.py}")

    def _release(self, e):
        if self._moved:
            self._update_dock()
            self._remember_position()
        else:
            self._click(e.x, e.y)

    def _click(self, x, y):
        if time.monotonic() - self._toggled_at < 0.35:      # ignore the second half of a quick double click
            return
        if self.minimized:
            self._set_minimized(False)                       # the orb or the bubble: open the panel
        elif math.hypot(x - ICON_MIN[0], y - ICON_MIN[1]) <= 12:
            self._set_minimized(True)
        elif math.hypot(x - ICON_SET[0], y - ICON_SET[1]) <= 12:
            self.open_settings()

    def _double(self, e):
        if self.minimized or time.monotonic() - self._toggled_at < 0.6:
            return
        if self.on_stop:
            self.on_stop()

    def _hover(self, e):
        on_icon = (not self.minimized and
                   (math.hypot(e.x - ICON_MIN[0], e.y - ICON_MIN[1]) <= 12 or
                    math.hypot(e.x - ICON_SET[0], e.y - ICON_SET[1]) <= 12))
        self.canvas.config(cursor="hand2" if (self.minimized or on_icon) else "")

    def _menu(self, e):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Expand" if self.minimized else "Minimize",
                         command=lambda: self._set_minimized(not self.minimized))
        menu.add_command(label="Settings...", command=self.open_settings)
        menu.add_command(label="Stop him", command=lambda: self.on_stop and self.on_stop())
        menu.add_separator()
        menu.add_command(label="Quit Jarvis", command=self._quit)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def _wheel(self, e):
        self.scroll += -3 if e.delta > 0 else 3

    def _quit(self):
        self.root.destroy()

    # Setters, safe to call from other threads (the panel applies them on its own thread)
    def set_state(self, state):
        if state in STATES:
            self.state = state

    def flash(self, state, seconds=2.0):
        """Show a state for a few seconds, then go back to idle by itself."""
        self._q.put(("flash", state, seconds))

    def set_user(self, text):
        self._q.put(("user", " ".join(text.split()), USER_CPS))

    def set_jarvis(self, text, duration=None):
        """duration = seconds of audio. The typing is paced to finish just before it ends."""
        text = " ".join(text.split())
        if duration and text:
            cps = max(15.0, min(120.0, len(text) / max(0.5, duration * 0.85)))
        else:
            cps = DEFAULT_CPS
        self._q.put(("jarvis", text, cps))

    def panel(self, action):
        """'minimize', 'expand', 'toggle' or 'settings'."""
        self._q.put(("panel", action, 0))

    def minimize(self):
        self.panel("minimize")

    def expand(self):
        self.panel("expand")

    def _drain(self):
        while True:
            try:
                kind, text, value = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "user":
                self.user.set(text, value)
            elif kind == "jarvis":
                self.jarvis.set(text, value)
                self.scroll = 0
            elif kind == "flash" and text in STATES:
                self.state = text
                self.flash_state = text
                self.flash_until = time.monotonic() + value
            elif kind == "panel":
                if text == "minimize":
                    self._set_minimized(True)
                elif text == "expand":
                    self._set_minimized(False)
                elif text == "toggle":
                    self._set_minimized(not self.minimized)
                elif text == "settings":
                    self.open_settings()

    # ---------------------------------------------------------------- drawing

    def _project(self, scale, cx, cy, radius):
        cos_y, sin_y = math.cos(self.ay), math.sin(self.ay)
        cos_x, sin_x = math.cos(self.ax), math.sin(self.ax)
        out = []
        for x, y, z in self.pts:
            x, z = x * cos_y + z * sin_y, -x * sin_y + z * cos_y
            y, z = y * cos_x - z * sin_x, y * sin_x + z * cos_x
            p = 1 / (1 - z * 0.35)
            out.append((cx + x * radius * scale * p, cy + y * radius * scale * p, z))
        return out

    def _layout(self):
        """Wrap the texts, work out what is visible so far and how tall the expanded panel should be."""
        width = W - 2 * PAD
        if self.user.text != self._user_src:
            self._user_src = self.user.text
            lines = wrap(self.user.text, self.font, width) if self.user.text else [""]
            if len(lines) > MAX_USER_LINES:
                lines = lines[:MAX_USER_LINES]
                lines[-1] = lines[-1][:-3].rstrip() + "..."
            self._user_all = lines
        if self.jarvis.text != self._jarvis_src:
            self._jarvis_src = self.jarvis.text
            self._jarvis_all = wrap(self.jarvis.text, self.font, width) if self.jarvis.text else [""]
            self._bubble_src = self.jarvis.text
            self._bubble_all = wrap(self.jarvis.text, self.font, BUBBLE_W - 28) if self.jarvis.text else [""]

        user_lines = visible_lines(self._user_all, self.user.count) if self.user.text else [""]
        reply_lines = visible_lines(self._jarvis_all, self.jarvis.count) if self.jarvis.text else [""]

        max_scroll = max(0, len(reply_lines) - MAX_REPLY_LINES)
        if self.jarvis.typing:
            self.scroll = max_scroll              # follow the newest line while it types
        self.scroll = max(0, min(self.scroll, max_scroll))
        shown = reply_lines[self.scroll:self.scroll + MAX_REPLY_LINES]

        lh = self.lh
        y_user = TEXT_TOP + 16
        y_jlabel = y_user + len(user_lines) * lh + 12
        y_jarvis = y_jlabel + 16
        target = y_jarvis + len(shown) * lh + (16 if max_scroll else 0) + 22
        return y_user, y_jlabel, y_jarvis, user_lines, shown, max_scroll, target

    def _draw_icons(self, c):
        color = hexc(mix(BG, self.color, 0.6))
        x, y = ICON_MIN
        c.create_line(x - 6, y, x + 6, y, fill=color, width=2)               # minimize: a dash
        gx, gy = ICON_SET
        c.create_oval(gx - 3.5, gy - 3.5, gx + 3.5, gy + 3.5, outline=color, width=2)
        for i in range(8):                                                    # settings: a little gear
            a = i * math.pi / 4
            c.create_line(gx + 5.5 * math.cos(a), gy + 5.5 * math.sin(a),
                          gx + 8 * math.cos(a), gy + 8 * math.sin(a), fill=color, width=2)

    def _draw_bubble(self, c, w, h, orb, orb_y, lines, bubble_h):
        """The thought bubble beside the orb, on the side facing the middle of the screen."""
        avail = w - orb - BUBBLE_GAP
        if avail < 60:
            return
        bh = min(bubble_h, h)
        bx = orb + BUBBLE_GAP if self.dock_left else 0
        by = 0 if self.dock_top else h - bh
        accent = hexc(mix(BG, self.color, 0.55))
        round_rect(c, bx + 2, by + 2, bx + avail - 2, by + bh - 2, 14, fill=hexc(BG), outline=accent, width=2)

        ty = max(by + 16, min(orb_y, by + bh - 16))            # a little tail pointing at the orb
        if self.dock_left:
            base, tip = bx + 2, bx - BUBBLE_GAP + 2
        else:
            base, tip = bx + avail - 2, bx + avail + BUBBLE_GAP - 2
        c.create_polygon(base, ty - 6, tip, ty, base, ty + 6, fill=hexc(BG), outline=accent, width=2)

        if avail > BUBBLE_W * 0.85:                            # text appears once the bubble has grown
            color = hexc(mix(self.color, (255, 255, 255), 0.35))
            for i, line in enumerate(lines):
                c.create_text(bx + 14, by + 10 + i * self.lh, text=line, anchor="nw", fill=color, font=self.font)

    def _tick(self):
        self.frame += 1
        self._drain()
        now = time.monotonic()

        # A flashed state (ignored, error) returns to idle by itself, unless something else took over
        if self.flash_state and now >= self.flash_until:
            if self.state == self.flash_state:
                self.state = "idle"
            self.flash_state = None

        self.user.step(FRAME_MS / 1000)
        self.jarvis.step(FRAME_MS / 1000)

        # What happens when he talks while minimized
        talking = self.state in ("speaking", "error")
        busy = self.state != "idle" or self.user.typing or self.jarvis.typing
        if busy:
            self.last_active = now
        if self.state == "idle":
            self._pop_blocked = False
            self._bubble_dismissed = False
        if talking and self.jarvis.text:
            self.bubble_until = now + self.s["bubble_seconds"]       # keeps it open while he talks, then lingers
        if self.minimized and talking and self.s["talk_mode"] == "open" and not self._pop_blocked:
            self._set_minimized(False, by_user=False)
        elif (not self.minimized and self._auto_opened and self.s["tuck_seconds"] > 0
                and now - self.last_active > self.s["tuck_seconds"]):
            self._set_minimized(True, by_user=False)
        bubble_on = (self.minimized and self.s["talk_mode"] == "bubble" and bool(self.jarvis.text)
                     and now < self.bubble_until and not self._bubble_dismissed)

        look = self.cfg(self.state)
        k = 0.08
        self.color = tuple(c + (t - c) * k for c, t in zip(self.color, look["color"]))
        self.rot += (look["rot"] - self.rot) * k
        self.spawn += (look["spawn"] - self.spawn) * k
        self.amp += (look["amp"] - self.amp) * k
        self.freq += (look["freq"] - self.freq) * k
        self.phase += self.freq * FRAME_MS / 1000
        self.ay += self.rot
        self.ax += self.rot * 0.5

        y_user, y_jlabel, y_jarvis, user_lines, shown, max_scroll, panel_h = self._layout()
        bubble_lines = visible_lines(self._bubble_all, self.jarvis.count)[-BUBBLE_LINES:]
        bubble_h = len(bubble_lines) * self.lh + 20

        # Window size: the full panel, the orb alone, or the orb with its bubble
        orb = self.s["orb_size"]
        if self.minimized:
            want_w, want_h = (orb + BUBBLE_GAP + BUBBLE_W, max(orb, bubble_h)) if bubble_on else (orb, orb)
        else:
            want_w, want_h = W, panel_h
        self._apply_size(self.cur_w + self._ease(want_w - self.cur_w),
                         self.cur_h + self._ease(want_h - self.cur_h))
        w, h = self.cur_w, self.cur_h

        # Morph the shape between the panel and the orb
        goal = 1.0 if self.minimized else 0.0
        self.morph = goal if abs(goal - self.morph) < 0.04 else self.morph + (goal - self.morph) * 0.35
        m = self.morph

        ox = orb / 2 if self.dock_left else w - orb / 2          # where the orb sits inside the window
        oy = orb / 2 if self.dock_top else h - orb / 2
        cx = w / 2 + (ox - w / 2) * m
        cy = CY_EXP + (oy - CY_EXP) * m
        radius = RADIUS_EXP + (orb * 0.33 - RADIUS_EXP) * m
        size = 1 - 0.4 * m                                      # neurons and signals get smaller in the orb

        scale = 1 + self.amp * math.sin(self.phase)
        pts = self._project(scale, cx, cy, radius)
        c = self.canvas
        c.delete("all")

        # Panel or orb
        outline = hexc(mix(BG, self.color, 0.35 + 0.25 * m))
        x1 = 4 + (ox - orb / 2 + 3 - 4) * m
        y1 = 4 + (oy - orb / 2 + 3 - 4) * m
        x2 = (w - 4) + (ox + orb / 2 - 3 - (w - 4)) * m
        y2 = (h - 4) + (oy + orb / 2 - 3 - (h - 4)) * m
        if m > 0.97:
            c.create_oval(x1, y1, x2, y2, fill=hexc(BG), outline=outline, width=3)
        else:
            corner = min(28 + (orb / 2 - 3 - 28) * m, (x2 - x1) / 2, (y2 - y1) / 2)
            round_rect(c, x1, y1, x2, y2, corner, fill=hexc(BG), outline=outline, width=2 + m)

        # Links
        for a, b in self.edges:
            xa, ya, za = pts[a]
            xb, yb, zb = pts[b]
            depth = (za + zb) / 4 + 0.5
            c.create_line(xa, ya, xb, yb, fill=hexc(mix(BG, self.color, 0.12 + 0.30 * depth)))

        # Neurons
        for x, y, z in pts:
            depth = (z + 1) / 2
            r = (2 + 2.5 * depth) * size
            c.create_oval(x - r, y - r, x + r, y + r,
                          fill=hexc(mix(BG, self.color, 0.45 + 0.55 * depth)), outline="")

        # Signals travelling along links
        if random.random() < self.spawn and len(self.pulses) < 60:
            e = random.randrange(len(self.edges))
            self.pulses.append([e, random.choice(self.edges[e]), 0.0])
        speed = 0.03 + self.spawn * 0.1
        alive = []
        for p in self.pulses:
            e, frm, t = p
            a, b = self.edges[e]
            to = b if frm == a else a
            t += speed
            if t >= 1:
                options = [x for x in self.adj[to] if x != e]
                if options and random.random() < 0.7 and len(self.pulses) < 60:
                    alive.append([random.choice(options), to, 0.0])
                continue
            xa, ya, _ = pts[frm]
            xb, yb, _ = pts[to]
            x = xa + (xb - xa) * t
            y = ya + (yb - ya) * t
            dot = 3 * size
            c.create_oval(x - dot, y - dot, x + dot, y + dot,
                          fill=hexc(mix(self.color, (255, 255, 255), 0.6)), outline="")
            p[2] = t
            alive.append(p)
        self.pulses = alive

        # The thought bubble, next to the orb
        if bubble_on and m > 0.9:
            self._draw_bubble(c, w, h, orb, oy, bubble_lines, bubble_h)

        # Everything below only exists on the full panel
        if m < 0.2:
            self._draw_icons(c)
            c.create_text(w / 2, 282, text=self.state.upper(),
                          fill=hexc(mix(BG, self.color, 0.8)), font=("Consolas", 9, "bold"))
            c.create_line(24, 300, W - 24, 300, fill=hexc(mix(BG, self.color, 0.25)))

            dim = hexc(mix(BG, self.color, 0.55))
            faint = hexc(mix(BG, self.color, 0.3))
            blink = (self.frame // 8) % 2 == 0
            cursor = "\u258c" if blink else " "

            c.create_text(PAD, TEXT_TOP, text="YOU", anchor="nw", fill=dim, font=self.font_small)
            if self.user.text:
                lines = list(user_lines)
                if self.user.typing:
                    lines[-1] += cursor
                for i, line in enumerate(lines):
                    c.create_text(PAD, y_user + i * self.lh, text=line, anchor="nw",
                                  fill=hexc((205, 218, 228)), font=self.font)
            else:
                c.create_text(PAD, y_user, text="...", anchor="nw", fill=faint, font=self.font)

            c.create_text(PAD, y_jlabel, text="JARVIS", anchor="nw", fill=dim, font=self.font_small)
            if self.jarvis.text:
                lines = list(shown)
                if self.jarvis.typing and self.scroll >= max_scroll:
                    lines[-1] += cursor
                reply_color = hexc(mix(self.color, (255, 255, 255), 0.35))
                for i, line in enumerate(lines):
                    c.create_text(PAD, y_jarvis + i * self.lh, text=line, anchor="nw",
                                  fill=reply_color, font=self.font)
            else:
                c.create_text(PAD, y_jarvis, text="...", anchor="nw", fill=faint, font=self.font)

            if max_scroll:
                hint = "scroll for more" if self.scroll < max_scroll else "end of reply"
                c.create_text(W - PAD, h - 20, text=hint, anchor="ne", fill=dim, font=self.font_small)

        self.root.after(FRAME_MS, self._tick)

    # --------------------------------------------------------------- settings

    def open_settings(self):
        if self._settings_win is not None:
            try:
                if self._settings_win.winfo_exists():
                    self._settings_win.lift()
                    return
            except tk.TclError:
                pass

        bg, fg, field, dim = "#0b1218", "#cfe3ee", "#16222c", "#7f98a8"
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title("Jarvis panel settings")
        win.configure(bg=bg)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.geometry(f"+{max(0, self.sw // 2 - 200)}+{max(0, self.sh // 2 - 280)}")

        tk.Label(win, text="Panel settings", bg=bg, fg=fg, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 0))

        def caption(row, text):
            tk.Label(win, text=text, bg=bg, fg=fg, font=("Segoe UI", 10), anchor="w").grid(
                row=row, column=0, sticky="w", padx=(16, 10), pady=(12, 0))

        def slider(row, text, low, high, current, on_change):
            caption(row, text)
            scale = tk.Scale(win, from_=low, to=high, orient="horizontal", length=190, bg=bg, fg=fg,
                             troughcolor=field, highlightthickness=0, bd=0, activebackground=dim)
            scale.set(current)                                   # set first, so it doesn't count as a change
            scale.config(command=lambda value: on_change(int(float(value))))
            scale.grid(row=row, column=1, sticky="w", padx=(0, 16), pady=(4, 0))

        def choice(row, text, labels, current, on_pick):
            caption(row, text)
            var = tk.StringVar(value=labels.get(current, "Custom (dragged)"))
            reverse = {label: key for key, label in labels.items()}
            menu = tk.OptionMenu(win, var, *labels.values(), command=lambda label: on_pick(reverse[label]))
            menu.config(bg=field, fg=fg, activebackground=dim, activeforeground=fg,
                        highlightthickness=0, bd=0, width=20, anchor="w")
            menu["menu"].config(bg=field, fg=fg, activebackground=dim, activeforeground=fg)
            menu.grid(row=row, column=1, sticky="w", padx=(0, 16), pady=(10, 0))

        def check(row, text, key):
            var = tk.BooleanVar(value=bool(self.s[key]))

            def toggled():
                self.s[key] = var.get()
                self._apply_window_settings()
                self._save()

            tk.Checkbutton(win, text=text, variable=var, command=toggled, bg=bg, fg=fg, selectcolor=field,
                           activebackground=bg, activeforeground=fg, highlightthickness=0, bd=0,
                           font=("Segoe UI", 10), anchor="w").grid(
                row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(10, 0))

        def set_alpha(percent):
            self.s["alpha"] = percent / 100
            self._apply_window_settings()
            self._save()

        def set_value(key):
            def apply(value):
                self.s[key] = value
                self._save()
            return apply

        slider(1, "Transparency", 40, 100, int(round(self.s["alpha"] * 100)), set_alpha)
        choice(2, "Position", CORNER_LABELS, self.s["corner"], self._snap)
        choice(3, "Orb size", SIZE_LABELS, self.s["orb_size"], set_value("orb_size"))
        choice(4, "Color theme", THEME_LABELS, self.s["theme"], set_value("theme"))
        choice(5, "When he talks while minimized", TALK_LABELS, self.s["talk_mode"], set_value("talk_mode"))
        slider(6, "Keep the bubble for (seconds)", 1, 15, self.s["bubble_seconds"], set_value("bubble_seconds"))
        slider(7, "Tuck an opened panel away after (s, 0 = never)", 0, 120, self.s["tuck_seconds"], set_value("tuck_seconds"))
        check(8, "Start as a small orb", "start_minimized")
        check(9, "Always on top", "always_on_top")

        buttons = tk.Frame(win, bg=bg)
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", padx=16, pady=16)

        def reset():
            self.s = dict(DEFAULTS)
            self._apply_window_settings()
            self._snap(self.s["corner"])
            win.destroy()
            self.open_settings()

        style = dict(bg=field, fg=fg, activebackground=dim, activeforeground=fg, bd=0, padx=14, pady=4)
        tk.Button(buttons, text="Reset", command=reset, **style).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Close", command=win.destroy, **style).pack(side="left")

    def run(self):
        self._tick()
        self.root.mainloop()


if __name__ == "__main__":
    body = Body()
    long_reply = (
        "I can't provide you with the current time as I don't have real-time capabilities. "
        "You might want to check your device or a reliable website for that information. "
        "How else can I assist you today, sir? I can open your apps, start your projects, "
        "and I can keep you company while you work. Just say the word and I will do my best "
        "to help with whatever you need, whenever you need it, for as long as the power stays on."
    )
    demo = [   # state, your text, Jarvis's text, seconds to wait, panel action
        ("idle", "", "", 3, None),
        ("listening", "", "", 3, None),
        ("thinking", "Jarvis, open docker desktop", "", 4, None),
        ("speaking", "Jarvis, open docker desktop", "Opening docker desktop, sir.", 5, None),
        ("idle", "", "", 4, "minimize"),
        ("speaking", "Jarvis, what time is it?", "It's 3:45 PM, sir.", 7, None),        # the bubble appears
        ("idle", "", "", 5, None),
        ("speaking", "Jarvis, open my project", "Opening Docker, then Ubuntu, then your project, sir. "
                                                  "All three should be ready in a moment.", 9, None),
        ("idle", "", "", 5, None),
        ("ignored", "what is the weather like", "Ignored: I didn't hear my name.", 4, "expand"),
        ("error", "Jarvis, open docker desktop", "I can't reach my brain, sir. Is LM Studio's server running?", 5, None),
        ("speaking", "Jarvis, what time is it?", long_reply, 16, None),
    ]

    def cycle(i=0):
        state, user, reply, wait, action = demo[i % len(demo)]
        body.set_state(state)
        body.set_user(user)
        body.set_jarvis(reply)
        if action:
            body.panel(action)
        body.root.after(wait * 1000, cycle, i + 1)

    cycle()
    body.run()