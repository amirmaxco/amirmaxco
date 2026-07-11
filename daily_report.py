import logging,os,sys,json
import jdatetime,datetime

from notifier import send_beautiful_email

BUDGET_TOMAN = 300000
logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)


def generate_daily_report(file_path):
    logger.info("📊 در حال محاسبه و تولید کارنامه معاملات ۲۴ ساعت گذشته...")
    file_path = "live_signals_v3.json"  # ⚠️ حواست به ورژن نام فایل (v2 یا v3) باشد

    if not os.path.exists(file_path):
        logger.warning("⚠️ فایلی برای گزارش‌گیری یافت نشد.")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"❌ خطا در خواندن فایل دیتابیس برای گزارش‌گیری: {e}")
        return None

    now = jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_trades = 0
    profitable_trades = 0
    total_profit_loss_toman = 0

    report_lines = []
    report_lines.append(f"📅 **گزارش عملکرد ربات نوسان‌گیری**")
    report_lines.append(f"⏱️ زمان تولید گزارش: {jdatetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}")
    report_lines.append("=" * 40)

    for symbol, config in data.items():
        if not isinstance(config, dict):
            continue

        history = config.get("trade_history", [])
        for trade in history:
            exit_time_str = trade.get("exit_time", "")
            try:
                exit_time = datetime.strptime(exit_time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue

            # ⏱️ فیلتر کردن معاملات ۲۴ ساعت گذشته
            if (now - exit_time).total_seconds() <= 86400:
                total_trades += 1
                entry_p = trade.get("entry_price", 0.0)
                exit_p = trade.get("exit_price", 0.0)
                trade_type = trade.get("type", "UNKNOWN")

                # محاسبه درصد سود/زیان این معامله
                if entry_p > 0:
                    pnl_percent = ((exit_p - entry_p) / entry_p) * 100
                else:
                    pnl_percent = 0.0

                # محاسبه سود یا زیان تومانی خالص روی این پوزیشن
                trade_pnl_toman = int(BUDGET_TOMAN * (pnl_percent / 100))
                total_profit_loss_toman += trade_pnl_toman

                if trade_pnl_toman > 0:
                    profitable_trades += 1
                    status_emoji = "🟢"
                else:
                    status_emoji = "🔴"

                report_lines.append(
                    f"{status_emoji} **{symbol}** ({trade_type})\n"
                    f"   📥 ورود: {int(entry_p):,} | 📤 خروج: {int(exit_p):,} تومان\n"
                    f"   📈 بازدهی: {pnl_percent:+.2f}% ({trade_pnl_toman:+,} تومان)"
                )
                report_lines.append("-" * 30)

    # 📈 محاسبه آمار کلی
    win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0.0
    pnl_color = "🟢" if total_profit_loss_toman >= 0 else "🔴"
    email_theme_color = "#10b981" if total_profit_loss_toman >= 0 else "#ef4444"

    summary_section = [
        "== 🎯 خلاصه وضعیت امروز ==",
        f"🔢 کل معاملات بسته شده: {total_trades}",
        f"✅ معاملات سودده: {profitable_trades} | ❌ معاملات زیان‌ده: {total_trades - profitable_trades}",
        f"📊 درصد موفقیت (Win Rate): {win_rate:.2f}%",
        f"{pnl_color} کل سود/زیان خالص امروز: {total_profit_loss_toman:,} تومان",
        "========================================"
    ]

    # چسباندن خلاصه به بالای گزارش جهت چاپ در ترمینال و فایل لاگ
    final_report = "\n".join(summary_section + [""] + report_lines)
    print(final_report)  # نمایش در ترمینال

    # 📝 ۱. ثبت گزارش نهایی در فایل متنی market_monitor.log
    with open("market_monitor.log", "a", encoding="utf-8") as log_file:
        log_file.write(f"\n\n=== 🕒 ثبت گزارش ۲۴ ساعته شبانه سیستم ({now.strftime('%Y-%m-%d')}) ===\n")
        log_file.write(final_report)
        log_file.write("\n======================================================\n\n")

    # 📧 ۲. ارسال ایمیل زیبا از وضعیت کارنامه شبانه
    if total_trades > 0:
        rows_data = [
            ("تعداد کل معاملات امروز", str(total_trades)),
            ("معاملات سودده (برنده)", f"🟢 {profitable_trades}"),
            ("معاملات زیان‌ده (بازنده)", f"🔴 {total_trades - profitable_trades}"),
            ("درصد موفقیت (Win Rate)", f"{win_rate:.2f}%"),
            ("سود/زیان خالص ۲۴ ساعت", f"{total_profit_loss_toman:,} تومان"),
            ("وضعیت نهایی بازدهی", "سودده" if total_profit_loss_toman >= 0 else "زیان‌ده")
        ]

        send_beautiful_email(
            subject=f"📊 کارنامه عملکرد ۲۴ ساعته ربات معاملاتی ({now.strftime('%Y-%m-%d')})",
            title=f"گزارش سود و زیان روزانه - وضعیت نهایی: {rows_data[5][1]}",
            type_color=email_theme_color,  # اگر سودده باشد سبز، در غیر این‌صورت قرمز می‌شود
            rows_data=rows_data
        )
        logger.info("📧 گزارش روزانه با موفقیت به ایمیل ارسال شد.")
    else:
        # حتی اگر معامله‌ای نبود هم یک ایمیل وضعیت جهت اطمینان از زنده بودن ربات بفرستد
        rows_data = [
            ("وضعیت معاملات", "امروز هیچ پوزیشنی بسته نشده است."),
            ("تعداد معامله", "0")
        ]
        send_beautiful_email(
            subject=f"💤 گزارش وضعیت ربات معاملاتی ({now.strftime('%Y-%m-%d')})",
            title="امروز معامله بسته‌شده‌ای وجود نداشت",
            type_color="#1e293b",  # رنگ سرمه‌ای خنثی
            rows_data=rows_data
        )
        logger.info("💤 امروز معامله بسته‌شده‌ای وجود نداشت؛ ایمیل خلاصه (ربات زنده است) ارسال شد.")

    return final_report