"""PT 站点等级监控插件。"""

from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from app import schemas
from app.chain.site import SiteChain
from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.log import logger
from app.plugins import _PluginBase
from app.utils.string import StringUtils

from app.plugins.ptdepilermp.rules import RuleRepository, SiteLevelResult, evaluate_requirement, parse_size


class PTDepilerMp(_PluginBase):
    """使用 MoviePilot 站点快照计算 PT 等级与保号状态。"""

    plugin_name = "PT站点等级监控"
    plugin_desc = "展示站点当前等级、保号状态和下一等级缺口。"
    plugin_icon = "database.png"
    plugin_version = "1.2.0"
    plugin_author = "zyt0339"
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins"
    plugin_config_prefix = "ptdepilermp_"
    plugin_order = 20
    auth_level = 2

    _enabled = False
    _allow_refresh_all = False
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
        self._enabled = bool(config.get("enabled", False))
        self._allow_refresh_all = bool(config.get("allow_refresh_all", False))
        self._repository.reload()

    def get_state(self) -> bool:
        """返回插件启用状态。"""
        return self._enabled

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
        return [
            {
                "path": "/refresh_site",
                "endpoint": self.refresh_site,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "刷新单个站点数据",
            },
            {
                "path": "/refresh_all",
                "endpoint": self.refresh_all,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "刷新全部启用站点数据",
            },
        ]

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
                                    "props": {"model": "enabled", "label": "启用插件"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "allow_refresh_all",
                                        "label": "允许刷新全部站点（默认关闭）",
                                        "color": "error",
                                    },
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "warning", "variant": "tonal", "class": "mb-4"},
                        "text": "刷新会真实访问 PT 站点，并可能触发 MoviePilot 的站点消息、低分享率提醒等既有副作用。",
                    },
                    {
                        "component": "VAlert",
                        "props": {"type": "info", "variant": "tonal", "class": "mt-4"},
                        "text": "等级配置来自插件 site_rules 目录。修改或新增单站 JSON 后刷新详情页即可重新读取。",
                    },
                ],
            }
        ], {
            "enabled": False,
            "allow_refresh_all": False,
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
        return rows

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
            parts.append("其他条件 " + "、".join(other))
        if requirement.unknown:
            parts.append("数据不足：" + "、".join(requirement.unknown))
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

    def get_page(self) -> List[dict]:
        """返回站点等级详情页。"""
        rows = self._rows()
        summary = self._summary(rows)
        header_names = [
            "站点", "状态", "当前等级", "上传/下载", "分享率", "入站时间", "下一等级",
            "目标上传/下载", "所需时间", "缺少值/其他条件", "数据时间", "操作",
        ]
        table_rows = []
        panels = []
        for row in rows:
            user, result = row["user"], row["result"]
            next_level = result.next_level or {}
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
                    self._cell(f"{self._size(user.get('upload'))} / {self._size(user.get('download'))}"),
                    self._cell(self._number(user.get("ratio"))),
                    self._cell(user.get("join_at") or "数据不足"),
                    self._cell(next_level.get("name") or "—"),
                    self._cell(f"{self._size(next_level.get('uploaded'))} / {self._size(next_level.get('downloaded'))}"),
                    self._cell(self._duration(next_level.get("interval"))),
                    self._cell(result.reason or self._gap_text(result)),
                    self._cell(update_text),
                    self._cell("", [self._refresh_button(row["site_id"])]),
                ],
            })
            panels.append(self._level_panel(row))

        actions = []
        if self._allow_refresh_all:
            actions = [{
                "component": "VBtn",
                "props": {"color": "error", "variant": "tonal", "prepend-icon": "mdi-refresh"},
                "text": "刷新全部站点",
                "events": {"click": {
                    "api": f"plugin/{self.__class__.__name__}/refresh_all",
                    "method": "post",
                }},
            }]
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
                {"component": "div", "props": {"class": "d-flex justify-end mb-3"}, "content": actions},
                {
                    "component": "VTable",
                    "props": {"hover": True, "fixed-header": True},
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
            else:
                icon, color, detail = "mdi-circle-outline", "grey", "未达到"
            level_rows.append({
                "component": "VListItem",
                "props": {
                    "prepend-icon": icon,
                    "base-color": color,
                    "title": level.get("name") or "未命名等级",
                    "subtitle": (
                        f"{detail}；上传 {self._size(level.get('uploaded'))}；下载 {self._size(level.get('downloaded'))}；"
                        f"注册时长 {self._duration(level.get('interval'))}"
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
            ("已配置", summary["configured"], "primary"),
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
        """返回带自动刷新的等级摘要仪表板。"""
        if not self._enabled:
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
        return {"cols": 12}, {"refresh": 60}, elements

    def _enqueue_refresh(self, mode: str, site_id: Optional[int] = None) -> bool:
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
                args=[mode, site_id],
                max_instances=1,
                name="PT站点等级数据刷新",
            )
            return True

    def _run_refresh(self, mode: str, site_id: Optional[int]):
        """在后台复用 MoviePilot 宿主刷新逻辑。"""
        try:
            if mode == "all":
                SiteChain().refresh_userdatas()
                return
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
        if not self._enabled:
            return schemas.Response(success=False, message="插件未启用")
        try:
            selected_id = int(site_id)
        except (TypeError, ValueError):
            return schemas.Response(success=False, message="站点 ID 无效")
        site = next((item for item in (SiteOper().list_active() or []) if item.id == selected_id), None)
        if not site:
            return schemas.Response(success=False, message="站点不存在或未启用")
        if not SitesHelper().get_indexer(site.domain):
            return schemas.Response(success=False, message="站点定义不存在")
        if not self._enqueue_refresh("site", selected_id):
            return schemas.Response(success=False, message="已有刷新任务正在执行")
        return schemas.Response(success=True, message="单站刷新任务已受理")

    def refresh_all(self) -> schemas.Response:
        """在配置允许时受理全站刷新请求。"""
        if not self._enabled:
            return schemas.Response(success=False, message="插件未启用")
        if not self._allow_refresh_all:
            return schemas.Response(success=False, message="全站刷新未开启")
        if not self._enqueue_refresh("all"):
            return schemas.Response(success=False, message="已有刷新任务正在执行")
        return schemas.Response(success=True, message="全站刷新任务已受理")

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
