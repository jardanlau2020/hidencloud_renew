#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hc_probe_login.py —— HidenCloud 帳密登入路徑「一次性只讀探針」

背景：2026-10-07 run 37599106127（hidencloud_renew）cookie 死 → 退帳密登入 →
      腳本報「❌ 帳密登入失敗（帳密錯／被封）」，但截圖顯示登入頁 Turnstile 仍
      喺「Verifying…」、Email 欄空白、且冇任何服務端錯誤 banner。
      → 「帳密錯」呢個結論未經證實，要用證據分清三個假設：

  H1  Turnstile 假通過：前端冇真 token，站方以 captcha 為由拒絕提交
  H2  填值冇生效：fill() 冇寫入實際 DOM/框架 state → 提交空表單
  H3  帳密真係錯：服務端會回 “These credentials do not match our records.”

收集嘅證據（全部只記「有無／長度」，唔記任何憑證值）：
  1. 表單內所有 input/textarea/button 嘅 tag/type/name/id/placeholder/可見性/值長度
  2. `cf-turnstile-response` token 有無、長度
  3. 填值後「回讀」值長度是否等於預期（驗 H2）
  4. 提交時有無真嘅 POST /auth/login（網絡層監聽）＋ POST body 內有無 token（驗 H1）
  5. POST 回應 status / location
  6. 提交後 URL / title / .alert 文字（驗 H3）

安全：
  - **只讀**：只去登入頁同 /dashboard，唔撳 Renew、唔提交任何續期表單
  - 永不列印 email／password／token／cookie 值，只列長度
  - 報告寫入 probe_report.json + 截圖，交 workflow artifact

執行：xvfb-run python -u hc_probe_login.py
"""

import json
import os
import sys
import time
import traceback
from urllib.parse import parse_qs

from hc_browser import (BASE_URL, STEALTH_JS, _login_form_visible, _sync_playwright,
                        log, page_ready, solve_turnstile, turnstile_state)

EMAIL = os.environ.get("HC_EMAIL", "") or ""
PASSWORD = os.environ.get("HC_PASSWORD", "") or ""
COOKIE = os.environ.get("HIDEN_COOKIE", "") or ""
# direct     = 乾淨 context 直入登入頁（v1 已做，成功）
# cookie_first = 先注入死 cookie → /dashboard 被彈 → 再做帳密登入（複製生產 run 情境）
PROBE_MODE = os.environ.get("HC_PROBE_MODE", "direct")

REPORT_PATH = "probe_report.json"
SHOT_PREFIX = "probe"

report = {
    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "base_url": BASE_URL,
    "email_len": len(EMAIL),
    "password_len": len(PASSWORD),
    "creds_present": bool(EMAIL and PASSWORD),
    "steps": [],
    "net_post": [],
    "net_post_resp": [],
    "shots": [],
}


def note(step, **kw):
    entry = {"step": step, "at": time.strftime("%H:%M:%S")}
    entry.update(kw)
    report["steps"].append(entry)
    log(f"[probe] {step}: " + ", ".join(f"{k}={v}" for k, v in kw.items()))


def shot(page, name):
    path = f"{SHOT_PREFIX}_{name}.png"
    try:
        page.screenshot(path=path)
        report["shots"].append(path)
        log(f"📸 截圖: {path}")
    except Exception as e:
        log(f"⚠️ 截圖失敗 {name}: {e}")


# 只回報「有無值／長度／種類」，永不回報值本身
_FORM_DUMP_JS = """
() => {
  const out = [];
  document.querySelectorAll('input, textarea, button').forEach(el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const v = el.value || '';
    out.push({
      tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      name: el.name || '',
      id: el.id || '',
      placeholder: el.placeholder || '',
      visible: !!(r.width > 0 && r.height > 0 &&
                  cs.visibility !== 'hidden' && cs.display !== 'none'),
      value_len: v.length,
      value_kind: v.length === 0 ? 'empty'
                  : (/^[^@\\s]+@[^@\\s]+$/.test(v) ? 'email-like' : 'opaque'),
      disabled: !!el.disabled,
      rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    });
  });
  return out;
}
"""

_ALERT_JS = """
() => {
  const sels = ['.alert', '[role="alert"]', '.invalid-feedback', '.error', '.text-danger'];
  const out = [];
  sels.forEach(s => document.querySelectorAll(s).forEach(el => {
    const t = (el.innerText || el.textContent || '').trim();
    if (t) out.push({sel: s, text: t.slice(0, 300)});
  }));
  return out;
}
"""

_TITLE_URL_JS = """
() => ({url: location.href, title: document.title,
        has_login_form: !!document.querySelector('input[type="password"]')})
"""


def token_len_in_body(body: str) -> int:
    """由 form-encoded POST body 抽 cf-turnstile-response 值嘅長度（唔回報值）"""
    try:
        qs = parse_qs(body, keep_blank_values=True)
        for k, vals in qs.items():
            if "turnstile" in k.lower():
                return len(vals[0]) if vals else 0
    except Exception:
        pass
    return 0


def parse_cookie_env(raw: str):
    """'a=1;b=2' → playwright cookie dicts（只回報數量／名，唔回報值）"""
    out = []
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        out.append({"name": name, "value": value, "url": BASE_URL})
    return out


def main() -> int:
    log("=" * 60)
    log("HidenCloud 帳密登入探針（只讀；唔會續期）")
    log(f"模式: {PROBE_MODE}")
    log("=" * 60)
    note("creds", email_len=len(EMAIL), password_len=len(PASSWORD),
         creds_present=bool(EMAIL and PASSWORD), mode=PROBE_MODE,
         cookie_env_len=len(COOKIE))
    if not (EMAIL and PASSWORD):
        log("❌ 冇帳密，探針無意義")
        return 0

    sync_playwright = _sync_playwright()
    with sync_playwright() as p:
        browser = None
        try:
            log("🚀 啟動瀏覽器（真 Chrome + xvfb）...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                      '--disable-infobars', '--window-size=1920,1080'],
            )
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            # ---- 網絡層監聽：證明有無真 POST ----
            def on_request(req):
                try:
                    if req.method.upper() != "POST":
                        return
                    body = ""
                    try:
                        body = req.post_data or ""
                    except Exception:
                        pass
                    ck_names = []
                    try:
                        ck_hdr = (req.headers or {}).get("cookie", "") or ""
                        ck_names = [p.split("=")[0].strip()
                                    for p in ck_hdr.split(";") if "=" in p]
                    except Exception:
                        pass
                    report["net_post"].append({
                        "url": req.url,
                        "body_len": len(body),
                        "has_turnstile_field": "turnstile" in body.lower(),
                        "turnstile_value_len": token_len_in_body(body),
                        "has_password_field": "password" in body.lower(),
                        "has_email_field": ("email" in body.lower() or "username" in body.lower()),
                        "cookie_names": ck_names,
                        "at": time.strftime("%H:%M:%S"),
                    })
                    log(f"🌐 POST → {req.url} | body_len={len(body)} "
                        f"| token_field={'turnstile' in body.lower()} "
                        f"| token_len={token_len_in_body(body)} "
                        f"| cookies={','.join(ck_names)}")
                except Exception:
                    pass

            def on_response(resp):
                try:
                    if resp.request.method.upper() != "POST":
                        return
                    report["net_post_resp"].append({
                        "url": resp.url,
                        "status": resp.status,
                        "location": resp.headers.get("location", ""),
                        "at": time.strftime("%H:%M:%S"),
                    })
                    log(f"🌐 POST ← {resp.status} {resp.url} "
                        f"loc={resp.headers.get('location','')}")
                except Exception:
                    pass

            page.on("request", on_request)
            page.on("response", on_response)

            # ---- 0. cookie_first 模式：先複製生產 run 嘅前置狀態 ----
            if PROBE_MODE == "cookie_first":
                cks = parse_cookie_env(COOKIE)
                note("cookie_inject", n=len(cks), names=[c["name"] for c in cks])
                try:
                    if cks:
                        context.add_cookies(cks)
                    page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded",
                              timeout=60000)
                    time.sleep(8)
                    info0 = page.evaluate(_TITLE_URL_JS)
                    note("after_cookie_dashboard", url=info0["url"], title=info0["title"],
                         bounced_to_login=("/auth/login" in info0["url"]))
                    shot(page, "00_cookie_first")
                except Exception as e:
                    note("cookie_inject", err=str(e)[:200])

            # ---- 1. 去登入頁 ----
            page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            info = page.evaluate(_TITLE_URL_JS)
            note("open_login", url=info["url"], title=info["title"])
            shot(page, "01_login_page")

            # ---- 2. 第一道 Turnstile（登入頁本身嗰道）----
            ok1 = solve_turnstile(page, timeout=180, success_check=_login_form_visible,
                                  reload_after=8, shot_on_timeout="probe_ts1_timeout.png")
            note("turnstile_1", passed=bool(ok1), state=turnstile_state(page))
            shot(page, "02_after_ts1")

            # ---- 3. 填值前 dump 表單 ----
            pre = page.evaluate(_FORM_DUMP_JS)
            report["form_before_fill"] = pre
            note("form_before_fill", n_inputs=len(pre),
                 password_visible=any(i["type"] == "password" and i["visible"] for i in pre))
            shot(page, "03_form_before_fill")

            # ---- 4. 填帳密（照 login_with_password 同款選擇器）----
            email_sel = ('input[name="username"], input#username, input[name="email"], '
                         'input[type="email"], input[name="EMAIL"]')
            pwd_sel = ('input[name="password"], input#password, '
                       'input[name="PASSWORD"], input[type="password"]')
            try:
                ei = page.locator(email_sel).first
                pi = page.locator(pwd_sel).first
                ei.wait_for(state="visible", timeout=60000)
                ei.click()
                ei.fill(EMAIL)
                time.sleep(1.2)
                pi.click()
                pi.fill(PASSWORD)
                time.sleep(1.0)
                note("fill", ok=True)
            except Exception as e:
                note("fill", ok=False, err=str(e)[:200])

            # ---- 5. 填值後回讀（驗 H2）----
            post_fill = page.evaluate(_FORM_DUMP_JS)
            report["form_after_fill"] = post_fill
            try:
                ev = page.evaluate(
                    """(sel) => { const el = document.querySelector(sel);
                                  return el ? (el.value || '').length : -1; }""",
                    'input[name="username"], input#username, input[name="email"], '
                    'input[type="email"], input[name="EMAIL"]')
                pv = page.evaluate(
                    """(sel) => { const el = document.querySelector(sel);
                                  return el ? (el.value || '').length : -1; }""",
                    'input[name="password"], input#password, '
                    'input[name="PASSWORD"], input[type="password"]')
                note("readback", email_len_in_dom=ev, expect_email_len=len(EMAIL),
                     email_ok=(ev == len(EMAIL)), pwd_len_in_dom=pv,
                     expect_pwd_len=len(PASSWORD), pwd_ok=(pv == len(PASSWORD)))
            except Exception as e:
                note("readback", err=str(e)[:200])
            shot(page, "04_after_fill")

            # ---- 6. 等 + 第二道 Turnstile ----
            time.sleep(8)
            ok2 = solve_turnstile(page, timeout=90, require_positive=True,
                                  shot_on_timeout="probe_ts2_timeout.png")
            st = turnstile_state(page)
            note("turnstile_2", passed=bool(ok2), state=st)
            shot(page, "05_before_submit")

            # 提交前最後狀態（H1 關鍵：token 有無）
            before_submit = page.evaluate(_FORM_DUMP_JS)
            report["form_before_submit"] = before_submit
            try:
                ck_now = context.cookies()
                report["context_cookies_before_submit"] = [c["name"] for c in ck_now]
                note("context_cookies_before_submit", n=len(ck_now),
                     names=[c["name"] for c in ck_now])
            except Exception as e:
                note("context_cookies_before_submit", err=str(e)[:120])
            note("before_submit", ts_state=st,
                 alert=page.evaluate(_ALERT_JS)[:3])

            # ---- 7. 提交（只登入，唔涉及續期）----
            n_post_before = len(report["net_post"])
            try:
                submit = page.locator('button[type="submit"], button:has-text("Login"), '
                                      'button:has-text("Sign in"), button:has-text("登录")').first
                submit.click(timeout=15000)
                note("submit_click", ok=True)
            except Exception as e:
                note("submit_click", ok=False, err=str(e)[:200])

            time.sleep(6)
            posted = len(report["net_post"]) > n_post_before
            note("post_observed", posted=posted,
                 n_posts=len(report["net_post"]) - n_post_before)
            shot(page, "06_after_submit")

            # 若撳掣冇 POST，試 Enter 再觀察一次
            if not posted:
                try:
                    page.locator('input[name="password"], input#password, '
                                 'input[type="password"]').first.press("Enter")
                    note("submit_enter", ok=True)
                except Exception as e:
                    note("submit_enter", ok=False, err=str(e)[:200])
                time.sleep(6)
                note("post_observed_after_enter",
                     posted=len(report["net_post"]) > n_post_before,
                     n_posts=len(report["net_post"]) - n_post_before)
                shot(page, "07_after_enter")

            # ---- 8. 提交後狀態（驗 H3）----
            time.sleep(4)
            after = page.evaluate(_TITLE_URL_JS)
            alerts = page.evaluate(_ALERT_JS)
            report["after_submit"] = {"page": after, "alerts": alerts}
            note("after_submit", url=after["url"], title=after["title"],
                 still_login_form=after["has_login_form"],
                 n_alerts=len(alerts))
            for a in alerts[:5]:
                log(f"   ⚠️ alert[{a['sel']}]: {a['text']}")
            shot(page, "08_final")

            # ---- 9. 裁決（睇硬證據，唔靠單一 alert 猜）----
            posts = report["net_post"]
            real_post = [x for x in posts if "/auth/login" in x["url"]]
            token_seen = any(x["turnstile_value_len"] > 20 for x in posts)
            landed_dashboard = "/auth/login" not in (after["url"] or "")
            alert_txt = " ".join(a["text"].lower() for a in alerts)
            success_hint = any(k in alert_txt for k in
                               ("welcome back", "success", "欢迎", "成功"))
            verdict = []
            if not real_post:
                verdict.append("H1/H2：完全冇 POST 去 /auth/login → 表單未提交")
            else:
                verdict.append(f"有真 POST 去 /auth/login（body_len={real_post[-1]['body_len']}）")
                verdict.append("POST 帶咗 Turnstile token（len=%d）→ H1 否決"
                               % real_post[-1]["turnstile_value_len"]
                               if token_seen else "POST 冇有效 token → H1 成立")
            if landed_dashboard:
                verdict.append("✅ 最終 URL 係 dashboard → 登入成功")
            elif success_hint:
                verdict.append("✅ 服務端回成功字句但頁面未跳轉（可能導航時序）")
            else:
                verdict.append("❌ 最終仍喺 /auth/login，且無成功字句 → 登入被拒")
                if alerts:
                    verdict.append("服務端錯誤文字："
                                   + " | ".join(a["text"][:120] for a in alerts[:3]))
                else:
                    verdict.append("服務端冇回任何錯誤文字 → 唔似帳密錯，似 CSRF/session 問題")
            report["verdict"] = verdict
            for v in verdict:
                log(f"🧾 {v}")

        except Exception as e:
            log(f"❌ 探針異常: {e}")
            log(traceback.format_exc()[:2000])
            report["error"] = f"{e}"
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"📄 報告已寫入 {REPORT_PATH}")
    log("（探針只讀，未對站方做任何寫操作）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
