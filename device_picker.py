import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

_inited = False

def _ensure_init():
    global _inited
    if not _inited:
        Gst.init(None)
        _inited = True


def pick_wasapi_mic_id(prefer_substring="USB PnP Audio Device"):
    _ensure_init()

    mon = Gst.DeviceMonitor()
    mon.add_filter("Audio/Source", None)
    mon.start()

    candidates = []
    for dev in mon.get_devices():
        props = dev.get_properties()
        if not props:
            continue

        if props.get_string("device.api") != "wasapi2":
            continue

        loopback = (
            props.get_boolean("wasapi2.device.loopback")[1]
            if props.has_field("wasapi2.device.loopback")
            else False
        )
        if loopback:
            continue

        desc = props.get_string("wasapi2.device.description") or dev.get_display_name() or ""
        dev_id = props.get_string("device.id")

        caps_ok = False
        caps = dev.get_caps()
        if caps:
            s = caps.to_string()
            if "rate=(int)48000" in s and "channels=(int)1" in s:
                caps_ok = True

        score = 0
        if prefer_substring.lower() in desc.lower():
            score += 10
        if caps_ok:
            score += 3

        candidates.append((score, desc, dev_id))

    mon.stop()

    candidates.sort(reverse=True, key=lambda x: x[0])
    if not candidates or candidates[0][0] == 0:
        print("No preferred mic found. Candidates:", flush=True)
        for sc, desc, did in candidates:
            print(f"  score={sc:2d}  desc={desc}  id={did}", flush=True)
        return None

    best = candidates[0]
    print(f"Selected: score={best[0]} desc={best[1]} id={best[2]}", flush=True)
    return best[2]