import time
import ccxt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import ta
import json
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
import jdatetime

now_shamsi=jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
# ==========================================
# 🛑 تنظیمات کلیدی و لایه‌های جدید ربات 🛑
# ==========================================
PAPER_TRADING = True
RISK_PERCENT = 2.0
MAX_DAILY_TRADES = 25
MAX_OPEN_POSITIONS = 25
target_day=0
MAX_HOLD_HOURS = 72
# تعاریف وضعیت‌های ربات
STATE_IDLE = "IDLE"
STATE_HOLDING = "HOLDING"
STATE_COOLDOWN = "COOLDOWN"

# وضعیت اولیه ربات
robot_state = STATE_IDLE
cooldown_until = 0  # زمانی که استراحت ربات تمام می‌شود

# ==========================================
# 🛑 تنظیمات پیشرفته سیستم لاگین روزانه با پشتیبانی از UTF-8
# ==========================================
logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)

log_handler = TimedRotatingFileHandler("nobitex_bot.log", when="midnight", interval=1, backupCount=7, encoding='utf-8')
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
if hasattr(stream_handler.stream, 'reconfigure'):
    stream_handler.stream.reconfigure(encoding='utf-8')
logger.addHandler(stream_handler)

exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '1h'
BUDGET_TOMAN = 5000000

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
CC_EMAIL = "www.rasul.mahmoudimajd1038@gmail.com"

NOBITEX_TOKEN = "o5TJUZrJoLj7afjp3jxhYa2wixNdKI4gdX8KVtj9Htk="
NOBITEX_TOKEN_PUBLIC="4f607aff93a0f574deeda11c0a88c8d89ecc56af"
STATE_FILE = "bot_signals_state.json"

daily_trade_count = 0
last_reset_date = time.strftime("%Y-%m-%d")
max_peak_balance = 0.0

GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[0m"
RESET = "\033[0m"
PINK="\033[95m"


def detect_swing_points(df, window=2):
    """
    تشخیص قله‌ها و کف‌های محلی (Swing High/Low)
    window: تعداد کندل قبل و بعد که باید کمتر/بیشتر باشن تا یه نقطه به‌عنوان swing تایید بشه
    """
    df = df.copy()
    df['swing_high'] = False
    df['swing_low'] = False

    for i in range(window, len(df) - window):
        # بررسی قله محلی
        is_high = all(df['high'].iloc[i] >= df['high'].iloc[i-j] for j in range(1, window+1)) and \
                  all(df['high'].iloc[i] >= df['high'].iloc[i+j] for j in range(1, window+1))
        if is_high:
            df.at[df.index[i], 'swing_high'] = True

        # بررسی کف محلی
        is_low = all(df['low'].iloc[i] <= df['low'].iloc[i-j] for j in range(1, window+1)) and \
                 all(df['low'].iloc[i] <= df['low'].iloc[i+j] for j in range(1, window+1))
        if is_low:
            df.at[df.index[i], 'swing_low'] = True

    return df


def detect_market_structure(df, window=2):
    """
    بر اساس آخرین دو Swing High و دو Swing Low، ساختار روند رو مشخص می‌کنه
    خروجی: 'bullish' (HH+HL), 'bearish' (LH+LL), یا 'neutral' (نامشخص)
    """
    df = detect_swing_points(df, window=window)

    swing_highs = df[df['swing_high']]['high'].tolist()
    swing_lows = df[df['swing_low']]['low'].tolist()

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "خنثی"

    last_high, prev_high = swing_highs[-1], swing_highs[-2]
    last_low, prev_low = swing_lows[-1], swing_lows[-2]

    is_hh = last_high > prev_high
    is_hl = last_low > prev_low
    is_lh = last_high < prev_high
    is_ll = last_low < prev_low

    if is_hh and is_hl:
        return "صعودی"
    elif is_lh and is_ll:
        return "نزولی"
    else:
        return "خنثی"



def get_hours_since_entry(entry_time_str):
    """محاسبه تعداد ساعت‌های گذشته از زمان ورود (بر پایه jdatetime)"""
    try:
        entry_dt = jdatetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        now_dt = jdatetime.datetime.now()
        delta = now_dt - entry_dt
        return delta.total_seconds() / 3600
    except Exception as e:
        logger.error(f"⚠️ خطا در محاسبه زمان ورود: {e}")
        return 0


def load_last_signals(symbols):
    file_path = "live_signals_v2.json"
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
    file_path = "live_signals_v2.json"  # هم‌نام با تابع load
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


def _send_request_with_retry(method, url, headers=None, json_data=None, params=None, retries=3):
    for attempt in range(retries):
        try:
            if method.upper() == "POST":
                res = requests.post(url, headers=headers, json=json_data, timeout=15)
            else:
                res = requests.get(url, headers=headers, params=params, timeout=15)
            return res.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.warning(f"⚠️ خطای شبکه (تلاش {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
            else:
                logger.error("🚨 قطع کامل اینترنت یا انسداد سرور صرافی.")
                return {"status": "failed", "message": "Network Error"}


def get_iran_dollar_price():
    url = "https://apiv2.nobitex.ir/v3/orderbook/USDTIRT"
    res = _send_request_with_retry("GET", url, retries=3)

    # اصلاح شرط برای خواندن مستقیم از روت پاسخ v3
    if res and res.get('status') == 'ok' and 'lastTradePrice' in res:
        tether_rial = res['lastTradePrice']
        # تبدیل ریال به تومان
        return int(float(tether_rial) / 10)

    logger.error("❌ خطای قطعی شبکه یا تغییر ساختار API: پس از ۳ بار تلاش مجدد، دریافت قیمت تتر ناموفق بود.")
    return None


def get_nobitex_live_price(coin_name):
    url = "https://apiv2.nobitex.ir/market/stats"
    res = _send_request_with_retry(
        "GET", url,
        params={"srcCurrency": coin_name.lower(), "dstCurrency": "rls"}
    )
    if res and res.get("status") == "ok":
        pair = f"{coin_name.lower()}-rls"
        stats = res.get("stats", {})
        price_rial = stats.get(pair, {}).get("latest", None)
        if price_rial is not None:
            return float(price_rial) / 10
        else:
            logger.warning(f"⚠️ کلید '{pair}' در پاسخ نوبیتکس یافت نشد.")
    else:
        logger.warning(f"⚠️ دریافت قیمت لایو {coin_name} ناموفق بود. پاسخ: {res}")
    return None



def get_nobitex_wallet_balance():
    if PAPER_TRADING:
        return 10000000.0  # ۱۰ میلیون تومان موجودی فرضی در حالت تست

    url = "https://apiv2.nobitex.ir/v2/wallets"
    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}",
        "Content-Type": "application/json"
    }

    # ارسال درخواست بدون فیلترهای محدودکننده برای دریافت پاسخ کامل مالتی‌ولت
    res = _send_request_with_retry("POST", url, headers=headers, json_data={})

    if res and res.get("status") == "ok":
        wallets = res.get("wallets", {})

        # بررسی وجود کیف پول ریال یا تومان در پاسخ صرافی
        if "RLS" in wallets:
            rial_balance = float(wallets["RLS"].get("balance", 0.0))
            return rial_balance / 10.0  # تبدیل به تومان
        elif "IRT" in wallets:
            return float(wallets["IRT"].get("balance", 0.0))  # اگر خود تومان بود مستقیم برگردان

    logger.error(f"❌ خطا در دریافت موجودی از نوبیتکس. پاسخ صرافی: {res}")
    return 0.0


def check_daily_limits():
    global daily_trade_count, last_reset_date
    current_date = time.strftime("%Y-%m-%d")
    if current_date != last_reset_date:
        daily_trade_count = 0
        last_reset_date = current_date
    return daily_trade_count < MAX_DAILY_TRADES


def send_beautiful_email(subject, title, type_color, rows_data):
    current_time = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Tahoma, Arial, sans-serif; direction: rtl; background-color: #f4f6f9; color: #333; padding: 20px; }}
            .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 10px rgba(0,0,0,0.05); border-top: 6px solid {type_color}; }}
            .header {{ background-color: #1e293b; color: #ffffff; padding: 20px; text-align: center; }}
            .header h2 {{ margin: 0; font-size: 20px; }}
            .content {{ padding: 25px; }}
            .info-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            .info-table td {{ padding: 12px; border-bottom: 1px solid #e2e8f0; font-size: 14px; }}
            .info-table td.label {{ font-weight: bold; color: #4a5568; width: 40%; }}
            .info-table td.value {{ color: #1a202c; text-align: left; direction: ltr; }}
            .footer {{ background-color: #f8fafc; padding: 15px; text-align: center; font-size: 11px; color: #718096; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body dir="rtl">
        <div class="container">
            <div class="header"><h2>{title}</h2></div>
            <div class="content"><table class="info-table">
    """
    for label, val in rows_data:
        html_body += f"<tr><td class='label'>{label}</td><td class='value'>{val}</td></tr>"

    html_body += f"""
                <tr><td class="label">زمان سیگنال</td><td class="value">{current_time}</td></tr>
            </table></div>
            <div class="footer">این یک پیام خودکار از ربات معاملاتی شماست.</div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject

    recipients = [RECEIVER_EMAIL]
    if CC_EMAIL:
        msg['Cc'] = CC_EMAIL
        recipients.append(CC_EMAIL)

    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        # ✅ استفاده از پورت 465 و SMTP_SSL برای پایداری ۱۰۰٪ در جیمیل
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=15)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        server.quit()
        logger.info("📧 ایمیل با موفقیت ارسال شد.")
    except Exception as e:
        logger.error(f"⚠️ خطا در ارسال ایمیل: {e}")

def get_nobitex_data(symbol, timeframe='1h', limit=300):
    """
    دریافت OHLCV از Nobitex UDF

    تایم‌فریم‌های پشتیبانی‌شده:
        15m -> 15
        1h  -> 60
        4h  -> 240
        1d  -> D

    Daily با resolution='D' دریافت می‌شود.
    """

    tf_map = {
        '15m': '15',
        '1h': '60',
        '4h': '240',
        '1d': 'D'
    }

    resolution = tf_map.get(timeframe)

    if resolution is None:
        logger.error(
            f"❌ تایم‌فریم نامعتبر: {timeframe}"
        )
        return None

    try:
        src, dst = symbol.split('/')
        src = src.upper()
        dst = dst.upper()
    except ValueError:
        logger.error(
            f"❌ فرمت نماد نامعتبر: {symbol}"
        )
        return None

    url = "https://apiv2.nobitex.ir/market/udf/history"

    # محاسبه بازه زمانی
    if resolution == 'D':
        seconds_per_candle = 86400
    else:
        seconds_per_candle = int(resolution) * 60

    to_timestamp = int(time.time())

    from_timestamp = (
        to_timestamp -
        (limit * seconds_per_candle)
    )

    params = {
        "symbol": f"{src}{dst}",
        "resolution": resolution,
        "from": from_timestamp,
        "to": to_timestamp
    }

    try:

        response = requests.get(
            url,
            params=params,
            timeout=15
        )

        # -----------------------------
        # خطای HTTP
        # -----------------------------
        if response.status_code != 200:

            logger.error(
                f"❌ خطای HTTP {response.status_code} "
                f"در دریافت {symbol} | "
                f"TF={timeframe} | "
                f"resolution={resolution} | "
                f"symbol={src}{dst}"
            )

            # اگر 400 بود، فقط این نماد رد شود
            if response.status_code == 400:
                logger.warning(
                    f"⏭️ {symbol} به دلیل درخواست نامعتبر "
                    f"از این چرخه رد شد."
                )

            return None

        # -----------------------------
        # JSON
        # -----------------------------
        try:
            data = response.json()
        except ValueError:

            logger.error(
                f"❌ پاسخ JSON نامعتبر برای "
                f"{symbol} | TF={timeframe}"
            )

            return None

        # -----------------------------
        # وضعیت Nobitex
        # -----------------------------
        if data.get('s') != 'ok':

            logger.warning(
                f"⚠️ Nobitex OHLC ناموفق "
                f"{symbol} | TF={timeframe} | "
                f"resolution={resolution} | "
                f"پاسخ: {data}"
            )

            return None

        # -----------------------------
        # بررسی ساختار داده
        # -----------------------------
        required_keys = [
            't',
            'o',
            'h',
            'l',
            'c',
            'v'
        ]

        if not all(
            key in data
            for key in required_keys
        ):

            logger.warning(
                f"⚠️ ساختار OHLC ناقص برای "
                f"{symbol} | TF={timeframe}"
            )

            return None

        # -----------------------------
        # ساخت DataFrame
        # -----------------------------
        df = pd.DataFrame({
            'timestamp': data['t'],
            'open': data['o'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c'],
            'volume': data['v']
        })

        # -----------------------------
        # تبدیل ستون‌ها به عدد
        # -----------------------------
        numeric_columns = [
            'open',
            'high',
            'low',
            'close',
            'volume'
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(
                df[col],
                errors='coerce'
            )

        # -----------------------------
        # حذف کندل‌های خراب
        # -----------------------------
        df = df.dropna(
            subset=[
                'open',
                'high',
                'low',
                'close'
            ]
        )

        # -----------------------------
        # مرتب‌سازی
        # -----------------------------
        df = df.sort_values(
            'timestamp'
        ).reset_index(drop=True)

        # -----------------------------
        # بررسی تعداد کندل
        # -----------------------------
        if df.empty:

            logger.warning(
                f"⚠️ هیچ کندلی برای "
                f"{symbol} | TF={timeframe} دریافت نشد."
            )

            return None

        logger.info(
            f"📥 {symbol} | "
            f"TF={timeframe} | "
            f"resolution={resolution} | "
            f"کندل دریافت شد: {len(df)}"
        )

        return df

    except requests.exceptions.Timeout:

        logger.error(
            f"⏱️ Timeout در دریافت "
            f"{symbol} | TF={timeframe}"
        )

        return None

    except requests.exceptions.ConnectionError as e:

        logger.error(
            f"🌐 خطای اتصال شبکه در دریافت "
            f"{symbol} | TF={timeframe}: {e}"
        )

        return None

    except requests.exceptions.RequestException as e:

        logger.error(
            f"❌ خطای شبکه در دریافت "
            f"{symbol} | TF={timeframe}: {e}"
        )

        return None

    except Exception as e:

        logger.exception(
            f"❌ خطای غیرمنتظره در دریافت "
            f"{symbol} | TF={timeframe}: {e}"
        )

        return None
    except requests.exceptions.RequestException as e:

        logger.error(
            f"❌ خطای شبکه در دریافت "
            f"{symbol} | TF={timeframe}: {e}"
        )

        return None

    except Exception as e:

        logger.error(
            f"❌ خطای پردازش داده "
            f"{symbol} | TF={timeframe}: {e}"
        )

        return None


def get_kucoin_data(symbol, timeframe, limit=300):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None


def calculate_ut_bot_1h_live(df, sensitivity=3, atr_period=10):
    """
    UT Bot Alerts - منطق نزدیک به TradingView
    تنظیمات:
        Key Value / Sensitivity = 3
        ATR Period = 10

    نکته مهم:
    سیگنال BUY/SELL فقط بر اساس کندل بسته‌شده معتبر است.
    """

    df = df.copy()

    if len(df) < max(atr_period + 20, 50):
        df['signal'] = 'HOLD'
        df['ATR'] = 0.0
        df['TrailingStop'] = 0.0
        df['UT_Position'] = 0
        df['UT_Bias'] = 'NEUTRAL'
        return df

    # =========================================================
    # 1) ATR - همان منطق Wilder ATR که UT Bot استفاده می‌کند
    # =========================================================

    df['ATR'] = ta.volatility.average_true_range(
        high=df['high'],
        low=df['low'],
        close=df['close'],
        window=atr_period,
        fillna=False
    )

    # Key Value = 3
    nLoss = sensitivity * df['ATR']

    close_prices = df['close'].astype(float).values

    trailing_stop = [0.0] * len(df)
    position = [0] * len(df)
    signals = ['HOLD'] * len(df)

    # =========================================================
    # 2) UT Bot Trailing Stop
    # منطق اصلی UT Bot
    # =========================================================

    for i in range(1, len(df)):

        prev_stop = trailing_stop[i - 1]

        current_close = close_prices[i]
        previous_close = close_prices[i - 1]

        current_loss = float(nLoss.iloc[i])

        if pd.isna(current_loss) or current_loss <= 0:
            trailing_stop[i] = prev_stop
            position[i] = position[i - 1]
            continue

        # -----------------------------------------------------
        # منطق اصلی xATRTrailingStop در UT Bot
        # -----------------------------------------------------

        if current_close > prev_stop and previous_close > prev_stop:

            trailing_stop[i] = max(
                prev_stop,
                current_close - current_loss
            )

        elif current_close < prev_stop and previous_close < prev_stop:

            trailing_stop[i] = min(
                prev_stop,
                current_close + current_loss
            )

        elif current_close > prev_stop:

            trailing_stop[i] = current_close - current_loss

        else:

            trailing_stop[i] = current_close + current_loss

        # -----------------------------------------------------
        # Position در UT Bot
        # -----------------------------------------------------

        if (
            previous_close < prev_stop
            and current_close > prev_stop
        ):
            position[i] = 1

        elif (
            previous_close > prev_stop
            and current_close < prev_stop
        ):
            position[i] = -1

        else:
            position[i] = position[i - 1]

        # -----------------------------------------------------
        # BUY / SELL
        #
        # در UT Bot:
        #
        # BUY  = کراس Close به بالای Trailing Stop
        # SELL = کراس Close به پایین Trailing Stop
        # -----------------------------------------------------

        if (
            previous_close <= prev_stop
            and current_close > trailing_stop[i]
        ):
            signals[i] = 'BUY'

        elif (
            previous_close >= prev_stop
            and current_close < trailing_stop[i]
        ):
            signals[i] = 'SELL'

        else:
            signals[i] = 'HOLD'

    df['TrailingStop'] = trailing_stop
    df['UT_Position'] = position
    df['signal'] = signals

    # =========================================================
    # 3) وضعیت کلی UT Bot
    # =========================================================

    df['UT_Bias'] = 'NEUTRAL'

    df.loc[
        df['close'] > df['TrailingStop'],
        'UT_Bias'
    ] = 'BULLISH'

    df.loc[
        df['close'] < df['TrailingStop'],
        'UT_Bias'
    ] = 'BEARISH'

    # =========================================================
    # 4) اطلاعات کمکی
    # =========================================================

    df['Volume_MA'] = df['volume'].rolling(window=20).mean()

    df['RSI'] = ta.momentum.rsi(
        close=df['close'],
        window=14
    )

    return df

def estimate_target_time(entry_price, target_price, atr_value, timeframe_hours=1):
    if entry_price <= 0 or target_price <= entry_price or atr_value <= 0:
        return 1, timeframe_hours, timeframe_hours / 24

    # محاسبه فاصله تا تارگت بر اساس ATR یا نوسان
    distance = abs(target_price - entry_price)

    # تخمین تعداد کندل‌ها بر اساس فاصله تقسیم بر اندازه نوسان (ATR)
    estimated_candles = distance / atr_value
    estimated_candles = max(1.0, estimated_candles)

    hours = estimated_candles * timeframe_hours
    days = hours / 24

    return estimated_candles, hours, days



def simulate_oco_trade(symbol, current_price, atr_value, dollar_price, df):
    coin_name = symbol.split('/')[0]
    recent_low = df['low'].iloc[-21:-1].min()

    stop_raw = min(current_price - (3.0 * atr_value), recent_low * 0.99)
    risk_amount = current_price - stop_raw
    target_raw = current_price + (risk_amount * 2.0)

    if stop_raw <= 0:
        stop_raw = current_price * 0.95

    price_in_toman = current_price * dollar_price
    target_in_toman = target_raw * dollar_price
    stop_in_toman = stop_raw * dollar_price

    toman_entry = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"
    toman_target = f"{target_in_toman:,.2f}" if target_in_toman < 100 else f"{int(target_in_toman):,}"
    toman_stop = f"{stop_in_toman:,.2f}" if stop_in_toman < 100 else f"{int(stop_in_toman):,}"

    subject = f"🎯 [خرید فوری] {coin_name}"
    title = f"🟢 سیگنال ورود به پوزیشن: {coin_name}"

    rows_data = [
        ("نام ارز دیجیتال", coin_name),
        ("قیمت خرید (تومان)", f"{toman_entry} تومان"),
        ("قیمت خرید (دلار)", f"${current_price:.5f}"),
        ("حد سود / تارگت (تومان)", f"{toman_target} تومان"),
        ("حد ضرر / استاپ (تومان)", f"{toman_stop} تومان")
    ]
    send_beautiful_email(subject, title, "#10b981", rows_data)
    return price_in_toman, target_in_toman, stop_in_toman


def simulate_sell_trade(symbol, current_price, dollar_price, reason="سیگنال اندیکاتور"):
    coin_name = symbol.split('/')[0]
    price_in_toman = current_price * dollar_price
    toman_price = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

    # 🔴 اضافه کردن لاگ به ترمینال قبل از ارسال ایمیل برای شفافیت پایش
    logger.warning(f"📉 [خروج فرضی] ماشه خروج برای {symbol} چکانده شد! قیمت: {toman_price} تومان | دلیل: {reason}")

    subject = f"🚨 [خروج فوری] {coin_name}"
    title = f"🔴 سیگنال فروش و خروج: {coin_name}"

    rows_data = [
        ("نام ارز دیجیتال", coin_name),
        ("قیمت فروش (دلار)", f"${current_price:.5f}"),
        ("قیمت فروش (تومان)", f"{toman_price} تومان"),
        ("دلیل خروج از معامله", reason)
    ]
    send_beautiful_email(subject, title, "#ef4444", rows_data)


def place_buy_order_and_notify(symbol, price_toman, budget_toman):
    global daily_trade_count, PAPER_TRADING
    coin_name = symbol.split('/')[0].lower()

    if not check_daily_limits():
        logger.warning("🚫 محدودیت تعداد معاملات روزانه تکمیل شده است.")
        return False, None

    # ۱. دریافت موجودی زنده ولت قبل از خرید
    total_balance = get_nobitex_wallet_balance()
    logger.info(f"🔍 [بررسی ولت قبل از خرید] موجودی دریافت شده توسط ربات: {total_balance} تومان")

    # ۲. تعیین بودجه امن
    if total_balance > 0:
        calculated_budget = total_balance * (RISK_PERCENT / 100)
        final_budget = max(calculated_budget, budget_toman)

        if final_budget >= total_balance * 0.95:
            safe_budget_toman = total_balance * 0.95
        else:
            safe_budget_toman = final_budget
    else:
        logger.warning("⚠️ موجودی ولت توسط صرافی ۰ یا نامعتبر برگشت! استفاده از بودجه پیش‌فرض.")
        safe_budget_toman = budget_toman

    # ۳. دریافت قیمت زنده نوبیتکس
    live_price_toman = get_nobitex_live_price(coin_name)

    # ✅ اصلاح منطق گپ: قیمت خرید لیمیت را ۰.۲٪ بالاتر می‌گذاریم تا آنی پر شود
    simulated_price_toman = live_price_toman * 1.002
    simulated_price_rial = int(simulated_price_toman * 10)

    # ✅ رفع باگ موجودی کافی نیست: محاسبه تعداد توکن بر اساس قیمت نهایی ارسال شده (نه قیمت لایو)
    calculated_amount = safe_budget_toman / simulated_price_toman
    string_amount = f"{calculated_amount:.6f}"

    budget_rial = int(safe_budget_toman * 10)
    logger.info(
        f"🔥 [ورود مارکت آنی] درخواست خرید {coin_name.upper()} | بودجه ارسالی: {int(safe_budget_toman)} تومان | قیمت لیمیت: {int(simulated_price_toman):,} تومان"
    )

    if PAPER_TRADING:
        logger.info(f"✨ [Paper Trading] خرید فرضی شبیه‌سازی شد.")
        daily_trade_count += 1
        return True, "mock_order_id"

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    payload = {
        "type": "buy",
        "execution": "market",
        "srcCurrency": coin_name.lower(),
        "dstCurrency": "rls",
        "amount": string_amount,
        "price": f"{simulated_price_rial}"
    }

    res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)

    if res and res.get("status") == "ok":
        order_id = res.get("order", {}).get("id")
        logger.info(f"🟢 خرید مارکت با موفقیت ثبت شد. شناسه اردر: {order_id}.")
        daily_trade_count += 1

        # ✅ ارسال ایمیل بر اساس قیمت خرید واقعی تنظیم‌شده
        send_nobitex_order_email(coin_name, simulated_price_toman, safe_budget_toman, calculated_amount)
        return True, order_id
    else:
        if res:
            error_code = res.get("code", "NO_CODE")
            error_msg = res.get("message", "خطای ناشناخته")
            full_error_details = f"[{error_code}] {error_msg}"
        else:
            full_error_details = "عدم پاسخ صرافی (تایم‌اوت یا قطع ارتباط)"

        logger.error(f"❌ خطای ثبت سفارش خرید مارکت: {full_error_details} | پاسخ کامل سرور: {res}")
        send_nobitex_error_email(coin_name, "خرید مارکت آنی", full_error_details)
        return False, None

def get_nobitex_order_matched_amount(order_id):
    if PAPER_TRADING:
        return 0.05

    url = "https://apiv2.nobitex.ir/market/orders/status"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    for _ in range(2):
        res = _send_request_with_retry("POST", url, headers=headers, json_data={"id": order_id})
        if res and res.get("status") == "ok":
            order_info = res["order"]
            matched_qty = float(order_info.get("matchedAmount", 0.0))
            if matched_qty > 0:
                return matched_qty
        time.sleep(5)
    return 0.0


def place_nobitex_oco_sell_order(symbol, quantity, target_toman, stop_toman):
    coin_name = symbol.split('/')[0].lower()

    if PAPER_TRADING:
        logger.info(f"🛡️ [Paper Trading] سفارش OCO فرضی برای {coin_name.upper()} ثبت شد.")
        send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
        return True

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    target_rial = f"{target_toman:.2f}"
    stop_rial = f"{stop_toman:.2f}"

    # قیمت نهایی فروش در صورت فعال شدن استاپ لاس (کمی پایین‌تر جهت پر شدن قطعی)
    stop_limit_toman = stop_toman * 0.99
    stop_limit_str = f"{stop_limit_toman:.2f}"

    # 💎 اصلاح فرمت مقدار اعشار به صورت پویا (حذف صفرهای اضافی و پشتیبانی تا ۸ رقم اعشار برای ارزهای سنگین)
    string_amount = f"{quantity:.8f}".rstrip('0').rstrip('.')

    # 🚀 اصلاح اصلی: تغییر execution به oco جهت فعال‌سازی همزمان حد سود و ضرر
    payload = {
        "type": "sell",
        "execution": "oco",
        "srcCurrency": coin_name,
        "dstCurrency": "rls",  # دقت کن: نوبیتکس در برخی APIها قیمت را بر اساس ارز مقصد (ریال/تومان) می‌خواهد
        "amount": string_amount,
        "price": target_rial,
        "stopPrice": stop_rial,
        "stopLimitPrice": stop_limit_str
    }

    for attempt in range(4):
        res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
        if res and res.get("status") == "ok":
            logger.info(f"🛡️ سفارش OCO واقعی با موفقیت قفل شد. شناسه: {res.get('order', {}).get('id')}")
            send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
            return True

        error_msg = res.get("message", "خطای ناشناخته") if res else "عدم پاسخ صرافی"
        logger.warning(f"⚠️ تلاش مجدد برای OCO (تلاش {attempt + 1}/4) | علت خطا: {error_msg} | پاسخ: {res}")
        time.sleep(3)

    logger.critical("🚨 ثبت OCO ناموفق بود! فعال‌سازی سفارش حد ضرر اضطراری تکی...")

    # 🚑 اصلاح بخش اضطراری: در صورت شکست OCO، یک استاپ‌لیمیت فروش تکی ثبت می‌شود
    backup_payload = {
        "type": "sell",
        "execution": "stop_limit",
        "srcCurrency": coin_name,
        "dstCurrency": "rls",
        "amount": string_amount,
        "price": f"{stop_limit_str}",  # قیمتی که روی آن می‌فروشد
        "stopPrice": f"{stop_rial}"  # ماشه‌ای که با رسیدن به آن اردر فعال می‌شود
    }
    _send_request_with_retry("POST", url, headers=headers, json_data=backup_payload)

    send_nobitex_error_email(coin_name, "فروش OCO (خطای مداوم)",
                             "سیستم به سفارش استاپ لیمیت اضطراری جایگزین سوییچ کرد.")
    return False

def update_drawdown_performance(current_total_balance):
    global max_peak_balance
    if current_total_balance > max_peak_balance:
        max_peak_balance = current_total_balance

    drawdown = ((max_peak_balance - current_total_balance) / max_peak_balance) * 100 if max_peak_balance > 0 else 0.0
    logger.info(
        f"📊 کارنامه عملکرد مالی | موجودی فعلی: {int(current_total_balance):,} تومان | حداکثر افت حساب: {drawdown:.2f}%")


def send_nobitex_order_email(coin_name, toman_price, budget_toman, quantity):
    subject = f"🛒 [سفارش نوبیتکس] خرید {coin_name.upper()}"
    title = f"🔵 سفارش خرید مارکت در نوبیتکس ثبت شد"
    rows_data = [
        ("نام ارز دیجیتال", coin_name.upper()),
        ("قیمت تقریبی هر واحد (تومان)", f"{int(toman_price):,} Toman"),
        ("تعداد تقریبی خرید", f"{quantity:.4f} {coin_name.upper()}"),
        ("کل بودجه مصرفی (تومان)", f"{int(budget_toman):,} Toman")
    ]
    send_beautiful_email(subject, title, "#1e40af", rows_data)


def send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman):
    subject = f"🛡️ [محافظت OCO] قفل اردر فروش {coin_name.upper()}"
    title = f"🟢 سپرهای محافظتی OCO با موفقیت ثبت شد"
    rows_data = [
        ("نام ارز دیجیتال", coin_name.upper()),
        ("تعداد دقیق و خالص برای فروش", f"{quantity:.4f} {coin_name.upper()}"),
        ("حد سود تعیین شده (تومان)", f"{int(target_toman):,} Toman"),
        ("حد ضرر تعیین شده (تومان)", f"{int(stop_toman):,} Toman")
    ]
    send_beautiful_email(subject, title, "#10b981", rows_data)


def send_nobitex_error_email(coin_name, operation_type, error_message):
    subject = f"⚠️ [خطای نوبیتکس] عدم انجام {operation_type} {coin_name.upper()}"
    title = f"🔴 خطا در عملیات {operation_type} صرافی"
    rows_data = [
        ("نام ارز دیجیتال", coin_name.upper()),
        ("نوع عملیات ناموفق", operation_type),
        ("علت/متن خطا", f"<span style='color: #ef4444; font-weight: bold;'>{error_message}</span>")
    ]
    send_beautiful_email(subject, title, "#ef4444", rows_data)


def maxhad(symbol):
    url = "https://apiv2.nobitex.ir/market/stats"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}
    stat_name = "dayHigh"
    #print(symbol)
    # پارامترها به جای بدنه، به صورت Query Parameters ارسال می‌شوند
    params = {
        "srcCurrency": symbol.upper(),  # معمولاً حروف کوچک می‌خواهد (مثل eth)
        "dstCurrency": "irt",  # اگر بازار تتری است یا ریل (irt)
    }

    response_data = _send_request_with_retry("GET", url, headers=headers, params=params)

    if response_data and isinstance(response_data, dict):
        # بررسی اینکه آیا درخواست موفق بوده یا ارور داده
        if response_data.get("status") == "ok":
            stats_data = response_data.get("stats",{})  # بسته به ساختار JSON پاسخ نوبیتکس
            #print(stats_data)
            market_key = f"{symbol.upper()}-irt"

            market_info = stats_data.get(market_key, {})
            # استخراج مقدار مورد نظر (پیش‌فرض روی dayHigh)
            value = market_info.get(stat_name)

            if value is not None:
                return float(value) / 10
        else:
            print(f"API Error: {response_data}")

    return None

def minhad(symbol):
    url = "https://apiv2.nobitex.ir/market/stats"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}
    stat_name = "dayLow"
    #print(symbol)
    # پارامترها به جای بدنه، به صورت Query Parameters ارسال می‌شوند
    params = {
        "srcCurrency": symbol.upper(),  # معمولاً حروف کوچک می‌خواهد (مثل eth)
        "dstCurrency": "irt",  # اگر بازار تتری است یا ریل (irt)
    }

    response_data = _send_request_with_retry("GET", url, headers=headers, params=params)

    if response_data and isinstance(response_data, dict):
        # بررسی اینکه آیا درخواست موفق بوده یا ارور داده
        if response_data.get("status") == "ok":
            stats_data = response_data.get("stats",{})  # بسته به ساختار JSON پاسخ نوبیتکس
            #print(stats_data)
            market_key = f"{symbol.upper()}-irt"

            market_info = stats_data.get(market_key, {})
            # استخراج مقدار مورد نظر (پیش‌فرض روی dayHigh)
            value = market_info.get(stat_name)

            if value is not None:
                return float(value) / 10
        else:
            print(f"API Error: {response_data}")

    return None

def monitor_market():
    global target_day

    logger.info("🔥 ربات نوسان‌گیری با استراتژی چندتایم‌فریمه فعال شد...")

    symbols = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "NEAR/USDT",
        "SUI/USDT", "TRX/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
        "LINK/USDT", "UNI/USDT", "LTC/USDT", "BCH/USDT",
        "POL/USDT", "ALGO/USDT", "XLM/USDT", "HBAR/USDT",
        "GRT/USDT", "ANKR/USDT", "HMSTR/USDT", "DOGS/USDT",
        "TNSR/USDT", "2Z/USDT", "RENDER/USDT", "APE/USDT", "DYDX/USDT",
        "BASED/USDT",
        "ONE/USDT", "BICO/USDT", "NOT/USDT", "KAITO/USDT",
        "PUMP/USDT", "BARD/USDT", "PROM/USDT", "LA/USDT",
        "ZAMA/USDT", "HOME/USDT"
    ]

    DB_FILE = "live_signals_v2.json"

    last_signals = load_last_signals(symbols)

    last_nobitex_update = 0
    dollar_price = None
    current_wallet = 0.0
    last_report_date = None

    while True:

        current_now = datetime.now()

        current_time_str = jdatetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        print(
            f"\n🔄 --- چرخه پایش آنی بازار "
            f"(چندتایم‌فریمه): {current_time_str} ---"
        )

        # ============================================================
        # گزارش روزانه
        # ============================================================

        if (
            current_now.hour == 0
            and current_now.minute == 0
            and last_report_date != current_now.date()
        ):
            try:
                generate_daily_report(file_path=DB_FILE)

            except Exception as e:
                logger.error(
                    f"⚠️ خطا در تولید گزارش روزانه: {e}"
                )

            last_report_date = current_now.date()

        # ============================================================
        # بروزرسانی قیمت تتر و موجودی
        # ============================================================

        current_timestamp = time.time()

        if (
            current_timestamp - last_nobitex_update > 600
            or dollar_price is None
        ):

            logger.info(
                "🔄 در حال به‌روزرسانی اطلاعات عمومی "
                "از نوبیتکس (قیمت تتر و موجودی)..."
            )

            dollar_price = get_iran_dollar_price()

            current_wallet = get_nobitex_wallet_balance()

            update_drawdown_performance(current_wallet)

            last_nobitex_update = current_timestamp

        # ============================================================
        # اگر قیمت تتر نداریم، چرخه رد شود
        # ============================================================

        if dollar_price is None:

            logger.warning(
                "⚠️ به دلیل عدم دسترسی به قیمت زنده تتر، "
                "این چرخه معاملاتی رد می‌شود."
            )

            print("💤 استراحت ۶۰ ثانیه‌ای تا چرخه بعدی...")

            time.sleep(60)

            continue

        print(
            f"  قیمت دلار (تومان): {dollar_price:,} "
            f" موجودی حساب شما : {current_wallet:.2f}"
        )

        print(
            "---------------------------------------------------------------------------------"
        )

        # ============================================================
        # محاسبه تعداد پوزیشن‌های باز
        # ============================================================

        open_positions_count = sum(
            1
            for sym in symbols
            if (
                isinstance(last_signals.get(sym), dict)
                and last_signals[sym].get("signal") == "BUY"
            )
        )

        log_lines_buffer = []

        # ============================================================
        # شروع بررسی ارزها
        # ============================================================

        for symbol in symbols:

            coin_name_lower = symbol.split("/")[0].lower()

            try:

                # ====================================================
                # دریافت 1H
                # ====================================================

                df = get_nobitex_data(
                    symbol,
                    timeframe="1h",
                    limit=300
                )

                if (
                    df is None
                    or df.empty
                    or len(df) < 60
                ):
                    continue

                # ====================================================
                # UT BOT 3/10 روی 1H
                # ====================================================

                df = calculate_ut_bot_1h_live(
                    df,
                    sensitivity=3,
                    atr_period=10
                )

                # ====================================================
                # کندل بسته‌شده
                #
                # [-1] = کندل در حال تشکیل
                # [-2] = آخرین کندل کاملاً بسته‌شده
                # ====================================================

                live_row = df.iloc[-1]

                signal_row = df.iloc[-2]

                current_price = float(
                    live_row["close"]
                )

                current_signal = signal_row["signal"]

                atr_value = float(
                    signal_row["ATR"]
                )

                ut_bias_1h = signal_row["UT_Bias"]

                signal_candle_timestamp = signal_row["timestamp"]

                # تبدیل زمان سیگنال به رشته قابل ذخیره
                try:
                    signal_time_str = str(
                        signal_candle_timestamp
                    )
                except Exception:
                    signal_time_str = "نامشخص"

                # ====================================================
                # دریافت Daily
                # ====================================================

                df_1d = get_nobitex_data(
                    symbol,
                    timeframe="1d",
                    limit=150
                )

                daily_bias = "NEUTRAL"

                if (
                    df_1d is not None
                    and not df_1d.empty
                    and len(df_1d) >= 60
                ):

                    df_1d = calculate_ut_bot_1h_live(
                        df_1d,
                        sensitivity=3,
                        atr_period=10
                    )

                    daily_row = df_1d.iloc[-2]

                    daily_bias = daily_row["UT_Bias"]

                else:

                    logger.warning(
                        f"⚠️ [{symbol}] اطلاعات Daily کافی نیست."
                    )

                # ====================================================
                # قیمت واقعی نوبیتکس
                # ====================================================

                nobitex_real_price = get_nobitex_live_price(
                    coin_name_lower
                )

                if nobitex_real_price is not None:

                    price_in_toman = float(
                        nobitex_real_price
                    )

                elif (
                    current_price is not None
                    and dollar_price is not None
                ):

                    price_in_toman = (
                        current_price * dollar_price
                    )

                else:

                    logger.warning(
                        f"⚠️ قیمت معتبر برای {symbol} "
                        f"در دسترس نیست، این نماد رد شد."
                    )

                    continue

                # ====================================================
                # فرمت قیمت
                # ====================================================

                if price_in_toman < 100:
                    toman_str = f"{price_in_toman:,.2f}"
                else:
                    toman_str = f"{int(price_in_toman):,}"

                # ====================================================
                # دریافت پوزیشن
                # ====================================================

                position = last_signals.get(symbol)

                if not isinstance(position, dict):

                    position = {
                        "signal": "HOLD",
                        "entry_price": 0.0,
                        "target_price": 0.0,
                        "stop_price": 0.0,
                        "oco_order_id": None,
                        "updated_at": current_time_str,
                        "signal_time": None,
                        "target_day": 0.0,
                        "trade_history": []
                    }

                    last_signals[symbol] = position

                # ====================================================
                # مقدار پیش‌فرض target_day
                #
                # مهم:
                # دیگر مقدار target_day ارز قبلی به این ارز منتقل نمی‌شود
                # ====================================================

                target_day = position.get(
                    "target_day",
                    0.0
                )

                # ====================================================
                # وضعیت نمایشی
                # ====================================================

                color_code = BLUE

                status_display = "HOLD"

                position_details = (
                    " | تعداد: -        "
                    "| هدف: -          "
                    "| استاپ: -         "
                    "| سود/زیان: -"
                )

                # ====================================================
                # اگر پوزیشن BUY داریم
                # ====================================================

                if position.get("signal") == "BUY":

                    color_code = GREEN

                    status_display = "BUY (OCO active)"

                    p_entry = float(
                        position.get(
                            "entry_price",
                            0
                        ) or 0
                    )

                    p_target = float(
                        position.get(
                            "target_price",
                            0
                        ) or 0
                    )

                    p_stop = float(
                        position.get(
                            "stop_price",
                            0
                        ) or 0
                    )

                    target_day = position.get(
                        "target_day",
                        0.0
                    )

                    calc_qty = (
                        BUDGET_TOMAN / p_entry
                        if p_entry > 0
                        else 0.0
                    )

                    potential_profit = (
                        (p_target - p_entry)
                        * calc_qty
                        if p_entry > 0
                        else 0.0
                    )

                    potential_loss = (
                        (p_entry - p_stop)
                        * calc_qty
                        if p_entry > 0
                        else 0.0
                    )

                    position_details = (
                        f" | تعداد: {calc_qty:<8.3f}"
                        f" | هدف: {p_target:<10,}"
                        f" | استاپ: {p_stop:<10,}"
                        f" | سود احتمالی: +{int(potential_profit):,} تومان"
                        f" | زیان احتمالی: -{int(potential_loss):,} تومان"
                        f" | بازه زمانی رسیدن به هدف: {target_day}"
                    )

                # ====================================================
                # SELL signal
                # ====================================================

                elif current_signal == "SELL":

                    color_code = RED

                    status_display = "SELL"

                # ====================================================
                # لاگ اولیه
                # ====================================================

                clean_console_line = (
                    f"📊 {symbol:<10} "
                    f"| قیمت: {toman_str:<10} تومان "
                    f"| وضعیت: {status_display:<18}"
                    f"{position_details}"
                )

                print(
                    f"{color_code}"
                    f"{clean_console_line}"
                    f"{RESET}"
                )

                print(
                    f"{color_code}"
                    f"{'-' * 84}"
                    f"{RESET}"
                )

                log_lines_buffer.append(
                    f"{clean_console_line} "
                    f"| زمان: {current_time_str}"
                )

                # ====================================================
                # مدیریت پوزیشن BUY
                # ====================================================

                if position.get("signal") == "BUY":

                    entry_time_str = position.get(
                        "updated_at",
                        "نامشخص"
                    )

                    hours_held = (
                        get_hours_since_entry(
                            entry_time_str
                        )
                        if entry_time_str != "نامشخص"
                        else 0
                    )

                    # =================================================
                    # 1. خروج زمانی
                    # =================================================

                    if hours_held >= MAX_HOLD_HOURS:

                        logger.warning(
                            f"⏰ [{symbol}] بیش از "
                            f"{MAX_HOLD_HOURS:.0f} ساعت بدون رسیدن "
                            f"به هدف/استاپ سپری شد. "
                            f"خروج به دلیل انقضای زمان."
                        )

                        # ---------------------------------------------
                        # Paper
                        # ---------------------------------------------

                        if PAPER_TRADING:

                            simulate_sell_trade(
                                symbol,
                                current_price,
                                dollar_price,
                                reason="Time Exit (Paper)"
                            )

                        now_str = jdatetime.datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )

                        exit_price = int(
                            price_in_toman
                        )

                        entry_price = int(
                            position.get(
                                "entry_price",
                                0
                            )
                        )

                        pnl_toman = (
                            exit_price - entry_price
                        )

                        past_trade = {
                            "type": (
                                "PAPER_TRADE"
                                if PAPER_TRADING
                                else "REAL_TIME_EXIT"
                            ),
                            "entry_time": position.get(
                                "updated_at",
                                "نامشخص"
                            ),
                            "signal_time": position.get(
                                "signal_time",
                                "نامشخص"
                            ),
                            "exit_time": now_str,
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "target_day": position.get(
                                "target_day"
                            ),
                            "reason": (
                                "Time Exit - "
                                "رسیدن به سقف زمانی نگهداری"
                            )
                        }

                        time_exit_rows_data = [

                            ("جفت ارز", symbol),

                            (
                                "قیمت ورود",
                                f"{entry_price:,} تومان"
                            ),

                            (
                                "قیمت خروج",
                                f"{exit_price:,} تومان"
                            ),

                            (
                                "سود/زیان تقریبی هر واحد",
                                f"{pnl_toman:,} تومان"
                            ),

                            (
                                "مدت نگهداری",
                                f"{hours_held:.1f} ساعت"
                            ),

                            (
                                "زمان سیگنال",
                                position.get(
                                    "signal_time",
                                    "نامشخص"
                                )
                            ),

                            (
                                "زمان ورود",
                                position.get(
                                    "updated_at",
                                    "نامشخص"
                                )
                            ),

                            (
                                "زمان خروج",
                                now_str
                            )
                        ]

                        last_signals[symbol] = {

                            "signal": "HOLD",

                            "entry_price": 0.0,

                            "target_price": 0.0,

                            "stop_price": 0.0,

                            "oco_order_id": None,

                            "updated_at": now_str,

                            "signal_time": None,

                            "target_day": 0.0,

                            "trade_history":
                                position.get(
                                    "trade_history",
                                    []
                                ) + [past_trade]
                        }

                        save_last_signals(
                            last_signals
                        )

                        open_positions_count = max(
                            0,
                            open_positions_count - 1
                        )

                        send_beautiful_email(

                            subject=(
                                f"⏰ [خروج زمانی] "
                                f"{symbol} پس از "
                                f"{hours_held:.1f} ساعت "
                                f"بسته شد."
                            ),

                            title=(
                                f"خروج به دلیل انقضای "
                                f"زمان برای {symbol}"
                            ),

                            type_color="#f59e0b",

                            rows_data=time_exit_rows_data
                        )

                        # بسیار مهم:
                        # بعد از خروج، این ارز در همین چرخه
                        # دیگر نباید مجدداً BUY شود.
                        continue

                    # =================================================
                    # 2. Stop Loss - Paper
                    #
                    # مهم:
                    # minhad() کاملاً حذف شد.
                    #
                    # فقط قیمت فعلی بررسی می‌شود.
                    # =================================================

                    if PAPER_TRADING:

                        if (
                            p_stop > 0
                            and price_in_toman <= p_stop
                        ):

                            logger.warning(
                                f"📉 حد ضرر فرضی برای {symbol} "
                                f"در قیمت "
                                f"{price_in_toman:,.0f} تومان "
                                f"لمس شد."
                            )

                            # قیمت استفاده‌شده برای خروج
                            # همان قیمت واقعی نوبیتکس است.
                            exit_price_toman = price_in_toman

                            simulate_sell_trade(
                                symbol,
                                current_price,
                                dollar_price,
                                reason="Stop Loss (Paper)"
                            )

                            logger.warning(
                                f"📉 [خروج فرضی] "
                                f"ماشۀ خروج برای {symbol} "
                                f"چکانده شد! "
                                f"قیمت خروج: "
                                f"{exit_price_toman:,.0f} تومان "
                                f"| دلیل: Stop Loss (Paper)"
                            )

                            now_str = (
                                jdatetime.datetime.now()
                                .strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            )

                            entry_price = int(
                                position.get(
                                    "entry_price",
                                    0
                                )
                            )

                            exit_price = int(
                                exit_price_toman
                            )

                            pnl_toman = (
                                exit_price
                                - entry_price
                            )

                            past_trade = {

                                "type": "PAPER_TRADE",

                                "entry_time":
                                    position.get(
                                        "updated_at",
                                        "نامشخص"
                                    ),

                                "signal_time":
                                    position.get(
                                        "signal_time",
                                        "نامشخص"
                                    ),

                                "exit_time":
                                    now_str,

                                "entry_price":
                                    entry_price,

                                "exit_price":
                                    exit_price,

                                "target_day":
                                    position.get(
                                        "target_day"
                                    ),

                                "reason":
                                    "Stop Loss (Paper)"
                            }

                            stop_rows_data = [

                                ("جفت ارز", symbol),

                                (
                                    "قیمت ورود",
                                    f"{entry_price:,} تومان"
                                ),

                                (
                                    "قیمت استاپ",
                                    f"{int(p_stop):,} تومان"
                                ),

                                (
                                    "قیمت خروج",
                                    f"{exit_price:,} تومان"
                                ),

                                (
                                    "سود/زیان تقریبی هر واحد",
                                    f"{pnl_toman:,} تومان"
                                ),

                                (
                                    "زمان سیگنال",
                                    position.get(
                                        "signal_time",
                                        "نامشخص"
                                    )
                                ),

                                (
                                    "زمان ورود",
                                    position.get(
                                        "updated_at",
                                        "نامشخص"
                                    )
                                ),

                                (
                                    "زمان خروج",
                                    now_str
                                )
                            ]

                            last_signals[symbol] = {

                                "signal": "HOLD",

                                "entry_price": 0.0,

                                "target_price": 0.0,

                                "stop_price": 0.0,

                                "oco_order_id": None,

                                "updated_at": now_str,

                                "signal_time": None,

                                "target_day": 0.0,

                                "trade_history":
                                    position.get(
                                        "trade_history",
                                        []
                                    ) + [past_trade]
                            }

                            save_last_signals(
                                last_signals
                            )

                            open_positions_count = max(
                                0,
                                open_positions_count - 1
                            )

                            send_beautiful_email(

                                subject=(
                                    f"📉 حد ضرر فرضی برای "
                                    f"{symbol} "
                                    f"در قیمت "
                                    f"{exit_price:,} تومان "
                                    f"لمس شد."
                                ),

                                title=(
                                    f"حد ضرر فرضی برای "
                                    f"{symbol}"
                                ),

                                type_color="#ef4444",

                                rows_data=
                                    stop_rows_data
                            )

                            # مهم:
                            # جلوگیری از ورود مجدد در همین چرخه
                            continue

                        # =================================================
                        # 3. Take Profit - Paper
                        # =================================================

                        if (
                            p_target > 0
                            and price_in_toman >= p_target
                        ):

                            logger.info(
                                f"🎯 حد سود فرضی برای {symbol} "
                                f"در قیمت "
                                f"{price_in_toman:,.0f} تومان "
                                f"لمس شد."
                            )

                            exit_price_toman = price_in_toman

                            simulate_sell_trade(
                                symbol,
                                current_price,
                                dollar_price,
                                reason="Take Profit (Paper)"
                            )

                            logger.info(
                                f"🎯 [خروج فرضی] "
                                f"{symbol} بسته شد. "
                                f"قیمت خروج: "
                                f"{exit_price_toman:,.0f} تومان"
                            )

                            now_str = (
                                jdatetime.datetime.now()
                                .strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            )

                            entry_price = int(
                                position.get(
                                    "entry_price",
                                    0
                                )
                            )

                            exit_price = int(
                                exit_price_toman
                            )

                            pnl_toman = (
                                exit_price
                                - entry_price
                            )

                            past_trade = {

                                "type": "PAPER_TRADE",

                                "entry_time":
                                    position.get(
                                        "updated_at",
                                        "نامشخص"
                                    ),

                                "signal_time":
                                    position.get(
                                        "signal_time",
                                        "نامشخص"
                                    ),

                                "exit_time":
                                    now_str,

                                "entry_price":
                                    entry_price,

                                "exit_price":
                                    exit_price,

                                "target_day":
                                    position.get(
                                        "target_day"
                                    ),

                                "reason":
                                    "Take Profit (Paper)"
                            }

                            target_rows_data = [

                                ("جفت ارز", symbol),

                                (
                                    "قیمت ورود",
                                    f"{entry_price:,} تومان"
                                ),

                                (
                                    "قیمت تارگت",
                                    f"{int(p_target):,} تومان"
                                ),

                                (
                                    "قیمت خروج",
                                    f"{exit_price:,} تومان"
                                ),

                                (
                                    "سود/زیان تقریبی هر واحد",
                                    f"{pnl_toman:,} تومان"
                                ),

                                (
                                    "زمان سیگنال",
                                    position.get(
                                        "signal_time",
                                        "نامشخص"
                                    )
                                ),

                                (
                                    "زمان ورود",
                                    position.get(
                                        "updated_at",
                                        "نامشخص"
                                    )
                                ),

                                (
                                    "زمان خروج",
                                    now_str
                                )
                            ]

                            last_signals[symbol] = {

                                "signal": "HOLD",

                                "entry_price": 0.0,

                                "target_price": 0.0,

                                "stop_price": 0.0,

                                "oco_order_id": None,

                                "updated_at": now_str,

                                "signal_time": None,

                                "target_day": 0.0,

                                "trade_history":
                                    position.get(
                                        "trade_history",
                                        []
                                    ) + [past_trade]
                            }

                            save_last_signals(
                                last_signals
                            )

                            open_positions_count = max(
                                0,
                                open_positions_count - 1
                            )

                            send_beautiful_email(

                                subject=(
                                    f"🎯 حد سود فرضی برای "
                                    f"{symbol} "
                                    f"در قیمت "
                                    f"{exit_price:,} تومان "
                                    f"لمس شد."
                                ),

                                title=(
                                    f"حد سود فرضی برای "
                                    f"{symbol}"
                                ),

                                type_color="#10b981",

                                rows_data=
                                    target_rows_data
                            )

                            # جلوگیری از BUY مجدد
                            # در همین چرخه
                            continue

                    # =================================================
                    # 4. مدیریت OCO واقعی
                    # =================================================

                    else:

                        url_wallet = (
                            "https://apiv2.nobitex.ir/v2/wallets"
                        )

                        headers = {
                            "Authorization":
                                f"Token {NOBITEX_TOKEN_PUBLIC}",
                            "Content-Type":
                                "application/json"
                        }

                        res_w = _send_request_with_retry(
                            "POST",
                            url_wallet,
                            headers=headers,
                            json_data={}
                        )

                        if (
                            res_w
                            and res_w.get("status") == "ok"
                        ):

                            wallets = (
                                res_w.get(
                                    "wallets",
                                    {}
                                ) or {}
                            )

                            wallet_entry = (
                                wallets.get(
                                    coin_name_lower.upper()
                                ) or {}
                            )

                            coin_balance = float(
                                wallet_entry.get(
                                    "balance",
                                    0.0
                                )
                            )

                            entry_price = (
                                position.get(
                                    "entry_price"
                                ) or 1
                            )

                            expected_quantity = (
                                BUDGET_TOMAN
                                / entry_price
                            )

                            if coin_balance < (
                                expected_quantity * 0.05
                            ):

                                logger.info(
                                    f"🎉 [خروج موفق OCO] "
                                    f"اردر OCO ارز {symbol} "
                                    f"در صرافی اجرا و بسته شد."
                                )

                                now_str = (
                                    jdatetime.datetime.now()
                                    .strftime(
                                        "%Y-%m-%d %H:%M:%S"
                                    )
                                )

                                entry_price_val = int(
                                    position.get(
                                        "entry_price",
                                        0
                                    )
                                )

                                exit_price_val = int(
                                    price_in_toman
                                )

                                pnl_toman = (
                                    exit_price_val
                                    - entry_price_val
                                )

                                was_profit = (
                                    pnl_toman >= 0
                                )

                                past_trade = {

                                    "type":
                                        "REAL_OCO_TRADE",

                                    "entry_time":
                                        position.get(
                                            "updated_at",
                                            "نامشخص"
                                        ),

                                    "signal_time":
                                        position.get(
                                            "signal_time",
                                            "نامشخص"
                                        ),

                                    "exit_time":
                                        now_str,

                                    "entry_price":
                                        entry_price_val,

                                    "exit_price":
                                        exit_price_val,

                                    "target_day":
                                        position.get(
                                            "target_day"
                                        ),

                                    "reason":
                                        "اجرای حد سود یا "
                                        "حد ضرر OCO در "
                                        "صرافی نوبیتکس"
                                }

                                real_exit_rows_data = [

                                    (
                                        "جفت ارز",
                                        symbol
                                    ),

                                    (
                                        "قیمت ورود",
                                        f"{entry_price_val:,} تومان"
                                    ),

                                    (
                                        "قیمت خروج",
                                        f"{exit_price_val:,} تومان"
                                    ),

                                    (
                                        "سود/زیان تقریبی "
                                        "هر واحد",
                                        f"{pnl_toman:,} تومان"
                                    ),

                                    (
                                        "زمان سیگنال",
                                        position.get(
                                            "signal_time",
                                            "نامشخص"
                                        )
                                    ),

                                    (
                                        "زمان ورود",
                                        position.get(
                                            "updated_at",
                                            "نامشخص"
                                        )
                                    ),

                                    (
                                        "زمان خروج",
                                        now_str
                                    ),

                                    (
                                        "نوع خروج",
                                        "اجرای OCO واقعی "
                                        "در نوبیتکس"
                                    )
                                ]

                                last_signals[symbol] = {

                                    "signal": "HOLD",

                                    "entry_price": 0.0,

                                    "target_price": 0.0,

                                    "stop_price": 0.0,

                                    "oco_order_id": None,

                                    "updated_at":
                                        now_str,

                                    "signal_time":
                                        None,

                                    "target_day": 0.0,

                                    "trade_history":
                                        position.get(
                                            "trade_history",
                                            []
                                        ) + [past_trade]
                                }

                                save_last_signals(
                                    last_signals
                                )

                                open_positions_count = max(
                                    0,
                                    open_positions_count - 1
                                )

                                send_beautiful_email(

                                    subject=(
                                        f"{'🎯' if was_profit else '📉'} "
                                        f"[خروج واقعی OCO] "
                                        f"{symbol} "
                                        f"با قیمت "
                                        f"{exit_price_val:,} "
                                        f"تومان بسته شد."
                                    ),

                                    title=(
                                        f"خروج واقعی از پوزیشن "
                                        f"{symbol}"
                                    ),

                                    type_color=(
                                        "#10b981"
                                        if was_profit
                                        else "#ef4444"
                                    ),

                                    rows_data=
                                        real_exit_rows_data
                                )

                                # جلوگیری از ورود مجدد
                                # در همین چرخه
                                continue

                # ====================================================
                # صدور BUY جدید
                #
                # اینجا فقط یک بلوک خرید داریم.
                # بلوک خرید تکراری حذف شده.
                # ====================================================

                if (
                    current_signal == "BUY"
                    and position.get("signal") != "BUY"
                ):

                    # =================================================
                    # Daily Filter
                    # =================================================

                    if daily_bias != "BULLISH":

                        logger.warning(
                            f"🚫 [{symbol}] BUY رد شد | "
                            f"UT Bot 1H = BUY ولی "
                            f"Daily = {daily_bias}"
                        )

                        continue

                    # =================================================
                    # 1H Bias
                    # =================================================

                    if ut_bias_1h != "BULLISH":

                        logger.warning(
                            f"🚫 [{symbol}] BUY رد شد | "
                            f"سیگنال 1H BUY است ولی "
                            f"Bias هنوز صعودی نیست."
                        )

                        continue

                    # =================================================
                    # جلوگیری از خرید اضافه
                    # =================================================

                    logger.info(
                        f"🟢 [{symbol}] BUY تأیید شد | "
                        f"UT Bot 1H = BUY | "
                        f"UT Bot 1H Bias = BULLISH | "
                        f"UT Bot 1D Bias = BULLISH | "
                        f"UT 3/10"
                    )

                    if (
                        open_positions_count
                        >= MAX_OPEN_POSITIONS
                    ):

                        logger.warning(
                            f"⚠️ سیگنال خرید {symbol} رد شد. "
                            f"سقف پوزیشن‌های باز "
                            f"({MAX_OPEN_POSITIONS}) پر است."
                        )

                        continue

                    # =================================================
                    # قیمت تتر لحظه خرید
                    # =================================================

                    dollar_price_now = (
                        get_iran_dollar_price()
                    )

                    if dollar_price_now is None:

                        logger.error(
                            f"❌ خرید {symbol} به دلیل "
                            f"قطع ناگهانی شبکه در لحظه "
                            f"دریافت قیمت تتر لغو شد."
                        )

                        continue

                    dollar_price = (
                        dollar_price_now
                    )

                    # =================================================
                    # Entry / Target / Stop
                    # =================================================

                    t_entry, t_target, t_stop = (
                        simulate_oco_trade(
                            symbol,
                            current_price,
                            atr_value,
                            dollar_price,
                            df
                        )
                    )

                    # =================================================
                    # تخمین زمان رسیدن به تارگت
                    # =================================================

                    result = estimate_target_time(
                        t_entry,
                        t_target,
                        atr_value * dollar_price,
                        1
                    )

                    eta_str = "نامشخص"

                    if result:

                        candles, hours, days = result

                        eta_str = (
                            f"{days:.1f} روز "
                            f"({hours:.1f} ساعت / "
                            f"~{candles:.1f} کندل)"
                        )

                        logger.info(
                            f"⏳ زمان تقریبی رسیدن به تارگت "
                            f"برای {symbol}: {eta_str}"
                        )

                    print(
                        f"{GREEN}"
                        f"⏳ [{symbol}] زمان تقریبی رسیدن "
                        f"به هدف: {eta_str}"
                        f"{RESET}"
                    )

                    # =================================================
                    # درصد سود و زیان
                    # =================================================

                    profit_pct = (
                        (t_target - t_entry)
                        / t_entry
                        if t_entry > 0
                        else 0.0
                    )

                    loss_pct = (
                        (t_entry - t_stop)
                        / t_entry
                        if t_entry > 0
                        else 0.0
                    )

                    final_target = int(
                        price_in_toman
                        * (1 + profit_pct)
                    )

                    final_stop = int(
                        price_in_toman
                        * (1 - loss_pct)
                    )

                    # =================================================
                    # ارسال خرید
                    # =================================================

                    order_success, order_id = (
                        place_buy_order_and_notify(
                            symbol,
                            price_in_toman,
                            budget_toman=BUDGET_TOMAN
                        )
                    )

                    if not order_success:

                        logger.error(
                            f"❌ خرید {symbol} انجام نشد."
                        )

                        continue

                    # =================================================
                    # Paper Trading
                    # =================================================

                    if PAPER_TRADING:

                        real_quantity = (
                            BUDGET_TOMAN
                            / (price_in_toman * 1.002)
                        )

                        logger.info(
                            f"✨ [Paper Trading] "
                            f"خرید فرضی {symbol} شبیه‌سازی شد."
                        )

                        logger.info(
                            f"🛡️ [Paper Trading] "
                            f"سفارش OCO فرضی برای "
                            f"{symbol} ثبت شد."
                        )

                    # =================================================
                    # Real Trading
                    # =================================================

                    else:

                        real_quantity = 0.0

                        logger.info(
                            f"⏳ در حال استعلام دائم وضعیت "
                            f"سفارش {order_id} از نوبیتکس..."
                        )

                        max_attempts = 60

                        attempts = 0

                        while (
                            real_quantity <= 0
                            and attempts < max_attempts
                        ):

                            attempts += 1

                            real_quantity = (
                                get_nobitex_order_matched_amount(
                                    order_id
                                )
                            )

                            if real_quantity > 0:

                                logger.info(
                                    f"✅ سفارش پس از "
                                    f"{attempts} بار تلاش "
                                    f"کاملاً پر شد."
                                )

                                break

                            time.sleep(2)

                    # =================================================
                    # اگر خرید واقعاً انجام شد
                    # =================================================

                    if real_quantity > 0:

                        # ---------------------------------------------
                        # OCO واقعی
                        # ---------------------------------------------

                        if not PAPER_TRADING:

                            logger.info(
                                f"📈 [تکمیل خرید واقعی] "
                                f"مقدار خالص معامله شده "
                                f"بعد کارمزد: "
                                f"{real_quantity:.4f}"
                            )

                            place_nobitex_oco_sell_order(
                                symbol,
                                real_quantity,
                                final_target,
                                final_stop
                            )

                        # ---------------------------------------------
                        # زمان ثبت پوزیشن
                        # ---------------------------------------------

                        now_str = (
                            jdatetime.datetime.now()
                            .strftime(
                                "%Y-%m-%d %H:%M:%S"
                            )
                        )

                        # ---------------------------------------------
                        # ذخیره پوزیشن
                        #
                        # signal_time اضافه شد.
                        # ---------------------------------------------

                        last_signals[symbol] = {

                            "signal": "BUY",

                            "entry_price": int(
                                price_in_toman * 1.002
                            ),

                            "target_price":
                                final_target,

                            "stop_price":
                                final_stop,

                            "oco_order_id":
                                order_id
                                if not PAPER_TRADING
                                else None,

                            "updated_at":
                                now_str,

                            "signal_time":
                                signal_time_str,

                            "target_day":
                                eta_str,

                            "trade_history":
                                position.get(
                                    "trade_history",
                                    []
                                )
                        }

                        save_last_signals(
                            last_signals
                        )

                        # چون اطلاعات موجودی/پوزیشن تغییر کرده
                        # در چرخه بعد دوباره Wallet بررسی می‌شود.
                        last_nobitex_update = 0

                        open_positions_count += 1

                        trade_mode = (
                            "تست فرضی (Paper)"
                            if PAPER_TRADING
                            else "معامله واقعی"
                        )

                        # ---------------------------------------------
                        # ایمیل خرید
                        # ---------------------------------------------

                        rows_data = [

                            (
                                "جفت ارز",
                                symbol
                            ),

                            (
                                "حالت معامله",
                                trade_mode
                            ),

                            (
                                "قیمت ورود",
                                f"{int(price_in_toman * 1.002):,} تومان"
                            ),

                            (
                                "تارگت OCO",
                                f"{final_target:,} تومان"
                            ),

                            (
                                "استاپ OCO",
                                f"{final_stop:,} تومان"
                            ),

                            (
                                "زمان تقریبی رسیدن به هدف",
                                eta_str
                            ),

                            (
                                "مقدار خرید",
                                f"{real_quantity:.4f}"
                            ),

                            (
                                "زمان سیگنال",
                                signal_time_str
                            ),

                            (
                                "زمان ثبت خرید",
                                now_str
                            ),

                            (
                                "تأیید Daily",
                                "UT Bot 3/10 - BULLISH"
                            ),

                            (
                                "سیگنال ورود",
                                "UT Bot 3/10 - 1H BUY"
                            )
                        ]

                        send_beautiful_email(

                            subject=(
                                f"🚀 سیگنال خرید "
                                f"{symbol} "
                                f"({trade_mode})"
                            ),

                            title=(
                                f"خرید موفقیت‌آمیز "
                                f"{symbol}"
                            ),

                            type_color="#10b981",

                            rows_data=rows_data
                        )

                    else:

                        logger.error(
                            f"❌ خطای بحرانی: سفارش "
                            f"{order_id} در نوبیتکس پر نشد! "
                            f"پوزیشن ذخیره نشد."
                        )

                # ====================================================
                # فاصله کوتاه بین ارزها
                # ====================================================

                time.sleep(0.2)

            except Exception as e:

                logger.error(
                    f"⚠️ خطا در پردازش {symbol}: {e}",
                    exc_info=True
                )

                continue

        # ============================================================
        # ذخیره لاگ چرخه
        # ============================================================

        if log_lines_buffer:

            try:

                with open(
                    "market_monitor.log",
                    "a",
                    encoding="utf-8"
                ) as log_file:

                    log_file.write(
                        "\n".join(
                            log_lines_buffer
                        ) + "\n"
                    )

            except OSError as e:

                logger.error(
                    f"⚠️ خطا در نوشتن فایل لاگ: {e}"
                )

        # ============================================================
        # پایان چرخه
        # ============================================================

        print(
            "\n💤 استراحت ۳۰۰ ثانیه‌ای "
            "تا چرخه بعدی..."
        )

        try:

            with open(
                "market_monitor.log",
                "a",
                encoding="utf-8"
            ) as log_file:

                log_file.write(
                    "\n--- چرخه بعدی پایش "
                    "در ۳۰۰ ثانیه آینده ---\n\n"
                )

        except OSError as e:

            logger.error(
                f"⚠️ خطا در نوشتن فایل لاگ: {e}"
            )

        time.sleep(300)

def generate_daily_report(file_path):
    logger.info("📊 در حال محاسبه و تولید کارنامه معاملات ۲۴ ساعت گذشته...")
    file_path = "live_signals_v2.json"  # ⚠️ حواست به ورژن نام فایل (v2 یا v3) باشد

    if not os.path.exists(file_path):
        logger.warning("⚠️ فایلی برای گزارش‌گیری یافت نشد.")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"❌ خطا در خواندن فایل دیتابیس برای گزارش‌گیری: {e}")
        return None

    now = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_trades = 0
    profitable_trades = 0
    total_profit_loss_toman = 0

    report_lines = []
    report_lines.append(f"📅 **گزارش عملکرد ربات نوسان‌گیری**")
    report_lines.append(f"⏱️ زمان تولید گزارش: {jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}")
    report_lines.append("=" * 40)

    for symbol, config in data.items():
        if not isinstance(config, dict):
            continue

        history = config.get("trade_history", [])
        for trade in history:
            exit_time_str = trade.get("exit_time", "")
            try:
                exit_time = datetime.strptime(exit_time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

            # ⏱️ فیلتر کردن معاملات ۲۴ ساعت گذشته
            if (now - exit_time).total_seconds() <= 86400:
                total_trades += 1
                entry_p = trade.get("entry_price", 0.0)
                exit_p = trade.get("exit_price", 0.0)
                trade_type = trade.get("type", "UNKNOWN")

                # محاسبه درصد سود/زیان این معامله
                if entry_p > 0:
                    pnl_percent = ((exit_p - entry_p) / entry_p) * 100
                else:
                    pnl_percent = 0.0

                # محاسبه سود یا زیان تومانی خالص روی این پوزیشن
                trade_pnl_toman = int(BUDGET_TOMAN * (pnl_percent / 100))
                total_profit_loss_toman += trade_pnl_toman

                if trade_pnl_toman > 0:
                    profitable_trades += 1
                    status_emoji = "🟢"
                else:
                    status_emoji = "🔴"

                report_lines.append(
                    f"{status_emoji} **{symbol}** ({trade_type})\n"
                    f"   📥 ورود: {int(entry_p):,} | 📤 خروج: {int(exit_p):,} تومان\n"
                    f"   📈 بازدهی: {pnl_percent:+.2f}% ({trade_pnl_toman:+,} تومان)"
                )
                report_lines.append("-" * 30)

    # 📈 محاسبه آمار کلی
    win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0.0
    pnl_color = "🟢" if total_profit_loss_toman >= 0 else "🔴"
    email_theme_color = "#10b981" if total_profit_loss_toman >= 0 else "#ef4444"

    summary_section = [
        "== 🎯 خلاصه وضعیت امروز ==",
        f"🔢 کل معاملات بسته شده: {total_trades}",
        f"✅ معاملات سودده: {profitable_trades} | ❌ معاملات زیان‌ده: {total_trades - profitable_trades}",
        f"📊 درصد موفقیت (Win Rate): {win_rate:.2f}%",
        f"{pnl_color} کل سود/زیان خالص امروز: {total_profit_loss_toman:,} تومان",
        "========================================"
    ]

    # چسباندن خلاصه به بالای گزارش جهت چاپ در ترمینال و فایل لاگ
    final_report = "\n".join(summary_section + [""] + report_lines)
    print(final_report)  # نمایش در ترمینال

    # 📝 ۱. ثبت گزارش نهایی در فایل متنی market_monitor.log
    with open("market_monitor.log", "a", encoding="utf-8") as log_file:
        log_file.write(f"\n\n=== 🕒 ثبت گزارش ۲۴ ساعته شبانه سیستم ({now.strftime('%Y-%m-%d')}) ===\n")
        log_file.write(final_report)
        log_file.write("\n======================================================\n\n")

    # 📧 ۲. ارسال ایمیل زیبا از وضعیت کارنامه شبانه
    if total_trades > 0:
        rows_data = [
            ("تعداد کل معاملات امروز", str(total_trades)),
            ("معاملات سودده (برنده)", f"🟢 {profitable_trades}"),
            ("معاملات زیان‌ده (بازنده)", f"🔴 {total_trades - profitable_trades}"),
            ("درصد موفقیت (Win Rate)", f"{win_rate:.2f}%"),
            ("سود/زیان خالص ۲۴ ساعت", f"{total_profit_loss_toman:,} تومان"),
            ("وضعیت نهایی بازدهی", "سودده" if total_profit_loss_toman >= 0 else "زیان‌ده")
        ]

        send_beautiful_email(
            subject=f"📊 کارنامه عملکرد ۲۴ ساعته ربات معاملاتی ({now.strftime('%Y-%m-%d')})",
            title=f"گزارش سود و زیان روزانه - وضعیت نهایی: {rows_data[5][1]}",
            type_color=email_theme_color,  # اگر سودده باشد سبز، در غیر این‌صورت قرمز می‌شود
            rows_data=rows_data
        )
        logger.info("📧 گزارش روزانه با موفقیت به ایمیل ارسال شد.")
    else:
        # حتی اگر معامله‌ای نبود هم یک ایمیل وضعیت جهت اطمینان از زنده بودن ربات بفرستد
        rows_data = [
            ("وضعیت معاملات", "امروز هیچ پوزیشنی بسته نشده است."),
            ("تعداد معامله", "0")
        ]
        send_beautiful_email(
            subject=f"💤 گزارش وضعیت ربات معاملاتی ({now.strftime('%Y-%m-%d')})",
            title="امروز معامله بسته‌شده‌ای وجود نداشت",
            type_color="#1e293b",  # رنگ سرمه‌ای خنثی
            rows_data=rows_data
        )
        logger.info("💤 امروز معامله بسته‌شده‌ای وجود نداشت؛ ایمیل خلاصه (ربات زنده است) ارسال شد.")

    return final_report


if __name__ == "__main__":
    monitor_market()
