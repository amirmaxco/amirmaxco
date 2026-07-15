import time
from decimal import Decimal
from sqlite3.dbapi2 import paramstyle

import requests



def _send_request_with_retry(method, url, headers=None, json_data=None, params=None, retries=3):
    for attempt in range(retries):
        try:
            if method.upper() == "POST":
                res = requests.post(url, headers=headers, json=json_data, params=params, timeout=15)
            else:
                res = requests.get(url, headers=headers, params=params, timeout=15)
            return res.json()
        except Exception as e:
            print(f"⚠️ خطای شبکه: {e}")
            time.sleep(3)
    return None


def parse_trade_history(transactions):
    trades = {}
    for tx in transactions:
        # فقط تراکنش‌های نوع معامله
        if tx.get('type') == 'معامله' and tx.get('tp') == 'buy' or tx.get('tp') == 'sell':
            desc = tx.get('description', '')
            try:
                # استخراج قیمت از رشته
                price_str = desc.split('واحد')[1].split('تومان')[0].replace(',', '').strip()
                trades[tx['id']] = {
                    "time": tx['created_at'],
                    "symbol": tx['currency'].upper(),
                    "side": "خرید" if "خرید" in desc else "فروش",
                    "amount": float(tx['amount']),
                    "price": int(price_str)
                }
            except:
                continue
    return trades


def get_nobitex_trade_history():
    NOBITEX_TOKEN_PUBLIC = "4f607aff93a0f574deeda11c0a88c8d89ecc56af"
    url = "https://apiv2.nobitex.ir/users/transactions-history"
    headers = {"Authorization": f"Token {NOBITEX_TOKEN_PUBLIC}", "Content-Type": "application/json"}
    param={"currency":"tnsr"}
    # برای گرفتن لیست تراکنش‌ها معمولاً body خالی یا تنظیمات صفحه نیاز است
    res = _send_request_with_retry("POST", url, headers=headers,params=param, json_data={})
    print(res)
    sum_buy = 0  # مجموع مثبت‌ها (خریدها)
    sum_sell = 0  # مجموع منفی‌ها (فروش‌ها)
    if res and 'transactions' in res:
        # پردازش دیتا
        trades = parse_trade_history(res['transactions'])
        print(f"نام ارز :{param['currency']}")
        for t_id, info in trades.items():
            price = float(str(info['price']))
            amount = round(float(info['amount']), 1)
            price_real=float(price)*float(amount)

            if price_real < 0:
                sum_sell += price_real  # جمع مقادیر منفی (فروش)
            else:
                sum_buy += price_real  # جمع مقادیر مثبت (خرید)

            print("-" * 30)
            print(f" {info['amount']:.1f} * {info['price']}  = {price_real}")
            # محاسبات نهایی بعد از اتمام حلقه

            print(f"مجموع کل خریدها (مثبت): {sum_buy:.2f}")
            print(f"مجموع کل فروش‌ها (منفی): {sum_sell:.2f}")

            # محاسبه تفاضل (سود یا زیان)
            # چون sum_sell منفی است، با جمع کردنش با sum_buy عملاً تفریق انجام می‌شود
            net_profit = sum_buy + sum_sell

            print(f"سود/زیان خالص: {net_profit:.2f}")

if __name__ == "__main__":
    get_nobitex_trade_history()