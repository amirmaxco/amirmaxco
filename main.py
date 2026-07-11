from logging.handlers import TimedRotatingFileHandler
from daily_report import generate_daily_report
from indicators import calculate_ut_bot_2h_live
from notifier import send_beautiful_email
from target import simulate_oco_trade
from signals import load_last_signals,save_last_signals
from utils import _send_request_with_retry
import daily_report
from target import simulate_oco_trade,simulate_sell_trade,place_nobitex_oco_sell_order,get_nobitex_order_matched_amount,place_buy_order_and_notify,estimate_target_time
from exchange import get_nobitex_live_price,get_iran_dollar_price,get_nobitex_wallet_balance
import os
import jdatetime
import datetime, time
import ta
import requests
import logging
import ccxt
from dotenv import load_dotenv
import pandas as pd
from nobitex_api import NobitexClient

load_dotenv()

now_shamsi=jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[0m"
RESET = "\033[0m"


RISK_PERCENT = 2.0
MAX_DAILY_TRADES = 6
MAX_OPEN_POSITIONS = 6

timeframe = '1h'
BUDGET_TOMAN = 300000

daily_trade_count = 0
last_reset_date = time.strftime("%Y-%m-%d")
max_peak_balance = 0.0

base_url = "https://apiv2.nobitex.ir"

PAPER_TRADING = True
NOBITEX_TOKEN_PUBLIC = os.getenv("NOBITEX_TOKEN_PUBLIC")
NOBITEX_TOKEN = os.getenv("NOBITEX_TOKEN")

logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)

exchange = NobitexClient(
    NOBITEX_TOKEN_PUBLIC=f'{NOBITEX_TOKEN_PUBLIC}',
    NOBITEX_TOKEN=f'{NOBITEX_TOKEN}'
)

log_handler = TimedRotatingFileHandler("nobitex_bot.log", when="midnight", interval=1, backupCount=7, encoding='utf-8')
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(log_formatter)
logger.addHandler(log_handler)


symbols = [
    # 👑 ارزهای سنگین و فوق پرحجم (بدون خطای کندل خطی)
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "NEAR/USDT",
    "SUI/USDT", "TRX/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
    "LINK/USDT", "UNI/USDT", "LTC/USDT", "BCH/USDT", "TON/USDT",

    # 🚀 ارزهای پرطرفدار و ترند بازار
    "POL/USDT", "ALGO/USDT", "XLM/USDT", "HBAR/USDT", "VET/USDT",
    "GRT/USDT", "STX/USDT", "ANKR/USDT", "HMSTR/USDT", "DOGS/USDT",
    "TNSR/USDT", "2Z/USDT", "RENDER/USDT", "APE/USDT", "DYDX/USDT",
    "BASED/USDT",

    # ⚠️ ارزهای فرعی (مراقب حجم تومانی این‌ها باش)
    "ONE/USDT", "BICO/USDT"
]

DB_FILE = "live_signals.json"
last_signals = load_last_signals(symbols)

def update_drawdown_performance(current_total_balance):
    global max_peak_balance
    if current_total_balance > max_peak_balance:
        max_peak_balance = current_total_balance

    drawdown = ((max_peak_balance - current_total_balance) / max_peak_balance) * 100 if max_peak_balance > 0 else 0.0
    logger.info(
        f"📊 کارنامه عملکرد مالی | موجودی فعلی: {int(current_total_balance):,} تومان | حداکثر افت حساب: {drawdown:.2f}%")


def get_nobitex_data( symbol, timeframe=timeframe, limit=300):
    # تبدیل رشته به عدد (بسیار مهم برای API نوبیتکس)
    tf_map = {'15m': 15, '1h': 60, '4h': 240, '1d': 1440}
    timeframe = tf_map.get(timeframe, 60)  # اگر تایم‌فریم ناشناخته بود، 60 دقیقه فرض کن

    src = symbol.split('/')[0].lower()
    dst = symbol.split('/')[1].lower()

    url = f"{base_url}/market/udf/history"
    params = {
        "symbol": f"{src.upper()}{dst.upper()}",
        "resolution": timeframe,  # از مقدارِ عددی استفاده کن
        "from": int(time.time()) - (limit * timeframe * 60),  # از resolution استفاده کن
        "to": int(time.time())
    }

    response = requests.get(url, params=params)
    data = response.json()

    if data.get('s') == 'ok':
        df = pd.DataFrame({
            'timestamp': data['t'],
            'open': data['o'],
            'high': data['h'],
            'low': data['l'],
            'close': data['c'],
            'volume': data['v']
        })
        return df
    return None


def monitor_market():
    logger.info("🔥 ربات نوسان‌گیری با استراتژی کندل ۱ ساعته (1h) فعال شد...")

    symbols = [
        # 👑 ارزهای سنگین و فوق پرحجم (بدون خطای کندل خطی)
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "NEAR/USDT",
        "SUI/USDT", "TRX/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT",
        "LINK/USDT", "UNI/USDT", "LTC/USDT", "BCH/USDT", "TON/USDT",

        # 🚀 ارزهای پرطرفدار و ترند بازار
        "POL/USDT", "ALGO/USDT", "XLM/USDT", "HBAR/USDT", "VET/USDT",
        "GRT/USDT", "STX/USDT", "ANKR/USDT", "HMSTR/USDT", "DOGS/USDT",
        "TNSR/USDT", "2Z/USDT", "RENDER/USDT", "APE/USDT", "DYDX/USDT",
        "BASED/USDT",

        # ⚠️ ارزهای فرعی (مراقب حجم تومانی این‌ها باش)
        "ONE/USDT", "BICO/USDT"
    ]

    DB_FILE = "live_signals.json"
    last_signals = load_last_signals(symbols)
    last_nobitex_update = 0
    dollar_price = None
    current_wallet = 0.0
    last_report_date = None

    while True:
        current_now = datetime.datetime.now()
        current_time_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # زمان کلی چرخه فقط یک‌بار در ابتدای لوپ چاپ می‌شود
        print(f"\n🔄 --- چرخه پایش آنی بازار (تایم‌فریم 1h): {current_time_str} ---")

        if current_now.hour == 0 and current_now.minute == 0 and last_report_date != current_now.date():
            generate_daily_report(file_path=DB_FILE)
            last_report_date = current_now.date()

        current_timestamp = time.time()
        if current_timestamp - last_nobitex_update > 600 or dollar_price is None:
            logger.info("🔄 در حال به‌روزرسانی اطلاعات عمومی از نوبیتکس (قیمت تتر و موجودی)...")
            dollar_price = get_iran_dollar_price()
            current_wallet = get_nobitex_wallet_balance()
            update_drawdown_performance(current_wallet)
            last_nobitex_update = current_timestamp

        if dollar_price is None:
            logger.warning("⚠️ به دلیل عدم دسترسی به قیمت زنده تتر، این چرخه معاملاتی رد می‌شود.")
            print("💤 استراحت ۶۰ ثانیه‌ای تا چرخه بعدی...")
            time.sleep(60)
            continue

        print(f"  قیمت دلار (تومان): {dollar_price:,}")

        open_positions_count = sum(
            1 for sym in symbols
            if isinstance(last_signals.get(sym), dict) and last_signals[sym].get("signal") == "BUY"
        )

        for symbol in symbols:
            position_details = ""
            plain_log_line = ""
            color_code = BLUE
            status_display = "HOLD"

            # ✅ تعریف نام کوچک کوین در ابتدای بررسی برای استفاده در توابع نوبیتکس
            coin_name_lower = symbol.split('/')[0].lower()

            try:
                df = get_nobitex_data(symbol, timeframe=timeframe, limit=300)
                if df is None or df.empty or len(df) < 25:
                    continue

                df = calculate_ut_bot_2h_live(df, sensitivity=4, atr_period=14)

                live_row = df.iloc[-1]
                signal_row = df.iloc[-2]

                current_price = live_row['close']
                current_signal = signal_row['signal']
                atr_value = signal_row['ATR']

                # ✅ استعلام مستقیم قیمت تومانی لایو از خود نوبیتکس جهت همسان‌سازی ۱۰۰٪ با چارت
                nobitex_real_price = get_nobitex_live_price(coin_name_lower)
                #print(nobitex_real_price)
                if nobitex_real_price:
                    price_in_toman = nobitex_real_price
                else:
                    price_in_toman = current_price * dollar_price

                # این کد را جایگزینِ محاسبه‌یِ toman_str کن
                try:
                    # ابتدا مطمئن شو که قیمت یک عددِ اعشاریِ واقعی است
                    float_price = float(price_in_toman)
                    toman_str = f"{float_price:,.2f}"


                except (ValueError, TypeError):
                    toman_str = "0.00"
                    logger.error(f"خطای غیرقابل تبدیل در قیمت: {toman_str}")

                position = last_signals.get(symbol, {"signal": "HOLD", "entry_price": 0.0, "target_price": 0.0,
                                                     "stop_price": 0.0, "oco_order_id": None,
                                                     "updated_at": current_time_str, "trade_history": []})

                if isinstance(position, str):
                    position = {"signal": position, "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": current_time_str, "trade_history": []}

                if position and position.get("signal") == 'BUY':
                    color_code = GREEN
                    status_display = "BUY (OCO active)"

                    p_entry = position.get("entry_price", 0)
                    p_target = position.get("target_price", 0)
                    p_stop = position.get("stop_price", 0)

                    calc_qty = BUDGET_TOMAN / p_entry if p_entry > 0 else 0.0
                    potential_profit = (p_target - p_entry) * calc_qty if p_entry > 0 else 0.0
                    potential_loss = (p_entry - p_stop) * calc_qty if p_entry > 0 else 0.0

                    qty_formatted = f"{calc_qty:.3f}"
                    target_formatted = f"{p_target:,}"
                    stop_formatted = f"{p_stop:,}"

                    position_details = (
                        f" | تعداد: {qty_formatted:<8}"
                        f" | هدف: {target_formatted:<10}"
                        f" | استاپ: {stop_formatted:<10}"
                        f" | سود احتمالی: +{int(potential_profit):,} تومان "
                        f" | زیان احتمالی: -{int(potential_loss):,} تومان"
                    )
                elif current_signal == 'SELL':
                    color_code = RED
                    status_display = "SELL"
                    position_details = " | تعداد: -        | هدف: -          | استاپ: -         | سود/زیان: -"
                else:
                    color_code = BLUE
                    status_display = "HOLD"
                    position_details = " | تعداد: -        | هدف: -          | استاپ: -         | سود/زیان: -"

                # ساخت لاگ تمیز برای فایل متنی متنی
                plain_log_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details} | زمان: {current_time_str}"
                with open("market_monitor.log", "a", encoding="utf-8") as log_file:
                    log_file.write(plain_log_line + "\n")

                # چاپ در ترمینال بدون نمایش تاریخ تکراری در انتهای هر خط
                clean_console_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details}"
                print(f"{color_code}{clean_console_line}{RESET}")

                # خط جداکننده زیر هر ارز در کنسول
                print(f"{color_code}----------------------------------------------------------------------------------{RESET}")

                # =============================================================
                # 🛡️ سناریوی اول: مدیریت خروج پوزیشن باز (مستقل و امن در برابر خطای شبکه)
                # =============================================================
                if position["signal"] == 'BUY':
                    if PAPER_TRADING:
                        # بررسی دقیق قیمت لایو فقط با اهدافی که از قبل فیکس و ذخیره شده بودند
                        if price_in_toman <= position["stop_price"]:
                            logger.warning(f"📉 حد ضرر فرضی برای {symbol} در قیمت {price_in_toman:,} تومان لمس شد.")
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="Stop Loss (Paper)")

                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE",
                                "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str,
                                "entry_price": position.get("entry_price", 0.0),
                                "exit_price": int(price_in_toman),
                                "reason": "Stop Loss (Paper)"
                            }
                            last_signals[symbol] = {
                                "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": now_str, "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)

                        elif price_in_toman >= position["target_price"]:
                            logger.info(f"🎯 حد سود فرضی برای {symbol} در قیمت {price_in_toman:,} تومان لمس شد.")
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="Take Profit (Paper)")

                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE",
                                "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str,
                                "entry_price": position.get("entry_price", 0.0),
                                "exit_price": int(price_in_toman),
                                "reason": "Take Profit (Paper)"
                            }
                            last_signals[symbol] = {
                                "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": now_str, "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)

                    else:
                        # 💼 مدیریت پوزیشن واقعی در صرافی نوبیتکس با چک کردن ولت
                        url_wallet = "https://apiv2.nobitex.ir/v2/wallets"
                        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

                        res_w = _send_request_with_retry("POST", url_wallet, headers=headers, json_data={})
                        if res_w and res_w.get("status") == "ok":
                            wallets = res_w.get("wallets", {})
                            coin_balance = float(wallets.get(coin_name_lower.upper(), {}).get("balance", 0.0))

                            if coin_balance < (BUDGET_TOMAN / (position.get("entry_price") or 1)) * 0.05:
                                logger.info(f"🎉 [خروج موفق OCO] اردر OCO ارز {symbol} در صرافی با موفقیت اجرا و بسته شد.")

                                now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                past_trade = {
                                    "type": "REAL_OCO_TRADE",
                                    "entry_time": position.get("updated_at", "نامشخص"),
                                    "exit_time": now_str,
                                    "entry_price": position.get("entry_price", 0.0),
                                    "exit_price": int(price_in_toman),
                                    "reason": "اجرای حد سود یا حد ضرر OCO در صرافی نوبیتکس"
                                }

                                last_signals[symbol] = {
                                    "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                    "oco_order_id": None, "updated_at": now_str, "trade_history": position.get("trade_history", []) + [past_trade]
                                }
                                save_last_signals(last_signals)

                # =============================================================
                # 🟢 سناریوی دوم: صادر شدن سیگنال خرید جدید (فقط برای جفت‌ارزهای بدون پوزیشن)
                # =============================================================
                elif current_signal == 'BUY':
                    if position["signal"] == "BUY":
                        continue

                    if 'MAX_OPEN_POSITIONS' in globals() and open_positions_count >= MAX_OPEN_POSITIONS:
                        logger.warning(f"⚠️ سیگنال خرید {symbol} رد شد. سقف پوزیشن‌های باز ({MAX_OPEN_POSITIONS}) پر است.")
                        continue

                    dollar_price = get_iran_dollar_price()
                    if dollar_price is None:
                        logger.error(f"❌ خرید {symbol} به دلیل قطع ناگهانی شبکه در لحظه دریافت قیمت تتر لغو شد.")
                        continue

                    # محاسبه قیمت مبنای استراتژی از روی کوکوین
                    t_entry, t_target, t_stop = simulate_oco_trade(symbol, current_price, atr_value, dollar_price, df)
                    print("t_entry =", t_entry)
                    print("t_target =", t_target)
                    print("atr_value =", atr_value)
                    print("dollar_price =", dollar_price)

                    result = estimate_target_time(
                        t_entry,
                        t_target,
                        atr_value * dollar_price,
                        1
                    )

                    print("result =", result)
                    if result:
                        candles, hours, days = result
                        logger.info(
                            f"⏳ زمان تقریبی رسیدن به تارگت: "
                            f"{days:.1f} روز ({hours:.1f} ساعت)"
                        )

                    # ✅ کالیبره کردن اهداف بر اساس درصد روی قیمت واقعی و مچ‌شده‌ی نوبیتکس
                    profit_pct = (t_target - t_entry) / t_entry if t_entry > 0 else 0.0
                    loss_pct = (t_entry - t_stop) / t_entry if t_entry > 0 else 0.0

                    final_target = int(price_in_toman * (1 + profit_pct))
                    final_stop = int(price_in_toman * (1 - loss_pct))

                    # ثبت سفارش خرید با قیمت واقعی لایو نوبیتکس
                    order_success, order_id = place_buy_order_and_notify(symbol, price_in_toman, budget_toman=BUDGET_TOMAN)

                    if order_success:
                        if PAPER_TRADING:
                            real_quantity = BUDGET_TOMAN / (price_in_toman * 1.002)
                            oco_success = True
                            logger.info(f"🛡️ [Paper Trading] سفارش OCO فرضی برای {symbol} ثبت شد.")
                        else:
                            real_quantity = 0.0
                            logger.info(f"⏳ در حال استعلام دائم وضعیت سفارش {order_id} از نوبیتکس...")
                            max_attempts = 60
                            attempts = 0
                            while real_quantity <= 0 and attempts < max_attempts:
                                attempts += 1
                                real_quantity = get_nobitex_order_matched_amount(order_id)
                                if real_quantity > 0:
                                    logger.info(f"✅ سفارش پس از {attempts} بار تلاش کاملاً پر شد.")
                                    break
                                time.sleep(2)
                            oco_success = False

                        # 🟢 اصلاحیه حیاتی: خروج فرآیند ذخیره‌سازی از شرط لایه OCO جهت جلوگیری از خریدهای مکرر
                        if real_quantity > 0:
                            if not PAPER_TRADING:
                                logger.info(f"📈 [تکمیل خرید واقعی] مقدار خالص معامله شده بعد کارمزد: {real_quantity:.4f}")
                                oco_success = place_nobitex_oco_sell_order(symbol, real_quantity, final_target, final_stop)

                            # پوزیشن در هر شرایطی قفلِ خرید می‌شود تا از باگ تکرار جلوگیری شود
                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            last_signals[symbol] = {
                                "signal": "BUY",
                                "entry_price": int(price_in_toman * 1.002),
                                "target_price": final_target,
                                "stop_price": final_stop,
                                "oco_order_id": order_id if not PAPER_TRADING else None,
                                "updated_at": now_str,
                                "trade_history": position.get("trade_history", [])
                            }
                            save_last_signals(last_signals)
                            last_nobitex_update = 0
                            open_positions_count += 1

                            # =============================================================
                            # 📧 ارسال ایمیل فاکتور و جزییات پوزیشن جدید خرید
                            # =============================================================
                            trade_mode = "تست فرضی (Paper)" if PAPER_TRADING else "معامله واقعی"
                            rows_data = [
                                ("جفت ارز", symbol),
                                ("حالت معامله", trade_mode),
                                ("قیمت ورود", f"{int(price_in_toman * 1.002):,} تومان"),
                                ("تارگت OCO", f"{final_target:,} تومان"),
                                ("استاپ OCO", f"{final_stop:,} تومان"),
                                ("مقدار خرید", f"{real_quantity:.4f}")
                            ]
                            send_beautiful_email(
                                subject=f"🚀 سیگنال خرید {symbol} ({trade_mode})",
                                title=f"خرید موفقیت‌آمیز {symbol}",
                                type_color="#10b981",  # رنگ سبز تم ایمیل
                                rows_data=rows_data
                            )
                            # =============================================================
                        else:
                            logger.error(f"❌ خطای بحرانی: سفارش {order_id} در نوبیتکس پر نشد! پوزیشن ذخیره نشد.")

                time.sleep(0.2)
            except Exception as e:
                logger.error(f"⚠️ خطا در پردازش {symbol}: {e}")
                #print(logger.error(f"⚠️ خطا در پردازش {symbol}: {e}"))
                continue

        print(f"\n💤 استراحت ۳۰۰ ثانیه‌ای تا چرخه بعدی...")
        with open("market_monitor.log", "a", encoding="utf-8") as log_file:
            log_file.write(f"\n--- چرخه بعدی پایش در ۳۰۰ ثانیه آینده ---\n\n")

        time.sleep(300)


if __name__ == '__main__':
    monitor_market()