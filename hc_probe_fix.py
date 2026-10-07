#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hc_probe_fix.py —— 驗收「死 cookie 污染登入 POST」嘅修正（只讀，唔續期）

情境：同生產 run 37599106127 一樣 —— 先注入死 cookie → /dashboard 被彈 →
      退帳密登入。修法＝`login_with_password` 開頭清走舊應用層 session cookie
      （hidencloud_session / XSRF-TOKEN / remember_web_*），保留 CF 憑證。

驗收：直接叫 `hc_browser.ensure_session()`（生產同一條路徑），
      期望 cookie 死 → 清 → 帳密登入 → 成功入 /dashboard。
      再讀一次服務清單，證明 10-12 續期窗口開嗰日走得通。

只讀：唔撳 Renew、唔提交任何續期表單。
"""

import json
import os
import sys
import time

from hc_browser import (BASE_URL, STEALTH_JS, _sync_playwright,
                        discover_service_ids, ensure_session, log)

EMAIL = os.environ.get("HC_EMAIL", "") or ""
PASSWORD = os.environ.get("HC_PASSWORD", "") or ""
COOKIE = os.environ.get("HIDEN_COOKIE", "") or ""

SHOT_PREFIX = "fix"
report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": [], "shots": []}


def note(step, **kw):
    e = {"step": step, "at": time.strftime("%H:%M:%S")}
    e.update(kw)
    report["steps"].append(e)
    log("[fix] " + step + ": " + ", ".join(f"{k}={v}" for k, v in kw.items()))


def shot(page, name):
    p = f"{SHOT_PREFIX}_{name}.png"
    try:
        page.screenshot(path=p)
        report["shots"].append(p)
        log(f"📸 截圖: {p}")
    except Exception as e:
        log(f"⚠️ 截圖失敗: {e}")


def parse_cookie_env(raw):
    out = []
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        out.append({"name": name.strip(), "value": value.strip(), "url": BASE_URL})
    return out


class _Jar:
    """（未使用）保留位置說明：生產係用 cookie_records_from_jar 由 requests jar 轉換，
    驗收腳本直接由 env 字串 parse，等效。"""


def main() -> int:
    log("=" * 60)
    log("HidenCloud 登入修正驗收（只讀）")
    log("=" * 60)
    note("env", email_len=len(EMAIL), password_len=len(PASSWORD),
         cookie_env_len=len(COOKIE), creds_present=bool(EMAIL and PASSWORD))
    if not (EMAIL and PASSWORD and COOKIE):
        log("❌ 缺 secret（需要 HIDEN_COOKIE + HC_EMAIL + HC_PASSWORD）")
        return 0

    cookies = parse_cookie_env(COOKIE)
    note("cookie_parsed", n=len(cookies), names=[c["name"] for c in cookies])

    import hc_browser
    sync_playwright = _sync_playwright()
    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(
                channel="chrome", headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                      '--disable-infobars', '--window-size=1920,1080'],
            )
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            t0 = time.time()
            ok = ensure_session(page, cookies, EMAIL, PASSWORD)
            dt = round(time.time() - t0, 1)
            note("ensure_session", ok=bool(ok), seconds=dt,
                 url=page.url, title=(page.title() or "")[:80],
                 last_login_error=hc_browser.LAST_LOGIN_ERROR or "(空)")
            shot(page, "01_after_ensure_session")

            report["ok"] = bool(ok)
            if ok:
                try:
                    ids = discover_service_ids(page, SHOT_PREFIX)
                    note("services", ids=ids)
                    report["services"] = ids
                except Exception as e:
                    note("services", err=str(e)[:200])
                verdict = "✅ 修正生效：死 cookie → 清 → 帳密登入 → 入到 dashboard"
            else:
                verdict = ("❌ 仍然失敗：" + (hc_browser.LAST_LOGIN_ERROR or "未知"))
            report["verdict"] = verdict
            log("🧾 " + verdict)
        except Exception as e:
            log(f"❌ 驗收腳本異常: {e}")
            report["error"] = str(e)
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    with open("fix_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log("📄 報告: fix_report.json（只讀，未對站方做寫操作）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
