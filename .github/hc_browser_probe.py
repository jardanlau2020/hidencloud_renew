# -*- coding: utf-8 -*-
"""探針 v4：查 CF widget 點解唔 render。

依次試：
  A. runner 網絡：challenges.cloudflare.com 連唔連到
  B. window.turnstile 存在？現有 widget 狀態
  C. 試 turnstile.reset() / 重新 render
  D. 試撳 widget 位置（互動式 checkbox）
  E. 換 plain chrome（唔用 uc）再試一次
"""
import os
import re
import subprocess
import time

import cloudscraper
from bs4 import BeautifulSoup

BASE = "https://dash.hidencloud.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def banner(t):
    print(f"\n{'=' * 12} {t} {'=' * 12}", flush=True)


banner("A. runner 網絡：challenges.cloudflare.com")
for url in ["https://challenges.cloudflare.com/turnstile/v0/api.js",
            "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit",
            "https://dash.hidencloud.com/service/230911/login"]:
    p = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{time_total}s",
                        "--max-time", "20", url], capture_output=True, text=True)
    print(f"  {url} -> {p.stdout.strip() or p.stderr.strip()[:80]}")

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

sid = None
for a in BeautifulSoup(sc.get(f"{BASE}/dashboard", timeout=30).text, 'html.parser').find_all('a', href=True):
    m = re.search(r'/service/(\d+)/manage', a['href'])
    if m:
        sid = m.group(1)
        break
print(f"\n服務 ID = {sid}", flush=True)

from seleniumbase import Driver


def run_pass(label, driver_kwargs):
    banner(f"E/{label} 開始")
    driver = None
    try:
        driver = Driver(**driver_kwargs)
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
        print("  標題:", driver.title, flush=True)

        clicked = driver.execute_script("""
            var btns = Array.from(document.querySelectorAll('button,a'));
            var t = btns.find(function(b){ return /^\\s*(Renew|续期|續期)\\s*$/i.test(b.textContent||''); });
            if (!t) return 'no-button'; t.click(); return 'clicked';
        """)
        print("  撳 Renew:", clicked, flush=True)
        time.sleep(3)

        print("\n  B. turnstile 全域狀態:")
        st = driver.execute_script("""
            var w = document.querySelector('.cf-turnstile,[data-sitekey]');
            return {
              has_api: typeof window.turnstile,
              has_render: !!(window.turnstile && window.turnstile.render),
              has_reset: !!(window.turnstile && window.turnstile.reset),
              script_tags: Array.from(document.scripts).filter(function(s){return /turnstile|challenge/i.test(s.src)}).map(function(s){return s.src.slice(0,90)}),
              widget_outer: w ? w.outerHTML.slice(0,400) : null,
              cf_iframe: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length,
              iframe_srcs: Array.from(document.querySelectorAll('iframe')).map(function(f){return (f.src||f.id||'').slice(0,60)}).slice(0,6)
            };
        """)
        for k, v in st.items():
            print(f"    {k} = {v}", flush=True)

        print("\n  C. 試 turnstile.reset() + 顯式 render（最多 60s）")
        driver.execute_script("""
            try { if (window.turnstile && window.turnstile.reset) window.turnstile.reset(); } catch(e){}
        """)
        time.sleep(5)
        print("    reset 後 CF iframe:", driver.execute_script(
            "return document.querySelectorAll('iframe[src*=\"challenges.cloudflare.com\"]').length;"))
        driver.execute_script("""
            try {
              var w = document.querySelector('.cf-turnstile');
              if (window.turnstile && window.turnstile.render && w) {
                window.__hcTok = '';
                window.turnstile.render(w, {sitekey: w.getAttribute('data-sitekey'),
                  callback: function(tk){ window.__hcTok = tk; }});
                return 're-rendered';
              }
            } catch(e) { return 'render-err:' + e.message; }
            return 'no-render';
        """)
        tok = ""
        for i in range(60):
            s = driver.execute_script("""
                var e=document.querySelector('input[name="cf-turnstile-response"]');
                return {tok: e ? (e.value||'') : (window.__hcTok||''),
                        cf: document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]').length};
            """)
            if (s["tok"] or "").strip():
                tok = s["tok"]
                print(f"    ✅ [{i}s] 攞到 token 長度 {len(tok)}", flush=True)
                break
            if i % 10 == 0:
                print(f"    [{i}s] CF iframe {s['cf']}", flush=True)
            time.sleep(1)

        if not tok.strip():
            print("  D. 試撳 widget 位置（互動式）", flush=True)
            for sel in ['.cf-turnstile', 'input[name="cf-turnstile-response"]']:
                els = driver.find_elements("css selector", sel)
                if els:
                    try:
                        driver.execute_script("arguments[0].click();", els[0])
                    except Exception as e:
                        print("    click 失敗:", e)
                    time.sleep(8)
                    t2 = driver.execute_script(
                        "var e=document.querySelector('input[name=\"cf-turnstile-response\"]');"
                        "return e ? (e.value||'').length : 0;")
                    n_iframe = driver.execute_script("return document.querySelectorAll('iframe[src*=challenges]').length;")
                    print(f"    撳 {sel} 後 token 長度 {t2} | CF iframe {n_iframe}")
            try:
                inner = driver.execute_script(
                    "var m=document.querySelector('.modal.show,[role=dialog]');"
                    "return m ? m.innerText.slice(0,400) : 'no-modal';")
                print("    modal 文字:", inner, flush=True)
            except Exception:
                pass
            driver.save_screenshot(f"hc_v4_{label}_fail.png")
            print(f"  ❌ [{label}] 過唔到 Turnstile", flush=True)
            return False

        banner(f"E/{label} 提交續期")
        driver.execute_script(
            "var f=document.querySelector('form[id^=\"renew-form-\"]');"
            "f.requestSubmit ? f.requestSubmit() : f.submit();")
        time.sleep(10)
        print("  提交後 URL:", driver.current_url, flush=True)
        body = driver.find_element("tag name", "body").text
        lines = [x.strip() for x in body.split("\n") if x.strip()]
        print("  頁面重點:")
        for l in lines[:35]:
            print("    ·", l[:110])
        print("  命中:", [l[:130] for l in lines
                    if re.search(r'error|錯誤|失败|required|captcha|invoice|账单|expire|到期|renew', l, re.I)][:12])
        driver.save_screenshot(f"hc_v4_{label}_after.png")
        return True
    except Exception as e:
        print(f"  ❌ [{label}] 例外: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass


ok = run_pass("uc-headless", dict(uc=True, headless=True, undetectable=True))
if not ok:
    run_pass("plain-headless", dict(uc=False, headless2=True))
banner("完")