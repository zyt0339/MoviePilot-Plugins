"""PT 站点等级监控插件。"""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.chain.site import SiteChain
from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
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

    plugin_name = "PT站点等级监控"
    plugin_desc = "展示站点当前等级、保号状态和下一等级缺口。"
    plugin_icon = "database.png"
    plugin_version = "1.5.0"
    plugin_author = "zyt0339"
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins"
    plugin_config_prefix = "ptdepilermp_"
    plugin_order = 20
    auth_level = 2

    _show_dashboard = False
    _diagnose_once = False
    _onlyonce = False
    _cron = ""
    _scheduler: Optional[BackgroundScheduler] = None

    def __init__(self):
        super().__init__()
        self._repository = RuleRepository()
        self._refresh_lock = Lock()
        self._refresh_pending = False

    def init_plugin(self, config: dict = None):
        """载入插件配置和磁盘站点规则。"""
        self.stop_service()
        config = dict(config or {})
        self._show_dashboard = bool(config.get("show_dashboard", config.get("enabled", False)))
        self._diagnose_once = bool(config.get("diagnose_once", False))
        self._onlyonce = bool(config.get("onlyonce", False))
        self._cron = str(config.get("cron") or "").strip()
        self._repository.reload()
        self._configure_refresh_jobs()

    def get_state(self) -> bool:
        """返回插件启用状态。"""
        return self._show_dashboard or bool(self._scheduler)

    def _save_current_config(self) -> None:
        """保存当前配置，并确保一次性开关不会重复执行。"""
        self.update_config({
            "show_dashboard": self._show_dashboard,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "diagnose_once": self._diagnose_once,
        })

    def _configure_refresh_jobs(self) -> None:
        """注册一次性与 cron 全站数据刷新任务。"""
        if not self._onlyonce and not self._cron:
            return
        scheduler = BackgroundScheduler(timezone=settings.TZ)
        has_jobs = False
        if self._onlyonce:
            scheduler.add_job(
                self._run_refresh_all,
                "date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                max_instances=1,
                name="PT站点等级数据立即刷新",
            )
            has_jobs = True
            self._onlyonce = False
            self._save_current_config()
        if self._cron:
            try:
                scheduler.add_job(
                    self._run_refresh_all,
                    CronTrigger.from_crontab(self._cron),
                    max_instances=1,
                    coalesce=True,
                    name="PT站点等级数据定时刷新",
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
        """返回带 Bearer 鉴权的手动刷新接口。"""
        return [{
            "path": "/refresh_site",
            "endpoint": self.refresh_site,
            "methods": ["POST"],
            "auth": "bear",
            "summary": "刷新单个站点数据",
        }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """返回插件配置表单和默认值。"""
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
                                        "label": "立即刷新一次全部站点",
                                        "color": "error",
                                    },
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "show_dashboard", "label": "显示仪表板"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "diagnose_once",
                                        "label": "输出一次调查日志",
                                        "color": "warning",
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
                                "component": "VTextField",
                                "props": {
                                    "model": "cron",
                                    "label": "全站数据刷新周期",
                                    "placeholder": "5 位 cron 表达式，留空不定时刷新",
                                    "hint": "例如：0 8 * * * 表示每天 08:00",
                                    "persistent-hint": True,
                                },
                            }],
                        }],
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "warning", "variant": "tonal", "class": "mb-4"},
                        "text": "立即运行和 cron 会刷新全部启用站点；单站按钮只刷新对应站点。刷新会真实访问 PT 站点，并可能触发站点消息和低分享率提醒。",
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "info", "variant": "tonal", "class": "mt-4"},
                        "text": "等级配置来自插件 site_rules 目录。修改或新增单站 JSON 后刷新详情页即可重新读取。",
                    },
                ],
            }
        ], {
            "show_dashboard": False,
            "onlyonce": False,
            "cron": "",
            "diagnose_once": False,
        }

    @staticmethod
    def _object_dict(value: Any) -> Dict[str, Any]:
        """从 ORM 对象提取计算所需的非敏感字段。"""
        keys = (
            "user_level", "join_at", "upload", "download", "ratio", "bonus",
            "seeding", "leeching", "seeding_size", "updated_day", "updated_time", "err_msg",
        )
        return {key: getattr(value, key, None) for key in keys}

    def _rows(self) -> List[Dict[str, Any]]:
        """读取启用站点和最新快照，生成页面安全数据。"""
        self._repository.reload()
        sites = SiteOper().list_active() or []
        latest = SiteOper().get_userdata_latest() or []
        # get_userdata_latest 可能返回同一天的多条记录，查询结果按时间倒序；只保留第一条。
        latest_by_domain = {}
        for item in latest:
            latest_by_domain.setdefault(getattr(item, "domain", None), item)
        today = datetime.now(tz=pytz.timezone(settings.TZ)).strftime("%Y-%m-%d")
        rows = []
        for site in sites:
            data = latest_by_domain.get(getattr(site, "domain", None))
            user = self._object_dict(data) if data else {}
            rule_id, rule = self._repository.match(site.name)
            result = self._repository.evaluate_site(user, rule_id, rule)
            rows.append({
                "site_id": site.id,
                "site_name": site.name,
                "user": user,
                "rule": rule,
                "result": result,
                "stale": not data or user.get("updated_day") != today or bool(user.get("err_msg")),
            })
        if self._diagnose_once:
            self._write_diagnostic_log(rows)
            self._diagnose_once = False
            self._save_current_config()
        return rows

    def _write_diagnostic_log(self, rows: List[Dict[str, Any]]) -> None:
        """输出一次不含域名和认证信息的等级判断调查日志。"""
        logger.info(
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
            logger.info(
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
            logger.info("PT站点等级监控调查：无效规则=" + "；".join(f"{name}: {reason}" for name, reason in details))

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
            text, color = "未达保号", "warning"
        else:
            text, color = "无法判断", "grey"
        return {
            "component": "VChip",
            "props": {"color": color, "variant": "tonal", "size": "small"},
            "text": text,
        }

    @staticmethod
    def _cell(text: Any, content: Optional[List[dict]] = None) -> Dict[str, Any]:
        """构建表格单元格。"""
        cell = {"component": "td", "props": {"class": "text-sm whitespace-nowrap"}}
        if content is not None:
            cell["content"] = content
        else:
            cell["text"] = str(text)
        return cell

    def _refresh_button(self, site_id: Any) -> Dict[str, Any]:
        """构建单站刷新按钮。"""
        return {
            "component": "VBtn",
            "props": {"size": "small", "variant": "tonal", "prepend-icon": "mdi-refresh"},
            "text": "刷新",
            "events": {"click": {
                "api": f"plugin/{self.__class__.__name__}/refresh_site",
                "method": "post",
                "params": {"site_id": site_id},
            }},
        }

    def _summary(self, rows: List[Dict[str, Any]]) -> Dict[str, int]:
        """汇总站点匹配、保号和陈旧数据数量。"""
        return {
            "configured": len(rows),
            "matched": sum(1 for row in rows if row["result"].rule_id),
            "retained": sum(1 for row in rows if row["result"].retained is True),
            "unretained": sum(1 for row in rows if row["result"].retained is False),
            "unknown": sum(1 for row in rows if row["result"].retained is None),
            "stale": sum(1 for row in rows if row["stale"]),
        }

    @staticmethod
    def _retention_level(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """返回首个普通保号等级；VIP/管理等级直接返回当前特殊等级。"""
        result = row["result"]
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
        if result.reason:
            return result.reason
        if result.current_group in {"vip", "manager"}:
            return "VIP/管理等级，已保号"
        if not retained_level:
            return "规则未配置保号等级"
        if result.retained:
            return "已达到保号等级"
        if not requirement:
            return "保号条件数据不足"
        proxy = SiteLevelResult(
            result.rule_id, result.current_level, result.current_group,
            retained_level, requirement, result.retained,
        )
        detail = self._gap_text(proxy)
        return "尚未达到保号等级；" + detail

    def get_page(self) -> List[dict]:
        """返回站点等级详情页。"""
        rows = self._rows()
        summary = self._summary(rows)
        header_names = [
            "站点", "状态", "当前等级", "保号等级", "上传/下载", "分享率",
            "保号上传/下载", "保号总结", "数据时间", "操作",
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
                    self._cell(row["site_name"]),
                    self._cell("", [self._status_chip(result)]),
                    self._cell(current_name),
                    self._cell((retained_level or {}).get("name") or "未配置"),
                    self._cell(f"{self._size(user.get('upload'))} / {self._size(user.get('download'))}"),
                    self._cell(self._number(user.get("ratio"))),
                    self._cell(
                        f"{self._requirement_size(retained_level or {}, 'uploaded')} / "
                        f"{self._requirement_size(retained_level or {}, 'downloaded')}"
                    ),
                    self._cell(self._retention_summary(result, retained_level, retained_requirement)),
                    self._cell(update_text),
                    self._cell("", [self._refresh_button(row["site_id"])]),
                ],
            })
            panels.append(self._level_panel(row))

        return [{
            "component": "div",
            "content": [
                self._summary_cards(summary),
                {
                    "component": "VAlert",
                    "props": {"type": "warning", "variant": "tonal", "class": "my-4"},
                    "text": (
                        "刷新会真实访问 PT 站点，并可能触发站点消息；非当天快照统一标记为陈旧。"
                        + (f" 当前有 {self._repository.load_errors} 个规则文件无效并已跳过。" if self._repository.load_errors else "")
                    ),
                },
                {
                    "component": "VTable",
                    "props": {"hover": True, "fixed-header": True, "density": "compact"},
                    "content": [
                        {"component": "thead", "content": [{
                            "component": "tr",
                            "content": [{"component": "th", "text": name} for name in header_names],
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
        for level in sorted(rule.get("levels") or [], key=lambda item: item.get("id", -1)):
            evaluation = evaluate_requirement(user, level)
            reached = current_id is not None and level.get("id", -1) <= current_id
            if reached:
                icon, color, detail = "mdi-check-circle", "success", "已达到"
            elif result.next_level and level.get("id") == result.next_level.get("id"):
                icon, color, detail = "mdi-arrow-right-circle", "warning", self._gap_text(result)
            elif evaluation.status == "unknown":
                icon, color, detail = "mdi-help-circle", "grey", "数据不足"
            elif evaluation.status == "met":
                icon, color, detail = "mdi-check-circle-outline", "info", "条件已满足"
            else:
                icon, color, detail = "mdi-circle-outline", "grey", "未达到"
            level_rows.append({
                "component": "VListItem",
                "props": {
                    "prepend-icon": icon,
                    "base-color": color,
                    "title": level.get("name") or "未命名等级",
                    "subtitle": (
                        f"{detail}；上传 {self._requirement_size(level, 'uploaded')}；下载 {self._requirement_size(level, 'downloaded')}；"
                        f"注册时长 {self._duration(level.get('interval'))}{self._extra_requirement_text(level)}"
                    ),
                },
            })
        if not level_rows:
            level_rows = [{
                "component": "VListItem",
                "props": {"title": result.reason or "无可用规则"},
            }]
        return {
            "component": "VExpansionPanel",
            "content": [
                {"component": "VExpansionPanelTitle", "text": row["site_name"]},
                {"component": "VExpansionPanelText", "content": [{"component": "VList", "content": level_rows}]},
            ],
        }

    @staticmethod
    def _summary_cards(summary: Dict[str, int]) -> Dict[str, Any]:
        """构建摘要统计卡片。"""
        items = [
            ("启用站点", summary["configured"], "primary"),
            ("规则匹配", summary["matched"], "info"),
            ("已保号", summary["retained"], "success"),
            ("未保号", summary["unretained"], "warning"),
            ("无法判断", summary["unknown"], "grey"),
            ("陈旧/失败", summary["stale"], "error"),
        ]
        return {
            "component": "VRow",
            "content": [{
                "component": "VCol",
                "props": {"cols": 6, "sm": 4, "md": 2},
                "content": [{
                    "component": "VCard",
                    "props": {"variant": "tonal", "color": color},
                    "content": [
                        {"component": "VCardTitle", "text": str(value)},
                        {"component": "VCardSubtitle", "text": label},
                    ],
                }],
            } for label, value, color in items],
        }

    def get_dashboard(self, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        """返回等级摘要仪表板；仅在页面加载时读取一次快照。"""
        if not self._show_dashboard:
            return None
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

    def _run_refresh_all(self) -> None:
        """互斥执行全部启用站点的数据刷新。"""
        with self._refresh_lock:
            if self._refresh_pending:
                logger.warning("PT站点等级监控：已有刷新任务，跳过本次全站刷新")
                return
            self._refresh_pending = True
        try:
            logger.info("PT站点等级监控：开始刷新全部启用站点数据")
            SiteChain().refresh_userdatas()
            logger.info("PT站点等级监控：全部启用站点数据刷新完成")
        except Exception as error:
            logger.error(f"PT站点等级监控：全站刷新任务失败：{error}")
        finally:
            with self._refresh_lock:
                self._refresh_pending = False

    def _enqueue_refresh(self, site_id: int) -> bool:
        """把刷新请求放入插件单任务调度器，并拒绝重复请求。"""
        with self._refresh_lock:
            if self._refresh_pending:
                return False
            self._refresh_pending = True
            if not self._scheduler:
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                self._scheduler.start()
            self._scheduler.add_job(
                self._run_refresh,
                "date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=1),
                args=[site_id],
                max_instances=1,
                name="PT站点等级数据刷新",
            )
            return True

    def _run_refresh(self, site_id: int):
        """在后台复用 MoviePilot 宿主刷新逻辑。"""
        try:
            site = next((item for item in (SiteOper().list_active() or []) if item.id == site_id), None)
            if not site:
                logger.warning("PT站点等级监控：待刷新站点不存在或未启用")
                return
            indexer = SitesHelper().get_indexer(site.domain)
            if not indexer:
                logger.warning("PT站点等级监控：待刷新站点未找到站点定义")
                return
            SiteChain().refresh_userdata(site=indexer)
        except Exception as error:
            logger.error(f"PT站点等级监控：刷新任务失败：{error}")
        finally:
            with self._refresh_lock:
                self._refresh_pending = False

    def refresh_site(self, site_id: int) -> schemas.Response:
        """校验并受理单站刷新请求。"""
        try:
            selected_id = int(site_id)
        except (TypeError, ValueError):
            return schemas.Response(success=False, message="站点 ID 无效")
        site = next((item for item in (SiteOper().list_active() or []) if item.id == selected_id), None)
        if not site:
            return schemas.Response(success=False, message="站点不存在或未启用")
        if not SitesHelper().get_indexer(site.domain):
            return schemas.Response(success=False, message="站点定义不存在")
        if not self._enqueue_refresh(selected_id):
            return schemas.Response(success=False, message="已有刷新任务正在执行")
        return schemas.Response(success=True, message="单站刷新任务已受理")

    def stop_service(self):
        """幂等停止调度器并释放刷新状态。"""
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler:
            try:
                scheduler.remove_all_jobs()
                scheduler.shutdown(wait=False)
            except Exception as error:
                logger.debug(f"PT站点等级监控：停止调度器时忽略异常：{error}")
        with self._refresh_lock:
            self._refresh_pending = False
