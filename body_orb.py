"""The glass orb body. It runs the Electron app in the body-ui folder and talks to it over a local WebSocket.
It has the same methods as the classic body.py, so jarvis.py can use either one."""
import asyncio
import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
from websockets.asyncio.server import serve

UI_DIR = Path(__file__).resolve().parent / "body-ui"
LOG_FILE = Path(__file__).resolve().parent / "data" / "body-ui.log"


class _VoiceLevel(threading.Thread):
    """While he speaks, tells the orb how loud the audio is right now, so the surface ripples with his voice."""

    def __init__(self, body, samples, rate):
        super().__init__(daemon=True)
        self.body = body
        self.rate = rate
        self.samples = np.asarray(samples, dtype="float32").reshape(-1)
        self.stopped = threading.Event()

    def run(self):
        count = len(self.samples)
        if count == 0:
            return
        window = int(self.rate * 0.05)                                    # 50 ms at a time
        peak = float(np.percentile(np.abs(self.samples), 99)) or 1.0      # the loudest bits count as "full"
        start = time.monotonic()
        while not self.stopped.is_set():
            pos = int((time.monotonic() - start) * self.rate)
            if pos >= count:
                break
            chunk = self.samples[pos:pos + window]
            level = float(np.sqrt(np.mean(chunk ** 2))) / peak if len(chunk) else 0.0
            self.body._send({"type": "level", "value": min(1.0, level * 1.6)})
            time.sleep(0.04)
        self.body._send({"type": "level", "value": 0.0})

    def stop(self):
        self.stopped.set()


class OrbBody:
    def __init__(self):
        exe = UI_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
        if not exe.exists():
            exe = UI_DIR / "node_modules" / ".bin" / "electron.cmd"
        if not exe.exists():
            raise RuntimeError("the orb isn't installed yet: run 'npm install' inside the body-ui folder")

        self.on_stop = None                  # set by jarvis.py: called when you say stop from the orb
        self._token = secrets.token_urlsafe(24)
        self._snapshot = {"state": "idle", "user": "", "jarvis": ""}     # so an orb that connects late catches up
        self._clients = set()
        self._loop = None
        self._outbox = None
        self._port = None
        self._error = None
        self._ready = threading.Event()
        threading.Thread(target=self._serve_thread, daemon=True).start()
        if not self._ready.wait(10) or self._port is None:
            raise RuntimeError(f"the orb's local connection didn't start ({self._error})")

        env = dict(os.environ)
        env.pop("ELECTRON_RUN_AS_NODE", None)             # some terminals set this, and it stops Electron from opening a window
        env["JARVIS_BODY_PORT"] = str(self._port)
        env["JARVIS_BODY_TOKEN"] = self._token
        LOG_FILE.parent.mkdir(exist_ok=True)
        log = open(LOG_FILE, "wb")
        self._proc = subprocess.Popen([str(exe), "main.js"], cwd=UI_DIR, env=env, stdout=log, stderr=log,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(1.5)
        if self._proc.poll() is not None:
            raise RuntimeError(f"the orb window closed right away, see {LOG_FILE}")

    # ----------------------------------------------------- the connection

    def _serve_thread(self):
        try:
            asyncio.run(self._serve())
        except Exception as error:
            self._error = error
            self._ready.set()

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._outbox = asyncio.Queue()
        async with serve(self._handler, "127.0.0.1", 0) as server:      # port 0: any free port
            self._port = server.sockets[0].getsockname()[1]
            self._ready.set()
            while True:
                message = await self._outbox.get()
                data = json.dumps(message)
                for client in list(self._clients):
                    try:
                        await client.send(data)
                    except Exception:
                        self._clients.discard(client)

    async def _handler(self, ws):
        query = parse_qs(urlparse(ws.request.path).query)
        if query.get("token", [""])[0] != self._token:                 # only our own orb may connect
            await ws.close(1008, "not allowed")
            return
        self._clients.add(ws)
        try:
            s = self._snapshot
            for message in ({"type": "state", "value": s["state"]},
                            {"type": "user", "text": s["user"]},
                            {"type": "jarvis", "text": s["jarvis"]}):
                await ws.send(json.dumps(message))
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if message.get("type") == "stop" and self.on_stop:
                    threading.Thread(target=self.on_stop, daemon=True).start()
        finally:
            self._clients.discard(ws)

    def _send(self, message):
        """Safe to call from any thread."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._outbox.put_nowait, message)

    # ----------------------------------------- the same methods as body.py

    def set_state(self, state):
        self._snapshot["state"] = state
        self._send({"type": "state", "value": state})

    def flash(self, state, seconds=2.0):
        """Show a state for a few seconds, then go back to idle by itself."""
        self._send({"type": "flash", "value": state, "seconds": seconds})

    def set_user(self, text):
        text = " ".join(text.split())
        self._snapshot["user"] = text
        self._send({"type": "user", "text": text})

    def set_jarvis(self, text, duration=None):
        """duration = seconds of audio, so the typing finishes about when his voice does."""
        text = " ".join(text.split())
        self._snapshot["jarvis"] = text
        message = {"type": "jarvis", "text": text}
        if duration:
            message["duration"] = duration
        self._send(message)

    def panel(self, action):
        """'minimize', 'expand', 'toggle' or 'settings'."""
        self._send({"type": "panel", "action": action})

    def minimize(self):
        self.panel("minimize")

    def expand(self):
        self.panel("expand")

    def voice_level(self, samples, rate):
        """Start sending his voice loudness to the orb. Call .stop() on the result when the audio ends."""
        meter = _VoiceLevel(self, samples, rate)
        meter.start()
        return meter

    def run(self):
        """Wait until the orb window is closed, like the classic body's main loop."""
        while self._proc.poll() is None:
            time.sleep(0.4)

    def close(self):
        """Close the orb window along with Jarvis."""
        try:
            if self._proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"], capture_output=True)
        except Exception:
            pass