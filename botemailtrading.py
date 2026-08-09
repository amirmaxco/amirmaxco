def monitor_market():
    global target_day
    logger.info("🔥 ربات نوسان‌گیری با استراتژی کندل ۱ ساعته (1h) فعال شد...")

    symbols = [
        "BTC/IRT", "ETH/IRT", "SOL/IRT", "AVAX/IRT", "NEAR/IRT",
        "SUI/IRT", "TRX/IRT", "XRP/IRT", "ADA/IRT", "DOGE/IRT",
        "LINK/IRT", "UNI/IRT", "LTC/IRT", "BCH/IRT", "TON/IRT",
        "POL/IRT", "ALGO/IRT", "XLM/IRT", "HBAR/IRT", "VET/IRT",
        "GRT/IRT", "STX/IRT", "ANKR/IRT", "HMSTR/IRT", "DOGS/IRT",
        "TNSR/IRT", "2Z/IRT", "RENDER/IRT", "APE/IRT", "DYDX/IRT",
        "BASED/IRT",
        "ONE/IRT", "BICO/IRT","NOT/IRT","KAITO/IRT","PUMP/IRT","BARD/IRT","PROM/IRT","LA/IRT","ZAMA/IRT"
    ]

    DB_FILE = "live_signals_v2.json"
    last_signals = load_last_signals(symbols)
    last_nobitex_update = 0
    dollar_price = None
    current_wallet = 0.0
    last_report_date = None

    while True:
        current_now = datetime.now()
        current_time_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n🔄 --- چرخه پایش آنی بازار (تایم‌فریم 1h): {current_time_str} ---")

        if current_now.hour == 0 and current_now.minute == 0 and last_report_date != current_now.date():
            try:
                generate_daily_report(file_path=DB_FILE)
            except Exception as e:
                logger.error(f"⚠️ خطا در تولید گزارش روزانه: {e}")
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

        print(f"  قیمت دلار (تومان): {dollar_price:,}  موجودی حساب شما : {current_wallet:.2f}")

        open_positions_count = sum(
            1 for sym in symbols
            if isinstance(last_signals.get(sym), dict) and last_signals[sym].get("signal") == "BUY"
        )

        log_lines_buffer = []

        for symbol in symbols:
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

                # ✅ رفع باگ اصلی: fallback درست به current_price * dollar_price
                nobitex_real_price = get_nobitex_live_price(coin_name_lower)
                if nobitex_real_price is not None:
                    price_in_toman = nobitex_real_price
                elif current_price is not None and dollar_price is not None:
                    price_in_toman = current_price * dollar_price
                else:
                    logger.warning(f"⚠️ قیمت معتبر برای {symbol} در دسترس نیست، این نماد رد شد.")
                    continue

                toman_str = f"{price_in_toman:,.2f}" if price_in_toman < 100 else f"{int(price_in_toman):,}"

                position = last_signals.get(symbol)
                if not isinstance(position, dict):
                    position = {
                        "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                        "oco_order_id": None, "updated_at": current_time_str,"target_day":0.0, "trade_history": []
                    }
                    last_signals[symbol] = position
                
                color_code = BLUE
                status_display = "HOLD"
                position_details = " | تعداد: -        | هدف: -          | استاپ: -         | سود/زیان: -"

                # دریافت بالاترین قیمت ۲۴ ساعت گذشته از نوبیتکس
                maxprice = maxhad(coin_name_lower)

                if position.get("signal") == 'BUY':
                    color_code = GREEN
                    status_display = "BUY (OCO active)"

                    p_entry = position.get("entry_price", 0)
                    p_target = position.get("target_price", 0)
                    p_stop = position.get("stop_price", 0)
                    target_day=position.get("target_day", 0)

                    calc_qty = BUDGET_TOMAN / p_entry if p_entry > 0 else 0.0
                    potential_profit = (p_target - p_entry) * calc_qty if p_entry > 0 else 0.0
                    potential_loss = (p_entry - p_stop) * calc_qty if p_entry > 0 else 0.0

                    position_details = (
                        f" | تعداد: {calc_qty:<8.3f}"
                        f" | هدف: {p_target:<10,}"
                        f" | استاپ: {p_stop:<10,}"
                        f" | سود احتمالی: +{int(potential_profit):,} تومان "
                        f" | زیان احتمالی: -{int(potential_loss):,} تومان"
                        f"| بازه زمانی رسیده به هدف : {target_day}"
                    )
                elif current_signal == 'SELL':
                    color_code = RED
                    status_display = "SELL"

                plain_log_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details} | زمان: {current_time_str}  زمان تقریبی رسیدن به قیمت هدف : {target_day}"
                log_lines_buffer.append(plain_log_line)

                clean_console_line = f"📊 {symbol:<10} | قیمت: {toman_str:<10} تومان | وضعیت: {status_display:<18}{position_details}"
                print(f"{color_code}{clean_console_line}{RESET}")
                print(f"{color_code}{'-' * 84}{RESET}")

                # ============ مدیریت خروج پوزیشن باز ============
                if position.get("signal") == 'BUY':
                    if PAPER_TRADING:
                        # بررسی اینکه آیا قیمت لحظه‌ای یا حداکثر قیمت ۲۴ ساعت گذشته به حد ضرر یا حد سود رسیده است
                        hit_stop = price_in_toman <= position["stop_price"]
                        hit_target = price_in_toman >= position["target_price"] or (maxprice is not None and maxprice >= position["target_price"])

                        if hit_stop:
                            logger.warning(f"📉 حد ضرر فرضی برای {symbol} در قیمت {price_in_toman:,} تومان لمس شد.")
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="Stop Loss (Paper)")
                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE", "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str, "entry_price": position.get("entry_price", 0.0),
                                "exit_price": int(price_in_toman),"target_day":position.get("target_day"),
                                "reason": "Stop Loss (Paper)"
                            }
                            last_signals[symbol] = {
                                "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": now_str,"target_day":position.get("target_day"),
                                "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)

                        elif hit_target:
                            logger.info(f"🎯 حد سود فرضی برای {symbol} لمس شد (قیمت لحظه‌ای: {price_in_toman:,} | اوج ۲۴ ساعته: {maxprice}).")
                            simulate_sell_trade(symbol, current_price, dollar_price, reason="Take Profit (Paper)")
                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            past_trade = {
                                "type": "PAPER_TRADE", "entry_time": position.get("updated_at", "نامشخص"),
                                "exit_time": now_str, "entry_price": position.get("entry_price", 0.0),"target_day":position.get("target_day"),
                                "exit_price": int(price_in_toman), "reason": "Take Profit (Paper)"
                            }
                            last_signals[symbol] = {
                                "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                "oco_order_id": None, "updated_at": now_str,"target_day":position.get("target_day"),
                                "trade_history": position.get("trade_history", []) + [past_trade]
                            }
                            save_last_signals(last_signals)
                    else:
                        url_wallet = "https://apiv2.nobitex.ir/v2/wallets"
                        headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}
                        res_w = _send_request_with_retry("POST", url_wallet, headers=headers, json_data={})
                        if res_w and res_w.get("status") == "ok":
                            wallets = res_w.get("wallets", {}) or {}
                            wallet_entry = wallets.get(coin_name_lower.upper()) or {}
                            coin_balance = float(wallet_entry.get("balance", 0.0))

                            entry_price = position.get("entry_price") or 1
                            if coin_balance < (BUDGET_TOMAN / entry_price) * 0.05:
                                logger.info(f"🎉 [خروج موفق OCO] اردر OCO ارز {symbol} در صرافی با موفقیت اجرا و بسته شد.")
                                now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                past_trade = {
                                    "type": "REAL_OCO_TRADE", "entry_time": position.get("updated_at", "نامشخص"),
                                    "exit_time": now_str, "entry_price": position.get("entry_price", 0.0),
                                    "exit_price": int(price_in_toman),"target_day":position.get("target_day"),
                                    "reason": "اجرای حد سود یا حد ضرر OCO در صرافی نوبیتکس"
                                }
                                last_signals[symbol] = {
                                    "signal": "HOLD", "entry_price": 0.0, "target_price": 0.0, "stop_price": 0.0,
                                    "oco_order_id": None, "updated_at": now_str,"target_day":0.0,
                                    "trade_history": position.get("trade_history", []) + [past_trade]
                                }
                                save_last_signals(last_signals)

                # ============ صدور سیگنال خرید جدید ============
                elif current_signal == 'BUY' and position.get("signal") != "BUY":
                    if open_positions_count >= MAX_OPEN_POSITIONS:
                        color_code=PINK
                        logger.warning(f"{color_code}⚠️ سیگنال خرید {symbol} رد شد. سقف پوزیشن‌های باز ({MAX_OPEN_POSITIONS}) پر است.")
                        continue

                    dollar_price_now = get_iran_dollar_price()
                    if dollar_price_now is None:
                        logger.error(f"❌ خرید {symbol} به دلیل قطع ناگهانی شبکه در لحظه دریافت قیمت تتر لغو شد.")
                        continue
                    dollar_price = dollar_price_now

                    t_entry, t_target, t_stop = simulate_oco_trade(symbol, current_price, atr_value, dollar_price, df)

                    result = estimate_target_time(t_entry, t_target, atr_value * dollar_price, 1)
                    eta_str = "نامشخص"
                    if result:
                        candles, hours, days = result
                        eta_str = f"{days:.1f} روز ({hours:.1f} ساعت / ~{candles:.1f} کندل)"
                        logger.info(f"⏳ زمان تقریبی رسیدن به تارگت برای {symbol}: {eta_str}")

                    # ✅ نمایش صریح در کنسول (نه فقط لاگ فایل)
                    print(f"{GREEN}⏳ [{symbol}] زمان تقریبی رسیدن به هدف: {eta_str}{RESET}")

                    profit_pct = (t_target - t_entry) / t_entry if t_entry > 0 else 0.0
                    loss_pct = (t_entry - t_stop) / t_entry if t_entry > 0 else 0.0

                    final_target = int(price_in_toman * (1 + profit_pct))
                    final_stop = int(price_in_toman * (1 - loss_pct))

                    order_success, order_id = place_buy_order_and_notify(symbol, price_in_toman, budget_toman=BUDGET_TOMAN)

                    if order_success:
                        if PAPER_TRADING:
                            real_quantity = BUDGET_TOMAN / (price_in_toman * 1.002)
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

                        if real_quantity > 0:
                            if not PAPER_TRADING:
                                logger.info(f"📈 [تکمیل خرید واقعی] مقدار خالص معامله شده بعد کارمزد: {real_quantity:.4f}")
                                place_nobitex_oco_sell_order(symbol, real_quantity, final_target, final_stop)

                            now_str = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            last_signals[symbol] = {
                                "signal": "BUY",
                                "entry_price": int(price_in_toman * 1.002),
                                "target_price": final_target,
                                "stop_price": final_stop,
                                "oco_order_id": order_id if not PAPER_TRADING else None,
                                "updated_at": now_str,
                                "target_day": eta_str,
                                "trade_history": position.get("trade_history", [])
                            }
                            save_last_signals(last_signals)
                            last_nobitex_update = 0
                            open_positions_count += 1

                            trade_mode = "تست فرضی (Paper)" if PAPER_TRADING else "معامله واقعی"
                            rows_data = [
                                ("جفت ارز", symbol),
                                ("حالت معامله", trade_mode),
                                ("قیمت ورود", f"{int(price_in_toman * 1.002):,} تومان"),
                                ("تارگت OCO", f"{final_target:,} تومان"),
                                ("استاپ OCO", f"{final_stop:,} تومان"),
                                ("زمان تقریبی رسیدن به هدف", eta_str),
                                ("مقدار خرید", f"{real_quantity:.4f}")
                            ]
                            send_beautiful_email(
                                subject=f"🚀 سیگنال خرید {symbol} ({trade_mode})",
                                title=f"خرید موفقیت‌آمیز {symbol}",
                                type_color="#10b981",
                                rows_data=rows_data
                            )
                        else:
                            logger.error(f"❌ خطای بحرانی: سفارش {order_id} در نوبیتکس پر نشد! پوزیشن ذخیره نشد.")

                time.sleep(0.2)
            except Exception as e:
                logger.error(f"⚠️ خطا در پردازش {symbol}: {e}", exc_info=True)
                continue

        if log_lines_buffer:
            try:
                with open("market_monitor.log", "a", encoding="utf-8") as log_file:
                    log_file.write("\n".join(log_lines_buffer) + "\n")
            except OSError as e:
                logger.error(f"⚠️ خطا در نوشتن فایل لاگ: {e}")

        print(f"\n💤 استراحت ۳۰۰ ثانیه‌ای تا چرخه بعدی...")
        try:
            with open("market_monitor.log", "a", encoding="utf-8") as log_file:
                log_file.write(f"\n--- چرخه بعدی پایش در ۳۰۰ ثانیه آینده ---\n\n")
        except OSError as e:
            logger.error(f"⚠️ خطا در نوشتن فایل لاگ: {e}")

        time.sleep(300)
