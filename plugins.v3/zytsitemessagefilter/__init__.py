"""按站内信标题或内容通配规则屏蔽内置渠道的站点通知。"""

import json
import re
from typing import Any

from app.schemas.message import Message
from app.schemas.types import MessageType
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase


class ZYTSiteMessageFilter(_PluginBase):
    """仅短路命中的外发站内信通知，保留宿主消息历史。"""

    plugin_name = "屏蔽站点无用消息"
    plugin_desc = "按站点消息标题或内容屏蔽，并在日志记录原消息。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "1.0.1"
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

    def get_page(self) -> None:
        """不提供详情页，由宿主卡片点击逻辑直接打开设置。"""
        pass

    def stop_service(self) -> None:
        """停用后立即放行通知，不持有后台资源。"""
        self._enabled = False
