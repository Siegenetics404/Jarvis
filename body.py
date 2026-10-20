import math
import queue
import random
import time
import tkinter as tk
import tkinter.font as tkfont

W = 340
CX, CY = W / 2, 170       # center of the neuron cloud
RADIUS = 90
NODES = 46
LINK_DIST = 0.45
FRAME_MS = 33             # about 30 fps
ALPHA = 0.85              # 1.0 = solid, lower = more see-through

PAD = 24                  # left/right padding of the text
TEXT_TOP = 312            # where the message area starts
MAX_USER_LINES = 4        # your message is shortened after this many lines
MAX_REPLY_LINES = 12      # Jarvis's reply scrolls after this many lines

USER_CPS = 70             # typing speed of your message, characters per second
DEFAULT_CPS = 45          # typing speed when no voice length is known

BG = (7, 11, 16)
KEY = "#010203"           # this exact color becomes fully transparent (the corners)

STATES = {
    "idle":      dict(color=(45, 125, 155), rot=0.004, spawn=0.03, amp=0.01, freq=1.0),
    "listening": dict(color=(0, 229, 255), rot=0.008, spawn=0.12, amp=0.04, freq=2.5),
    "thinking":  dict(color=(255, 179, 0), rot=0.030, spawn=0.55, amp=0.02, freq=5.0),
    "speaking":  dict(color=(124, 255, 178), rot=0.012, spawn=0.30, amp=0.07, freq=6.0),
    "ignored":   dict(color=(176, 84, 96), rot=0.003, spawn=0.02, amp=0.00, freq=1.0),
    "error":     dict(color=(255, 72, 72), rot=0.020, spawn=0.40, amp=0.05, freq=5.0),
}


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

        cfg = STATES["idle"]
        self.color = cfg["color"]
        self.rot = cfg["rot"]
        self.spawn = cfg["spawn"]
        self.amp = cfg["amp"]
        self.freq = cfg["freq"]
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
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", KEY)
        self.root.attributes("-alpha", ALPHA)
        self.root.configure(bg=KEY)

        self.font = tkfont.Font(family="Consolas", size=10)
        self.font_small = tkfont.Font(family="Consolas", size=8, weight="bold")
        self.lh = self.font.metrics("linespace")

        self.H = 440
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.px = sw - W - 30
        self.py = sh - self.H - 80
        self.root.geometry(f"{W}x{self.H}+{self.px}+{self.py}")
        self.canvas = tk.Canvas(self.root, width=W, height=self.H, bg=KEY, highlightthickness=0)
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<Button-3>", lambda e: self.root.destroy())   # right-click to quit
        self.canvas.bind("<MouseWheel>", self._wheel)                    # scroll long replies
        self.on_stop = None                                              # set by jarvis.py
        self.canvas.bind("<Double-Button-1>", lambda e: self.on_stop and self.on_stop())   # double-click = stop

    def _drag_start(self, e):
        self._dx = e.x_root - self.px
        self._dy = e.y_root - self.py

    def _drag_move(self, e):
        self.px = e.x_root - self._dx
        self.py = e.y_root - self._dy
        self.root.geometry(f"+{self.px}+{self.py}")

    def _wheel(self, e):
        self.scroll += -3 if e.delta > 0 else 3

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

    def _project(self, scale):
        cy, sy = math.cos(self.ay), math.sin(self.ay)
        cx, sx = math.cos(self.ax), math.sin(self.ax)
        out = []
        for x, y, z in self.pts:
            x, z = x * cy + z * sy, -x * sy + z * cy
            y, z = y * cx - z * sx, y * sx + z * cx
            p = 1 / (1 - z * 0.35)
            out.append((CX + x * RADIUS * scale * p, CY + y * RADIUS * scale * p, z))
        return out

    def _layout(self):
        """Work out what is visible so far, how tall the panel should be, and ease toward it."""
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

        # Ease the panel height toward the target, anchored at the bottom edge
        diff = target - self.H
        if diff:
            step = min(abs(diff), max(1, int(abs(diff) * 0.35)))
            new_h = self.H + (step if diff > 0 else -step)
            bottom = self.py + self.H
            self.py = max(0, bottom - new_h)
            self.root.geometry(f"{W}x{new_h}+{self.px}+{self.py}")
            self.canvas.config(height=new_h)
            self.H = new_h

        return y_user, y_jlabel, y_jarvis, user_lines, shown, max_scroll

    def _tick(self):
        self.frame += 1
        self._drain()

        # A flashed state (ignored, error) returns to idle by itself, unless something else took over
        if self.flash_state and time.monotonic() >= self.flash_until:
            if self.state == self.flash_state:
                self.state = "idle"
            self.flash_state = None

        self.user.step(FRAME_MS / 1000)
        self.jarvis.step(FRAME_MS / 1000)

        cfg = STATES[self.state]
        k = 0.08
        self.color = tuple(c + (t - c) * k for c, t in zip(self.color, cfg["color"]))
        self.rot += (cfg["rot"] - self.rot) * k
        self.spawn += (cfg["spawn"] - self.spawn) * k
        self.amp += (cfg["amp"] - self.amp) * k
        self.freq += (cfg["freq"] - self.freq) * k
        self.phase += self.freq * FRAME_MS / 1000
        self.ay += self.rot
        self.ax += self.rot * 0.5

        y_user, y_jlabel, y_jarvis, user_lines, shown, max_scroll = self._layout()

        scale = 1 + self.amp * math.sin(self.phase)
        pts = self._project(scale)
        c = self.canvas
        c.delete("all")

        # Panel
        round_rect(c, 4, 4, W - 4, self.H - 4, 28, fill=hexc(BG),
                   outline=hexc(mix(BG, self.color, 0.35)), width=2)

        # Links
        for a, b in self.edges:
            x1, y1, z1 = pts[a]
            x2, y2, z2 = pts[b]
            depth = (z1 + z2) / 4 + 0.5
            c.create_line(x1, y1, x2, y2, fill=hexc(mix(BG, self.color, 0.12 + 0.30 * depth)))

        # Neurons
        for x, y, z in pts:
            depth = (z + 1) / 2
            r = 2 + 2.5 * depth
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
            x1, y1, _ = pts[frm]
            x2, y2, _ = pts[to]
            x = x1 + (x2 - x1) * t
            y = y1 + (y2 - y1) * t
            c.create_oval(x - 3, y - 3, x + 3, y + 3,
                          fill=hexc(mix(self.color, (255, 255, 255), 0.6)), outline="")
            p[2] = t
            alive.append(p)
        self.pulses = alive

        # State label and divider
        c.create_text(CX, 282, text=self.state.upper(),
                      fill=hexc(mix(BG, self.color, 0.8)), font=("Consolas", 9, "bold"))
        c.create_line(24, 300, W - 24, 300, fill=hexc(mix(BG, self.color, 0.25)))

        # Messages, with a blinking cursor while typing
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
            c.create_text(W - PAD, self.H - 20, text=hint, anchor="ne", fill=dim, font=self.font_small)

        self.root.after(FRAME_MS, self._tick)

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
    demo = [   # state, your text, Jarvis's text, seconds to wait
        ("idle", "", "", 3),
        ("listening", "", "", 3),
        ("thinking", "Jarvis, open docker desktop", "", 4),
        ("speaking", "Jarvis, open docker desktop", "Opening docker desktop, sir.", 5),
        ("ignored", "what is the weather like", "Ignored: I didn't hear my name.", 4),
        ("error", "Jarvis, open docker desktop", "I can't reach my brain, sir. Is LM Studio's server running?", 5),
        ("speaking", "Jarvis, what time is it?", long_reply, 16),
    ]

    def cycle(i=0):
        state, user, reply, wait = demo[i % len(demo)]
        body.set_state(state)
        body.set_user(user)
        body.set_jarvis(reply)
        body.root.after(wait * 1000, cycle, i + 1)

    cycle()
    body.run()