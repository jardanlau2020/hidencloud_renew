"""discover_service_ids 回歸測試（2026-10-16 假紅事件）。

背景：run 38867592042（10-16 07:30Z）login 成功、Turnstile 全過，但 dashboard
HTML 讀得太早（JS 未 render 完）→ 主 regex `/service/(\\d+)/manage` 零收穫 →
舊碼兜底 `#(\\d{4,})` 掃到 **CSS 顏色碼**（Tailwind 色板：292524/737373/525252/
404040/0284/0891/059669/047857/075985…）→ 51 個幽靈服務 → 逐個去
`/service/<hex>/manage` 撞 404 → 51 條「撳 Renew 後 modal 彈唔出」假紅。

呢幾個測試釘死：① 顏色碼永遠唔會當服務；② 讀唔到清單時返 []（唔會爆幽靈）；
③ 頁面遲 render 時會等到／reload 到。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hc_browser  # noqa: E402

# 真・Tailwind 色板片段（同當日 log 嘅 51 個「服務」一致）
TAILWIND_HTML = """
<html><head><style>
  .bg-stone-800{color:#292524} .bg-neutral-500{color:#737373}
  .bg-neutral-600{color:#525252} .bg-neutral-700{color:#404040}
  .bg-sky-600{color:#0284c7} .bg-cyan-600{color:#0891b2}
  .bg-emerald-600{color:#059669} .bg-slate-700{color:#334155}
</style></head><body><div id="app"></div></body></html>
"""

DASHBOARD_HTML = """
<html><body>
  <a href="/service/230911/manage">Manage</a>
</body></html>
"""


class FakePage:
    """只實作 discover_service_ids 會用到嘅方法。

    shell  = 未 render 完嘅 HTML（JS 未出服務連結）
    full   = render 完嘅 HTML
    ready_after_reload: True = 只有 reload 之後先攞到 full
    """

    def __init__(self, shell, full=None, ready_after_reload=False):
        self.shell = shell
        self.full = full if full is not None else shell
        self.ready_after_reload = ready_after_reload
        self.url = "https://dash.hidencloud.com/dashboard"
        self.reloads = 0
        self.shots = []

    def goto(self, *a, **k):
        return None

    def wait_for_selector(self, *a, **k):
        if self.reloads or not self.ready_after_reload:
            if "/service/" in self.full:
                return None
        raise TimeoutError("fake: 等唔到服務連結")

    def reload(self, *a, **k):
        self.reloads += 1

    def content(self):
        return self.full if (self.reloads or not self.ready_after_reload) else self.shell

    def title(self):
        return "Dashboard"

    def screenshot(self, path=None, **k):
        self.shots.append(path)


def _noop_solve(*a, **k):
    return True


hc_browser.solve_turnstile = _noop_solve  # 測試唔需要真 Turnstile


def test_hex_colors_never_become_services():
    page = FakePage(TAILWIND_HTML)
    assert hc_browser.discover_service_ids(page, "t") == []


def test_empty_dashboard_returns_empty_not_phantoms():
    page = FakePage("<html><body></body></html>")
    assert hc_browser.discover_service_ids(page, "t") == []
    assert page.shots, "零收穫時應該留截圖證據"


def test_slow_render_recovered_by_reload():
    """頁面第一輪未 render 完 → reload 後攞到真服務（唔會爆幽靈）。"""
    page = FakePage(TAILWIND_HTML, DASHBOARD_HTML, ready_after_reload=True)
    ids = hc_browser.discover_service_ids(page, "t")
    assert ids == ["230911"], ids
    assert page.reloads == 1


class FakeGatePage(FakePage):
    """模擬 10-16 實測：/dashboard 被重定向到帳號驗證頁。"""

    def __init__(self):
        super().__init__("<html><body>Multi-Account Detection</body></html>")
        self.url = "https://dash.hidencloud.com/account-verification?type=multiaccounts"

    def title(self):
        return "Account Verification Required - HidenCloud"

    def inner_text(self, sel=None):
        return ("Multi-Account Detection\nYour account has been restricted due to the "
                "detection of multiple accounts. To regain access to our platform, you "
                "must verify your account.")


def test_account_gate_detected():
    page = FakeGatePage()
    gate = hc_browser._account_gate(page)
    assert gate, "應該認出帳號驗證頁"
    assert "Multi-Account" in gate, gate


def test_real_service_found_immediately():
    page = FakePage(DASHBOARD_HTML)
    ids = hc_browser.discover_service_ids(page, "t")
    assert ids == ["230911"], ids
    assert page.reloads == 0, "第一輪就搵到就唔應該 reload"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"✅ {name}")
    print("全部通過")
