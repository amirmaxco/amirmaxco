import os
import smtplib
import subprocess
import sys
import tkinter as tk
from email.message import EmailMessage
from tkinter.scrolledtext import ScrolledText
import time
import threading
from datetime import datetime
import jdatetime
import pystray
from PIL import Image, ImageDraw


SENDER_EMAIL = "amirghoorbaninia3002@gmail.com"
SENDER_PASSWORD = "qcmg jxrc vxic mucu"
RECEIVER_EMAIL = "amirghoorbaninia3002@gmail.com"

# تابع ثبت لاگ
def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}\n"
    text_area.insert(tk.END, full_message)
    text_area.see(tk.END)
    with open("network_log.txt", "a", encoding="utf-8") as f:
        f.write(full_message)


# گرفتن لیست تمام کارت‌های شبکه
def get_all_network_adapters():
    result = subprocess.run("netsh interface show interface", shell=True, capture_output=True, text=True)
    lines = result.stdout.splitlines()
    adapters = []
    for line in lines:
        if "Enabled" in line or "Disabled" in line:
            parts = line.split()
            adapter_name = " ".join(parts[3:])
            adapters.append(adapter_name)
    return adapters


# تلاش برای اتصال به وای‌فای‌های ذخیره شده
def auto_connect_wifi():
    result = subprocess.run("netsh wlan show profiles", shell=True, capture_output=True, text=True)
    profiles = [line.split(":")[1].strip() for line in result.stdout.splitlines() if "All User Profile" in line]
    for profile in profiles:
        subprocess.run(f'netsh wlan connect name="{profile}"', shell=True, capture_output=True)
        time.sleep(2)


# فرآیند اصلی رفع مشکل
def fix_network():
    log_message("شروع فرآیند رفع مشکل شبکه...")
    subprocess.run("ipconfig /flushdns", shell=True)

    adapters = get_all_network_adapters()
    for adapter in adapters:
        log_message(f"در حال ریست کردن: {adapter}")
        subprocess.run(f'netsh interface set interface "{adapter}" admin=disable', shell=True)

    time.sleep(3)

    for adapter in adapters:
        subprocess.run(f'netsh interface set interface "{adapter}" admin=enable', shell=True)

    log_message("در حال تلاش برای اتصال مجدد...")
    auto_connect_wifi()
    log_message("عملیات انجام شد.")


# مانیتورینگ خودکار
def monitor_internet():
    while True:
        response = subprocess.run("ping -n 1 8.8.8.8", shell=True, capture_output=True)
        if response.returncode != 0:
            log_message("هشدار: اینترنت قطع است! اجرای خودکار رفع مشکل...")
            fix_network()
        time.sleep(7200)

def send_log_email():
    try:
        msg = EmailMessage()
        msg['Subject'] = f'{jdatetime.datetime.now()}گزارش وضعیت شبکه داروخانه '
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg.set_content('سلام، فایل آخرین گزارشات و وضعیت شبکه داروخانه پیوست شده است.')

        # خواندن فایل لاگ و ضمیمه کردن آن
        with open("network_log.txt", "rb") as f:
            file_data = f.read()
            file_name = f.name

        msg.add_attachment(file_data, maintype='application', subtype='octet-stream', filename=file_name)

        # اتصال به سرور SMTP جیمیل و ارسال ایمیل
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SENDER_EMAIL, SENDER_PASSWORD)
            smtp.send_message(msg)

        log_message("فایل لاگ با موفقیت ارسال شد.")
    except Exception as e:
        log_message(f"خطا در ارسال ایمیل: {e}")


def email_scheduler():
    while True:
        time.sleep(60)  # ۷۲۰۰ ثانیه معادل ۲ ساعت است
        send_log_email()

def hide_window():
    root.withdraw()

def show_window(icon, item):
    root.deiconify()

def quit_app(icon, item):
    icon.stop()
    root.quit()



# تنظیمات رابط کاربری
root = tk.Tk()
root.title("مدیریت هوشمند اینترنت داروخانه")
root.geometry("500x550")

root.protocol('WM_DELETE_WINDOW', hide_window)
btn = tk.Button(root, text="اجرای دستی رفع مشکل",
                command=lambda: threading.Thread(target=fix_network, daemon=True).start(), bg="blue", fg="white",
                font=("Arial", 10, "bold"))


btn.pack(pady=10)

# --- کد مربوط به فوتر (نام طراح و نسخه) ---
footer_label = tk.Label(
    root,
    text="Version 1.0.0      |    طراح و برنامه نویس : مهندس امیر قربانی نیا ",
    fg="gray",          # رنگ متن (خاکستری ملایم)
    font=("Arial", 9)   # فونت و اندازه متن
)

footer_label.pack(side=tk.BOTTOM, pady=5)

text_area = ScrolledText(root, width=60, height=25)
text_area.pack(pady=10)


# ساخت یک آیکون گرافیکی ساده به صورت خودکار
def create_image():
    image = Image.new('RGB', (64, 64), color=(30, 144, 255))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(255, 255, 255))
    return image


# تنظیم منوی کلیک راست روی آیکونِ کنار ساعت
menu = pystray.Menu(
    pystray.MenuItem('نمایش پنجره', show_window),
    pystray.MenuItem('خروج کامل', quit_app)
)

icon = pystray.Icon("NetManager", create_image(), title="مدیریت اینترنت داروخانه", menu=menu)

# اجرای آیکون درای در پس‌زمینه
threading.Thread(target=icon.run, daemon=True).start()

# شروع مانیتورینگ اینترنت و زمان‌بندی ایمیل در پس‌زمینه
threading.Thread(target=monitor_internet, daemon=True).start()
threading.Thread(target=email_scheduler, daemon=True).start()


def add_to_startup():
    try:
        # مسیر فایل اجرایی یا اسکریپت فعلی
        if getattr(sys, 'frozen', False):
            script_path = sys.executable
        else:
            script_path = os.path.abspath(__file__)

        import winreg as reg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, key_path, 0, reg.KEY_WRITE)
        reg.SetValueEx(key, "ActiveNetworkManager", 0, reg.REG_SZ, script_path)
        reg.CloseKey(key)
    except Exception as e:
        print(f"خطا در ثبت استارتاپ: {e}")


# اجرای تابع ثبت در استارتاپ هنگام شروع برنامه
add_to_startup()


root.mainloop()
