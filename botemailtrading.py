import time
import ccxt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import ta

# --- تنظیمات صرافی (تایم‌فریم ۲ ساعته - فرکانس بالا + ارزهای زیر 100 هزار تومان) ---
exchange = ccxt.kucoin({'enableRateLimit': True})
timeframe = '2h'

# --- تنظیمات ارسال ایمیل شما ---
SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
cc_email = "www.rasul.mahmoudimajd1038@gmail.com"


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

    if cc_email:
        msg['Cc'] = cc_email

    msg.attach(MIMEText(body, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)

        recipients = [RECEIVER_EMAIL]
        if cc_email:
            recipients.append(cc_email)

        server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        server.quit()
        print(f"📧 ایمیل ارسال شد به {RECEIVER_EMAIL}")
    except Exception as e:
        print(f"⚠️ خطا در ارسال ایمیل: {e}")


def get_kucoin_data(symbol, timeframe, limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    except Exception:
        return None


def calculate_ut_bot_professional(df):
    sensibility = 2.0
    atr_period = 10
    df['EMA_10'] = df['close'].ewm(span=10, adjust=False).mean()

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df['ATR'] = ranges.max(axis=1).rolling(atr_period).mean()

    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()

    adx_indicator = ta.trend.ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
    df['ADX'] = adx_indicator.adx()
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
            if df['close'].iloc[i] > df['EMA_200'].iloc[i] and df['ADX'].iloc[i] > 16 and df['RSI'].iloc[i] < 72:
                df.at[df.index[i], 'signal'] = 'BUY'
            else:
                df.at[df.index[i], 'signal'] = 'HOLD'
        elif ut_sell:
            df.at[df.index[i], 'signal'] = 'SELL'

    return df


def simulate_oco_trade(symbol, current_price, atr_value, rsi_value):
    coin_name = symbol.split('/')[0]
    dollar_price = get_iran_dollar_price()

    stop_raw = current_price - (1.5 * atr_value)
    target_raw = current_price + (3.0 * atr_value)

    if stop_raw <= 0:
        stop_raw = current_price * 0.96

    risk_percentage = ((current_price - stop_raw) / current_price) * 100
    recommended_position_size = 200 / risk_percentage if risk_percentage > 0 else 10
    if recommended_position_size > 100: recommended_position_size = 100

    price_in_toman = current_price * dollar_price
    target_in_toman = target_raw * dollar_price
    stop_in_toman = stop_raw * dollar_price

    toman_entry = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"
    toman_target = f"{target_in_toman:,.2f}" if target_in_toman < 100 else f"{int(target_in_toman):,}"
    toman_stop = f"{stop_in_toman:,.2f}" if stop_in_toman < 100 else f"{int(stop_in_toman):,}"

    subject = f"🎯 [سیگنال خرید] موقعیت ورود: {coin_name}"

    body = f"""
    <html>
        <body style="font-family: Tahoma, Arial, sans-serif; line-height: 1.8; color: #333; direction: rtl; text-align: right;">
            <div style="max-width: 500px; margin: auto; padding: 25px; border: 1px solid #e0e0e0; border-radius: 12px; background-color: #ffffff; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                <h2 style="color: #27ae60; text-align: center; margin-bottom: 20px;">🚀 سیگنال خرید فعال (ارزهای زیر 100 هزار تومان)</h2>
                <hr style="border: 0; border-top: 1px solid #eee; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse; direction: rtl; text-align: right;">
                    <tr style="border-bottom: 1px solid #f5f5f5;">
                        <td style="padding: 10px 0; font-weight: bold; color: #7f8c8d;">جفت ارز:</td>
                        <td style="padding: 10px 0; font-weight: bold; color: #2980b9; text-align: left;">#{coin_name}/تومان</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f5f5f5;">
                        <td style="padding: 10px 0; font-weight: bold; color: #7f8c8d;">💵 دلار تتر مبنا:</td>
                        <td style="padding: 10px 0; color: #e67e22; font-weight: bold; text-align: left;">{int(dollar_price):,} تومان</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f5f5f5;">
                        <td style="padding: 10px 0; font-weight: bold; color: #7f8c8d;">🟢 قیمت ورود:</td>
                        <td style="padding: 10px 0; font-weight: bold; color: #27ae60; text-align: left; font-size: 16px;">{toman_entry} تومان</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f5f5f5;">
                        <td style="padding: 10px 0; font-weight: bold; color: #7f8c8d;">🎯 حد سود (تارگت):</td>
                        <td style="padding: 10px 0; font-weight: bold; color: #2ecc71; text-align: left; font-size: 16px;">{toman_target} تومان</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f5f5f5;">
                        <td style="padding: 10px 0; font-weight: bold; color: #7f8c8d;">🛑 حد ضرر (استاپ):</td>
                        <td style="padding: 10px 0; font-weight: bold; color: #c0392b; text-align: left; font-size: 16px;">{toman_stop} تومان</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #f5f5f5;">
                        <td style="padding: 10px 0; font-weight: bold; color: #7f8c8d;">📊 شاخص قدرت (RSI):</td>
                        <td style="padding: 10px 0; font-weight: bold; color: #9b59b6; text-align: left;">{rsi_value:.1f}</td>
                    </tr>
                </table>
            </div>
        </body>
    </html>
    """
    send_email_notification(subject, body, cc_email=cc_email)


def simulate_sell_trade(symbol, current_price):
    coin_name = symbol.split('/')[0]
    dollar_price = get_iran_dollar_price()
    price_in_toman = current_price * dollar_price
    toman_price = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

    subject = f"🚨 [سیگنال خروج] فروش: {coin_name}"
    body = f"""
    <html>
        <body style="font-family: Tahoma, Arial, sans-serif; line-height: 1.8; color: #333; direction: rtl; text-align: right;">
            <div style="max-width: 500px; margin: auto; padding: 25px; border: 1px solid #fecdcd; border-radius: 12px; background-color: #fffbfb; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
                <h3 style="color: #c0392b; text-align: center; margin-bottom: 15px;">📉 خروج و فروش</h3>
                <h2 style="color: #d35400; text-align: center; margin-bottom: 20px;">{coin_name}</h2>
                <hr style="border: 0; border-top: 1px solid #fecdcd; margin-bottom: 20px;">
                <p style="text-align: center; font-size: 15px;">قیمت پیشنهادی فروش: <b style="color: #c0392b; font-size: 18px;">{toman_price} تومان</b></p>
            </div>
        </body>
    </html>
    """
    send_email_notification(subject, body, cc_email=cc_email)


def monitor_market():
    print(f"🚀 ربات ۲ ساعته پرسرعت (ویژه ارزهای زیر 100 هزار تومان) فعال شد...")

    # 🛒 لیست اصلاح‌شده: ۴۳ آلت‌کوین محبوب بازار که قیمت همگی زیر ۱۰۰ هزار تومان است
    symbols = [
        "ADA/USDT", "POL/USDT", "ALGO/USDT", "XLM/USDT", "S/USDT", "HBAR/USDT", "ONE/USDT", "ZIL/USDT",
        "VET/USDT", "GRT/USDT", "STX/USDT", "BICO/USDT", "RENDER/USDT", "ANKR/USDT", "IOTX/USDT", "JASMY/USDT",
        "TRX/USDT", "XRP/USDT", "DOGE/USDT", "CRO/USDT", "TNSR/USDT", "DOGS/USDT", "HMSTR/USDT", "APE/USDT", "FET/USDT",
        "DOT/USDT", "SEI/USDT", "DYDX/USDT", "SUI/USDT", "FTM/USDT", "OP/USDT", "ARB/USDT", "GALA/USDT",
        "BASED/USDT", "BONK/USDT", "SAND/USDT", "MANA/USDT", "MASK/USDT", "LRC/USDT", "CHZ/USDT", "ENJ/USDT", "BAT/USDT"
    ]

    last_signals = {symbol: "HOLD" for symbol in symbols}
    send_email_notification("🔴 سیستم هوشمند سیگنال‌دهی فعال شد", "<p>پایش بازار آغاز شد.</p>")

    while True:
        print(f"\n🔄 --- شروع چرخه جدید پایش: {pd.Timestamp.now()} ---")

        dollar_price = get_iran_dollar_price()
        print(f"💵 نرخ تتر مبنا در این دوره: {dollar_price:,} تومان")

        btc_trend_ok = True
        try:
            btc_df = get_kucoin_data("BTC/USDT", timeframe='2h', limit=30)
            if btc_df is not None and not btc_df.empty:
                btc_ema_20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                btc_current_price = btc_df['close'].iloc[-1]

                if btc_current_price < (btc_ema_20 * 0.975):
                    btc_trend_ok = False
                    print("⚠️ بازار بیت‌کوین در ریزش شدید است. پایش آلت‌کوین‌ها موقتاً متوقف شد.")
        except Exception as e:
            print(f"خطا در دریافت دیتای بیت‌کوین: {e}")

        if not btc_trend_ok:
            time.sleep(300)
            continue

        for symbol in symbols:
            try:
                df = get_kucoin_data(symbol, timeframe=timeframe, limit=100)
                if df is None or df.empty or len(df) < 30:
                    continue

                df = calculate_ut_bot_professional(df)

                confirmed_row = df.iloc[-2]
                current_price = df.iloc[-1]['close']
                current_signal = confirmed_row['signal']

                price_in_toman = current_price * dollar_price

                # 🛑 فیلتر نهایی و قطعی برای بررسی قیمت تومانی در لحظه (زیر 100 هزار تومان)
                if price_in_toman > 100000:
                    continue

                if price_in_toman < 100:
                    toman_str = f"{price_in_toman:,.2f}"
                else:
                    toman_str = f"{int(price_in_toman):,}"

                avg_volume = df['volume'].rolling(window=20).mean().iloc[-2]
                current_volume = confirmed_row['volume']
                rsi_value = confirmed_row['RSI']

                print(
                    f"📊 {symbol:<10} | قیمت: {toman_str:<12} تومان | سیگنال: {current_signal:<5} | RSI: {rsi_value:.1f}")

                if current_signal == 'BUY' and current_signal != last_signals[symbol]:
                    if current_volume >= (avg_volume * 0.9):
                        atr_value = confirmed_row['ATR'] if 'ATR' in confirmed_row else (current_price * 0.02)
                        simulate_oco_trade(symbol, current_price, atr_value, rsi_value)
                        last_signals[symbol] = current_signal
                    else:
                        print(f"   ⏭️ سیگنال {symbol} به دلیل حجم مرده فیلتر شد.")

                elif current_signal == 'SELL' and current_signal != last_signals[symbol]:
                    simulate_sell_trade(symbol, current_price)
                    last_signals[symbol] = current_signal

                time.sleep(1.5)
            except Exception:
                continue

        print("\n💤 پایان چرخه پایش. استراحت ۱۰ دقیقه ای...")
        time.sleep(600)


if __name__ == "__main__":
    monitor_market()
