import requests
import time,os,json
from dotenv import load_dotenv

load_dotenv()

NOBITEX_TOKEN_PUBLIC = os.getenv("NOBITEX_TOKEN_PUBLIC")
NOBITEX_TOKEN = os.getenv("NOBITEX_TOKEN")

class NobitexClient:
    def __init__(self, NOBITEX_TOKEN_PUBLIC, NOBITEX_TOKEN):
        self.NOBITEX_TOKEN_PUBLIC = f'{NOBITEX_TOKEN_PUBLIC}'
        self.NOBITEX_TOKEN = f'{NOBITEX_TOKEN}'
        self.base_url = "https://apiv2.nobitex.ir"

    def _headers(self):
        return {
            "Authorization": f"Token {self.NOBITEX_TOKEN_PUBLIC}",
            "Content-Type": "application/json"
        }

    # دریافت قیمت لحظه‌ای (جایگزینِ fetchTicker)
    def get_ticker(self, symbol):
        url = f"{self.base_url}/market/stats"
        params = {"srcCurrency": symbol.split('/')[0].lower(), "dstCurrency": "rls"}
        response = requests.get(url, params=params)
        return response.json()

    # ثبت سفارش (جایگزینِ createOrder)
    def create_order(self, symbol, type, amount, price, execution="market"):
        url = f"{self.base_url}/market/orders/add"
        payload = {
            "type": type,
            "execution": execution,
            "srcCurrency": symbol.split('/')[0].lower(),
            "dstCurrency": "rls",
            "amount": str(amount),
            "price": str(price)
        }
        response = requests.post(url, headers=self._headers(), json=payload)
        return response.json()