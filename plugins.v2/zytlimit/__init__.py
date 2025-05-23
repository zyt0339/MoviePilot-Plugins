import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from app.helper.sites import SitesHelper
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import time
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.helper.torrent import TorrentHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo
from app.schemas.types import EventType


class ZYTLimit(_PluginBase):
    # 插件名称
    plugin_name = "QB&TR上传限速"
    # 插件描述
    plugin_desc = "自定义时间点执行限速逻辑,只支持qb"
    # 插件图标
    plugin_icon = "upload.png"
    # 插件版本
    plugin_version = "1.0.14"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytlimit_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 2

    downloader_helper = None
    sites_helper = None
    site_oper = None
    torrent_helper = None

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _onlyonce = False
    _notify = False
    _cron = None
    # _downloaders = []
    _downloaders1 = []
    _limit_sites1 = []
    _limit_speed1 = 0
    _limit_sites_pause_threshold1 = 0
    _active_time_range_site_config1 = None

    _downloaders2 = []
    _limit_sites2 = []
    _limit_speed2 = 0
    _limit_sites_pause_threshold2 = 0
    _active_time_range_site_config2 = None

    _downloaders3 = []
    _limit_sites3 = []
    _limit_speed3 = 0
    _limit_sites_pause_threshold3 = 0
    _active_time_range_site_config3 = None

    _downloaders4 = []
    _limit_sites4 = []
    _limit_speed4 = 0
    _limit_sites_pause_threshold4 = 0
    _active_time_range_site_config4 = None

    _downloaders5 = []
    _limit_sites5 = []
    _limit_speed5 = 0
    _limit_sites_pause_threshold5 = 0
    _active_time_range_site_config5 = None
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    to_pausedUP_hashs = {}  # 位于限速站点中因活动而暂停的种子hash,value=和最后活动时间

    def init_plugin(self, config: dict = None):
        self.sites_helper = SitesHelper()
        self.site_oper = SiteOper()
        self.torrent_helper = TorrentHelper()
        self.downloader_helper = DownloaderHelper()
        # 停止现有任务
        self.stop_service()
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._notify = config.get("notify")
            self._cron = config.get("cron")
            # self._downloaders = config.get("downloaders")

            self._downloaders1 = config.get("downloaders1")
            self._limit_sites1 = config.get("limit_sites1") or []
            self._limit_speed1 = int(config.get("limit_speed1") or 0)
            self._limit_sites_pause_threshold1 = int(config.get("limit_sites_pause_threshold1") or 0)
            self._active_time_range_site_config1 = config.get("active_time_range_site_config1")

            self._downloaders2 = config.get("downloaders2")
            self._limit_sites2 = config.get("limit_sites2") or []
            self._limit_speed2 = int(config.get("limit_speed2") or 0)
            self._limit_sites_pause_threshold2 = int(config.get("limit_sites_pause_threshold2") or 0)
            self._active_time_range_site_config2 = config.get("active_time_range_site_config2")

            self._downloaders3 = config.get("downloaders3")
            self._limit_sites3 = config.get("limit_sites3") or []
            self._limit_speed3 = int(config.get("limit_speed3") or 0)
            self._limit_sites_pause_threshold3 = int(config.get("limit_sites_pause_threshold3") or 0)
            self._active_time_range_site_config3 = config.get("active_time_range_site_config3")

            self._downloaders4 = config.get("downloaders4")
            self._limit_sites4 = config.get("limit_sites4") or []
            self._limit_speed4 = int(config.get("limit_speed4") or 0)
            self._limit_sites_pause_threshold4 = int(config.get("limit_sites_pause_threshold4") or 0)
            self._active_time_range_site_config4 = config.get("active_time_range_site_config4")

            self._downloaders5 = config.get("downloaders5")
            self._limit_sites5 = config.get("limit_sites5") or []
            self._limit_speed5 = int(config.get("limit_speed5") or 0)
            self._limit_sites_pause_threshold5 = int(config.get("limit_sites_pause_threshold5") or 0)
            self._active_time_range_site_config5 = config.get("active_time_range_site_config5")

            # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            # if self._enabled and self._cron:
            #     try:
            #         self._scheduler.add_job(
            #             func=self.run,
            #             trigger=CronTrigger.from_crontab(self._cron),
            #             name="QB&TR上传限速",
            #         )
            #     except Exception as err:
            #         logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"QB&TR上传限速服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="QB&TR上传限速",
                )
                # 关闭一次性开关
                self._onlyonce = False
            self.__update_config()

            # 启动任务
            # if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def get_downloader_service_infos(self, downloaders) -> Optional[Dict[str, ServiceInfo]]:
        """
        服务信息
        """
        if not downloaders:
            logger.warning("尚未配置下载器，请检查配置")
            return None

        services = self.downloader_helper.get_services(name_filters=downloaders)
        if not services:
            logger.warning("获取下载器实例失败，请检查配置")
            return None

        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"下载器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info

        if not active_services:
            logger.warning("没有已连接的下载器，请检查配置")
            return None
        return active_services

    def get_state(self) -> bool:
        return True if self._enabled and self._cron else False

    @eventmanager.register(EventType.PluginAction)
    def run(self, event: Event = None):
        # class _PluginBase(metaclass=ABCMeta)
        # 插件模块基类，通过继续该类实现插件功能
        # 除内置属性外，还有以下方法可以扩展或调用：
        # - stop_service() 停止插件服务
        # - get_config() 获取配置信息
        # - update_config() 更新配置信息
        # - init_plugin() 生效配置信息
        # - get_data_path() 获取插件数据保存目录
        logger.debug(f"QB&TR上传限速 run...")
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "limit":
                return
            logger.info("收到limit命令，开始QB&TR上传限速 ...")
            self.post_message(
                mtype=NotificationType.SiteMessage, title=f"开始QB&TR上传限速 ...")

        # if not self.get_downloader_service_infos:
        #     return

        msg = ""
        try:
            self.limit()
            success = True
        except Exception as e:
            success = False
            logger.error(f"QB&TR上传限速出错: {e}")
            msg = f"{e}"
        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB&TR限速成功】")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB&TR限速出错】", text=msg
                )

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
                定义远程控制命令
                :return: 命令关键字、事件、描述、附带数据
                """
        return [{
            "cmd": "/limit",
            "event": EventType.PluginAction,
            "desc": "QB&TR上传限速",
            "data": {
                "action": "limit"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self._enabled and self._cron:
            logger.info(f"QB&TR限速服务重新启动，执行周期 {self._cron}")
            return [{
                "id": "ZYTLimit",
                "name": "QB&TR限速",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run,
                "kwargs": {}
            }]
        logger.info("QB&TR限速服务未开启")
        return []

    def __custom_sites(self) -> List[Any]:
        custom_sites = []
        custom_sites_config = self.get_config("CustomSites")
        if custom_sites_config and custom_sites_config.get("enabled"):
            custom_sites = custom_sites_config.get("sites")
        return custom_sites

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 站点的可选项（内置站点 + 自定义站点）
        customSites = self.__custom_sites()

        # 站点的可选项
        site_options = ([{"title": site.name, "value": site.id}
                         for site in self.site_oper.list_order_by_pri()]
                        + [{"title": site.get("name"), "value": site.get("id")}
                           for site in customSites])
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
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
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "enabled",
                                                   "label": "启用插件"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 4},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "notify",
                                                   "label": "开启通知"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 4},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "onlyonce",
                                                   "label": "立即运行一次"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 12},
                                       "content": [
                                           {
                                               "component": "VCronField",
                                               "props": {"model": "cron", "label": "执行周期"}
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {'cols': 12},
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '限速一'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 2},
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "downloaders1",
                                                   "label": "下载器",
                                                   'items': [{"title": config.name, "value": config.name}
                                                             for config in self.downloader_helper.get_configs().values()]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "limit_sites1",
                                                   "label": "限速站点1",
                                                   "items": site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_speed1",
                                                   "label": "上行速度(KB)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_sites_pause_threshold1",
                                                   "label": "限速暂停(分钟)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "active_time_range_site_config1",
                                                   "label": "限速时间段",
                                                   "placeholder": "如：00:00-08:00,默认全天"
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {'cols': 12},
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '限速二'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 2},
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "downloaders2",
                                                   "label": "下载器",
                                                   'items': [{"title": config.name, "value": config.name}
                                                             for config in
                                                             self.downloader_helper.get_configs().values()]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "limit_sites2",
                                                   "label": "限速站点2",
                                                   "items": site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_speed2",
                                                   "label": "上行速度(KB)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_sites_pause_threshold2",
                                                   "label": "限速暂停(分钟)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "active_time_range_site_config2",
                                                   "label": "限速时间段",
                                                   "placeholder": "如：00:00-08:00,默认全天"
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {'cols': 12},
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '限速三'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 2},
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "downloaders3",
                                                   "label": "下载器",
                                                   'items': [{"title": config.name, "value": config.name}
                                                             for config in
                                                             self.downloader_helper.get_configs().values()]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "limit_sites3",
                                                   "label": "限速站点3",
                                                   "items": site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_speed3",
                                                   "label": "上行速度(KB)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_sites_pause_threshold3",
                                                   "label": "限速暂停(分钟)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "active_time_range_site_config3",
                                                   "label": "限速时间段",
                                                   "placeholder": "如：00:00-08:00,默认全天"
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {'cols': 12},
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '限速四'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 2},
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "downloaders4",
                                                   "label": "下载器",
                                                   'items': [{"title": config.name, "value": config.name}
                                                             for config in
                                                             self.downloader_helper.get_configs().values()]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "limit_sites4",
                                                   "label": "限速站点4",
                                                   "items": site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_speed4",
                                                   "label": "上行速度(KB)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_sites_pause_threshold4",
                                                   "label": "限速暂停(分钟)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "active_time_range_site_config4",
                                                   "label": "限速时间段",
                                                   "placeholder": "如：00:00-08:00,默认全天"
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {'cols': 12},
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '限速五'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 2},
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "downloaders5",
                                                   "label": "下载器",
                                                   'items': [{"title": config.name, "value": config.name}
                                                             for config in
                                                             self.downloader_helper.get_configs().values()]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "limit_sites5",
                                                   "label": "限速站点5",
                                                   "items": site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_speed5",
                                                   "label": "上行速度(KB)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "limit_sites_pause_threshold5",
                                                   "label": "限速暂停(分钟)",
                                                   "placeholder": ""
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 4,
                                           "md": 2
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "active_time_range_site_config5",
                                                   "label": "限速时间段",
                                                   "placeholder": "如：00:00-08:00,默认全天"
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                       },
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '配置重复时后面会覆盖前面。限速暂停时间(分钟):限速后还活动就暂停x分钟。限速时间段默认全天开启。'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           }
                       ]
                   }
               ], {
            "enabled": False,
            "onlyonce": False,
            "notify": False,
            "cron": "",
            # "downloaders": [],
            "downloaders1": [],
            "limit_sites1": [],
            "limit_speed1": 0,
            # "limit_sites_pause_threshold1": 0,
            "active_time_range_site_config1": None,
            "downloaders2": [],
            "limit_sites2": [],
            "limit_speed2": 0,
            # "limit_sites_pause_threshold2": 0,
            "active_time_range_site_config2": None,
            "downloaders3": [],
            "limit_sites3": [],
            "limit_speed3": 0,
            # "limit_sites_pause_threshold3": 0,
            "active_time_range_site_config3": None,
            "downloaders4": [],
            "limit_sites4": [],
            "limit_speed4": 0,
            # "limit_sites_pause_threshold4": 0,
            "active_time_range_site_config4": None,
            "downloaders5": [],
            "limit_sites5": [],
            "limit_speed5": 0,
            # "limit_sites_pause_threshold5": 0,
            "active_time_range_site_config5": None
        }

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": False,
            "notify": self._notify,
            "cron": self._cron,
            # "downloaders": self._downloaders,
            "downloaders1": self._downloaders1,
            "limit_sites1": self._limit_sites1,
            "limit_speed1": self._limit_speed1,
            "limit_sites_pause_threshold1": self._limit_sites_pause_threshold1,
            "active_time_range_site_config1": self._active_time_range_site_config1,
            "downloaders2": self._downloaders2,
            "limit_sites2": self._limit_sites2,
            "limit_speed2": self._limit_speed2,
            "limit_sites_pause_threshold2": self._limit_sites_pause_threshold2,
            "active_time_range_site_config2": self._active_time_range_site_config2,
            "downloaders3": self._downloaders3,
            "limit_sites3": self._limit_sites3,
            "limit_speed3": self._limit_speed3,
            "limit_sites_pause_threshold3": self._limit_sites_pause_threshold3,
            "active_time_range_site_config3": self._active_time_range_site_config3,
            "downloaders4": self._downloaders4,
            "limit_sites4": self._limit_sites4,
            "limit_speed4": self._limit_speed4,
            "limit_sites_pause_threshold4": self._limit_sites_pause_threshold4,
            "active_time_range_site_config4": self._active_time_range_site_config4,
            "downloaders5": self._downloaders5,
            "limit_sites5": self._limit_sites5,
            "limit_speed5": self._limit_speed5,
            "limit_sites_pause_threshold5": self._limit_sites_pause_threshold5,
            "active_time_range_site_config5": self._active_time_range_site_config5,
        })

    def limit(self):
        """
        开始限速
        """
        if self._downloaders1 or self._downloaders2 or self._downloaders3 or self._downloaders4 or self._downloaders5:
            pass
        else:
            logger.warning("未设置下载器,取消执行")
            return
        if self._limit_sites1 or self._limit_sites2 or self._limit_sites3 or self._limit_sites4 or self._limit_sites5:
            pass
        else:
            logger.warning("未设置限速站点,取消执行")
            return
        logger.info("开始执行限速逻辑 ...")
        # 站点name:id {}
        all_site_name_id_map = {}
        for site in self.site_oper.list_order_by_pri():
            all_site_name_id_map[site.name] = site.id
        for site in self.__custom_sites():
            all_site_name_id_map[site.get("name")] = site.get("id")
        all_site_names = set(all_site_name_id_map.keys())

        infos = [
            (self._downloaders1, self._limit_sites1, self._limit_speed1, self._limit_sites_pause_threshold1, self._active_time_range_site_config1),
            (self._downloaders3, self._limit_sites2, self._limit_speed2, self._limit_sites_pause_threshold2, self._active_time_range_site_config2),
            (self._downloaders3, self._limit_sites3, self._limit_speed3, self._limit_sites_pause_threshold3, self._active_time_range_site_config3),
            (self._downloaders4, self._limit_sites4, self._limit_speed4, self._limit_sites_pause_threshold4, self._active_time_range_site_config4),
            (self._downloaders5, self._limit_sites5, self._limit_speed5, self._limit_sites_pause_threshold5, self._active_time_range_site_config5),
        ]
        for info in infos:
            downloaders, limit_sites, limit_speed, limit_sites_pause_threshold, active_time_range_site_config = info
            is_in_time_range = self.__is_current_time_in_range_site_config(active_time_range_site_config)
            if not downloaders or not limit_sites:
                return
            downloader_service_infos = self.get_downloader_service_infos(downloaders)
            if not downloader_service_infos:
                return
            for downloader_service_info in downloader_service_infos.values():
                self.limit_per_downloader(all_site_name_id_map, all_site_names, downloader_service_info,
                                          limit_sites, limit_speed, limit_sites_pause_threshold, is_in_time_range)
        # 保存缓存
        # self.__update_config()
        logger.info("限速执行完成")

    def limit_per_downloader(self, all_site_name_id_map, all_site_names, downloader_service_info,
                                          limit_sites, limit_speed, limit_sites_pause_threshold, is_in_time_range):
        downloader = downloader_service_info.name
        downloader_obj = downloader_service_info.instance
        dl_type = downloader_service_info.type
        # 设置限速
        to_limit_torrent_hashs = []
        cancel_limit_torrent_hashs = []
        cancel_limit_torrent_hashs_other = []
        # 限速后仍然活动种子处理↓
        # 限速100K中,且活动的种子,本次要暂停
        to_pausedUP_hashs_cur = []
        # 已经暂停,暂停时间超过x分钟的种子,本次要重新开始
        to_cancel_pausedUP_hashs_cur = []
        # 当前时间戳
        current_time = time.time()
        _limit_sites_pause_threshold_s = limit_sites_pause_threshold * 60
        # 限速后仍然活动种子处理↑
        if dl_type == "qbittorrent":
            logger.info(f"{downloader} 开始设置限速 ...")
            all_torrents, _ = downloader_obj.get_torrents()
            for torrent in all_torrents:
                # 当前种子 tags list
                current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                # qb 补充站点标签,交集第一个就是站点标签
                intersection = all_site_names.intersection(current_torrent_tag_list)
                if intersection:
                    site_name = list(intersection)[0]
                    site_id = all_site_name_id_map[site_name] or -1
                else:
                    site_id = -1
                    logger.error(f"{torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                if site_id in limit_sites:
                    if is_in_time_range:
                        to_limit_torrent_hashs.append(torrent.hash)
                        # 限速后还活动就暂停
                        if limit_sites_pause_threshold > 0:
                            state = torrent.state  # str
                            if "uploading" == state:
                                to_pausedUP_hashs_cur.append(torrent.hash)
                            elif state in ["pausedUP", "stoppedUP"] and ('暂停' not in current_torrent_tag_list):
                                pausedUPTime = self.to_pausedUP_hashs.get(torrent.hash, 0)
                                if (current_time - pausedUPTime) > _limit_sites_pause_threshold_s:
                                    to_cancel_pausedUP_hashs_cur.append(torrent.hash)
                    else:
                        cancel_limit_torrent_hashs.append(torrent.hash)
                        to_cancel_pausedUP_hashs_cur.append(torrent.hash)
                else:
                    cancel_limit_torrent_hashs_other.append(torrent.hash)
            if to_limit_torrent_hashs:
                downloader_obj.qbc.torrents_set_upload_limit(1024 * limit_speed, to_limit_torrent_hashs)
                logger.info(f"{downloader} 限速{limit_speed}K种子个数: {len(to_limit_torrent_hashs)}")
            # 其他的都是不限速的,塞到一个list吧
            cancel_limit_list_all = cancel_limit_torrent_hashs + cancel_limit_torrent_hashs_other
            logger.info(f"{downloader} 取消限速种子个数{len(cancel_limit_list_all)}")
            downloader_obj.qbc.torrents_set_upload_limit(0, cancel_limit_list_all)

            # 限速中仍然有上传就暂停
            if to_pausedUP_hashs_cur:
                downloader_obj.stop_torrents(to_pausedUP_hashs_cur)
                # downloader_obj.set_torrents_tag(to_pausedUP_hashs_cur, ["P"])
                logger.info(f"{downloader} 限速后仍活动,暂停种子个数: {len(to_pausedUP_hashs_cur)}")
                for t_hash in to_pausedUP_hashs_cur:
                    self.to_pausedUP_hashs[t_hash] = current_time
            if to_cancel_pausedUP_hashs_cur:
                downloader_obj.start_torrents(to_cancel_pausedUP_hashs_cur)
                # downloader_obj.remove_torrents_tag(to_cancel_pausedUP_hashs_cur, ["P"])
                logger.info(f"{downloader} 到达暂停时间,重新开始种子个数: {len(to_cancel_pausedUP_hashs_cur)}")
                for t_hash in to_cancel_pausedUP_hashs_cur:
                    if t_hash in self.to_pausedUP_hashs:
                        del self.to_pausedUP_hashs[t_hash]

        elif dl_type == "transmission":
            logger.info(f"{downloader} 开始设置限速 ...")
            _trarg = ["id", "name", "labels", "hashString", "status", "rateUpload"]
            tr_client = downloader_obj.trc
            all_torrents = tr_client.get_torrents(arguments=_trarg)
            # all_torrents, _ = downloader_obj.get_torrents()
            for torrent in all_torrents:
                # 当前种子 tags list
                current_torrent_tag_list = [element.strip() for element in torrent.labels]
                # qb 补充站点标签,交集第一个就是站点标签
                intersection = all_site_names.intersection(current_torrent_tag_list)
                if intersection:
                    site_name = list(intersection)[0]
                    site_id = all_site_name_id_map[site_name] or -1
                else:
                    site_id = -1
                    logger.error(f"{torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                if site_id in limit_sites:
                    if is_in_time_range:
                        to_limit_torrent_hashs.append(torrent.hashString)
                        # 限速后还活动就暂停
                        if limit_sites_pause_threshold > 0:
                            state = torrent.status  # Enum
                            if state.seeding and torrent.rate_upload > 0:
                                to_pausedUP_hashs_cur.append(torrent.hashString)
                            elif state.stopped and ('暂停' not in current_torrent_tag_list):
                                pausedUPTime = self.to_pausedUP_hashs.get(torrent.hashString, 0)
                                if (current_time - pausedUPTime) > _limit_sites_pause_threshold_s:
                                    to_cancel_pausedUP_hashs_cur.append(torrent.hashString)
                    else:
                        cancel_limit_torrent_hashs.append(torrent.hashString)
                        to_cancel_pausedUP_hashs_cur.append(torrent.hashString)
                else:
                    cancel_limit_torrent_hashs_other.append(torrent.hashString)
            if to_limit_torrent_hashs:
                tr_client.change_torrent(ids=to_limit_torrent_hashs, upload_limit=limit_speed,
                                         upload_limited=True)
                logger.info(f"{downloader} 限速{limit_speed}K种子个数: {len(to_limit_torrent_hashs)}")
            # 其他的都是不限速的,塞到一个list吧
            cancel_limit_list_all = cancel_limit_torrent_hashs + cancel_limit_torrent_hashs_other
            logger.info(f"{downloader} 取消限速种子个数{len(cancel_limit_list_all)}")
            tr_client.change_torrent(ids=cancel_limit_list_all, upload_limit=0, upload_limited=False)

            # 限速中仍然有上传就暂停
            if to_pausedUP_hashs_cur:
                downloader_obj.stop_torrents(to_pausedUP_hashs_cur)
                # downloader_obj.set_torrents_tag(to_pausedUP_hashs_cur, ["P"])
                logger.info(f"{downloader} 限速后仍活动,暂停种子个数: {len(to_pausedUP_hashs_cur)}")
                for t_hash in to_pausedUP_hashs_cur:
                    self.to_pausedUP_hashs[t_hash] = current_time
            if to_cancel_pausedUP_hashs_cur:
                downloader_obj.start_torrents(to_cancel_pausedUP_hashs_cur)
                # downloader_obj.remove_torrents_tag(to_cancel_pausedUP_hashs_cur, ["P"])
                logger.info(f"{downloader} 到达暂停时间,重新开始种子个数: {len(to_cancel_pausedUP_hashs_cur)}")
                for t_hash in to_cancel_pausedUP_hashs_cur:
                    if t_hash in self.to_pausedUP_hashs:
                        del self.to_pausedUP_hashs[t_hash]

    def __is_current_time_in_range_site_config(self, active_time_range_site_config) -> bool:
        """判断当前时间是否在时间区间内-默认全天"""
        if not self.__is_valid_time_range(active_time_range_site_config):
            # 如果时间范围格式不正确或不存在，说明当前没有开启时间段，返回True
            return True

        start_str, end_str = active_time_range_site_config.split('-')
        start_time = datetime.strptime(start_str, '%H:%M').time()
        end_time = datetime.strptime(end_str, '%H:%M').time()
        now = datetime.now().time()
        if start_time <= end_time:
            # 情况1: 时间段不跨越午夜
            return start_time <= now <= end_time
        else:
            # 情况2: 时间段跨越午夜
            return now >= start_time or now <= end_time

    @staticmethod
    def __is_valid_time_range(time_range: str) -> bool:
        """检查时间范围字符串是否有效：格式为"HH:MM-HH:MM"，且时间有效"""
        if not time_range:
            return False

        # 使用正则表达式匹配格式
        pattern = re.compile(r'^\d{2}:\d{2}-\d{2}:\d{2}$')
        if not pattern.match(time_range):
            return False

        try:
            start_str, end_str = time_range.split('-')
            datetime.strptime(start_str, '%H:%M').time()
            datetime.strptime(end_str, '%H:%M').time()
        except Exception as e:
            print(str(e))
            return False

        return True

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))


