# HidenCloud Auto Renew（Python 版）

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Automated-green.svg)
![License](https://img.shields.io/badge/License-MIT-yellow.svg)

基于 Python 编写的 HidenCloud（海敦云）自动续期与支付脚本，专为 GitHub Actions 设计。

✨ **核心亮点：**

- 自动续期 + 自动支付
- 使用 Infinicloud（WebDAV）持久化 Cookie
- 支持 **青龙风格全渠道通知**
- 运行时采用 **单选渠道**
- **默认使用 wxpusher**
- 中文日志与通知正文按 **UTF-8** 处理，尽量避免乱码

---

## 功能特性

- **自动续期**：自动检测服务并延长有效期
- **自动支付**：续期后自动识别账单并支付
- **Cookie 持久化**：
  - 自动将最新 Cookie 上传到 Infinicloud（WebDAV）
  - 脚本运行时优先读取云端缓存，减少 Cookie 失效影响
- **单选通知渠道**：
  - 默认 `wxpusher`
  - 支持青龙风格通知渠道：
    - `wxpusher`
    - `serverchan`
    - `telegram`
    - `dingtalk`
    - `wework_bot`
    - `wework_app`
    - `feishu`
    - `bark`
    - `pushplus`
    - `gotify`
    - `gocqhttp`
    - `pushdeer`
    - `chat`
    - `aibotk`
    - `igot`
    - `weplusbot`
    - `email`
    - `pushme`
    - `webhook`
    - `chronocat`
    - `ntfy`
- **多账号支持**：支持多个账号批量运行
- **失败重试**：支持 GitHub Actions 二次重跑

---

## 部署指南

### 1. 准备工作

- GitHub 账号
- HidenCloud 账号
- Infinicloud 账号：用于存储 Cookie JSON 文件
- 至少一个通知渠道账号/机器人配置（默认推荐 wxpusher）

### 2. 获取 HidenCloud Cookie

1. 浏览器打开 HidenCloud 控制台并登录
2. 按 `F12` 打开开发者工具，切到 **Network**
3. 刷新页面，点击任意已登录请求
4. 在请求头中复制完整 `Cookie`

### 3. 获取 Infinicloud WebDAV 信息

1. 登录 Infinicloud
2. 进入 **My Page**
3. 开启 **Apps Connection**
4. 记录：
   - `WEBDAV_URL`
   - `WEBDAV_USER`
   - `WEBDAV_PASS`

---

## 通知说明

### 运行模式

- 只发送 **一个** 通知渠道
- 通过 `NOTIFY_CHANNEL` 指定渠道
- **不填 `NOTIFY_CHANNEL` 时默认使用 `wxpusher`**

### 渠道选择示例

将 `NOTIFY_CHANNEL` 配置为以下任一值：

| 值 | 说明 |
|---|---|
| `wxpusher` | 默认渠道 |
| `serverchan` | Server 酱 |
| `telegram` | Telegram Bot |
| `dingtalk` | 钉钉机器人 |
| `wework_bot` | 企业微信机器人 |
| `wework_app` | 企业微信应用 |
| `feishu` | 飞书机器人 |
| `bark` | Bark |
| `pushplus` | PushPlus |
| `gotify` | Gotify |
| `gocqhttp` | go-cqhttp |
| `pushdeer` | PushDeer |
| `chat` | Synology Chat |
| `aibotk` | 智能微秘书 |
| `igot` | iGot |
| `weplusbot` | 微加机器人 |
| `email` | SMTP 邮件 |
| `pushme` | PushMe |
| `webhook` | 自定义 Webhook |
| `chronocat` | Chronocat |
| `ntfy` | ntfy |

> 建议把 `NOTIFY_CHANNEL` 放在 GitHub **Repository Variables** 中，其余敏感值放在 **Repository Secrets** 中。

### WxPusher 兼容说明

当前项目兼容两套 WxPusher 变量：

- **旧版项目变量**
  - `WP_APP_TOKEN_ONE`
  - `WP_UIDs`
- **青龙风格变量**
  - `WXPUSHER_APP_TOKEN`
  - `WXPUSHER_TOPIC_IDS`
  - `WXPUSHER_UIDS`

如果你以前已经在用本项目的 WxPusher 配置，**升级后可以不改配置继续使用**。

---

## GitHub Actions 配置

### 1. 必填配置

#### Repository Secrets

| 名称 | 是否必填 | 说明 |
|---|:---:|---|
| `HIDEN_COOKIE` | ✅ | 初始 Cookie。多账号用换行或 `&` 分隔 |
| `WEBDAV_URL` | ✅ | Infinicloud WebDAV 地址 |
| `WEBDAV_USER` | ✅ | Infinicloud 用户名 |
| `WEBDAV_PASS` | ✅ | Infinicloud 密码 |

#### Repository Variables

| 名称 | 是否必填 | 说明 |
|---|:---:|---|
| `NOTIFY_CHANNEL` | ❌ | 选中的通知渠道；不填默认 `wxpusher` |

### 2. 各通知渠道配置

只需要配置**当前所选渠道**对应的变量即可。

#### wxpusher

| 名称 | 说明 |
|---|---|
| `WP_APP_TOKEN_ONE` | 旧版 app token |
| `WP_UIDs` | 旧版 UID，多个用 `;` 分隔 |
| `WXPUSHER_APP_TOKEN` | 青龙风格 app token |
| `WXPUSHER_TOPIC_IDS` | 主题 ID，多个用 `;` 分隔 |
| `WXPUSHER_UIDS` | UID，多个用 `;` 分隔 |

#### Server 酱

| 名称 | 说明 |
|---|---|
| `PUSH_KEY` | 青龙常用变量 |
| `SERVERCHAN_SENDKEY` | 兼容别名 |

#### Telegram

| 名称 | 说明 |
|---|---|
| `TG_BOT_TOKEN` | 机器人 Token |
| `TG_CHAT_ID` | 推荐使用 |
| `TG_USER_ID` | 兼容旧写法 |
| `TG_API_HOST` | 可选，自定义 API Host |
| `TG_PROXY_AUTH` / `TG_PROXY_HOST` / `TG_PROXY_PORT` | 可选，代理配置 |

#### 钉钉

| 名称 | 说明 |
|---|---|
| `DD_BOT_TOKEN` | 机器人 Token |
| `DD_BOT_SECRET` | 可选，签名密钥 |

#### 企业微信机器人

| 名称 | 说明 |
|---|---|
| `QYWX_KEY` | 机器人 key |
| `QYWX_ORIGIN` | 可选，企业微信代理地址 |

#### 企业微信应用

| 名称 | 说明 |
|---|---|
| `QYWX_AM` | `corpid,corpsecret,touser,agentid[,media_id]` |
| `QYWX_ORIGIN` | 可选，企业微信代理地址 |

#### 飞书

| 名称 | 说明 |
|---|---|
| `FEISHU_WEBHOOK` | 直接 webhook 地址 |
| `FEISHU_SECRET` | 可选，签名密钥 |
| `FSKEY` | 兼容青龙旧变量 |
| `FSSECRET` | 兼容青龙旧变量 |

#### Bark

| 名称 | 说明 |
|---|---|
| `BARK_PUSH` | Bark 地址或设备码 |
| `BARK_ARCHIVE` / `BARK_GROUP` / `BARK_SOUND` / `BARK_ICON` / `BARK_LEVEL` / `BARK_URL` | 可选参数 |

#### PushPlus

| 名称 | 说明 |
|---|---|
| `PUSH_PLUS_TOKEN` | 青龙常用变量 |
| `PUSHPLUS_TOKEN` | 兼容别名 |
| `PUSH_PLUS_USER` | 群组编码 |
| `PUSH_PLUS_TEMPLATE` | 模板类型 |
| `PUSH_PLUS_CHANNEL` | 渠道 |
| `PUSH_PLUS_WEBHOOK` | webhook 编码 |
| `PUSH_PLUS_CALLBACKURL` | 回调地址 |
| `PUSH_PLUS_TO` | 好友令牌 / 企业微信用户 ID |

#### 其他渠道

| 渠道 | 变量 |
|---|---|
| `gotify` | `GOTIFY_URL` `GOTIFY_TOKEN` `GOTIFY_PRIORITY` |
| `gocqhttp` | `GOBOT_URL` `GOBOT_QQ` `GOBOT_TOKEN` |
| `pushdeer` | `DEER_KEY` / `PUSHDEER_KEY`，可选 `DEER_URL` |
| `chat` | `CHAT_URL` `CHAT_TOKEN` |
| `aibotk` | `AIBOTK_KEY` `AIBOTK_TYPE` `AIBOTK_NAME` |
| `igot` | `IGOT_PUSH_KEY` |
| `weplusbot` | `WE_PLUS_BOT_TOKEN` `WE_PLUS_BOT_RECEIVER` `WE_PLUS_BOT_VERSION` |
| `email` | `SMTP_SERVER` `SMTP_SSL` `SMTP_EMAIL` `SMTP_PASSWORD` `SMTP_NAME`，可选 `SMTP_TO_EMAIL` |
| `pushme` | `PUSHME_KEY`，可选 `PUSHME_URL` |
| `webhook` | `WEBHOOK_URL` `WEBHOOK_METHOD` `WEBHOOK_BODY` `WEBHOOK_HEADERS` `WEBHOOK_CONTENT_TYPE` |
| `chronocat` | `CHRONOCAT_QQ` `CHRONOCAT_TOKEN` `CHRONOCAT_URL` |
| `ntfy` | `NTFY_URL` `NTFY_TOPIC`，可选 `NTFY_PRIORITY` `NTFY_TOKEN` `NTFY_USERNAME` `NTFY_PASSWORD` `NTFY_ACTIONS` |

---

## 启动运行

1. Fork 本项目
2. 配置好 `Secrets` 与 `Variables`
3. 打开 GitHub 仓库的 **Actions**
4. 选择 **HidenCloud Auto Renew (Python)**
5. 点击 **Run workflow**
6. 检查：
   - Infinicloud 是否生成 `hiden_cookies.json`
   - 通知是否成功送达

脚本默认按 GitHub Actions 定时运行，并会在失败时延迟再试一次。

---

## 文件结构

```text
.
├── .github/workflows/
│   ├── cron.yml
│   └── main.yml
├── main.py
├── notify.py
├── requirements.txt
├── tests/
│   └── test_notify.py
└── README.md
```

---

## 编码与中文输出

项目已按 UTF-8 处理中文内容：

- Python 文件使用 UTF-8
- GitHub Actions 中设置：
  - `PYTHONUTF8=1`
  - `PYTHONIOENCODING=UTF-8`
- 通知 JSON / 文本正文尽量使用 UTF-8 发送

---

## 免责声明

1. 本项目仅供学习交流使用，请勿用于非法用途
2. 脚本涉及账号操作，作者不对由此造成的损失负责
3. 请妥善保管你的 Secrets 与 Variables
4. 建议定期检查 Cookie 状态与通知配置

---

如果这个项目对你有帮助，欢迎点个 Star ⭐

---

## ⚠️ 續期模式說明（2026-10 起）

面板 renew form 已加 **Cloudflare Turnstile**：

```html
<form id="renew-form-<id>" action="/service/<id>/renew" method="POST"
      onsubmit="return hidenRenewCaptchaGuard(this, 'renew-captcha-wait-<id>')">
  <div class="cf-turnstile" data-sitekey="0x4AAAAAAAPmSriFX9MUUlyZ"> ... </div>
</form>
```

純 HTTP 提交（無 Turnstile token）一律被拒：
`Info Error! The cf-turnstile-response field is required.`

而 GitHub Actions runner IP 上就算用真 headless Chrome 都過唔到（實測 2026-10-05）：
- uc / undetected 模式：`window.turnstile` 存在、widget 喺 modal 入面且可見，但 **CF iframe 從頭到尾冇建立**，token 恆空
- plain headless 模式：直接撞 Cloudflare interstitial（`Just a moment...` / `chl_page/v1`）

所以排程預設 **watchdog 模式**：只讀面板到期狀態，唔提交續期。

| 模式 | 行為 | 點用 |
| --- | --- | --- |
| `watchdog`（預設） | 讀到期日；剩 ≤ `alert_days` 日就 TG 叫人 | 排程／gha_keeper 每日自動 |
| `renew` | 嘗試自動續期（需要面板有 bypass 先至有效） | workflow_dispatch 手動揀 `mode=renew` |

環境變數：`HC_MODE`（`watchdog` / `renew`）、`HC_ALERT_DAYS`（預設 `1`）。

**人手續期**：登入 `https://dash.hidencloud.com/service/<id>/manage` → 撳 **Renew** → 過 Turnstile → **Create Invoice**（免費服務 1 Week = €0.00）。
