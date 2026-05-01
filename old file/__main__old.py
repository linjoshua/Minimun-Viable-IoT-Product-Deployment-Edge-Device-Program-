from __future__ import annotations

import signal
import numpy as np
import paho.mqtt.client as mqtt
from pathlib import Path

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gst, GLib

from .config import CFG
from .http_server import start_static_http_server
from .ws_audio import WebAudioWSServer
from .pcm_writer import PcmOnlyWriter
from .device_picker import pick_wasapi_mic_id
from .gst_pipeline import build_capture_to_appsink_pipeline
from .sink_puller import AppSinkPuller
from .bus_watch import attach_bus_watch
from .wav_roll_writer import WavRollWriter

# ==========================================
# 1. RMS 與 MQTT 發送器 (還原為 Windows 原始無 DC Offset 校正版)
# ==========================================
class RMSPublisherWriter:
    def __init__(self, rate, channels, sampwidth):
        self.bytes_per_second = rate * channels * sampwidth
        self.buffer = bytearray()
        
        self.V_REF = 2.25
        self.S_DBV = -38.0
        self.P_0 = 20e-6
        self.MIC_SENSITIVITY_PA = (10 ** (self.S_DBV / 20)) * self.P_0

        self.mqtt_topic = "sensor/cnc/audio_rms"
        self.mqtt_client = mqtt.Client()
        try:
            # 因為現在要在 Windows 邊緣裝置上跑，如果 Mosquitto 也裝在同一台，就用 localhost
            self.mqtt_client.connect("localhost", 1883, 60)
            self.mqtt_client.loop_start()
            print("[MQTT] Connected to broker successfully!", flush=True)
        except Exception as e:
            print(f"[MQTT Warning] Could not connect to broker: {e}", flush=True)

    def write_pcm_bytes(self, data: bytes) -> None:
        self.buffer.extend(data)
        if len(self.buffer) >= self.bytes_per_second:
            one_sec_data = self.buffer[:self.bytes_per_second]
            self.buffer = self.buffer[self.bytes_per_second:]
            
            # Windows 的底層 WASAPI 處理得很好，直接算 RMS 即可，不扣除 -40 校正值
            data_int16 = np.frombuffer(one_sec_data, dtype=np.int16)
            data_norm = data_int16.astype(np.float32) / 32768.0
            x_rms = np.sqrt(np.mean(np.square(data_norm)))
            
            db_spl = 0.0
            if x_rms > 0:
                db_spl = 20 * np.log10((x_rms * self.V_REF) / self.MIC_SENSITIVITY_PA)
            
            if self.mqtt_client.is_connected():
                self.mqtt_client.publish(self.mqtt_topic, f"{db_spl:.2f}")

    def close(self) -> None:
        self.mqtt_client.loop_stop()
        self.mqtt_client.disconnect()

# ==========================================
# 2. 終極分流器
# ==========================================
class MultiWriter:
    def __init__(self, writers: list):
        self.writers = writers

    def write_pcm_bytes(self, data: bytes) -> None:
        for w in self.writers:
            w.write_pcm_bytes(data)

    def close(self) -> None:
        for w in self.writers:
            try:
                w.close()
            except Exception:
                pass

# ==========================================
# 3. 主程式
# ==========================================
def main() -> int:
    Gst.init(None)

    # 啟動網頁伺服器
    web_root = Path(__file__).resolve().parent
    httpd = start_static_http_server(web_root, CFG.http_host, CFG.http_port, quiet=True)

    # 啟動 WebSocket 音訊伺服器
    ws = WebAudioWSServer(CFG)
    ws.start()

    # 抓取麥克風 (這裡會自動呼叫你原本 Windows 版的 WASAPI 邏輯)
    device_id = pick_wasapi_mic_id(CFG.prefer_name)
    if not device_id:
        print("[ERR] No suitable WASAPI microphone found.", flush=True)
        return 2

    # 初始化三個 Writer
    web_writer = PcmOnlyWriter(ws_server=ws)
    wav_writer = WavRollWriter(
        work_dir=CFG.work_dir, final_dir=CFG.final_dir, segment_sec=CFG.segment_sec,
        rate=CFG.rate_hz, channels=CFG.channels, sampwidth=CFG.bytes_per_sample
    )
    mqtt_writer = RMSPublisherWriter(
        rate=CFG.rate_hz, channels=CFG.channels, sampwidth=CFG.bytes_per_sample
    )

    ultimate_writer = MultiWriter([web_writer, wav_writer, mqtt_writer])

    pipeline, appsink = build_capture_to_appsink_pipeline(device_id=device_id, cfg=CFG)
    glib_loop = GLib.MainLoop()
    puller = AppSinkPuller(appsink, ultimate_writer, cfg=CFG)

    def stop_everything() -> None:
        try: puller.stop()
        except Exception: pass
        try: pipeline.set_state(Gst.State.NULL)
        except Exception: pass
        try: ws.stop()
        except Exception: pass
        try: httpd.shutdown()
        except Exception: pass
        try: ultimate_writer.close()
        except Exception: pass

    stop_bus_watch = attach_bus_watch(pipeline, glib_loop)

    def handle_sigint(_sig, _frame):
        print("\n[CTRL+C] Stopping Ultimate Windows Monitor...", flush=True)
        stop_bus_watch()
        stop_everything()
        try: glib_loop.quit()
        except Exception: pass

    signal.signal(signal.SIGINT, handle_sigint)

    puller.start()
    pipeline.set_state(Gst.State.PLAYING)

    print("========================================", flush=True)
    print("🚀 ULTIMATE WINDOWS CNC MONITOR STARTED!", flush=True)
    print(f"📊 Web UI : http://localhost:{CFG.http_port}/web.html")
    print(f"💾 WAV Dir: {CFG.final_dir}")
    print("========================================", flush=True)

    try:
        glib_loop.run()
    finally:
        stop_bus_watch()
        stop_everything()

    return 0

if __name__ == "__main__":
    raise SystemExit(main())