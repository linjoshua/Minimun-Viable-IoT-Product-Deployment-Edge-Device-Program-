from __future__ import annotations

from typing import Callable, Optional

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GLib  # noqa: E402


def attach_bus_watch(
    pipeline: Gst.Pipeline,
    loop: GLib.MainLoop,
    on_fatal: Optional[Callable[[], None]] = None,
) -> Callable[[], None]:
    """
    Attach a signal watch to the pipeline bus.

    - Prints ERROR/WARNING/EOS
    - Prints pipeline state transitions
    - On ERROR/EOS, quits GLib loop and optionally calls on_fatal()

    Returns:
      stop_cb(): removes bus watch (safe to call multiple times).
    """
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    stopped = {"v": False}

    def stop_cb() -> None:
        if stopped["v"]:
            return
        stopped["v"] = True
        try:
            bus.remove_signal_watch()
        except Exception:
            pass

    def _fatal_shutdown() -> None:
        if on_fatal is not None:
            try:
                on_fatal()
            except Exception as e:
                print(f"[WARN] on_fatal callback raised: {type(e).__name__}: {e}", flush=True)
        loop.quit()

    def on_message(_bus: Gst.Bus, message: Gst.Message) -> None:
        t = message.type

        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[GStreamer ERROR] {err}", flush=True)
            if debug:
                print(f"[Debug] {debug}", flush=True)
            stop_cb()
            _fatal_shutdown()

        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            print(f"[GStreamer WARNING] {err}", flush=True)
            if debug:
                print(f"[Debug] {debug}", flush=True)

        elif t == Gst.MessageType.EOS:
            print("[GStreamer] EOS", flush=True)
            stop_cb()
            _fatal_shutdown()

        elif t == Gst.MessageType.STATE_CHANGED and message.src == pipeline:
            old, new, pending = message.parse_state_changed()
            print(f"[STATE] {old.value_nick} -> {new.value_nick} (pending={pending.value_nick})", flush=True)

    bus.connect("message", on_message)
    return stop_cb