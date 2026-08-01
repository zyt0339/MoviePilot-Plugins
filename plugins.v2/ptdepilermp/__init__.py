"""PT 站点保号状态插件。"""

from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import Event, eventmanager
from app.db.site_oper import SiteOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import EventType
from app.utils.string import StringUtils

from app.plugins.ptdepilermp.rules import (
    RequirementResult,
    RuleRepository,
    SiteLevelResult,
    evaluate_requirement,
    parse_size,
)


class PTDepilerMp(_PluginBase):
    """使用 MoviePilot 站点快照计算 PT 等级与保号状态。"""

    plugin_name = "PT 站点保号状态"
    plugin_desc = "展示站点当前等级、保号等级和保号缺口。"
    plugin_icon = "database.png"
    plugin_version = "1.33.0"
    plugin_author = "zyt0339"
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins"
    plugin_config_prefix = "ptdepilermp_"
    plugin_order = 20
    auth_level = 2

    _onlyonce = False
    _cron = ""
    _donor_sites: List[str] = []
    _scheduler: Optional[BackgroundScheduler] = None

    def __init__(self):
        super().__init__()
        self._repository = RuleRepository()
        self._calculation_lock = Lock()
        self._cached_rows: List[Dict[str, Any]] = []
        self._has_calculated = False

    def init_plugin(self, config: dict = None):
        """载入插件配置和磁盘站点规则。"""
        self.stop_service()
        config = dict(config or {})
        self._onlyonce = bool(config.get("onlyonce", False))
        self._cron = str(config.get("cron") or "").strip()
        donor_sites = config.get("donor_sites") or []
        if not isinstance(donor_sites, list):
            donor_sites = []
        self._donor_sites = []
        seen_donor_sites = set()
        for site_name in donor_sites:
            normalized = str(site_name or "").strip()
            name_key = normalized.casefold()
            if normalized and name_key not in seen_donor_sites:
                self._donor_sites.append(normalized)
                seen_donor_sites.add(name_key)
        self._recalculate("手动刷新" if self._onlyonce else "插件加载")
        if self._onlyonce:
            # 保存配置会重新加载插件，上面的计算已经立即完成；这里只负责复位一次性开关。
            self._onlyonce = False
            self._save_current_config()
        self._configure_calculation_jobs()

    def get_state(self) -> bool:
        """返回插件启用状态。"""
        return True

    def _save_current_config(self) -> None:
        """保存当前配置，并确保一次性开关不会重复执行。"""
        self.update_config({
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "donor_sites": self._donor_sites,
        })

    def _configure_calculation_jobs(self) -> None:
        """注册 cron 保号数据重算任务。"""
        if not self._cron:
            return
        scheduler = BackgroundScheduler(timezone=settings.TZ)
        has_jobs = False
        if self._cron:
            try:
                scheduler.add_job(
                    self._recalculate,
                    CronTrigger.from_crontab(self._cron),
                    args=["cron"],
                    max_instances=1,
                    coalesce=True,
                    name="PT站点保号数据定时重算",
                )
                has_jobs = True
            except Exception as error:
                logger.error(f"PT站点等级监控：cron 表达式无效：{error}")
        if has_jobs:
            self._scheduler = scheduler
            scheduler.start()

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """本插件不注册命令。"""
        return []

    @staticmethod
    def get_service() -> List[Dict[str, Any]]:
        """本插件不注册周期服务。"""
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """本插件不提供 API。"""
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回插件配置表单和默认值。"""
        donor_site_options = []
        donor_site_keys = set()
        for site in SiteOper().list_active() or []:
            site_name = str(getattr(site, "name", None) or "").strip()
            site_name_key = site_name.casefold()
            if site_name and site_name_key not in donor_site_keys:
                donor_site_options.append({"title": site_name, "value": site_name})
                donor_site_keys.add(site_name_key)
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "onlyonce",
                                        "label": "立即重新计算一次",
                                        "color": "primary",
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [{
                                "component": "VCronField",
                                "props": {
                                    "model": "cron",
                                    "label": "保号数据重算周期",
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VRow",
                        "content": [{
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [{
                                "component": "VSelect",
                                "props": {
                                    "model": "donor_sites",
                                    "label": "黄星/捐赠者特殊保号站点",
                                    "items": donor_site_options,
                                    "multiple": True,
                                    "chips": True,
                                    "clearable": True,
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "info", "variant": "tonal", "class": "mb-4"},
                        "text": "立即重算和 cron 只读取 MoviePilot 已保存的站点快照并重新计算保号结果，不会连接 PT 站点；cron 留空时不创建定时任务。",
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "info", "variant": "tonal", "class": "mt-4"},
                        "text": "等级配置来自插件 site_rules 目录。上方可从全部启用站点中选择当前拥有黄星或捐赠者身份的站点；黄星失效后请及时取消。修改规则后请执行一次立即重算或重新加载插件。",
                    },
                ],
            }
        ], {
            "onlyonce": False,
            "cron": "",
            "donor_sites": [],
        }

    @staticmethod
    def _object_dict(value: Any) -> Dict[str, Any]:
        """从 ORM 对象提取计算所需的非敏感字段。"""
        keys = (
            "user_level", "join_at", "upload", "download", "ratio", "bonus",
            "seeding", "leeching", "seeding_size", "updated_day", "updated_time", "err_msg",
        )
        return {key: getattr(value, key, None) for key in keys}

    @staticmethod
    def _safe_site_url(value: Any) -> Optional[str]:
        """仅允许浏览器打开不含认证信息的 HTTP(S) 站点地址。"""
        site_url = str(value or "").strip()
        if not site_url:
            return None
        try:
            parsed = urlparse(site_url)
        except (TypeError, ValueError):
            return None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            return None
        return site_url

    def _calculate_rows(self) -> List[Dict[str, Any]]:
        """读取 MoviePilot 已有快照并计算保号结果，不访问 PT 站点。"""
        self._repository.reload()
        sites = SiteOper().list_active() or []
        latest = SiteOper().get_userdata_latest() or []
        # 规则与用户快照统一只按 MoviePilot 站点名称关联，不依赖可能变化的站点域名。
        # 同名站点可能残留多个历史域名，必须在名称分组内按完整日期时间选择最新记录，
        # 不能依赖 get_userdata_latest() 跨域名时仅按 updated_time 排列的返回顺序。
        latest_by_name = {}
        for item in latest:
            name_key = str(getattr(item, "name", None) or "").strip().casefold()
            if not name_key:
                continue
            current = latest_by_name.get(name_key)
            item_updated_at = (
                str(getattr(item, "updated_day", None) or ""),
                str(getattr(item, "updated_time", None) or ""),
            )
            current_updated_at = (
                str(getattr(current, "updated_day", None) or ""),
                str(getattr(current, "updated_time", None) or ""),
            ) if current else ("", "")
            if current is None or item_updated_at > current_updated_at:
                latest_by_name[name_key] = item
        today = datetime.now(tz=pytz.timezone(settings.TZ)).strftime("%Y-%m-%d")
        donor_site_keys = {site_name.casefold() for site_name in self._donor_sites}
        rows = []
        for site in sites:
            site_name_key = str(site.name or "").strip().casefold()
            data = latest_by_name.get(site_name_key)
            user = self._object_dict(data) if data else {}
            # MoviePilot 暂不保存黄星状态；该瞬时字段只来自用户配置，不访问站点也不持久化。
            user["is_donor"] = site_name_key in donor_site_keys
            rule_id, rule = self._repository.match(site.name)
            rule = self._repository.resolve_rule(user, rule)
            result = self._repository.evaluate_site(user, rule_id, rule)
            rows.append({
                "site_name": site.name,
                "site_url": self._safe_site_url(getattr(site, "url", None)),
                "user": user,
                "rule": rule,
                "result": result,
                "stale": not data or user.get("updated_day") != today or bool(user.get("err_msg")),
            })
        return rows

    def _recalculate(self, trigger_source: str = "内部调用") -> None:
        """重新计算并缓存保号数据。"""
        with self._calculation_lock:
            rows = self._calculate_rows()
            self._cached_rows = rows
            self._has_calculated = True
            self._write_debug_log(rows)
            completed_at = datetime.now(tz=pytz.timezone(settings.TZ)).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(
                f"PT 站点保号状态：保号数据刷新完成，刷新时间={completed_at}，"
                f"触发源={trigger_source}，站点数={len(rows)}"
            )

    @eventmanager.register(EventType.SiteRefreshed)
    def on_all_sites_refreshed(self, event: Event) -> None:
        """MoviePilot 全站刷新完成后，使用最新快照重算保号数据。"""
        if (event.event_data or {}).get("site_id") != "*":
            return
        logger.debug("PT站点等级监控：收到全站数据刷新完成事件，开始重新计算保号数据")
        try:
            self._recalculate("站点全量刷新通知")
        except Exception as error:
            logger.error(f"PT站点等级监控：全站刷新完成后重新计算失败：{error}")

    def _rows(self) -> List[Dict[str, Any]]:
        """返回最近一次计算结果；首次缺失时即时计算。"""
        if not self._has_calculated:
            self._recalculate("页面首次计算")
        return self._cached_rows

    def _write_debug_log(self, rows: List[Dict[str, Any]]) -> None:
        """默认输出不含域名和认证信息的 debug 调查日志。"""
        logger.debug(
            f"PT站点等级监控调查：站点数={len(rows)}，规则数={len(self._repository.sites)}，"
            f"无效规则数={self._repository.load_errors}"
        )
        for row in rows:
            result = row["result"]
            if result.retained is not None and not row["stale"]:
                continue
            retained_level = self._retention_level(row)
            requirement = evaluate_requirement(row["user"], retained_level) if retained_level else None
            unknown = requirement.unknown if requirement else []
            logger.debug(
                "PT站点等级监控调查："
                f"站点={row['site_name']}，当前等级={row['user'].get('user_level') or '缺失'}，"
                f"规则={result.rule_id or '未匹配'}，状态="
                f"{'已保号' if result.retained is True else '未保号' if result.retained is False else '无法判断'}，"
                f"原因={result.reason or '无'}，保号等级="
                f"{(retained_level or {}).get('name') or '未配置'}，保号未知条件={','.join(unknown) or '无'}，"
                f"快照={'陈旧或失败' if row['stale'] else '正常'}"
            )
        for start in range(0, len(self._repository.load_error_details), 20):
            details = self._repository.load_error_details[start:start + 20]
            logger.debug("PT站点等级监控调查：无效规则=" + "；".join(f"{name}: {reason}" for name, reason in details))

    @staticmethod
    def _size(value: Any) -> str:
        """格式化容量，保留未知状态。"""
        if value is None:
            return "数据不足"
        try:
            parsed = parse_size(value)
            return StringUtils.str_filesize(parsed) if parsed is not None else "数据不足"
        except (TypeError, ValueError):
            return "数据不足"

    @staticmethod
    def _number(value: Any) -> str:
        """格式化普通数值。"""
        if value is None:
            return "数据不足"
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return "数据不足"

    @staticmethod
    def _duration(value: Any) -> str:
        """格式化 ISO 注册时长要求。"""
        if not value:
            return "无"
        return str(value).removeprefix("P").replace("Y", "年").replace("M", "月").replace("W", "周").replace("D", "天")

    @staticmethod
    def _membership_weeks(value: Any) -> str:
        """将 MoviePilot 入站时间格式化为已经过的完整周数。"""
        if not value:
            return "数据不足"
        try:
            joined = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            timezone = pytz.timezone(settings.TZ)
            now = datetime.now(tz=timezone)
            if joined.tzinfo is None:
                joined = timezone.localize(joined) if hasattr(timezone, "localize") else joined.replace(tzinfo=timezone)
            else:
                joined = joined.astimezone(timezone)
            elapsed_days = (now - joined).days
            return f"{elapsed_days // 7}周" if elapsed_days >= 0 else "数据不足"
        except (TypeError, ValueError, OverflowError):
            return "数据不足"

    @staticmethod
    def _retention_field_met(
        retained_level: Optional[Dict[str, Any]],
        requirement: Optional[RequirementResult],
        keys: Tuple[str, ...],
    ) -> bool:
        """判断摘要字段是否不阻碍保号：无要求或已满足均返回 True。"""
        if not retained_level or not requirement:
            return False
        configured_keys = [key for key in keys if key in retained_level]
        if not configured_keys:
            return True
        return all(
            key not in requirement.gaps and key not in requirement.unknown
            for key in configured_keys
        )

    def _current_level_data_content(
        self,
        user: Dict[str, Any],
        retained_level: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """构建当前数据摘要：无要求/已满足为绿色，明确未满足为黄色。"""
        requirement = evaluate_requirement(user, retained_level) if retained_level else None
        fields = [
            ("上传", self._size(user.get("upload")), ("uploaded", "trueUploaded")),
            ("下载", self._size(user.get("download")), ("downloaded", "trueDownloaded")),
            ("注册时长", self._membership_weeks(user.get("join_at")), ("interval",)),
            ("分享率", self._number(user.get("ratio")), ("ratio", "trueRatio")),
            ("魔力", self._number(user.get("bonus")), ("bonus",)),
        ]
        content: List[Dict[str, Any]] = [{"component": "span", "text": "当前："}]
        for index, (label, value, keys) in enumerate(fields):
            props = {}
            if retained_level and requirement:
                props["class"] = (
                    "text-success"
                    if self._retention_field_met(retained_level, requirement, keys)
                    else "text-warning"
                )
            item = {"component": "span", "text": f"{label} {value}"}
            if props:
                item["props"] = props
            content.append(item)
            if index < len(fields) - 1:
                content.append({"component": "span", "text": "；"})
        return content

    def _requirement_size(self, requirement: Dict[str, Any], key: str) -> str:
        """区分等级没有该门槛与门槛值无效。"""
        return self._size(requirement.get(key)) if key in requirement else "无要求"

    @staticmethod
    def _field_label(key: str) -> str:
        return {
            "bonus": "魔力", "seedingBonus": "做种积分", "ratio": "分享率",
            "seeding": "做种数", "seedingSize": "做种量", "uploads": "发布数",
            "alternative": "可选条件", "interval": "注册时长",
        }.get(key, key)

    def _extra_requirement_text(self, level: Dict[str, Any]) -> str:
        """展示常见的非流量等级门槛。"""
        parts = []
        for key in ("ratio", "bonus", "seedingBonus", "seeding", "seedingSize", "uploads"):
            if key not in level:
                continue
            value = level[key]
            if key == "seedingSize":
                text = self._size(value)
            elif isinstance(value, list):
                text = "～".join(self._number(item) for item in value)
            else:
                text = self._number(value)
            parts.append(f"{self._field_label(key)} {text}")
        return "；" + "；".join(parts) if parts else ""

    def _gap_text(self, result: SiteLevelResult) -> str:
        """把下一等级三态结果转为简洁文本。"""
        requirement = result.next_requirement
        if not result.next_level:
            return "已是最高普通等级"
        if not requirement:
            return "数据不足"
        parts = []
        if "uploaded" in requirement.gaps:
            parts.append(f"上传 {self._size(requirement.gaps['uploaded'])}")
        if "downloaded" in requirement.gaps:
            parts.append(f"下载 {self._size(requirement.gaps['downloaded'])}")
        interval = requirement.gaps.get("interval")
        if isinstance(interval, dict):
            days = max(1, int(interval.get("seconds", 0) / 86400 + 0.999))
            parts.append(f"时间 {days}天")
        other = [key for key in requirement.gaps if key not in {"uploaded", "downloaded", "interval"}]
        if other:
            parts.append("其他条件 " + "、".join(self._field_label(key) for key in other))
        if requirement.unknown:
            parts.append("数据不足：" + "、".join(self._field_label(key) for key in requirement.unknown))
        return "；".join(parts) if parts else "已满足已知条件"

    @staticmethod
    def _status_chip(result: SiteLevelResult) -> Dict[str, Any]:
        """构建保号状态 Chip。"""
        if result.retained is True:
            text, color = "已保号", "success"
        elif result.retained is False:
            text, color = "未保号", "warning"
        else:
            text, color = "无法判断", "grey"
        return {
            "component": "VChip",
            "props": {"color": color, "variant": "tonal", "size": "small"},
            "text": text,
        }

    @staticmethod
    def _cell(
        text: Any,
        content: Optional[List[dict]] = None,
        width: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建表格单元格。"""
        props: Dict[str, Any] = {"class": "text-sm whitespace-nowrap"}
        if width:
            props["style"] = {"width": width}
        cell = {"component": "td", "props": props}
        if content is not None:
            cell["content"] = content
        else:
            cell["text"] = str(text)
        return cell

    def _summary(self, rows: List[Dict[str, Any]]) -> Dict[str, int]:
        """汇总站点保号和陈旧数据数量。"""
        return {
            "retained": sum(1 for row in rows if row["result"].retained is True),
            "unretained": sum(1 for row in rows if row["result"].retained is False),
            "unknown": sum(1 for row in rows if row["result"].retained is None),
            "stale": sum(1 for row in rows if row["stale"]),
        }

    @staticmethod
    def _retention_level(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """返回首个普通保号等级；VIP/管理等级直接返回当前特殊等级。"""
        result = row["result"]
        if result.retention_type == "donor":
            return {"name": "黄星（特殊保号）"}
        if result.current_group in {"vip", "manager"}:
            return result.current_level or {"name": row["user"].get("user_level") or "VIP/管理等级"}
        levels = sorted((row["rule"] or {}).get("levels") or [], key=lambda item: item.get("id", -1))
        return next((
            level for level in levels
            if (level.get("groupType") or "user") == "user" and level.get("isKept")
        ), None)

    def _retention_summary(
        self,
        result: SiteLevelResult,
        retained_level: Optional[Dict[str, Any]],
        requirement: Optional[RequirementResult],
    ) -> str:
        """生成围绕保号等级的结论，而不是下一等级结论。"""
        if result.retention_type == "donor":
            return "黄星保号，已保号"
        if result.reason:
            return result.reason
        if result.current_group in {"vip", "manager"}:
            return "VIP/管理等级，已保号"
        if not retained_level:
            return "规则未配置保号等级"
        if result.retained:
            return "已保号"
        if not requirement:
            return "保号条件数据不足"
        proxy = SiteLevelResult(
            result.rule_id, result.current_level, result.current_group,
            retained_level, requirement, result.retained,
        )
        detail = self._gap_text(proxy)
        return "目标：" + detail

    def _site_cell(self, row: Dict[str, Any], width: str) -> Dict[str, Any]:
        """未保号或无法判断的站点提供带下划线的新窗口入口。"""
        site_name = row["site_name"]
        site_url = row.get("site_url")
        if row["result"].retained is not True and site_url:
            return self._cell("", [{
                "component": "a",
                "props": {
                    "href": site_url,
                    "target": "_blank",
                    "rel": "noopener noreferrer",
                    "class": "text-decoration-underline",
                },
                "text": site_name,
            }], width=width)
        return self._cell(site_name, width=width)

    @staticmethod
    def _join_at_sort_key(row: Dict[str, Any]) -> Tuple[int, float]:
        """按入站时间升序排列，缺失或异常时间统一放在最后。"""
        value = row.get("user", {}).get("join_at")
        if not value:
            return 1, 0.0
        try:
            joined = (
                value
                if isinstance(value, datetime)
                else datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            )
            return 0, joined.timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            return 1, 0.0

    def get_page(self) -> List[dict]:
        """返回站点等级详情页。"""
        rows = sorted(self._rows(), key=self._join_at_sort_key)
        summary = self._summary(rows)
        headers = [
            ("站点", "7%"),
            ("状态", "7%"),
            ("当前等级", "8%"),
            ("保号等级", "12%"),
            ("上传/下载/分享率", "19%"),
            ("保号上传/下载/分享率", "22%"),
            ("总结", "13%"),
            ("数据时间", "12%"),
        ]
        table_rows = []
        panels = []
        for row in rows:
            user, result = row["user"], row["result"]
            retained_level = self._retention_level(row)
            retained_requirement = evaluate_requirement(user, retained_level) if retained_level else None
            current_name = (result.current_level or {}).get("name") or user.get("user_level") or "数据不足"
            update_text = " ".join(filter(None, [user.get("updated_day"), user.get("updated_time")])) or "无快照"
            if row["stale"]:
                update_text += "（陈旧/失败）"
            table_rows.append({
                "component": "tr",
                "content": [
                    self._site_cell(row, width=headers[0][1]),
                    self._cell("", [self._status_chip(result)], width=headers[1][1]),
                    self._cell(current_name, width=headers[2][1]),
                    self._cell((retained_level or {}).get("name") or "未配置", width=headers[3][1]),
                    self._cell(
                        f"{self._size(user.get('upload'))} / "
                        f"{self._size(user.get('download'))} / "
                        f"{self._number(user.get('ratio'))}",
                        width=headers[4][1],
                    ),
                    self._cell(
                        f"{self._requirement_size(retained_level or {}, 'uploaded')} / "
                        f"{self._requirement_size(retained_level or {}, 'downloaded')} / "
                        f"{self._number(retained_level.get('ratio')) if retained_level and 'ratio' in retained_level else '无要求'}",
                        width=headers[5][1],
                    ),
                    self._cell(
                        self._retention_summary(result, retained_level, retained_requirement),
                        width=headers[6][1],
                    ),
                    self._cell(update_text, width=headers[7][1]),
                ],
            })
            panels.append(self._level_panel(row))

        return [{
            "component": "div",
            "content": [
                self._summary_cards(summary),
                {
                    "component": "VTable",
                    "props": {"hover": True, "fixed-header": True, "density": "compact", "class": "mt-4"},
                    "content": [
                        {"component": "thead", "content": [{
                            "component": "tr",
                            "content": [
                                {
                                    "component": "th",
                                    "props": {"style": {"width": width}},
                                    "text": name,
                                }
                                for name, width in headers
                            ],
                        }]},
                        {"component": "tbody", "content": table_rows},
                    ],
                },
                {"component": "h3", "props": {"class": "mt-6 mb-2"}, "text": "完整等级规则"},
                {"component": "VExpansionPanels", "props": {"variant": "accordion"}, "content": panels},
            ],
        }]

    def _level_panel(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """构建单站完整等级规则折叠面板。"""
        result, rule, user = row["result"], row["rule"] or {}, row["user"]
        level_rows = []
        current_id = (result.current_level or {}).get("id")
        sorted_levels = sorted(rule.get("levels") or [], key=lambda item: item.get("id", -1))
        minimum_retention_level = next((
            level for level in sorted_levels
            if (level.get("groupType") or "user") == "user" and level.get("isKept")
        ), None)
        minimum_retention_id = (minimum_retention_level or {}).get("id")
        displayed_retention_level = (
            self._retention_level(row)
            if result.retention_type == "donor"
            else minimum_retention_level
        )
        for level in sorted_levels:
            if result.current_group == "user":
                reached = (
                    (level.get("groupType") or "user") == "user"
                    and current_id is not None
                    and level.get("id", -1) <= current_id
                )
            else:
                reached = current_id is not None and level.get("id") == current_id
            icon = "mdi-check-circle-outline" if reached else "mdi-circle-outline"
            # 首个保号等级始终使用绿色，作为规则标识；是否达到只由图标形态表达。
            if level.get("id") == minimum_retention_id:
                color = "success"
            else:
                color = "info" if reached else None
            level_name = level.get("name") or "未命名等级"
            if minimum_retention_id is not None and level.get("id") == minimum_retention_id:
                level_name += "（保号等级）"
            item_props = {
                "prepend-icon": icon,
                "title": level_name,
                "subtitle": (
                    f"上传 {self._requirement_size(level, 'uploaded')}；下载 {self._requirement_size(level, 'downloaded')}；"
                    f"注册时长 {self._duration(level.get('interval'))}{self._extra_requirement_text(level)}"
                ),
            }
            if color:
                item_props["base-color"] = color
            level_rows.append({
                "component": "VListItem",
                "props": item_props,
            })
        if not level_rows:
            level_rows = [{
                "component": "VListItem",
                "props": {"title": result.reason or "无可用规则"},
            }]
        panel_title = {"component": "VExpansionPanelTitle"}
        if result.retained is True:
            panel_title["content"] = [{
                "component": "span",
                "props": {"class": "text-success"},
                "text": row["site_name"],
            }]
        else:
            panel_title["text"] = row["site_name"]
        return {
            "component": "VExpansionPanel",
            "content": [
                panel_title,
                {"component": "VExpansionPanelText", "content": [
                    {
                        "component": "div",
                        "props": {"class": "px-4 pt-2 pb-1 text-body-2 font-weight-medium"},
                        "content": self._current_level_data_content(user, displayed_retention_level),
                    },
                    {"component": "VList", "content": level_rows},
                ]},
            ],
        }

    @staticmethod
    def _summary_cards(summary: Dict[str, int]) -> Dict[str, Any]:
        """构建摘要统计卡片。"""
        items = [
            ("已保号", summary["retained"], "success"),
            ("未保号", summary["unretained"], "warning"),
            ("无法判断", summary["unknown"], "grey"),
            ("陈旧/失败", summary["stale"], "error"),
        ]
        return {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 6, "sm": 3, "md": 3},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "tonal", "color": color},
                    "content": [{
                        "component": "VCardText",
                        "props": {"class": "text-center py-4 text-subtitle-1 font-weight-medium"},
                        "text": f"{label} {value}",
                    }],
                }],
            } for label, value, color in items],
        }

    def get_dashboard(self, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        """返回等级摘要仪表板；仅在页面加载时读取一次快照。"""
        rows = self._rows()
        summary = self._summary(rows)
        pending = [row["site_name"] for row in rows if row["result"].retained is False]
        elements = [self._summary_cards(summary)]
        elements.append({
            "component": "VAlert",
            "props": {"type": "warning" if pending else "success", "variant": "tonal", "class": "mt-2"},
            "text": "未保号站点：" + "、".join(pending) if pending else "当前没有明确未保号的站点",
        })
        return {"cols": 12}, {}, elements

    def stop_service(self):
        """幂等停止重算调度器。"""
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler:
            try:
                scheduler.remove_all_jobs()
                scheduler.shutdown(wait=False)
            except Exception as error:
                logger.debug(f"PT站点等级监控：停止调度器时忽略异常：{error}")
