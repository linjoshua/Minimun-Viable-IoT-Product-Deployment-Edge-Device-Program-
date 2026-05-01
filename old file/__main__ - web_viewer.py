from __future__ import annotations

import signal
import threading
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GLib  # noqa: E402

from web_viewer.config import CFG, WebViewerConfig
from .http_server import start_static_http_server
from .ws_audio import WebAudioWSServer
from .pcm_writer import PcmOnlyWriter
from .device_picker import pick_wasapi_mic_id
from .gst_pipeline import build_capture_to_appsink_pipeline
from .sink_puller import AppSinkPuller
from .bus_watch import attach_bus_watch


def main() -> int:
    Gst.init(None)

    # Start HTTP server from project root (where web.html exists)
    web_root = Path(__file__).resolve().parent
    httpd = start_static_http_server(web_root, CFG.http_host, CFG.http_port, quiet=True)

    # WebSocket audio server
    ws = WebAudioWSServer(CFG)
    ws.start()

    # Pick device
    device_id = pick_wasapi_mic_id(CFG.prefer_name)
    if not device_id:
        print("[ERR] No suitable WASAPI microphone found.", flush=True)
        ws.stop()
        httpd.shutdown()
        httpd.server_close()
        return 2

    # Build pipeline (using wav_saver module)
    # NOTE: build_capture_to_appsink_pipeline expects a config object with:
    #   rate_hz, channels, bytes_per_sample, etc.
    # If your wav_saver.gst_pipeline imports Config type strictly, adjust it to accept any cfg-like object.
    pipeline, appsink = build_capture_to_appsink_pipeline(device_id=device_id, cfg=CFG)

    # GLib loop for bus watch
    glib_loop = GLib.MainLoop()

    writer = PcmOnlyWriter(ws_server=ws)
    puller = AppSinkPuller(appsink, writer, cfg=CFG)

    def stop_everything() -> None:
        try:
            puller.stop()
        except Exception:
            pass
        try:
            pipeline.set_state(Gst.State.NULL)
        except Exception:
            pass
        try:
            ws.stop()
        except Exception:
            pass
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass

    # Bus watch
    stop_bus_watch = attach_bus_watch(pipeline, glib_loop)

    # Ctrl+C
    def handle_sigint(_sig, _frame):
        stop_bus_watch()
        stop_everything()
        try:
            glib_loop.quit()
        except Exception:
            pass

    signal.signal(signal.SIGINT, handle_sigint)

    # Start capture
    puller.start()
    ret = pipeline.set_state(Gst.State.PLAYING)
    if CFG.debug_print_state:
        print("[Start] set_state:", ret.value_nick, flush=True)

    print(f"[OK] HTTP: http://{CFG.http_host}:{CFG.http_port}/{CFG.web_html_filename}", flush=True)
    print(f"[OK] WS  : ws://{CFG.ws_host}:{CFG.ws_port}", flush=True)

    # Run GLib loop in this thread
    try:
        glib_loop.run()
    finally:
        stop_bus_watch()
        stop_everything()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())