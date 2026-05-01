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

# 👉 新增：引入我們剛剛寫好的 ANN 模型預測器
from .ann_predictor import MachineStatePredictor  # 如果沒有放在 package 裡，把前面的 . 拿掉

# ==========================================
# 1. RMS 與 MQTT 發送器 (結合 ANN 推論)
# ==========================================
class RMSPublisherWriter:
    # 👉 修改：在初始化時接收 predictor
    def __init__(self, rate, channels, sampwidth, predictor=None):
        self.bytes_per_second = rate * channels * sampwidth
        self.buffer = bytearray()
        
        self.V_REF = 2.25
        self.S_DBV = -38.0
        self.P_0 = 20e-6
        self.MIC_SENSITIVITY_PA = (10 ** (self.S_DBV / 20)) * self.P_0

        self.mqtt_topic_rms = "sensor/cnc/audio_rms"
        self.mqtt_topic_state = "sensor/cnc/machine_state" # 👉 新增：狀態發送路徑
        self.predictor = predictor # 👉 將模型存入實例中

        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        try:
            # 記得確認你的 Port 是 1883 還是 1884 喔！
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
            
            data_int16 = np.frombuffer(one_sec_data, dtype=np.int16)
            data_norm = data_int16.astype(np.float32) / 32768.0
            x_rms = np.sqrt(np.mean(np.square(data_norm)))
            
            db_spl = 0.0
            if x_rms > 0:
                db_spl = 20 * np.log10((x_rms * self.V_REF) / self.MIC_SENSITIVITY_PA)
            
            if self.mqtt_client.is_connected():
                # 👉 1. 發送 RMS 數值
                self.mqtt_client.publish(self.mqtt_topic_rms, f"{db_spl:.2f}")
                
                # 👉 2. 進行 ANN 推論並發送狀態
                if self.predictor:
                    current_state = self.predictor.predict(db_spl)
                    self.mqtt_client.publish(self.mqtt_topic_state, current_state)
                    # 在終端機印出精美的即時戰情
                    print(f"MQTT Sent | RMS: {db_spl:.2f} dB | State: {current_state}", flush=True)
                else:
                    print(f"MQTT Sent | RMS: {db_spl:.2f} dB", flush=True)

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

    # 抓取麥克風
    device_id = pick_wasapi_mic_id(CFG.prefer_name)
    if not device_id:
        print("[ERR] No suitable WASAPI microphone found.", flush=True)
        return 2

    # 👉 新增：初始化 ANN 模型
    model_dir = Path(__file__).resolve().parent / "20260421_142944_CNC_Machine_ANN_Model"
    ann_predictor = MachineStatePredictor(str(model_dir))

    # 初始化三個 Writer
    web_writer = PcmOnlyWriter(ws_server=ws)
    wav_writer = WavRollWriter(
        work_dir=CFG.work_dir, final_dir=CFG.final_dir, segment_sec=CFG.segment_sec,
        rate=CFG.rate_hz, channels=CFG.channels, sampwidth=CFG.bytes_per_sample
    )
    
    # 👉 修改：把 ANN 預測器塞給 MQTT 發送器
    mqtt_writer = RMSPublisherWriter(
        rate=CFG.rate_hz, channels=CFG.channels, sampwidth=CFG.bytes_per_sample,
        predictor=ann_predictor
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