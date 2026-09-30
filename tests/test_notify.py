# -*- coding: utf-8 -*-
import os
import sys
import time
import unittest
import importlib
from unittest.mock import Mock, patch

import notify


class NotifyRoutingTests(unittest.TestCase):
    def test_default_channel_is_wxpusher(self):
        self.assertEqual(notify.normalize_channel(None), "wxPusherBot")
        self.assertEqual(notify.normalize_channel(""), "wxPusherBot")

    def test_alias_maps_to_official_name(self):
        self.assertEqual(notify.normalize_channel("telegram"), "telegramBot")
        self.assertEqual(notify.normalize_channel("dingtalk"), "dingtalkBot")

    @patch("notify.requests.post")
    def test_default_wxpusher_supports_legacy_env(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"code": 1000, "msg": "ok"}))
        env = {
            "WP_APP_TOKEN_ONE": "legacy-token",
            "WP_UIDs": "UID_A;UID_B",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("中文标题", "中文内容")

        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["appToken"], "legacy-token")
        self.assertEqual(kwargs["json"]["uids"], ["UID_A", "UID_B"])
        self.assertEqual(kwargs["json"]["summary"], "中文标题")

    def test_missing_channel_config_returns_false(self):
        with patch.dict(os.environ, {"NOTIFY_CHANNEL": "telegram"}, clear=True):
            self.assertFalse(notify.send_notify("标题", "内容"))

    def test_unknown_channel_returns_false(self):
        with patch.dict(os.environ, {"NOTIFY_CHANNEL": "unknown"}, clear=True):
            self.assertFalse(notify.send_notify("标题", "内容"))

    def test_main_uses_notify_module_send_notify(self):
        fake_cloudscraper = Mock(create_scraper=Mock(return_value=Mock()))
        fake_bs4 = Mock(BeautifulSoup=Mock())
        with patch.dict(sys.modules, {"cloudscraper": fake_cloudscraper, "bs4": fake_bs4}):
            main_module = importlib.import_module("main")
            main_module = importlib.reload(main_module)
        self.assertIs(main_module.send_notify, notify.send_notify)
        self.assertIs(main_module.build_summary, notify.build_summary)

    def test_all_official_channels_have_senders(self):
        self.assertEqual(set(notify.SENDERS), notify.OFFICIAL_CHANNELS)

    @patch("notify.requests.post")
    def test_serverchan_supports_sendkey_alias(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"code": 0}))
        env = {
            "NOTIFY_CHANNEL": "serverchan",
            "SERVERCHAN_SENDKEY": "SCT123456TEST",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "第一行\n第二行")

        self.assertTrue(ok)
        args, kwargs = mock_post.call_args
        self.assertIn("SCT123456TEST", args[0])
        self.assertEqual(kwargs["data"]["text"], "标题")
        self.assertIn("第一行", kwargs["data"]["desp"])

    @patch("notify.requests.post")
    def test_telegram_supports_chat_id_alias(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"ok": True}))
        env = {
            "NOTIFY_CHANNEL": "telegram",
            "TG_BOT_TOKEN": "123:ABC",
            "TG_CHAT_ID": "999999",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "内容")

        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["params"]["chat_id"], "999999")
        self.assertEqual(kwargs["params"]["text"], "标题\n内容")

    @patch("notify.requests.post")
    def test_dingtalk_uses_utf8_json(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"errcode": 0}))
        env = {
            "NOTIFY_CHANNEL": "dingtalk",
            "DD_BOT_TOKEN": "token",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "中文内容")

        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertIn("charset=utf-8", kwargs["headers"]["Content-Type"].lower())
        self.assertIn("中文内容", kwargs["data"])

    @patch("notify.requests.post")
    def test_wework_bot_sends_text_message(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"errcode": 0}))
        env = {
            "NOTIFY_CHANNEL": "wework_bot",
            "QYWX_KEY": "qy-key",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "内容")

        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertIn("qy-key", kwargs["url"])
        self.assertIn("标题", kwargs["data"])

    @patch("notify.requests.post")
    def test_wework_app_fetches_token_and_sends_text(self, mock_post):
        mock_post.side_effect = [
            Mock(text='{"access_token":"access-token"}', json=Mock(return_value={"access_token": "access-token"})),
            Mock(json=Mock(return_value={"errmsg": "ok"})),
        ]
        env = {
            "NOTIFY_CHANNEL": "wework_app",
            "QYWX_AM": "corp,secret,@all,1000001",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "内容")

        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 2)

    @patch("notify.requests.post")
    def test_pushplus_supports_pushplus_token_alias(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"code": 200, "data": "serial"}))
        env = {
            "NOTIFY_CHANNEL": "pushplus",
            "PUSHPLUS_TOKEN": "pushplus-token",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "内容")

        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertIn("pushplus-token", kwargs["data"].decode("utf-8"))

    @patch("notify.requests.post")
    def test_feishu_supports_direct_webhook(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"code": 0}))
        env = {
            "NOTIFY_CHANNEL": "feishu",
            "FEISHU_WEBHOOK": "https://open.feishu.cn/open-apis/bot/v2/hook/abc",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "内容")

        self.assertTrue(ok)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], env["FEISHU_WEBHOOK"])
        self.assertIn("标题", kwargs["data"])

    @patch("notify.requests.post")
    def test_bark_uses_configured_url(self, mock_post):
        mock_post.return_value = Mock(json=Mock(return_value={"code": 200}))
        env = {
            "NOTIFY_CHANNEL": "bark",
            "BARK_PUSH": "https://api.day.app/test-token",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("标题", "内容")

        self.assertTrue(ok)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.day.app/test-token")
        self.assertIn("标题", kwargs["data"])

    @patch("notify.requests.request")
    def test_webhook_replaces_title_and_content(self, mock_request):
        mock_request.return_value = Mock(status_code=200, text="ok")
        env = {
            "NOTIFY_CHANNEL": "webhook",
            "WEBHOOK_URL": "https://example.com/hook?title=$title&content=$content",
            "WEBHOOK_METHOD": "POST",
            "WEBHOOK_BODY": '{"title":"$title","content":"$content"}',
            "WEBHOOK_CONTENT_TYPE": "application/json",
            "WEBHOOK_HEADERS": "X-Test: test",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("中文标题", "中文内容")

        self.assertTrue(ok)
        _, kwargs = mock_request.call_args
        self.assertIn("%E4%B8%AD%E6%96%87%E6%A0%87%E9%A2%98", kwargs["url"])
        self.assertIn("中文内容", kwargs["data"])

    @patch("notify.requests.post")
    def test_ntfy_encodes_title_and_body(self, mock_post):
        mock_post.return_value = Mock(status_code=200, text="ok")
        env = {
            "NOTIFY_CHANNEL": "ntfy",
            "NTFY_URL": "https://ntfy.sh",
            "NTFY_TOPIC": "topic",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("中文标题", "中文内容")

        self.assertTrue(ok)
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"], "中文内容".encode("utf-8"))
        self.assertIn("=?utf-8?B?", kwargs["headers"]["Title"])

    @patch("notify.smtplib.SMTP")
    def test_email_uses_utf8_message(self, mock_smtp):
        smtp_instance = mock_smtp.return_value
        env = {
            "NOTIFY_CHANNEL": "email",
            "SMTP_SERVER": "smtp.example.com:25",
            "SMTP_SSL": "false",
            "SMTP_EMAIL": "bot@example.com",
            "SMTP_PASSWORD": "pwd",
            "SMTP_NAME": "机器人",
        }
        with patch.dict(os.environ, env, clear=True):
            ok = notify.send_notify("中文标题", "中文内容")

        self.assertTrue(ok)
        smtp_instance.sendmail.assert_called_once()
        args = smtp_instance.sendmail.call_args[0]
        self.assertEqual(args[0], "bot@example.com")


class SummaryFormatTests(unittest.TestCase):
    """通知瘦身格式：統計一行 + 每項一行（冇空行／冇分隔線／冇水印）"""

    def setUp(self):
        patcher = patch("notify.now_local", return_value="09-30 12:00")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_all_success_is_two_lines(self):
        text = notify.build_summary("HidenCloud 續期", [
            {"name": "帳號 1 服務 147008", "status": "ok"},
        ])
        self.assertEqual(
            text,
            "🎮 HidenCloud 續期 ｜ 09-30 12:00 ｜ ✅ 1 ｜ ⏭️ 0 ｜ ❌ 0\n"
            "▪️ 帳號 1 服務 147008 · ✅ 已續期",
        )

    def test_success_with_expiry_shows_new_expiry(self):
        text = notify.build_summary("HidenCloud 續期", [
            {"name": "帳號 1 服務 147008", "status": "ok", "expire": "2026-10-12T08:00:00"},
        ])
        self.assertEqual(
            text.splitlines()[1],
            "▪️ 帳號 1 服務 147008 · ✅ 已續期 → 10-12 08:00",
        )

    def test_all_skipped_keeps_reason(self):
        text = notify.build_summary("HidenCloud 續期", [
            {"name": "帳號 1 服務 147008", "status": "skip", "detail": "未可續（未到窗口，剩 3 天）"},
            {"name": "帳號 2 服務 147009", "status": "skip", "detail": "未可續（未到窗口，剩 5 天）"},
        ])
        lines = text.splitlines()
        self.assertEqual(lines[0], "🎮 HidenCloud 續期 ｜ 09-30 12:00 ｜ ✅ 0 ｜ ⏭️ 2 ｜ ❌ 0")
        self.assertEqual(lines[1], "▪️ 帳號 1 服務 147008 · ⏭️ 未可續（未到窗口，剩 3 天）")
        self.assertEqual(lines[2], "▪️ 帳號 2 服務 147009 · ⏭️ 未可續（未到窗口，剩 5 天）")
        self.assertEqual(len(lines), 3)

    def test_failure_adds_hint_line_and_truncates_reason(self):
        text = notify.build_summary("HidenCloud 續期", [
            {"name": "帳號 1 服務 147008", "status": "ok"},
            {"name": "帳號 2 服務 147009", "status": "bad", "detail": "處理異常：" + "x" * 80},
        ])
        lines = text.splitlines()
        self.assertEqual(lines[0], "🎮 HidenCloud 續期 ｜ 09-30 12:00 ｜ ✅ 1 ｜ ⏭️ 0 ｜ ❌ 1")
        self.assertTrue(lines[2].startswith("▪️ 帳號 2 服務 147009 · ❌ 處理異常：xxxx"))
        self.assertLessEqual(len(lines[2]), len("▪️ 帳號 2 服務 147009 · ❌ ") + 60)
        self.assertEqual(lines[-1], "⚠️ 睇 workflow log 排查")

    def test_dry_run_and_no_blank_lines(self):
        text = notify.build_summary("HidenCloud 續期", [
            {"name": "帳號 1 服務 147008", "status": "dry", "expire": "2026-10-12"},
        ])
        self.assertNotIn("\n\n", text)
        self.assertNotIn("───", text)
        self.assertEqual(text.splitlines()[1], "▪️ 帳號 1 服務 147008 · 🧪 dry-run · 到期 10-12")

class SummaryHelperTests(unittest.TestCase):
    """排版 helper（唔 patch now_local，順便驗真時間）"""

    def test_clip_text_flattens_and_truncates(self):
        self.assertEqual(notify.clip_text("  多行\n內容\t測試 "), "多行 內容 測試")
        self.assertEqual(notify.clip_text("abcdef", 4), "abc…")
        self.assertEqual(notify.clip_text(None), "")

    def test_fmt_expiry_variants(self):
        self.assertEqual(notify.fmt_expiry(None), "")
        self.assertEqual(notify.fmt_expiry(""), "")
        self.assertEqual(notify.fmt_expiry("2026-10-12T08:30:00"), "10-12 08:30")
        self.assertEqual(notify.fmt_expiry("2026-10-12"), "10-12")
        self.assertEqual(notify.fmt_expiry(1760000000), "10-09 16:53")  # 秒（UTC+8）
        self.assertEqual(notify.fmt_expiry(1760000000000), "10-09 16:53")  # 毫秒

    def test_now_local_is_utc8_mm_dd_hh_mm(self):
        expected = time.strftime("%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600))
        self.assertRegex(notify.now_local(), r"^\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(notify.now_local(), expected)


if __name__ == "__main__":
    unittest.main()
