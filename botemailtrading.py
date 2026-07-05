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
from datetime import datetime

# --- 🎯 تنظیمات صرافی: تایم‌فریم ۲ ساعته زنده ---
exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '2h'
BUDGET_TOMAN = 300000  # حداقل مقدار مجاز نوبیتکس ۳۰۰ هزار تومان است

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
CC_EMAIL = "amirghoorbaninia3002@gmail.com"

NOBITEX_TOKEN = "o5TJUZrJoLj7afjp3jxhYa2wixNdKI4gdX8KVtj9Htk="
NOBITEX_TOKEN_PUBLIC= "af580cc838c22460b3d35078a52f14ed2e1d2237"
STATE_FILE = "bot_signals_state.json"

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
        print(f"⚠️ خطا در ذخیره فایل وضعیت: {e}")


def get_iran_dollar_price():
    try:
        url = "https://apiv2.nobitex.ir/v3/orderbook/USDTIRT"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and data.get('status') == 'ok' and 'USDTIRT' in data:
            tether_rial = data['USDTIRT']['lastTradePrice']
            return int(float(tether_rial) / 10)
    except Exception:
        pass

    try:
        url = "https://api.wallex.ir/v1/markets"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and 'result' in data and 'symbols' in data['result']:
            tether_toman = data['result']['symbols']['USDTTMN']['stats']['lastPrice']
            return int(float(tether_toman))
    except Exception:
        pass

    return 65000


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
        print("📧 ایمیل با موفقیت ارسال شد.")
    except Exception as e:
        print(f"⚠️ خطا در ارسال ایمیل: {e}")


def get_kucoin_data(symbol, timeframe, limit=150):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None


def calculate_ut_bot_2h_live(df):
    sensibility = 3.0
    atr_period = 10

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df['ATR'] = ranges.max(axis=1).rolling(atr_period).mean()

    df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['RSI'] = ta.momentum.rsi(close=df['close'], window=14)

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
            if df['close'].iloc[i] > df['EMA_50'].iloc[i] and df['RSI'].iloc[i] < 78:
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

def place_nobitex_buy_order(symbol, toman_price, budget_toman=BUDGET_TOMAN):
    coin_name = symbol.split('/')[0].lower()
    url = "https://api.nobitex.ir/market/orders/add"
    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN}",
        "Content-Type": "application/json"
    }

    quantity = budget_toman / toman_price
    payload = {
        "type": "buy",
        "execution": "limit",
        "srcCurrency": coin_name,
        "dstCurrency": "irt",
        "amount": f"{quantity:.4f}",
        "price": f"{int(toman_price)}"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        result = response.json()

        if result.get("status") == "ok" and "order" in result:
            order_id = result["order"]["id"] # دریافت شناسه اردر برای پیگیری دقیق بعدی
            print(f"📌 [سفارش خرید ثبت شد] ارز {coin_name.upper()} با شناسه {order_id} در صف قرار گرفت.")
            send_nobitex_order_email(coin_name, toman_price, budget_toman, quantity)
            return True, order_id
        else:
            error_msg = result.get('message', 'خطای ناشناخته نوبیتکس')
            print(f"❌ [خطای خرید نوبیتکس] {error_msg}")
            send_nobitex_error_email(coin_name, "خرید لیمیت", f"پاسخ صرافی: {error_msg}")
            return False, None
    except Exception as e:
        print(f"⚠️ خطای شبکه در ثبت سفارش خرید نوبیتکس: {e}")
        return False, None


def get_nobitex_order_matched_amount(order_id):
    """دریافت مقدار دقیق ارز معامله شده پس از کسر کارمزد بر اساس شناسه سفارش"""
    url = "https://api.nobitex.ir/market/orders/status"
    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"id": order_id}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        result = response.json()

        if result.get("status") == "ok" and "order" in result:
            order_info = result["order"]
            # در نوبیتکس matchedAmount مقدار نهایی پر شده و خالص است
            matched_qty = float(order_info.get("matchedAmount", 0.0))
            return matched_qty
        return 0.0
    except Exception as e:
        print(f"⚠️ خطا در استعلام وضعیت اردر {order_id}: {e}")
        return 0.0


def place_nobitex_oco_sell_order(symbol, quantity, target_toman, stop_toman):
    """ثبت سفارش فروش واقعی OCO با واحد تومان (IRT) در نوبیتکس"""
    coin_name = symbol.split('/')[0].lower()
    url = "https://api.nobitex.ir/market/orders/add"
    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN}",
        "Content-Type": "application/json"
    }

    # برای اردر لیمیتِ حد ضرر، قیمت را نیم درصد پایین‌تر از قیمت توقف قرار می‌دهیم تا در صورت ریزش سنگین حتماً پر شود
    stop_limit_toman = int(stop_toman * 0.995)

    payload = {
        "type": "sell",
        "mode": "oco",                 # فعال‌سازی قابلیت OCO
        "srcCurrency": coin_name,
        "dstCurrency": "irt",          # بازار تومان (IRT) طبق مستندات و پنل شما
        "amount": f"{quantity:.4f}",
        "price": f"{int(target_toman)}",       # قیمت حد سود به تومان (بدون ضرب در ۱۰)
        "stopPrice": f"{int(stop_toman)}",     # قیمت توقف حد ضرر به تومان (بدون ضرب در ۱۰)
        "stopLimitPrice": f"{int(stop_limit_toman)}" # قیمت نهایی فروش حد ضرر به تومان
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        result = response.json()

        if result.get("status") == "ok":
            print(f"🛡️ [سفارش OCO ثبت شد] سپرهای محافظتی فروش {coin_name.upper()} با موفقیت در نوبیتکس قفل شدند.")
            send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
            return True
        else:
            error_msg = result.get('message', 'خطای ناشناخته نوبیتکس')
            print(f"❌ [خطای ثبت OCO نوبیتکس] {error_msg}")
            send_nobitex_error_email(coin_name, "فروش OCO", f"پاسخ صرافی: {error_msg}")
            return False
    except Exception as e:
        print(f"⚠️ خطای شبکه در ثبت سفارش OCO نوبیتکس: {e}")
        return False

# --- 📧 ایمیل‌های سیستم نوبیتکس ---

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
        ("تعداد دقیق و خالص برای فروش", f"{quantity:.4f} {coin_name.upper()}"),  # نشان دادن تعداد دقیق بعد از کارمزد
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
    print(f"🔥 ربات ۲ ساعته با قابلیت ثبت مستقیم اردر فروش OCO واقعی فعال شد...")

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
                    t_entry, t_target, t_stop = simulate_oco_trade(symbol, current_price, atr_value, dollar_price)

                    # ۱. ثبت خرید و دریافت شناسه اردر نوبیتکس
                    order_success, order_id = place_nobitex_buy_order(symbol, price_in_toman, budget_toman=BUDGET_TOMAN)

                    if order_success:
                        print("⏳ ۵ ثانیه انتظار برای پر شدن سفارش خرید در صرافی...")
                        time.sleep(5)

                        # ۲. دریافت مقدار واقعی و خالص خریداری شده (پس از کسر کارمزد)
                        real_quantity = get_nobitex_order_matched_amount(order_id)

                        if real_quantity > 0:
                            # رند کردن قیمت‌ها برای صرافی
                            final_target = int(t_target)
                            final_stop = int(t_stop)

                            print(f"📈 [جزئیات سفارش] مقدار واقعی خریداری شده: {real_quantity:.4f}")

                            # ۳. ثبت سفارش فروش OCO واقعی در نوبیتکس با تعداد دقیق خالص
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
                            print(f"❌ خطای حیاتی: سفارش با شناسه {order_id} هنوز پر نشده یا مقدار آن صفر است!")

                # --- 🛠️ در صورت تغییر گارد اندیکاتور به SELL در صرافی خارجی ---
                elif current_signal == 'SELL' and position["signal"] == 'BUY':
                    # اگر اندیکاتور زودتر از خوردن تارگت/استاپ سیگنال خروج داد، فقط به شما ایمیل هشدار می‌دهد چون اردر در صرافی قفل است
                    simulate_sell_trade(symbol, current_price, dollar_price,
                                        reason="📉 تغییر سیگنال اندیکاتور به SELL (اردر OCO شما در صرافی همچنان فعال است)")
                    position = {"signal": "SELL", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0}
                    last_signals[symbol] = position
                    save_last_signals(last_signals)

                time.sleep(0.5)
            except Exception as e:
                print(f"⚠️ خطا در پردازش {symbol}: {e}")
                continue

                # ------ انتهای حلقه بررسی ارزها ------

                # 🔍 جمع‌آوری پوزیشن‌های باز برای ارسال گزارش
        active_positions = {sym: pos for sym, pos in last_signals.items() if pos.get("signal") == "BUY"}

        print("📧 در حال ارسال گزارش وضعیت پوزیشن‌ها به ایمیل شما...")
        send_status_report_email(active_positions)

        print("💤 استراحت ۱۰ دقیقه‌ای تا چرخه بعدی...")
        time.sleep(600)



def send_status_report_email(active_positions):
    """ارسال ایمیل خلاصه وضعیت پوزیشن‌های باز ربات"""
    subject = "📊 [گزارش وضعیت] پوزیشن‌های فعال ربات معاملاتی"
    title = "📈 خلاصه پوزیشن‌های باز شما در نوبیتکس"

    rows_data = []
    if not active_positions:
        rows_data.append(("وضعیت حساب", "در حال حاضر هیچ پوزیشن بازی وجود ندارد. ربات در حالت HOLD است."))
    else:
        for symbol, pos in active_positions.items():
            rows_data.append((f"📌 ارز {symbol.split('/')[0]}",
                              f"ورود: {pos['entry_price']:,} | حد سود: {pos['target_price']:,} | حد ضرر: {pos['stop_price']:,}"))

    # استفاده از تابع ارسال ایمیلی که خودتان در کد داشتید
    send_beautiful_email(subject, title, "#4b5563", rows_data)


def calculate_and_email_total_pnl():
    """دریافت لیست تمام معاملات شخصی دقیقاً منطبق بر تصویر پست‌من"""
    url = "https://apiv2.nobitex.ir/market/trades/list"

    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}",
        "Content-Type": "application/json",
    }

    try:
        # 🟢 دقیقاً مثل عکس: متد GET بدون هیچ پارامتر اضافی (params یا data حذف شدند)
        response = requests.get(url, headers=headers, timeout=15)
        result = response.json()

        if result.get("status") != "ok":
            print(f"⚠️ پاسخ مستقیم صرافی: {result}")
            return

        trades = result.get("trades", [])
        if not trades:
            print("📭 هیچ معامله‌ای یافت نشد.")
            return

        total_buy_value_toman = 0.0
        total_sell_value_toman = 0.0
        total_fee_toman = 0.0

        for trade in trades:
            market = trade.get("market", "")

            # 🚨 تفکیک بسیار مهم: فقط معاملاتی که مقصد آن‌ها ریال (RLS) است را حساب می‌کنیم
            # تا سود و زیان دلاری با تومانی در فرمول شما قاطی نشود
            if not market.endswith("-RLS"):
                continue  # معاملات دلاری/تتری مثل BTC-USDT نادیده گرفته می‌شوند

            trade_total = float(trade.get("total", 0))  # ارزش کل به ریال
            price = float(trade.get("price", 0))  # قیمت به ریال
            fee = float(trade.get("fee", 0))  # مقدار کارمزد

            # تبدیل ریال داخل عکس به تومان (تقسیم بر ۱۰)
            trade_value_toman = trade_total / 10
            price_toman = price / 10

            if trade.get("type") == "buy":
                total_buy_value_toman += trade_value_toman
                total_fee_toman += (fee * price_toman)
            elif trade.get("type") == "sell":
                total_sell_value_toman += trade_value_toman
                total_fee_toman += (fee / 10)

        # 🎯 محاسبه سود و زیان خالص بازارهای تومانی
        net_pnl = total_sell_value_toman - total_buy_value_toman - total_fee_toman

        # ... بخش ساخت و ارسال ایمیل شما کاملاً درست است و اینجا قرار می‌گیرد ...
        print(f"✅ گزارش PnL بازارهای تومانی با موفقیت محاسبه شد: {int(net_pnl):,} تومان")

    except Exception as e:
        print(f"⚠️ خطای پردازش داده‌ها: {e}")


def show_all_my_trades():
    """دریافت و نمایش لیست تمام تراکنش‌های حساب نوبیتکس"""
    url = "https://apiv2.nobitex.ir/market/trades/list"

    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}"
    }

    try:
        # درخواست GET بدون پارامتر اضافی (دقیقاً مثل پست‌من شما)
        response = requests.get(url, headers=headers, timeout=15)
        result = response.json()

        if result.get("status") != "ok":
            print(f"⚠️ خطای صرافی: {result}")
            return

        trades = result.get("trades", [])
        if not trades:
            print("📭 هیچ تراکنشی در تاریخچه معاملات شما پیدا نشد.")
            return

        print(f"📦 تعداد کل تراکنش‌های یافت شده: {len(trades)}\n")
        print(f"{'بازار':<12} | {'نوع':<6} | {'قیمت':<12} | {'مقدار':<12} | {'ارزش کل':<15} | {'کارمزد':<10}")
        print("-" * 80)

        for trade in trades:
            market = trade.get("market", "")  # مثلاً USDT-RLS یا BTC-USDT
            trade_type = trade.get("type", "")  # buy یا sell
            price = float(trade.get("price", 0))
            amount = float(trade.get("amount", 0))
            total = float(trade.get("total", 0))
            fee = float(trade.get("fee", 0))

            # تشخیص واحد پولی (تومان/ریال یا تتر) برای خوانایی بهتر
            is_toman_market = market.endswith("-RLS")
            unit = "تومان" if is_toman_market else "تتر/ارز"

            # اگر بازار تومانی (ریالی نوبیتکس) بود، مبالغ رو برای نمایش بهتر به تومان تبدیل می‌کنیم
            display_price = price / 10 if is_toman_market else price
            display_total = total / 10 if is_toman_market else total

            # تبدیل نوع معامله به فارسی برای زیبایی خروجی
            type_fa = "🟢 خرید" if trade_type == "buy" else "🔴 فروش"

            # چاپ مرتب در ترمینال
            print(
                f"{market:<12} | {type_fa:<5} | {display_price:,.2f} {unit:<5} | {amount:<12} | {display_total:,.2f} {unit:<5} | {fee} ")

    except Exception as e:
        print(f"⚠️ خطای پردازش تراکنش‌ها: {e}")





if __name__ == "__main__":
   # calculate_and_email_total_pnl()
    # اجرای تابع برای تست
    #show_all_my_trades()
    monitor_market()
