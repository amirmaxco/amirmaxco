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

def get_nobitex_data( symbol, timeframe='1h', limit=300):
    # تبدیل رشته به عدد (بسیار مهم برای API نوبیتکس)
    tf_map = {'15m': 15, '1h': 60, '4h': 240, '1d': 1440}
    timeframe = tf_map.get(timeframe, 60)  # اگر تایم‌فریم ناشناخته بود، 60 دقیقه فرض کن

    src = symbol.split('/')[0].lower()
    dst = symbol.split('/')[1].lower()

    url = f"https://apiv2.nobitex.ir/market/udf/history"
    params = {
        "symbol": f"{src.upper()}{dst.upper()}",
        "resolution": timeframe,  # از مقدارِ عددی استفاده کن
        "from": int(time.time()) - (limit * timeframe * 60),  # از resolution استفاده کن
        "to": int(time.time())
    }

    response = requests.get(url, params=params)
    data = response.json()

    if data.get('s') == 'ok':
        df = pd.DataFrame({
            'timestamp': data['t'],
            'open': data['o'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c'],
            'volume': data['v']
        })
        return df
    return None


def get_kucoin_data(symbol, timeframe, limit=300):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None


def calculate_ut_bot_2h_live(df, sensitivity=4, atr_period=14):
    if len(df) < max(atr_period + 5, 50):
        df['signal'] = 'HOLD'
        df['ATR'] = 0.0
        return df

    # محاسبه ATR برای تعیین میزان نوسان و حد ضرر پویا
    df['ATR'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=atr_period)

    # محاسبه میانگین متحرک پایه (مثلا EMA 1 یا بر اساس Close به عنوان بیس Trailing Stop)
    # در استراتژی‌های UT Bot معمولا از ATR Trailing Stop استفاده می‌شود
    xATR = df['ATR'] * sensitivity

    # پیاده‌سازی منطق Trailing Stop مشابه اسکریپت‌های استاندارد چارت
    nLoss = xATR

    # محاسبه خط تریلینگ استاپ (Trailing Stop Line)
    trailing_stop = [0.0] * len(df)
    signals = ['HOLD'] * len(df)

    close_prices = df['close'].values
    high_prices = df['high'].values
    low_prices = df['low'].values

    for i in range(1, len(df)):
        prev_ts = trailing_stop[i - 1]
        curr_close = close_prices[i]
        prev_close = close_prices[i - 1]
        curr_loss = nLoss.iloc[i]

        if curr_close > prev_ts and prev_close > prev_ts:
            trailing_stop[i] = max(prev_ts, curr_close - curr_loss)
        elif curr_close < prev_ts and prev_close < prev_ts:
            trailing_stop[i] = min(prev_ts, curr_close + curr_loss)
        elif curr_close > prev_ts:
            trailing_stop[i] = curr_close - curr_loss
        else:
            trailing_stop[i] = curr_close + curr_loss

        # بررسی سیگنال‌های خرید و فروش بر اساس کراس قیمت و خط استاپ (مشابه چارت)
        # کراس به سمت بالا (سیگنال Buy)
        if close_prices[i - 1] <= trailing_stop[i - 1] and curr_close > trailing_stop[i]:
            signals[i] = 'BUY'
        # کراس به سمت پایین (سیگنال Sell)
        elif close_prices[i - 1] >= trailing_stop[i - 1] and curr_close < trailing_stop[i]:
            signals[i] = 'SELL'
        else:
            signals[i] = 'HOLD'

    df['TrailingStop'] = trailing_stop
    df['signal'] = signals

    # اندیکاتورهای کمکی برای فیلتر حجم و تاییدیه
    df['Volume_MA'] = df['volume'].rolling(window=20).mean()
    df['RSI'] = ta.momentum.rsi(close=df['close'], window=14)

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

            market_key = f"{symbol.upper()}-irt"

            market_info = stats_data.get(market_key, {})
            # استخراج مقدار مورد نظر (پیش‌فرض روی dayHigh)
            value = market_info.get(stat_name)
            value=float(value)/10
            #print(f"{value}")
            return float(value)
        else:
            print(f"API Error: {response_data}")

    return None


def monitor_market():
    global target_day
    logger.info("🔥 ربات نوسان‌گیری با استراتژی کندل ۱ ساعته (1h) فعال شد...")

    symbols = [
        "BTC/IRT", "ETH/IRT", "SOL/IRT", "AVAX/IRT", "NEAR/IRT",
        "SUI/IRT", "TRX/IRT", "XRP/IRT", "ADA/IRT", "DOGE/IRT",
        "LINK/IRT", "UNI/IRT", "LTC/IRT", "BCH/IRT", "TON/IRT",
        "POL/IRT", "ALGO/IRT", "XLM/IRT", "HBAR/IRT", "VET/IRT",
        "GRT/IRT", "STX/IRT", "ANKR/IRT", "HMSTR/IRT", "DOGS/IRT",
        "TNSR/IRT", "2Z/IRT", "RENDER/IRT", "APE/IRT", "DYDX/IRT",
        "BASED/IRT",
        "ONE/IRT", "BICO/IRT", "NOT/IRT", "KAITO/IRT", "PUMP/IRT", "BARD/IRT", "PROM/IRT", "LA/IRT", "ZAMA/IRT"
    ]

    DB_FILE = "live_signals_v2.json"
    last_signals = load_last_signals(symbols)
    last_nobitex_update = 0
    dollar_price = None
    current_wallet = 0.0
    last_report_date = None

    while True:
        current_now = datetime.now()
        current_time_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n🔄 --- چرخه پایش آنی بازار (تایم‌فریم 1h): {current_time_str} ---")

        if current_now.hour == 0 and current_now.minute == 0 and last_report_date != current_now.date():
            try:
                generate_daily_report(file_path=DB_FILE)
            except Exception as e:
                logger.error(f"⚠️ خطا در تولید گزارش روزانه: {e}")
            last_report_date = current_now.date()

        current_timestamp = time.time()
        if current_timestamp - last_nobitex_update > 600 or dollar_price is None:
            logger.info("🔄 در حال به‌روزرسانی اطلاعات عمومی از نوبیتکس (قیمت تتر و موجودی)...")
            dollar_price = get_iran_dollar_price()
            current_wallet = get_nobitex_wallet_balance()
            update_drawdown_performance(current_wallet)
            last_nobitex_update = current_timestamp

        if dollar_price is None:
            logger.warning("⚠️ به دلیل عدم دسترسی به قیمت زنده تتر، این چرخه معاملاتی رد می‌شود.")
            print("💤 استراحت ۶۰ ثانیه‌ای تا چرخه بعدی...")
            time.sleep(60)
            continue

        print(f"  قیمت دلار (تومان): {dollar_price:,}  موجودی حساب شما : {current_wallet:.2f}")

        open_positions_count = sum(
            1 for sym in symbols
            if isinstance(last_signals.get(sym), dict) and last_signals[sym].get("signal") == "BUY"
        )

        log_lines_buffer = []

        for symbol in symbols:
            coin_name_lower = symbol.split('/')[0].lower()

            try:
                df = get_nobitex_data(symbol, timeframe=timeframe, limit=300)
                if df is None or df.empty or len(df) < 25:
                    continue

                df = calculate_ut_bot_2h_live(df, sensitivity=4, atr_period=14)

                live_row = df.iloc[-1]
                signal_row = df.iloc[-2]

                current_price = live_row['close']
                current_signal = signal_row['signal']
                atr_value = signal_row['ATR']

                # ✅ رفع باگ اصلی: fallback درست به current_price * dollar_price
                nobitex_real_price = get_nobitex_live_price(coin_name_lower)
                if nobitex_real_price is not None:
                    price_in_toman = nobitex_real_price
                elif current_price is not None and dollar_price is not None:
                    price_in_toman = current_price * dollar_price
                else:
                    logger.warning(f"⚠️ قیمت معتبر برای {symbol} در دسترس نیست، این نماد رد شد.")
                    continue

                toman_str = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

                position = last_signals.get(symbol)
                if not isinstance(position, dict):
                    position = {
                        "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                        "oco_order_id": None, "updated_at": current_time_str, "target_day": 0.0, "trade_history": []
                    }
                    last_signals[symbol] = position

                color_code = BLUE
                status_display = "HOLD"
                position_details = " | تعداد: -        | هدف: -          | استاپ: -         | سود/زیان: -"

                # دریافت بالاترین قیمت ۲۴ ساعت گذشته از نوبیتکس
                maxprice = maxhad(coin_name_lower)

                if position.get("signal") == 'BUY':
                    color_code = GREEN
                    status_display = "BUY (OCO active)"

                    p_entry = position.get("entry_price", 0)
                    p_target = position.get("target_price", 0)
                    p_stop = position.get("stop_price", 0)
                    target_day = position.get("target_day", 0)

                    calc_qty = BUDGET_TOMAN / p_entry if p_entry > 0 else 0.0
                    potential_profit = (p_target - p_entry) * calc_qty if p_entry > 0 else 0.0
                    potential_loss = (p_entry - p_stop) * calc_qty if p_entry > 0 else 0.0

                    position_details = (
                        f" | تعداد: {calc_qty:<8.3f}"
                        f" | هدف: {p_target:<10,}"
                        f" | استاپ: {p_stop:<10,}"
                        f" | سود احتمالی: +{int(potential_profit):,} تومان "
                        f" | زیان احتمالی: -{int(potential_loss):,} تومان"
                        f"| بازه زمانی رسیده به هدف : {target_day}"
                    )
                elif current_signal == 'SELL':
                    color_code = RED
                    status_display = "SELL"

                plain_log_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details} | زمان: {current_time_str}  زمان تقریبی رسیدن به قیمت هدف : {target_day}"
                log_lines_buffer.append(plain_log_line)

                clean_console_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details}"
                print(f"{color_code}{clean_console_line}{RESET}")
                print(f"{color_code}{'-' * 84}{RESET}")

                # ============ مدیریت خروج پوزیشن باز ============
                if position.get("signal") == 'BUY':
                    if PAPER_TRADING:
                        # بررسی اینکه آیا قیمت لحظه‌ای یا حداکثر قیمت ۲۴ ساعت گذشته به حد ضرر یا حد سود رسیده است
                        hit_stop = price_in_toman <= position["stop_price"]
                        hit_target = price_in_toman >= position["target_price"] or (
                                    maxprice is not None and maxprice >= position["target_price"])

                        if hit_stop:
                            logger.warning(f"📉 حد ضرر فرضی برای {symbol} در قیمت {price_in_toman:,} تومان لمس شد.")
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="Stop Loss (Paper)")
                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE", "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str, "entry_price": position.get("entry_price", 0.0),
                                "exit_price": int(price_in_toman), "target_day": position.get("target_day"),
                                "reason": "Stop Loss (Paper)"
                            }
                            last_signals[symbol] = {
                                "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": now_str, "target_day": position.get("target_day"),
                                "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)

                        elif hit_target:
                            logger.info(
                                f"🎯 حد سود فرضی برای {symbol} لمس شد (قیمت لحظه‌ای: {price_in_toman:,} | اوج ۲۴ ساعته: {maxprice}).")
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="Take Profit (Paper)")
                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE", "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str, "entry_price": position.get("entry_price", 0.0),
                                "target_day": position.get("target_day"),
                                "exit_price": int(price_in_toman), "reason": "Take Profit (Paper)"
                            }
                            last_signals[symbol] = {
                                "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": now_str, "target_day": position.get("target_day"),
                                "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)
                    else:
                        url_wallet = "https://apiv2.nobitex.ir/v2/wallets"
                        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}
                        res_w = _send_request_with_retry("POST", url_wallet, headers=headers, json_data={})
                        if res_w and res_w.get("status") == "ok":
                            wallets = res_w.get("wallets", {}) or {}
                            wallet_entry = wallets.get(coin_name_lower.upper()) or {}
                            coin_balance = float(wallet_entry.get("balance", 0.0))

                            entry_price = position.get("entry_price") or 1
                            if coin_balance < (BUDGET_TOMAN / entry_price) * 0.05:
                                logger.info(
                                    f"🎉 [خروج موفق OCO] اردر OCO ارز {symbol} در صرافی با موفقیت اجرا و بسته شد.")
                                now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                past_trade = {
                                    "type": "REAL_OCO_TRADE", "entry_time": position.get("updated_at", "نامشخص"),
                                    "exit_time": now_str, "entry_price": position.get("entry_price", 0.0),
                                    "exit_price": int(price_in_toman), "target_day": position.get("target_day"),
                                    "reason": "اجرای حد سود یا حد ضرر OCO در صرافی نوبیتکس"
                                }
                                last_signals[symbol] = {
                                    "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                    "oco_order_id": None, "updated_at": now_str, "target_day": 0.0,
                                    "trade_history": position.get("trade_history", []) + [past_trade]
                                }
                                save_last_signals(last_signals)

                # ============ صدور سیگنال خرید جدید ============
                elif current_signal == 'BUY' and position.get("signal") != "BUY":
                    if open_positions_count >= MAX_OPEN_POSITIONS:
                        color_code = PINK
                        logger.warning(
                            f"{color_code}⚠️ سیگنال خرید {symbol} رد شد. سقف پوزیشن‌های باز ({MAX_OPEN_POSITIONS}) پر است.")
                        continue

                    dollar_price_now = get_iran_dollar_price()
                    if dollar_price_now is None:
                        logger.error(f"❌ خرید {symbol} به دلیل قطع ناگهانی شبکه در لحظه دریافت قیمت تتر لغو شد.")
                        continue
                    dollar_price = dollar_price_now

                    t_entry, t_target, t_stop = simulate_oco_trade(symbol, current_price, atr_value, dollar_price, df)

                    result = estimate_target_time(t_entry, t_target, atr_value * dollar_price, 1)
                    eta_str = "نامشخص"
                    if result:
                        candles, hours, days = result
                        eta_str = f"{days:.1f} روز ({hours:.1f} ساعت / ~{candles:.1f} کندل)"
                        logger.info(f"⏳ زمان تقریبی رسیدن به تارگت برای {symbol}: {eta_str}")

                    # ✅ نمایش صریح در کنسول (نه فقط لاگ فایل)
                    print(f"{GREEN}⏳ [{symbol}] زمان تقریبی رسیدن به هدف: {eta_str}{RESET}")

                    profit_pct = (t_target - t_entry) / t_entry if t_entry > 0 else 0.0
                    loss_pct = (t_entry - t_stop) / t_entry if t_entry > 0 else 0.0

                    final_target = int(price_in_toman * (1 + profit_pct))
                    final_stop = int(price_in_toman * (1 - loss_pct))

                    order_success, order_id = place_buy_order_and_notify(symbol, price_in_toman,
                                                                         budget_toman=BUDGET_TOMAN)

                    if order_success:
                        if PAPER_TRADING:
                            real_quantity = BUDGET_TOMAN / (price_in_toman * 1.002)
                            logger.info(f"🛡️ [Paper Trading] سفارش OCO فرضی برای {symbol} ثبت شد.")
                        else:
                            real_quantity = 0.0
                            logger.info(f"⏳ در حال استعلام دائم وضعیت سفارش {order_id} از نوبیتکس...")
                            max_attempts = 60
                            attempts = 0
                            while real_quantity <= 0 and attempts < max_attempts:
                                attempts += 1
                                real_quantity = get_nobitex_order_matched_amount(order_id)
                                if real_quantity > 0:
                                    logger.info(f"✅ سفارش پس از {attempts} بار تلاش کاملاً پر شد.")
                                    break
                                time.sleep(2)

                        if real_quantity > 0:
                            if not PAPER_TRADING:
                                logger.info(
                                    f"📈 [تکمیل خرید واقعی] مقدار خالص معامله شده بعد کارمزد: {real_quantity:.4f}")
                                place_nobitex_oco_sell_order(symbol, real_quantity, final_target, final_stop)

                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            last_signals[symbol] = {
                                "signal": "BUY",
                                "entry_price": int(price_in_toman * 1.002),
                                "target_price": final_target,
                                "stop_price": final_stop,
                                "oco_order_id": order_id if not PAPER_TRADING else None,
                                "updated_at": now_str,
                                "target_day": eta_str,
                                "trade_history": position.get("trade_history", [])
                            }
                            save_last_signals(last_signals)
                            last_nobitex_update = 0
                            open_positions_count += 1

                            trade_mode = "تست فرضی (Paper)" if PAPER_TRADING else "معامله واقعی"
                            rows_data = [
                                ("جفت ارز", symbol),
                                ("حالت معامله", trade_mode),
                                ("قیمت ورود", f"{int(price_in_toman * 1.002):,} تومان"),
                                ("تارگت OCO", f"{final_target:,} تومان"),
                                ("استاپ OCO", f"{final_stop:,} تومان"),
                                ("زمان تقریبی رسیدن به هدف", eta_str),
                                ("مقدار خرید", f"{real_quantity:.4f}")
                            ]
                            send_beautiful_email(
                                subject=f"🚀 سیگنال خرید {symbol} ({trade_mode})",
                                title=f"خرید موفقیت‌آمیز {symbol}",
                                type_color="#10b981",
                                rows_data=rows_data
                            )
                        else:
                            logger.error(f"❌ خطای بحرانی: سفارش {order_id} در نوبیتکس پر نشد! پوزیشن ذخیره نشد.")

                time.sleep(0.2)
            except Exception as e:
                logger.error(f"⚠️ خطا در پردازش {symbol}: {e}", exc_info=True)
                continue

        if log_lines_buffer:
            try:
                with open("market_monitor.log", "a", encoding="utf-8") as log_file:
                    log_file.write("\n".join(log_lines_buffer) + "\n")
            except OSError as e:
                logger.error(f"⚠️ خطا در نوشتن فایل لاگ: {e}")

        print(f"\n💤 استراحت ۳۰۰ ثانیه‌ای تا چرخه بعدی...")
        try:
            with open("market_monitor.log", "a", encoding="utf-8") as log_file:
                log_file.write(f"\n--- چرخه بعدی پایش در ۳۰۰ ثانیه آینده ---\n\n")
        except OSError as e:
            logger.error(f"⚠️ خطا در نوشتن فایل لاگ: {e}")

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
