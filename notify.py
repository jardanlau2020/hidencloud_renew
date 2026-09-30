# -*- coding: utf-8 -*-
"""
QingLong 风格的单选通知分发模块。

特点：
- 默认渠道为 wxPusherBot
- 支持 QingLong 官方通知类型
- 保留当前项目旧版 WxPusher 环境变量兼容
- 统一按 UTF-8 处理中文内容
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import smtplib
import time
import urllib.parse
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape
from typing import Callable

import requests


OFFICIAL_CHANNELS = {
    "gotify",
    "goCqHttpBot",
    "serverChan",
    "pushDeer",
    "bark",
    "chat",
    "telegramBot",
    "dingtalkBot",
    "weWorkBot",
    "weWorkApp",
    "aibotk",
    "iGot",
    "pushPlus",
    "wePlusBot",
    "email",
    "pushMe",
    "feishu",
    "webhook",
    "chronocat",
    "ntfy",
    "wxPusherBot",
}


CHANNEL_ALIASES = {
    "": "wxPusherBot",
    "wxpusher": "wxPusherBot",
    "wxpusherbot": "wxPusherBot",
    "serverchan": "serverChan",
    "serverj": "serverChan",
    "pushdeer": "pushDeer",
    "telegram": "telegramBot",
    "telegrambot": "telegramBot",
    "dingtalk": "dingtalkBot",
    "dingtalkbot": "dingtalkBot",
    "wework_bot": "weWorkBot",
    "weworkbot": "weWorkBot",
    "wework_app": "weWorkApp",
    "weworkapp": "weWorkApp",
    "pushplus": "pushPlus",
    "weplusbot": "wePlusBot",
    "pushme": "pushMe",
    "gocqhttp": "goCqHttpBot",
    "gocqhttpbot": "goCqHttpBot",
}


def _log(message: str) -> None:
    print(message)


def json_dumps(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _split_values(raw: str) -> list[str]:
    if not raw:
        return []
    normalized = raw.replace(",", ";").replace("\n", ";")
    return [item.strip() for item in normalized.split(";") if item.strip()]


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ================= 通知排版 helper（fleet 統一瘦身格式） =================
def now_local() -> str:
    """UTC+8 當地時間 MM-DD HH:MM（GitHub runner 係 UTC）"""
    return time.strftime("%m-%d %H:%M", time.gmtime(time.time() + 8 * 3600))


def clip_text(text: object, limit: int = 60) -> str:
    """壓平換行／多餘空白並截短，超出用 … 收尾"""
    cleaned = " ".join(str(text or "").split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def fmt_expiry(value: object) -> str:
    """到期時間（timestamp 秒／毫秒 或 ISO／日期字串）→ MM-DD HH:MM 或 MM-DD"""
    if value in (None, "", 0):
        return ""
    try:
        number = float(value)
        if number > 1e11:
            number /= 1000.0
        if number > 1e9:
            return time.strftime("%m-%d %H:%M", time.gmtime(number + 8 * 3600))
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", text)
    if match:
        return f"{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}"
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return f"{match.group(2)}-{match.group(3)}"
    return text[:19]


def build_summary(service_name: str, items: list[dict]) -> str:
    """瘦身版通知：統計一行 + 每項一行（純文字，唔加任何標記）

    items: [{"name": 帳號／伺服器, "status": ok|skip|bad|dry,
             "detail": 短原因, "expire": 到期時間}]
    """
    n_ok = sum(1 for item in items if item.get("status") in ("ok", "done"))
    n_skip = sum(1 for item in items if item.get("status") == "skip")
    n_bad = sum(1 for item in items if item.get("status") == "bad")
    lines = [f"🎮 {service_name} ｜ {now_local()} ｜ ✅ {n_ok} ｜ ⏭️ {n_skip} ｜ ❌ {n_bad}"]
    for item in items:
        bits = ["▪️ " + str(item.get("name") or "?")]
        status = item.get("status")
        expire = fmt_expiry(item.get("expire"))
        if status in ("ok", "done"):
            bits.append("✅ 已續期" + (f" → {expire}" if expire else ""))
        elif status == "skip":
            bits.append("⏭️ " + (item.get("detail") or "未可續") + (f" · 到期 {expire}" if expire else ""))
        elif status == "dry":
            bits.append("🧪 dry-run" + (f" · 到期 {expire}" if expire else ""))
        else:
            bits.append("❌ " + clip_text(item.get("detail") or "失敗"))
        lines.append(" · ".join(bits))
    if n_bad:
        lines.append("⚠️ 睇 workflow log 排查")
    return "\n".join(lines)


def normalize_channel(channel: str | None) -> str:
    if channel is None:
        return "wxPusherBot"
    key = channel.strip()
    if not key:
        return "wxPusherBot"
    if key in OFFICIAL_CHANNELS:
        return key
    return CHANNEL_ALIASES.get(key.lower(), key)


def _rfc2047(text: str) -> str:
    encoded_bytes = base64.b64encode(text.encode("utf-8"))
    return f"=?utf-8?B?{encoded_bytes.decode('utf-8')}?="


def parse_headers(headers: str) -> dict[str, str]:
    if not headers:
        return {}
    parsed: dict[str, str] = {}
    for line in headers.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def parse_string(input_string: str, value_format_fn: Callable[[str], str] | None = None) -> dict[str, object]:
    if not input_string:
        return {}
    matches: dict[str, object] = {}
    pattern = r"(\w+):\s*((?:(?!\n\w+:).)*)"
    for match in re.finditer(pattern, input_string):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if value_format_fn:
            value = value_format_fn(value)
        try:
            matches[key] = json.loads(value)
        except Exception:
            matches[key] = value
    return matches


def parse_body(body: str, content_type: str, value_format_fn: Callable[[str], str] | None = None) -> str | dict[str, object]:
    if not body:
        return ""
    if content_type == "text/plain":
        return value_format_fn(body) if value_format_fn else body

    try:
        raw = value_format_fn(body) if value_format_fn else body
        if content_type == "application/json":
            parsed = json.loads(raw)
            return json_dumps(parsed)
        if content_type == "application/x-www-form-urlencoded":
            parsed_form = parse_string(raw)
            return urllib.parse.urlencode(parsed_form, doseq=True)
        return raw
    except Exception:
        return value_format_fn(body) if value_format_fn else body


def _response_json(response) -> dict:
    try:
        return response.json()
    except Exception:
        return {}


def _validate_wxpusher() -> tuple[bool, str]:
    app_token = _env_first("WXPUSHER_APP_TOKEN", "WP_APP_TOKEN_ONE")
    receivers = _env_first("WXPUSHER_TOPIC_IDS") or _env_first("WXPUSHER_UIDS", "WP_UIDs")
    if not app_token:
        return False, "缺少 WXPUSHER_APP_TOKEN 或 WP_APP_TOKEN_ONE"
    if not receivers:
        return False, "缺少 WXPUSHER_TOPIC_IDS / WXPUSHER_UIDS / WP_UIDs"
    return True, ""


def _validate_wework_app() -> tuple[bool, str]:
    raw = _env_first("QYWX_AM")
    if not raw:
        return False, "缺少 QYWX_AM"
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if len(parts) not in {4, 5}:
        return False, "QYWX_AM 格式应为 corpid,corpsecret,touser,agentid[,media_id]"
    return True, ""


def _validate_dingtalk() -> tuple[bool, str]:
    if not _env_first("DD_BOT_TOKEN"):
        return False, "缺少 DD_BOT_TOKEN"
    return True, ""


def validate_channel_config(channel: str) -> tuple[bool, str]:
    validators: dict[str, Callable[[], tuple[bool, str]]] = {
        "gotify": lambda: (bool(_env_first("GOTIFY_URL") and _env_first("GOTIFY_TOKEN")), "缺少 GOTIFY_URL 或 GOTIFY_TOKEN"),
        "goCqHttpBot": lambda: (bool(_env_first("GOBOT_URL") and _env_first("GOBOT_QQ")), "缺少 GOBOT_URL 或 GOBOT_QQ"),
        "serverChan": lambda: (bool(_env_first("PUSH_KEY", "SERVERCHAN_SENDKEY")), "缺少 PUSH_KEY 或 SERVERCHAN_SENDKEY"),
        "pushDeer": lambda: (bool(_env_first("DEER_KEY", "PUSHDEER_KEY")), "缺少 DEER_KEY 或 PUSHDEER_KEY"),
        "bark": lambda: (bool(_env_first("BARK_PUSH")), "缺少 BARK_PUSH"),
        "chat": lambda: (bool(_env_first("CHAT_URL") and _env_first("CHAT_TOKEN")), "缺少 CHAT_URL 或 CHAT_TOKEN"),
        "telegramBot": lambda: (bool(_env_first("TG_BOT_TOKEN") and _env_first("TG_CHAT_ID", "TG_USER_ID")), "缺少 TG_BOT_TOKEN 或 TG_CHAT_ID/TG_USER_ID"),
        "dingtalkBot": _validate_dingtalk,
        "weWorkBot": lambda: (bool(_env_first("QYWX_KEY")), "缺少 QYWX_KEY"),
        "weWorkApp": _validate_wework_app,
        "aibotk": lambda: (bool(_env_first("AIBOTK_KEY") and _env_first("AIBOTK_TYPE") and _env_first("AIBOTK_NAME")), "缺少 AIBOTK_KEY/AIBOTK_TYPE/AIBOTK_NAME"),
        "iGot": lambda: (bool(_env_first("IGOT_PUSH_KEY")), "缺少 IGOT_PUSH_KEY"),
        "pushPlus": lambda: (bool(_env_first("PUSH_PLUS_TOKEN", "PUSHPLUS_TOKEN")), "缺少 PUSH_PLUS_TOKEN 或 PUSHPLUS_TOKEN"),
        "wePlusBot": lambda: (bool(_env_first("WE_PLUS_BOT_TOKEN")), "缺少 WE_PLUS_BOT_TOKEN"),
        "email": lambda: (bool(_env_first("SMTP_SERVER") and _env_first("SMTP_EMAIL") and _env_first("SMTP_PASSWORD") and _env_first("SMTP_NAME")), "缺少 SMTP_SERVER/SMTP_EMAIL/SMTP_PASSWORD/SMTP_NAME"),
        "pushMe": lambda: (bool(_env_first("PUSHME_KEY")), "缺少 PUSHME_KEY"),
        "feishu": lambda: (bool(_env_first("FEISHU_WEBHOOK", "FSKEY")), "缺少 FEISHU_WEBHOOK 或 FSKEY"),
        "webhook": lambda: (bool(_env_first("WEBHOOK_URL") and _env_first("WEBHOOK_METHOD")), "缺少 WEBHOOK_URL 或 WEBHOOK_METHOD"),
        "chronocat": lambda: (bool(_env_first("CHRONOCAT_URL") and _env_first("CHRONOCAT_QQ") and _env_first("CHRONOCAT_TOKEN")), "缺少 CHRONOCAT_URL/CHRONOCAT_QQ/CHRONOCAT_TOKEN"),
        "ntfy": lambda: (bool(_env_first("NTFY_URL") and _env_first("NTFY_TOPIC")), "缺少 NTFY_URL 或 NTFY_TOPIC"),
        "wxPusherBot": _validate_wxpusher,
    }
    validator = validators.get(channel)
    if not validator:
        return False, f"未注册渠道校验器: {channel}"
    return validator()


def send_bark(title: str, content: str) -> bool:
    bark_push = _env_first("BARK_PUSH")
    url = bark_push if bark_push.startswith("http") else f"https://api.day.app/{bark_push}"
    data = {"title": title, "body": content}
    option_mapping = {
        "BARK_ARCHIVE": "isArchive",
        "BARK_GROUP": "group",
        "BARK_SOUND": "sound",
        "BARK_ICON": "icon",
        "BARK_LEVEL": "level",
        "BARK_URL": "url",
    }
    for env_name, payload_name in option_mapping.items():
        value = _env_first(env_name)
        if value:
            data[payload_name] = value
    response = requests.post(
        url,
        data=json_dumps(data),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("code") == 200


def send_gotify(title: str, content: str) -> bool:
    url = f"{_env_first('GOTIFY_URL').rstrip('/')}/message?token={_env_first('GOTIFY_TOKEN')}"
    data = {
        "title": title,
        "message": content,
        "priority": _env_first("GOTIFY_PRIORITY") or 0,
    }
    response = requests.post(url, data=data, timeout=15)
    return bool(_response_json(response).get("id"))


def send_go_cqhttp(title: str, content: str) -> bool:
    url = _env_first("GOBOT_URL")
    params = {
        "message": f"标题:{title}\n内容:{content}",
    }
    token = _env_first("GOBOT_TOKEN")
    if token:
        params["access_token"] = token
    target = _env_first("GOBOT_QQ")
    if "=" in target:
        key, value = target.split("=", 1)
        params[key] = value
    response = requests.get(url, params=params, timeout=15)
    return _response_json(response).get("status") == "ok"


def send_server_chan(title: str, content: str) -> bool:
    send_key = _env_first("PUSH_KEY", "SERVERCHAN_SENDKEY")
    match = re.match(r"sctp(\d+)t", send_key)
    if match:
        url = f"https://{match.group(1)}.push.ft07.com/send/{send_key}.send"
    else:
        url = f"https://sctapi.ftqq.com/{send_key}.send"
    response = requests.post(
        url,
        data={"text": title, "desp": content.replace("\n", "\n\n")},
        timeout=15,
    )
    data = _response_json(response)
    return data.get("errno") == 0 or data.get("code") == 0


def send_pushdeer(title: str, content: str) -> bool:
    url = _env_first("DEER_URL") or "https://api2.pushdeer.com/message/push"
    response = requests.post(
        url,
        data={
            "text": title,
            "desp": content,
            "type": "markdown",
            "pushkey": _env_first("DEER_KEY", "PUSHDEER_KEY"),
        },
        timeout=15,
    )
    data = _response_json(response)
    return bool((data.get("content") or {}).get("result"))


def send_chat(title: str, content: str) -> bool:
    url = _env_first("CHAT_URL") + _env_first("CHAT_TOKEN")
    response = requests.post(
        url,
        data="payload=" + json_dumps({"text": f"{title}\n{content}"}),
        timeout=15,
    )
    return response.status_code == 200


def send_telegram(title: str, content: str) -> bool:
    api_host = _env_first("TG_API_HOST")
    token = _env_first("TG_BOT_TOKEN")
    url = f"{api_host}/bot{token}/sendMessage" if api_host else f"https://api.telegram.org/bot{token}/sendMessage"
    proxies = None
    proxy_host = _env_first("TG_PROXY_HOST")
    proxy_port = _env_first("TG_PROXY_PORT")
    proxy_auth = _env_first("TG_PROXY_AUTH")
    if proxy_host and proxy_port:
        if proxy_auth and "@" not in proxy_host:
            proxy_host = f"{proxy_auth}@{proxy_host}"
        proxy_str = f"http://{proxy_host}:{proxy_port}"
        proxies = {"http": proxy_str, "https": proxy_str}
    response = requests.post(
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        params={
            "chat_id": _env_first("TG_CHAT_ID", "TG_USER_ID"),
            # 表頭同內容之間唔留空行（通知瘦身）
            "text": f"{title}\n{content}",
            "disable_web_page_preview": "true",
        },
        proxies=proxies,
        timeout=15,
    )
    return bool(_response_json(response).get("ok"))


def send_dingtalk(title: str, content: str) -> bool:
    token = _env_first("DD_BOT_TOKEN")
    secret = _env_first("DD_BOT_SECRET")
    url = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        url = f"{url}&timestamp={timestamp}&sign={sign}"
    response = requests.post(
        url,
        data=json_dumps({"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}}),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("errcode") == 0


def send_wework_bot(title: str, content: str) -> bool:
    origin = _env_first("QYWX_ORIGIN") or "https://qyapi.weixin.qq.com"
    url = f"{origin}/cgi-bin/webhook/send?key={_env_first('QYWX_KEY')}"
    response = requests.post(
        url=url,
        data=json_dumps({"msgtype": "text", "text": {"content": f"{title}\n\n{content}"}}),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("errcode") == 0


def send_wework_app(title: str, content: str) -> bool:
    corpid, corpsecret, touser, agentid, *rest = [item.strip() for item in _env_first("QYWX_AM").split(",") if item.strip()]
    media_id = rest[0] if rest else ""
    origin = _env_first("QYWX_ORIGIN") or "https://qyapi.weixin.qq.com"
    token_response = requests.post(
        url=f"{origin}/cgi-bin/gettoken",
        params={"corpid": corpid, "corpsecret": corpsecret},
        timeout=15,
    )
    access_token = _response_json(token_response).get("access_token")
    if not access_token:
        return False

    send_url = f"{origin}/cgi-bin/message/send?access_token={access_token}"
    if media_id:
        payload = {
            "touser": touser,
            "msgtype": "mpnews",
            "agentid": agentid,
            "mpnews": {
                "articles": [
                    {
                        "title": title,
                        "thumb_media_id": media_id,
                        "author": "Author",
                        "content_source_url": "",
                        "content": content.replace("\n", "<br/>"),
                        "digest": content,
                    }
                ]
            },
        }
    else:
        payload = {
            "touser": touser,
            "msgtype": "text",
            "agentid": agentid,
            "text": {"content": f"{title}\n\n{content}"},
            "safe": "0",
        }
    response = requests.post(send_url, data=json_dumps(payload).encode("utf-8"), timeout=15)
    return _response_json(response).get("errmsg") == "ok"


def send_aibotk(title: str, content: str) -> bool:
    bot_type = _env_first("AIBOTK_TYPE")
    if bot_type == "room":
        url = "https://api-bot.aibotk.com/openapi/v1/chat/room"
        payload = {
            "apiKey": _env_first("AIBOTK_KEY"),
            "roomName": _env_first("AIBOTK_NAME"),
            "message": {"type": 1, "content": f"【青龙快讯】\n\n{title}\n{content}"},
        }
    else:
        url = "https://api-bot.aibotk.com/openapi/v1/chat/contact"
        payload = {
            "apiKey": _env_first("AIBOTK_KEY"),
            "name": _env_first("AIBOTK_NAME"),
            "message": {"type": 1, "content": f"【青龙快讯】\n\n{title}\n{content}"},
        }
    response = requests.post(
        url=url,
        data=json_dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("code") == 0


def send_igot(title: str, content: str) -> bool:
    response = requests.post(
        f"https://push.hellyw.com/{_env_first('IGOT_PUSH_KEY')}",
        data={"title": title, "content": content},
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("ret") == 0


def send_pushplus(title: str, content: str) -> bool:
    payload = {
        "token": _env_first("PUSH_PLUS_TOKEN", "PUSHPLUS_TOKEN"),
        "title": title,
        "content": content,
        "topic": _env_first("PUSH_PLUS_USER"),
        "template": _env_first("PUSH_PLUS_TEMPLATE") or "html",
        "channel": _env_first("PUSH_PLUS_CHANNEL") or "wechat",
        "webhook": _env_first("PUSH_PLUS_WEBHOOK"),
        "callbackUrl": _env_first("PUSH_PLUS_CALLBACKURL"),
        "to": _env_first("PUSH_PLUS_TO"),
    }
    body = json_dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    response = requests.post(url="https://www.pushplus.plus/send", data=body, headers=headers, timeout=15)
    result = _response_json(response)
    if result.get("code") == 200:
        return True
    fallback = requests.post(url="http://pushplus.hxtrip.com/send", data=body, headers=headers, timeout=15)
    return _response_json(fallback).get("code") == 200


def send_weplus_bot(title: str, content: str) -> bool:
    template = "html" if len(content) > 800 else "txt"
    payload = {
        "token": _env_first("WE_PLUS_BOT_TOKEN"),
        "title": title,
        "content": content,
        "template": template,
        "receiver": _env_first("WE_PLUS_BOT_RECEIVER"),
        "version": _env_first("WE_PLUS_BOT_VERSION") or "pro",
    }
    response = requests.post(
        url="https://www.weplusbot.com/send",
        data=json_dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("code") == 200


def send_email(title: str, content: str) -> bool:
    sender = _env_first("SMTP_EMAIL")
    receiver = _env_first("SMTP_TO_EMAIL") or sender
    message = MIMEText(content, "plain", "utf-8")
    display_name = _env_first("SMTP_NAME")
    message["From"] = formataddr((Header(display_name, "utf-8").encode(), sender))
    message["To"] = formataddr((Header(display_name, "utf-8").encode(), receiver))
    message["Subject"] = Header(title, "utf-8")
    smtp_ssl = _bool_env("SMTP_SSL", default=False)
    smtp_class = smtplib.SMTP_SSL if smtp_ssl else smtplib.SMTP
    smtp_server = smtp_class(_env_first("SMTP_SERVER"))
    try:
        smtp_server.login(sender, _env_first("SMTP_PASSWORD"))
        smtp_server.sendmail(sender, receiver, message.as_bytes())
    finally:
        smtp_server.close()
    return True


def send_pushme(title: str, content: str) -> bool:
    url = _env_first("PUSHME_URL") or "https://push.i-i.me/"
    response = requests.post(
        url,
        data={
            "push_key": _env_first("PUSHME_KEY"),
            "title": title,
            "content": content,
            "date": _env_first("date"),
            "type": _env_first("type"),
        },
        timeout=15,
    )
    return response.status_code == 200 and response.text == "success"


def send_feishu(title: str, content: str) -> bool:
    webhook = _env_first("FEISHU_WEBHOOK")
    if not webhook:
        webhook = f"https://open.feishu.cn/open-apis/bot/v2/hook/{_env_first('FSKEY')}"
    payload = {"msg_type": "text", "content": {"text": f"{title}\n\n{content}"}}
    secret = _env_first("FEISHU_SECRET", "FSSECRET")
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    response = requests.post(
        webhook,
        data=json_dumps(payload),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    result = _response_json(response)
    return result.get("StatusCode") == 0 or result.get("code") == 0


def send_webhook(title: str, content: str) -> bool:
    webhook_url = _env_first("WEBHOOK_URL")
    webhook_method = _env_first("WEBHOOK_METHOD")
    content_type = _env_first("WEBHOOK_CONTENT_TYPE") or "application/json"
    webhook_body = _env_first("WEBHOOK_BODY")
    webhook_headers = _env_first("WEBHOOK_HEADERS")

    formatter = lambda value: value.replace("$title", title.replace("\n", "\\n")).replace("$content", content.replace("\n", "\\n"))
    body = parse_body(webhook_body, content_type, formatter)
    headers = parse_headers(webhook_headers)
    if content_type and "Content-Type" not in headers and "content-type" not in {key.lower() for key in headers}:
        headers["Content-Type"] = f"{content_type}; charset=utf-8" if "charset" not in content_type.lower() else content_type
    formatted_url = webhook_url.replace("$title", urllib.parse.quote_plus(title)).replace("$content", urllib.parse.quote_plus(content))
    response = requests.request(
        method=webhook_method,
        url=formatted_url,
        headers=headers,
        timeout=15,
        data=body,
    )
    return 200 <= response.status_code < 300


def send_chronocat(title: str, content: str) -> bool:
    qq_config = _env_first("CHRONOCAT_QQ")
    user_ids = re.findall(r"user_id=(\d+)", qq_config)
    group_ids = re.findall(r"group_id=(\d+)", qq_config)
    url = f"{_env_first('CHRONOCAT_URL').rstrip('/')}/api/message/send"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {_env_first('CHRONOCAT_TOKEN')}",
    }
    all_success = True
    for chat_type, ids in ((1, user_ids), (2, group_ids)):
        for chat_id in ids:
            response = requests.post(
                url,
                headers=headers,
                data=json_dumps(
                    {
                        "peer": {"chatType": chat_type, "peerUin": chat_id},
                        "elements": [
                            {
                                "elementType": 1,
                                "textElement": {"content": f"{title}\n\n{content}"},
                            }
                        ],
                    }
                ),
                timeout=15,
            )
            if response.status_code != 200:
                all_success = False
    return all_success


def send_ntfy(title: str, content: str) -> bool:
    headers = {
        "Title": _rfc2047(title),
        "Priority": _env_first("NTFY_PRIORITY") or "3",
        "Icon": "https://qn.whyour.cn/logo.png",
    }
    token = _env_first("NTFY_TOKEN")
    username = _env_first("NTFY_USERNAME")
    password = _env_first("NTFY_PASSWORD")
    actions = _env_first("NTFY_ACTIONS")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif username and password:
        auth_string = f"{username}:{password}"
        headers["Authorization"] = "Basic " + base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
    if actions:
        headers["Actions"] = _rfc2047(actions)
    response = requests.post(
        f"{_env_first('NTFY_URL').rstrip('/')}/{_env_first('NTFY_TOPIC')}",
        data=content.encode("utf-8"),
        headers=headers,
        timeout=15,
    )
    return response.status_code == 200


def send_wxpusher(title: str, content: str) -> bool:
    app_token = _env_first("WXPUSHER_APP_TOKEN", "WP_APP_TOKEN_ONE")
    topic_ids = [
        int(item)
        for item in _split_values(_env_first("WXPUSHER_TOPIC_IDS"))
        if item.isdigit()
    ]
    uids = _split_values(_env_first("WXPUSHER_UIDS", "WP_UIDs"))
    payload = {
        "appToken": app_token,
        "content": f"<h1>{escape(title)}</h1><br/><div style='white-space: pre-wrap;'>{escape(content)}</div>",
        "summary": title,
        "contentType": 2,
        "topicIds": topic_ids,
        "uids": uids,
        "verifyPayType": 0,
    }
    response = requests.post(
        "https://wxpusher.zjiecode.com/api/send/message",
        json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=15,
    )
    return _response_json(response).get("code") == 1000


SENDERS: dict[str, Callable[[str, str], bool]] = {
    "gotify": send_gotify,
    "goCqHttpBot": send_go_cqhttp,
    "serverChan": send_server_chan,
    "pushDeer": send_pushdeer,
    "bark": send_bark,
    "chat": send_chat,
    "telegramBot": send_telegram,
    "dingtalkBot": send_dingtalk,
    "weWorkBot": send_wework_bot,
    "weWorkApp": send_wework_app,
    "aibotk": send_aibotk,
    "iGot": send_igot,
    "pushPlus": send_pushplus,
    "wePlusBot": send_weplus_bot,
    "email": send_email,
    "pushMe": send_pushme,
    "feishu": send_feishu,
    "webhook": send_webhook,
    "chronocat": send_chronocat,
    "ntfy": send_ntfy,
    "wxPusherBot": send_wxpusher,
}


def send_notify(title: str, content: str) -> bool:
    channel = normalize_channel(os.environ.get("NOTIFY_CHANNEL"))
    if channel not in OFFICIAL_CHANNELS:
        _log(f"不支持的通知渠道: {channel}")
        return False
    is_valid, reason = validate_channel_config(channel)
    if not is_valid:
        _log(f"通知渠道 {channel} 缺少必要配置，跳过推送：{reason}")
        return False

    try:
        ok = SENDERS[channel](title, content)
    except Exception as exc:
        _log(f"通知渠道 {channel} 推送失败：{exc}")
        return False

    if ok:
        _log(f"通知渠道 {channel} 推送成功")
    else:
        _log(f"通知渠道 {channel} 推送失败")
    return ok
