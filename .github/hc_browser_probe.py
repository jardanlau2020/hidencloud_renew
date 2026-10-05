# -*- coding: utf-8 -*-
"""探針 v2：睇 renew form 結構 + 真瀏覽器自動過 Turnstile 並提交續期。

只診斷／試跑，唔發通知。輸出 token 一律 redact。
"""
import os
import re
import sys
import time

import cloudscraper
from bs4 import BeautifulSoup

BASE = "https://dash.hidencloud.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def redact(t):
    t = re.sub(r'(value=")[A-Za-z0-9]{40,}(")', r'\1***\2', t)
    t = re.sub(r'(_token[^>]{0,20}value=")[^"]+', r'\1***', t)
    return t


def banner(t):
    print(f"\n{'=' * 12} {t} {'=' * 12}", flush=True)


raw = os.environ.get("HIDEN_COOKIE", "")
groups = [c.strip() for c in re.split(r'[&\n]', raw) if c.strip()]
sc = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
sc.headers.update({"User-Agent": UA})
records = []
for part in groups[0].split(";"):
    if "=" in part:
        k, v = part.strip().split("=", 1)
        dom = ".hidencloud.com" if k in ("cf_clearance", "hc_cf_turnstile") else ".dash.hidencloud.com"
        sc.cookies.set(k.strip(), v.strip(), domain=dom, path="/")
        records.append({"name": k.strip(), "value": v.strip(), "domain": dom,
                        "path": "/", "secure": True})

dash = sc.get(f"{BASE}/dashboard", timeout=30)
sid = None
for a in BeautifulSoup(dash.text, 'html.parser').find_all('a', href=True):
    m = re.search(r'/service/(\d+)/manage', a['href'])
    if m:
        sid = m.group(1)
        break
print(f"服務 ID = {sid}")
manage = sc.get(f"{BASE}/service/{sid}/manage", timeout=30).text

banner("renew form 結構（redact）")
fm = re.search(r'<form[^>]*id="renew-form-\d+".*?</form>', manage, re.S)
print(redact(fm.group(0))[:2500] if fm else "❌ HTML 冇 renew-form")

banner("hidenRenewCaptchaGuard 函數")
g = re.search(r'function\s+hidenRenewCaptchaGuard\s*\(.*?\n\s*\}', manage, re.S)
print(redact(g.group(0))[:2000] if g else "❌ 冇搵到（可能喺外鏈 js）")

banner("頁面所有掣／連結文字（搵續期入口）")
soup = BeautifulSoup(manage, 'html.parser')
for el in soup.find_all(['button', 'a']):
    txt = el.get_text(" ", strip=True)[:30]
    oc = (el.get('onclick') or '')[:70]
    href = (el.get('href') or '')[:50]
    if txt and re.search(r'续期|續期|renew|Renew|延长|恢復|restore', txt + oc + href, re.I):
        print(f"  <{el.name}> text={txt!r} onclick={oc!r} href={href!r}")

banner("headless Chrome：自動過 Turnstile → 提交 renew form")
from seleniumbase import Driver

driver = None
try:
    driver = Driver(uc=True, headless=True, undetectable=True)
    driver.set_window_size(1400, 1100)
    driver.get(f"{BASE}/login")
    time.sleep(2)
    for c in records:
        try:
            driver.execute_cdp_cmd("Network.setCookie", c)
        except Exception as e:
            print(f"  ⚠️ setCookie {c['name']}: {e}")
    driver.get(f"{BASE}/service/{sid}/manage")
    time.sleep(5)
    print("  URL:", driver.current_url, "| 標題:", driver.title)

    info = driver.execute_script("""
        var f = document.querySelector('form[id^="renew-form-"]');
        if (!f) return {form:false};
        var inputs = [];
        f.querySelectorAll('input,select,textarea').forEach(function(e){
            inputs.push({name:e.name, type:e.type, value:(e.type==='hidden'&&e.name==='_token')?'***':(e.value||'').slice(0,20)});
        });
        var btn = f.querySelector('button[type=submit],button:not([type])');
        return {form:true, action:f.action, method:f.method, onsubmit:(f.getAttribute('onsubmit')||'').slice(0,120),
                inputs:inputs, btn: btn ? (btn.textContent||'').trim().slice(0,30) : null,
                turnstile_in_form: !!f.querySelector('.cf-turnstile, [data-sitekey]')};
    """)
    print("  form 狀態:", info)
    if not info.get("form"):
        print("  ❌ 瀏覽器 DOM 冇 renew-form")
        sys.exit(0)

    print("\n  等 Turnstile token（最多 60s）…")
    tok = ""
    for i in range(60):
        tok = driver.execute_script(
            "var e=document.querySelector('input[name=\"cf-turnstile-response\"]');"
            "return e ? (e.value||'') : '';") or ""
        if tok.strip():
            print(f"  ✅ [{i}s] 自動過到 token，長度 {len(tok)}")
            break
        if i % 10 == 0:
            n = len(driver.find_elements("css selector", 'iframe[src*="challenges.cloudflare.com"]'))
            vis = driver.execute_script(
                "var e=document.querySelector('.cf-turnstile,[data-sitekey]');"
                "return e ? e.innerText.slice(0,120) : 'no-widget';")
            print(f"  [{i}s] 冇 token | CF iframe {n} | widget 文字={vis!r}")
        time.sleep(1)

    if not tok.strip():
        print("  ❌ 60s 過唔到 Turnstile → 只能人手")
        driver.save_screenshot("hc_turnstile_fail.png")
        sys.exit(0)

    banner("提交 renew form（瀏覽器原生 requestSubmit，會行 onsubmit guard）")
    driver.execute_script("arguments[0].requestSubmit ? arguments[0].requestSubmit() : arguments[0].submit();",
                          driver.find_element("css selector", 'form[id^="renew-form-"]'))
    time.sleep(8)
    print("  提交後 URL:", driver.current_url)
    body = driver.find_element("tag name", "body").text
    lines = [x.strip() for x in body.split("\n") if x.strip()]
    print("  頁面重點文字:")
    for l in lines[:40]:
        print("    ·", l[:110])
    for kw in ["success", "成功", "renew", "续期", "續期", "invoice", "账单", "expire"]:
        if re.search(kw, body, re.I):
            print(f"  命中關鍵字: {kw}")
    driver.save_screenshot("hc_renew_after.png")
except Exception as e:
    print(f"❌ 例外: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
finally:
    try:
        if driver:
            driver.quit()
    except Exception:
        pass