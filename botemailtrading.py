import time
import ccxt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import ta
from datetime import datetime

# --- 🎯 تنظیمات صرافی: تایم‌فریم ۲ ساعته زنده ---
exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '2h'
budget_toman=50000

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
cc_email = "amirghoorbaninia3002@gmail.com"#www.rasul.mahmoudimajd1038@gmail.com"

NOBITEX_TOKEN = "af580cc838c22460b3d35078a52f14ed2e1d2237"


# --- 🎨 کدهای رنگی پیشرفته کنسول (ANSI) ---
GREEN = "\033[92m"  # سبز برای BUY
RED = "\033[91m"  # قرمز برای SELL
BLUE = "\033[00m"  # آبی برای HOLD
RESET = "\033[0m"  # ریست کردن رنگ خط بعدی


def get_iran_dollar_price():
    # اولویت اول: نوبیتکس V3
    try:
        url = "apiv2.nobitex.ir/v3/orderbook/USDTIRT"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and 'status' in data and data['status'] == 'ok':
            # پیدا کردن بازار تتر به ریال (USDTIRT)
            if 'USDTIRT' in data:
                # قیمت در نوبیتکس به ریال است، تقسیم بر 10 می‌شود تومان
                tether_rial = data['USDTIRT']['lastTradePrice']
                return int(float(tether_rial) / 10)
    except Exception:
        pass

    # اولویت دوم: والکس (تتر به تومان)
    try:
        url = "https://api.wallex.ir/v1/markets"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and 'result' in data and 'symbols' in data['result']:
            tether_toman = data['result']['symbols']['USDTTMN']['stats']['lastPrice']
            return int(float(tether_toman))
    except Exception:
        pass

    # قیمت زاپاس در صورت خطای شبکه
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
            <div class="header">
                <h2>{title}</h2>
            </div>
            <div class="content">
                <table class="info-table">
    """

    for label, val in rows_data:
        html_body += f"""
                    <tr>
                        <td class="label">{label}</td>
                        <td class="value">{val}</td>
                    </tr>
        """

    html_body += f"""
                    <tr>
                        <td class="label">زمان سیگنال</td>
                        <td class="value">{current_time}</td>
                    </tr>
                </table>
            </div>
            <div class="footer">
                این یک پیام خودکار از ربات معاملاتی شماست.
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject

    recipients = [RECEIVER_EMAIL]
    target_cc = cc_email if cc_email is not None else globals().get('cc_email')
    if target_cc:
        msg['Cc'] = target_cc
        recipients.append(target_cc)

    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        server.quit()
        print(f"📧 ایمیل با موفقیت ارسال شد.")
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


def simulate_oco_trade(symbol, current_price, atr_value):
    coin_name = symbol.split('/')[0]
    dollar_price = get_iran_dollar_price()

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


def simulate_sell_trade(symbol, current_price):
    coin_name = symbol.split('/')[0]
    dollar_price = get_iran_dollar_price()
    price_in_toman = current_price * dollar_price
    toman_price = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

    subject = f"🚨 [خروج فوری] {coin_name}"
    title = f"🔴 سیگنال فروش و خروج: {coin_name}"

    rows_data = [
        ("نام ارز دیجیتال", coin_name),
        ("قیمت فروش (دلار)", f"${current_price:.5f}"),
        ("قیمت فروش (تومان)", f"{toman_price} تومان")

    ]

    send_beautiful_email(subject, title, "#ef4444", rows_data)


def monitor_market():
    print(f"🔥 ربات ۲ ساعته با پایش آنی و مانیتورینگ تمام رنگی فعال شد...")

    symbols = [
        "ADA/USDT", "POL/USDT", "ALGO/USDT", "XLM/USDT", "S/USDT", "HBAR/USDT", "ONE/USDT", "ZIL/USDT",
        "VET/USDT", "GRT/USDT", "STX/USDT", "BICO/USDT", "RENDER/USDT", "ANKR/USDT", "IOTX/USDT", "JASMY/USDT",
        "TRX/USDT", "XRP/USDT", "DOGE/USDT", "CRO/USDT", "TNSR/USDT", "DOGS/USDT", "HMSTR/USDT", "APE/USDT", "FET/USDT",
        "DOT/USDT", "SEI/USDT", "DYDX/USDT", "SUI/USDT", "FTM/USDT", "OP/USDT", "ARB/USDT", "GALA/USDT",
        "BONK/USDT", "SAND/USDT", "MANA/USDT", "MASK/USDT", "LRC/USDT", "CHZ/USDT", "ENJ/USDT", "BAT/USDT"
    ]

    last_signals = {symbol: "HOLD" for symbol in symbols}

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

                price_in_toman = current_price * dollar_price
                if price_in_toman > 100000:
                    continue

                toman_str = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

                # 🎨 اعمال رنگ به کل سطر بر اساس گارد اندیکاتور
                if current_signal == 'BUY':
                    color_code = GREEN
                elif current_signal == 'SELL':
                    color_code = RED
                else:
                    color_code = BLUE  # تمام سطرهای HOLD یکپارچه آبی جذاب می‌شوند

                print(
                    f"{color_code}📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {current_signal:<5} | زمان: {current_time_str}{RESET}")

                if current_signal == 'BUY' and current_signal != last_signals[symbol]:
                    atr_value = live_row['ATR']
                    simulate_oco_trade(symbol, current_price, atr_value)
                    #place_nobitex_buy_order(symbol, price_in_toman, budget_toman=budget_toman)
                    last_signals[symbol] = current_signal

                elif current_signal == 'SELL' and current_signal != last_signals[symbol]:
                    simulate_sell_trade(symbol, current_price)
                    last_signals[symbol] = current_signal

                time.sleep(0.4)
            except Exception:
                continue

        print("💤 استراحت ۱۰ دقیقه‌ای تا چرخه بعدی...")
        time.sleep(600)


def place_nobitex_buy_order(symbol, toman_price, budget_toman=budget_toman):
    """
    ثبت سفارش خرید در نوبیتکس بر اساس تومان
    budget_toman: مقداری که می‌خواهید به تومان خرید کنید (مثلاً ۵۰۰ هزار تومان)
    """
    # تبدیل نام ارز صرافی کوکوین به فرمت نوبیتکس (مثلاً ADA/USDT به ada)
    coin_name = symbol.split('/')[0].lower()

    url = "https://api.nobitex.ir/market/orders/add"

    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN}",
        "Content-Type": "application/json"
    }

    # محاسبه مقدار (Quantity) ارز بر اساس بودجه شما و قیمت فعلی
    # نوبیتکس مقدار ارز را می‌خواهد
    quantity = budget_toman / toman_price

    # ساختار ارسالی برای سفارش خرید (نوع: buy، مدل: limit یا market)
    # در اینجا از نوع خرید با قیمت مشخص (limit) استفاده شده که امن‌تر است
    payload = {
        "type": "buy",
        "execution": "limit",  # یا market برای خرید آنی با قیمت بازار
        "srcCurrency": coin_name,
        "dstCurrency": "rls",  # نوبیتکس بر پایه ریال کار می‌کند
        "amount": f"{quantity:.4f}",
        "price": f"{int(toman_price * 10)}"  # تبدیل قیمت تومان به ریال برای نوبیتکس
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        result = response.json()

        if result.get("status") == "ok":
            print(f"🟢 [خرید موفق] سفارش خرید {coin_name.upper()} در نوبیتکس با موفقیت ثبت شد.")
            return True
        else:
            print(f"❌ [خطا در نوبیتکس] {result.get('message', 'خطای ناشناخته')}")
            return False
    except Exception as e:
        print(f"⚠️ خطای شبکه در ثبت سفارش نوبیتکس: {e}")
        return False


if __name__ == "__main__":
    monitor_market()
