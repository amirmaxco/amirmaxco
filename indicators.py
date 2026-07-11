import ta
import pandas as pd

def calculate_ut_bot_2h_live(df, sensitivity=4, atr_period=14):
    MIN_SCORE=65
    if len(df) < 60:
        df["signal"] = "HOLD"
        return df

    # =========================
    # اندیکاتورها
    # =========================

    df["EMA20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["EMA50"] = ta.trend.ema_indicator(df["close"], window=50)

    macd = ta.trend.MACD(df["close"])
    df["MACD"] = macd.macd()
    df["MACD_SIGNAL"] = macd.macd_signal()

    df["RSI"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

    df["ATR"] = ta.volatility.AverageTrueRange(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).average_true_range()

    df["VOL_MA"] = df["volume"].rolling(20).mean()

    df["signal"] = "HOLD"

    # =========================
    # محاسبه سیگنال
    # =========================

    for i in range(50, len(df)):

        score = 0

        # روند
        if df["EMA20"].iloc[i] > df["EMA50"].iloc[i]:
            score += 25

        # قیمت بالای EMA20
        if df["close"].iloc[i] > df["EMA20"].iloc[i]:
            score += 15

        # مکدی
        if df["MACD"].iloc[i] > df["MACD_SIGNAL"].iloc[i]:
            score += 25

        # RSI
        rsi = df["RSI"].iloc[i]

        if 45 <= rsi <= 75:
            score += 20

        # حجم
        if df["volume"].iloc[i] > df["VOL_MA"].iloc[i] * 1.10:
            score += 15

        # =======================
        # BUY
        # =======================

        if score >= MIN_SCORE:
            df.at[df.index[i], "signal"] = "BUY"

        # =======================
        # SELL
        # =======================

        elif (
            df["EMA20"].iloc[i] < df["EMA50"].iloc[i]
            and df["MACD"].iloc[i] < df["MACD_SIGNAL"].iloc[i]
        ):
            df.at[df.index[i], "signal"] = "SELL"

    return df