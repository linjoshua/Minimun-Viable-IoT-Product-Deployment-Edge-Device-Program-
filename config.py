from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebViewerConfig:
    # Device
    prefer_name: str = "USB PnP Audio Device"

    # === 補上 WAV 存檔的設定 ===
    work_dir: str = r"C:\Users\joikh\OneDrive\Desktop\ME 597\Project\mic_work_temp"
    final_dir: str = r"C:\Users\joikh\OneDrive\Desktop\ME 597\Project\wav_file"
    segment_sec: int = 5
    # ==========================

    # Audio format (must match pipeline caps)
    rate_hz: int = 48000
    channels: int = 1
    bytes_per_sample: int = 2  # S16LE

    # Chunking (Python-side)
    chunk_ms: int = 20
    pull_timeout_ms: int = 100

    # Servers
    http_host: str = "0.0.0.0"
    http_port: int = 8000
    ws_host: str = "0.0.0.0"
    ws_port: int = 8765

    # Browser page
    web_html_filename: str = "web.html"

    # Per-client queue size; controls max latency build-up
    ws_max_queue_chunks: int = 200

    # Debug prints
    debug_print_caps: bool = False
    debug_print_state: bool = True


CFG = WebViewerConfig()