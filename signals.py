import time
import jdatetime
import os
import json

def load_last_signals(symbols):
    file_path = "live_signals.json"
    if not os.path.exists(file_path):
        # ساختار جدید مجهز به فیلد زمان و تاریخچه معامله
        return {
            sym: {
                "signal": "HOLD",
                "entry_price": 0.0,
                "target_price": 0.0,
                "stop_price": 0.0,
                "oco_order_id": None,
                "updated_at": jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "trade_history": []
            } for sym in symbols
        }

    # تلاش برای خواندن ایمن با تکرار در صورت قفل بودن فایل
    for _ in range(5):
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            time.sleep(0.5)  # نیم ثانیه صبر کن تا پروسس احتمالی دیگر کارش تمام شود

    # اگر کلاً فایل خراب شده بود، دیتای خالی برگردان تا کرش نکند
    return {}


def save_last_signals(data):
    file_path = "live_signals.json"  # هم‌نام با تابع load
    temp_file_path = file_path + ".tmp"
    for _ in range(5):
        try:
            with open(temp_file_path, 'w') as f:
                json.dump(data, f, indent=4)
            if os.path.exists(temp_file_path):
                os.replace(temp_file_path, file_path)
            return True
        except IOError:
            time.sleep(0.5)
    return False

