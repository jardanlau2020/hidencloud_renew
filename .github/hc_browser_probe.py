# -*- coding: utf-8 -*-
"""一次性診斷：HidenCloud 續期被 `cf-turnstile-response field is required` 拒絕。

做三件事，全部只讀／試探，唔發通知：
  1. 用 cloudscraper 讀服務狀態（剩幾多日、續期掣 onclick、面板 JS 點解要 Turnstile）
  2. 讀 /service/<id>/renew 頁，睇表單字段同有冇非瀏覽器路徑
  3. 用真 headless Chrome 注入 cookie，開管理頁撳續期掣，睇 Turnstile 自動過唔過、modal 結構係點
輸出只印診斷資料，token 之類敏感值一律 redact。
"""
import os
import re
import sys
import time

import cloudscraper
import requests
from bs4 import BeautifulSoup

BASE = "https://dash.hidencloud.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def redact(text):
    text = re.sub(r'(name="_token"[^>]*value=")[^"]+', r'\1***', text)
    text = re.sub(r'(content=")[A-Za-z0-9]{40,}(")', r'\1***\2', text)
    text = re.sub(r'(value=")[A-Za-z0-9]{60,}(")', r'\1***\2', text)
    return text


def banner(t):
    print(f"\n{'=' * 12} {t} {'=' * 12}", flush=True)


# ---------------- 1. HTTP 側診斷 ----------------
raw = os.environ.get("HIDEN_COOKIE", "")
groups = [c.strip() for c in re.split(r'[&\n]', raw) if c.strip()]
banner(f"cookie 組數 = {len(groups)}")

sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
sc.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

for part in groups[0].split(";"):
    if "=" in part:
        k, v = part.strip().split("=", 1)
        dom = ".hidencloud.com" if k in ("cf_clearance", "hc_cf_turnstile") else ".dash.hidencloud.com"
        sc.cookies.set(k.strip(), v.strip(), domain=dom, path="/")

dash = sc.get(f"{BASE}/dashboard", timeout=30)
print(f"GET /dashboard -> {dash.status_code} | 最終 URL {dash.url}")
sid = None
for a in BeautifulSoup(dash.text, 'html.parser').find_all('a', href=True):
    m = re.search(r'/service/(\d+)/manage', a['href'])
    if m:
        sid = m.group(1)
        break
print(f"服務 ID = {sid}")
if not sid:
    print("❌ 搵唔到服務，cookie 可能已失效")
    sys.exit(1)

manage = sc.get(f"{BASE}/service/{sid}/manage", timeout=30)
msoup = BeautifulSoup(manage.text, 'html.parser')
btn = msoup.find('button', onclick=re.compile(r'showRenewAlert'))
print(f"GET /service/{sid}/manage -> {manage.status_code}")
print(f"續期掣 onclick = {btn.get('onclick') if btn else '❌ 冇搵到'}")

banner("到期／剩餘日數（頁面文字線索）")
for pat in [r'剩余[^<]{0,30}', r'剩餘[^<]{0,30}', r'\d+\s*(?:天|days?)[^<]{0,20}',
            r'(?:到期|过期|expire)[^<]{0,40}']:
    hits = re.findall(pat, manage.text, re.I)
    if hits:
        print("  ", sorted(set(h.strip() for h in hits))[:6])

banner("showRenewAlert 函數本體")
m = re.search(r'function\s+showRenewAlert\s*\([^)]*\)\s*\{.*?\n\s*\}', manage.text, re.S)
print(redact(m.group(0))[:3500] if m else "❌ 冇搵到函數本體（可能喺外鏈 js）")

banner("管理頁 Turnstile 線索")
print("  sitekeys:", sorted(set(re.findall(
    r'(?:sitekey|data-sitekey)\s*[:=]\s*["\']([0-9A-Za-z_\-]{10,})', manage.text))))
print("  cf-turnstile 出現次數:", manage.text.count('cf-turnstile'))
for s in re.findall(r'<script[^>]*src="([^"]+)"', manage.text):
    if 'turnstile' in s.lower() or 'challenge' in s.lower():
        print("  外鏈:", s)

# ---------------- 2. renew 頁直抓 ----------------
banner(f"GET /service/{sid}/renew（純 HTTP 直抓）")
rn = sc.get(f"{BASE}/service/{sid}/renew", timeout=30, allow_redirects=True)
print(f"  -> {rn.status_code} | 最終 URL {rn.url}")
print("  hidden inputs:", redact(" ".join(
    re.findall(r'<input[^>]*type="hidden"[^>]*>', rn.text)))[:900])
print("  有 cf-turnstile:", 'cf-turnstile' in rn.text)
forms = re.findall(r'<form[^>]*>', rn.text)
print("  forms:", redact(" ".join(forms))[:500])

# ---------------- 3. 真瀏覽器試 Turnstile ----------------
banner("headless Chrome 試過 Turnstile（只觀察，唔撳最終確認）")
try:
    from seleniumbase import Driver
except Exception as e:
    print(f"❌ 匯入 seleniumbase 失敗: {e}")
    sys.exit(0)

cookie_records = []
for part in groups[0].split(";"):
    if "=" in part:
        k, v = part.strip().split("=", 1)
        dom = ".hidencloud.com" if k in ("cf_clearance", "hc_cf_turnstile") else ".dash.hidencloud.com"
        cookie_records.append({"name": k.strip(), "value": v.strip(), "domain": dom,
                               "path": "/", "secure": True})
print(f"  準備注入 {len(cookie_records)} 個 cookie（只印名）:",
      [c['name'] for c in cookie_records])

driver = None
try:
    driver = Driver(uc=True, headless=True, undetectable=True)
    driver.set_window_size(1400, 1000)
    driver.get(f"{BASE}/login")
    time.sleep(2)
    for c in cookie_records:
        try:
            driver.execute_cdp_cmd("Network.setCookie", c)
        except Exception as e:
            print(f"  ⚠️ setCookie {c['name']} 失敗: {e}")
    driver.get(f"{BASE}/service/{sid}/manage")
    time.sleep(4)
    print("  開頁後 URL:", driver.current_url)
    print("  標題:", driver.title)

    els = driver.find_elements("css selector", 'button[onclick*="showRenewAlert"]')
    print(f"  搵到續期掣: {len(els)} 個")
    if not els:
        print("  ❌ 頁面冇續期掣（可能 session 未注入成功）")
        sys.exit(0)
    driver.execute_script("arguments[0].click();", els[0])
    time.sleep(3)

    banner("modal 結構（按鈕／輸入／iframe）")
    for el in driver.find_elements("css selector", ".modal.show button, .modal.show input, .modal.show iframe, .modal.show div[class*=turnstile]"):
        try:
            print("  ", el.tag_name, "| id=", el.get_attribute("id"),
                  "| class=", (el.get_attribute("class") or "")[:60],
                  "| type=", el.get_attribute("type"),
                  "| name=", el.get_attribute("name"),
                  "| text=", (el.text or "")[:24].replace("\n", " "),
                  "| src=", (el.get_attribute("src") or "")[:70])
        except Exception:
            pass

    print("\n  等 Turnstile 自動 token（最多 45s）…")
    got = ""
    for i in range(45):
        try:
            got = driver.execute_script(
                "var e=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                "return e ? e.value : '';") or ""
        except Exception as e:
            print(f"  [{i}] execute_script 失敗: {e}")
        if got.strip():
            print(f"  ✅ [{i}s] 攞到 token，長度 {len(got)}")
            break
        iframes = driver.find_elements("css selector", 'iframe[src*="challenges.cloudflare.com"]')
        if i % 5 == 0:
            print(f"  [{i}s] token 仲未有；CF iframe {len(iframes)} 個")
        time.sleep(1)
    if not got.strip():
        body = driver.find_element("tag name", "body").text
        print("  ❌ 45s 都攞唔到 token")
        print("  modal 文字:", " / ".join(x.strip() for x in body.split("\n") if x.strip())[:600])
    driver.save_screenshot("hc_probe.png", name="hc_probe")
except Exception as e:
    print(f"❌ 瀏覽器步驟例外: {type(e).__name__}: {e}")
finally:
    try:
        if driver:
            driver.quit()
    except Exception:
        pass

banner("診斷完")