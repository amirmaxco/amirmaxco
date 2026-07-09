import logging
import json
import os
import time
from dotenv import load_dotenv
import requests
from datetime import datetime
import jdatetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

# ۲. حالا با دستور os.environ.get خیلی امن پسوردها را می‌کشیم بیرون
api_key = os.environ.get("NOBITEX_TOKEN_PUBLIC")

email_pass = os.environ.get("SENDER_PASSWORD")
sender_email = os.environ.get("SENDER_EMAIL")
receiver_email = os.environ.get("RECEIVER_EMAIL")


def get_wallet_balance(currency):
    wallet={}
    url = "https://apiv2.nobitex.ir/v2/wallets"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, headers=headers)
        result = response.json()
        print(result)
        if result.get("status") == "ok":
           wallets = result.get("wallets", {})
           if currency in wallets:
                   balance = float(wallets["RLS"].get("balance", 0.0))
                   return balance
           return 0.0
        else:
                   print("❌ خطا در دریافت موجودی:", result.get("message"))
                   return 0.0
    except Exception as e:
        print(f"🚨 خطای شبکه در دریافت موجودی: {e}")
        return 0.0



print(get_wallet_balance("RLS"))


