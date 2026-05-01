import os
import numpy as np

# 👉 新增這行：強制 TensorFlow 切換回 Keras 2 (懷舊模式) 來讀取舊版資料夾
os.environ["TF_USE_LEGACY_KERAS"] = "1"

# 隱藏 TensorFlow 煩人的警告訊息
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
import tensorflow as tf

# ... (下面的 class MachineStatePredictor 都不用動) ...

class MachineStatePredictor:
    def __init__(self, model_path):
        print(f"[ANN] loading ANN model: {model_path}...", flush=True)
        try:
            self.model = tf.keras.models.load_model(model_path)
            print("[ANN] Model loading success！", flush=True)
        except Exception as e:
            print(f"[ANN] Model loading failed: {e}", flush=True)
            self.model = None
            
        # 定義狀態標籤 (需對應模型輸出的 0, 1, 2)
        #self.states = ["Idle", "On_not_cleaning", "Cleaning"]
        
        # 寫死訓練資料的 StandardScaler 參數 (從截圖取得)
        self.TRAIN_MEAN = 98.85920227920228
        self.TRAIN_STD = 13.971594420311979

    def predict(self, rms_db):
        if self.model is None:
            return "模型未載入"
            
        try:
            # 1. Z-Score 標準化
            scaled_rms = (rms_db - self.TRAIN_MEAN) / self.TRAIN_STD
            
            # 2. 轉換成模型預期的 2D Tensor
            input_data = tf.constant([[scaled_rms]], dtype=tf.float32)
            
            # 3. 進行推論！(針對各種格式的萬用解法)
            if hasattr(self.model, 'predict'):
                # 情況 A：它是完整的 Keras 模型
                prediction = self.model.predict(input_data, verbose=0)
                
            elif hasattr(self.model, 'signatures'):
                # 情況 B：它是底層的 SavedModel (你現在的情況)
                # 拿出它的推論簽名 (鑰匙)
                infer = self.model.signatures["serving_default"]
                
                # 將資料丟進去算，回傳的會是一個「字典 (Dictionary)」
                result = infer(input_data)
                
                # 我們不在乎字典的 key 是什麼，直接把裡面算好的陣列 (values) 抽出來
                prediction = list(result.values())[0].numpy()
            else:
                return "未知的模型結構"
            
            # 4. 取得機率最高的分數索引
            state_index = np.argmax(prediction)
            
            #return self.states[state_index]
            return str(int(state_index))
            
        except Exception as e:
            print(f"⚠️ 預測過程發生錯誤: {e}", flush=True)
            return "預測錯誤"