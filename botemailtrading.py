
# -*- coding: utf-8 -*-
"""
Nobitex UT Bot 3/10
Daily + 1H confirmation
BUY and SELL signal monitoring
Prices and ATR in Toman
No real orders are placed.
"""

import os
import time
import logging
from logging.handlers import TimedRotatingFileHandler

import requests
import pandas as pd
import numpy as np
import ta
import csv
from datetime import datetime

SIGNALS_FILE = "signals_history.csv"

# ================= SETTINGS =================
SENSITIVITY = 3.0
ATR_PERIOD = 10
SCAN_INTERVAL = 300
REQUEST_TIMEOUT = 15

API_HISTORY = "https://apiv2.nobitex.ir/market/udf/history"
API_STATS = "https://apiv2.nobitex.ir/market/stats"

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
CC_EMAIL ="" #"www.rasul.mahmoudimajd1038@gmail.com"

NOBITEX_TOKEN = "o5TJUZrJoLj7afjp3jxhYa2wixNdKI4gdX8KVtj9Htk="
NOBITEX_TOKEN_PUBLIC = "4f607aff93a0f574deeda11c0a88c8d89ecc56af"

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT",
    "NEAR/USDT", "SUI/USDT", "TRX/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "LINK/USDT", "UNI/USDT",
    "LTC/USDT", "BCH/USDT", "POL/USDT", "ALGO/USDT",
    "XLM/USDT", "HBAR/USDT", "GRT/USDT", "ANKR/USDT",
    "DOGS/USDT", "TNSR/USDT", "RENDER/USDT", "APE/USDT",
    "DYDX/USDT", "ONE/USDT", "BICO/USDT", "NOT/USDT",
    "KAITO/USDT", "PUMP/USDT", "BARD/USDT", "PROM/USDT",
    "LA/USDT", "ZAMA/USDT", "HOME/USDT", "XAUT/USDT",
    "TURBO/USDT", "T/USDT"
]

TIMEFRAMES = {"1h": "60", "1d": "D"}

# ================= LOGGING =================
logger = logging.getLogger("NobitexUTBot")
logger.setLevel(logging.INFO)

if not logger.handlers:
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    fh = TimedRotatingFileHandler(
        "botmailtrading.log",
        when="midnight",
        backupCount=7,
        encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)


# ================= HTTP =================
def get_json(url, params=None):
    try:
        response = requests.get(
            url, params=params, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("API error: %s", exc)
        return None


def get_usdt_toman():
    data = get_json(
        API_STATS,
        {"srcCurrency": "usdt", "dstCurrency": "rls"}
    )

    if not data or data.get("status") != "ok":
        return None

    stats = data.get("stats", {})
    item = stats.get("usdt-rls", {})

    try:
        price = float(item["latest"]) / 10
        return price if price > 0 else None
    except (KeyError, TypeError, ValueError):
        logger.error("USDT/Rial price not found in API response.")
        return None


def get_coin_toman(coin):
    data = get_json(
        API_STATS,
        {"srcCurrency": coin.lower(), "dstCurrency": "rls"}
    )

    if not data or data.get("status") != "ok":
        return None

    try:
        price = float(data["stats"][f"{coin.lower()}-rls"]["latest"]) / 10
        return price if price > 0 else None
    except (KeyError, TypeError, ValueError):
        return None


def get_candles(symbol, timeframe, limit=300):
    resolution = TIMEFRAMES[timeframe]
    base, quote = symbol.split("/")

    seconds_per_candle = 86400 if timeframe == "1d" else 3600
    end_time = int(time.time())
    start_time = end_time - limit * seconds_per_candle

    data = get_json(
        API_HISTORY,
        {
            "symbol": f"{base.upper()}{quote.upper()}",
            "resolution": resolution,
            "from": start_time,
            "to": end_time
        }
    )

    if not data or data.get("s") != "ok":
        logger.warning("%s %s: candle data unavailable", symbol, timeframe)
        return None

    keys = ("t", "o", "h", "l", "c", "v")
    if not all(key in data for key in keys):
        return None

    df = pd.DataFrame({
        "timestamp": data["t"],
        "open": data["o"],
        "high": data["h"],
        "low": data["l"],
        "close": data["c"],
        "volume": data["v"]
    })

    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = (
        df.dropna(subset=["timestamp", "open", "high", "low", "close"])
          .sort_values("timestamp")
          .drop_duplicates("timestamp")
          .reset_index(drop=True)
    )

    return df if not df.empty else None


# ================= UT BOT 3,10 =================
def calculate_utbot(df):
    out = df.copy()

    out["ATR"] = ta.volatility.average_true_range(
        out["high"],
        out["low"],
        out["close"],
        window=ATR_PERIOD,
        fillna=False
    )

    close = out["close"].to_numpy(dtype=float)
    atr = out["ATR"].to_numpy(dtype=float)
    n = len(out)

    trailing_stop = np.full(n, np.nan)
    signals = np.full(n, "HOLD", dtype=object)
    position = np.zeros(n, dtype=int)

    valid_rows = np.flatnonzero(np.isfinite(atr) & (atr > 0))

    if len(valid_rows) == 0:
        out["TrailingStop"] = trailing_stop
        out["signal"] = signals
        out["UT_Bias"] = "NEUTRAL"
        return out

    first = int(valid_rows[0])
    trailing_stop[first] = close[first] - SENSITIVITY * atr[first]
    position[first] = 1

    for i in range(first + 1, n):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            trailing_stop[i] = trailing_stop[i - 1]
            position[i] = position[i - 1]
            continue

        previous_close = close[i - 1]
        current_close = close[i]
        previous_stop = trailing_stop[i - 1]
        loss = SENSITIVITY * atr[i]

        if not np.isfinite(previous_stop):
            trailing_stop[i] = current_close - loss
            position[i] = 1
            continue

        if previous_close > previous_stop and current_close > previous_stop:
            trailing_stop[i] = max(previous_stop, current_close - loss)
        elif previous_close < previous_stop and current_close < previous_stop:
            trailing_stop[i] = min(previous_stop, current_close + loss)
        elif current_close > previous_stop:
            trailing_stop[i] = current_close - loss
        else:
            trailing_stop[i] = current_close + loss

        # Crossover signals
        if previous_close <= previous_stop and current_close > previous_stop:
            signals[i] = "BUY"
            position[i] = 1
        elif previous_close >= previous_stop and current_close < previous_stop:
            signals[i] = "SELL"
            position[i] = -1
        else:
            position[i] = position[i - 1]

    out["TrailingStop"] = trailing_stop
    out["signal"] = signals
    out["UT_Position"] = position
    out["UT_Bias"] = np.select(
        [
            out["close"] > out["TrailingStop"],
            out["close"] < out["TrailingStop"]
        ],
        ["BULLISH", "BEARISH"],
        default="NEUTRAL"
    )

    # Do not act on the potentially unfinished last candle.
    if n:
        out.loc[n - 1, "signal"] = "HOLD"

    return out



def calculate_trade_levels(price, atr, signal):
    """
    همه ورودی‌ها و خروجی‌ها به تومان هستند.
    ریسک بر اساس 1.5 ATR و هدف‌ها با نسبت سود به ریسک 1:1 و 1:2.
    """
    price = float(price)
    atr = float(atr)

    if price <= 0 or atr <= 0:
        raise ValueError("قیمت و ATR باید مثبت باشند")

    risk = 1.5 * atr

    if signal == "BUY":
        stop_loss = price - risk
        target1 = price + risk
        target2 = price + (2 * risk)

    elif signal == "SELL":
        stop_loss = price + risk
        target1 = price - risk
        target2 = price - (2 * risk)

    else:
        return None

    return {
        "entry": price,
        "stop_loss": stop_loss,
        "target1": target1,
        "target2": target2,
        "risk": risk,
        "rr1": 1.0,
        "rr2": 2.0
    }


# ================= SIGNAL HISTORY =================

SIGNAL_FIELDS = [
    "signal_id",
    "timestamp",
    "symbol",
    "signal",
    "daily_bias",
    "entry",
    "atr",
    "stop_loss",
    "target1",
    "target2",
    "status",
    "exit_price",
    "result",
    "closed_at",
]


def get_last_saved_signal(symbol):
    """خواندن آخرین سیگنال ثبت‌شده برای یک ارز."""

    if not os.path.exists(SIGNALS_FILE):
        return None

    try:
        with open(SIGNALS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            rows = [
                row for row in csv.DictReader(f)
                if row.get("symbol") == symbol
                and row.get("signal") in ("BUY", "SELL")
            ]

        if not rows:
            return None

        rows.sort(
            key=lambda row: row.get("timestamp", ""),
            reverse=True
        )

        return rows[0]

    except (OSError, csv.Error) as exc:
        logger.error("Cannot read saved signal for %s: %s", symbol, exc)
        return None


def ensure_signals_file():
    if not os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=SIGNAL_FIELDS)
            writer.writeheader()


def record_signal(symbol, signal, daily_bias, price, atr, candle_timestamp):
    """ذخیره سیگنال جدید همراه با ورود، استاپ و تارگت‌ها."""

    levels = calculate_trade_levels(price, atr, signal)
    if levels is None:
        return

    ensure_signals_file()

    now = datetime.now().astimezone().isoformat(timespec="seconds")

    record = {
        "signal_id": f"{symbol}_{signal}_{int(candle_timestamp)}",
        "timestamp": now,
        "symbol": symbol,
        "signal": signal,
        "daily_bias": daily_bias,
        "entry": round(levels["entry"], 2),
        "atr": round(float(atr), 2),
        "stop_loss": round(levels["stop_loss"], 2),
        "target1": round(levels["target1"], 2),
        "target2": round(levels["target2"], 2),
        "status": "OPEN",
        "exit_price": "",
        "result": "PENDING",
        "closed_at": "",
    }

    # جلوگیری از ثبت دوباره همان سیگنال
    existing_ids = set()

    with open(SIGNALS_FILE, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing_ids.add(row.get("signal_id", ""))

    if record["signal_id"] in existing_ids:
        logger.info("%s | Signal already recorded", symbol)
        return

    with open(SIGNALS_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SIGNAL_FIELDS)
        writer.writerow(record)

    logger.info(
        "%s | Signal saved | %s | Entry=%.2f | SL=%.2f | TP1=%.2f | TP2=%.2f",
        symbol, signal, levels["entry"], levels["stop_loss"],
        levels["target1"], levels["target2"]
    )


def signal_already_recorded(symbol, signal, candle_timestamp):
    """بررسی فایل تاریخچه برای جلوگیری از سیگنال تکراری."""

    if not os.path.exists(SIGNALS_FILE):
        return False

    signal_id = f"{symbol}_{signal}_{int(candle_timestamp)}"

    try:
        with open(
            SIGNALS_FILE,
            "r",
            newline="",
            encoding="utf-8-sig"
        ) as f:
            reader = csv.DictReader(f)

            for row in reader:
                if row.get("signal_id") == signal_id:
                    return True

    except (OSError, csv.Error) as exc:
        logger.error("Cannot read signal history: %s", exc)
        # اگر فایل قابل خواندن نیست، برای جلوگیری از ایمیل تکراری
        # سیگنال جدید ارسال نشود.
        return True

    return False


def update_open_signals(symbol, hourly_df):
    """
    پیگیری سیگنال‌های باز با کندل‌های بسته‌شده.
    اگر در یک کندل هم استاپ و هم تارگت لمس شوند،
    برای محافظه‌کاری استاپ را مقدم فرض می‌کنیم.
    """

    if not os.path.exists(SIGNALS_FILE):
        return

    ensure_signals_file()

    with open(SIGNALS_FILE, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    changed = False

    for record in rows:
        if record.get("symbol") != symbol:
            continue

        if record.get("status") != "OPEN":
            continue

        try:
            signal_time = int(record["signal_id"].rsplit("_", 1)[1])
            entry = float(record["entry"])
            stop = float(record["stop_loss"])
            tp1 = float(record["target1"])
            tp2 = float(record["target2"])
        except (ValueError, KeyError, IndexError):
            logger.warning("Invalid signal record: %s", record.get("signal_id"))
            continue

        # فقط کندل‌هایی که بعد از کندل سیگنال شروع شده‌اند
        later = hourly_df[hourly_df["timestamp"] > signal_time]

        if later.empty:
            continue

        for _, candle in later.iterrows():
            high = float(candle["high"])
            low = float(candle["low"])

            if record["signal"] == "BUY":
                if low <= stop:
                    status, exit_price, result = "STOP_LOSS", stop, "LOSS"
                elif high >= tp2:
                    status, exit_price, result = "TP2", tp2, "WIN_TP2"
                elif high >= tp1:
                    status, exit_price, result = "TP1", tp1, "WIN_TP1"
                else:
                    continue

            elif record["signal"] == "SELL":
                if high >= stop:
                    status, exit_price, result = "STOP_LOSS", stop, "LOSS"
                elif low <= tp2:
                    status, exit_price, result = "TP2", tp2, "WIN_TP2"
                elif low <= tp1:
                    status, exit_price, result = "TP1", tp1, "WIN_TP1"
                else:
                    continue
            else:
                continue

            record["status"] = status
            record["exit_price"] = round(exit_price, 2)
            record["result"] = result
            record["closed_at"] = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )

            logger.info(
                "%s | Signal outcome=%s | Exit=%.2f",
                symbol, result, exit_price
            )
            changed = True
            break

    if changed:
        temp_file = SIGNALS_FILE + ".tmp"

        with open(temp_file, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=SIGNAL_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

        os.replace(temp_file, SIGNALS_FILE)


# ================= EMAIL ALERTS =================

def send_email(symbol, signal, daily_bias, price, atr):
    if not (SENDER_EMAIL and SENDER_PASSWORD and RECEIVER_EMAIL):
        return

    import smtplib
    from email.mime.text import MIMEText

    levels = calculate_trade_levels(price, atr, signal)
    if levels is None:
        return

    direction = "خرید (BUY)" if signal == "BUY" else "فروش (SELL)"

    body = f"""
سیگنال جدید ربات Nobitex UT Bot

نماد: {symbol}
جهت سیگنال: {direction}
روند روزانه: {daily_bias}

قیمت ورود: {levels['entry']:,.2f} تومان
حد ضرر (SL): {levels['stop_loss']:,.2f} تومان
هدف اول (TP1): {levels['target1']:,.2f} تومان
هدف دوم (TP2): {levels['target2']:,.2f} تومان

ATR: {float(atr):,.2f} تومان
ریسک قیمتی: {levels['risk']:,.2f} تومان
نسبت سود به زیان هدف اول: 1:1
نسبت سود به زیان هدف دوم: 1:2

توجه: این‌ها سطوح محاسباتی هستند، نه سفارش ثبت‌شده.
برای SELL، اجرای فروش تعهدی و امکان معامله باید جداگانه بررسی شود.
"""

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = f"UT Bot {signal} - {symbol}"
    message["From"] = SENDER_EMAIL
    message["To"] = RECEIVER_EMAIL

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(
                SENDER_EMAIL,
                [RECEIVER_EMAIL],
                message.as_string()
            )
    except Exception as exc:
        logger.warning("Email error: %s", exc)


# ================= SIGNAL RULES =================
def analyze_symbol(symbol, usdt_toman):

    daily = get_candles(symbol, "1d", 100)
    hourly = get_candles(symbol, "1h", 300)

    if daily is None or hourly is None:
        logger.info("%s | HOLD | missing candles", symbol)
        return

    if len(daily) < 20 or len(hourly) < 30:
        logger.info("%s | HOLD | insufficient history", symbol)
        return

    # Convert all OHLC prices to Toman before calculating ATR.
    for df in (daily, hourly):
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float) * usdt_toman

    daily_ind = calculate_utbot(daily)
    hourly_ind = calculate_utbot(hourly)

    # پیگیری نتایج سیگنال‌های قبلی
    update_open_signals(symbol, hourly_ind)

    # Assumes the final row is the current, unfinished candle.
    d = daily_ind.iloc[-2]
    h = hourly_ind.iloc[-2]
    previous_h = hourly_ind.iloc[-3]

    daily_bias = str(d["UT_Bias"])
    hourly_signal = str(h["signal"]).upper()
    previous_signal = str(previous_h["signal"]).upper()

    if daily_bias == "BULLISH" and hourly_signal == "BUY":
        final_signal = "BUY"
        trade_direction = "LONG / خرید"
    elif daily_bias == "BEARISH" and hourly_signal == "SELL":
        final_signal = "SELL"
        trade_direction = "SHORT signal / فروش تعهدی بالقوه"
    else:
        final_signal = "HOLD"
        trade_direction = "بدون ورود"

    atr = h["ATR"]
    if pd.isna(atr) or float(atr) <= 0:
        logger.info("%s | HOLD | ATR unavailable", symbol)
        return

    coin = symbol.split("/")[0]
    price = get_coin_toman(coin)
    if price is None:
        price = float(h["close"])

    saved_signal = get_last_saved_signal(symbol)

    # ================= CONSOLE OUTPUT =================

    separator = "=" * 110

    # محاسبه ورود، حد ضرر و تارگت‌ها برای نمایش

    levels = None
    display_signal = final_signal
    display_price = price
    display_atr = float(atr)

    if final_signal in ("BUY", "SELL") and pd.notna(atr) and float(atr) > 0:
        levels = calculate_trade_levels(price, float(atr), final_signal)

    elif saved_signal:
        # نمایش آخرین سیگنال ذخیره‌شده، حتی اگر سیگنال فعلی HOLD باشد
        display_signal = saved_signal["signal"]

        try:
            display_price = float(saved_signal["entry"])
            display_atr = float(saved_signal["atr"])

            levels = {
                "entry": float(saved_signal["entry"]),
                "stop_loss": float(saved_signal["stop_loss"]),
                "target1": float(saved_signal["target1"]),
                "target2": float(saved_signal["target2"]),
            }
        except (ValueError, KeyError, TypeError):
            levels = None

    if final_signal == "BUY":
        color = "\033[92m"  # سبز
    elif final_signal == "SELL":
        color = "\033[91m"  # قرمز
    else:
        color = "\033[97m"  # سفید

    reset = "\033[0m"

    print(f"\n{separator}")
    print(f" ارز: {symbol}")
    print(f"{'-' * 110}")

    print(
        f"Daily: {daily_bias:<8} | "
        f"1H UT Bot: {hourly_signal:<4} | "
        f"FINAL: {color}{final_signal:<4}{reset} | "
        f"جهت: {trade_direction}"
    )

    print(
        f"قیمت لحظه‌ای: {price:,.2f} تومان | "
        f"ATR: {float(atr):,.2f} تومان"
    )

    if levels:
        print(f"قیمت ورود محاسباتی: {levels['entry']:,.2f} تومان")
        print(f"حد ضرر (SL):        {levels['stop_loss']:,.2f} تومان")
        print(f"هدف اول (TP1):      {levels['target1']:,.2f} تومان")
        print(f"هدف دوم (TP2):      {levels['target2']:,.2f} تومان")
        print(
            f"نسبت سود به زیان: TP1 = 1:1 | TP2 = 1:2"
        )
    else:
        print("وضعیت: سیگنال ورود جدید وجود ندارد.")
        print("قیمت ورود / حد ضرر / تارگت: محاسبه نشده")

    levels = None
    print(separator)

    logger.info(
        "%s | Daily=%s | 1H=%s | FINAL=%s | Price=%.2f | ATR=%.2f",
        symbol, daily_bias, hourly_signal, final_signal,
        price, float(atr)
    )

    # Alert only for a new qualifying crossover.
    is_new_signal = (
        hourly_signal in ("BUY", "SELL")
        and hourly_signal != previous_signal
    )

    if is_new_signal and final_signal in ("BUY", "SELL"):
        signal_candle_timestamp = int(h["timestamp"])

        if signal_already_recorded(
                symbol,
                final_signal,
                signal_candle_timestamp
        ):
            logger.info(
                "%s | %s | DUPLICATE BLOCKED | candle=%s",
                symbol,
                final_signal,
                signal_candle_timestamp
            )
            return

        record_signal(
            symbol=symbol,
            signal=final_signal,
            daily_bias=daily_bias,
            price=price,
            atr=float(atr),
            candle_timestamp=signal_candle_timestamp,
        )

        send_email(
            symbol,
            final_signal,
            daily_bias,
            price,
            float(atr)
        )


# ================= MAIN LOOP =================
def monitor_market():
    logger.info("Started: UT Bot 3/10, Daily + 1H, BUY/SELL/HOLD")
    logger.info("Monitor-only mode. No orders or short positions are placed.")

    while True:
        try:
            usdt_toman = get_usdt_toman()

            if usdt_toman is None or usdt_toman <= 0:
                logger.error("USDT/Toman price unavailable; scan skipped.")
            else:
                logger.info("USDT = %.2f Toman", usdt_toman)

                for symbol in SYMBOLS:
                    try:
                        analyze_symbol(symbol, usdt_toman)
                    except Exception:
                        logger.exception("Error analysing %s", symbol)
                    time.sleep(1.1)

        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            break
        except Exception:
            logger.exception("Unexpected monitor error")

        try:
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            logger.info("Stopped by user.")
            break


if __name__ == "__main__":
    monitor_market()
