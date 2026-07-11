import requests
import time
import logging

logger = logging.getLogger("NobitexBot")
logger.setLevel(logging.INFO)

def _send_request_with_retry(method, url, headers=None, json_data=None, params=None, retries=3):
    for attempt in range(retries):
        try:
            if method.upper() == "POST":
                res = requests.post(url, headers=headers, json=json_data, timeout=15)
            else:
                res = requests.get(url, headers=headers, params=params, timeout=15)
            return res.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.warning(f"⚠️ خطای شبکه (تلاش {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
            else:
                logger.error("🚨 قطع کامل اینترنت یا انسداد سرور صرافی.")
                return {"status": "failed", "message": "Network Error"}
