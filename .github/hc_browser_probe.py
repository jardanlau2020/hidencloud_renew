# -*- coding: utf-8 -*-
"""探針 v3：撳「Renew」打開 modal → 等 Turnstile 渲染並自動過 → 提交 renew form。"""
import os
import re
import sys
import time

import cloudscraper
from bs4 import BeautifulSoup

BASE = "https://dash.hidencloud.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


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
print(f"服務 ID = {sid}", flush=True)

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
    print("  URL:", driver.current_url, "| 標題:", driver.title, flush=True)

    banner("撳 Renew 掣打開 modal")
    clicked = driver.execute_script("""
        var btns = Array.from(document.querySelectorAll('button,a'));
        var t = btns.find(function(b){ return /^\\s*(Renew|续期|續期)\\s*$/i.test(b.textContent||''); });
        if (!t) return 'no-button';
        t.scrollIntoView({block:'center'});
        t.click();
        return 'clicked:' + t.tagName + '|' + (t.className||'').slice(0,50);
    """)
    print("  點擊結果:", clicked, flush=True)
    time.sleep(4)

    state = driver.execute_script("""
        var f = document.querySelector('form[id^="renew-form-"]');
        var vis = f ? !!(f.offsetWidth||f.offsetHeight||f.getClientRects().length) : false;
        var modal = document.querySelector('.modal.show, [role=dialog], .modal[style*="block"]');
        var w = document.querySelector('.cf-turnstile,[data-sitekey]');
        return {form:!!f, form_visible:vis,
                modal: modal ? (modal.id||modal.className).slice(0,60) : null,
                widget_in_view: w ? !!(w.offsetWidth||w.offsetHeight) : false,
                widget_html: w ? w.outerHTML.slice(0,300) : null,
                iframes: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length,
                iframe_all: document.querySelectorAll('iframe').length};
    """)
    print("  modal 狀態:", state, flush=True)

    banner("等 Turnstile 渲染＋自動過（最多 90s）")
    tok = ""
    for i in range(90):
        st = driver.execute_script("""
            var e=document.querySelector('input[name="cf-turnstile-response"]');
            return {tok: e ? (e.value||'') : '',
                    iframes: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length,
                    iframes_all: document.querySelectorAll('iframe').length};
        """)
        if st["tok"].strip():
            tok = st["tok"]
            print(f"  ✅ [{i}s] 自動過到 Turnstile，token 長度 {len(tok)}", flush=True)
            break
        if i % 10 == 0:
            print(f"  [{i}s] token 冇 | CF iframe {st['iframes']} | 全部 iframe {st['iframes_all']}", flush=True)
        time.sleep(1)

    if not tok.strip():
        print("  ❌ 撳 modal 之後都過唔到 Turnstile", flush=True)
        # 影相＋dump modal 文字
        try:
            txt = driver.execute_script("""
                var m=document.querySelector('.modal.show,[role=dialog]');
                return m ? m.innerText.slice(0,800) : 'no-modal';
            """)
            print("  modal 文字:", txt, flush=True)
        except Exception as e:
            print("  dump 失敗:", e)
        driver.save_screenshot("hc_v3_turnstile_fail.png")
        sys.exit(0)

    banner("提交 renew form")
    driver.execute_script(
        "var f=document.querySelector('form[id^=\"renew-form-\"]');"
        "f.requestSubmit ? f.requestSubmit() : f.submit();")
    time.sleep(10)
    print("  提交後 URL:", driver.current_url, flush=True)
    body = driver.find_element("tag name", "body").text
    lines = [x.strip() for x in body.split("\n") if x.strip()]
    print("  頁面文字（頭 45 行）:")
    for l in lines[:45]:
        print("    ·", l[:110])
    print("\n  錯誤提示:", [l[:120] for l in lines if re.search(r'error|錯誤|失败|required|captcha', l, re.I)][:8])
    print("  續期線索:", [l[:120] for l in lines if re.search(r'renew|续期|續期|expire|到期|invoice|账单', l, re.I)][:10])
    driver.save_screenshot("hc_v3_after_submit.png")
except Exception as e:
    print(f"❌ 例外: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
finally:
    try:
        if driver:
            driver.quit()
    except Exception:
        pass