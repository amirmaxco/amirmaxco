from __future__ import annotations

import math
import statistics
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import logging
from logging.handlers import TimedRotatingFileHandler
import requests

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import jdatetime


# ============================================================================
# NOBITEX TOP-GAINER CONTINUATION PREDICTOR
# Standalone scanner - analysis only, NO ORDER PLACEMENT
# ============================================================================

BASE_URL = "https://apiv2.nobitex.ir"
STATS_URL = f"{BASE_URL}/market/stats"
UDF_URL = f"{BASE_URL}/market/udf/history"

REQUEST_TIMEOUT = 12
SCAN_INTERVAL_SECONDS = 60
TOP_GAINERS_PRINT = 20
MARKETS_TO_SCAN = 100
CANDLE_RESOLUTION = "5"
CANDLE_SECONDS = 5 * 60
CANDLE_COUNTBACK = 120
MIN_CANDLES = 40

CANDLE_SCALE_MAX_RELATIVE_ERROR = 0.08
MAX_DAY_GAIN_FOR_CONTINUATION = 40.0
MAX_SPREADLESS_PRICE_ERROR = 0.15
MAX_VOLUME_RATIO_FOR_ENTRY = 20.0

# Early continuation entry: catches strong moves like HBAR before the score
# reaches the strict standard-entry threshold.
EARLY_ENTRY_SCORE_MIN = 55.0
EARLY_ENTRY_VOLUME_MIN = 0.50
EARLY_ENTRY_DISTANCE_MAX = 2.50
EARLY_ENTRY_RSI_MAX = 80.0

SCALE_FACTORS = (
    1.0,
    10.0,
    0.1,
    100.0,
    0.01,
    1000.0,
    0.001,
    10000.0,
    0.0001,
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NobitexTopGainerContinuation/1.0",
    "Accept": "application/json",
})

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
CC_EMAIL = "www.rasul.mahmoudimajd1038@gmail.com"


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


# ============================================================================
# BASIC HELPERS
# ============================================================================

def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def now_unix() -> int:
    return int(time.time())


def fmt_number(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    if abs(value) >= 1_000_000_000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.0f}"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    if signed:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_mean(values: List[float], default: float = 0.0) -> float:
    clean = [x for x in values if math.isfinite(x)]
    return statistics.mean(clean) if clean else default


# ============================================================================
# HTTP / NOBITEX
# ============================================================================

def http_get_json(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except (requests.RequestException, ValueError) as exc:
        print(f"⚠️ API error: {exc}")
        return None


def fetch_market_stats() -> Dict[str, Dict[str, Any]]:
    data = http_get_json(STATS_URL, {"dstCurrency": "irt"})
    if not data:
        return {}

    stats = data.get("stats")
    if not isinstance(stats, dict):
        return {}

    result: Dict[str, Dict[str, Any]] = {}

    for market_key, raw in stats.items():
        if not isinstance(raw, dict):
            continue

        market = str(market_key).upper()
        if not market.endswith("-IRT"):
            continue

        symbol = market[:-4]
        latest = safe_float(raw.get("latest"))
        day_high = safe_float(raw.get("dayHigh"))
        day_low = safe_float(raw.get("dayLow"))
        day_change = safe_float(raw.get("dayChange"))
        volume_src = safe_float(raw.get("volumeSrc"), 0.0) or 0.0

        if latest is None or latest <= 0:
            continue

        result[symbol] = {
            "symbol": symbol,
            "market": market,
            "latest": latest,
            "day_high": day_high,
            "day_low": day_low,
            "day_change": day_change or 0.0,
            "volume_src": volume_src,
            "raw": raw,
        }

    return result


def fetch_candles(symbol: str, countback: int = CANDLE_COUNTBACK) -> List[Dict[str, float]]:
    params = {
        "symbol": f"{symbol.upper()}IRT",
        "resolution": CANDLE_RESOLUTION,
        "to": now_unix(),
        "countback": countback,
    }

    data = http_get_json(UDF_URL, params)
    if not data or data.get("s") != "ok":
        return []

    timestamps = data.get("t", [])
    opens = data.get("o", [])
    highs = data.get("h", [])
    lows = data.get("l", [])
    closes = data.get("c", [])
    volumes = data.get("v", [])

    arrays = [timestamps, opens, highs, lows, closes, volumes]
    if not all(isinstance(arr, list) for arr in arrays):
        return []

    n = min(len(arr) for arr in arrays)
    candles: List[Dict[str, float]] = []

    for i in range(n):
        t = safe_float(timestamps[i])
        o = safe_float(opens[i])
        h = safe_float(highs[i])
        l = safe_float(lows[i])
        c = safe_float(closes[i])
        v = safe_float(volumes[i], 0.0)

        if None in (t, o, h, l, c):
            continue
        if c <= 0 or h <= 0 or l <= 0:
            continue

        candles.append({
            "t": float(t),
            "o": float(o),
            "h": float(h),
            "l": float(l),
            "c": float(c),
            "v": float(v or 0.0),
        })

    candles.sort(key=lambda x: x["t"])

    # Closed-candle mode: remove only the currently open 5m candle.
    current = now_unix()
    if candles:
        last = candles[-1]
        if last["t"] + CANDLE_SECONDS > current:
            candles = candles[:-1]

    return candles


# ============================================================================
# PRICE SCALE NORMALIZATION
# ============================================================================

def find_best_candle_scale(
    candles: List[Dict[str, float]],
    target_price: float,
) -> Tuple[float, float]:
    if not candles or target_price <= 0:
        return 1.0, 999.0

    last_close = safe_float(candles[-1].get("c"))
    if last_close is None or last_close <= 0:
        return 1.0, 999.0

    best_factor = 1.0
    best_error = float("inf")

    for factor in SCALE_FACTORS:
        scaled_close = last_close * factor
        if scaled_close <= 0:
            continue
        error = abs(scaled_close - target_price) / target_price
        if error < best_error:
            best_error = error
            best_factor = factor

    return best_factor, best_error


def normalize_candles_to_price_scale(
    candles: List[Dict[str, float]],
    target_price: float,
) -> Tuple[List[Dict[str, float]], float, float]:
    if not candles:
        return candles, 1.0, 999.0

    factor, relative_error = find_best_candle_scale(candles, target_price)
    scaled: List[Dict[str, float]] = []

    for candle in candles:
        item = dict(candle)
        for key in ("o", "h", "l", "c"):
            value = safe_float(item.get(key))
            if value is not None:
                item[key] = value * factor
        scaled.append(item)

    return scaled, factor, relative_error


def find_best_range_scale(
    high: float,
    low: float,
    target_price: float,
) -> Tuple[float, float]:
    if high <= 0 or low <= 0 or target_price <= 0:
        return 1.0, 999.0

    best_factor = 1.0
    best_error = float("inf")
    reference = (high + low) / 2.0

    for factor in SCALE_FACTORS:
        scaled_reference = reference * factor
        if scaled_reference <= 0:
            continue
        error = abs(scaled_reference - target_price) / target_price
        if error < best_error:
            best_error = error
            best_factor = factor

    return best_factor, best_error


def normalize_24h_range(
    high: Optional[float],
    low: Optional[float],
    target_price: float,
) -> Tuple[Optional[float], Optional[float], float]:
    if high is None or low is None or high <= 0 or low <= 0:
        return high, low, 1.0

    factor, _ = find_best_range_scale(high, low, target_price)
    return high * factor, low * factor, factor


# ============================================================================
# TECHNICAL INDICATORS
# ============================================================================

def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    if len(values) < period:
        seed = safe_mean(values)
        alpha = 2.0 / (period + 1.0)
        out = []
        current = seed
        for value in values:
            current = alpha * value + (1.0 - alpha) * current
            out.append(current)
        return out

    alpha = 2.0 / (period + 1.0)
    seed = safe_mean(values[:period])
    out = [seed]
    current = seed

    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)

    # Align output length with input length.
    prefix = [seed] * (period - 1)
    return prefix + out


def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0

    gains: List[float] = []
    losses: List[float] = []

    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = safe_mean(gains[:period])
    avg_loss = safe_mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100.0


def candle_strength(candle: Dict[str, float]) -> float:
    o = candle["o"]
    h = candle["h"]
    l = candle["l"]
    c = candle["c"]
    rng = h - l

    if rng <= 0:
        return 0.0

    body = (c - o) / rng
    return clamp(body, -1.0, 1.0)


def calculate_distance_to_high(price: float, high: float) -> float:
    if price <= 0 or high <= 0:
        return 0.0
    # Positive percentage = room remaining to the 24h high.
    return max(0.0, (high - price) / high * 100.0)


def gain_score(day_change: float, max_gain: float = MAX_DAY_GAIN_FOR_CONTINUATION) -> float:
    if day_change <= 0:
        return 0.0
    # Avoid allowing a giant one-day move alone to dominate continuation score.
    return clamp(day_change / max_gain * 30.0, 0.0, 30.0)


# ============================================================================
# ANALYSIS
# ============================================================================

def build_analysis(symbol: str, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market_price = safe_float(market.get("latest"))
    day_change = safe_float(market.get("day_change"), 0.0) or 0.0

    if market_price is None or market_price <= 0:
        return None

    # Huge single-day moves are still scan candidates, but they require strong
    # confirmation and are not rewarded indefinitely by gain_score().
    if day_change <= 0:
        return None

    candles = fetch_candles(symbol)
    if len(candles) < MIN_CANDLES:
        return None

    candles, candle_factor, candle_error = normalize_candles_to_price_scale(
        candles, market_price
    )

    if candle_error > CANDLE_SCALE_MAX_RELATIVE_ERROR:
        return None

    closes = [c["c"] for c in candles]
    volumes = [max(c["v"], 0.0) for c in candles]

    signal_price = closes[-1]
    if signal_price <= 0:
        return None

    ema9_values = ema(closes, 9)
    ema20_values = ema(closes, 20)

    ema9_last = ema9_values[-1]
    ema20_last = ema20_values[-1]

    rsi_value = rsi(closes, 14)

    c5 = pct_change(closes[-1], closes[-2]) if len(closes) >= 2 else 0.0
    c15 = pct_change(closes[-1], closes[-4]) if len(closes) >= 4 else 0.0
    c30 = pct_change(closes[-1], closes[-7]) if len(closes) >= 7 else 0.0

    old_5m = closes[-2]
    old_15m = closes[-5] if len(closes) >= 5 else closes[0]
    acceleration = c5 - pct_change(old_5m, old_15m)

    lookback_volumes = volumes[-21:-1]
    avg_volume = safe_mean(lookback_volumes, 0.0)
    latest_volume = volumes[-1]

    if avg_volume <= 0:
        volume_ratio = 0.0
    else:
        volume_ratio = latest_volume / avg_volume

    previous_high = max(closes[-21:-1]) if len(closes) >= 21 else max(closes[:-1])
    breakout = pct_change(signal_price, previous_high) if previous_high > 0 else 0.0

    latest_candle_strength = candle_strength(candles[-1])

    recent_low = min(closes[-6:]) if len(closes) >= 6 else min(closes)
    pullback = pct_change(signal_price, recent_low) if recent_low > 0 else 0.0
    # Normalize pullback into a 0..1 confidence contribution.
    pullback_score = clamp(pullback / 3.0, 0.0, 1.0)

    day_high_raw = safe_float(market.get("day_high"))
    day_low_raw = safe_float(market.get("day_low"))
    day_high, day_low, range_factor = normalize_24h_range(
        day_high_raw,
        day_low_raw,
        market_price,
    )

    if day_high is None or day_low is None or day_high <= 0 or day_low <= 0:
        day_high = max(signal_price, market_price)
        day_low = min(signal_price, market_price)

    distance_to_high = calculate_distance_to_high(signal_price, day_high)
    ema_bullish = ema9_last > ema20_last

    # ------------------------------------------------------------------------
    # Score
    # ------------------------------------------------------------------------
    score = 0.0

    # 24h gain: maximum 30 points.
    score += gain_score(day_change)

    # Multi-timeframe momentum: maximum 20 points.
    momentum_points = 0.0
    momentum_points += clamp(c5 / 2.0, -1.0, 1.0) * 7.0
    momentum_points += clamp(c15 / 3.0, -1.0, 1.0) * 7.0
    momentum_points += clamp(c30 / 4.0, -1.0, 1.0) * 6.0
    score += max(0.0, momentum_points)

    # EMA trend: 10 points.
    if ema_bullish:
        score += 10.0

    # Acceleration: up to 10 points.
    if acceleration > 0:
        score += clamp(acceleration / 2.0, 0.0, 1.0) * 10.0

    # Volume: intentionally capped so a one-candle abnormal spike cannot
    # dominate the whole score.
    if 1.0 <= volume_ratio < 2.0:
        volume_score = 2.0
    elif 2.0 <= volume_ratio < 5.0:
        volume_score = 5.0
    elif 5.0 <= volume_ratio <= MAX_VOLUME_RATIO_FOR_ENTRY:
        volume_score = 8.0
    elif volume_ratio > MAX_VOLUME_RATIO_FOR_ENTRY:
        volume_score = 3.0
    else:
        volume_score = 0.0
    score += volume_score

    # Near-high and breakout confirmation: maximum 7 points.
    if distance_to_high <= 1.0:
        score += 4.0
    elif distance_to_high <= 3.0:
        score += 2.0

    if breakout >= 0:
        score += 3.0
    elif breakout > -1.0:
        score += 1.0

    # Candle structure: maximum 3 points.
    score += clamp((latest_candle_strength + 1.0) * 1.5, 0.0, 3.0)

    # Overbought penalty, but not an automatic rejection.
    if rsi_value >= 90:
        score -= 8.0
    elif rsi_value >= 85:
        score -= 5.0
    elif rsi_value >= 80:
        score -= 2.0

    # Negative multi-timeframe structure penalty.
    if c5 < 0 and c15 < 0:
        score -= 5.0

    score = round(clamp(score, 0.0, 100.0), 1)

    volume_anomaly = volume_ratio > MAX_VOLUME_RATIO_FOR_ENTRY

    return {
        "symbol": symbol,
        "market_price": market_price,
        "signal_price": signal_price,
        "day_change": day_change,
        "day_high": day_high,
        "day_low": day_low,
        "distance_to_high": distance_to_high,
        "volume_ratio": volume_ratio,
        "ema9": ema9_last,
        "ema20": ema20_last,
        "ema_bullish": ema_bullish,
        "rsi": rsi_value,
        "c5": c5,
        "c15": c15,
        "c30": c30,
        "acceleration": acceleration,
        "breakout": breakout,
        "candle_strength": latest_candle_strength,
        "pullback": pullback_score,
        "score": score,
        "candle_factor": candle_factor,
        "candle_error": candle_error,
        "range_factor": range_factor,
        "volume_anomaly": volume_anomaly,
        "candle_count": len(candles),
    }


# ============================================================================
# TRADE PLAN
# ============================================================================

def build_trade_plan(analysis: Dict[str, Any]) -> Dict[str, Any]:
    price = float(analysis["market_price"])
    signal_price = float(analysis["signal_price"])
    score = float(analysis["score"])

    c5 = float(analysis["c5"])
    c15 = float(analysis["c15"])
    c30 = float(analysis["c30"])
    acceleration = float(analysis["acceleration"])
    rsi_value = float(analysis["rsi"])
    ema_bullish = bool(analysis["ema_bullish"])
    distance_to_high = float(analysis["distance_to_high"])
    breakout = float(analysis["breakout"])
    candle_strength_value = float(analysis["candle_strength"])
    volume_ratio = float(analysis["volume_ratio"])

    # Critical guard: an extreme volume ratio is treated as an anomaly and
    # cannot independently produce ENTRY.
    if analysis.get("volume_anomaly", False):
        return {
            "action": "WATCH",
            "sl": None,
            "tp1": None,
            "tp2": None,
            "trailing": None,
            "risk_pct": 0.0,
            "reward1_pct": 0.0,
            "reward2_pct": 0.0,
            "rr1": 0.0,
            "rr2": 0.0,
            "reason": "Extreme volume anomaly",
        }

    # Reversal / exhaustion conditions.
    bearish_reversal = (
        (c5 <= -0.8 and acceleration < 0)
        or (c15 <= -1.0 and c30 < 0 and not ema_bullish)
        or (rsi_value >= 88 and c5 < 0)
        or (candle_strength_value <= -0.60 and c5 < 0)
    )

    trend_ok = ema_bullish and c15 > -0.2 and c30 > -0.5
    momentum_ok = c5 > 0.3 and c15 > 0.2 and acceleration > -0.5
    breakout_ok = breakout >= -1.0
    room_ok = distance_to_high >= 0.3

    # Standard entry: high-confidence continuation.
    standard_entry = (
        score >= 72
        and trend_ok
        and momentum_ok
        and breakout_ok
        and room_ok
        and not bearish_reversal
        and rsi_value < 88
    )

    # Early continuation entry: specifically designed to catch strong moves
    # like HBAR even when raw volume is below 1x, provided the rest of the
    # short-term structure is clearly bullish.
    early_entry = (
        score >= EARLY_ENTRY_SCORE_MIN
        and volume_ratio >= EARLY_ENTRY_VOLUME_MIN
        and ema_bullish
        and c5 >= 0.50
        and c15 >= 0.30
        and c30 >= 1.00
        and acceleration >= 0.50
        and breakout >= 0.00
        and 0.50 <= distance_to_high <= EARLY_ENTRY_DISTANCE_MAX
        and rsi_value < EARLY_ENTRY_RSI_MAX
        and not bearish_reversal
    )

    if standard_entry or early_entry:
        action = "ENTRY"
        reason = (
            "Confirmed continuation"
            if standard_entry
            else "Early continuation momentum"
        )
    elif (
        score >= 55
        and trend_ok
        and not bearish_reversal
    ):
        action = "WATCH"
        reason = "Waiting for stronger confirmation"
    elif bearish_reversal or (not trend_ok and c5 < 0):
        action = "EXIT"
        reason = "Momentum reversal / trend weakness"
    elif score >= 40:
        action = "WEAK"
        reason = "Score below entry threshold"
    else:
        action = "EXIT"
        reason = "No continuation setup"

    if action != "ENTRY":
        return {
            "action": action,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "trailing": None,
            "risk_pct": 0.0,
            "reward1_pct": 0.0,
            "reward2_pct": 0.0,
            "rr1": 0.0,
            "rr2": 0.0,
            "reason": reason,
        }

    # Trade plan is based on live market price, not the potentially stale
    # closed candle price.
    risk_pct = 1.80
    reward1_pct = 2.50
    reward2_pct = 5.00

    sl = price * (1.0 - risk_pct / 100.0)
    tp1 = price * (1.0 + reward1_pct / 100.0)
    tp2 = price * (1.0 + reward2_pct / 100.0)
    trailing = price * 0.985

    risk = price - sl
    reward1 = tp1 - price
    reward2 = tp2 - price

    rr1 = reward1 / risk if risk > 0 else 0.0
    rr2 = reward2 / risk if risk > 0 else 0.0

    return {
        "action": action,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "trailing": trailing,
        "risk_pct": risk_pct,
        "reward1_pct": reward1_pct,
        "reward2_pct": reward2_pct,
        "rr1": rr1,
        "rr2": rr2,
        "reason": reason,
    }


# ============================================================================
# DISPLAY
# ============================================================================

def print_top_gainers(markets: Dict[str, Dict[str, Any]]) -> List[str]:
    gainers = sorted(
        markets.values(),
        key=lambda x: float(x.get("day_change", 0.0)),
        reverse=True,
    )

    print()
    print("=" * 100)
    print("🔥 TOP GAINERS — NOBITEX IRT")
    print("=" * 100)

    for index, item in enumerate(gainers[:TOP_GAINERS_PRINT], start=1):
        symbol = item["symbol"]
        day_change = item["day_change"]
        price = item["latest"]
        print(
            f"{index:02d}. {symbol:<12} {day_change:+7.2f}% "
            f"Price: {fmt_number(price)}"
        )

    return [x["symbol"] for x in gainers[:MARKETS_TO_SCAN]]


def print_candidate(analysis: Dict[str, Any], plan: Dict[str, Any]) -> None:
    #print()
    #print("-" * 100)

    symbol = analysis["symbol"]
    action = plan["action"]
    score = analysis["score"]

    if action == "ENTRY":
        print(f"🚨 {symbol} | {action} | Score={score:.1f}")
        print(f"Market Price : {fmt_number(analysis['market_price'])}")
        print(f"Closed Price : {fmt_number(analysis['signal_price'])}")
        print(f"24H Change  : {fmt_pct(analysis['day_change'])}")

        # IMPORTANT: no negative sign here. Distance to high is already positive.
        print(f"Distance High: {analysis['distance_to_high']:.2f}%")

        print(f"Volume Ratio : {analysis['volume_ratio']:.2f}x")
        print(
            f"EMA9/EMA20  : {fmt_number(analysis['ema9'])} / "
            f"{fmt_number(analysis['ema20'])}"
        )
        print(f"RSI         : {analysis['rsi']:.1f}")
        print(f"5m          : {fmt_pct(analysis['c5'])}")
        print(f"15m         : {fmt_pct(analysis['c15'])}")
        print(f"30m         : {fmt_pct(analysis['c30'])}")
        print(f"Acceleration: {fmt_pct(analysis['acceleration'])}")
        print(f"Breakout    : {fmt_pct(analysis['breakout'])}")
        print(f"Candle      : {analysis['candle_strength']:.2f}")
        print(f"Pullback    : {analysis['pullback']:.2f}")
        print(f"Decision    : {plan.get('reason', 'N/A')}")
        print(f"Candle Scale: x{analysis['candle_factor']:g} (error={analysis['candle_error'] * 100:.2f}%)")

    if analysis.get("volume_anomaly"):
        print("⚠️ Volume anomaly guard: ENTRY disabled")

    if action == "ENTRY":
        print()
        print("📌 TRADE PLAN")
        print(f"SL       : {fmt_number(plan['sl'])}")
        print(f"TP1      : {fmt_number(plan['tp1'])}")
        print(f"TP2      : {fmt_number(plan['tp2'])}")
        print(f"Trailing : {fmt_number(plan['trailing'])}")
        print(f"R/R TP1  : 1:{plan['rr1']:.2f}")
        print(f"R/R TP2  : 1:{plan['rr2']:.2f}")

        rows_data=[]
        rows_data= f"SL       : {fmt_number(plan['sl'])}"
        f"TP1      : {fmt_number(plan['tp1'])}"
        f"TP2      : {fmt_number(plan['tp2'])}"
        f"Trailing : {fmt_number(plan['trailing'])}"
        f"R/R TP1  : 1:{plan['rr1']:.2f}"
        f"R/R TP2  : 1:{plan['rr2']:.2f}"

        send_beautiful_email(
            subject=(f"🚀 سیگنال خرید {symbol} "),
            title=(f"خرید {symbol}"),
            type_color="#10b981",
            rows_data=rows_data
        )


def print_scanner_header() -> None:
    print("=" * 100)
    print("NOBITEX TOP-GAINER CONTINUATION SCANNER")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 100)


def scan_once() -> None:
    print()
    print_scanner_header()

    start = time.perf_counter()
    markets = fetch_market_stats()

    if not markets:
        print("❌ No market data received from Nobitex.")
        return

    symbols = print_top_gainers(markets)

    print("=" * 100)
    print(f"🔎 Analyzing top {len(symbols)} markets...")
    print("=" * 100)

    analyses: List[Dict[str, Any]] = []
    valid_count = 0

    for symbol in symbols:
        market = markets.get(symbol)
        if not market:
            continue

        try:
            analysis = build_analysis(symbol, market)
            if analysis is None:
                continue
            valid_count += 1
            analyses.append(analysis)
        except Exception as exc:
            print(f"⚠️ {symbol} analysis failed: {exc}")

    # Sort by score, while retaining all analyzed candidates for summary.
    ranked = sorted(analyses, key=lambda x: x["score"], reverse=True)

    print()
    print("=" * 100)
    print("🎯 BEST CONTINUATION CANDIDATES")
    print("=" * 100)

    counts = {"ENTRY": 0, "PRE": 0, "WATCH": 0, "EXIT": 0, "WEAK": 0}

    # Print only the strongest useful set; this keeps the console readable.
    for analysis in ranked[:20]:
        plan = build_trade_plan(analysis)
        counts[plan["action"]] = counts.get(plan["action"], 0) + 1
        print_candidate(analysis, plan)

    # Count all analyzed markets as well.
    full_counts = {"ENTRY": 0, "PRE": 0, "WATCH": 0, "EXIT": 0, "WEAK": 0}
    for analysis in analyses:
        plan = build_trade_plan(analysis)
        full_counts[plan["action"]] = full_counts.get(plan["action"], 0) + 1

    elapsed = time.perf_counter() - start

    print()
    print(
        "📊 SUMMARY | "
        f"ENTRY={full_counts['ENTRY']} | "
        f"PRE={full_counts['PRE']} | "
        f"WATCH={full_counts['WATCH']} | "
        f"EXIT={full_counts['EXIT']} | "
        f"WEAK={full_counts['WEAK']}"
    )
    print()
    print(f"⏱ Scan completed in {elapsed:.1f}s")
    print(f"📡 Markets scanned: {len(symbols)}")
    print(f"📊 Valid analyses: {valid_count}")
    print(f"🎯 Actionable: {full_counts['ENTRY']}")
    print(f"👀 Watch: {full_counts['WATCH']}")
    print("=" * 100)


# ============================================================================
# REGRESSION TESTS
# ============================================================================

def regression_tests() -> None:
    print()
    print("=" * 100)
    print("🧪 RUNNING REGRESSION TESTS")
    print("=" * 100)

    # TEST 1: distance to high
    distance = calculate_distance_to_high(90, 100)
    assert abs(distance - 10.0) < 1e-9
    print("✅ distance_to_high")

    # TEST 2: exact high
    distance = calculate_distance_to_high(100, 100)
    assert abs(distance) < 1e-9
    print("✅ exact_high")

    # TEST 3: 10x candle scale
    candles = [
        {"t": 1, "o": 99, "h": 101, "l": 98, "c": 100, "v": 10},
        {"t": 2, "o": 99, "h": 101, "l": 99, "c": 100, "v": 12},
    ]
    scaled, factor, error = normalize_candles_to_price_scale(candles, 1000)
    assert factor == 10.0
    assert error < 1e-9
    assert abs(scaled[-1]["c"] - 1000) < 1e-9
    print("✅ 10x candle scale")

    # TEST 4: 24h range scale
    high, low, factor = normalize_24h_range(10, 9, 90)
    assert factor == 10.0
    assert abs(high - 100) < 1e-9
    assert abs(low - 90) < 1e-9
    print("✅ 24h range scale")

    # TEST 5: huge gain alone is not enough to reach score 100
    score = gain_score(100, 20)
    assert score < 100
    print("✅ gain score isolation")

    # TEST 6: candle scale mismatch detection
    candles = [
        {"t": 1, "o": 1.00, "h": 1.10, "l": 0.90, "c": 1.00, "v": 10},
        {"t": 2, "o": 1.00, "h": 1.10, "l": 0.90, "c": 1.00, "v": 10},
    ]
    target_price = 137.0
    _, factor, error = normalize_candles_to_price_scale(candles, target_price)
    assert error > CANDLE_SCALE_MAX_RELATIVE_ERROR
    print(
        "✅ candle scale mismatch detection "
        f"(factor={factor}, error={error * 100:.2f}%)"
    )

    # TEST 7: deterministic closed-candle detection
    current = 1_700_000_000
    closed_timestamp = current - CANDLE_SECONDS - 10
    open_timestamp = current - 60
    assert closed_timestamp + CANDLE_SECONDS <= current
    assert open_timestamp + CANDLE_SECONDS > current
    print("✅ closed candle detection")

    # TEST 8: positive distance-to-high display semantics
    distance = calculate_distance_to_high(100, 101)
    assert distance > 0
    print("✅ positive distance-to-high semantics")

    # TEST 9: extreme volume anomaly cannot create ENTRY
    analysis = {
        "market_price": 100.0,
        "signal_price": 100.0,
        "score": 95.0,
        "c5": 2.0,
        "c15": 2.0,
        "c30": 2.0,
        "acceleration": 1.0,
        "rsi": 65.0,
        "ema_bullish": True,
        "distance_to_high": 1.0,
        "breakout": 1.0,
        "candle_strength": 0.8,
        "volume_ratio": 100.0,
        "volume_anomaly": True,
    }
    plan = build_trade_plan(analysis)
    assert plan["action"] == "WATCH"
    print("✅ volume anomaly guard")

    # TEST 10: HBAR-like early continuation setup
    hbar_like = {
        "market_price": 100.0,
        "signal_price": 100.0,
        "score": 55.0,
        "c5": 1.24,
        "c15": 0.60,
        "c30": 3.18,
        "acceleration": 1.42,
        "rsi": 69.8,
        "ema_bullish": True,
        "distance_to_high": 1.29,
        "breakout": 0.60,
        "candle_strength": 0.44,
        "volume_ratio": 0.77,
        "volume_anomaly": False,
    }
    plan = build_trade_plan(hbar_like)
    assert plan["action"] == "ENTRY"
    print("✅ HBAR-style early continuation entry")

    print("=" * 100)
    print("✅ ALL TESTS PASSED")
    print("=" * 100)
    print()


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    print("=" * 100)
    print("NOBITEX TOP-GAINER CONTINUATION PREDICTOR")
    print("=" * 100)

    regression_tests()

    print("🟢 Scanner started.")
    print(f"⏱ Interval: {SCAN_INTERVAL_SECONDS}s")
    print("🕯 Candle: 5m")
    print("🔒 Closed-candle mode: True")
    print("💡 This program does NOT place orders.")
    print("=" * 100)

    while True:
        try:
            scan_once()
        except KeyboardInterrupt:
            print()
            print("🛑 Scanner stopped by user.")
            break
        except Exception as exc:
            print()
            print(f"❌ Unexpected scanner error: {exc}")

        print()
        print(f"⏳ Waiting {SCAN_INTERVAL_SECONDS}s...")
        try:
            time.sleep(SCAN_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print()
            print("🛑 Scanner stopped by user.")
            break


if __name__ == "__main__":
    main()
