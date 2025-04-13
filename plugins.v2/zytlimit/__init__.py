import re
from datetime import datetime, timedelta
from threading import Event
from typing import Any, Dict, List, Optional, Tuple

import pytz
from app.helper.sites import SitesHelper
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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
    plugin_name = "QB限速管理"
    # 插件描述
    plugin_desc = "自定义时间点执行限速逻辑,只支持qb"
    # 插件图标
    plugin_icon = "Qbittorrent_A.png"
    # 插件版本
    plugin_version = "1.0.2"
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
    _downloaders = []
    _limit_sites1 = []
    _limit_speed1 = 0
    # _limit_sites_pause_threshold1 = 0
    _active_time_range_site_config1 = None

    _limit_sites2 = []
    _limit_speed2 = 0
    # _limit_sites_pause_threshold2 = 0
    _active_time_range_site_config2 = None

    _limit_sites3 = []
    _limit_speed3 = 0
    # _limit_sites_pause_threshold3 = 0
    _active_time_range_site_config3 = None
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

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
            self._downloaders = config.get("downloaders")

            self._limit_sites1 = config.get("limit_sites1") or []
            self._limit_speed1 = config.get("limit_speed1") or 0
            # self._limit_sites_pause_threshold1 = config.get("limit_sites_pause_threshold1") or 0
            self._active_time_range_site_config1 = config.get("active_time_range_site_config1")

            self._limit_sites2 = config.get("limit_sites2") or []
            self._limit_speed2 = config.get("limit_speed2") or 0
            # self._limit_sites_pause_threshold2 = config.get("limit_sites_pause_threshold2") or 0
            self._active_time_range_site_config2 = config.get("active_time_range_site_config2")

            self._limit_sites3 = config.get("limit_sites3") or []
            self._limit_speed3 = config.get("limit_speed3") or 0
            # self._limit_sites_pause_threshold3 = config.get("limit_sites_pause_threshold3") or 0
            self._active_time_range_site_config3 = config.get("active_time_range_site_config3")

            # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(
                        func=self.run,
                        trigger=CronTrigger.from_crontab(self._cron),
                        name="QB限速管理",
                    )
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"QB限速管理服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="QB限速管理",
                )
                # 关闭一次性开关
                self._onlyonce = False
                self.__update_config()

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.logger.info_jobs()
                self._scheduler.start()

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        """
        服务信息
        """
        if not self._downloaders:
            logger.warning("尚未配置下载器，请检查配置")
            return None

        services = self.downloader_helper.get_services(name_filters=self._downloaders)
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
        return True if self._enabled and self._downloaders else False

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
        logger.debug(f"QB限速管理 run...")
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "limit":
                return
            logger.info("收到limit命令，开始QB限速管理 ...")
            self.post_message(
                mtype=NotificationType.SiteMessage, title=f"开始QB限速管理 ...")

        if not self.service_infos:
            return

        msg = ""
        success = False
        try:
            self.auto_seed()
            success = True
        except Exception as e:
            success = False
            logger.error(f"QB限速管理出错: {e}")
            msg = f"{e}"
        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB限速管理成功】")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB限速管理出错】", text=msg
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
            "desc": "QB限速管理",
            "data": {
                "action": "limit"
            }
        }]

    def get_api(self) -> List[Dict[str, Any]]:
        pass

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
                                                   "label": "启用插件",
                                               },
                                           }
                                       ],
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 4},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "notify",
                                                   "label": "开启通知",
                                               },
                                           }
                                       ],
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 4},
                                       "content": [
                                           {
                                               "component": "VSwitch",
                                               "props": {
                                                   "model": "onlyonce",
                                                   "label": "立即运行一次",
                                               },
                                           }
                                       ],
                                   },
                               ],
                           },
                           {
                               "component": "VRow",
                               "content": [
                                   {
                                       'component': 'VCol',
                                       "props": {"cols": 12, "md": 6},
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'chips': True,
                                                   'multiple': True,
                                                   'clearable': True,
                                                   'model': 'downloaders',
                                                   'label': '下载器',
                                                   'items': [
                                                       {"title": config.name, "value": config.name}
                                                       for config in
                                                       self.downloader_helper.get_configs().values()]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 6},
                                       "content": [
                                           {
                                               "component": "VCronField",
                                               "props": {"model": "cron", "label": "执行周期"},
                                           }
                                       ],
                                   },
                               ],
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 6
                                       },
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'chips': True,
                                                   'multiple': True,
                                                   'clearable': True,
                                                   'model': 'limit_sites1',
                                                   'label': '限速站点1',
                                                   'items': site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'limit_speed1',
                                                   'label': '上行速度(KB)',
                                                   'placeholder': "例如100就是100K"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'active_time_range_site_config1',
                                                   'label': '生效时间段',
                                                   'placeholder': '如：00:00-08:00,默认全天'
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
                                           'md': 6
                                       },
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'chips': True,
                                                   'multiple': True,
                                                   'clearable': True,
                                                   'model': 'limit_sites2',
                                                   'label': '限速站点2',
                                                   'items': site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'limit_speed2',
                                                   'label': '上行速度(KB)',
                                                   'placeholder': "例如100就是100K"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'active_time_range_site_config2',
                                                   'label': '生效时间段',
                                                   'placeholder': '如：00:00-08:00,默认全天'
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
                                           'md': 6
                                       },
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'chips': True,
                                                   'multiple': True,
                                                   'clearable': True,
                                                   'model': 'limit_sites3',
                                                   'label': '限速站点3',
                                                   'items': site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'limit_speed3',
                                                   'label': '上行速度(KB)',
                                                   'placeholder': "例如100就是100K"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'active_time_range_site_config3',
                                                   'label': '生效时间段',
                                                   'placeholder': '如：00:00-08:00,默认全天'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                       ],
                   }
               ], {
                   "enabled": False,
                   "onlyonce": False,
                   "notify": False,
                   "cron": "",
                   "downloaders": [],
                   "limit_sites1": [],
                   "limit_speed1": 0,
                   # "limit_sites_pause_threshold1": 0,
                   "active_time_range_site_config1": None,
                   "limit_sites2": [],
                   "limit_speed2": 0,
                   # "limit_sites_pause_threshold2": 0,
                   "active_time_range_site_config2": None,
                   "limit_sites3": [],
                   "limit_speed3": 0,
                   # "limit_sites_pause_threshold3": 0,
                   "active_time_range_site_config3": None,
               }

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": False,
            "notify": self._notify,
            "cron": self._cron,
            "downloaders": self._downloaders,
            "limit_sites1": self._limit_sites1,
            "limit_speed1": self._limit_speed1,
            # "limit_sites_pause_threshold1": self._limit_sites_pause_threshold1,
            "active_time_range_site_config1": self._active_time_range_site_config1,
            "limit_sites2": self._limit_sites2,
            "limit_speed2": self._limit_speed2,
            # "limit_sites_pause_threshold2": self._limit_sites_pause_threshold2,
            "active_time_range_site_config2": self._active_time_range_site_config2,
            "limit_sites3": self._limit_sites3,
            "limit_speed3": self._limit_speed3,
            # "limit_sites_pause_threshold3": self._limit_sites_pause_threshold3,
            "active_time_range_site_config3": self._active_time_range_site_config3,
        })

    def auto_seed(self):
        """
        开始限速
        """
        service_infos = self.service_infos
        if not service_infos:
            return
        logger.info("开始执行限速逻辑 ...")
        # 站点name:id {}
        all_site_name_id_map = {}
        for site in self.site_oper.list_order_by_pri():
            all_site_name_id_map[site.name] = site.id
        for site in self.__custom_sites():
            all_site_name_id_map[site.get("name")] = site.get("id")
        all_site_names = set(all_site_name_id_map.keys())
        for service in service_infos.values():
            downloader = service.name
            downloader_obj = service.instance
            dl_type = service.type
            if dl_type == "qbittorrent":
                if self._limit_sites1 or self._limit_sites2 or self._limit_sites3:
                    logger.info("开始设置限速1 ...")
                    is_in_time_range1 = self.__is_current_time_in_range_site_config(
                        self._active_time_range_site_config1)
                    is_in_time_range2 = self.__is_current_time_in_range_site_config(
                        self._active_time_range_site_config2)
                    is_in_time_range3 = self.__is_current_time_in_range_site_config(
                        self._active_time_range_site_config3)
                    all_torrents, _ = downloader_obj.get_torrents()
                    to_limit_torrent_hashs1 = []
                    cancel_limit_torrent_hashs1 = []
                    to_limit_torrent_hashs2 = []
                    cancel_limit_torrent_hashs2 = []
                    to_limit_torrent_hashs3 = []
                    cancel_limit_torrent_hashs3 = []
                    cancel_limit_torrent_hashs_other = []
                    for torrent in all_torrents:
                        # 当前种子 tags list
                        current_torrent_tag_list = [element.strip() for element in
                                                    torrent.tags.split(',')]
                        # qb 补充站点标签,交集第一个就是站点标签
                        intersection = all_site_names.intersection(current_torrent_tag_list)
                        site_name = None
                        if intersection:
                            site_name = list(intersection)[0]

                        if site_name:
                            site_id = all_site_name_id_map[site_name]
                            is_in_limit_sites1 = site_id in self._limit_sites1
                            is_in_limit_sites2 = site_id in self._limit_sites2
                            is_in_limit_sites3 = site_id in self._limit_sites3
                        else:
                            is_in_limit_sites1 = False
                            is_in_limit_sites2 = False
                            is_in_limit_sites3 = False
                            logger.error(f"{torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                        if is_in_limit_sites1:
                            if is_in_time_range1:
                                to_limit_torrent_hashs1.append(torrent.hash)
                            else:
                                cancel_limit_torrent_hashs1.append(torrent.hash)
                        elif is_in_limit_sites2:
                            if is_in_time_range2:
                                to_limit_torrent_hashs2.append(torrent.hash)
                            else:
                                cancel_limit_torrent_hashs2.append(torrent.hash)
                        elif is_in_limit_sites3:
                            if is_in_time_range3:
                                to_limit_torrent_hashs3.append(torrent.hash)
                            else:
                                cancel_limit_torrent_hashs3.append(torrent.hash)
                        else:
                            cancel_limit_torrent_hashs_other.append(torrent.hash)
                    if to_limit_torrent_hashs1:
                        downloader_obj.qbc.torrents_set_upload_limit(1024 * self._limit_speed1,
                                                                     to_limit_torrent_hashs1)
                        logger.info(
                            f"{downloader} 限速{self._limit_speed1}K种子个数: {len(to_limit_torrent_hashs1)}")
                    if to_limit_torrent_hashs2:
                        downloader_obj.qbc.torrents_set_upload_limit(1024 * self._limit_speed2,
                                                                     to_limit_torrent_hashs2)
                        logger.info(
                            f"{downloader} 限速{self._limit_speed2}K种子个数: {len(to_limit_torrent_hashs2)}")
                    if to_limit_torrent_hashs3:
                        downloader_obj.qbc.torrents_set_upload_limit(1024 * self._limit_speed3,
                                                                     to_limit_torrent_hashs3)
                        logger.info(
                            f"{downloader} 限速{self._limit_speed3}K种子个数: {len(to_limit_torrent_hashs3)}")
                    # 其他的都是不限速的,塞到一个list吧
                    cancel_limit_list_all = cancel_limit_torrent_hashs1 + cancel_limit_torrent_hashs2 + cancel_limit_torrent_hashs3 + cancel_limit_torrent_hashs_other
                    logger.info(f"{downloader} 取消限速种子个数{len(cancel_limit_list_all)}")
                    downloader_obj.qbc.torrents_set_upload_limit(0, cancel_limit_list_all)

                    # 判断当前是否在生效时间段内,如果在就执行限速,如果不在就取消限速
                    # if self.__is_current_time_in_range_site_config(self._active_time_range_site_config1):
                    #     for torrent in all_torrents:
                    #         # 当前种子 tags list
                    #         current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                    #         # qb 补充站点标签,交集第一个就是站点标签
                    #         intersection = all_site_names.intersection(current_torrent_tag_list)
                    #         site_name = None
                    #         if intersection:
                    #             site_name = list(intersection)[0]
                    #         if site_name:
                    #             is_in_limit_sites = all_site_name_id_map[site_name] in self._limit_sites1
                    #         else:
                    #             is_in_limit_sites = False
                    #             logger.error(f"{torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                    #         if is_in_limit_sites:
                    #             to_limit_torrent_hashs1.append(torrent.hash)
                    #     if to_limit_torrent_hashs1:
                    #         downloader_obj.qbc.torrents_set_upload_limit(102400, to_limit_torrent_hashs1)
                    #         downloader_obj.set_torrents_tag(to_limit_torrent_hashs1, ["F100K"])
                    #         logger.info(f"{downloader} 限速100K种子个数: {len(to_limit_torrent_hashs1)}")
                    # else:  #
                    #     for torrent in all_torrents:
                    #         # 当前种子 tags list
                    #         current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                    #         # qb 补充站点标签,交集第一个就是站点标签
                    #         intersection = all_site_names.intersection(current_torrent_tag_list)
                    #         site_name = None
                    #         if intersection:
                    #             site_name = list(intersection)[0]
                    #         if site_name in all_site_name_id_map:
                    #             is_in_limit_sites = all_site_name_id_map[site_name] in self._limit_sites
                    #         else:
                    #             is_in_limit_sites = None
                    #             logger.error(f"{site_name} not in {all_site_name_id_map}")
                    #         if is_in_limit_sites:
                    #             to_limit_torrent_hashs1.append(torrent.hash)
                    #     # to_limit_torrent_hashs1 取消限速,删除标签
                    #     if to_limit_torrent_hashs1:
                    #         downloader_obj.qbc.torrents_set_upload_limit(0, to_limit_torrent_hashs1)
                    #         downloader_obj.remove_torrents_tag(to_limit_torrent_hashs1, ["F100K", "P100K"])
                    #         self.to_pausedUP_hashs.clear()
                    #         logger.info(f"在非限速时间区间,{downloader} 解除限速100K种子个数{len(to_limit_torrent_hashs1)}")
            # elif dl_type == "transmission":
            #     pass
        # 保存缓存
        # self.__update_config()
        logger.info("限速执行完成")

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
