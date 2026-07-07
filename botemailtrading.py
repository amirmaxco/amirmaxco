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

# ==========================================
# 🛑 تنظیمات کلیدی و لایه‌های جدید ربات 🛑
# ==========================================
PAPER_TRADING = True
RISK_PERCENT = 2.0
MAX_DAILY_TRADES = 5
MAX_OPEN_POSITIONS = 5

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
BUDGET_TOMAN = 300000

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
CC_EMAIL = ""

NOBITEX_TOKEN = "o5TJUZrJoLj7afjp3jxhYa2wixNdKI4gdX8KVtj9Htk="
NOBITEX_TOKEN_PUBLIC="af580cc838c22460b3d35078a52f14ed2e1d2237"
STATE_FILE = "bot_signals_state.json"

daily_trade_count = 0
last_reset_date = time.strftime("%Y-%m-%d")
max_peak_balance = 0.0

GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[34m"
RESET = "\033[0m"


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
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    res = _send_request_with_retry("GET", url, params={"srcCurrency": coin_name.lower(), "dstCurrency": "rls"})
    if res and res.get("status") == "ok":
        pair = f"{coin_name.upper()}-RLS"
        price_rial = res.get("stats", {}).get(pair, {}).get("latest", None)
        if price_rial:
            return float(price_rial) / 10
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
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        server.quit()
        logger.info("📧 ایمیل با موفقیت ارسال شد.")
    except Exception as e:
        logger.error(f"⚠️ خطا در ارسال ایمیل: {e}")


def get_kucoin_data(symbol, timeframe, limit=300):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None


def calculate_ut_bot_2h_live(df, sensitivity=3, atr_period=10):
    if len(df) < 25:
        df['signal'] = 'HOLD'
        return df

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df['ATR'] = ranges.max(axis=1).rolling(10).mean()

    df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['RSI'] = ta.momentum.rsi(close=df['close'], window=14)

    df['Local_Resistance'] = df['high'].shift(1).rolling(window=10).max()
    df['Volume_MA'] = df['volume'].shift(1).rolling(window=20).mean()

    df['signal'] = 'HOLD'

    for i in range(21, len(df)):
        current_price = df['close'].iloc[i]
        resistance = df['Local_Resistance'].iloc[i]
        current_volume = df['volume'].iloc[i]
        v_ma = df['Volume_MA'].iloc[i]
        rsi_val = df['RSI'].iloc[i]

        is_breakout = current_price > resistance
        is_volume_heavy = current_volume > (v_ma * 2.0)
        is_not_overbought = rsi_val < 80

        if is_breakout and is_volume_heavy and is_not_overbought:
            df.at[df.index[i], 'signal'] = 'BUY'
        elif current_price < df['low'].shift(1).rolling(window=5).min().iloc[i]:
            df.at[df.index[i], 'signal'] = 'SELL'

    return df


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

    # 🔍 لاگ حیاتی برای عیب‌یابی: اینجا مچ توکن و صرافی را می‌گیریم
    logger.info(f"🔍 [بررسی ولت قبل از خرید] موجودی دریافت شده توسط ربات: {total_balance} تومان")

    # ۲. تعیین بودجه (اگر موجودی صفر بیاید، این فرمول خراب می‌شود)
    if total_balance > 0:
        calculated_budget = total_balance * (RISK_PERCENT / 100)
        final_budget = max(calculated_budget, budget_toman)

        # گارد محافظتی: اگر بودجه متمایل به کل موجودی بود، آن را محدود کن
        if final_budget >= total_balance * 0.95:
            safe_budget_toman = total_balance * 0.95
        else:
            safe_budget_toman = final_budget
    else:
        # 🚨 اگر موجودی به هر دلیلی ۰ برگشت، بودجه امن را همان بودجه ورودی فرض میکنیم
        logger.warning("⚠️ موجودی ولت توسط صرافی ۰ یا نامعتبر برگشت! استفاده از بودجه پیش‌فرض.")
        safe_budget_toman = budget_toman

    # ۳. دریافت قیمت زنده و محاسبه پارامترهای شبیه‌ساز مارکت
    live_price_toman = get_nobitex_live_price(coin_name) or price_toman

    # قیمت خرید را ۵٪ بالاتر می‌گذاریم تا صرافی آنی در مارکت پر کند
    simulated_price_rial = int((live_price_toman * 1.05) * 10)

    # محاسبه تعداد توکن (amount) بر اساس بودجه امن تومانی
    calculated_amount = safe_budget_toman / live_price_toman
    # تبدیل به رشته با دقت ۶ رقم اعشار هماهنگ با نمونه صرافی
    string_amount = f"{calculated_amount:.6f}"

    budget_rial = int(safe_budget_toman * 10)
    logger.info(
        f"🔥 [ورود مارکت آنی] درخواست خرید {coin_name.upper()} | بودجه ارسالی: {int(safe_budget_toman)} تومان ({budget_rial} ریال)"
    )

    if PAPER_TRADING:
        logger.info(f"✨ [Paper Trading] خرید فرضی شبیه‌سازی شد.")
        daily_trade_count += 1
        return True, "mock_order_id"

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    # 🎯 پِیلود کاملاً هماهنگ با نمونه پست‌من و صرافی شما (شبیه‌ساز مارکت با ساختار Limit)
    payload = {
        "type": "buy",
        "execution": "limit",
        "srcCurrency": coin_name.lower(),
        "dstCurrency": "rls",
        "amount": string_amount,  # تعداد توکن (رشته)
        "price": f"{simulated_price_rial}"  # قیمت ریالی بالاتر از بازار (رشته)
    }

    res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)

    if res and res.get("status") == "ok":
        order_id = res.get("order", {}).get("id")
        logger.info(f"🟢 خرید مارکت با موفقیت ثبت شد. شناسه اردر: {order_id}.")
        daily_trade_count += 1
        send_nobitex_order_email(coin_name, price_toman, safe_budget_toman, calculated_amount)
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
        # ارسال ایمیل را بعد از منطق اصلی یا به صورت ناهمگام انجام بده تا ربات معطل نشود
        send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
        return True

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    target_rial = int(target_toman * 10)
    stop_rial = int(stop_toman * 10)
    stop_limit_rial = int(stop_rial * 0.995)

    payload = {
        "type": "sell",
        "execution": "stop_limit",
        "srcCurrency": coin_name,  # 💎 اصلاح شد: دیگر روی bico قفل نیست و نام ارز واقعی را می‌فرستد
        "dstCurrency": "rls",
        "amount": f"{quantity:.4f}",
        "price": f"{target_rial}",
        "stopPrice": f"{stop_rial}",
        "stopLimitPrice": f"{stop_limit_rial}"
    }

    for attempt in range(4):
        res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
        if res and res.get("status") == "ok":
            logger.info(f"🛡️ سفارش OCO با موفقیت قفل شد. شناسه: {res.get('order', {}).get('id')}")
            # ابتدا خروجی موفقیت ثبت شود، ایمیل در بک‌گراند برود
            send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
            return True

        error_msg = res.get("message", "خطای ناشناخته") if res else "عدم پاسخ صرافی"
        logger.warning(f"⚠️ تلاش مجدد برای OCO (تلاش {attempt + 1}/3) | علت خطا: {error_msg} | پاسخ: {res}")
        time.sleep(3)

    logger.critical("🚨 ثبت OCO ناموفق بود! فعال‌سازی سفارش حد ضرر اضطراری تکی...")

    backup_payload = {
        "type": "sell",
        "execution": "stop_limit",
        "srcCurrency": coin_name,
        "dstCurrency": "rls",
        "amount": f"{quantity:.4f}",
        "price": f"{stop_limit_rial}",
        "stopPrice": f"{stop_limit_rial}" if 'stop_line_rial' in locals() else f"{stop_rial}"  # تبرک امنیتی
    }
    _send_request_with_retry("POST", url, headers=headers, json_data=backup_payload)

    send_nobitex_error_email(coin_name, "فروش OCO (خطای مداوم)", "سیستم به سفارش استاپ لیمیت جایگزین سوییچ کرد.")
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


def monitor_market():
    logger.info("🔥 ربات نوسان‌گیری با استراتژی کندل ۱ ساعته (1h) فعال شد...")

    symbols = [
        # 👑 ارزهای سنگین و فوق پرحجم (بدون خطای کندل خطی)
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "NEAR/USDT",
        "SUI/USDT", "TRX/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
        "LINK/USDT", "UNI/USDT", "LTC/USDT", "BCH/USDT", "TON/USDT",

        # 🚀 ارزهای پرطرفدار و ترند بازار
        "POL/USDT", "ALGO/USDT", "XLM/USDT", "HBAR/USDT", "VET/USDT",
        "GRT/USDT", "STX/USDT", "ANKR/USDT", "HMSTR/USDT", "DOGS/USDT",
        "TNSR/USDT", "2Z/USDT", "RENDER/USDT", "APE/USDT", "DYDX/USDT",
        "BASED/USDT",

        # ⚠️ ارزهای فرعی (مراقب حجم تومانی این‌ها باش)
        "ONE/USDT", "BICO/USDT"
    ]

    last_signals = load_last_signals(symbols)
    last_nobitex_update = 0
    dollar_price = None
    current_wallet = 0.0
    last_report_date = None

    while True:
        current_now = datetime.now()
        current_time_str = current_now.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n🔄 --- چرخه پایش آنی بازار (تایم‌فریم 1h): {current_time_str} ---")

        # ⏱️ خودکارسازی گزارش روزانه: راس ساعت 05:15 صبح کارنامه ۲۴ ساعته را بفرست
        if current_now.hour == 5 and current_now.minute == 15 and last_report_date != current_now.date():
            generate_daily_report()
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

        print(f"  قیمت دلار (تومان): {dollar_price:,}")

        # شمارش پوزیشن‌های فعال قبل از شروع چرخه خرید جدید
        open_positions_count = sum(
            1 for sym in symbols
            if isinstance(last_signals.get(sym), dict) and last_signals[sym].get("signal") == "BUY"
        )

        for symbol in symbols:
            # 🟢 در ابتدای بررسی هر ارز، متغیرها را کاملاً ریست کن
            position_details = ""
            plain_log_line = ""
            color_code = BLUE
            status_display = "HOLD"

            try:
                df = get_kucoin_data(symbol, timeframe=timeframe, limit=300)
                if df is None or df.empty or len(df) < 25:
                    continue

                df = calculate_ut_bot_2h_live(df, sensitivity=3, atr_period=10)
                live_row = df.iloc[-1]
                current_price = live_row['close']
                current_signal = live_row['signal']
                atr_value = live_row['ATR']

                price_in_toman = current_price * dollar_price
                toman_str = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

                position = last_signals.get(symbol, {"signal": "HOLD", "entry_price": 0.0, "target_price": 0.0,
                                                     "stop_price": 0.0, "oco_order_id": None,
                                                     "updated_at": current_time_str, "trade_history": []})

                if isinstance(position, str):
                    position = {"signal": position, "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": current_time_str, "trade_history": []}

                # ✅ بخش تشخیص وضعیت
                if position and position.get("signal") == 'BUY':
                    color_code = GREEN
                    status_display = "BUY (OCO active)"

                    p_entry = position.get("entry_price", 0)
                    p_target = position.get("target_price", 0)
                    p_stop = position.get("stop_price", 0)

                    # محاسبات تعداد و سود/زیان احتمالی
                    calc_qty = BUDGET_TOMAN / p_entry if p_entry > 0 else 0.0
                    potential_profit = (p_target - p_entry) * calc_qty if p_entry > 0 else 0.0
                    potential_loss = (p_entry - p_stop) * calc_qty if p_entry > 0 else 0.0

                    # ساخت رشته‌های فرمت‌شده به صورت جداگانه برای جلوگیری از تداخل سینتکس
                    qty_formatted = f"{calc_qty:.3f}"
                    target_formatted = f"{p_target:,}"
                    stop_formatted = f"{p_stop:,}"

                    # ساخت ساختار جزئیات بدون تداخل فرمت‌دهی
                    position_details = (
                        f" | تعداد: {qty_formatted:<8}"
                        f" | هدف: {target_formatted:<10}"
                        f" | استاپ: {stop_formatted:<10}"
                        f" | سود احتمالی: +{int(potential_profit):,} تومان "
                        f" | زیان احتمالی: -{int(potential_loss):,} تومان"
                    )
                elif current_signal == 'SELL':
                    color_code = RED
                    status_display = "SELL"
                    position_details = " | تعداد: -        | هدف: -          | استاپ: -         | سود/زیان: -"

                else:
                    color_code = BLUE
                    status_display = "HOLD"
                    position_details = " | تعداد: -        | هدف: -          | استاپ: -         | سود/زیان: -"

                # 📄 ساخت نسخه تمیز بدون کد رنگی برای فایل لاگ
                plain_log_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details} | زمان: {current_time_str}"

                # 📝 ذخیره خودکار در فایل متنی
                with open("market_monitor.log", "a", encoding="utf-8") as log_file:
                    log_file.write(plain_log_line + "\n")

                # 📊 چاپ در ترمینال با رنگ مخصوص به خود
                print(f"{color_code}{plain_log_line}{RESET}")

                # 🟢 سناریوی اول: صادر شدن سیگنال خرید جدید
                if current_signal == 'BUY':
                    if position["signal"] == "BUY":
                        continue

                    # ✅ اصلاح دنده‌گذاری گارد محافظتی سقف پوزیشن‌های باز
                    if 'MAX_OPEN_POSITIONS' in globals() and open_positions_count >= MAX_OPEN_POSITIONS:
                        logger.warning(f"⚠️ سیگنال خرید {symbol} رد شد. سقف پوزیشن‌های باز ({MAX_OPEN_POSITIONS}) پر است.")
                        continue

                    dollar_price = get_iran_dollar_price()
                    if dollar_price is None:
                        logger.error(f"❌ خرید {symbol} به دلیل قطع ناگهانی شبکه در لحظه دریافت قیمت تتر لغو شد.")
                        continue

                    t_entry, t_target, t_stop = simulate_oco_trade(symbol, current_price, atr_value, dollar_price, df)
                    order_success, order_id = place_buy_order_and_notify(symbol, price_in_toman,
                                                                         budget_toman=BUDGET_TOMAN)

                    if order_success:
                        if PAPER_TRADING:
                            real_quantity = 0.0500
                            oco_success = True
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
                            oco_success = False

                        if real_quantity > 0:
                            final_target = int(t_target)
                            final_stop = int(t_stop)

                            if not PAPER_TRADING:
                                logger.info(f"📈 [تکمیل خرید واقعی] مقدار خالص معامله شده بعد کارمزد: {real_quantity:.4f}")
                                oco_success = place_nobitex_oco_sell_order(symbol, real_quantity, final_target, final_stop)

                            if oco_success:
                                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                last_signals[symbol] = {
                                    "signal": "BUY",
                                    "entry_price": int(price_in_toman),
                                    "target_price": final_target,
                                    "stop_price": final_stop,
                                    "oco_order_id": order_id if not PAPER_TRADING else None,
                                    "updated_at": now_str,
                                    "trade_history": position.get("trade_history", [])
                                }
                                save_last_signals(last_signals)
                                last_nobitex_update = 0
                                open_positions_count += 1  # افزایش شمارنده برای ارز بعدی همین چرخه
                        else:
                            logger.error(f"❌ خطای بحرانی: سفارش {order_id} در نوبیتکس پر نشد! پوزیشن ذخیره نشد.")

                # 🔴 سناریوی دوم: مدیریت پوزیشن باز
                elif position["signal"] == 'BUY':

                    if PAPER_TRADING:
                        if current_signal == 'SELL' or price_in_toman <= position["stop_price"]:
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="📉 خروج فرضی در حالت تست")

                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE",
                                "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str,
                                "entry_price": position.get("entry_price", 0.0),
                                "exit_price": int(price_in_toman),
                                "reason": "لمس حد ضرر یا سیگنال اندیکاتور (تست فرضی)"
                            }

                            last_signals[symbol] = {
                                "signal": "HOLD",
                                "entry_price": 0.0,
                                "target_price": 0.0,
                                "stop_price": 0.0,
                                "oco_order_id": None,
                                "updated_at": now_str,
                                "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)

                    else:
                        coin_name_lower = symbol.split('/')[0].lower()
                        url_wallet = "https://apiv2.nobitex.ir/v2/wallets"
                        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

                        res_w = _send_request_with_retry("POST", url_wallet, headers=headers, json_data={})
                        if res_w and res_w.get("status") == "ok":
                            wallets = res_w.get("wallets", {})
                            coin_balance = float(wallets.get(coin_name_lower.upper(), {}).get("balance", 0.0))

                            if coin_balance < 0.001:
                                logger.info(f"🎉 [خروج موفق OCO] اردر OCO ارز {symbol} در صرافی با موفقیت اجرا و بسته شد.")

                                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                past_trade = {
                                    "type": "REAL_OCO_TRADE",
                                    "entry_time": position.get("updated_at", "نامشخص"),
                                    "exit_time": now_str,
                                    "entry_price": position.get("entry_price", 0.0),
                                    "exit_price": int(price_in_toman),
                                    "reason": "اجرای حد سود یا حد ضرر OCO در صرافی نوبیتکس"
                                }

                                last_signals[symbol] = {
                                    "signal": "HOLD",
                                    "entry_price": 0.0,
                                    "target_price": 0.0,
                                    "stop_price": 0.0,
                                    "oco_order_id": None,
                                    "updated_at": now_str,
                                    "trade_history": position.get("trade_history", []) + [past_trade]
                                }
                                save_last_signals(last_signals)

                time.sleep(0.2)
            except Exception as e:
                logger.error(f"⚠️ خطا در پردازش {symbol}: {e}")
                continue

        # 💤 بخش استراحت ۳۰۰ ثانیه‌ای (خارج از حلقه for ارزها)
        print(f"\n💤 استراحت ۳۰۰ ثانیه‌ای تا چرخه بعدی...")
        with open("market_monitor.log", "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- چرخه بعدی پایش در ۳۰۰ ثانیه آینده ---\n\n")
        time.sleep(300)

def generate_daily_report():
    logger.info("📊 در حال محاسبه و تولید کارنامه معاملات ۲۴ ساعت گذشته...")
    file_path = "live_signals_v3.json"

    if not os.path.exists(file_path):
        logger.warning("⚠️ فایلی برای گزارش‌گیری یافت نشد.")
        return None

    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"❌ خطا در خواندن فایل دیتابیس برای گزارش‌گیری: {e}")
        return None

    now = datetime.now()
    total_trades = 0
    profitable_trades = 0
    total_profit_loss_toman = 0

    report_lines = []
    report_lines.append(f"📅 **گزارش عملکرد ربات نوسان‌گیری**")
    report_lines.append(f"⏱️ زمان تولید گزارش: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append("=" * 40)

    for symbol, config in data.items():
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

                # فرض بر این است که با BUDGET_TOMAN وارد هر پوزیشن می‌شوی
                # سود یا زیان تومانی خالص روی این پوزیشن
                trade_pnl_toman = int(BUDGET_TOMAN * (pnl_percent / 100))
                total_profit_loss_toman += trade_pnl_toman

                if trade_pnl_toman > 0:
                    profitable_trades += 1
                    status_emoji = "🟢"
                else:
                    status_emoji = "🔴"

                report_lines.append(
                    f"{status_emoji} **{symbol}** ({trade_type})\n"
                    f"   📥 ورود: {entry_p:,} | 📤 خروج: {exit_p:,} تومان\n"
                    f"   📈 بازدهی: {pnl_percent:+.2f}% ({trade_pnl_toman:+,} تومان)"
                )
                report_lines.append("-" * 30)

    # 📈 محاسبه آمار کلی
    win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0.0
    pnl_color = "🟢" if total_profit_loss_toman >= 0 else "🔴"

    summary_section = [
        "== 🎯 خلاصه وضعیت امروز ==",
        f"🔢 کل معاملات بسته شده: {total_trades}",
        f"✅ معاملات سودده: {profitable_trades} | ❌ معاملات زیان‌ده: {total_trades - profitable_trades}",
        f"📊 درصد موفقیت (Win Rate): {win_rate:.2f}%",
        f"{pnl_color} کل سود/زیان خالص امروز: {total_profit_loss_toman:,} تومان",
        "========================================"
    ]

    # چسباندن خلاصه به بالای گزارش
    final_report = "\n".join(summary_section + [""] + report_lines)

    print(final_report)  # نمایش در ترمینال

    # 📧 ارسال ایمیل در صورت وجود معامله
    if total_trades > 0:
        # اینجا می‌توانی از همان تابع ارسال ایمیل خودت استفاده کنی
        # send_email_notification("📊 کارنامه روزانه ربات نوسان‌گیری", final_report)
        logger.info("📧 گزارش روزانه با موفقیت به ایمیل ارسال شد.")
    else:
        logger.info("💤 امروز معامله بسته‌شده‌ای وجود نداشت؛ ایمیلی ارسال نشد.")

    return final_report


if __name__ == "__main__":
    monitor_market()
