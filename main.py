# -*- coding: utf-8 -*-
"""
HidenCloud 自动续期 - Python 全日志推送版
"""
import os
import sys
import time
import json
import random
import re
import requests
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote
from notify import build_summary, send_notify
try:
    from cookie_context import normalize_cookie_records, parse_seed_cookie_string, success_path_label
except ModuleNotFoundError:
    DEFAULT_COOKIE_DOMAIN = '.dash.hidencloud.com'
    COOKIE_DOMAIN_OVERRIDES = {
        'cf_clearance': '.hidencloud.com',
    }
    CRITICAL_COOKIE_NAMES = {
        'XSRF-TOKEN',
        'hidencloud_session',
        'cf_clearance',
        'hc_cf_turnstile',
    }
    CRITICAL_COOKIE_PREFIXES = (
        'remember_web_',
    )

    def _domain_for_cookie(name):
        return COOKIE_DOMAIN_OVERRIDES.get(name, DEFAULT_COOKIE_DOMAIN)

    def _is_critical_cookie_name(name):
        if name in CRITICAL_COOKIE_NAMES:
            return True
        return any(name.startswith(prefix) for prefix in CRITICAL_COOKIE_PREFIXES)

    def parse_seed_cookie_string(cookie_str):
        deduped = {}
        for item in cookie_str.split(';'):
            if '=' not in item:
                continue
            name, value = item.split('=', 1)
            name = name.strip()
            value = value.strip()
            if not name:
                continue
            deduped[name] = {
                'name': name,
                'value': value,
                'domain': _domain_for_cookie(name),
                'path': '/',
                'secure': True,
            }
        return list(deduped.values())

    def _cookie_score(record):
        name = str(record.get('name') or '')
        domain = str(record.get('domain') or '')
        path = str(record.get('path') or '/')
        secure = 1 if record.get('secure') else 0
        preferred_domain = _domain_for_cookie(name)
        domain_match = 1 if preferred_domain in domain else 0
        non_empty_domain = 1 if domain else 0
        return (domain_match, non_empty_domain, len(path), secure)

    def normalize_cookie_records(records):
        kept_by_name = {}
        changes = []
        ordered_passthrough = []

        for record in records:
            name = str(record.get('name') or '')
            if not _is_critical_cookie_name(name):
                ordered_passthrough.append(record)
                continue

            current = kept_by_name.get(name)
            if current is None:
                kept_by_name[name] = record
                continue

            if _cookie_score(record) >= _cookie_score(current):
                changes.append({'name': name, 'dropped': current, 'kept': record})
                kept_by_name[name] = record
            else:
                changes.append({'name': name, 'dropped': record, 'kept': current})

        normalized = ordered_passthrough + list(kept_by_name.values())
        return normalized, changes

    def success_path_label(stage, rebuild_retry=False):
        if stage == 'first_submit':
            return '重建会话后首次提交进入成功路径' if rebuild_retry else '首次提交进入成功路径'
        if stage == 'same_session_retry':
            return '重建会话后重试进入成功路径' if rebuild_retry else '同会话重试后进入成功路径'
        return '进入成功路径'

# ================= 配置常量 =================
RENEW_DAYS = 7
CACHE_FILE_NAME = 'hiden_cookies.json'
LOCAL_CACHE_PATH = os.path.join(os.path.dirname(__file__), CACHE_FILE_NAME)

# ================= 全局日志收集器 =================
ALL_LOGS = []

def log_print(msg):
    print(msg)
    ALL_LOGS.append(str(msg))

# ================= WebDAV 模块 =================
class WebDavManager:
    def __init__(self):
        self.url = os.environ.get("WEBDAV_URL", "")
        self.user = os.environ.get("WEBDAV_USER")
        self.password = os.environ.get("WEBDAV_PASS")

        if self.url and not self.url.endswith('/'):
            self.url += '/'
        self.full_url = self.url + CACHE_FILE_NAME if self.url else ""

    def download(self):
        if not self.url or not self.user:
            log_print("⚠️ 未配置 WebDAV，跳过云端同步")
            return

        log_print("☁️ 正在从 Infinicloud 下载缓存...")
        try:
            res = requests.get(self.full_url, auth=(self.user, self.password), timeout=30)
            if res.status_code == 200:
                with open(LOCAL_CACHE_PATH, 'w', encoding='utf-8') as f:
                    f.write(res.text)
                log_print("✅ 云端缓存下载成功")
            elif res.status_code == 404:
                log_print("⚪ 云端暂无缓存文件 (首次运行)")
            else:
                log_print(f"⚠️ 下载失败，状态码: {res.status_code}")
        except Exception as e:
            log_print(f"❌ WebDAV 下载错误: {e}")

    def upload(self, data):
        if not self.url or not self.user:
            return

        log_print("☁️ 正在上传最新缓存到 Infinicloud...")
        try:
            json_str = json.dumps(data, indent=2)
            res = requests.put(
                self.full_url,
                data=json_str,
                auth=(self.user, self.password),
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            if res.status_code in [200, 201, 204]:
                log_print("✅ 云端缓存上传成功")
            else:
                log_print(f"❌ WebDAV 上传失败: {res.status_code}")
        except Exception as e:
            log_print(f"❌ WebDAV 上传错误: {e}")

# ================= 辅助工具 =================
def sleep_random(min_ms=3000, max_ms=8000):
    sec = random.randint(min_ms, max_ms) / 1000.0
    time.sleep(sec)

def service_result(status, detail=""):
    """單個服務嘅通知結果（status: ok / skip / bad），餵畀 notify.build_summary。"""
    return {"status": status, "detail": detail}

class CacheManager:
    @staticmethod
    def load():
        if os.path.exists(LOCAL_CACHE_PATH):
            try:
                with open(LOCAL_CACHE_PATH, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                log_print("读取本地缓存失败")
        return {}

    @staticmethod
    def update(index, cookie_str, upload=True):
        """只在内容真正变化时才写盘/上传，减少无效 WebDAV 请求。"""
        data = CacheManager.load()
        key = str(index)

        if data.get(key) == cookie_str:
            return  # 无变化，跳过

        data[key] = cookie_str
        with open(LOCAL_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        log_print(f"💾 [账号 {index + 1}] 本地缓存已更新")

        if upload:
            WebDavManager().upload(data)

# ================= 核心机器人类 =================
class HidenCloudBot:
    def __init__(self, env_cookie, index):
        self.index = index + 1
        self.base_url = "https://dash.hidencloud.com"
        self.env_cookie = env_cookie
        self.session = self.create_session()

        self.csrf_token = ""
        self.services = []
        # 跨服务去重集合，避免对同一张账单反复尝试支付
        self.processed_invoices = set()
        # 本轮运行中已确认当前页面不可支付的账单，避免每个服务重复打开
        self.non_payable_invoices = set()
        # 标记本账号本轮是否建议由 GitHub Actions 稍后重跑一次
        self.retry_needed = False

        cached_data = CacheManager.load()
        cached_cookie = cached_data.get(str(index))

        if cached_cookie:
            log_print(f"[账号 {self.index}] 发现本地缓存 Cookie，优先使用...")
            self.load_cookie_str(cached_cookie)
        else:
            log_print(f"[账号 {self.index}] 使用环境变量 Cookie...")
            self.load_cookie_str(env_cookie)

    def log(self, msg):
        log_print(f"[账号 {self.index}] {msg}")

    def mark_retry_needed(self, reason):
        self.retry_needed = True
        if reason:
            self.log(f"🔁 标记本轮任务需要重试: {reason}")

    def create_session(self):
        return cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

    def load_cookie_str(self, cookie_str):
        if not cookie_str:
            return
        for cookie in parse_seed_cookie_string(cookie_str):
            self.session.cookies.set_cookie(
                requests.cookies.create_cookie(
                    name=cookie['name'],
                    value=cookie['value'],
                    domain=cookie['domain'],
                    path=cookie.get('path', '/'),
                    secure=bool(cookie.get('secure', True)),
                )
            )

    def get_cookie_str(self):
        return '; '.join([f"{c.name}={c.value}" for c in self.session.cookies])

    def normalize_critical_cookies(self, stage=""):
        records = []
        for cookie in self.session.cookies:
            records.append({
                'name': cookie.name,
                'value': cookie.value,
                'domain': cookie.domain or '',
                'path': cookie.path or '/',
                'secure': bool(cookie.secure),
                'expires': cookie.expires,
                'rest': getattr(cookie, '_rest', {}) or {},
            })

        normalized, changes = normalize_cookie_records(records)
        if not changes:
            return False

        new_jar = requests.cookies.RequestsCookieJar()
        for record in normalized:
            new_jar.set_cookie(
                requests.cookies.create_cookie(
                    name=record['name'],
                    value=record['value'],
                    domain=record.get('domain', ''),
                    path=record.get('path', '/'),
                    secure=bool(record.get('secure', False)),
                    expires=record.get('expires'),
                    rest=record.get('rest', {}),
                )
            )
        self.session.cookies = new_jar

        changed_names = []
        for change in changes:
            name = change['name']
            if name not in changed_names:
                changed_names.append(name)
        stage_text = f"{stage} " if stage else ""
        self.log(f"[COOKIE_NORMALIZED] {stage_text}检测到并归一化关键 Cookie: {', '.join(changed_names)}")
        return True

    def find_cookie_value(self, *names, preferred_domain=''):
        matches = []
        target_names = set(names)

        for cookie in self.session.cookies:
            if cookie.name in target_names and cookie.value:
                matches.append(cookie)

        if not matches:
            return ""

        if preferred_domain:
            domain_matches = [
                cookie for cookie in matches
                if cookie.domain and preferred_domain in cookie.domain
            ]
            if domain_matches:
                matches = domain_matches

        # requests 在同名 cookie 上可能抛 CookieConflictError，这里手动挑一个最合适的值。
        matches.sort(key=lambda cookie: (len(cookie.domain or ''), len(cookie.path or '')))
        return matches[-1].value

    def save_cookies(self, upload=True):
        """由关键操作节点显式调用，而非每次请求都触发。"""
        CacheManager.update(self.index - 1, self.get_cookie_str(), upload=upload)

    def reset_to_env(self, env_cookie):
        self.session.cookies.clear()
        self.load_cookie_str(env_cookie)
        self.log("切换回环境变量原始 Cookie 重试...")

    def rebuild_session(self, cookie_str=None):
        self.session = self.create_session()
        self.csrf_token = ""
        if cookie_str:
            self.load_cookie_str(cookie_str)

    def rebuild_session_and_reinit(self):
        current_cookie = self.get_cookie_str()
        self.log("♻️ 重建会话并重新验证登录状态...")
        self.rebuild_session(current_cookie)

        if self.init():
            return True, 'invoice_page'

        self.log("⚠️ 当前 Cookie 重建会话后初始化失败，回退环境变量 Cookie 再试一次...")
        self.rebuild_session()
        self.load_cookie_str(self.env_cookie)
        return self.init()

    def request(self, method, url, data=None, headers=None):
        full_url = urljoin(self.base_url, url)
        try:
            resp = self.session.request(method, full_url, data=data, headers=headers, timeout=30)
            self.normalize_critical_cookies(f"{method} {url}")
            return resp
        except Exception as e:
            self.log(f"请求异常: {e}")
            raise

    def _refresh_csrf(self, soup):
        """从页面 HTML 中刷新 CSRF token，防止因 token 过期导致 419 错误。"""
        token_tag = soup.find('meta', attrs={'name': 'csrf-token'})
        if token_tag:
            self.csrf_token = token_tag['content']
            return
        # 降级：从表单 _token 字段读取
        token_input = soup.find('input', attrs={'name': '_token'})
        if token_input:
            self.csrf_token = token_input['value']

    def normalize_url(self, url):
        return urljoin(self.base_url, url)

    def has_invoice_payment_context(self, text):
        normalized = re.sub(r'\s+', ' ', text or '').strip().lower()
        if not normalized:
            return False

        positive_keywords = [
            'unpaid', 'pending', 'pay now', 'payment due', 'pay invoice',
            'pendiente', 'pagar ahora', 'sin pagar',
            '未支付', '待支付', '待付款', '立即支付', '去支付', '付款', '支付'
        ]
        negative_keywords = [
            'paid', 'completed', 'cancelled', 'canceled', 'refunded',
            'pagado', 'completado', 'cancelado', 'reembolsado',
            '已支付', '已付款', '已完成', '已取消', '已退款', '作废'
        ]

        has_positive = any(self.contains_context_keyword(normalized, keyword) for keyword in positive_keywords)
        has_negative = any(self.contains_context_keyword(normalized, keyword) for keyword in negative_keywords)
        return has_positive and not has_negative

    def contains_context_keyword(self, normalized_text, keyword):
        if re.search(r'[a-z0-9]', keyword):
            pattern = rf'(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])'
            return re.search(pattern, normalized_text) is not None
        return keyword in normalized_text

    def extract_invoice_links(self, soup, require_payment_context=False):
        invoice_links = []

        if require_payment_context:
            containers = soup.find_all(['tr', 'li', 'div', 'article', 'section'])
            for container in containers:
                links = []
                for a in container.find_all('a', href=True):
                    href = a['href']
                    if '/invoice/' in href and 'download' not in href:
                        links.append(self.normalize_url(href))

                if not links:
                    continue

                container_text = container.get_text(" ", strip=True)
                if self.has_invoice_payment_context(container_text):
                    invoice_links.extend(links)

            return sorted(set(invoice_links))

        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/invoice/' in href and 'download' not in href:
                invoice_links.append(self.normalize_url(href))
        return sorted(set(invoice_links))

    def extract_server_error_message(self, soup):
        selectors = [
            '[role="alert"]',
            '[class*="alert"]',
            '[class*="text-red"]',
            '[class*="text-danger"]',
            '[class*="bg-red"]',
            '[class*="border-red"]',
            '[class*="error"]',
        ]
        ignored_phrases = [
            'this action is irreversible',
            'permanently deleted',
            'delete server',
            'sign out',
        ]
        error_keywords = [
            'error', 'failed', 'failure', 'must ', 'cannot', 'not allowed',
            'restricted', 'connect your discord', 'only renew',
            '错误', '失败', '无法', '不能', '不允许', '受限'
        ]

        for element in soup.select(','.join(selectors)):
            text = re.sub(r'\s+', ' ', element.get_text(" ", strip=True)).strip()
            if not text:
                continue

            normalized = text.lower()
            if any(phrase in normalized for phrase in ignored_phrases):
                continue
            if any(keyword in normalized for keyword in error_keywords):
                return text

        return ""

    def extract_form_payload(self, form):
        payload = {}

        for field in form.find_all(['input', 'select', 'textarea']):
            name = field.get('name')
            if not name or field.has_attr('disabled'):
                continue

            tag_name = field.name.lower()
            if tag_name == 'input':
                field_type = (field.get('type') or '').lower()
                if field_type in ('checkbox', 'radio') and not field.has_attr('checked'):
                    continue
                payload[name] = field.get('value', '')
            elif tag_name == 'select':
                option = field.find('option', selected=True) or field.find('option')
                payload[name] = option.get('value', '') if option else ''
            else:
                payload[name] = field.get_text()

        return payload

    def find_renew_form(self, soup, service_id):
        exact_path = f"/service/{service_id}/renew"
        fallback_form = None
        fallback_action = ""

        for form in soup.find_all('form'):
            action = form.get('action', '')
            if not action:
                continue

            action_url = self.normalize_url(action)
            if exact_path in action_url:
                return form, action_url

            form_text = form.get_text(" ", strip=True)
            if '/renew' in action_url or 'renew' in form_text.lower() or '续期' in form_text:
                fallback_form = form
                fallback_action = action_url

        return fallback_form, fallback_action

    def fetch_manage_page(self, service_id):
        manage_res = self.request('GET', f"/service/{service_id}/manage")
        soup = BeautifulSoup(manage_res.text, 'html.parser')
        self._refresh_csrf(soup)
        return manage_res, soup

    def submit_renew_request(self, service_id, soup, referer_url):
        form, action_url = self.find_renew_form(soup, service_id)
        payload = self.extract_form_payload(form) if form else {}

        token_input = soup.find('input', attrs={'name': '_token'})
        if token_input and not payload.get('_token'):
            payload['_token'] = token_input.get('value', '')

        payload['days'] = RENEW_DAYS

        target_url = action_url or self.normalize_url(f"/service/{service_id}/renew")
        xsrf_cookie = self.find_cookie_value(
            'XSRF-TOKEN',
            'XSRF_TOKEN',
            'csrf_token',
            preferred_domain='dash.hidencloud.com'
        )
        headers = {
            'X-CSRF-TOKEN': self.csrf_token,
            'Referer': referer_url,
            'Origin': self.base_url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        if xsrf_cookie:
            headers['X-XSRF-TOKEN'] = unquote(xsrf_cookie)
        return self.request('POST', target_url, data=payload, headers=headers)

    def try_handle_invoice_from_response(self, service_id, response, allow_invoice_poll=True):
        if '/invoice/' in response.url:
            self.log("⚡️ 续期成功，已跳转账单页，自动执行支付...")
            pay_result = self.perform_pay_from_html(response.text, response.url)
            if pay_result in {'paid', 'already_processed'}:
                return True, 'invoice_page'
            if not allow_invoice_poll:
                return False, None
            self.log("⚠️ 跳转账单页当前不可支付，改为检查当前服务未付账单...")
            invoice_polled = self.check_and_pay_invoices(service_id, is_precheck=False, retries=6, retry_delay=8)
            if invoice_polled:
                return True, 'invoice_poll'
            return False, None

        soup_resp = BeautifulSoup(response.text, 'html.parser')
        server_error = self.extract_server_error_message(soup_resp)
        if server_error:
            self.log(f"⚠️ 续期请求被服务端拒绝，页面提示: {server_error}")
            return True, 'server_reject'

        invoice_links = self.extract_invoice_links(soup_resp, require_payment_context=False)
        if invoice_links:
            for invoice_url in invoice_links:
                self.log(f"🔗 在响应HTML中发现账单链接: {invoice_url}")
                pay_result = self.pay_single_invoice(invoice_url)
                if pay_result in {'paid', 'already_processed'}:
                    return True, 'invoice_link'

            if not allow_invoice_poll:
                return False, None

            self.log("⚠️ 响应中的账单链接均不可支付，改为检查当前服务未付账单...")
            invoice_polled = self.check_and_pay_invoices(service_id, is_precheck=False, retries=6, retry_delay=8)
            if invoice_polled:
                return True, 'invoice_poll'
            return False, None

        err_div = soup_resp.find('div', class_=re.compile(r'(alert-danger|text-danger|error)'))
        if err_div:
            self.log(f"⚠️ 续期请求被服务端拒绝，页面提示: {err_div.get_text(strip=True)}")
            return True, 'server_reject'

        if not allow_invoice_poll:
            return False, None

        if response.status_code == 419:
            self.log("⚠️ 续期请求返回 419，重试后仍未跳转，开始检查是否生成账单...")
        else:
            self.log(f"⚠️ 提交成功但未自动跳转，响应URL: {response.url} | 状态码: {response.status_code}")
            self.log("后置轮询检查账单...")

        invoice_polled = self.check_and_pay_invoices(service_id, is_precheck=False, retries=6, retry_delay=8)
        if invoice_polled:
            return True, 'invoice_poll'
        return False, None

    def init(self):
        self.log("正在验证登录状态...")
        try:
            res = self.request('GET', '/dashboard')

            if '/login' in res.url:
                self.log("❌ 当前 Cookie 已失效")
                return False

            soup = BeautifulSoup(res.text, 'html.parser')
            log_print(f"👀 [调试] 网页标题是: {soup.title.string if soup.title else '无标题'}")

            self._refresh_csrf(soup)

            self.services = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/service/' in href and '/manage' in href:
                    svc_id = href.split('/service/')[1].split('/')[0]
                    if not any(s['id'] == svc_id for s in self.services):
                        self.services.append({'id': svc_id, 'url': href})

            self.log(f"✅ 登录成功，发现 {len(self.services)} 个服务。")
            self.save_cookies(upload=True)
            return True
        except Exception as e:
            self.log(f"❌ 初始化异常: {e}")
            return False

    def process_service(self, service, allow_rebuild_retry=True, skip_initial_delay=False, rebuild_retry=False):
        if not skip_initial_delay:
            sleep_random(2000, 4000)
        self.log(f">>> 处理服务 ID: {service['id']}")

        try:
            # 1. 预检：清理遗留未付账单（已处理过的会被 processed_invoices 过滤）
            self.check_and_pay_invoices(service['id'], is_precheck=True)

            # 2. 获取管理页面，同时刷新 CSRF token
            manage_res, soup = self.fetch_manage_page(service['id'])

            # ================== 3. 检测是否允许续期 ==================
            renew_btn = soup.find('button', onclick=re.compile(r'showRenewAlert'))
            if renew_btn:
                onclick_val = renew_btn['onclick']
                match = re.search(r'showRenewAlert\((\d+),\s*(\d+),\s*(true|false)\)', onclick_val)
                if match:
                    days_until = int(match.group(1))
                    threshold = int(match.group(2))
                    is_free = match.group(3) == 'true'

                    if days_until > threshold:
                        threshold_text = "1 day" if threshold == 1 else f"{threshold} days"
                        kind = "免费服务" if is_free else "服务"
                        self.log(f"⏳ 暂未到达续期时间: {kind}剩余时间低于 {threshold_text} 才可续期。当前剩余: {days_until} 天。")
                        return service_result('skip', f"未可續（未到窗口，剩 {days_until} 天）")

            # ================== 4. 执行单次精准续期 ==================
            token_input = soup.find('input', attrs={'name': '_token'})
            if not token_input:
                self.log("❌ 无法找到续期 Token (可能是服务已到期或页面结构变更)")
                return service_result('bad', "搵唔到續期 Token（可能已到期或頁面改版）")

            self.log(f"提交续期 ({RENEW_DAYS}天)...")
            sleep_random(1000, 2000)

            submit_stage = 'first_submit'
            res = self.submit_renew_request(service['id'], soup, manage_res.url)
            handled, outcome = self.try_handle_invoice_from_response(service['id'], res, allow_invoice_poll=False)

            if not handled and res.status_code == 419:
                self.log("♻️ 首次续期请求返回 419，刷新管理页获取新 Token 后重试一次...")
                sleep_random(1000, 2000)
                manage_res, soup = self.fetch_manage_page(service['id'])
                submit_stage = 'same_session_retry'
                res = self.submit_renew_request(service['id'], soup, manage_res.url)
                handled, outcome = False, None

            # ================== 5. 结果校验与支付 ==================
            if not handled:
                handled, outcome = self.try_handle_invoice_from_response(service['id'], res)

            if handled and outcome in {'invoice_page', 'invoice_link', 'invoice_poll'}:
                self.log(f"[RENEW_RESULT] {success_path_label(submit_stage, rebuild_retry=rebuild_retry)}")
                outcome_result = service_result('ok')
            elif handled and outcome == 'server_reject':
                self.log(f"[RENEW_RESULT] {'重建会话后' if rebuild_retry else '当前会话'}提交已被服务端拒绝")
                outcome_result = service_result('bad', "續期請求被服務端拒絕")
            else:
                outcome_result = None

            if not handled and allow_rebuild_retry and res.status_code == 419:
                self.log("♻️ 当前会话内续期仍失败，模拟重跑 Job：重建会话后完整重试当前服务一次...")
                if self.rebuild_session_and_reinit():
                    return self.process_service(service, allow_rebuild_retry=False, skip_initial_delay=True, rebuild_retry=True)
                else:
                    self.log("❌ 重建会话后仍无法重新登录，放弃本服务本轮续期")
                    self.mark_retry_needed(f"服务 {service['id']} 重建会话后仍无法完成续期")
                    return service_result('bad', "重建會話後仍無法登入")
            elif not handled:
                self.mark_retry_needed(f"服务 {service['id']} 本轮续期未完成")
                return service_result('bad', "本輪續期未完成")

            return outcome_result or service_result('bad', "本輪續期未完成")

        except Exception as e:
            self.log(f"处理异常: {e}")
            self.mark_retry_needed(f"服务 {service['id']} 处理异常")
            return service_result('bad', f"處理異常：{e}")
        finally:
            # 每处理完一个服务保存一次 Cookie，而非每次请求都上传
            self.save_cookies(upload=True)

    def check_and_pay_invoices(self, service_id, is_precheck=False, retries=1, retry_delay=5):
        if not is_precheck:
            sleep_random(2000, 3000)

        for attempt in range(retries):
            try:
                res = self.request('GET', f"/service/{service_id}/invoices?where=unpaid")
                soup = BeautifulSoup(res.text, 'html.parser')
                invoice_links = self.extract_invoice_links(soup, require_payment_context=True)

                # 过滤掉本次运行中已处理过的账单，避免重复操作
                unique_invoices = [url for url in set(invoice_links)
                                   if url not in self.processed_invoices
                                   and url not in self.non_payable_invoices]

                if not unique_invoices:
                    if retries > 1 and attempt < retries - 1:
                        self.log(f"⚪ 第{attempt+1}次检查无新账单，{retry_delay}秒后重试...")
                        time.sleep(retry_delay)
                        continue
                    if not is_precheck:
                        self.log("⚪ 无未支付账单")
                    return False

                self.log(f"🔍 发现 {len(unique_invoices)} 个未付账单，准备清理...")
                paid_any = False
                for url in unique_invoices:
                    pay_result = self.pay_single_invoice(url)
                    if pay_result in {'paid', 'already_processed'}:
                        paid_any = True
                    sleep_random(3000, 5000)
                return paid_any

            except Exception as e:
                self.log(f"查账单出错: {e}")
                self.mark_retry_needed(f"服务 {service_id} 查询账单异常")
                return False

    def pay_single_invoice(self, url):
        normalized_url = self.normalize_url(url)
        if normalized_url in self.processed_invoices:
            self.log(f"⏭️ 账单已处理，跳过重复支付: {normalized_url}")
            return 'already_processed'
        if normalized_url in self.non_payable_invoices:
            self.log(f"⏭️ 账单当前不可支付，跳过重复检查: {normalized_url}")
            return 'non_payable'

        try:
            self.log(f"📄 打开账单: {normalized_url}")
            res = self.request('GET', normalized_url)
            return self.perform_pay_from_html(res.text, normalized_url)
        except Exception as e:
            self.log(f"访问账单失败: {e}")
            self.mark_retry_needed("账单页面访问失败")
            return 'invoice_fetch_failed'

    def perform_pay_from_html(self, html_content, current_url):
        normalized_current_url = self.normalize_url(current_url)
        if normalized_current_url in self.processed_invoices:
            self.log(f"⏭️ 账单已处理，跳过重复支付: {normalized_current_url}")
            return 'already_processed'
        if normalized_current_url in self.non_payable_invoices:
            self.log(f"⏭️ 账单当前不可支付，跳过重复检查: {normalized_current_url}")
            return 'non_payable'

        soup = BeautifulSoup(html_content, 'html.parser')
        self._refresh_csrf(soup)

        target_form = None
        target_action = ""

        for form in soup.find_all('form'):
            action = form.get('action', '')
            if not action or 'balance/add' in action:
                continue
            btn = form.find('button')
            # Match both English "pay" and Chinese payment button text
            if btn and ('pay' in btn.get_text().lower() or '支付' in btn.get_text()):
                target_form = form
                target_action = action
                break

        # Fallback: any form whose action contains 'invoice' or 'payment' and has a submit button
        if not target_form:
            for form in soup.find_all('form'):
                action = form.get('action', '')
                if any(kw in action for kw in ['/invoice/', '/payment/']) and 'balance/add' not in action:
                    if form.find('button'):
                        target_form = form
                        target_action = action
                        self.log(f"🔁 降级匹配到支付表单: {action}")
                        break

        if not target_form:
            page_title = soup.title.string.strip() if soup.title and soup.title.string else "无标题"
            page_text = soup.get_text(" ", strip=True)
            if not self.has_invoice_payment_context(page_text):
                self.non_payable_invoices.add(normalized_current_url)
                self.log(f"⚪ 账单页面未显示未支付/支付入口，视为本轮不可支付并跳过: {normalized_current_url}")
                return 'non_payable'
            else:
                self.log(f"⚠️ 未找到可用的支付表单，可能页面结构变更。标题: {page_title}")
                self.mark_retry_needed(f"账单 {normalized_current_url} 页面结构疑似变更")
                return 'payment_form_missing'

        payload = {}
        for inp in target_form.find_all('input'):
            name = inp.get('name')
            value = inp.get('value', '')
            if name:
                payload[name] = value

        self.log("👉 提交支付...")
        try:
            action_url = self.normalize_url(target_action)
            headers = {'X-CSRF-TOKEN': self.csrf_token, 'Referer': current_url}
            res = self.request('POST', action_url, data=payload, headers=headers)

            if res.status_code == 200:
                self.log("✅ 支付成功！")
                self.processed_invoices.add(normalized_current_url)
                return 'paid'
            else:
                self.log(f"⚠️ 支付响应: {res.status_code}")
                self.mark_retry_needed(f"账单 {normalized_current_url} 支付响应异常")
                return 'payment_failed'
        except Exception as e:
            self.log(f"❌ 支付失败: {e}")
            self.mark_retry_needed(f"账单 {normalized_current_url} 支付异常")
            return 'payment_failed'

# ================= 主程序 =================
if __name__ == '__main__':
    env_cookies = os.environ.get("HIDEN_COOKIE", "")
    cookies_list = re.split(r'[&\n]', env_cookies)
    cookies_list = [c for c in cookies_list if c.strip()]
    any_retry_needed = False

    if not cookies_list:
        log_print("❌ 未配置环境变量 HIDEN_COOKIE")
        sys.exit(1)

    WebDavManager().download()

    log_print(f"\n=== HidenCloud 续期脚本启动 (Python版) ===")

    summary_items = []
    for i, cookie in enumerate(cookies_list):
        bot = HidenCloudBot(cookie, i)
        account_label = f"帳號 {bot.index}"
        success = bot.init()

        if not success:
            bot.reset_to_env(cookie)
            success = bot.init()

        if success and bot.services:
            for service in bot.services:
                result = bot.process_service(service) or service_result('bad', "本輪續期未完成")
                summary_items.append({
                    "name": f"{account_label} 服務 {service['id']}",
                    "status": result["status"],
                    "detail": result["detail"],
                })
        elif success:
            summary_items.append({"name": account_label, "status": "skip", "detail": "未可續（冇服務）"})
        else:
            log_print(f"账号 {i + 1}: 登录失败，请检查 Cookie")
            bot.mark_retry_needed("账号初始化失败")
            summary_items.append({"name": account_label, "status": "bad", "detail": "登入失敗，Cookie 失效"})

        if bot.retry_needed:
            any_retry_needed = True

        log_print("\n----------------------------------------\n")
        if i < len(cookies_list) - 1:
            sleep_random(5000, 10000)

    # 通知瘦身：表頭一行當 title，每項一行當 content（TG 砌成 title\ncontent，冇空行）
    if summary_items:
        summary = build_summary("HidenCloud 續期", summary_items)
        headline, _, body = summary.partition("\n")
        send_notify(headline, body)

    if any_retry_needed:
        log_print("🔁 本轮存在可重试失败，脚本将返回退出码 1，供 GitHub Actions 延时再跑一次")
        sys.exit(1)

    sys.exit(0)
