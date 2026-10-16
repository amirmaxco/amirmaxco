import html
import json
import logging
import math
import os
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

import jdatetime
import numpy as np
import pandas as pd
import requests
import smtplib
import ta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# =============================================================================
# Configuration  (all prices in this version are in TOMAN / IRT)
# =============================================================================
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").strip().lower() in {"1", "true", "yes", "y"}
ALLOW_LIVE_TRADING = os.getenv("ALLOW_LIVE_TRADING", "false").strip().lower() in {"1", "true", "yes", "y"}

RISK_PERCENT = 2.0  # Informational only; position size is capped by BUDGET_TOMAN below.
BUDGET_TOMAN = 5_000_000
MAX_DAILY_TRADES = 25
MAX_OPEN_POSITIONS = 25
MAX_HOLD_HOURS = 72
SCAN_INTERVAL_SECONDS = 300
REQUEST_TIMEOUT = 15
UT_SENSITIVITY = 3.0
UT_ATR_PERIOD = 10
RISK_REWARD = 2.0
ATR_MULTIPLIER = 2.0
MAX_SIGNAL_AGE_SECONDS = 90 * 60
STATE_FILE = "live_signals_irt.json"   # فایل جدید؛ سیگنال‌های قدیمی (USDT) با IRT قاطی نشوند
LOG_FILE = "nobitex_bot.log"
MARKET_LOG_FILE = "market_monitor.log"

# --- Secrets: از Environment خوانده می‌شوند، نه از داخل کد ---
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL", "")
CC_EMAIL = os.getenv("CC_EMAIL", "")
NOBITEX_TOKEN = os.getenv("NOBITEX_TOKEN", "")

# همه نمادها روی بازار ریالی (IRT)
SYMBOLS = [
    "BTC/IRT", "ETH/IRT", "SOL/IRT", "AVAX/IRT", "NEAR/IRT",
    "SUI/IRT", "TRX/IRT", "XRP/IRT", "ADA/IRT", "DOGE/IRT",
    "LINK/IRT", "UNI/IRT", "LTC/IRT", "BCH/IRT", "POL/IRT",
    "ALGO/IRT", "XLM/IRT", "HBAR/IRT", "GRT/IRT", "ANKR/IRT",
    "HMSTR/IRT", "DOGS/IRT", "TNSR/IRT", "RENDER/IRT", "APE/IRT",
    "DYDX/IRT", "BASED/IRT", "ONE/IRT", "BICO/IRT", "NOT/IRT",
    "KAITO/IRT", "PUMP/IRT", "BARD/IRT", "PROM/IRT", "LA/IRT",
    "ZAMA/IRT", "HOME/IRT", "XAUT/IRT", "TURBO/IRT", "STRK/IRT",
    "ENA/IRT", "EDU/IRT", "ATOM/IRT",
]

GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# =============================================================================
# Logging
# =============================================================================
logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    if hasattr(stream_handler.stream, "reconfigure"):
        try:
            stream_handler.stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    logger.addHandler(stream_handler)

# =============================================================================
# Common helpers
# =============================================================================
def jalali_now_str() -> str:
    return jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def fmt_toman(value: Any) -> str:
    """قالب‌بندی تومان؛ برای ارزهای ارزان اعشار هم نشان داده می‌شود."""
    v = safe_float(value, 0.0)
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.2f}"


def _auth_headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if NOBITEX_TOKEN:
        headers["Authorization"] = f"Token {NOBITEX_TOKEN}"
    return headers


def _request_json(method: str, url: str, *, params=None, payload=None, headers=None, retries=3) -> Optional[dict]:
    """HTTP helper with retries and response validation."""
    request_headers = headers or {}
    for attempt in range(max(1, retries)):
        try:
            if method.upper() == "POST":
                response = requests.post(url, headers=request_headers, json=payload, timeout=REQUEST_TIMEOUT)
            else:
                response = requests.get(url, headers=request_headers, params=params, timeout=REQUEST_TIMEOUT)

            if response.status_code != 200:
                logger.warning(
                    "HTTP %s از %s (تلاش %s/%s): %s",
                    response.status_code, url, attempt + 1, retries, response.text[:180]
                )
                # 4xx مثل InvalidSymbol معمولاً خطای دائمی درخواست است؛ retry نکن.
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    logger.error("درخواست با خطای غیرقابل تکرار رد شد: HTTP %s | %s", response.status_code, url)
                    return None
                if attempt + 1 < retries:
                    time.sleep(2)
                    continue
                return None

            try:
                data = response.json()
            except ValueError:
                logger.warning("پاسخ غیر JSON از %s: %s", url, response.text[:180])
                if attempt + 1 < retries:
                    time.sleep(2)
                    continue
                return None

            if not isinstance(data, dict):
                logger.warning("ساختار پاسخ نامعتبر از %s", url)
                return None
            return data

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            logger.warning("خطای شبکه %s (تلاش %s/%s): %s", url, attempt + 1, retries, exc)
            if attempt + 1 < retries:
                time.sleep(2)
        except requests.exceptions.RequestException as exc:
            logger.error("خطای درخواست %s: %s", url, exc)
            if attempt + 1 < retries:
                time.sleep(2)
        except Exception:
            logger.exception("خطای پیش‌بینی‌نشده هنگام درخواست %s", url)
            if attempt + 1 < retries:
                time.sleep(2)
    return None


def send_beautiful_email(subject: str, title: str, type_color: str, rows_data) -> bool:
    """Send HTML email. Credentials are read from environment variables."""
    if not (SENDER_EMAIL and SENDER_PASSWORD and RECEIVER_EMAIL):
        logger.warning("ایمیل ارسال نشد؛ SENDER_EMAIL/SENDER_PASSWORD/RECEIVER_EMAIL در Environment تنظیم نشده‌اند.")
        return False

    current_time = jalali_now_str()
    table_rows = []
    for label, value in rows_data:
        table_rows.append(
            "<tr><td class='label'>{}</td><td class='value'>{}</td></tr>".format(
                html.escape(str(label)), html.escape(str(value))
            )
        )

    html_body = f"""<!doctype html>
    <html><head><meta charset="utf-8"><style>
    body {{font-family:Tahoma,Arial,sans-serif;direction:rtl;background:#f4f6f9;color:#333;padding:20px}}
    .container {{max-width:650px;margin:auto;background:#fff;border-radius:8px;overflow:hidden;border-top:6px solid {type_color}}}
    .header {{background:#1e293b;color:white;padding:20px;text-align:center}}
    .content {{padding:22px}} .info-table {{width:100%;border-collapse:collapse}}
    .info-table td {{padding:11px;border-bottom:1px solid #e2e8f0;font-size:14px}}
    .label {{font-weight:bold;color:#4a5568;width:42%}}
    .value {{color:#1a202c;text-align:left;direction:ltr;overflow-wrap:anywhere}}
    .footer {{background:#f8fafc;padding:14px;text-align:center;font-size:11px;color:#718096}}
    </style></head><body><div class="container">
    <div class="header"><h2>{html.escape(str(title))}</h2></div>
    <div class="content"><table class="info-table">{''.join(table_rows)}
    <tr><td class="label">زمان پیام</td><td class="value">{html.escape(current_time)}</td></tr>
    </table></div><div class="footer">پیام خودکار ربات سیگنال‌دهی نوبیتکس</div>
    </div></body></html>"""

    message = MIMEMultipart("alternative")
    message["From"] = SENDER_EMAIL
    message["To"] = RECEIVER_EMAIL
    message["Subject"] = subject
    recipients = [RECEIVER_EMAIL]
    if CC_EMAIL:
        message["Cc"] = CC_EMAIL
        recipients.append(CC_EMAIL)
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, recipients, message.as_string())
        logger.info("ایمیل ارسال شد: %s", subject)
        return True
    except Exception as exc:
        logger.error("خطا در ارسال ایمیل: %s", exc)
        return False

# =============================================================================
# Nobitex market data  (IRT / toman)
# =============================================================================
def get_nobitex_live_price(coin_name: str) -> Optional[float]:
    """Return the direct Nobitex coin/IRT latest price, expressed in toman."""
    coin = coin_name.lower().strip()
    url = "https://apiv2.nobitex.ir/market/stats"
    data = _request_json("GET", url, params={"srcCurrency": coin, "dstCurrency": "rls"}, retries=2)
    if not data or data.get("status") != "ok":
        logger.warning("قیمت زنده %s از نوبیتکس دریافت نشد.", coin.upper())
        return None
    market = data.get("stats", {}).get(f"{coin}-rls", {})
    latest_rial = safe_float(market.get("latest"))
    if not math.isfinite(latest_rial) or latest_rial <= 0:
        logger.warning("قیمت latest برای %s-rls معتبر نیست.", coin)
        return None
    return latest_rial / 10.0


def get_nobitex_wallet_balance() -> Optional[float]:
    if PAPER_TRADING:
        return 10_000_000.0
    if not NOBITEX_TOKEN:
        logger.error("NOBITEX_TOKEN تنظیم نشده است؛ امکان دریافت موجودی وجود ندارد.")
        return None
    url = "https://apiv2.nobitex.ir/v2/wallets"
    data = _request_json("POST", url, headers=_auth_headers(), payload={}, retries=2)
    if not data or data.get("status") != "ok":
        logger.error("دریافت موجودی کیف پول نوبیتکس ناموفق بود.")
        return None
    wallets = data.get("wallets", {})
    if isinstance(wallets, dict):
        if "RLS" in wallets:
            balance = safe_float(wallets["RLS"].get("balance"), 0.0)
            return max(0.0, balance / 10.0)
        if "IRT" in wallets:
            return max(0.0, safe_float(wallets["IRT"].get("balance"), 0.0))
    logger.warning("کیف پول ریالی در پاسخ نوبیتکس یافت نشد.")
    return None


def get_nobitex_data(symbol: str, timeframe: str = "1h", limit: int = 300) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from the Nobitex UDF endpoint for the IRT market.
    واحد قیمت خام ممکن است ریال یا تومان باشد؛ نرمال‌سازی در normalize_ohlc_to_toman انجام می‌شود."""
    resolution_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    resolution = resolution_map.get(timeframe)
    if resolution is None:
        logger.error("تایم‌فریم نامعتبر: %s", timeframe)
        return None
    try:
        src, _dst = symbol.split("/")
    except ValueError:
        logger.error("فرمت نماد نامعتبر است: %s", symbol)
        return None

    period_seconds = 86400 if resolution == "D" else int(resolution) * 60
    now_ts = int(time.time())
    params = {
        "symbol": f"{src.upper()}IRT",
        "resolution": resolution,
        "from": now_ts - (limit * period_seconds),
        "to": now_ts,
    }
    url = "https://apiv2.nobitex.ir/market/udf/history"
    data = _request_json("GET", url, params=params, retries=2)
    if not data or data.get("s") != "ok":
        logger.warning("دریافت OHLC ناموفق: %s | TF=%s | پاسخ=%s", symbol, timeframe, data)
        return None

    required = ("t", "o", "h", "l", "c", "v")
    if not all(key in data for key in required):
        logger.warning("ساختار OHLC ناقص است: %s | TF=%s", symbol, timeframe)
        return None
    try:
        df = pd.DataFrame({
            "timestamp": data["t"], "open": data["o"], "high": data["h"],
            "low": data["l"], "close": data["c"], "volume": data["v"],
        })
        for col in ["timestamp", "open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])
        df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
        if df.empty:
            return None
        logger.info("دریافت کندل: %s | TF=%s | تعداد=%s", symbol, timeframe, len(df))
        return df
    except Exception:
        logger.exception("خطا در ساخت DataFrame برای %s", symbol)
        return None


def normalize_ohlc_to_toman(df: pd.DataFrame, reference_toman: float, symbol: str) -> Optional[pd.DataFrame]:
    """
    واحد کندل‌ها را با قیمت زنده (تومان) مقایسه می‌کند:
      - نسبت ~۱  => خود داده تومان است.
      - نسبت ~۱۰ => داده ریال است و تقسیم بر ۱۰ می‌شود.
      - غیر از این => داده مشکوک است و نماد رد می‌شود.
    """
    if df is None or df.empty or not (reference_toman and reference_toman > 0):
        return None
    last_close = safe_float(df["close"].iloc[-1])
    if not (math.isfinite(last_close) and last_close > 0):
        return None
    ratio = last_close / reference_toman
    out = df.copy()
    if 0.5 <= ratio <= 2.0:
        return out
    if 5.0 <= ratio <= 20.0:
        for col in ["open", "high", "low", "close"]:
            out[col] = out[col] / 10.0
        return out
    logger.warning("[%s] ناسازگاری واحد قیمت: close کندل=%.4f، قیمت زنده=%.4f (نسبت=%.2f)؛ نماد رد شد.",
                   symbol, last_close, reference_toman, ratio)
    return None


def filter_supported_symbols(symbols: List[str]) -> List[str]:
    """نمادهایی که بازار IRT/کندل ندارند را یک‌بار در شروع حذف می‌کند."""
    supported = []
    for sym in symbols:
        df = get_nobitex_data(sym, timeframe="1d", limit=5)
        if df is None or df.empty:
            logger.warning("نماد حذف شد (دادهٔ IRT ندارد): %s", sym)
        else:
            supported.append(sym)
        time.sleep(0.2)
    logger.info("نمادهای فعال: %s از %s", len(supported), len(symbols))
    return supported


def get_last_closed_candle_index(df: pd.DataFrame, timeframe: str) -> Optional[int]:
    """Return index of the last closed candle, assuming UDF timestamps are Unix seconds/bar starts."""
    if df is None or df.empty or "timestamp" not in df.columns:
        return None
    seconds_map = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
    period = seconds_map.get(timeframe)
    if period is None:
        return len(df) - 1
    try:
        last_ts = int(float(df.iloc[-1]["timestamp"]))
        now_ts = int(time.time())
        current_bar_start = now_ts - (now_ts % period)
        if last_ts >= current_bar_start:
            return len(df) - 2 if len(df) >= 2 else None
        return len(df) - 1
    except (TypeError, ValueError, OverflowError):
        return len(df) - 2 if len(df) >= 2 else None

# =============================================================================
# UT Bot indicator (unit-agnostic; here input is toman)
# =============================================================================
def calculate_ut_bot_1h_live(df: pd.DataFrame, sensitivity: float = 3.0, atr_period: int = 10) -> pd.DataFrame:
    required_columns = ["open", "high", "low", "close", "volume"]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"ستون‌های لازم وجود ندارند: {missing}")

    out = df.copy()
    for col in required_columns:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["high", "low", "close"]).copy()
    if out.empty:
        return out

    out["ATR"] = ta.volatility.average_true_range(
        high=out["high"], low=out["low"], close=out["close"],
        window=atr_period, fillna=False
    )
    out["nLoss"] = out["ATR"] * float(sensitivity)

    length = len(out)
    trailing_stop = np.full(length, np.nan, dtype=float)
    position = np.zeros(length, dtype=int)
    signals = np.full(length, "HOLD", dtype=object)
    min_bars = max(atr_period + 20, 50)

    if length < min_bars:
        out["TrailingStop"] = np.nan
        out["UT_Position"] = 0
        out["signal"] = "HOLD"
        out["UT_Bias"] = "NEUTRAL"
        out["Volume_MA"] = out["volume"].rolling(20).mean()
        out["RSI"] = ta.momentum.rsi(out["close"], window=14)
        return out.drop(columns=["nLoss"], errors="ignore")

    close = out["close"].astype(float).to_numpy()
    nloss = out["nLoss"].astype(float).to_numpy()
    valid = np.where(np.isfinite(nloss) & (nloss > 0))[0]
    if len(valid) == 0:
        out["TrailingStop"] = np.nan
        out["UT_Position"] = 0
        out["signal"] = "HOLD"
        out["UT_Bias"] = "NEUTRAL"
        out["Volume_MA"] = out["volume"].rolling(20).mean()
        out["RSI"] = ta.momentum.rsi(out["close"], window=14)
        return out.drop(columns=["nLoss"], errors="ignore")

    first = int(valid[0])
    trailing_stop[first] = close[first] - nloss[first]
    position[first] = 1 if close[first] >= trailing_stop[first] else -1

    for i in range(first + 1, length):
        current_close = close[i]
        previous_close = close[i - 1]
        current_loss = nloss[i]
        previous_stop = trailing_stop[i - 1]

        if not np.isfinite(current_loss) or current_loss <= 0 or not np.isfinite(previous_stop):
            trailing_stop[i] = previous_stop
            position[i] = position[i - 1]
            signals[i] = "HOLD"
            continue

        if current_close > previous_stop and previous_close > previous_stop:
            trailing_stop[i] = max(previous_stop, current_close - current_loss)
        elif current_close < previous_stop and previous_close < previous_stop:
            trailing_stop[i] = min(previous_stop, current_close + current_loss)
        elif current_close > previous_stop:
            trailing_stop[i] = current_close - current_loss
        else:
            trailing_stop[i] = current_close + current_loss

        crossed_up = previous_close <= previous_stop and current_close > trailing_stop[i]
        crossed_down = previous_close >= previous_stop and current_close < trailing_stop[i]

        if crossed_up:
            position[i] = 1
            signals[i] = "BUY"
        elif crossed_down:
            position[i] = -1
            signals[i] = "SELL"
        else:
            if current_close > trailing_stop[i]:
                position[i] = 1
            elif current_close < trailing_stop[i]:
                position[i] = -1
            else:
                position[i] = position[i - 1]
            signals[i] = "HOLD"

    out["TrailingStop"] = trailing_stop
    out["UT_Position"] = position
    out["signal"] = signals
    out["UT_Bias"] = "NEUTRAL"
    out.loc[np.isfinite(out["TrailingStop"]) & (out["close"] > out["TrailingStop"]), "UT_Bias"] = "BULLISH"
    out.loc[np.isfinite(out["TrailingStop"]) & (out["close"] < out["TrailingStop"]), "UT_Bias"] = "BEARISH"
    out["Volume_MA"] = out["volume"].rolling(20).mean()
    out["RSI"] = ta.momentum.rsi(out["close"], window=14)
    return out.drop(columns=["nLoss"], errors="ignore")

# =============================================================================
# Risk / target calculations (all in toman)
# =============================================================================
def estimate_target_time(entry_price: float, target_price: float, atr_value: float, timeframe_hours: float = 1.0) -> Tuple[float, float, float]:
    """Very rough ETA from target distance / ATR; not a prediction or guarantee."""
    entry = safe_float(entry_price)
    target = safe_float(target_price)
    atr = safe_float(atr_value)
    tf_hours = safe_float(timeframe_hours)
    if not all(math.isfinite(x) for x in (entry, target, atr, tf_hours)) or entry <= 0 or target <= 0 or atr <= 0 or tf_hours <= 0:
        return 0.0, 0.0, 0.0
    distance = abs(target - entry)
    candles = max(1.0, distance / atr)
    hours = candles * tf_hours
    return candles, hours, hours / 24.0


def simulate_oco_trade(symbol: str, entry_toman: float, atr_toman: float,
                       risk_reward: float = RISK_REWARD,
                       atr_multiplier: float = ATR_MULTIPLIER) -> Tuple[float, float, float]:
    """LONG levels. Entry and ATR must both be in toman. Returns (entry, target, stop)."""
    entry = safe_float(entry_toman)
    atr = safe_float(atr_toman)
    if not (math.isfinite(entry) and math.isfinite(atr)) or entry <= 0 or atr <= 0:
        raise ValueError(f"[{symbol}] قیمت ورود یا ATR نامعتبر است.")
    if risk_reward <= 0 or atr_multiplier <= 0:
        raise ValueError("risk_reward و atr_multiplier باید مثبت باشند.")

    stop = entry - atr_multiplier * atr
    if stop <= 0:
        raise ValueError(f"[{symbol}] استاپ نامعتبر شد؛ معامله رد شد. Entry={entry:.4f}, ATR={atr:.4f}.")
    target = entry + (entry - stop) * risk_reward
    logger.info(
        "[%s] LONG levels | Entry=%.4f تومان | ATR=%.4f | Stop=%.4f | Target=%.4f | R:R=1:%.2f",
        symbol, entry, atr, stop, target, risk_reward
    )
    return entry, target, stop


def simulate_short_trade(symbol: str, entry_toman: float, atr_toman: float,
                         risk_reward: float = RISK_REWARD,
                         atr_multiplier: float = ATR_MULTIPLIER) -> Tuple[float, float, float]:
    """Paper SHORT levels in toman. Returns (entry, target, stop)."""
    entry = safe_float(entry_toman)
    atr = safe_float(atr_toman)
    if not (math.isfinite(entry) and math.isfinite(atr)) or entry <= 0 or atr <= 0:
        raise ValueError(f"[{symbol}] قیمت ورود یا ATR برای SHORT نامعتبر است.")
    if risk_reward <= 0 or atr_multiplier <= 0:
        raise ValueError("risk_reward و atr_multiplier باید مثبت باشند.")

    stop = entry + atr_multiplier * atr
    target = entry - (stop - entry) * risk_reward
    if target <= 0:
        raise ValueError(f"[{symbol}] تارگت SHORT منفی/صفر است؛ معامله رد شد.")
    return entry, target, stop

# =============================================================================
# Order functions
# =============================================================================
def place_buy_order_and_notify(symbol: str, price_toman: float, budget_toman: float) -> Tuple[bool, Optional[str], float]:
    """Place a spot BUY only when explicitly enabled; return success, order id, filled quantity estimate."""
    coin = symbol.split("/")[0].lower()
    price_toman = safe_float(price_toman)
    budget_toman = safe_float(budget_toman)
    if not math.isfinite(price_toman) or price_toman <= 0 or not math.isfinite(budget_toman) or budget_toman <= 0:
        logger.error("قیمت یا بودجه خرید نامعتبر است: %s", symbol)
        return False, None, 0.0

    if PAPER_TRADING:
        qty = budget_toman / price_toman
        logger.info("[Paper Trading] BUY شبیه‌سازی شد: %s | بودجه=%.0f تومان | مقدار≈%.8f", symbol, budget_toman, qty)
        return True, "paper-order", qty

    if not ALLOW_LIVE_TRADING:
        logger.critical("خرید واقعی مسدود شد؛ برای فعال‌سازی باید ALLOW_LIVE_TRADING=true باشد.")
        return False, None, 0.0
    if not NOBITEX_TOKEN:
        logger.error("خرید واقعی ممکن نیست؛ NOBITEX_TOKEN تنظیم نشده است.")
        return False, None, 0.0

    wallet = get_nobitex_wallet_balance()
    if wallet is None or wallet <= 0:
        logger.error("موجودی ریالی تأیید نشد؛ سفارش خرید ارسال نمی‌شود.")
        return False, None, 0.0
    safe_budget = min(budget_toman, wallet * 0.95)
    if safe_budget <= 0:
        return False, None, 0.0

    buffered_price_toman = price_toman * 1.002
    qty = safe_budget / buffered_price_toman
    amount = f"{qty:.8f}".rstrip("0").rstrip(".")
    price_rial = str(int(round(buffered_price_toman * 10)))
    payload = {
        "type": "buy", "execution": "market", "srcCurrency": coin,
        "dstCurrency": "rls", "amount": amount, "price": price_rial,
    }
    data = _request_json(
        "POST", "https://apiv2.nobitex.ir/market/orders/add",
        headers=_auth_headers(), payload=payload, retries=2
    )
    if data and data.get("status") == "ok":
        order_id = str(data.get("order", {}).get("id", "")) or None
        logger.info("سفارش BUY نوبیتکس ارسال شد: %s | order_id=%s", symbol, order_id)
        return True, order_id, qty
    logger.error("ثبت سفارش BUY ناموفق: %s | پاسخ=%s", symbol, data)
    return False, None, 0.0


def get_nobitex_order_matched_amount(order_id: str) -> float:
    if PAPER_TRADING:
        return 0.0
    if not NOBITEX_TOKEN or not order_id:
        return 0.0
    url = "https://apiv2.nobitex.ir/market/orders/status"
    data = _request_json("POST", url, headers=_auth_headers(), payload={"id": order_id}, retries=2)
    if data and data.get("status") == "ok":
        return max(0.0, safe_float(data.get("order", {}).get("matchedAmount"), 0.0))
    return 0.0


def place_nobitex_oco_sell_order(symbol: str, quantity: float, target_toman: float, stop_toman: float) -> bool:
    """Submit OCO exit prices. Toman values are explicitly converted to rial for the RLS market."""
    coin = symbol.split("/")[0].lower()
    if PAPER_TRADING:
        logger.info("[Paper Trading] OCO فرضی: %s | qty=%.8f | target=%.2f تومان | stop=%.2f تومان", coin, quantity, target_toman, stop_toman)
        return True
    if not (ALLOW_LIVE_TRADING and NOBITEX_TOKEN):
        logger.critical("OCO واقعی ارسال نشد؛ دسترسی معاملات واقعی فعال نیست یا توکن تنظیم نشده است.")
        return False
    if quantity <= 0 or target_toman <= 0 or stop_toman <= 0:
        logger.error("مقادیر OCO نامعتبر هستند: %s", symbol)
        return False

    target_rial = str(int(round(target_toman * 10)))
    stop_rial = str(int(round(stop_toman * 10)))
    stop_limit_rial = str(int(round(stop_toman * 0.99 * 10)))
    amount = f"{quantity:.8f}".rstrip("0").rstrip(".")
    payload = {
        "type": "sell", "execution": "oco", "srcCurrency": coin,
        "dstCurrency": "rls", "amount": amount, "price": target_rial,
        "stopPrice": stop_rial, "stopLimitPrice": stop_limit_rial,
    }
    for attempt in range(3):
        data = _request_json(
            "POST", "https://apiv2.nobitex.ir/market/orders/add",
            headers=_auth_headers(), payload=payload, retries=1
        )
        if data and data.get("status") == "ok":
            logger.info("OCO ثبت شد: %s | id=%s", symbol, data.get("order", {}).get("id"))
            return True
        logger.error("OCO ناموفق (تلاش %s/3): %s | پاسخ=%s", attempt + 1, symbol, data)
        time.sleep(2)

    logger.critical("OCO برای %s ثبت نشد؛ پوزیشن واقعی ممکن است بدون محافظت مانده باشد.", symbol)
    send_beautiful_email(
        f"هشدار فوری: OCO ثبت نشد {symbol}",
        f"پوزیشن {symbol} نیاز به بررسی دستی دارد",
        "#ef4444",
        [("نماد", symbol), ("مقدار", f"{quantity:.8f}"),
         ("تارگت (تومان)", fmt_toman(target_toman)), ("استاپ (تومان)", fmt_toman(stop_toman)),
         ("اقدام", "سفارش OCO ثبت نشد؛ وضعیت سفارش‌ها را در نوبیتکس بررسی کنید.")]
    )
    return False

# =============================================================================
# State file and paper position management
# =============================================================================
def _default_position() -> dict:
    return {
        "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0,
        "stop_price": 0.0, "oco_order_id": None, "updated_at": jalali_now_str(),
        "signal_time": None, "target_day": "", "trade_history": [],
        "last_alert_candle": None, "last_alert_signal": None, "oco_active": False,
    }


def load_last_signals(symbols) -> dict:
    if not os.path.exists(STATE_FILE):
        return {symbol: _default_position() for symbol in symbols}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError("state root is not a dict")
        result = {symbol: _default_position() for symbol in symbols}
        for symbol, value in loaded.items():
            if symbol in result and isinstance(value, dict):
                result[symbol].update(value)
                result[symbol].setdefault("trade_history", [])
                result[symbol].setdefault("last_alert_candle", None)
                result[symbol].setdefault("last_alert_signal", None)
                result[symbol].setdefault("oco_active", False)
        return result
    except Exception as exc:
        logger.error("فایل state خوانده نشد: %s", exc)
        backup_path = f"{STATE_FILE}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        try:
            if os.path.exists(STATE_FILE):
                os.replace(STATE_FILE, backup_path)
                logger.warning("فایل state معیوب به این نام پشتیبان‌گیری شد: %s", backup_path)
        except OSError as backup_exc:
            logger.error("پشتیبان‌گیری از state معیوب ناموفق بود: %s", backup_exc)

        defaults = {symbol: _default_position() for symbol in symbols}
        save_last_signals(defaults)
        logger.warning("وضعیت‌های پیش‌فرض ساخته شدند؛ اگر قبلاً پوزیشن بازی داشته‌اید، قبل از ادامه آن را دستی بررسی کنید.")
        return defaults


def save_last_signals(data: dict) -> bool:
    temp_path = STATE_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, STATE_FILE)
        return True
    except OSError as exc:
        logger.error("ذخیره state ناموفق بود: %s", exc)
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def get_hours_since_entry(entry_time_str: str) -> float:
    try:
        entry_dt = jdatetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
        return max(0.0, (jdatetime.datetime.now() - entry_dt).total_seconds() / 3600.0)
    except Exception as exc:
        logger.warning("محاسبه زمان نگهداری ناموفق بود: %s", exc)
        return 0.0


def close_paper_position(last_signals: dict, symbol: str, position: dict, exit_price_toman: float, reason: str) -> None:
    try:
        entry = safe_float(position.get("entry_price"), 0.0)
        exit_price = safe_float(exit_price_toman, 0.0)
        trade_type = position.get("signal", "UNKNOWN")
        if entry <= 0:
            pnl_pct = 0.0
        elif trade_type == "SHORT":
            pnl_pct = (entry - exit_price) / entry * 100.0
        else:
            pnl_pct = (exit_price - entry) / entry * 100.0
        history = list(position.get("trade_history", []))
        history.append({
            "type": trade_type, "entry_price": entry, "exit_price": exit_price,
            "exit_time": jalali_now_str(), "reason": reason, "pnl_percent": pnl_pct,
        })
        last_signals[symbol] = _default_position()
        last_signals[symbol]["trade_history"] = history[-100:]
        last_signals[symbol]["last_alert_candle"] = position.get("last_alert_candle")
        last_signals[symbol]["last_alert_signal"] = position.get("last_alert_signal")
        save_last_signals(last_signals)
        logger.info("پوزیشن Paper بسته شد: %s | دلیل=%s | PnL=%+.2f%%", symbol, reason, pnl_pct)
    except Exception:
        logger.exception("خطا در بستن پوزیشن Paper: %s", symbol)


def generate_daily_report(file_path: str = STATE_FILE) -> Optional[str]:
    if not os.path.exists(file_path):
        logger.warning("فایل گزارش معاملات وجود ندارد: %s", file_path)
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.exception("خواندن state برای گزارش ناموفق بود.")
        return None

    now = jdatetime.datetime.now()
    trades = []
    for symbol, config in data.items():
        if not isinstance(config, dict):
            continue
        for trade in config.get("trade_history", []):
            try:
                exit_dt = jdatetime.datetime.strptime(trade.get("exit_time", ""), "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if 0 <= (now - exit_dt).total_seconds() <= 86400:
                trades.append((symbol, trade))

    wins = 0
    total_pnl_toman = 0.0
    lines = [f"گزارش عملکرد ربات | {jalali_now_str()}", "=" * 45]
    for symbol, trade in trades:
        pnl_pct = safe_float(trade.get("pnl_percent"), 0.0)
        pnl_toman = BUDGET_TOMAN * pnl_pct / 100.0
        total_pnl_toman += pnl_toman
        wins += int(pnl_toman > 0)
        lines.append(
            f"{symbol} ({trade.get('type', '?')}) | ورود={fmt_toman(trade.get('entry_price', 0))} | "
            f"خروج={fmt_toman(trade.get('exit_price', 0))} تومان | بازده={pnl_pct:+.2f}% | "
            f"برآورد سود/زیان={pnl_toman:+,.0f} تومان | دلیل={trade.get('reason', '')}"
        )
    count = len(trades)
    win_rate = 100.0 * wins / count if count else 0.0
    lines.extend([
        "=" * 45, f"تعداد معاملات بسته‌شده: {count}", f"معاملات سودده: {wins}",
        f"Win Rate: {win_rate:.2f}%", f"سود/زیان تقریبی: {total_pnl_toman:+,.0f} تومان",
        "توجه: محاسبه تقریبی است و کارمزد/لغزش قیمت را شامل نمی‌شود.",
    ])
    report = "\n".join(lines)
    print(report)
    with open(MARKET_LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + report + "\n")
    send_beautiful_email(
        f"گزارش ۲۴ ساعته ربات - {jalali_now_str()}",
        "گزارش معاملات ۲۴ ساعت گذشته",
        "#10b981" if total_pnl_toman >= 0 else "#ef4444",
        [("تعداد معاملات بسته‌شده", str(count)), ("معاملات سودده", str(wins)),
         ("Win Rate", f"{win_rate:.2f}%"), ("سود/زیان تخمینی", f"{total_pnl_toman:+,.0f} تومان")]
    )
    return report

# =============================================================================
# Signal monitor
# =============================================================================
def monitor_market() -> None:
    logger.info("ربات سیگنال‌دهی UT Bot نوبیتکس (IRT) فعال شد. PAPER_TRADING=%s", PAPER_TRADING)
    if not PAPER_TRADING and not ALLOW_LIVE_TRADING:
        logger.critical("حالت واقعی غیرفعال است: برای جلوگیری از خرید ناخواسته، ALLOW_LIVE_TRADING باید جداگانه فعال شود.")
    if not PAPER_TRADING and not NOBITEX_TOKEN:
        logger.critical("NOBITEX_TOKEN تنظیم نشده؛ سفارش واقعی ممکن نیست.")

    active_symbols = filter_supported_symbols(SYMBOLS)
    if not active_symbols:
        logger.critical("هیچ نماد IRT معتبری پیدا نشد؛ برنامه متوقف شد.")
        return

    last_signals = load_last_signals(active_symbols)
    last_report_date = None
    daily_trade_count = 0
    daily_count_date = time.strftime("%Y-%m-%d")

    while True:
        cycle_time = jalali_now_str()
        logger.info("چرخه پایش بازار شروع شد: %s", cycle_time)

        today = time.strftime("%Y-%m-%d")
        if today != daily_count_date:
            daily_count_date = today
            daily_trade_count = 0

        now_local = datetime.now()
        if now_local.hour == 0 and now_local.minute < 5 and last_report_date != now_local.date():
            generate_daily_report(STATE_FILE)
            last_report_date = now_local.date()

        open_positions_count = sum(
            1 for sym in active_symbols
            if isinstance(last_signals.get(sym), dict)
            and last_signals[sym].get("signal") in ("BUY", "SHORT")
        )
        log_lines = []

        for symbol in active_symbols:
            try:
                coin = symbol.split("/")[0].lower()
                position = last_signals.setdefault(symbol, _default_position())
                position.setdefault("last_alert_candle", None)
                position.setdefault("last_alert_signal", None)
                position.setdefault("trade_history", [])
                position.setdefault("oco_active", False)

                # 0) Live price (toman) first; it is also the reference for unit normalisation.
                live_toman = get_nobitex_live_price(coin)
                if live_toman is None or live_toman <= 0:
                    logger.warning("قیمت زنده قابل اتکا نیست؛ نماد رد شد: %s", symbol)
                    continue

                # 1) Daily confirmation (toman).
                df_1d = get_nobitex_data(symbol, timeframe="1d", limit=150)
                if df_1d is None or len(df_1d) < 50:
                    logger.warning("اطلاعات روزانه کافی نیست: %s", symbol)
                    continue
                df_1d = normalize_ohlc_to_toman(df_1d, live_toman, symbol)
                if df_1d is None:
                    continue
                daily_ind = calculate_ut_bot_1h_live(df_1d, sensitivity=UT_SENSITIVITY, atr_period=UT_ATR_PERIOD)
                daily_idx = get_last_closed_candle_index(daily_ind, "1d")
                if daily_idx is None or daily_idx >= len(daily_ind):
                    logger.warning("کندل بسته Daily پیدا نشد: %s", symbol)
                    continue
                daily_row = daily_ind.iloc[daily_idx]
                daily_bias = str(daily_row.get("UT_Bias", "NEUTRAL"))
                daily_green = safe_float(daily_row["close"]) > safe_float(daily_row["open"])
                daily_red = safe_float(daily_row["close"]) < safe_float(daily_row["open"])
                daily_bullish = daily_bias == "BULLISH" and daily_green
                daily_bearish = daily_bias == "BEARISH" and daily_red
                logger.info(
                    "[%s] Daily closed: O=%.4f C=%.4f Green=%s Red=%s Bias=%s",
                    symbol, safe_float(daily_row["open"]), safe_float(daily_row["close"]),
                    daily_green, daily_red, daily_bias
                )
                if not daily_bullish and not daily_bearish:
                    logger.info("[%s] Daily filter did not pass; skip 1H.", symbol)
                    continue

                # 2) One-hour UT Bot (toman).
                df_1h = get_nobitex_data(symbol, timeframe="1h", limit=400)
                if df_1h is None or len(df_1h) < 60:
                    logger.warning("اطلاعات 1H کافی نیست: %s", symbol)
                    continue
                df_1h = normalize_ohlc_to_toman(df_1h, live_toman, symbol)
                if df_1h is None:
                    continue
                ind = calculate_ut_bot_1h_live(df_1h, sensitivity=UT_SENSITIVITY, atr_period=UT_ATR_PERIOD)
                closed_idx = get_last_closed_candle_index(ind, "1h")
                if closed_idx is None or closed_idx >= len(ind):
                    logger.warning("کندل بسته 1H پیدا نشد: %s", symbol)
                    continue
                signal_row = ind.iloc[closed_idx]
                signal = str(signal_row.get("signal", "HOLD"))
                bias_1h = str(signal_row.get("UT_Bias", "NEUTRAL"))
                atr_toman = safe_float(signal_row.get("ATR"))
                sig_close = safe_float(signal_row.get("close"))
                sig_open = safe_float(signal_row.get("open"))
                sig_stop = safe_float(signal_row.get("TrailingStop"))
                signal_green = sig_close > sig_open
                ts_raw = safe_float(signal_row.get("timestamp"))
                signal_ts = int(ts_raw) if math.isfinite(ts_raw) else None
                signal_time_str = str(signal_ts) if signal_ts is not None else "unknown"
                signal_age = time.time() - signal_ts if signal_ts is not None else float("inf")

                entry_state = str(position.get("signal", "HOLD"))
                target_toman_old = safe_float(position.get("target_price"), 0.0)
                stop_toman_old = safe_float(position.get("stop_price"), 0.0)
                held_hours = get_hours_since_entry(position.get("updated_at", "")) if entry_state in ("BUY", "SHORT") else 0.0

                # Paper exits are simulated from the direct live market price.
                if PAPER_TRADING and entry_state == "BUY":
                    if held_hours >= MAX_HOLD_HOURS:
                        close_paper_position(last_signals, symbol, position, live_toman, "TIME_EXIT")
                        continue
                    if stop_toman_old > 0 and live_toman <= stop_toman_old:
                        close_paper_position(last_signals, symbol, position, live_toman, "STOP_LOSS")
                        continue
                    if target_toman_old > 0 and live_toman >= target_toman_old:
                        close_paper_position(last_signals, symbol, position, live_toman, "TAKE_PROFIT")
                        continue

                if PAPER_TRADING and entry_state == "SHORT":
                    if held_hours >= MAX_HOLD_HOURS:
                        close_paper_position(last_signals, symbol, position, live_toman, "TIME_EXIT")
                        continue
                    if stop_toman_old > 0 and live_toman >= stop_toman_old:
                        close_paper_position(last_signals, symbol, position, live_toman, "SHORT_STOP_LOSS")
                        continue
                    if target_toman_old > 0 and live_toman <= target_toman_old:
                        close_paper_position(last_signals, symbol, position, live_toman, "SHORT_TAKE_PROFIT")
                        continue

                target_label = f"هدف={fmt_toman(target_toman_old)}" if target_toman_old > 0 else "هدف=-"
                stop_label = f"استاپ={fmt_toman(stop_toman_old)}" if stop_toman_old > 0 else "استاپ=-"
                line = (
                    f"{symbol:<10} | قیمت={fmt_toman(live_toman)} تومان | 1H={signal:<4} | "
                    f"Bias={bias_1h:<8} | وضعیت={entry_state:<5} | {target_label} | {stop_label}"
                )
                print((GREEN if signal == "BUY" else RED if signal == "SELL" else BLUE) + line + RESET)
                log_lines.append(f"{line} | زمان={cycle_time}")

                can_alert_same_candle = not (
                    position.get("last_alert_signal") == signal
                    and str(position.get("last_alert_candle")) == signal_time_str
                )

                # BUY: daily closed green + bullish bias; 1H closed green + fresh UT BUY above trailing stop.
                if signal == "BUY" and entry_state == "HOLD":
                    reject_reason = None
                    if not daily_bullish:
                        reject_reason = f"Daily filter failed (bias={daily_bias}, green={daily_green})"
                    elif not signal_green:
                        reject_reason = "closed 1H signal candle is not green"
                    elif bias_1h != "BULLISH":
                        reject_reason = f"1H bias is {bias_1h}, not BULLISH"
                    elif not (math.isfinite(sig_close) and math.isfinite(sig_stop) and sig_close > sig_stop):
                        reject_reason = "1H close is not above trailing stop"
                    elif not (0 <= signal_age <= MAX_SIGNAL_AGE_SECONDS):
                        reject_reason = f"signal stale/invalid ({signal_age / 60:.1f} minutes)"
                    elif not can_alert_same_candle:
                        reject_reason = "BUY already alerted for this candle"
                    elif open_positions_count >= MAX_OPEN_POSITIONS:
                        reject_reason = "maximum open positions reached"
                    elif daily_trade_count >= MAX_DAILY_TRADES:
                        reject_reason = "maximum daily trades reached"
                    elif not math.isfinite(atr_toman) or atr_toman <= 0:
                        reject_reason = "ATR invalid"

                    if reject_reason:
                        logger.info("BUY رد شد برای %s: %s", symbol, reject_reason)
                    else:
                        logger.info(
                            "BUY تأیید شد: %s | Daily=%s/green | 1H close=%.4f > UTStop=%.4f | ATR=%.4f | age=%.1fmin",
                            symbol, daily_bias, sig_close, sig_stop, atr_toman, signal_age / 60.0
                        )
                        try:
                            # Entry and ATR are both toman.
                            t_entry, t_target, t_stop = simulate_oco_trade(
                                symbol, live_toman, atr_toman,
                                risk_reward=RISK_REWARD, atr_multiplier=ATR_MULTIPLIER
                            )
                            eta_candles, eta_hours, eta_days = estimate_target_time(
                                t_entry, t_target, atr_toman, 1.0
                            )
                            eta_str = f"{eta_days:.1f} روز ({eta_hours:.1f} ساعت / ~{eta_candles:.1f} کندل) - خیلی تقریبی"

                            ok, order_id, quantity = place_buy_order_and_notify(symbol, t_entry, BUDGET_TOMAN)
                            if not ok:
                                logger.error("سفارش BUY انجام نشد؛ position ذخیره نشد: %s", symbol)
                                continue

                            oco_active = True
                            if not PAPER_TRADING:
                                matched = 0.0
                                for _ in range(6):
                                    matched = get_nobitex_order_matched_amount(order_id or "")
                                    if matched > 0:
                                        break
                                    time.sleep(2)
                                if matched > 0:
                                    quantity = matched
                                    oco_active = place_nobitex_oco_sell_order(symbol, quantity, t_target, t_stop)
                                else:
                                    logger.critical("خرید ارسال شد ولی مقدار پرشده تأیید نشد: %s", symbol)
                                    quantity = 0.0
                                    oco_active = False
                                    send_beautiful_email(
                                        f"بررسی فوری سفارش {symbol}", "وضعیت خرید تأیید نشده", "#ef4444",
                                        [("نماد", symbol), ("Order ID", order_id or "نامشخص"),
                                         ("پیام", "مقدار پرشده تأیید نشد. سفارش‌ها و موجودی را در پنل نوبیتکس بررسی کنید؛ OCO ارسال نشده است.")]
                                    )

                            now_str = jalali_now_str()
                            last_signals[symbol] = {
                                "signal": "BUY", "entry_price": round(t_entry, 2),
                                "target_price": round(t_target, 2), "stop_price": round(t_stop, 2),
                                "oco_order_id": order_id if not PAPER_TRADING else None,
                                "updated_at": now_str, "signal_time": signal_time_str,
                                "target_day": eta_str, "trade_history": position.get("trade_history", [])[-100:],
                                "last_alert_candle": signal_time_str, "last_alert_signal": "BUY",
                                "oco_active": bool(oco_active),
                            }
                            save_last_signals(last_signals)
                            daily_trade_count += 1
                            open_positions_count += 1

                            qty_for_budget = BUDGET_TOMAN / t_entry if t_entry > 0 else 0.0
                            profit_est = (t_target - t_entry) * qty_for_budget
                            loss_est = (t_entry - t_stop) * qty_for_budget
                            trade_mode = "Paper Trading" if PAPER_TRADING else "خرید واقعی نوبیتکس"
                            rows = [
                                ("نماد", symbol), ("حالت", trade_mode),
                                ("قیمت ورود (تومان)", fmt_toman(t_entry)),
                                ("قیمت زنده نوبیتکس (تومان)", fmt_toman(live_toman)),
                                ("ATR (تومان)", f"{atr_toman:,.4f}"),
                                ("حد سود (تومان)", fmt_toman(t_target)),
                                ("حد ضرر (تومان)", fmt_toman(t_stop)),
                                ("Risk / Reward", f"1:{RISK_REWARD:.1f}"),
                                ("بودجه مبنا (تومان)", f"{BUDGET_TOMAN:,.0f}"),
                                ("سود احتمالی تقریبی روی بودجه", f"+{profit_est:,.0f} تومان"),
                                ("زیان احتمالی تقریبی روی بودجه", f"-{loss_est:,.0f} تومان"),
                                ("تخمین زمان تارگت", eta_str),
                                ("کندل سیگنال Unix", signal_time_str),
                                ("تأیید Daily", "کندل بسته سبز و UT Bias صعودی"),
                                ("تأیید 1H", "BUY روی کندل بسته و Close بالای Trailing Stop"),
                                ("وضعیت OCO", "ثبت‌شده/شبیه‌سازی‌شده" if oco_active else "ناموفق یا نیازمند بررسی"),
                            ]
                            send_beautiful_email(
                                f"سیگنال/خرید BUY {symbol} ({trade_mode})",
                                f"BUY تأییدشده {symbol}", "#10b981", rows
                            )
                        except Exception as exc:
                            logger.exception("ساخت/ثبت BUY برای %s ناموفق بود: %s", symbol, exc)

                # SHORT is paper-only in this script. It does not send a live short order.
                if signal == "SELL" and entry_state == "HOLD" and daily_bearish:
                    if not PAPER_TRADING:
                        logger.warning("سیگنال SELL برای %s شناسایی شد، اما SHORT واقعی در این ربات اسپات اجرا نمی‌شود.", symbol)
                    elif (can_alert_same_candle and open_positions_count < MAX_OPEN_POSITIONS
                          and daily_trade_count < MAX_DAILY_TRADES and bias_1h == "BEARISH"
                          and math.isfinite(atr_toman) and atr_toman > 0
                          and 0 <= signal_age <= MAX_SIGNAL_AGE_SECONDS):
                        try:
                            t_entry, t_target, t_stop = simulate_short_trade(
                                symbol, live_toman, atr_toman,
                                risk_reward=RISK_REWARD, atr_multiplier=ATR_MULTIPLIER
                            )
                            eta_candles, eta_hours, eta_days = estimate_target_time(
                                t_entry, t_target, atr_toman, 1.0
                            )
                            eta_str = f"{eta_days:.1f} روز ({eta_hours:.1f} ساعت / ~{eta_candles:.1f} کندل) - خیلی تقریبی"
                            now_str = jalali_now_str()
                            last_signals[symbol] = {
                                "signal": "SHORT", "entry_price": round(t_entry, 2),
                                "target_price": round(t_target, 2), "stop_price": round(t_stop, 2),
                                "oco_order_id": None, "updated_at": now_str,
                                "signal_time": signal_time_str, "target_day": eta_str,
                                "trade_history": position.get("trade_history", [])[-100:],
                                "last_alert_candle": signal_time_str, "last_alert_signal": "SELL",
                                "oco_active": False,
                            }
                            save_last_signals(last_signals)
                            daily_trade_count += 1
                            open_positions_count += 1
                            send_beautiful_email(
                                f"سیگنال SHORT فرضی {symbol}", f"SHORT فرضی {symbol}", "#ef4444",
                                [("نماد", symbol), ("حالت", "فقط شبیه‌سازی"),
                                 ("ورود (تومان)", fmt_toman(t_entry)),
                                 ("تارگت (تومان)", fmt_toman(t_target)),
                                 ("استاپ (تومان)", fmt_toman(t_stop)),
                                 ("ATR (تومان)", f"{atr_toman:,.4f}"), ("R:R", f"1:{RISK_REWARD:.1f}"),
                                 ("تخمین زمان", eta_str),
                                 ("هشدار", "این یک SHORT فرضی است؛ فروش استقراضی واقعی اجرا نشده است.")]
                            )
                        except Exception as exc:
                            logger.exception("محاسبه SHORT برای %s ناموفق بود: %s", symbol, exc)

                time.sleep(0.2)
            except Exception as exc:
                logger.exception("خطا در پردازش نماد %s: %s", symbol, exc)
                continue

        if log_lines:
            try:
                with open(MARKET_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write("\n".join(log_lines) + "\n")
            except OSError:
                logger.exception("نوشتن market log ناموفق بود.")

        logger.info("پایان چرخه؛ استراحت %s ثانیه‌ای.", SCAN_INTERVAL_SECONDS)
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    logger.info("شروع برنامه | زمان شمسی=%s", jalali_now_str())
    logger.info("PAPER_TRADING=%s | ALLOW_LIVE_TRADING=%s", PAPER_TRADING, ALLOW_LIVE_TRADING)
    if not PAPER_TRADING and not ALLOW_LIVE_TRADING:
        logger.warning("حالت واقعی مجاز نیست؛ خریدها در تابع سفارش مسدود خواهند شد.")
    monitor_market()
