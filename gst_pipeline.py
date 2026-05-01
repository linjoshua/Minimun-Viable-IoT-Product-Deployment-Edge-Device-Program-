from __future__ import annotations

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .config import WebViewerConfig


def build_capture_to_appsink_pipeline(device_id: str, cfg: Config) -> tuple[Gst.Pipeline, Gst.Element]:
    """
    Build a robust microphone capture pipeline (ElementFactory based).

    Pipeline:
      wasapi2src(device) !
      queue !
      audioconvert !
      audioresample !
      capsfilter(S16LE, 48kHz, mono) !
      queue(leaky=downstream) !
      appsink(drop)
    """
    pipeline = Gst.Pipeline.new("mic-pipe")
    if pipeline is None:
        raise RuntimeError("Failed to create Gst.Pipeline")

    def mk(name: str, inst: str) -> Gst.Element:
        e = Gst.ElementFactory.make(name, inst)
        if e is None:
            raise RuntimeError(f"Failed to create element: {name}")
        return e

    src = mk("wasapi2src", "src")
    src.set_property("device", device_id)
    src.set_property("do-timestamp", True)

    q0 = mk("queue", "q0")

    aconv = mk("audioconvert", "aconv")
    ares = mk("audioresample", "ares")

    capsfilter = mk("capsfilter", "caps")
    caps = Gst.Caps.from_string(
        f"audio/x-raw,format=S16LE,layout=interleaved,channels={cfg.channels},rate={cfg.rate_hz}"
    )
    capsfilter.set_property("caps", caps)

    q1 = mk("queue", "q1")
    # Prevent unbounded buffering if Python becomes slow
    q1.set_property("leaky", 2)  # 2 = downstream
    q1.set_property("max-size-buffers", 30)
    q1.set_property("max-size-time", 0)
    q1.set_property("max-size-bytes", 0)

    sink = mk("appsink", "asink")
    # We pull samples in Python (no signals)
    sink.set_property("emit-signals", False)
    sink.set_property("sync", False)
    sink.set_property("async", False)
    # Drop old buffers if downstream is slow
    sink.set_property("max-buffers", 20)
    sink.set_property("drop", True)
    sink.set_property("enable-last-sample", False)

    for e in (src, q0, aconv, ares, capsfilter, q1, sink):
        pipeline.add(e)

    if not Gst.Element.link(src, q0):
        raise RuntimeError("Failed to link src -> q0")
    if not Gst.Element.link(q0, aconv):
        raise RuntimeError("Failed to link q0 -> aconv")
    if not Gst.Element.link(aconv, ares):
        raise RuntimeError("Failed to link aconv -> ares")
    if not Gst.Element.link(ares, capsfilter):
        raise RuntimeError("Failed to link ares -> capsfilter")
    if not Gst.Element.link(capsfilter, q1):
        raise RuntimeError("Failed to link capsfilter -> q1")
    if not Gst.Element.link(q1, sink):
        raise RuntimeError("Failed to link q1 -> sink")

    return pipeline, sink