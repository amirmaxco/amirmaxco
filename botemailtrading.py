import time
import ccxt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# --- تنظیمات صرافی (تایم‌فریم بهینه شده ۲ ساعته) ---
exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '2h'  # پایش دقیق و نوسانی چارت ۲ ساعته

# --- تنظیمات ارسال ایمیل شما ---
SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
cc_email="www.rasul.mahmoudimajd1038@gmail.com"

def get_iran_dollar_price():
    try:
        url = "https://api.wallex.ir/v1/markets"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and 'result' in data and 'symbols' in data['result']:
            tether_toman = data['result']['symbols']['USDTTMN']['stats']['lastPrice']
            return int(float(tether_toman))
    except Exception:
        pass
    try:
        url = "https://api.ramzinex.com/v1/exchange/pairs/1"
        response = requests.get(url, timeout=5)
        data = response.json()
        if data and 'data' in data and 'sell' in data['data']:
            return int(data['data']['sell'] / 10)
    except Exception:
        pass
    return 65000


def send_email_notification(subject, body, cc_email=cc_email):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject

    # اضافه کردن CC
    if cc_email:
        msg['Cc'] = cc_email

    msg.attach(MIMEText(body, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)

        # برای اینکه به CC هم ارسال شود، باید آدرس آن را در لیست گیرندگان قرار دهی
        recipients = [RECEIVER_EMAIL]
        if cc_email:
            recipients.append(cc_email)

        server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        server.quit()
        print(f"📧 ایمیل ارسال شد به {RECEIVER_EMAIL} و CC: {cc_email}")
    except Exception as e:
        print(f"⚠️ خطا در ایمیل: {e}")


def get_kucoin_data(symbol, timeframe, limit=50):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception as e:
        print(f"❌ خطا در دیتای {symbol}: {e}")
        return None


def calculate_ut_bot(df):
    # کالیبره شده برای نوسان‌گیری هوشمند چارت ۲ ساعته
    sensibility = 2.0
    atr_period = 10

    df['EMA'] = df['close'].ewm(span=10, adjust=False).mean()

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df['ATR'] = ranges.max(axis=1).rolling(atr_period).mean()

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
        if df['close'].iloc[i] > df['trailing_stop'].iloc[i] and df['close'].iloc[i - 1] <= df['trailing_stop'].iloc[
            i - 1]:
            df.at[df.index[i], 'signal'] = 'BUY'
        elif df['close'].iloc[i] < df['trailing_stop'].iloc[i] and df['close'].iloc[i - 1] >= df['trailing_stop'].iloc[
            i - 1]:
            df.at[df.index[i], 'signal'] = 'SELL'

    return df


def simulate_oco_trade(symbol, current_price, atr_value):
    coin_name = symbol.split('/')[0]
    dollar_price = get_iran_dollar_price()

    # در نظر گرفتن جوانب ریسک: نسبت ریسک به ریوارد ۱ به ۲ متناسب با تایم‌فریم ۲ ساعته
    stop_raw = current_price - (1.5 * atr_value)
    target_raw = current_price + (3.0 * atr_value)

    if stop_raw <= 0:
        stop_raw = current_price * 0.96

    price_in_toman = current_price * dollar_price
    target_in_toman = target_raw * dollar_price
    stop_in_toman = stop_raw * dollar_price

    toman_entry = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"
    toman_target = f"{target_in_toman:,.2f}" if target_in_toman < 100 else f"{int(target_in_toman):,}"
    toman_stop = f"{stop_in_toman:,.2f}" if stop_in_toman < 100 else f"{int(stop_in_toman):,}"

    subject = f"🎯 [نوسان‌گیری ۲ ساعته] سیگنال ورود: {coin_name}"

    body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; direction: rtl; text-align: right;">
            <div style="max-width: 500px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; background-color: #f9f9f9;">
                <h2 style="color: #2980b9; text-align: center;">🚀 موقعیت نوسانی ۲ ساعته (ریسک به ریوارد 1:2)</h2>
                <hr style="border: 0; border-top: 1px solid #eee;">
                <table style="width: 100%; border-collapse: collapse; direction: ltr;">
                    <tr style="border-bottom: 1px solid #eee;">
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">جفت ارز:</td>
                        <td style="padding: 8px 0; text-align: right; color: #2980b9;">#{coin_name}/IRT</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">💵 دلار تتر مبنا:</td>
                        <td style="padding: 8px 0; text-align: right; color: #e67e22;">{int(dollar_price):,} تومان</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">🟢 قیمت ورود:</td>
                        <td style="padding: 8px 0; text-align: right; font-weight: bold; color: #27ae60;">{toman_entry} تومان</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">🎯 حد سود (R=2):</td>
                        <td style="padding: 8px 0; text-align: right; font-weight: bold; color: #2ecc71;">{toman_target} تومان</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; font-weight: bold; text-align: left;">🛑 حد ضرر پاشنه:</td>
                        <td style="padding: 8px 0; text-align: right; font-weight: bold; color: #c0392b;">{toman_stop} تومان</td>
                    </tr>
                </table>
            </div>
        </body>
    </html>
    """
    send_email_notification(subject, body,cc_email=cc_email)


def simulate_sell_trade(symbol, current_price):
    coin_name = symbol.split('/')[0]
    dollar_price = get_iran_dollar_price()
    price_in_toman = current_price * dollar_price
    toman_price = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

    subject = f"🚨 [نوسان‌گیری ۲ ساعته] خروج سریع: {coin_name}"
    body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; direction: rtl; text-align: right;">
            <div style="max-width: 500px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; background-color: #fdf2f2;">
                <h3 style="color: #e74c3c; text-align: center;">📉 تغییر روند و خروج اندیکاتور</h3>
                <h3 style="color: #e74c3c; text-align: center;">{coin_name}</h3>
                <p style="text-align: center;">قیمت فروش تومانی: <b>{toman_price} تومان</b></p>
            </div>
        </body>
    </html>
    """
    send_email_notification(subject, body,cc_email=cc_email)


def monitor_market():
    print(f"🚀 ربات نوسان‌گیر ۲ ساعته با واچ‌لیست ۲۵ تایی فعال شد...")

    # لیست ۲۵ تایی شما (ارز حذف شده یا بدون دیتا به طور خودکار skip می‌شود)
    symbols = [
        "ADA/USDT", "POL/USDT", "ALGO/USDT", "XLM/USDT", "S/USDT", "HBAR/USDT", "ONE/USDT", "ZIL/USDT",
        "VET/USDT", "GRT/USDT", "STX/USDT", "BICO/USDT", "RENDER/USDT", "ANKR/USDT", "IOTX/USDT", "JASMY/USDT",
        "TRX/USDT", "XRP/USDT", "DOGE/USDT",  "CRO/USDT",
        "TNSR/USDT", "DOGS/USDT", "HOME/USDT", "HMSTR/USDT","BASED/USDT","A/USDT","APE/USDT","DYDX/USDT",
    ]

    last_signals = {symbol: "HOLD" for symbol in symbols}
    send_email_notification("2-Hour Trading Bot Active", f"<p>ربات با چرخه پایش ۲ ساعته روی سیستم امیر فعال شد.</p>")

    while True:
        print(f"🔄 شروع پایش دوره ای بازار در تاریخ: {pd.Timestamp.now()}")

        for symbol in symbols:
            try:
                df = get_kucoin_data(symbol, timeframe=timeframe, limit=50)
                if df is None or df.empty or len(df) < 3:
                    continue

                df = calculate_ut_bot(df)

                # 🛑 بسیار مهم: بررسی کندل بسته شده قبلی [-2] برای جلوگیری از سیگنال فیک و اصلاحی
                confirmed_row = df.iloc[-2]
                current_price = df.iloc[-1]['close']  # قیمت لحظه ای برای ورود
                current_signal = confirmed_row['signal']

                atr_value = confirmed_row['ATR'] if 'ATR' in confirmed_row and not pd.isna(confirmed_row['ATR']) else (
                            current_price * 0.01)

                if current_signal != 'HOLD' and current_signal != last_signals[symbol]:
                    if current_signal == 'BUY':
                        simulate_oco_trade(symbol, current_price, atr_value)
                    elif current_signal == 'SELL':
                        simulate_sell_trade(symbol, current_price)

                    last_signals[symbol] = current_signal

                # یک تاخیر ریز بین هر درخواست برای رعایت قوانین Rate Limit کوکوین
                time.sleep(1.5)

            except Exception as e:
                print(f"⚠️ خطای موقت در جفت‌ارز {symbol}: {e}")
                continue

        # ⏰ استراحت هوشمند: هر ۱۵ دقیقه یک‌بار بازار را چک کند (به جای هر ۱ دقیقه)
        print("💤 پایش این دوره تمام شد. رفتن به استراحت 10 دقیقه‌ای...")
        time.sleep(600)


if __name__ == "__main__":
    monitor_market()