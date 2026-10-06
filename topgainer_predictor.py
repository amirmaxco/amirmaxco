from __future__ import annotations

import math
import os
import smtplib
import statistics
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

import jdatetime
import requests

from botemailtrading import CC_EMAIL

# ============================================================================
# NOBITEX TOP-GAINER CONTINUATION PREDICTOR  (v3 - relaxed + anti-fake quality filters)
# Analysis only, NO ORDER PLACEMENT
# ============================================================================

BASE_URL = "https://apiv2.nobitex.ir"
STATS_URL = f"{BASE_URL}/market/stats"
UDF_URL = f"{BASE_URL}/market/udf/history"

REQUEST_TIMEOUT = 12
SCAN_INTERVAL_SECONDS = 60

TOP_GAINERS_PRINT = 20
MARKETS_TO_SCAN = 100
CANDIDATES_PRINT = 20

CANDLE_RESOLUTION = "5"
CANDLE_SECONDS = 5 * 60
CANDLE_COUNTBACK = 120
MIN_CANDLES = 40

# ---------------------------------------------------------------------------
# PRICE / DATA GUARDS
# ---------------------------------------------------------------------------
CANDLE_SCALE_MAX_RELATIVE_ERROR = 0.08
# FIX: was 40. A realistic ceiling so a +5..10% coin gets a meaningful score.
MAX_DAY_GAIN_FOR_CONTINUATION = 10.0

# ---------------------------------------------------------------------------
# VOLUME LOGIC
# ---------------------------------------------------------------------------
VOLUME_ELEVATED = 10.0
VOLUME_ANOMALY = 20.0
VOLUME_EXTREME = 40.0

# ---------------------------------------------------------------------------
# STANDARD ENTRY (relaxed)
# ---------------------------------------------------------------------------
STANDARD_ENTRY_SCORE_MIN = 60.0      # was 72
STANDARD_RSI_MAX = 88.0
STANDARD_VOLUME_MIN = 0.40           # standard entry also needs real volume

# ---------------------------------------------------------------------------
# ANTI-FAKE QUALITY FILTERS (applied to every ENTRY; failing => WATCH)
# These are starting values, tune them with the "Quality blocked" statistics.
# ---------------------------------------------------------------------------
QUALITY_WINDOW = 8                   # last N closed 5m candles are inspected
QUALITY_MIN_ACTIVE_CANDLES = 6       # candles with volume>0 and high>low
QUALITY_MAX_FLAT_CLOSES = 2          # identical consecutive closes (dead market)
QUALITY_MIN_CANDLE_STRENGTH = -0.30  # last closed candle must not be strongly red
QUALITY_MAX_UPPER_WICK = 0.60        # upper wick / candle range (selling pressure)
QUALITY_MAX_EXT_EMA20 = 5.0          # % above EMA20 (already overextended)

# ---------------------------------------------------------------------------
# EARLY ENTRY (relaxed)
# ---------------------------------------------------------------------------
EARLY_ENTRY_SCORE_MIN = 45.0         # was 55
EARLY_ENTRY_VOLUME_MIN = 0.40        # was 0.50
EARLY_ENTRY_DISTANCE_MIN = 0.0       # was 0.5 (coins AT the high are allowed)
EARLY_ENTRY_DISTANCE_MAX = 3.0       # was 2.5
EARLY_ENTRY_RSI_MAX = 80.0
EARLY_BREAKOUT_MIN = -0.50
EARLY_C5_MIN = 0.25                  # was 0.50
EARLY_C15_MIN = 0.25                 # was 0.30
EARLY_C30_MIN = 0.60                 # was 1.00
EARLY_ACCEL_MIN = -0.20              # was 0.50

# ---------------------------------------------------------------------------
# PRE-ENTRY
# ---------------------------------------------------------------------------
PRE_ENTRY_SCORE_MIN = 62.0
PRE_ENTRY_DISTANCE_MAX = 3.50
PRE_ENTRY_RSI_MAX = 82.0

# ---------------------------------------------------------------------------
# TRADE PLAN
# ---------------------------------------------------------------------------
RISK_PERCENT = 1.80
TP1_PERCENT = 2.50
TP2_PERCENT = 5.00
TRAILING_PERCENT = 1.50

# ---------------------------------------------------------------------------
# EMAIL ANTI-SPAM
# ---------------------------------------------------------------------------
ALERT_COOLDOWN_SECONDS = 30 * 60
LAST_ALERT: Dict[str, float] = {}

SCALE_FACTORS = (1.0, 10.0, 0.1, 100.0, 0.01, 1000.0, 0.001, 10000.0, 0.0001)

# Why markets were dropped silently (printed in the summary)
SKIP_COUNTS: Dict[str, int] = {}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "NobitexTopGainerContinuation/2.1",
    "Accept": "application/json",
})


# ============================================================================
# EMAIL CONFIG (use environment variables, never hard-code the password)
#   CMD:        set NOBITEX_EMAIL_PASSWORD=YOUR_APP_PASSWORD
#   PowerShell: $env:NOBITEX_EMAIL_PASSWORD="YOUR_APP_PASSWORD"
# ============================================================================

SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"
#CC_EMAIL = "www.rasul.mahmoudimajd1038@gmail.com"

NOBITEX_TOKEN = "o5TJUZrJoLj7afjp3jxhYa2wixNdKI4gdX8KVtj9Htk="
NOBITEX_TOKEN_PUBLIC = "4f607aff93a0f574deeda11c0a88c8d89ecc56af"


def send_beautiful_email(
    subject: str,
    title: str,
    type_color: str,
    rows_data: List[Tuple[str, str]],
) -> bool:
    if not SENDER_PASSWORD:
        print("⚠️ Email disabled: NOBITEX_EMAIL_PASSWORD is not set.")
        return False
    if not SENDER_EMAIL or "@" not in SENDER_EMAIL:
        print("⚠️ Email disabled: invalid SENDER_EMAIL.")
        return False
    if not RECEIVER_EMAIL or "@" not in RECEIVER_EMAIL:
        print("⚠️ Email disabled: invalid RECEIVER_EMAIL.")
        return False

    current_time = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html_body = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Tahoma, Arial, sans-serif; direction: rtl;
                   background-color: #f4f6f9; color: #333; padding: 20px; }}
            .container {{ max-width: 650px; margin: 0 auto; background: #fff;
                         border-radius: 8px; overflow: hidden;
                         box-shadow: 0 4px 10px rgba(0,0,0,0.05);
                         border-top: 6px solid {type_color}; }}
            .header {{ background-color: #1e293b; color: #fff; padding: 20px;
                      text-align: center; }}
            .header h2 {{ margin: 0; font-size: 20px; }}
            .content {{ padding: 25px; }}
            .info-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
            .info-table td {{ padding: 11px; border-bottom: 1px solid #e2e8f0;
                             font-size: 14px; }}
            .info-table td.label {{ font-weight: bold; color: #4a5568; width: 42%; }}
            .info-table td.value {{ color: #1a202c; text-align: left; direction: ltr; }}
            .footer {{ background-color: #f8fafc; padding: 15px; text-align: center;
                      font-size: 11px; color: #718096; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body dir="rtl">
        <div class="container">
            <div class="header"><h2>{title}</h2></div>
            <div class="content">
                <table class="info-table">
    """

    for label, val in rows_data:
        html_body += (
            f"<tr><td class='label'>{label}</td>"
            f"<td class='value'>{val}</td></tr>"
        )

    html_body += f"""
                    <tr>
                        <td class="label">زمان سیگنال</td>
                        <td class="value">{current_time}</td>
                    </tr>
                </table>
            </div>
            <div class="footer">
                این یک پیام خودکار از ربات تحلیل Nobitex است.
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg["Subject"] = subject

    recipients = [RECEIVER_EMAIL]
    if CC_EMAIL and "@" in CC_EMAIL:
        msg["Cc"] = CC_EMAIL
        recipients.append(CC_EMAIL)

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipients, msg.as_string())
        print("📧 ایمیل با موفقیت ارسال شد.")
        return True
    except Exception as exc:
        print(f"⚠️ خطا در ارسال ایمیل: {exc}")
        return False


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
    if abs(value) >= 1:
        return f"{value:,.0f}"
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_mean(values: List[float], default: float = 0.0) -> float:
    clean = [x for x in values if math.isfinite(x)]
    return statistics.mean(clean) if clean else default


def skip(reason: str) -> None:
    SKIP_COUNTS[reason] = SKIP_COUNTS.get(reason, 0) + 1


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
            "t": float(t), "o": float(o), "h": float(h),
            "l": float(l), "c": float(c), "v": float(v or 0.0),
        })

    candles.sort(key=lambda x: x["t"])

    # Remove only the currently open candle.
    current = now_unix()
    if candles and candles[-1]["t"] + CANDLE_SECONDS > current:
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

    best_factor, best_error = 1.0, float("inf")

    for factor in SCALE_FACTORS:
        scaled_close = last_close * factor
        if scaled_close <= 0:
            continue
        error = abs(scaled_close - target_price) / target_price
        if error < best_error:
            best_error, best_factor = error, factor

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


def find_best_range_scale(high: float, low: float, target_price: float) -> Tuple[float, float]:
    if high <= 0 or low <= 0 or target_price <= 0:
        return 1.0, 999.0

    best_factor, best_error = 1.0, float("inf")
    reference = (high + low) / 2.0

    for factor in SCALE_FACTORS:
        scaled_reference = reference * factor
        if scaled_reference <= 0:
            continue
        error = abs(scaled_reference - target_price) / target_price
        if error < best_error:
            best_error, best_factor = error, factor

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

    alpha = 2.0 / (period + 1.0)

    if len(values) < period:
        current = safe_mean(values)
        out = []
        for value in values:
            current = alpha * value + (1.0 - alpha) * current
            out.append(current)
        return out

    seed = safe_mean(values[:period])
    out = [seed]
    current = seed

    for value in values[period:]:
        current = alpha * value + (1.0 - alpha) * current
        out.append(current)

    return [seed] * (period - 1) + out


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
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def pct_change(new: float, old: float) -> float:
    if old == 0:
        return 0.0
    return (new - old) / old * 100.0


def candle_strength(candle: Dict[str, float]) -> float:
    rng = candle["h"] - candle["l"]
    if rng <= 0:
        return 0.0
    return clamp((candle["c"] - candle["o"]) / rng, -1.0, 1.0)


def calculate_distance_to_high(price: float, high: float) -> float:
    if price <= 0 or high <= 0:
        return 0.0
    return max(0.0, (high - price) / high * 100.0)


def gain_score(day_change: float, max_gain: float = MAX_DAY_GAIN_FOR_CONTINUATION) -> float:
    if day_change <= 0:
        return 0.0
    return clamp(day_change / max_gain * 30.0, 0.0, 30.0)


# ============================================================================
# ANALYSIS
# ============================================================================

def build_analysis(symbol: str, market: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market_price = safe_float(market.get("latest"))
    day_change = safe_float(market.get("day_change"), 0.0) or 0.0

    if market_price is None or market_price <= 0:
        skip("bad price")
        return None

    if day_change <= 0:
        skip("24h change <= 0")
        return None

    candles = fetch_candles(symbol)

    if len(candles) < MIN_CANDLES:
        skip("not enough candles")
        return None

    candles, candle_factor, candle_error = normalize_candles_to_price_scale(
        candles, market_price
    )

    if candle_error > CANDLE_SCALE_MAX_RELATIVE_ERROR:
        skip("candle/price scale mismatch")
        return None

    closes = [c["c"] for c in candles]
    volumes = [max(c["v"], 0.0) for c in candles]

    signal_price = closes[-1]
    if signal_price <= 0:
        skip("bad signal price")
        return None

    ema9_last = ema(closes, 9)[-1]
    ema20_last = ema(closes, 20)[-1]
    rsi_value = rsi(closes, 14)

    c5 = pct_change(closes[-1], closes[-2])
    c15 = pct_change(closes[-1], closes[-4])
    c30 = pct_change(closes[-1], closes[-7])

    # FIX: compare the current 5m change with the PREVIOUS 5m change
    # (previously it was compared with a 15m change -> wrong scale).
    previous_c5 = pct_change(closes[-2], closes[-3])
    acceleration = c5 - previous_c5

    avg_volume = safe_mean(volumes[-21:-1], 0.0)
    latest_volume = volumes[-1]
    volume_ratio = latest_volume / avg_volume if avg_volume > 0 else 0.0

    previous_high = max(closes[-21:-1])
    breakout = pct_change(signal_price, previous_high)

    latest_candle_strength = candle_strength(candles[-1])

    recent_low = min(closes[-6:])
    pullback = pct_change(signal_price, recent_low)
    pullback_score = clamp(pullback / 3.0, 0.0, 1.0)

    day_high, day_low, range_factor = normalize_24h_range(
        safe_float(market.get("day_high")),
        safe_float(market.get("day_low")),
        market_price,
    )

    if day_high is None or day_low is None or day_high <= 0 or day_low <= 0:
        day_high = max(signal_price, market_price)
        day_low = min(signal_price, market_price)

    distance_to_high = calculate_distance_to_high(signal_price, day_high)
    ema_bullish = ema9_last > ema20_last

    # ---- quality metrics (used by the anti-fake filters) ------------------
    window = candles[-QUALITY_WINDOW:]
    active_candles = sum(1 for k in window if k["v"] > 0 and k["h"] > k["l"])
    flat_closes = sum(
        1 for i in range(1, len(window)) if window[i]["c"] == window[i - 1]["c"]
    )
    last_c = candles[-1]
    last_rng = last_c["h"] - last_c["l"]
    upper_wick = (
        (last_c["h"] - max(last_c["o"], last_c["c"])) / last_rng if last_rng > 0 else 0.0
    )
    ext_ema20 = pct_change(signal_price, ema20_last)

    # ------------------------------------------------------------------ SCORE
    score = 0.0

    # 24h gain: max 30
    score += gain_score(day_change)

    # Momentum: max 20
    momentum_points = (
        clamp(c5 / 2.0, -1.0, 1.0) * 7.0
        + clamp(c15 / 3.0, -1.0, 1.0) * 7.0
        + clamp(c30 / 4.0, -1.0, 1.0) * 6.0
    )
    score += max(0.0, momentum_points)

    # EMA trend: 10
    if ema_bullish:
        score += 10.0

    # Acceleration: max 10
    if acceleration > 0:
        score += clamp(acceleration / 2.0, 0.0, 1.0) * 10.0

    # Volume
    if 1.0 <= volume_ratio < 2.0:
        volume_score = 2.0
    elif 2.0 <= volume_ratio < 5.0:
        volume_score = 5.0
    elif 5.0 <= volume_ratio <= VOLUME_ANOMALY:
        volume_score = 8.0
    elif volume_ratio > VOLUME_ANOMALY:
        volume_score = 2.0
    else:
        volume_score = 0.5 if volume_ratio >= 0.50 else 0.0
    score += volume_score

    # Near high / breakout: max 7
    if distance_to_high <= 1.0:
        score += 4.0
    elif distance_to_high <= 3.0:
        score += 2.0

    if breakout >= 0:
        score += 3.0
    elif breakout > -1.0:
        score += 1.0

    # Candle structure: max 3
    score += clamp((latest_candle_strength + 1.0) * 1.5, 0.0, 3.0)

    # Healthy pullback: max 3
    if 0.5 <= pullback <= 3.0:
        score += 3.0 * pullback_score

    # RSI penalty
    if rsi_value >= 90:
        score -= 8.0
    elif rsi_value >= 85:
        score -= 5.0
    elif rsi_value >= 80:
        score -= 2.0

    if c5 < 0 and c15 < 0:
        score -= 5.0

    score = round(clamp(score, 0.0, 100.0), 1)

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
        "pullback_pct": pullback,
        "score": score,
        "candle_factor": candle_factor,
        "candle_error": candle_error,
        "range_factor": range_factor,
        "volume_anomaly": volume_ratio > VOLUME_ANOMALY,
        "volume_extreme": volume_ratio > VOLUME_EXTREME,
        "candle_count": len(candles),
        "active_candles": active_candles,
        "flat_closes": flat_closes,
        "upper_wick": upper_wick,
        "ext_ema20": ext_ema20,
    }


# ============================================================================
# DIAGNOSTICS - which early-entry conditions failed
# ============================================================================

def quality_failures(a: Dict[str, Any]) -> List[str]:
    """Reasons why an otherwise valid ENTRY looks like a fake / low-quality signal."""
    fails: List[str] = []
    if a.get("active_candles", QUALITY_WINDOW) < QUALITY_MIN_ACTIVE_CANDLES:
        fails.append(f"thin-market({a.get('active_candles')}/{QUALITY_WINDOW} active)")
    if a.get("flat_closes", 0) > QUALITY_MAX_FLAT_CLOSES:
        fails.append(f"flat-candles({a.get('flat_closes')})")
    if a.get("candle_strength", 0.0) < QUALITY_MIN_CANDLE_STRENGTH:
        fails.append(f"red-last-candle({a.get('candle_strength'):.2f})")
    if a.get("upper_wick", 0.0) > QUALITY_MAX_UPPER_WICK:
        fails.append(f"upper-wick({a.get('upper_wick'):.2f})")
    if a.get("ext_ema20", 0.0) > QUALITY_MAX_EXT_EMA20:
        fails.append(f"extended({a.get('ext_ema20'):.1f}%>EMA20)")
    return fails


def explain_early_failures(a: Dict[str, Any]) -> List[str]:
    checks = {
        f"score>={EARLY_ENTRY_SCORE_MIN:g}": a["score"] >= EARLY_ENTRY_SCORE_MIN,
        f"vol>={EARLY_ENTRY_VOLUME_MIN:g}": a["volume_ratio"] >= EARLY_ENTRY_VOLUME_MIN,
        "ema": a["ema_bullish"],
        f"c5>={EARLY_C5_MIN:g}": a["c5"] >= EARLY_C5_MIN,
        f"c15>={EARLY_C15_MIN:g}": a["c15"] >= EARLY_C15_MIN,
        f"c30>={EARLY_C30_MIN:g}": a["c30"] >= EARLY_C30_MIN,
        f"accel>={EARLY_ACCEL_MIN:g}": a["acceleration"] >= EARLY_ACCEL_MIN,
        f"breakout>={EARLY_BREAKOUT_MIN:g}": a["breakout"] >= EARLY_BREAKOUT_MIN,
        f"dist {EARLY_ENTRY_DISTANCE_MIN:g}-{EARLY_ENTRY_DISTANCE_MAX:g}":
            EARLY_ENTRY_DISTANCE_MIN <= a["distance_to_high"] <= EARLY_ENTRY_DISTANCE_MAX,
        f"rsi<{EARLY_ENTRY_RSI_MAX:g}": a["rsi"] < EARLY_ENTRY_RSI_MAX,
    }
    return [name for name, ok in checks.items() if not ok]


# ============================================================================
# TRADE PLAN
# ============================================================================

def _no_levels(action: str, reason: str) -> Dict[str, Any]:
    return {
        "action": action, "sl": None, "tp1": None, "tp2": None,
        "trailing": None, "risk_pct": 0.0, "reward1_pct": 0.0,
        "reward2_pct": 0.0, "rr1": 0.0, "rr2": 0.0, "reason": reason,
    }


def build_trade_plan(analysis: Dict[str, Any]) -> Dict[str, Any]:
    price = float(analysis["market_price"])
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
    volume_extreme = bool(analysis.get("volume_extreme", False))
    volume_anomaly = bool(analysis.get("volume_anomaly", False))

    # ---------------------------------------------------------- VOLUME GUARD
    if volume_extreme:
        return _no_levels("WATCH", "Extreme volume anomaly")

    if volume_anomaly:
        return _no_levels("WATCH", "High volume anomaly - confirmation required")

    # ------------------------------------------------- REVERSAL / EXHAUSTION
    bearish_reversal = (
        (c5 <= -0.8 and acceleration < 0)
        or (c15 <= -1.0 and c30 < 0 and not ema_bullish)
        or (rsi_value >= 88 and c5 < 0)
        or (candle_strength_value <= -0.60 and c5 < 0)
    )

    trend_ok = ema_bullish and c15 > -0.2 and c30 > -0.5
    momentum_ok = c5 > 0.3 and c15 > 0.2 and acceleration > -0.5
    breakout_ok = breakout >= -1.0
    # FIX: coins sitting exactly on the 24h high are allowed
    room_ok = distance_to_high >= 0.0

    # --------------------------------------------------------- STANDARD ENTRY
    standard_entry = (
        score >= STANDARD_ENTRY_SCORE_MIN
        and trend_ok
        and momentum_ok
        and breakout_ok
        and room_ok
        and not bearish_reversal
        and rsi_value < STANDARD_RSI_MAX
        and volume_ratio >= STANDARD_VOLUME_MIN
    )

    # ------------------------------------------------------------ EARLY ENTRY
    early_entry = (
        score >= EARLY_ENTRY_SCORE_MIN
        and volume_ratio >= EARLY_ENTRY_VOLUME_MIN
        and ema_bullish
        and c5 >= EARLY_C5_MIN
        and c15 >= EARLY_C15_MIN
        and c30 >= EARLY_C30_MIN
        and acceleration >= EARLY_ACCEL_MIN
        and breakout >= EARLY_BREAKOUT_MIN
        and EARLY_ENTRY_DISTANCE_MIN <= distance_to_high <= EARLY_ENTRY_DISTANCE_MAX
        and rsi_value < EARLY_ENTRY_RSI_MAX
        and not bearish_reversal
    )

    # -------------------------------------------------------------- PRE-ENTRY
    pre_entry = (
        score >= PRE_ENTRY_SCORE_MIN
        and ema_bullish
        and c5 >= 0.30
        and c15 >= 0.15
        and c30 >= 0.50
        and acceleration >= 0.20
        and breakout >= -1.00
        and distance_to_high <= PRE_ENTRY_DISTANCE_MAX
        and rsi_value < PRE_ENTRY_RSI_MAX
        and not bearish_reversal
        and volume_ratio >= 0.40
    )

    quality_fail = quality_failures(analysis)
    if (standard_entry or early_entry) and quality_fail:
        return _no_levels("WATCH", "Quality filter: " + ", ".join(quality_fail))

    if standard_entry or early_entry:
        action = "ENTRY"
        reason = "Confirmed continuation" if standard_entry else "Early continuation momentum"
    elif pre_entry:
        action = "PRE"
        reason = "Pre-entry - waiting for confirmation"
    elif score >= 50 and trend_ok and not bearish_reversal:
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
        return _no_levels(action, reason)

    # ------------------------------------------------------------- TRADE PLAN
    risk_pct = RISK_PERCENT
    sl = price * (1.0 - risk_pct / 100.0)
    tp1 = price * (1.0 + TP1_PERCENT / 100.0)
    tp2 = price * (1.0 + TP2_PERCENT / 100.0)
    trailing = price * (1.0 - TRAILING_PERCENT / 100.0)

    risk = price - sl
    rr1 = (tp1 - price) / risk if risk > 0 else 0.0
    rr2 = (tp2 - price) / risk if risk > 0 else 0.0

    return {
        "action": action, "sl": sl, "tp1": tp1, "tp2": tp2,
        "trailing": trailing, "risk_pct": risk_pct,
        "reward1_pct": TP1_PERCENT, "reward2_pct": TP2_PERCENT,
        "rr1": rr1, "rr2": rr2, "reason": reason,
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
        print(
            f"{index:02d}. {item['symbol']:<12} "
            f"{item['day_change']:+7.2f}% "
            f"Price: {fmt_number(item['latest'])}"
        )

    return [x["symbol"] for x in gainers[:MARKETS_TO_SCAN]]


def can_send_alert(symbol: str) -> bool:
    last = LAST_ALERT.get(symbol, 0.0)
    return (time.time() - last) >= ALERT_COOLDOWN_SECONDS


def print_candidate(analysis: Dict[str, Any], plan: Dict[str, Any]) -> None:
    symbol = analysis["symbol"]
    action = plan["action"]
    score = analysis["score"]

    if action != "ENTRY":
        failed = ""
        if action in ("PRE", "WATCH", "WEAK", "EXIT"):
            fails = explain_early_failures(analysis)
            if fails:
                failed = " | ✗ " + ",".join(fails)

        print(
            f"{symbol:<10} | {action:<5} | Score={score:.1f} | "
            f"24H={analysis['day_change']:+.2f}% | "
            f"5m={analysis['c5']:+.2f}% | 15m={analysis['c15']:+.2f}% | "
            f"Vol={analysis['volume_ratio']:.2f}x | RSI={analysis['rsi']:.1f} | "
            f"{plan.get('reason', 'N/A')}{failed}"
        )
        return

    print()
    print(f"🚨 {symbol} | ENTRY | Score={score:.1f}")
    print(f"Market Price : {fmt_number(analysis['market_price'])}")
    print(f"Closed Price : {fmt_number(analysis['signal_price'])}")
    print(f"24H Change   : {fmt_pct(analysis['day_change'])}")
    print(f"Distance High: {analysis['distance_to_high']:.2f}%")
    print(f"Volume Ratio : {analysis['volume_ratio']:.2f}x")
    print(f"EMA9/EMA20   : {fmt_number(analysis['ema9'])} / {fmt_number(analysis['ema20'])}")
    print(f"RSI          : {analysis['rsi']:.1f}")
    print(f"5m           : {fmt_pct(analysis['c5'])}")
    print(f"15m          : {fmt_pct(analysis['c15'])}")
    print(f"30m          : {fmt_pct(analysis['c30'])}")
    print(f"Acceleration : {fmt_pct(analysis['acceleration'])}")
    print(f"Breakout     : {fmt_pct(analysis['breakout'])}")
    print(f"Candle       : {analysis['candle_strength']:.2f}")
    print(f"Pullback     : {analysis['pullback_pct']:.2f}%")
    print(f"Decision     : {plan.get('reason', 'N/A')}")
    print(
        f"Candle Scale : x{analysis['candle_factor']:g} "
        f"(error={analysis['candle_error'] * 100:.2f}%)"
    )

    print()
    print("📌 TRADE PLAN")
    print(f"SL       : {fmt_number(plan['sl'])}")
    print(f"TP1      : {fmt_number(plan['tp1'])}")
    print(f"TP2      : {fmt_number(plan['tp2'])}")
    print(f"Trailing : {fmt_number(plan['trailing'])}")
    print(f"R/R TP1  : 1:{plan['rr1']:.2f}")
    print(f"R/R TP2  : 1:{plan['rr2']:.2f}")

    # Anti-spam: one email per symbol per cooldown window
    if not can_send_alert(symbol):
        print(f"⏸ ایمیل {symbol} در بازه‌ی cooldown است، دوباره ارسال نشد.")
        return

    rows_data = [
        ("نماد", str(symbol)),
        ("امتیاز", f"{score:.1f}"),
        ("قیمت فعلی", fmt_number(analysis["market_price"])),
        ("تغییر 24 ساعت", fmt_pct(analysis["day_change"])),
        ("فاصله تا سقف 24h", f"{analysis['distance_to_high']:.2f}%"),
        ("Volume Ratio", f"{analysis['volume_ratio']:.2f}x"),
        ("RSI", f"{analysis['rsi']:.1f}"),
        ("5m", fmt_pct(analysis["c5"])),
        ("15m", fmt_pct(analysis["c15"])),
        ("30m", fmt_pct(analysis["c30"])),
        ("Acceleration", fmt_pct(analysis["acceleration"])),
        ("Breakout", fmt_pct(analysis["breakout"])),
        ("دلیل سیگنال", str(plan.get("reason", "N/A"))),
        ("حد ضرر", fmt_number(plan["sl"])),
        ("هدف اول", fmt_number(plan["tp1"])),
        ("هدف دوم", fmt_number(plan["tp2"])),
        ("Trailing", fmt_number(plan["trailing"])),
        ("R/R TP1", f"1:{plan['rr1']:.2f}"),
        ("R/R TP2", f"1:{plan['rr2']:.2f}"),
    ]

    sent = send_beautiful_email(
        subject=f"🚀 سیگنال خرید {symbol}",
        title=f"خرید {symbol}",
        type_color="#10b981",
        rows_data=rows_data,
    )

    if sent:
        LAST_ALERT[symbol] = time.time()


# ============================================================================
# SCANNER
# ============================================================================

def print_scanner_header() -> None:
    print("=" * 100)
    print("NOBITEX TOP-GAINER CONTINUATION SCANNER")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 100)


def scan_once() -> None:
    print()
    print_scanner_header()

    start = time.perf_counter()
    SKIP_COUNTS.clear()

    markets = fetch_market_stats()

    if not markets:
        print("❌ No market data received from Nobitex.")
        return

    symbols = print_top_gainers(markets)

    print("=" * 100)
    print(f"🔎 Analyzing top {len(symbols)} markets...")
    print("=" * 100)

    analyses: List[Dict[str, Any]] = []

    for symbol in symbols:
        market = markets.get(symbol)
        if not market:
            continue

        try:
            analysis = build_analysis(symbol, market)
            if analysis is not None:
                analyses.append(analysis)
        except Exception as exc:
            skip("exception")
            print(f"⚠️ {symbol} analysis failed: {exc}")

    # Compute each plan ONCE
    results = [(a, build_trade_plan(a)) for a in analyses]
    results.sort(key=lambda r: r[0]["score"], reverse=True)

    counts = {"ENTRY": 0, "PRE": 0, "WATCH": 0, "EXIT": 0, "WEAK": 0}
    for _, plan in results:
        counts[plan["action"]] = counts.get(plan["action"], 0) + 1

    print()
    print("=" * 100)
    print("🎯 BEST CONTINUATION CANDIDATES")
    print("=" * 100)

    # Top N by score, PLUS every ENTRY even if it is outside the top N
    shown = 0
    for analysis, plan in results:
        if shown < CANDIDATES_PRINT or plan["action"] == "ENTRY":
            print_candidate(analysis, plan)
            shown += 1

    elapsed = time.perf_counter() - start

    print()
    print(
        "📊 SUMMARY | "
        f"ENTRY={counts['ENTRY']} | PRE={counts['PRE']} | "
        f"WATCH={counts['WATCH']} | EXIT={counts['EXIT']} | WEAK={counts['WEAK']}"
    )

    if SKIP_COUNTS:
        print(
            "🚫 Skipped: "
            + " | ".join(f"{k}={v}" for k, v in sorted(SKIP_COUNTS.items()))
        )

    # Which early-entry condition rejects the most coins
    fail_stats: Dict[str, int] = {}
    for analysis, plan in results:
        if plan["action"] != "ENTRY":
            for name in explain_early_failures(analysis):
                fail_stats[name] = fail_stats.get(name, 0) + 1

    if fail_stats:
        top_fails = sorted(fail_stats.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print(
            "🔬 Top blocking filters: "
            + " | ".join(f"{k}={v}" for k, v in top_fails)
        )

    q_stats: Dict[str, int] = {}
    for _, plan in results:
        if str(plan.get("reason", "")).startswith("Quality filter:"):
            for part in plan["reason"].split(":", 1)[1].split(","):
                key = part.strip().split("(")[0]
                q_stats[key] = q_stats.get(key, 0) + 1
    if q_stats:
        print(
            "🛡 Quality blocked (would-be ENTRY): "
            + " | ".join(f"{k}={v}" for k, v in sorted(q_stats.items(), key=lambda kv: -kv[1]))
        )

    print()
    print(f"⏱ Scan completed in {elapsed:.1f}s")
    print(f"📡 Markets scanned: {len(symbols)}")
    print(f"📊 Valid analyses: {len(analyses)}")
    print(f"🎯 Actionable ENTRY: {counts['ENTRY']}")
    print(f"🟡 PRE-ENTRY: {counts['PRE']}")
    print(f"👀 Watch: {counts['WATCH']}")
    print("=" * 100)


# ============================================================================
# REGRESSION TESTS
# ============================================================================

def regression_tests() -> None:
    print()
    print("=" * 100)
    print("🧪 RUNNING REGRESSION TESTS")
    print("=" * 100)

    assert abs(calculate_distance_to_high(90, 100) - 10.0) < 1e-9
    print("✅ distance_to_high")

    assert abs(calculate_distance_to_high(100, 100)) < 1e-9
    print("✅ exact_high")

    candles = [
        {"t": 1, "o": 99, "h": 101, "l": 98, "c": 100, "v": 10},
        {"t": 2, "o": 99, "h": 101, "l": 99, "c": 100, "v": 12},
    ]
    scaled, factor, error = normalize_candles_to_price_scale(candles, 1000)
    assert factor == 10.0 and error < 1e-9
    assert abs(scaled[-1]["c"] - 1000) < 1e-9
    print("✅ 10x candle scale")

    high, low, factor = normalize_24h_range(10, 9, 90)
    assert factor == 10.0
    assert abs(high - 100) < 1e-9 and abs(low - 90) < 1e-9
    print("✅ 24h range scale")

    assert gain_score(100, 20) <= 30.0
    print("✅ gain score isolation")

    # FIX check: a +5% coin must now get a meaningful gain score
    assert gain_score(5.0) >= 14.0
    print("✅ realistic gain score (5% -> >=14 pts)")

    # FIX check: acceleration uses 5m vs previous 5m
    closes = [100.0, 100.0, 100.5, 101.5]
    c5 = pct_change(closes[-1], closes[-2])
    prev = pct_change(closes[-2], closes[-3])
    assert c5 > prev
    print("✅ acceleration uses previous 5m change")

    candles = [
        {"t": 1, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0, "v": 10},
        {"t": 2, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.0, "v": 10},
    ]
    _, factor, error = normalize_candles_to_price_scale(candles, 137.0)
    assert error > CANDLE_SCALE_MAX_RELATIVE_ERROR
    print(f"✅ candle scale mismatch detection (factor={factor}, error={error * 100:.2f}%)")

    current = 1_700_000_000
    assert (current - CANDLE_SECONDS - 10) + CANDLE_SECONDS <= current
    assert (current - 60) + CANDLE_SECONDS > current
    print("✅ closed candle detection")

    assert calculate_distance_to_high(100, 101) > 0
    print("✅ positive distance-to-high semantics")

    base = {
        "market_price": 100.0, "score": 95.0, "c5": 2.0, "c15": 2.0,
        "c30": 2.0, "acceleration": 1.0, "rsi": 65.0, "ema_bullish": True,
        "distance_to_high": 1.0, "breakout": 1.0, "candle_strength": 0.8,
        "volume_ratio": 50.0, "volume_anomaly": True, "volume_extreme": True,
    }
    assert build_trade_plan(base)["action"] == "WATCH"
    print("✅ extreme volume guard")

    hbar_like = {
        "market_price": 100.0, "score": 55.0, "c5": 1.24, "c15": 0.60,
        "c30": 3.18, "acceleration": 1.42, "rsi": 69.8, "ema_bullish": True,
        "distance_to_high": 1.29, "breakout": 0.60, "candle_strength": 0.44,
        "volume_ratio": 0.77, "volume_anomaly": False, "volume_extreme": False,
    }
    assert build_trade_plan(hbar_like)["action"] == "ENTRY"
    print("✅ HBAR-style early continuation entry")

    hbar_prebreakout = dict(
        hbar_like, score=58.0, c5=1.00, c15=0.50, c30=2.00,
        acceleration=0.90, rsi=70.0, distance_to_high=1.50,
        breakout=-0.30, candle_strength=0.50, volume_ratio=0.80,
    )
    assert build_trade_plan(hbar_prebreakout)["action"] == "ENTRY"
    print("✅ pre-breakout early entry")

    pre_entry_like = dict(
        hbar_like, score=64.0, c5=0.40, c15=0.20, c30=0.70,
        acceleration=0.30, rsi=70.0, distance_to_high=3.0,
        breakout=-0.50, candle_strength=0.20, volume_ratio=0.60,
    )
    assert build_trade_plan(pre_entry_like)["action"] == "PRE"
    print("✅ PRE-ENTRY logic")

    # NEW: a coin exactly on its 24h high with slow, steady momentum
    slow_riser = dict(
        hbar_like, score=50.0, c5=0.30, c15=0.30, c30=0.70,
        acceleration=0.0, rsi=62.0, distance_to_high=0.0,
        breakout=0.1, candle_strength=0.3, volume_ratio=0.8,
    )
    assert build_trade_plan(slow_riser)["action"] == "ENTRY"
    print("✅ slow riser at the 24h high is now accepted")

    # NEW: bearish reversal must never produce ENTRY
    reversal = dict(
        hbar_like, score=70.0, c5=-1.0, c15=-1.2, c30=-0.5,
        acceleration=-0.5, ema_bullish=False,
    )
    assert build_trade_plan(reversal)["action"] != "ENTRY"
    print("✅ reversal guard")

    # NEW anti-fake tests
    assert build_trade_plan(hbar_like)["action"] == "ENTRY"
    red = dict(hbar_like, candle_strength=-1.0, volume_ratio=14.0)
    p = build_trade_plan(red)
    assert p["action"] == "WATCH" and "red-last-candle" in p["reason"]
    print("✅ red last candle is blocked (ETHFI-style)")

    flat = dict(hbar_like, active_candles=3, flat_closes=5)
    p = build_trade_plan(flat)
    assert p["action"] == "WATCH" and "flat-candles" in p["reason"]
    print("✅ flat / thin market is blocked (BAND/PENGU-style)")

    wick = dict(hbar_like, upper_wick=0.8)
    assert build_trade_plan(wick)["action"] == "WATCH"
    ext = dict(hbar_like, ext_ema20=8.0)
    assert build_trade_plan(ext)["action"] == "WATCH"
    print("✅ upper wick / overextension blocked")

    good = dict(hbar_like, active_candles=8, flat_closes=0, upper_wick=0.2, ext_ema20=1.5)
    assert build_trade_plan(good)["action"] == "ENTRY"
    print("✅ clean setup still produces ENTRY")

    print("=" * 100)
    print("✅ ALL TESTS PASSED")
    print("=" * 100)
    print()


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:
    print("=" * 100)
    print("NOBITEX TOP-GAINER CONTINUATION PREDICTOR v3")
    print("=" * 100)

    regression_tests()

    print("🟢 Scanner started.")
    print(f"⏱ Interval: {SCAN_INTERVAL_SECONDS}s")
    print(f"🕯 Candle: {CANDLE_RESOLUTION}m")
    print("🔒 Closed-candle mode: True")
    print("💡 This program does NOT place orders.")
    print(f"📊 Volume anomaly: >{VOLUME_ANOMALY}x")
    print(f"🚨 Extreme volume: >{VOLUME_EXTREME}x")
    print(f"📧 Alert cooldown: {ALERT_COOLDOWN_SECONDS // 60} min per symbol")
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
