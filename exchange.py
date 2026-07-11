from utils import _send_request_with_retry
import logging
from dotenv import load_dotenv
import os
import ccxt


load_dotenv()


logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)
PAPER_TRADING = False
NOBITEX_TOKEN_PUBLIC = os.getenv("NOBITEX_TOKEN_PUBLIC")



def get_nobitex_live_price(coin_name):
    url = "https://apiv2.nobitex.ir/market/stats"
    res = _send_request_with_retry("GET", url, params={"srcCurrency": coin_name.lower(), "dstCurrency": "rls"})
    #print(res)
    if res and res.get("status") == "ok":
        pair = f"{coin_name.lower()}-rls"
        price_rial = res.get("stats", {}).get(pair, {}).get("latest", None)
        price_rial=float(price_rial)/10
        #print(f"{coin_name.upper()}-RLS price: {price_rial}")
        if price_rial:
            return float(price_rial)
    return None


def get_nobitex_wallet_balance():
    if PAPER_TRADING:
        return 10000000.0  # ۱۰ میلیون تومان موجودی فرضی در حالت تست

    url = "https://apiv2.nobitex.ir/v2/wallets"
    headers = {
        "Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}",
        "Content-Type": "application/json"
    }

    # ارسال درخواست بدون فیلترهای محدودکننده برای دریافت پاسخ کامل مالتی‌ولت
    res = _send_request_with_retry("POST", url, headers=headers, json_data={})
    #print(res)
    if res and res.get("status") == "ok":
        wallets = res.get("wallets", {})

        # بررسی وجود کیف پول ریال یا تومان در پاسخ صرافی
        if "RLS" in wallets:
            rial_balance = float(wallets["RLS"].get("balance", 0.0))
            #print(rial_balance/10)
            return rial_balance / 10.0  # تبدیل به تومان
        elif "IRT" in wallets:
            return float(wallets["IRT"].get("balance", 0.0))  # اگر خود تومان بود مستقیم برگردان

    logger.error(f"❌ خطا در دریافت موجودی از نوبیتکس. پاسخ صرافی: {res}")
    return 0.0


def get_iran_dollar_price():
    url = "https://apiv2.nobitex.ir/v3/orderbook/USDTIRT"
    res = _send_request_with_retry("GET", url, retries=3)

    # اصلاح شرط برای خواندن مستقیم از روت پاسخ v3
    if res and res.get('status') == 'ok' and 'lastTradePrice' in res:
        tether_rial = res['lastTradePrice']
        # تبدیل ریال به تومان
        #print(tether_rial/10)
        return int(float(tether_rial) / 10)

    logger.error("❌ خطای قطعی شبکه یا تغییر ساختار API: پس از ۳ بار تلاش مجدد، دریافت قیمت تتر ناموفق بود.")
    return None

