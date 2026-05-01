from __future__ import annotations

import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def start_static_http_server(root_dir: Path, host: str, port: int, quiet: bool = True) -> ThreadingHTTPServer:
    """
    Serve files under root_dir (e.g., web.html) via HTTP.
    """
    os.chdir(str(root_dir))

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            if quiet:
                return
            super().log_message(format, *args)

    httpd = ThreadingHTTPServer((host, port), QuietHandler)
    th = threading.Thread(target=httpd.serve_forever, name="http-server", daemon=True)
    th.start()
    return httpd