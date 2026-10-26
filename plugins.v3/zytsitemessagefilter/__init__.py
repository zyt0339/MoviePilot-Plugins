"""按站内信标题或内容通配规则屏蔽内置渠道的站点通知。"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.schemas.message import Message
from app.schemas.types import MessageType
from app.sdk.config import settings
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase


class ZYTSiteMessageFilter(_PluginBase):
    """仅短路命中的外发站内信通知，保留宿主消息历史。"""

    plugin_name = "屏蔽站点无用消息"
    plugin_desc = "按站点消息标题或内容屏蔽，并在日志记录原消息。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "1.0.2"
    plugin_author = "zyt0339"
    author_url = "https://github.com/zyt0339"
    plugin_config_prefix = "zytsitemessagefilter_"
    plugin_order = 1
    auth_level = 1

    DEFAULT_RULES = (
        "种子被删除\n您正在下载或做种的种子被删除*\n"
        "*认领结算\n*请求续种*\n收到来自 zmpt 赠送的 *"
    )
    DEFAULT_CONTENT_RULES = "你正在下载或做种的种子*删除*"
    _enabled = False
    _rules: tuple[tuple[str, re.Pattern[str]], ...] = ()

    _content_rules: tuple[tuple[str, re.Pattern[str]], ...] = ()

    def init_plugin(self, config: dict | None = None) -> None:
        """读取开关和规则，保留用户显式清空规则的选择。"""
        config = config or {}
        self._enabled = False
        # 保留标题规则旧配置键；各输入框显式清空后独立停用。
        self._rules = self._compile_rules(config.get("prefixes", self.DEFAULT_RULES))
        self._content_rules = self._compile_rules(config.get("content_rules", self.DEFAULT_CONTENT_RULES))
        self._enabled = bool(config.get("enabled", False))

    @staticmethod
    def _compile_rules(raw: Any) -> tuple[tuple[str, re.Pattern[str]], ...]:
        """逐行编译仅星号特殊的通配规则，允许内容跨行匹配。"""
        rules = []
        if isinstance(raw, str):
            for line in raw.splitlines():
                rule = line.strip()
                if rule:
                    pattern = ".*".join(re.escape(part) for part in rule.split("*"))
                    rules.append((rule, re.compile(pattern, re.DOTALL)))
        return tuple(rules)

    def get_state(self) -> bool:
        """返回是否启用过滤。"""
        return self._enabled

    def get_module(self) -> dict[str, Any]:
        """注册通知模块，在宿主渠道发送前执行过滤。"""
        return {"post_message": self.filter_message}

    def filter_message(self, message: Message | None = None, **kwargs: Any) -> bool | None:
        """命中返回 True 短路模块分发，其余情况返回 None 放行。"""
        if not self._enabled or message is None or not (self._rules or self._content_rules):
            return None
        if message.mtype != MessageType.SiteMessage or message.action != 1:
            return None
        candidates = (
            ("标题", self._extract_title(message.text), self._rules),
            ("内容", self._extract_content(message.text), self._content_rules),
        )
        for field, value, rules in candidates:
            if value is None:
                continue
            for rule, pattern in rules:
                if not pattern.fullmatch(value):
                    continue
                # 单行 JSON 保留原始换行信息，避免倒序日志拆散同一条消息。
                detail = json.dumps(
                    {"命中字段": field, "命中规则": rule,
                     "通知标题": message.title, "原始正文": message.text},
                    ensure_ascii=False,
                )
                logger.info(f"[屏蔽站点无用消息] 已屏蔽 {detail}")
                return True
        return None

    @staticmethod
    def _extract_content(text: str | None) -> str | None:
        """只提取正文内容标记后的文本，不把标题及时间作为内容匹配。"""
        if not isinstance(text, str):
            return None
        parts = re.split(r"^内容：", text.replace("\r\n", "\n"), maxsplit=1, flags=re.MULTILINE)
        return parts[1].strip() if len(parts) == 2 else None

    @staticmethod
    def _extract_title(text: str | None) -> str | None:
        """只提取正文标题行，不依赖通知主题或时间，不搜索内容段落。"""
        if not isinstance(text, str):
            return None
        normalized = text.replace("\r\n", "\n")
        header = re.split(r"^内容：", normalized, maxsplit=1, flags=re.MULTILINE)[0]
        titles = re.findall(r"^标题：([^\n]*)$", header, flags=re.MULTILINE)
        return titles[0].strip() if len(titles) == 1 else None

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """提供基础配置表单和纯文字规则介绍。"""
        fields = [
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
            {"component": "VTextarea", "props": {
                "model": "prefixes", "label": "标题屏蔽规则（一行一条）", "rows": 6,
            }},
            {"component": "VTextarea", "props": {
                "model": "content_rules", "label": "内容屏蔽规则（一行一条）", "rows": 3,
            }},
            {"component": "VAlert", "props": {
                "type": "info", "variant": "tonal", "style": "white-space: pre-line;",
            },
             "text": "规则介绍："
                     "采用简化版正则表达，匹配站点消息正文中的标题或内容，任一命中即屏蔽，例如\n"
                     "1、种子被删除：匹配这个完整标题，会去除首尾空格对比\n"
                     "2、种子被删除*：匹配以此开头的标题\n"
                     "3、*种子被删除：匹配以此结尾的标题\n"
                     "4、种子被*删除*：如种子被管理员abc删除，原因Dup\n"
                     "5、*请求续种*：匹配包含请求续种的标题"},
        ]
        return [{"component": "VForm", "content": [
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [field]},
            ]} for field in fields
        ]}], {"enabled": False, "prefixes": self.DEFAULT_RULES,
                 "content_rules": self.DEFAULT_CONTENT_RULES}

    def get_api(self) -> list[dict]:
        """无需注册插件 API。"""
        return []

    @classmethod
    def _parse_log_record(cls, line: str) -> dict[str, str] | None:
        """解析一条完整的屏蔽日志，格式异常时跳过。"""
        match = re.fullmatch(
            r"【INFO】(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} "
            r"\[[^\]\r\n]*\] - [^\r\n]+? - \[屏蔽站点无用消息\] 已屏蔽 (\{.*\})",
            line,
        )
        if not match:
            return None
        try:
            datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S")
            detail = json.loads(match[2])
        except (ValueError, TypeError):
            return None
        if not isinstance(detail, dict):
            return None
        if detail.get("命中字段") not in ("标题", "内容") or not isinstance(detail.get("命中规则"), str):
            return None
        subject, text = detail.get("通知标题"), detail.get("原始正文")
        if not isinstance(subject, str) or not isinstance(text, str):
            return None
        site = re.fullmatch(r"【站点\s+(.+?)\s+消息】", subject.strip())
        header = re.split(r"^内容：", text.replace("\r\n", "\n"), maxsplit=1, flags=re.MULTILINE)[0]
        title_match = re.search(r"^标题：(.*)", header, flags=re.MULTILINE | re.DOTALL)
        title = title_match[1].strip() if title_match and len(re.findall(r"^标题：", header, flags=re.MULTILINE)) == 1 else None
        content = cls._extract_content(text)
        if not site or title is None or content is None:
            return None
        return {"site": site[1], "title": title, "content": content, "time": match[1]}

    def _read_blocked_records(self) -> list[dict[str, str]]:
        """有界读取当前实例日志末尾，最多返回最近 100 条有效记录。"""
        # 与宿主日志路由使用同一模块身份，避免读取其他分身的日志。
        log_name = self.__class__.__module__.split(".")[2]
        path = Path(settings.LOG_PATH) / "plugins" / f"{log_name}.log"
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                offset = max(0, stream.tell() - 100 * 1024)
                stream.seek(offset)
                data = stream.read(100 * 1024)
                if offset:
                    # 只在这次读取的 100 KiB 内丢弃首行，不额外读取。
                    data = data.partition(b"\n")[2]
        except OSError:
            return []
        records = []
        for raw in reversed(data.splitlines()):
            try:
                record = self._parse_log_record(raw.decode("utf-8"))
            except UnicodeDecodeError:
                continue
            if record is not None:
                records.append(record)
                if len(records) == 100:
                    break
        return records

    @staticmethod
    def _display_text(text: str, fallback: str) -> str:
        """展示时移除空白行，不改变原日志及过滤逻辑。"""
        return "\n".join(line for line in text.splitlines() if line.strip()).strip() or fallback

    @staticmethod
    def _message_nodes(title: str, content: str) -> list[dict]:
        """消息以纯文字渲染，不解释原文中的 HTML。"""
        return [
            {"component": "div", "props": {"style": "font-weight: 700; white-space: pre-wrap;"}, "text": title},
            {"component": "div", "props": {"style": "white-space: pre-wrap; font-size: 0.8125rem;"}, "text": content},
        ]

    def get_page(self) -> list[dict]:
        """四列滚动展示日志，长消息使用浏览器原生展开控件。"""
        records = self._read_blocked_records()
        if not records:
            return [{"component": "VAlert", "props": {
                "type": "info", "variant": "tonal", "text": "暂无可展示的屏蔽记录",
            }}]
        rows = []
        for index, record in enumerate(records, 1):
            title = self._display_text(record["title"], "无标题")
            content = self._display_text(record["content"], "无内容")
            message_nodes = self._message_nodes(title, content)
            combined = title + "\n" + content
            if len(combined) > 200 or len(combined.splitlines()) > 6:
                preview = "\n".join(combined.splitlines()[:6])[:200]
                short_title, _, short_content = preview.partition("\n")
                message_nodes = [{"component": "details", "props": {"class": "zyt-message-expand"}, "content": [
                    {"component": "summary", "props": {"aria-label": "展开或收起消息"}, "content": [
                        {"component": "div", "props": {"class": "zyt-message-preview"},
                         "content": self._message_nodes(short_title, short_content + "…")},
                        {"component": "span", "props": {"class": "zyt-message-more"}, "text": "展开 ▾"},
                        {"component": "span", "props": {"class": "zyt-message-less"}, "text": "收起 ▴"},
                    ]},
                    {"component": "div", "content": self._message_nodes(title, content)},
                ]}]
            site = record["site"]
            rows.append({"component": "tr", "content": [
                {"component": "td", "props": {"style": "width: 36px; min-width: 36px; max-width: 36px; white-space: nowrap; text-align: center; vertical-align: middle;"}, "text": str(index)},
                {"component": "td", "props": {"style": "width: 8em; max-width: 8em; vertical-align: middle; overflow-wrap: anywhere;",
                                               "title": site},
                 "text": site if len(site) <= 10 else site[:10] + "…"},
                {"component": "td", "props": {"style": "min-width: 200px; overflow-wrap: anywhere; padding-top: 8px; padding-bottom: 8px;"},
                 "content": message_nodes},
                {"component": "td", "props": {"style": "width: 165px; white-space: nowrap; vertical-align: middle;"},
                 "text": record["time"]},
            ]})
        return [{"component": "VTable", "props": {
            "class": "zyt-message-history", "density": "compact", "height": "60vh", "fixed-header": True,
        }, "content": [
            {"component": "style", "text":
             ".zyt-message-history table {table-layout:fixed; min-width:536px;}"
             ".zyt-message-history.v-table > .v-table__wrapper > table > thead > tr > th,"
             ".zyt-message-history.v-table > .v-table__wrapper > table > tbody > tr > td {padding-left:6px; padding-right:6px;}"
             ".zyt-message-history.v-table > .v-table__wrapper > table > thead > tr > th:first-child,"
             ".zyt-message-history.v-table > .v-table__wrapper > table > tbody > tr > td:first-child {padding-left:1px; padding-right:1px;}"
             ".zyt-message-history.v-table > .v-table__wrapper > table > thead > tr > th:nth-child(2),"
             ".zyt-message-history.v-table > .v-table__wrapper > table > tbody > tr > td:nth-child(2) {padding-left:1px;}"
             ".zyt-message-history th:first-child {width:36px; min-width:36px; max-width:36px; white-space:nowrap;}"
             ".zyt-message-history th:nth-child(2) {width:8em;}"
             ".zyt-message-history th:last-child {width:165px;}"
             ".zyt-message-history summary {cursor:pointer; list-style:none;}"
             ".zyt-message-history summary::-webkit-details-marker {display:none;}"
             ".zyt-message-history .zyt-message-less {display:none;}"
             ".zyt-message-history details[open] .zyt-message-preview,"
             ".zyt-message-history details[open] .zyt-message-more {display:none;}"
             ".zyt-message-history details[open] .zyt-message-less {display:inline;}"
             ".zyt-message-history details[open] {display:flex; flex-direction:column;}"
             ".zyt-message-history details[open] > summary {order:2;}"
             ".zyt-message-history details[open]::details-content {order:1;}"
             ".zyt-message-history .zyt-message-more,.zyt-message-history .zyt-message-less {font-size:0.75rem; color:rgb(var(--v-theme-primary));}"},
            {"component": "thead", "content": [{"component": "tr", "content": [
                {"component": "th", "text": name} for name in ("", "站点", "消息", "屏蔽时间")
            ]}]},
            {"component": "tbody", "content": rows},
        ]}]

    def stop_service(self) -> None:
        """停用后立即放行通知，不持有后台资源。"""
        self._enabled = False
