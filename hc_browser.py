#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hc_browser.py —— HidenCloud 真瀏覽器續期路徑（解 Cloudflare Turnstile）

點解有呢個模組
--------------
面板 renew 表單自 2026-10 起加咗 CF Turnstile（sitekey `0x4AAAAAAAPmSriFX9MUUlyZ`），
純 HTTP 表單提交（main.py 嘅 requests/cloudscraper 路徑）一定被拒：
`Info Error! The cf-turnstile-response field is required.`（實測 2026-10-05，run 37256550222）

技術要點（移植自 eooce/Auto-Renew-HidenCloud 嘅實測結論，並非憑空寫）：
  - 新版 Turnstile 把挑戰 iframe 渲染喺「閉包 shadow DOM」入面，
    `page.locator('iframe[src*=...]')` 同 `querySelectorAll` 都搵唔到；
    唯一覆蓋得到嘅方法係行 `page.frames`（瀏覽器層 frame 樹）反查
    `challenges.cloudflare.com`，再用 `frame_element()` 攞返元素。
  - 隱藏 token 輸入框 `input[name="cf-turnstile-response"]` 同佢個 300x65 容器
    一定留喺 light DOM，可以做兜底定位。
  - 點擊複選框：首選 `frame_element.click(position=複選框位置)`（Playwright 會把
    事件路由入跨進程 iframe），失敗先用 CDP 底層滑鼠事件兜底（isTrusted=true）。

執行環境：需要 `xvfb`（headless=False + 真 Chrome）+ `patchright`。
"""

import os
import re
import time
import random

BASE_URL = "https://dash.hidencloud.com"
TURNSTILE_IFRAME_SEL = 'iframe[src*="challenges.cloudflare.com"], iframe[title*="Cloudflare"]'
TURNSTILE_FRAME_URL_MARKER = 'challenges.cloudflare.com'

# 反偵測：抹 webdriver 指標、補 window.chrome、修 permissions.query 特徵
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
window.chrome.loadTimes = window.chrome.loadTimes || function () { return {}; };
window.chrome.csi = window.chrome.csi || function () { return {}; };
if (!window.chrome.app) {
  window.chrome.app = { isInstalled: false,
    InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
    RunningState: { CANT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };
}
try {
  const origQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (origQuery) {
    window.navigator.permissions.query = (p) =>
      (p && p.name === 'notifications')
        ? Promise.resolve({ state: (window.Notification && Notification.permission) || 'prompt' })
        : origQuery(p);
  }
} catch (e) {}
"""

TURNSTILE_STATE_JS = """
() => {
    try {
        let total = 0, solved = 0;
        document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').forEach(n => {
            total += 1;
            if (n.value && n.value.length > 20) solved += 1;
        });
        return { total: total, solved: solved };
    } catch (e) { return { total: 0, solved: 0 }; }
}
"""

_CDP_SESSIONS = {}


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [browser] {message}", flush=True)


def _sync_playwright():
    """patchright（反偵測分支）優先，冇就退原生 playwright。"""
    try:
        from patchright.sync_api import sync_playwright
        log("使用 patchright（反偵測）")
        return sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright
        log("patchright 未裝，退回原生 playwright")
        return sync_playwright


# ============================ CDP 底層點擊 ============================

def get_cdp_session(page):
    session = _CDP_SESSIONS.get(page)
    if session is None:
        try:
            session = page.context.new_cdp_session(page)
        except Exception as e:
            log(f"⚠️ 開 CDP 會話失敗: {e}")
            return None
        _CDP_SESSIONS[page] = session
    return session


def reset_cdp_session(page):
    session = _CDP_SESSIONS.pop(page, None)
    try:
        if session is not None:
            session.detach()
    except Exception:
        pass


def cdp_click_at(page, x, y):
    """喺瀏覽器內核層注入真實滑鼠事件（isTrusted=true）"""
    session = get_cdp_session(page)
    if not session:
        return False
    try:
        # 由附近分多步平滑移動過去，唔好一落就跳
        sx = x - random.uniform(50, 110)
        sy = y - random.uniform(35, 75)
        steps = random.randint(8, 14)
        for i in range(1, steps + 1):
            ix = sx + (x - sx) * i / steps + random.uniform(-1.5, 1.5)
            iy = sy + (y - sy) * i / steps + random.uniform(-1.5, 1.5)
            session.send('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': ix, 'y': iy})
            time.sleep(random.uniform(0.01, 0.035))
        time.sleep(random.uniform(0.1, 0.25))
        session.send('Input.dispatchMouseEvent', {
            'type': 'mousePressed', 'x': x, 'y': y,
            'button': 'left', 'buttons': 1, 'clickCount': 1
        })
        time.sleep(random.uniform(0.05, 0.12))
        session.send('Input.dispatchMouseEvent', {
            'type': 'mouseReleased', 'x': x, 'y': y,
            'button': 'left', 'clickCount': 1
        })
        return True
    except Exception as e:
        log(f"⚠️ CDP 底層點擊失敗: {e}")
        reset_cdp_session(page)
        return False


# ============================ 狀態與定位 ============================

def turnstile_state(page):
    try:
        st = page.evaluate(TURNSTILE_STATE_JS)
        if isinstance(st, dict):
            return {"total": int(st.get("total", 0)), "solved": int(st.get("solved", 0))}
    except Exception:
        pass
    return {"total": 0, "solved": 0}


def _overlaps(box, boxes, dx=25, dy=25, dw=60):
    for b in boxes:
        if (abs(b['x'] - box['x']) < dx and abs(b['y'] - box['y']) < dy
                and abs(b['width'] - box['width']) < dw):
            return True
    return False


def challenge_frames(page):
    """真 challenge iframe：frame 樹反查（覆蓋 shadow DOM 內嵌）+ light DOM 兜底"""
    targets = []
    seen = []

    # 1) frame 樹反查 —— 唯一覆蓋閉包 shadow DOM 內嵌 iframe 嘅方法
    try:
        for f in page.frames:
            if TURNSTILE_FRAME_URL_MARKER not in (f.url or ''):
                continue
            try:
                fe = f.frame_element()
                if not fe.is_visible():
                    continue
                box = fe.bounding_box()
                if box and box.get('width', 0) > 10 and box.get('height', 0) > 10:
                    seen.append(box)
                    targets.append((fe, box))
            except Exception:
                continue
    except Exception:
        pass

    # 2) light DOM 內嘅 challenge iframe（舊版結構）
    try:
        for el in page.locator(TURNSTILE_IFRAME_SEL).all():
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if box and box.get('width', 0) > 10 and box.get('height', 0) > 10 \
                        and not _overlaps(box, seen):
                    seen.append(box)
                    targets.append((el, box))
            except Exception:
                continue
    except Exception:
        pass

    return targets


def challenge_containers(page):
    """token 輸入框嘅 light DOM 祖先容器（兜底點擊目標）"""
    targets = []
    try:
        for el in page.locator(
                'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').all():
            try:
                if el.evaluate("n => !!(n.value && n.value.length > 20)"):
                    continue
                box = el.evaluate("""n => {
                    let p = n.parentElement;
                    for (let i = 0; i < 4 && p; i++) {
                        const r = p.getBoundingClientRect();
                        if (r.width > 40 && r.height > 20)
                            return {x: r.x, y: r.y, width: r.width, height: r.height};
                        p = p.parentElement;
                    }
                    return null;
                }""")
                if box and not _overlaps(box, [b for _, b in targets]):
                    targets.append((None, box))
            except Exception:
                continue
    except Exception:
        pass
    return targets


def challenge_boxes(page):
    """合併挑戰框目標（iframe 優先，容器兜底去重）"""
    frames = challenge_frames(page)
    seen = [b for _, b in frames]
    targets = list(frames)
    for el, box in challenge_containers(page):
        if not _overlaps(box, seen):
            targets.append((el, box))
    return targets


def page_ready(p):
    """頁面唔係 CF 攔截頁／安全驗證頁"""
    try:
        t = (p.title() or "").lower()
        blocked = ("just a moment", "attention required", "checking your browser",
                   "请稍候", "security verification", "请验证")
        return bool(t) and not any(k in t for k in blocked)
    except Exception:
        return False


def solve_turnstile(page, timeout=120, success_check=None,
                    require_positive=False, appear_grace=5, reload_after=None,
                    shot_on_timeout="hc_turnstile_timeout.png"):
    """行到 True = 通過（依「自訂回調／全部 widget 有 token／挑戰框消失」三種訊號判定）"""
    log("🛡️ 開始處理 Turnstile...")
    start = time.time()
    baseline = turnstile_state(page)
    had_iframe = False
    iframe_gone_since = None
    container_only_since = None
    click_count = 0
    reload_done = 0

    while time.time() - start < timeout:
        # 訊號 1：調用方自訂判定
        if success_check is not None:
            try:
                if success_check(page):
                    log("✅ Turnstile 驗證通過！")
                    return True
            except Exception:
                pass

        # 訊號 2：新 widget 出現且全部有 token
        st = turnstile_state(page)
        if st["total"] > 0 and st["solved"] >= st["total"] and (
                st["total"] > baseline["total"] or st["solved"] > baseline["solved"]):
            log(f"✅ Turnstile 驗證通過（token 已生成 {st['solved']}/{st['total']}）！")
            return True

        frames = challenge_frames(page)
        seen = [b for _, b in frames]
        targets = list(frames) + [(el, b) for el, b in challenge_containers(page)
                                  if not _overlaps(b, seen)]

        if frames:
            had_iframe = True
            iframe_gone_since = None
            container_only_since = None
        elif targets:
            had_iframe = True
            iframe_gone_since = None
            if container_only_since is None:
                container_only_since = time.time()
            elif time.time() - container_only_since >= 12:
                log("✅ Turnstile 驗證通過（挑戰已結束）！")
                return True
        else:
            container_only_since = None
            if had_iframe:
                if iframe_gone_since is None:
                    iframe_gone_since = time.time()
                elif time.time() - iframe_gone_since >= 8:
                    log("✅ Turnstile 驗證通過（挑戰框已消失）！")
                    return True
            elif (not require_positive and success_check is None
                    and time.time() - start >= appear_grace):
                log("ℹ️ 頁面未出現 Turnstile，無需處理")
                return True
            time.sleep(1)
            continue

        # 逐個點擊：Playwright 定點為主，CDP 底層兜底
        for el, box in targets:
            clicked = False
            try:
                off_x = min(30, box['width'] / 2)
                pos_y = box['height'] / 2
                if el is not None:
                    try:
                        el.scroll_into_view_if_needed(timeout=3000)
                    except Exception:
                        pass
                    try:
                        el.click(position={'x': off_x, 'y': pos_y}, timeout=5000)
                        clicked = True
                        log(f"🖱️ 點擊 Turnstile ({box['x'] + off_x:.0f}, {box['y'] + pos_y:.0f}) ...")
                    except Exception:
                        log("⚠️ 挑戰框點擊失敗，轉底層點擊...")
                if not clicked:
                    cx = box['x'] + off_x + random.uniform(-2, 2)
                    cy = box['y'] + pos_y + random.uniform(-2, 2)
                    log(f"🖱️ CDP 底層點擊 Turnstile ({cx:.0f}, {cy:.0f}) ...")
                    clicked = cdp_click_at(page, cx, cy)
            except Exception as e:
                log(f"⚠️ 點擊挑戰框出錯: {e}")
            click_count += 1
            # 點完 widget 會進入數秒「驗證中」狀態，唔好即刻再撳
            time.sleep(random.uniform(4.0, 6.0))

        # 撳咗好多次都唔過：重載攞個全新挑戰
        if reload_after and click_count >= reload_after and reload_done < 2:
            reload_done += 1
            log(f"🔄 累計點擊 {click_count} 次未通過，重載重試（第 {reload_done}/2 次）...")
            click_count = 0
            had_iframe = False
            iframe_gone_since = None
            container_only_since = None
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"⚠️ 重載失敗: {e}")
            time.sleep(random.uniform(3.0, 5.0))

    log(f"❌ Turnstile 處理超時（{timeout}s）")
    try:
        cf = [f.url[:100] for f in page.frames if TURNSTILE_FRAME_URL_MARKER in (f.url or '')]
        st = turnstile_state(page)
        log(f"🔍 超時現場: cf_frames={len(cf)} token={st} title={page.title()!r}")
        page.screenshot(path=shot_on_timeout)
        log(f"📸 已存超時截圖: {shot_on_timeout}")
    except Exception:
        pass
    return False


# ============================ 登入 ============================

def _login_form_visible(p):
    try:
        return p.locator('input[type="password"]').first.is_visible()
    except Exception:
        return False


# ===== 死 cookie 污染登入 POST（2026-10-07 實測根因）=====
# 生產 run 37599106127：注入 4 個死 cookie → /dashboard 被彈 → 退帳密登入 → 報「帳密錯」。
# 探針實測（只讀，兩版對照）：
#   direct（乾淨 context）      → POST /auth/login 帶 token → 302 /dashboard ✅
#   cookie_first（先注入死 cookie）→ POST /auth/login 帶 token → 419 Page Expired ❌
# 原因：舊 `hidencloud_session` / `XSRF-TOKEN` 同頁面新發嘅並存（POST 上見到兩個同名），
#       伺服器揀舊嗰份 → CSRF 對唔上 → Laravel 419。帳密本身完全正確。
# 所以退帳密登入之前，必須清走舊「應用層 session」cookie；
# CF 憑證（cf_clearance / hc_cf_turnstile）保留，唔係就要重過 Cloudflare。
STALE_APP_COOKIE_RE = re.compile(r"^(hidencloud_session|XSRF-TOKEN|remember_web_)", re.I)

LAST_LOGIN_ERROR = ""


def _set_login_error(msg):
    global LAST_LOGIN_ERROR
    LAST_LOGIN_ERROR = msg


def purge_stale_app_cookies(page):
    """清走舊應用層 session cookie（保留 Cloudflare 憑證）。返清除數量。"""
    removed = 0
    try:
        cookies = page.context.cookies()
    except Exception as e:
        log(f"⚠️ 讀 cookie 失敗: {e}")
        return 0
    for c in cookies:
        name = c.get("name", "")
        if not STALE_APP_COOKIE_RE.match(name):
            continue
        ok = False
        for kwargs in ({"name": name, "domain": c.get("domain"), "path": c.get("path")},
                       {"name": name, "url": BASE_URL},
                       {"name": name}):
            try:
                page.context.clear_cookies(**kwargs)
                ok = True
                break
            except Exception:
                continue
        if ok:
            removed += 1
    if removed:
        log(f"🧹 清走 {removed} 個舊 session cookie（保留 CF 憑證）")
    return removed


def login_with_password(page, email, password):
    """登入頁本身有第一道 Turnstile：通過後先會出帳密輸入框。

    實測順序（eooce 方案）：solve(第一道) → 填帳密 → 等 8 秒 → solve(第二道，
    require_positive，冇出現都照撳）→ 撳登入 → 可能再有第三道 → 等跳 dashboard
    """
    log(f"💣 嘗試帳密登入 {email[:2]}*** …")
    # 清走死 session cookie：否則舊 XSRF-TOKEN / hidencloud_session 會令
    # 登入 POST 變 419 Page Expired（見上面 STALE_APP_COOKIE_RE 註解）
    purge_stale_app_cookies(page)
    page.goto(f"{BASE_URL}/auth/login", wait_until="domcontentloaded", timeout=60000)

    log("🛡️ 處理登入頁第一道 Turnstile…")
    if not solve_turnstile(page, timeout=180, success_check=_login_form_visible,
                           reload_after=8, shot_on_timeout="hc_login_ts1.png"):
        log("❌ 第一道 Turnstile 未通過，入唔到登入表單")
        return False

    email_sel = ('input[name="username"], input#username, input[name="email"], '
                 'input[type="email"], input[name="EMAIL"]')
    pwd_sel = ('input[name="password"], input#password, '
               'input[name="PASSWORD"], input[type="password"]')
    try:
        email_input = page.locator(email_sel).first
        pwd_input = page.locator(pwd_sel).first
        email_input.wait_for(state="visible", timeout=60000)
        email_input.click()
        email_input.fill(email)
        time.sleep(random.uniform(0.8, 1.5))
        pwd_input.click()
        pwd_input.fill(password)
    except Exception as e:
        log(f"❌ 填帳密出錯: {e}")
        return False

    log("⏳ 輸入完成，等 Turnstile 載入…")
    time.sleep(8)

    log("🛡️ 處理第二道 Turnstile…")
    if not solve_turnstile(page, timeout=90, require_positive=True,
                           shot_on_timeout="hc_login_ts2.png"):
        log("⚠️ 第二道 Turnstile 未確認通過，仲係試下撳登入…")

    try:
        submit = page.locator('button[type="submit"], button:has-text("Login"), '
                              'button:has-text("Sign in"), button:has-text("登录")').first
        submit.click(timeout=15000)
    except Exception as e:
        log(f"⚠️ 撳登入掣失敗: {e}")
        return False

    # 提交後若再出現 Turnstile，一邊處理一邊等跳轉
    solve_turnstile(page, timeout=45, success_check=lambda p: "/auth/login" not in (p.url or ""),
                    shot_on_timeout="hc_login_ts3.png")
    try:
        page.wait_for_url(lambda u: "/auth/login" not in (u or ""), timeout=30000)
    except Exception:
        pass

    # 419 = Laravel CSRF/session 對唔上（Page Expired），唔係帳密錯
    try:
        ttl = (page.title() or "").lower()
    except Exception:
        ttl = ""
    if "page expired" in ttl or "419" in ttl:
        log("❌ 419 Page Expired：登入 POST 被 CSRF/session 擋（唔係帳密問題）")
        _set_login_error("登入被 419 CSRF 擋（session cookie 衝突，非帳密問題）")
        return False

    page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
    solve_turnstile(page, timeout=90, success_check=page_ready, reload_after=8,
                    shot_on_timeout="hc_login_ts4.png")
    if "/auth/login" in page.url:
        log("❌ 帳密登入失敗（帳密錯／被封）")
        _set_login_error("登入被拒或未跳轉（請檢查帳號密碼）")
        return False
    log("✅ 帳密登入成功，已到 dashboard")
    _set_login_error("")
    return True


def ensure_session(page, cookies, email="", password=""):
    """cookie 先行；cookie 死咗就退帳密登入。返 True = 已登入。"""
    if cookies:
        try:
            page.context.add_cookies(cookies)
            log(f"📇 已注入 {len(cookies)} 個 cookie")
        except Exception as e:
            log(f"⚠️ 注入 cookie 出錯: {e}")
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=120, success_check=page_ready, reload_after=8,
                        shot_on_timeout="hc_session_guard.png")
        if "/auth/login" not in (page.url or ""):
            log("✅ Cookie 會話有效")
            return True
        log("⚠️ Cookie 唔認帳，轉帳密登入…")

    if not (email and password):
        log("❌ 冇帳密可用，而 cookie 又已失效")
        _set_login_error("cookie 失效且冇帳密後備")
        return False
    return login_with_password(page, email, password)


# ============================ Cookie 橋接 ============================

def cookie_records_from_jar(jar):
    """requests.cookiesjar → Playwright cookie records"""
    records = []
    for c in jar:
        domain = (c.domain or '').lstrip('.') or 'dash.hidencloud.com'
        records.append({
            'name': c.name,
            'value': c.value,
            'domain': domain if domain.startswith('dash.') else 'dash.hidencloud.com',
            'path': c.path or '/',
            'secure': True,
            'httpOnly': False,
            'sameSite': 'Lax',
        })
    return records


def get_due_date(page):
    try:
        body = page.locator("body").inner_text(timeout=15000)
        for pat in (r'Current Due Date\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4})',
                    r'expires on the\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4})',
                    r'Due date\s*\n?\s*([0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4})'):
            m = re.search(pat, body, re.I)
            if m:
                return m.group(1)
    except Exception as e:
        log(f"⚠️ 讀到期日失敗: {e}")
    return ""


def _server_error_text(page):
    """面板拒絕時嘅原文（通知要帶返上去）"""
    try:
        text = page.locator("body").inner_text(timeout=10000)
    except Exception:
        return ""
    for pat in (r'Info Error!\s*([^\n]+)', r'Renewal Restricted[^\n]*'):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(0).strip()[:200]
    return ""


# ============================ 主流程 ============================

def discover_service_ids(page, shot_prefix='hc'):
    """由 dashboard HTML 抽服務 ID（HTTP cookie 死咗都唔影響，browser 自己睇）"""
    try:
        page.goto(f"{BASE_URL}/dashboard", wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=120, success_check=page_ready, reload_after=8,
                        shot_on_timeout=f"{shot_prefix}_discover.png")
        html = page.content()
        ids = []
        for sid in re.findall(r'/service/(\d+)/manage', html):
            if sid not in ids:
                ids.append(sid)
        if not ids:
            for sid in re.findall(r'#(\d{4,})', html):
                if sid not in ids:
                    ids.append(sid)
        log(f"🔎 瀏覽器發現服務: {ids or '（冇）'}")
        return ids
    except Exception as e:
        log(f"❌ 發現服務失敗: {e}")
        return []


def _renew_one_in_session(page, service_id, shot_prefix='hc'):
    """已登入狀態下續期單一服務。返 dict：status/detail/due_before/due_after/shots"""
    service_url = f"{BASE_URL}/service/{service_id}/manage"
    shots = []

    def shot(name):
        path = f"{shot_prefix}_{name}.png"
        try:
            page.screenshot(path=path, full_page=False)
            shots.append(path)
            log(f"📸 截圖: {path}")
        except Exception:
            pass

    def refresh_manage():
        if page.url != service_url:
            page.goto(service_url, wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=90, success_check=page_ready, reload_after=8,
                        shot_on_timeout=f"{shot_prefix}_guard.png")
        return page

    try:
        log(f"➡ 前往服務頁 {service_url}")
        page.goto(service_url, wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=120, success_check=page_ready, reload_after=8,
                        shot_on_timeout=f"{shot_prefix}_entry.png")

        if '/auth/login' in page.url or '/login' in page.url:
            shot("login_required")
            return {'status': 'bad', 'detail': '被踢去登入頁（會話失效）',
                    'due_before': '', 'due_after': '', 'shots': shots}

        due_before = get_due_date(page)
        log(f"📆 續期前到期: {due_before or '（抽唔到）'}")

        # ---- 撳 Renew 打開 modal ----
        log("🖱️ 準備點擊 Renew…")
        renew_btn = page.locator('button[onclick*="showRenewAlert"], button:has-text("Renew")').first
        create_btn = page.locator('button:has-text("Create Invoice")')

        modal_opened = False
        for i in range(6):
            try:
                renew_btn.wait_for(state="visible", timeout=10000)
                renew_btn.scroll_into_view_if_needed(timeout=5000)
                log(f"🖱️ 第 {i + 1} 次點擊 Renew...")
                renew_btn.click(timeout=10000)

                time.sleep(2)
                page_text = page.locator("body").inner_text(timeout=10000)
                if "Renewal Restricted" in page_text or "can only renew" in page_text.lower():
                    shot("renew_not_allowed")
                    return {'status': 'skip', 'detail': '未到續期窗口',
                            'due_before': due_before, 'due_after': due_before, 'shots': shots}
                err = _server_error_text(page)
                if err:
                    shot("server_reject")
                    return {'status': 'bad', 'detail': err,
                            'due_before': due_before, 'due_after': due_before, 'shots': shots}

                try:
                    create_btn.wait_for(state="visible", timeout=5000)
                    modal_opened = True
                    log("✅ modal 已彈出（見 Create Invoice）")
                    break
                except Exception:
                    # modal 可能先出 Turnstile，Create Invoice 稍後先出現
                    if challenge_boxes(page):
                        modal_opened = True
                        log("✅ modal 已彈出（先出 Turnstile）")
                        break
                    log("⚠️ modal 未出現，準備重試…")
                    time.sleep(2)
            except Exception as e:
                log(f"❌ 點擊 Renew 出錯: {e}")

        if not modal_opened:
            shot("modal_failed")
            return {'status': 'bad', 'detail': '撳 Renew 後 modal 彈唔出',
                    'due_before': due_before, 'due_after': due_before, 'shots': shots}

        # ---- modal 內 Turnstile ----
        log("🛡️ 處理 modal 內 Turnstile…")
        if not solve_turnstile(page, timeout=120, require_positive=True,
                               shot_on_timeout=f"{shot_prefix}_modal_ts.png"):
            log("⚠️ modal Turnstile 未確認通過，嘗試照樣撳 Create Invoice…")

        try:
            create_btn.wait_for(state="visible", timeout=30000)
        except Exception:
            pass

        create_clicked = False
        for i in range(3):
            try:
                log(f"🖱️ 點擊 Create Invoice（第 {i + 1} 次）...")
                create_btn.click(timeout=10000)
                create_clicked = True
                break
            except Exception as e:
                log(f"⚠️ Create Invoice 點擊失敗: {e}")
                solve_turnstile(page, timeout=40, require_positive=True)

        if not create_clicked:
            shot("create_invoice_failed")
            return {'status': 'bad', 'detail': '撳唔到 Create Invoice',
                    'due_before': due_before, 'due_after': due_before, 'shots': shots}

        # ---- 等跳落 invoice 頁 ----
        invoice_url = None
        t0 = time.time()
        while time.time() - t0 < 120:
            if '/invoice/' in page.url or '/payment/' in page.url:
                invoice_url = page.url
                log(f"🎉 已跳落: {invoice_url}")
                break
            if page.locator(f'iframe[src*="{TURNSTILE_FRAME_URL_MARKER}"]').count() > 0:
                solve_turnstile(page, timeout=45, reload_after=8)
            time.sleep(1)

        if not invoice_url:
            shot("stuck_invoice")
            err = _server_error_text(page)
            return {'status': 'bad',
                    'detail': err or '撳完 Create Invoice 但冇跳落發票頁',
                    'due_before': due_before, 'due_after': due_before, 'shots': shots}

        # ---- 發票頁撳 Pay ----
        refresh_manage()
        if page.url != invoice_url:
            page.goto(invoice_url, wait_until="domcontentloaded", timeout=60000)
        solve_turnstile(page, timeout=90, success_check=page_ready, reload_after=8,
                        shot_on_timeout=f"{shot_prefix}_invoice_ts.png")

        pay_btn = page.locator('a:has-text("Pay"):visible, button:has-text("Pay"):visible').first
        try:
            pay_btn.wait_for(state="visible", timeout=30000)
            pay_btn.click(timeout=15000)
            log("✅ 已點擊 Pay")
        except Exception as e:
            log(f"⚠️ 搵唔到 Pay 掣: {e}")
            shot("no_pay_button")
            return {'status': 'bad', 'detail': '發票頁搵唔到 Pay 掣',
                    'due_before': due_before, 'due_after': due_before, 'shots': shots}

        time.sleep(6)
        solve_turnstile(page, timeout=60, success_check=page_ready, reload_after=8)

        # ---- 返去睇新到期日 ----
        refresh_manage()
        due_after = get_due_date(page)
        log(f"📆 續期後到期: {due_after or '（抽唔到）'}")

        if due_before and due_after and due_before != due_after:
            log("✅ 續期確認：到期日已推後")
            return {'status': 'ok', 'detail': f'續期成功（{due_before} → {due_after}）',
                    'due_before': due_before, 'due_after': due_after, 'shots': shots}

        shot("after_pay")
        return {'status': 'bad', 'detail': f'付咗但到期日冇變（{due_before or "?"} → {due_after or "?"}）',
                'due_before': due_before, 'due_after': due_after, 'shots': shots}

    except Exception as e:
        log(f"❌ 續期流程異常: {e}")
        shot("exception")
        return {'status': 'bad', 'detail': f'續期流程異常：{e}',
                'due_before': '', 'due_after': '', 'shots': shots}


def renew_account_via_browser(service_ids, cookies, email='', password='', shot_prefix='hc'):
    """開一次瀏覽器處理成個帳號：建 session →（冇 service_ids 就自己發現）→ 逐個續期。

    重點：唔靠 HTTP 層（bot.init()）——cookie 死咗都仲有出路。
    """
    sync_playwright = _sync_playwright()
    results = []
    all_shots = []

    with sync_playwright() as p:
        browser = None
        page = None
        try:
            log("🚀 啟動瀏覽器（真 Chrome + xvfb）...")
            browser = p.chromium.launch(
                channel="chrome",
                headless=False,
                args=['--no-sandbox', '--disable-blink-features=AutomationControlled',
                      '--disable-infobars', '--window-size=1920,1080']
            )
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            page.add_init_script(STEALTH_JS)

            if not ensure_session(page, cookies, email, password):
                try:
                    path = f"{shot_prefix}_login_fail.png"
                    page.screenshot(path=path)
                    all_shots.append(path)
                    log(f"📸 截圖: {path}")
                except Exception:
                    pass
                return {'services': [{'id': sid or '?', 'status': 'bad',
                                      'detail': LAST_LOGIN_ERROR
                                                or '登入唔到（cookie 失效且冇可用帳密）',
                                      'due_before': '', 'due_after': ''} for sid in (service_ids or ['?'])],
                        'shots': all_shots}

            ids = list(service_ids or [])
            if not ids:
                ids = discover_service_ids(page, shot_prefix)
            if not ids:
                return {'services': [{'id': '?', 'status': 'bad',
                                      'detail': '搵唔到任何服務',
                                      'due_before': '', 'due_after': ''}],
                        'shots': all_shots}

            for sid in ids:
                log(f"━━━ 服務 {sid} ━━━")
                out = _renew_one_in_session(page, sid, shot_prefix=f"{shot_prefix}_{sid}")
                out['id'] = sid
                all_shots.extend(out.pop('shots', []))
                results.append(out)

            return {'services': results, 'shots': all_shots}

        except Exception as e:
            log(f"❌ 瀏覽器流程異常: {e}")
            if page is not None:
                try:
                    path = f"{shot_prefix}_exception.png"
                    page.screenshot(path=path)
                    all_shots.append(path)
                except Exception:
                    pass
            return {'services': results or [{'id': sid or '?', 'status': 'bad',
                                             'detail': f'瀏覽器流程異常：{e}',
                                             'due_before': '', 'due_after': ''}
                                            for sid in (service_ids or ['?'])],
                    'shots': all_shots}
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass


def renew_via_browser(service_id, cookies, shot_prefix='hc', email='', password=''):
    """單一服務嘅便利包裝（相容舊介面）"""
    out = renew_account_via_browser([service_id], cookies, email, password, shot_prefix)
    svc = (out.get('services') or [{}])[0]
    svc['shots'] = out.get('shots', [])
    return svc
