import asyncio
import itertools
import json
import threading

from websockets.asyncio.server import serve

PORT = 8765

_loop = None
_client = None
_pending = {}
_ids = itertools.count(1)
_ready = threading.Event()


async def _handler(ws):
    global _client
    # Only the Chrome extension may connect, not a random web page
    origin = ws.request.headers.get("Origin", "")
    if not origin.startswith("chrome-extension://"):
        await ws.close(1008, "not allowed")
        return
    _client = ws
    try:
        async for raw in ws:
            msg = json.loads(raw)
            future = _pending.pop(msg.get("id"), None)
            if future and not future.done():
                future.set_result(msg)
    finally:
        if _client is ws:
            _client = None


async def _serve():
    async with serve(_handler, "127.0.0.1", PORT):
        _ready.set()
        await asyncio.Future()      # run forever


def _run():
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_until_complete(_serve())


def start():
    """Start the bridge in a background thread."""
    threading.Thread(target=_run, daemon=True).start()
    _ready.wait(5)


def connected():
    return _client is not None


async def _send(action, **kwargs):
    if _client is None:
        raise RuntimeError("the browser extension isn't connected")
    msg_id = next(_ids)
    future = _loop.create_future()
    _pending[msg_id] = future
    await _client.send(json.dumps({"id": msg_id, "action": action, **kwargs}))
    return await asyncio.wait_for(future, 5)


def request(action, **kwargs):
    """Send a command to the extension and wait for its answer. Safe to call from any thread."""
    if _loop is None:
        raise RuntimeError("the bridge isn't started")
    return asyncio.run_coroutine_threadsafe(_send(action, **kwargs), _loop).result(8)