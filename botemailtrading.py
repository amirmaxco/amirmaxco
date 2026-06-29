import time
import ccxt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# --- تنظیمات صرافی (فقط برای خواندن دیتای عمومی، بدون نیاز به API Key) ---
exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '4h'  # تنظیم روی تایم‌فریم اصلی و پایدار ۲ ساعته

# --- تنظیمات ارسال ایمیل شما ---
SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"  # رمز برنامه جیمیل شما
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"


def get_iran_dollar_price():
    # منبع اول: صرافی معتبر والکس (Wallex) - پایدارترین API تومانی
    try:
        url = "https://api.wallex.ir/v1/markets"
        response = requests.get(url, timeout=5)
        data = response.json()
        # استخراج قیمت تتر به تومان از دیتای والکس
        if data and 'result' in data and 'symbols' in data['result']:
            tether_toman = data['result']['symbols']['USDTTMN']['stats']['lastPrice']
            # والکس قیمت را به تومان می‌دهد؛ در صورت نیاز به رند کردن:
            print(f"💰 قیمت تتر با موفقیت از والکس دریافت شد: {int(float(tether_toman)):,} تومان")
            return int(float(tether_toman))
    except Exception as e:
        print(f"⚠️ منبع اول (والکس) پاسخ نداد: {e}")

    # منبع پشتیبان: صرافی رمزینکس (Ramzinex)
    try:
        url = "https://api.ramzinex.com/v1/exchange/pairs/1" # کد ۱ متعلق به جفت‌ارز تتر/تومان است
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and 'data' in data and 'sell' in data['data']:
            tether_toman = data['data']['sell'] / 10 # تبدیل ریال به تومان
            print(f"💰 قیمت تتر از منبع پشتیبان (رمزینکس) دریافت شد: {int(tether_toman):,} تومان")
            return int(tether_toman)
    except Exception as e:
        print(f"⚠️ منبع دوم (رمزینکس) هم قطع بود: {e}")

    # لایه سوم (آفلاین): اگر به هر دلیلی کلا اینترنت قطع بود، این عدد را می‌گذارد تا کد متوقف نشود
    FALLBACK_PRICE = 65000
    print(f"🚨 عدم اتصال به صرافی‌ها! استفاده از نرخ ثابت: {FALLBACK_PRICE:,} تومان")
    return FALLBACK_PRICE


def send_email_notification(subject, body):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html', 'utf-8'))

    try:
        # اضافه کردن تایم‌اوت برای جلوگیری از قفل شدن کد هنگام قطعی اینترنت
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        text = msg.as_string()
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, text)
        server.quit()
        print(f"📧 ایمیل با موفقیت ارسال شد: {subject}")
        return True
    except Exception as e:
        # تغییر حیاتی: حالا خطاها فقط چاپ می‌شوند و برنامه متوقف نمی‌شود
        print(f"⚠️ اختلال در ارسال ایمیل (ربات متوقف نمی‌شود): {e}")
        return False


def get_kucoin_data(symbol, timeframe, limit=50):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception as e:
        print(f"❌ خطا در دریافت دیتای {symbol}: {e}")
        return None


def calculate_ut_bot(df):
    df['EMA'] = df['close'].ewm(span=10, adjust=False).mean()
    df['signal'] = 'HOLD'
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['EMA'].iloc[i] and df['close'].iloc[i - 1] <= df['EMA'].iloc[i - 1]:
            df.at[df.index[i], 'signal'] = 'BUY'
        elif df['close'].iloc[i] < df['EMA'].iloc[i] and df['close'].iloc[i - 1] >= df['EMA'].iloc[i - 1]:
            df.at[df.index[i], 'signal'] = 'SELL'
    return df


# --- تابع شبیه‌ساز OCO (به همراه محاسبه قیمت به تومان) ---
def simulate_oco_trade(symbol, current_price):
    coin_name = symbol.split('/')[0]

    # ۱. دریافت قیمت لحظه‌ای دلار تومانی
    dollar_price = get_iran_dollar_price()

    # محاسبه فرضی حد سود (3%) و حد ضرر (1.5%)
    target_raw = current_price * 1.03
    stop_raw = current_price * 0.985

    # تبدیل به فرمت اعشاری تمیز
    current_price_clean = f"{current_price:.8f}".rstrip('0').rstrip('.') if current_price < 1 else f"{current_price:.4f}"
    target_price = f"{target_raw:.8f}".rstrip('0').rstrip('.') if target_raw < 1 else f"{target_raw:.4f}"
    stop_loss_price = f"{stop_raw:.8f}".rstrip('0').rstrip('.') if stop_raw < 1 else f"{stop_raw:.4f}"

    if '.' not in current_price_clean: current_price_clean = f"{current_price:.4f}"
    if '.' not in target_price: target_price = f"{target_raw:.4f}"
    if '.' not in stop_loss_price: stop_loss_price = f"{stop_raw:.4f}"

    # ۲. محاسبه قیمت ارز به تومان
    toman_info_html = ""
    if dollar_price:
        price_in_toman = current_price * dollar_price
        price_in_toman_clean = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

        toman_info_html = f"""
        <tr>
            <td style="padding: 8px 0; font-weight: bold; text-align: left;">💵 قیمت دلار (تتر):</td>
            <td style="padding: 8px 0; text-align: right; color: #e67e22;">{int(dollar_price):,} تومان</td>
        </tr>
        <tr>
            <td style="padding: 8px 0; font-weight: bold; text-align: left;">🇮🇷 قیمت ورود به تومان:</td>
            <td style="padding: 8px 0; text-align: right; font-weight: bold; color: #8e44ad;">{price_in_toman_clean} تومان</td>
        </tr>
        """
    else:
        toman_info_html = """
        <tr>
            <td style="padding: 8px 0; font-weight: bold; text-align: left;">⚠️ قیمت تومانی:</td>
            <td style="padding: 8px 0; text-align: right; color: #7f8c8d;">خطا در دریافت قیمت از نوبیتکس</td>
        </tr>
        """

    # ساختن قالب ایمیل
    subject = f"🎯 [شبیه‌سازی] سیگنال خرید جدید: {coin_name}"

    body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; direction: rtl; text-align: right;">
            <div style="max-width: 500px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; background-color: #f9f9f9;">
                <h2 style="color: #2ecc71; text-align: center;">🤖 شبیه‌ساز معامله خودکار (تست OCO)</h2>
                <p>ربات یک موقعیت خرید جدید بر اساس استراتژی UT Bot در تایم‌فریم <b>{timeframe}</b> پیدا کرده است.</p>
                <hr style="border: 0; border-top: 1px solid #eee;">
                <table style="width: 100%; border-collapse: collapse; direction: ltr;">
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">جفت ارز:</td>
                        <td style="padding: 8px 0; text-align: right; color: #2980b9;">#{coin_name}/USDT</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">🟢 قیمت ورود فرضی:</td>
                        <td style="padding: 8px 0; text-align: right; font-weight: bold; color: #27ae60;">{current_price_clean} دلار</td>
                    </tr>
                    {toman_info_html}
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">🎯 حد سود (Target 3%):</td>
                        <td style="padding: 8px 0; text-align: right; color: #27ae60;">{target_price} دلار</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">🛑 حد ضرر (Stop Loss 1.5%):</td>
                        <td style="padding: 8px 0; text-align: right; color: #c0392b;">{stop_loss_price} دلار</td>
                    </tr>
                </table>
                <hr style="border: 0; border-top: 1px solid #eee;">
                <p style="font-size: 12px; color: #7f8c8d; text-align: center;">این یک معامله شبیه‌سازی شده است و هیچ پولی جابجا نشده است.</p>
            </div>
        </body>
    </html>
    """
    send_email_notification(subject, body)


def monitor_market():
    print("🚀 ربات شبیه‌ساز OCO فعال شد (بدون ریسک)...")

    symbols = ["TRX/USDT", "DOGS/USDT", "TNSR/USDT", "XRP/USDT", "BICO/USDT", "DOGE/USDT", "HOME/USDT", "HMSTR/USDT"]
    last_signals = {symbol: "HOLD" for symbol in symbols}

    # ارسال ایمیل تستی اولیه داخل بلاک امن
    send_email_notification("Trading Bot Activated",
                            f"<p>ربات شبیه‌ساز معاملات با موفقیت روی سیستم شما فعال شد و در حال رصد چارت {timeframe} است.</p>")

    while True:
        for symbol in symbols:
            try:
                df = get_kucoin_data(symbol, timeframe=timeframe, limit=50)
                if df is None or df.empty:
                    continue

                df = calculate_ut_bot(df)
                latest_row = df.iloc[-1]
                current_price = latest_row['close']
                current_signal = latest_row['signal']

                if current_signal != 'HOLD' and current_signal != last_signals[symbol]:
                    if current_signal == 'BUY':
                        simulate_oco_trade(symbol, current_price)
                    elif current_signal == 'SELL':
                        print(f"🔴 سیگنال فروش برای {symbol} در قیمت {current_price} صادر شد.")

                    last_signals[symbol] = current_signal

            except Exception as loop_error:
                print(f"⚠️ خطای موقت در رصد {symbol}: {loop_error}")
                continue

        time.sleep(60)


if __name__ == "__main__":
    monitor_market()