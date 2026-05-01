from __future__ import annotations

import asyncio
import json
import threading
from typing import Dict, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol

from .config import WebViewerConfig


class WebAudioWSServer:
    """
    WebSocket PCM broadcaster.

    Protocol expected by web.html:
      - On connect: send JSON header as text (one-time)
      - Then: send PCM S16LE chunks as binary frames
    """

    def __init__(self, cfg: WebViewerConfig):
        self.cfg = cfg
        self._stop_evt = threading.Event()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

        self._clients: Set[WebSocketServerProtocol] = set()
        self._queues: Dict[WebSocketServerProtocol, asyncio.Queue[bytes]] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ws-audio-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._loop:
            # Wake up the event loop so main_async can exit quickly.
            self._loop.call_soon_threadsafe(lambda: None)
        if self._thread:
            self._thread.join(timeout=2.0)

    def broadcast_pcm(self, pcm: bytes) -> None:
        """
        Thread-safe. Called from appsink pull thread.
        Drops for slow clients to keep latency bounded.
        """
        if not pcm or not self._loop:
            return

        def _enqueue() -> None:
            for ws in list(self._clients):
                q = self._queues.get(ws)
                if q is None:
                    continue
                if q.full():
                    try:
                        q.get_nowait()  # drop oldest
                    except Exception:
                        pass
                try:
                    q.put_nowait(pcm)
                except Exception:
                    pass

        self._loop.call_soon_threadsafe(_enqueue)

    @staticmethod
    def _is_disconnect_oserror(e: OSError) -> bool:
        # Common Windows disconnects:
        # - 64: "The specified network name is no longer available"
        # - 10054: connection reset by peer
        # - 10053: software caused connection abort
        winerr = getattr(e, "winerror", None)
        return winerr in (64, 10054, 10053)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def handler(ws: WebSocketServerProtocol):
            addr = ws.remote_address
            self._clients.add(ws)
            q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max(1, int(self.cfg.ws_max_queue_chunks)))
            self._queues[ws] = q

            header = {
                "codec": "pcm",
                "format": "S16LE",
                "rate": int(self.cfg.rate_hz),
                "channels": int(self.cfg.channels),
                "chunk_ms": int(self.cfg.chunk_ms),
            }

            try:
                # Header (text)
                await ws.send(json.dumps(header))

                # PCM stream (binary)
                while not self._stop_evt.is_set():
                    try:
                        pcm = await asyncio.wait_for(q.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        await ws.send(pcm)
                    except ConnectionClosed:
                        # Normal client disconnect (tab closed, phone sleeps, Wi-Fi switches, etc.)
                        print(f"[WS] disconnected: {addr}", flush=True)
                        break
                    except OSError as e:
                        if self._is_disconnect_oserror(e):
                            print(f"[WS] disconnected: {addr}", flush=True)
                            break
                        # Unexpected OS error; keep a single-line signal (no traceback spam)
                        print(f"[WS] send failed ({type(e).__name__}): {addr}", flush=True)
                        break

            except ConnectionClosed:
                # Header send can also fail if client closes immediately.
                print(f"[WS] disconnected: {addr}", flush=True)

            except OSError as e:
                if self._is_disconnect_oserror(e):
                    print(f"[WS] disconnected: {addr}", flush=True)
                else:
                    print(f"[WS] handler failed ({type(e).__name__}): {addr}", flush=True)

            finally:
                self._clients.discard(ws)
                self._queues.pop(ws, None)
                try:
                    await ws.close()
                except Exception:
                    pass

        async def main_async():
            async with websockets.serve(
                handler,
                self.cfg.ws_host,
                int(self.cfg.ws_port),
                max_size=None,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=2,
            ):
                while not self._stop_evt.is_set():
                    await asyncio.sleep(0.2)

        try:
            self._loop.run_until_complete(main_async())
        finally:
            try:
                self._loop.stop()
                self._loop.close()
            except Exception:
                pass
            self._loop = None