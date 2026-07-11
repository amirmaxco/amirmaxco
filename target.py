def simulate_oco_trade(symbol, current_price, atr_value, dollar_price, df):
    coin_name = symbol.split('/')[0]
    recent_low = df['low'].iloc[-21:-1].min()

    stop_raw = min(current_price - (3.0 * atr_value), recent_low * 0.99)
    risk_amount = current_price - stop_raw
    target_raw = current_price + (risk_amount * 2.0)

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
    return price_in_toman, target_in_toman, stop_in_toman



def simulate_sell_trade(symbol, current_price, dollar_price, reason="سیگنال اندیکاتور"):
    coin_name = symbol.split('/')[0]
    price_in_toman = current_price * dollar_price
    toman_price = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

    # 🔴 اضافه کردن لاگ به ترمینال قبل از ارسال ایمیل برای شفافیت پایش
    logger.warning(f"📉 [خروج فرضی] ماشه خروج برای {symbol} چکانده شد! قیمت: {toman_price} تومان | دلیل: {reason}")

    subject = f"🚨 [خروج فوری] {coin_name}"
    title = f"🔴 سیگنال فروش و خروج: {coin_name}"

    rows_data = [
        ("نام ارز دیجیتال", coin_name),
        ("قیمت فروش (دلار)", f"${current_price:.5f}"),
        ("قیمت فروش (تومان)", f"{toman_price} تومان"),
        ("دلیل خروج از معامله", reason)
    ]
    send_beautiful_email(subject, title, "#ef4444", rows_data)

    def place_buy_order_and_notify(symbol, price_toman, budget_toman):
        global daily_trade_count, PAPER_TRADING
        coin_name = symbol.split('/')[0].lower()

        if not check_daily_limits():
            logger.warning("🚫 محدودیت تعداد معاملات روزانه تکمیل شده است.")
            return False, None

        # ۱. دریافت موجودی زنده ولت قبل از خرید
        total_balance = get_nobitex_wallet_balance()
        logger.info(f"🔍 [بررسی ولت قبل از خرید] موجودی دریافت شده توسط ربات: {total_balance} تومان")

        # ۲. تعیین بودجه امن
        if total_balance > 0:
            calculated_budget = total_balance * (RISK_PERCENT / 100)
            final_budget = max(calculated_budget, budget_toman)

            if final_budget >= total_balance * 0.95:
                safe_budget_toman = total_balance * 0.95
            else:
                safe_budget_toman = final_budget
        else:
            logger.warning("⚠️ موجودی ولت توسط صرافی ۰ یا نامعتبر برگشت! استفاده از بودجه پیش‌فرض.")
            safe_budget_toman = budget_toman

        # ۳. دریافت قیمت زنده نوبیتکس
        live_price_toman = get_nobitex_live_price(coin_name) or price_toman

        # ✅ اصلاح منطق گپ: قیمت خرید لیمیت را ۰.۲٪ بالاتر می‌گذاریم تا آنی پر شود
        simulated_price_toman = live_price_toman * 1.002
        simulated_price_rial = int(simulated_price_toman * 10)

        # ✅ رفع باگ موجودی کافی نیست: محاسبه تعداد توکن بر اساس قیمت نهایی ارسال شده (نه قیمت لایو)
        calculated_amount = safe_budget_toman / simulated_price_toman
        string_amount = f"{calculated_amount:.6f}"

        budget_rial = int(safe_budget_toman * 10)
        logger.info(
            f"🔥 [ورود مارکت آنی] درخواست خرید {coin_name.upper()} | بودجه ارسالی: {int(safe_budget_toman)} تومان | قیمت لیمیت: {int(simulated_price_toman):,} تومان"
        )

        if PAPER_TRADING:
            logger.info(f"✨ [Paper Trading] خرید فرضی شبیه‌سازی شد.")
            daily_trade_count += 1
            return True, "mock_order_id"

        url = "https://apiv2.nobitex.ir/market/orders/add"
        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

        payload = {
            "type": "buy",
            "execution": "market",
            "srcCurrency": coin_name.lower(),
            "dstCurrency": "rls",
            "amount": string_amount,
            "price": f"{simulated_price_rial}"
        }

        res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)

        if res and res.get("status") == "ok":
            order_id = res.get("order", {}).get("id")
            logger.info(f"🟢 خرید مارکت با موفقیت ثبت شد. شناسه اردر: {order_id}.")
            daily_trade_count += 1

            # ✅ ارسال ایمیل بر اساس قیمت خرید واقعی تنظیم‌شده
            send_nobitex_order_email(coin_name, simulated_price_toman, safe_budget_toman, calculated_amount)
            return True, order_id
        else:
            if res:
                error_code = res.get("code", "NO_CODE")
                error_msg = res.get("message", "خطای ناشناخته")
                full_error_details = f"[{error_code}] {error_msg}"
            else:
                full_error_details = "عدم پاسخ صرافی (تایم‌اوت یا قطع ارتباط)"

            logger.error(f"❌ خطای ثبت سفارش خرید مارکت: {full_error_details} | پاسخ کامل سرور: {res}")
            send_nobitex_error_email(coin_name, "خرید مارکت آنی", full_error_details)
            return False, None

    def get_nobitex_order_matched_amount(order_id):
        if PAPER_TRADING:
            return 0.05

        url = "https://apiv2.nobitex.ir/market/orders/status"
        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

        for _ in range(2):
            res = _send_request_with_retry("POST", url, headers=headers, json_data={"id": order_id})
            if res and res.get("status") == "ok":
                order_info = res["order"]
                matched_qty = float(order_info.get("matchedAmount", 0.0))
                if matched_qty > 0:
                    return matched_qty
            time.sleep(5)
        return 0.0

    def place_nobitex_oco_sell_order(symbol, quantity, target_toman, stop_toman):
        coin_name = symbol.split('/')[0].lower()

        if PAPER_TRADING:
            logger.info(f"🛡️ [Paper Trading] سفارش OCO فرضی برای {coin_name.upper()} ثبت شد.")
            send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
            return True

        url = "https://apiv2.nobitex.ir/market/orders/add"
        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

        target_rial = f"{target_toman:.2f}"
        stop_rial = f"{stop_toman:.2f}"

        # قیمت نهایی فروش در صورت فعال شدن استاپ لاس (کمی پایین‌تر جهت پر شدن قطعی)
        stop_limit_toman = stop_toman * 0.99
        stop_limit_str = f"{stop_limit_toman:.2f}"

        # 💎 اصلاح فرمت مقدار اعشار به صورت پویا (حذف صفرهای اضافی و پشتیبانی تا ۸ رقم اعشار برای ارزهای سنگین)
        string_amount = f"{quantity:.8f}".rstrip('0').rstrip('.')

        # 🚀 اصلاح اصلی: تغییر execution به oco جهت فعال‌سازی همزمان حد سود و ضرر
        payload = {
            "type": "sell",
            "execution": "oco",
            "srcCurrency": coin_name,
            "dstCurrency": "rls",  # دقت کن: نوبیتکس در برخی APIها قیمت را بر اساس ارز مقصد (ریال/تومان) می‌خواهد
            "amount": string_amount,
            "price": target_rial,
            "stopPrice": stop_rial,
            "stopLimitPrice": stop_limit_str
        }

        for attempt in range(4):
            res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
            if res and res.get("status") == "ok":
                logger.info(f"🛡️ سفارش OCO واقعی با موفقیت قفل شد. شناسه: {res.get('order', {}).get('id')}")
                send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
                return True

            error_msg = res.get("message", "خطای ناشناخته") if res else "عدم پاسخ صرافی"
            logger.warning(f"⚠️ تلاش مجدد برای OCO (تلاش {attempt + 1}/4) | علت خطا: {error_msg} | پاسخ: {res}")
            time.sleep(3)

        logger.critical("🚨 ثبت OCO ناموفق بود! فعال‌سازی سفارش حد ضرر اضطراری تکی...")

        # 🚑 اصلاح بخش اضطراری: در صورت شکست OCO، یک استاپ‌لیمیت فروش تکی ثبت می‌شود
        backup_payload = {
            "type": "sell",
            "execution": "stop_limit",
            "srcCurrency": coin_name,
            "dstCurrency": "rls",
            "amount": string_amount,
            "price": f"{stop_limit_str}",  # قیمتی که روی آن می‌فروشد
            "stopPrice": f"{stop_rial}"  # ماشه‌ای که با رسیدن به آن اردر فعال می‌شود
        }
        _send_request_with_retry("POST", url, headers=headers, json_data=backup_payload)

        send_nobitex_error_email(coin_name, "فروش OCO (خطای مداوم)",
                                 "سیستم به سفارش استاپ لیمیت اضطراری جایگزین سوییچ کرد.")
        return False

def place_buy_order_and_notify(symbol, price_toman, budget_toman):
    global daily_trade_count, PAPER_TRADING
    coin_name = symbol.split('/')[0].lower()

    if not check_daily_limits():
        logger.warning("🚫 محدودیت تعداد معاملات روزانه تکمیل شده است.")
        return False, None

    # ۱. دریافت موجودی زنده ولت قبل از خرید
    total_balance = get_nobitex_wallet_balance()
    logger.info(f"🔍 [بررسی ولت قبل از خرید] موجودی دریافت شده توسط ربات: {total_balance} تومان")

    # ۲. تعیین بودجه امن
    if total_balance > 0:
        calculated_budget = total_balance * (RISK_PERCENT / 100)
        final_budget = max(calculated_budget, budget_toman)

        if final_budget >= total_balance * 0.95:
            safe_budget_toman = total_balance * 0.95
        else:
            safe_budget_toman = final_budget
    else:
        logger.warning("⚠️ موجودی ولت توسط صرافی ۰ یا نامعتبر برگشت! استفاده از بودجه پیش‌فرض.")
        safe_budget_toman = budget_toman

    # ۳. دریافت قیمت زنده نوبیتکس
    live_price_toman = get_nobitex_live_price(coin_name) or price_toman

    # ✅ اصلاح منطق گپ: قیمت خرید لیمیت را ۰.۲٪ بالاتر می‌گذاریم تا آنی پر شود
    simulated_price_toman = live_price_toman * 1.002
    simulated_price_rial = int(simulated_price_toman * 10)

    # ✅ رفع باگ موجودی کافی نیست: محاسبه تعداد توکن بر اساس قیمت نهایی ارسال شده (نه قیمت لایو)
    calculated_amount = safe_budget_toman / simulated_price_toman
    string_amount = f"{calculated_amount:.6f}"

    budget_rial = int(safe_budget_toman * 10)
    logger.info(
        f"🔥 [ورود مارکت آنی] درخواست خرید {coin_name.upper()} | بودجه ارسالی: {int(safe_budget_toman)} تومان | قیمت لیمیت: {int(simulated_price_toman):,} تومان"
    )

    if PAPER_TRADING:
        logger.info(f"✨ [Paper Trading] خرید فرضی شبیه‌سازی شد.")
        daily_trade_count += 1
        return True, "mock_order_id"

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    payload = {
        "type": "buy",
        "execution": "market",
        "srcCurrency": coin_name.lower(),
        "dstCurrency": "rls",
        "amount": string_amount,
        "price": f"{simulated_price_rial}"
    }

    res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)

    if res and res.get("status") == "ok":
        order_id = res.get("order", {}).get("id")
        logger.info(f"🟢 خرید مارکت با موفقیت ثبت شد. شناسه اردر: {order_id}.")
        daily_trade_count += 1

        # ✅ ارسال ایمیل بر اساس قیمت خرید واقعی تنظیم‌شده
        send_nobitex_order_email(coin_name, simulated_price_toman, safe_budget_toman, calculated_amount)
        return True, order_id
    else:
        if res:
            error_code = res.get("code", "NO_CODE")
            error_msg = res.get("message", "خطای ناشناخته")
            full_error_details = f"[{error_code}] {error_msg}"
        else:
            full_error_details = "عدم پاسخ صرافی (تایم‌اوت یا قطع ارتباط)"

        logger.error(f"❌ خطای ثبت سفارش خرید مارکت: {full_error_details} | پاسخ کامل سرور: {res}")
        send_nobitex_error_email(coin_name, "خرید مارکت آنی", full_error_details)
        return False, None

def get_nobitex_order_matched_amount(order_id):
    if PAPER_TRADING:
        return 0.05

    url = "https://apiv2.nobitex.ir/market/orders/status"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    for _ in range(2):
        res = _send_request_with_retry("POST", url, headers=headers, json_data={"id": order_id})
        if res and res.get("status") == "ok":
            order_info = res["order"]
            matched_qty = float(order_info.get("matchedAmount", 0.0))
            if matched_qty > 0:
                return matched_qty
        time.sleep(5)
    return 0.0


def place_nobitex_oco_sell_order(symbol, quantity, target_toman, stop_toman):
    coin_name = symbol.split('/')[0].lower()

    if PAPER_TRADING:
        logger.info(f"🛡️ [Paper Trading] سفارش OCO فرضی برای {coin_name.upper()} ثبت شد.")
        send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
        return True

    url = "https://apiv2.nobitex.ir/market/orders/add"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}

    target_rial = f"{target_toman:.2f}"
    stop_rial = f"{stop_toman:.2f}"

    # قیمت نهایی فروش در صورت فعال شدن استاپ لاس (کمی پایین‌تر جهت پر شدن قطعی)
    stop_limit_toman = stop_toman * 0.99
    stop_limit_str = f"{stop_limit_toman:.2f}"

    # 💎 اصلاح فرمت مقدار اعشار به صورت پویا (حذف صفرهای اضافی و پشتیبانی تا ۸ رقم اعشار برای ارزهای سنگین)
    string_amount = f"{quantity:.8f}".rstrip('0').rstrip('.')

    # 🚀 اصلاح اصلی: تغییر execution به oco جهت فعال‌سازی همزمان حد سود و ضرر
    payload = {
        "type": "sell",
        "execution": "oco",
        "srcCurrency": coin_name,
        "dstCurrency": "rls",  # دقت کن: نوبیتکس در برخی APIها قیمت را بر اساس ارز مقصد (ریال/تومان) می‌خواهد
        "amount": string_amount,
        "price": target_rial,
        "stopPrice": stop_rial,
        "stopLimitPrice": stop_limit_str
    }

    for attempt in range(4):
        res = _send_request_with_retry("POST", url, headers=headers, json_data=payload)
        if res and res.get("status") == "ok":
            logger.info(f"🛡️ سفارش OCO واقعی با موفقیت قفل شد. شناسه: {res.get('order', {}).get('id')}")
            send_nobitex_oco_success_email(coin_name, quantity, target_toman, stop_toman)
            return True

        error_msg = res.get("message", "خطای ناشناخته") if res else "عدم پاسخ صرافی"
        logger.warning(f"⚠️ تلاش مجدد برای OCO (تلاش {attempt + 1}/4) | علت خطا: {error_msg} | پاسخ: {res}")
        time.sleep(3)

    logger.critical("🚨 ثبت OCO ناموفق بود! فعال‌سازی سفارش حد ضرر اضطراری تکی...")

    # 🚑 اصلاح بخش اضطراری: در صورت شکست OCO، یک استاپ‌لیمیت فروش تکی ثبت می‌شود
    backup_payload = {
        "type": "sell",
        "execution": "stop_limit",
        "srcCurrency": coin_name,
        "dstCurrency": "rls",
        "amount": string_amount,
        "price": f"{stop_limit_str}",  # قیمتی که روی آن می‌فروشد
        "stopPrice": f"{stop_rial}"  # ماشه‌ای که با رسیدن به آن اردر فعال می‌شود
    }
    _send_request_with_retry("POST", url, headers=headers, json_data=backup_payload)

    send_nobitex_error_email(coin_name, "فروش OCO (خطای مداوم)",
                             "سیستم به سفارش استاپ لیمیت اضطراری جایگزین سوییچ کرد.")
    return False


def estimate_target_time(entry_price, target_price, atr_value, timeframe_hours):
    if atr_value <= 0:
        return None

    distance = abs(target_price - entry_price)

    candles = distance / atr_value

    hours = candles * timeframe_hours

    days = hours / 24

    return candles, hours, days
