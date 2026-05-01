from __future__ import annotations

import threading

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .config import WebViewerConfig
from .wav_roll_writer import WavRollWriter


class AppSinkPuller:
    """
    Pull raw PCM from appsink in a dedicated thread.

    - Accumulates incoming bytes into an internal buffer
    - Emits fixed-size chunks (chunk_ms) to WavRollWriter
    - Provides a chunks/sec metric for optional debugging
    """

    def __init__(self, appsink: Gst.Element, writer: WavRollWriter, cfg: Config):
        self.appsink = appsink
        self.writer = writer
        self.cfg = cfg

        self._stop_evt = threading.Event()
        self._th = threading.Thread(target=self._run, name="appsink-pull", daemon=True)

        self._cnt_lock = threading.Lock()
        self._chunks_per_sec = 0

        # Derived sizes (must match capsfilter)
        self._bytes_per_frame = int(cfg.channels * cfg.bytes_per_sample)
        self._frames_per_chunk = int(round(cfg.rate_hz * (cfg.chunk_ms / 1000.0)))
        self._bytes_per_chunk = int(self._frames_per_chunk * self._bytes_per_frame)

        # Prevent unbounded memory growth if downstream stalls
        self._max_acc_bytes = self._bytes_per_chunk * 200  # ~4 seconds for 20ms chunks

        self._printed_caps = False

    @property
    def bytes_per_chunk(self) -> int:
        return self._bytes_per_chunk

    @property
    def frames_per_chunk(self) -> int:
        return self._frames_per_chunk

    def start(self) -> None:
        self._th.start()

    def stop(self) -> None:
        self._stop_evt.set()
        self._th.join(timeout=2.0)

    def pop_and_reset_chunks_per_sec(self) -> int:
        with self._cnt_lock:
            v = self._chunks_per_sec
            self._chunks_per_sec = 0
        return v

    def _inc_chunk_count(self) -> None:
        with self._cnt_lock:
            self._chunks_per_sec += 1

    def _run(self) -> None:
        acc = bytearray()
        timeout_ns = int(self.cfg.pull_timeout_ms * 1e6)

        while not self._stop_evt.is_set():
            sample = self.appsink.emit("try-pull-sample", timeout_ns)
            if sample is None:
                continue

            # Print caps once (debug-only)
            if (not self._printed_caps) and self.cfg.debug_print_caps:
                caps = sample.get_caps()
                if caps is not None:
                    print("[Caps] sample caps:", caps.to_string(), flush=True)
                self._printed_caps = True

            buf = sample.get_buffer()
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                acc.extend(mapinfo.data)
            finally:
                buf.unmap(mapinfo)

            # Safety: prevent accumulator blow-up
            if len(acc) > self._max_acc_bytes:
                print(f"[WARN] Accumulator overflow ({len(acc)} bytes). Dropping old data.", flush=True)
                del acc[:-self._bytes_per_chunk]

            # Emit fixed-size chunks (sample-accurate)
            while len(acc) >= self._bytes_per_chunk:
                chunk = bytes(acc[:self._bytes_per_chunk])
                del acc[:self._bytes_per_chunk]
                self.writer.write_pcm_bytes(chunk)
                self._inc_chunk_count()

        acc.clear()