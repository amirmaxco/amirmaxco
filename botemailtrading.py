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
PAPER_TRADING = False  # اگر True باشد، ربات فقط معامله را تست می‌کند (بدون سرمایه واقعی)
RISK_PERCENT = 2.0  # مدیریت سرمایه: ورود با ۱۰ درصد از کل موجودی به جای مبلغ ثابت
MAX_DAILY_TRADES = 5  # سقف مجاز معاملات روزانه برای کنترل ریسک

# ==========================================
# 🛑 تنظیمات پیشرفته سیستم لاگین روزانه با پشتیبانی از UTF-8
# ==========================================
logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)

# 🟢 اضافه شدن encoding='utf-8' برای حل مشکل ایموجی‌ها و متن فارسی در فایل
log_handler = TimedRotatingFileHandler("nobitex_bot.log", when="midnight", interval=1, backupCount=7, encoding='utf-8')
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)

# 🟢 تنظیم انکودینگ خروجی ترمینال برای ویندوز
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
# در پایتون‌های جدید ویندوز این خط جلوی ارور ترمینال را می‌گیرد
if hasattr(stream_handler.stream, 'reconfigure'):
    stream_handler.stream.reconfigure(encoding='utf-8')
logger.addHandler(stream_handler)

# --- 🎯 تنظیمات صرافی: تایم‌فریم ۲ ساعته زنده ---
exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '2h'
BUDGET_TOMAN = 50000  # حداقل مقدار مجاز نوبیتکس 50 هزار تومان است

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
CC_EMAIL = ""

NOBITEX_TOKEN = "o5TJUZrJoLj7afjp3jxhYa2wixNdKI4gdX8KVtj9Htk="
NOBITEX_TOKEN_PUBLIC = "af580cc838c22460b3d35078a52f14ed2e1d2237"
STATE_FILE = "bot_signals_state.json"

# آمار داخلی مدیریت ریسک
daily_trade_count = 0
last_reset_date = time.strftime("%Y-%m-%d")
max_peak_balance = 0.0

# --- 🎨 کدهای رنگی کنسول ---
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[34m"
RESET = "\033[0m"


# --- 💾 مدیریت وضعیت ربات ---
def load_last_signals(symbols):
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {symbol: {"signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0, "oco_order_id": None}
            for symbol in symbols}


def save_last_signals(signals):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(signals, f)
    except Exception as e:
        logger.error(f"⚠️ خطا در ذخیره فایل وضعیت: {e}")


def _send_request_with_retry(method, url, headers=None, json_data=None, params=None, retries=3):
    """مدیریت قطعی اینترنت، خودکارسازی مجدد (Retry) و کنترل Timeout"""
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
    res = _send_request_with_retry("GET", url)
    if res and res.get('status') == 'ok' and 'USDTIRT' in res:
        tether_rial = res['USDTIRT']['lastTradePrice']
        return int(float(tether_rial) / 10)
    return 65000


def get_nobitex_live_price(coin_name):
    """استفاده از قیمت لحظه‌ای نوبیتکس برای ثبت سفارش (به جای قیمت کوکوین)"""
    url = "https://apiv2.nobitex.ir/market/stats"
    res = _send_request_with_retry("GET", url, params={"srcCurrency": coin_name.lower(), "dstCurrency": "rls"})
    if res and res.get("status") == "ok":
        pair = f"{coin_name.upper()}-RLS"
        price_rial = res.get("stats", {}).get(pair, {}).get("latest", None)
        if price_rial:
            return float(price_rial) / 10  # تبدیل به تومان
    return None


def get_nobitex_wallet_balance():
    """بررسی موجودی قبل از خرید بر اساس درصد حساب"""
    if PAPER_TRADING:
        return 10000000.0  # ۱۰ میلیون تومان موجودی فرضی در حالت تست

    url = "https://apiv2.nobitex.ir/v2/wallets"
    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}",
        "Content-Type": "application/json"
    }

    # اطلاعات باید در بادی آرگومان json_data قرار بگیرند
    payload = {
        "currencies": "rls"  # نوبیتکس معمولاً حروف کوچک را ترجیح می‌دهد
    }

    res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
    #print("پاسخ کامل صرافی برای موجودی:", res)

    if res and res.get("status") == "ok":
        # ۱. ابتدا لایه wallets را می‌گیریم
        wallets = res.get("wallets", {})

        # ۲. بررسی می‌کنیم آیا کلید RLS (ریال) در آن وجود دارد؟
        if "RLS" in wallets:
            # ۳. مقدار موجودی ریالی را استخراج می‌کنیم
            rial_balance = float(wallets["RLS"].get("balance", 0.0))
            # ۴. چون کل سیستم مدیریت سرمایه ربات شما به "تومان" است، آن را تقسیم بر ۱۰ می‌کنیم
            toman_balance = rial_balance / 10.0
            # print(f"موجودی واقعی به تومان: {toman_balance}")
            return toman_balance

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


def get_kucoin_data(symbol, timeframe, limit=150):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None


def calculate_ut_bot_2h_live(df):
    sensibility = 3.0
    atr_period = 10

    # ۱. محاسبات پایه UT Bot (ATR)
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df['ATR'] = ranges.max(axis=1).rolling(atr_period).mean()

    df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['RSI'] = ta.momentum.rsi(close=df['close'], window=14)

    # 🟢 اندیکاتور ADX برای تشخیص قدرت روند
    df['ADX'] = ta.trend.adx(high=df['high'], low=df['low'], close=df['close'], window=14)

    # 🟢 میانگین متحرک حجم (Volume MA)
    df['Volume_MA'] = df['volume'].rolling(window=20).mean()

    df['nLoss'] = sensibility * df['ATR']
    df['trailing_stop'] = 0.0

    for i in range(1, len(df)):
        prev_close = df['close'].iloc[i - 1]
        curr_close = df['close'].iloc[i]
        prev_ts = df['trailing_stop'].iloc[i - 1]
        n_loss = df['nLoss'].iloc[i]

        if curr_close > prev_ts and prev_close > prev_ts:
            df.at[df.index[i], 'trailing_stop'] = max(prev_ts, curr_close - n_loss)
        elif curr_close < prev_ts and prev_close < prev_ts:
            df.at[df.index[i], 'trailing_stop'] = min(prev_ts, curr_close + n_loss)
        elif curr_close > prev_ts:
            df.at[df.index[i], 'trailing_stop'] = curr_close - n_loss
        else:
            df.at[df.index[i], 'trailing_stop'] = curr_close + n_loss

    df['signal'] = 'HOLD'
    for i in range(1, len(df)):
        ut_buy = df['close'].iloc[i] > df['trailing_stop'].iloc[i] and df['close'].iloc[i - 1] <= \
                 df['trailing_stop'].iloc[i - 1]
        ut_sell = df['close'].iloc[i] < df['trailing_stop'].iloc[i] and df['close'].iloc[i - 1] >= \
                  df['trailing_stop'].iloc[i - 1]

        if ut_buy:
            # 🟢 فیلترهای متعادل شده (کاهش سخت‌گیری):
            # - ADX بالای 20 باشد (روند ضعیف یا متوسط شکل گرفته باشد)
            # - حجم کندل حداقل 80 درصد میانگین حجم 20 کندل اخیر باشد

            is_trend_ok = df['ADX'].iloc[i] > 20
            is_volume_ok = df['volume'].iloc[i] > (df['Volume_MA'].iloc[i] * 0.8)

            if df['close'].iloc[i] > df['EMA_50'].iloc[i] and df['RSI'].iloc[i] < 78 and is_trend_ok and is_volume_ok:
                df.at[df.index[i], 'signal'] = 'BUY'
            else:
                df.at[df.index[i], 'signal'] = 'HOLD'
        elif ut_sell:
            df.at[df.index[i], 'signal'] = 'SELL'

    return df


def simulate_oco_trade(symbol, current_price, atr_value, dollar_price):
    coin_name = symbol.split('/')[0]

    stop_raw = current_price - (2.0 * atr_value)
    target_raw = current_price + (3.5 * atr_value)

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

    subject = f"🚨 [خروج فوری] {coin_name}"
    title = f"🔴 سیگنال فروش و خروج: {coin_name}"

    rows_data = [
        ("نام ارز دیجیتال", coin_name),
        ("قیمت فروش (دلار)", f"${current_price:.5f}"),
        ("قیمت فروش (تومان)", f"{toman_price} تومان"),
        ("دلیل خروج از معامله", reason)
    ]
    send_beautiful_email(subject, title, "#ef4444", rows_data)


# --- 🛒 تراکنش‌های خودکار صرافی نوبیتکس ---

def place_buy_order_and_notify(symbol, price_toman, budget_toman):
    """مدیریت سرمایه درصدی، تست بدون معامله واقعی و تایید نهایی خرید قبل از ارسال ایمیل"""
    global daily_trade_count
    coin_name = symbol.split('/')[0].lower()

    if not check_daily_limits():
        logger.warning("🚫 محدودیت تعداد معاملات روزانه تکمیل شده است.")
        return False, None

    # بررسی موجودی و مدیریت سرمایه بر اساس درصد تعیین شده
    total_balance = get_nobitex_wallet_balance()
    calculated_budget = total_balance * (RISK_PERCENT / 100)
    final_budget = max(calculated_budget, BUDGET_TOMAN)

    # استفاده از قیمت زنده نوبیتکس به جای کوکوین برای دقت معامله
    live_nobitex_price = get_nobitex_live_price(coin_name)
    execution_price = live_nobitex_price if live_nobitex_price else price_toman

    quantity = final_budget / execution_price
    logger.info(
        f"🛒 درخواست خرید {coin_name.upper()} | حجم محاسبه شده: {quantity:.4f} | قیمت مبنا: {int(execution_price)} تومان")

    if PAPER_TRADING:
        logger.info(f"✨ [Paper Trading] خرید فرضی {coin_name.upper()} با موفقیت شبیه‌سازی شد.")
        daily_trade_count += 1
        send_nobitex_order_email(coin_name, execution_price, final_budget, quantity)
        return True, "mock_order_id_12345"

    # ارسال سفارش واقعی به نوبیتکس
    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN}", "Content-Type": "application/json"}

    # نوبیتکس قیمت را به ریال می‌خواهد
    payload = {
        "type": "buy",
        "execution": "limit",
        "srcCurrency": coin_name,
        "dstCurrency": "irt",
        "amount": f"{quantity:.4f}",
        "price": f"{int(execution_price)}"
    }

    res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
    if res and res.get("status") == "ok":
        order_id = res.get("order", {}).get("id")
        logger.info(f"🟢 خرید در صرافی ثبت شد. شناسه اردر: {order_id}. بررسی کامل تا زمان Filled شدن...")
        daily_trade_count += 1
        send_nobitex_order_email(coin_name, execution_price, final_budget, quantity)
        return True, order_id
    else:
        error_msg = res.get("message", "خطای ناشناخته") if res else "عدم پاسخ صرافی"
        logger.error(f"❌ خطای ثبت سفارش خرید نوبیتکس: {error_msg}")
        send_nobitex_error_email(coin_name, "خرید لیمیت", error_msg)
        return False, None


def get_nobitex_order_matched_amount(order_id):
    """بررسی کامل وضعیت سفارش تا زمان Filled شدن و مدیریت سفارش‌های Partial Fill"""
    if PAPER_TRADING:
        return 0.05  # مقدار فرضی تست

    url = "https://apiv2.nobitex.ir/market/orders/status"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN}", "Content-Type": "application/json"}

    # ۵ دقیقه تلاش برای مانیتور کردن وضعیت تا پر شدن نهایی اردر
    start_time = time.time()
    while time.time() - start_time < 300:
        res = _send_request_with_retry("POST", url, headers=headers, json_data={"id": order_id})
        if res and res.get("status") == "ok":
            order_info = res["order"]
            status = order_info.get("status")
            matched_qty = float(order_info.get("matchedAmount", 0.0))

            logger.info(f"⏱️ وضعیت سفارش {order_id}: {status} | مقدار پر شده: {matched_qty}")

            if status == "Filled":
                return matched_qty
            elif status in ["Canceled", "PartialCanceled"]:
                logger.warning(f"⚠️ سفارش لغو شد. حجم نیمه پر شده (Partial Fill): {matched_qty}")
                return matched_qty
        time.sleep(10)

    # لغو خودکار در صورت عدم تکمیل پس از ۵ دقیقه
    logger.warning("⏱️ مهلت انتظار خرید تمام شد. لغو خودکار سفارش برای مدیریت دارایی...")
    cancel_url = "https://apiv2.nobitex.ir/market/orders/update-status"
    _send_request_with_retry("POST", cancel_url, headers=headers, json_data={"id": order_id, "status": "cancel"})

    # مجدداً مقدار نهایی معامله شده را استخراج کن
    res = _send_request_with_retry("POST", url, headers=headers, json_data={"id": order_id})
    return float(res["order"].get("matchedAmount", 0.0)) if res else 0.0


def place_nobitex_oco_sell_order(symbol, quantity, target_toman, stop_toman):
    """ثبت خودکار OCO فقط بعد از تکمیل خرید با قابلیت Retry و ثبت سفارش محافظتی جایگزین"""
    coin_name = symbol.split('/')[0].lower()

    if PAPER_TRADING:
        logger.info(f"🛡️ [Paper Trading] سفارش OCO فرضی برای {coin_name.upper()} ثبت شد.")
        send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
        return True

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN}", "Content-Type": "application/json"}
    stop_limit_toman = int(stop_toman * 0.995)

    payload = {
        "type": "sell",
        "mode": "oco",
        "srcCurrency": coin_name,
        "dstCurrency": "irt",
        "amount": f"{quantity:.4f}",
        "price": f"{int(target_toman)}",
        "stopPrice": f"{int(stop_toman)}",
        "stopLimitPrice": f"{int(stop_limit_toman)}"
    }

    # چند بار تلاش مجدد در صورت خطا در ثبت OCO
    for attempt in range(3):
        res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
        if res and res.get("status") == "ok":
            logger.info(f"🛡️ اردر OCO با موفقیت قفل شد. شناسه: {res.get('order', {}).get('id')}")
            send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
            return True
        logger.warning(f"⚠️ تلاش مجدد برای OCO (تلاش {attempt + 1}/3)")
        time.sleep(3)

    # ثبت سفارش محافظتی جایگزین در صورت باز نشدن OCO
    logger.critical("🚨 ثبت OCO ناموفق بود! فعال‌سازی سفارش حد ضرر اضطراری تکی...")
    send_nobitex_error_email(coin_name, "فروش OCO (خطای مداوم)", "سیستم به سفارش استاپ لیمیت جایگزین سوییچ کرد.")

    backup_payload = {
        "type": "sell",
        "execution": "stop_limit",
        "srcCurrency": coin_name,
        "dstCurrency": "irt",
        "amount": f"{quantity:.4f}",
        "price": f"{int(stop_limit_toman)}",
        "stopPrice": f"{int(stop_toman)}"
    }
    _send_request_with_retry("POST", url, headers=headers, json_data=backup_payload)
    return False


def update_drawdown_performance(current_total_balance):
    """محاسبه سود و زیان واقعی و حداکثر افت سرمایه (Drawdown) بر مبنای پیک ولت"""
    global max_peak_balance
    if current_total_balance > max_peak_balance:
        max_peak_balance = current_total_balance

    drawdown = ((max_peak_balance - current_total_balance) / max_peak_balance) * 100 if max_peak_balance > 0 else 0.0
    logger.info(f"\n📊 کارنامه عملکرد مالی ")
    logger.info(f"\nموجودی فعلی : {int(current_total_balance):,} تومان ")
    logger.info(f"\n حداکثر افت حساب : {drawdown:.2f}")



def send_nobitex_order_email(coin_name, toman_price, budget_toman, quantity):
    subject = f"🛒 [سفارش نوبیتکس] خرید {coin_name.upper()}"
    title = f"🔵 سفارش خرید در نوبیتکس ثبت شد"
    rows_data = [
        ("نام ارز دیجیتال", coin_name.upper()),
        ("قیمت خرید هر واحد (تومان)", f"{int(toman_price):,} Toman"),
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


# --- 🔄 چرخه اصلی مانیتورینگ بازار ---

def monitor_market():
    logger.info("🔥 ربات ۲ ساعته با قابلیت ثبت مستقیم اردر فروش OCO واقعی فعال شد...")

    symbols = [
        "ADA/USDT", "POL/USDT", "ALGO/USDT", "XLM/USDT", "HBAR/USDT", "ONE/USDT", "ZIL/USDT",
        "VET/USDT", "GRT/USDT", "STX/USDT", "ANKR/USDT", "IOTX/USDT", "JASMY/USDT",
        "TRX/USDT", "XRP/USDT", "DOGE/USDT", "SHIB/USDT", "SAND/USDT", "MANA/USDT", "CHZ/USDT"
    ]

    last_signals = load_last_signals(symbols)

    while True:
        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n🔄 --- چرخه پایش آنی: {current_time_str} ---")
        dollar_price = get_iran_dollar_price()

        # مانیتورینگ منظم Drawdown بر اساس تغییرات دارایی ولت شما
        current_wallet = get_nobitex_wallet_balance()
        update_drawdown_performance(current_wallet)

        for symbol in symbols:
            try:
                df = get_kucoin_data(symbol, timeframe=timeframe, limit=120)
                if df is None or df.empty or len(df) < 20:
                    continue

                df = calculate_ut_bot_2h_live(df)
                live_row = df.iloc[-1]
                current_price = live_row['close']
                current_signal = live_row['signal']
                atr_value = live_row['ATR']

                price_in_toman = current_price * dollar_price
                toman_str = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

                position = last_signals.get(symbol, {"signal": "HOLD", "entry_price": 0.0, "target_price": 0.0,
                                                     "stop_price": 0.0, "oco_order_id": None})
                if isinstance(position, str):
                    position = {"signal": position, "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None}

                # --- 🎨 رنگ‌بندی کنسول ---
                if position["signal"] == 'BUY':
                    color_code = GREEN
                    status_display = f"BUY (OCO active)"
                elif current_signal == 'SELL' or position["signal"] == 'SELL':
                    color_code = RED
                    status_display = "SELL"
                else:
                    color_code = BLUE
                    status_display = "HOLD"

                print(
                    f"{color_code}📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18} | زمان: {current_time_str}{RESET}")

                # --- 🛠️ سیگنال خرید جدید ---
                if current_signal == 'BUY' and position["signal"] != 'BUY':
                    # جلوگیری از خرید تکراری همزمان روی یک ارز مشخص
                    if position["signal"] == "BUY":
                        continue

                    t_entry, t_target, t_stop = simulate_oco_trade(symbol, current_price, atr_value, dollar_price)

                    # ۱. ثبت خرید با قیمت زنده و مدیریت سرمایه درصدی حساب
                    order_success, order_id = place_buy_order_and_notify(symbol, price_in_toman,
                                                                         budget_toman=BUDGET_TOMAN)

                    if order_success:
                        # ۲. بررسی کامل وضعیت سفارش تا زمان Filled شدن (پشتیبانی از Partial Fill)
                        real_quantity = get_nobitex_order_matched_amount(order_id)

                        if real_quantity > 0:
                            final_target = int(t_target)
                            final_stop = int(t_stop)

                            logger.info(f"📈 [تکمیل خرید] مقدار خالص معامله شده بعد کارمزد: {real_quantity:.4f}")

                            # ۳. ثبت سفارش فروش OCO واقعی پس از اتمام کامل خرید با حجم واقعی معامله شده
                            oco_success = place_nobitex_oco_sell_order(symbol, real_quantity, final_target, final_stop)

                            if oco_success:
                                position = {
                                    "signal": "BUY",
                                    "entry_price": int(price_in_toman),
                                    "target_price": final_target,
                                    "stop_price": final_stop
                                }
                                last_signals[symbol] = position
                                save_last_signals(last_signals)
                        else:
                            logger.error(f"❌ خطای حیاتی: سفارش با شناسه {order_id} پر نشد و مقدار آن صفر است!")

                # --- 🛠️ در صورت تغییر گارد اندیکاتور به SELL در صرافی خارجی ---
                elif current_signal == 'SELL' and position["signal"] == 'BUY':
                    simulate_sell_trade(symbol, current_price, dollar_price,
                                        reason="📉 تغییر سیگنال اندیکاتور به SELL (اردر OCO شما در صرافی همچنان فعال است)")
                    position = {"signal": "SELL", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0}
                    last_signals[symbol] = position
                    save_last_signals(last_signals)

                time.sleep(0.5)
            except Exception as e:
                logger.error(f"⚠️ خطا در پردازش {symbol}: {e}")
                continue

        print("💤 استراحت ۱۰ دقیقه‌ای تا چرخه بعدی...")
        time.sleep(600)


if __name__ == "__main__":
    monitor_market()
