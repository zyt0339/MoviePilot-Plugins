"""按站内信标题通配规则屏蔽内置渠道的站点通知。"""

import re
from typing import Any

from app.schemas.message import Message
from app.schemas.types import MessageType
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase


class ZYTSiteMessageFilter(_PluginBase):
    """仅短路命中的外发站内信通知，保留宿主消息历史。"""

    plugin_name = "屏蔽站点无用消息"
    plugin_desc = "按站内信标题通配规则屏蔽内置渠道推送，并在日志保留原消息。"
    plugin_icon = "Moviepilot_A.png"
    plugin_version = "1.0.0"
    plugin_author = "zyt0339"
    author_url = "https://github.com/zyt0339"
    plugin_config_prefix = "zytsitemessagefilter_"
    plugin_order = 1
    auth_level = 1

    RULE_EXAMPLES = (
        ("种子被删除", "只匹配这个完整标题"),
        ("种子被删除*", "匹配以此开头的标题"),
        ("您正在下载或做种的种子被删除*", "匹配以此开头的标题"),
        ("*认领结算", "匹配以“认领结算”结尾的标题"),
        ("*请求续种*", "匹配包含“请求续种”的标题"),
        ("收到来自 zmpt 赠送的 *", "匹配此前缀"),
    )
    DEFAULT_RULES = "\n".join(rule for rule, _ in RULE_EXAMPLES)
    _enabled = False
    _rules: tuple[tuple[str, re.Pattern[str]], ...] = ()

    def init_plugin(self, config: dict | None = None) -> None:
        """读取开关和规则，保留用户显式清空规则的选择。"""
        config = config or {}
        # 保留既有配置键，不覆盖用户输入或自动追加通配符。
        raw = config.get("prefixes", self.DEFAULT_RULES)
        rules = []
        if isinstance(raw, str):
            for line in raw.splitlines():
                rule = line.strip()
                if rule:
                    pattern = ".*".join(re.escape(part) for part in rule.split("*"))
                    rules.append((rule, re.compile(pattern)))
        self._enabled = False
        self._rules = tuple(rules)
        self._enabled = bool(config.get("enabled", False))

    def get_state(self) -> bool:
        """返回是否启用过滤。"""
        return self._enabled

    def get_module(self) -> dict[str, Any]:
        """注册通知模块，在宿主渠道发送前执行过滤。"""
        return {"post_message": self.filter_message}

    def filter_message(self, message: Message | None = None, **kwargs: Any) -> bool | None:
        """命中返回 True 短路模块分发，其余情况返回 None 放行。"""
        if not self._enabled or message is None or not self._rules:
            return None
        if message.mtype != MessageType.SiteMessage or message.action != 1:
            return None
        title = self._extract_title(message.title, message.text)
        if title is None:
            return None
        for rule, pattern in self._rules:
            if pattern.fullmatch(title):
                logger.info(
                    f"[屏蔽站点无用消息] 已屏蔽，命中规则：{rule}\n"
                    f"{message.title}\n{message.text}"
                )
                return True
        return None

    @staticmethod
    def _extract_title(title: str | None, text: str | None) -> str | None:
        """从完整的宿主站内信格式提取标题，格式不符时放行。"""
        if not isinstance(title, str) or not isinstance(text, str):
            return None
        if not re.fullmatch(r"【站点 [^\r\n]+ 消息】", title):
            return None
        normalized = text.replace("\r\n", "\n")
        match = re.fullmatch(r"时间：[^\n]+\n标题：([^\n]*)\n内容：\n[\s\S]*", normalized)
        return match.group(1).strip() if match else None

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        """提供开关、逐行通配规则、示例表及过滤范围说明。"""
        return [{"component": "VForm", "content": [
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
            {"component": "VTextarea", "props": {
                "model": "prefixes", "label": "屏蔽标题规则（一行一条）", "rows": 6,
                "hint": "标题和规则去除首尾空格，保留内部空格，区分大小写；"
                        "* 匹配任意长度文字（可为空），其他字符按原文匹配；清空则不屏蔽。",
                "persistent-hint": True,
            }},
            {"component": "VTable", "props": {"density": "compact"}, "content": [
                {"component": "thead", "content": [{"component": "tr", "content": [
                    {"component": "th", "text": "规则"},
                    {"component": "th", "text": "效果"},
                ]}]},
                {"component": "tbody", "content": [
                    {"component": "tr", "content": [
                        {"component": "td", "text": rule},
                        {"component": "td", "text": effect},
                    ]} for rule, effect in self.RULE_EXAMPLES
                ]},
            ]},
            {"component": "VAlert", "props": {
                "type": "warning", "variant": "tonal",
                "text": "旧规则提示：无星号现在为严格匹配，开头匹配需在末尾添加 *。"
                        "已有规则不会自动修改。",
            }},
            {"component": "VAlert", "props": {
                "type": "info", "variant": "tonal",
                "text": "仅过滤向外发送的站内信通知，保留消息历史，命中内容写入 INFO 日志。"
                        "不处理分享率预警和未读数量提醒。仅阻止后续内置通知模块，"
                        "不影响邮件、WPUSH 等广播插件或较早自行发送的模块插件。",
            }},
        ]}], {"enabled": False, "prefixes": self.DEFAULT_RULES}

    def get_api(self) -> list[dict]:
        """无需注册插件 API。"""
        return []

    def get_page(self) -> list[dict]:
        """原消息通过宿主历史和 INFO 日志查看，无额外详情页。"""
        return []

    def stop_service(self) -> None:
        """停用后立即放行通知，不持有后台资源。"""
        self._enabled = False
