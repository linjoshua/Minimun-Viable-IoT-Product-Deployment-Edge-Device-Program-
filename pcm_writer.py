from __future__ import annotations

from .ws_audio import WebAudioWSServer


class PcmOnlyWriter:
    """
    Writer adapter for AppSinkPuller.
    No disk writes. Streams PCM to WebAudioWSServer.
    """

    def __init__(self, ws_server: WebAudioWSServer):
        self.ws = ws_server

    def write_pcm_bytes(self, pcm_bytes: bytes) -> None:
        self.ws.broadcast_pcm(pcm_bytes)

    def close(self) -> None:
        return