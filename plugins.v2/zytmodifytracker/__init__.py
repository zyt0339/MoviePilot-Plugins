from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from qbittorrentapi import TrackerStatus
from app.core.config import settings
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo


class ZYTModifyTracker(_PluginBase):
    # 插件名称
    plugin_name = "QB&TR修改tracker"
    # 插件描述
    plugin_desc = "QB&TR修改tracker"
    # 插件图标
    plugin_icon = "upload.png"
    # 插件版本
    plugin_version = "1.0.3"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytmodifytracker_"
    # 加载顺序
    plugin_order = 40
    # 可使用的用户级别
    auth_level = 2

    downloader_helper = None

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _onlyonce = False
    _notify = False
    _cron = None
    # _downloaders = []
    _downloaders1 = []
    _replace_content = None
    _remove_content = None
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
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
            self._replace_content = config.get("replace_content")
            self._remove_content = config.get("remove_content")

            # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._onlyonce:
                logger.info(f"QB&TR修改tracker服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="QB&TR修改tracker",
                )
                # 关闭一次性开关
                self._onlyonce = False
            self.__update_config()

            # 启动任务
            # if self._scheduler.get_jobs():
            self._scheduler.self.logger_info_jobs()
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

    def run(self):
        logger.debug(f"QB&TR修改tracker run...")
        msg = ""
        try:
            self.modify()
            success = True
        except Exception as e:
            success = False
            logger.error(f"QB&TR修改tracker出错: {e}")
            msg = f"{e}"
        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB&TR修改tracker成功】")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB&TR修改tracker出错】", text=msg
                )

    def modify(self):
        """
        开始替换、移除
        """
        if not self._downloaders1:
            logger.warning("未设置下载器,取消执行")
            return
        if self._replace_content or self._remove_content:
            pass
        else:
            logger.warning("未配置替换、删除规则,取消执行")
            return
        logger.info(f"----------开始执行替换、删除tracker逻辑----------")

        downloader_service_infos = self.get_downloader_service_infos(self._downloaders1)
        if not downloader_service_infos:
            return
        # 将配置文件读取到map
        to_replace_item_map = {}
        for line in self._replace_content:
            line = line.strip()
            if not line or "|" not in line:
                continue
            old, new = line.split("|", 1)
            to_replace_item_map[old] = new
        to_remove_item_list = []
        for line in self._remove_content:
            line = line.strip()
            if not line:
                continue
            to_remove_item_list.append(line)

        for downloader_service_info in downloader_service_infos.values():
            self.modify_per_downloader(downloader_service_info, to_replace_item_map, to_remove_item_list)
        logger.info(f"执行完成")

    def modify_per_downloader(self, downloader_service_info, to_replace_item_map, to_remove_item_list):
        downloader = downloader_service_info.name
        downloader_obj = downloader_service_info.instance
        dl_type = downloader_service_info.type

        if dl_type == "qbittorrent":
            self.logger_info(f"{downloader} 开始替换、删除tracker")
            all_torrents, _ = downloader_obj.get_torrents()
            for torrent in all_torrents:
                trackers = torrent.trackers
                tracker_url_old_list = [tracker.url for tracker in trackers if
                                        tracker.status != TrackerStatus.DISABLED]
                for tracker_url_old in tracker_url_old_list:
                    for old, new in to_replace_item_map.items():
                        if old in tracker_url_old:
                            tracker_new_url = tracker_url_old.replace(old, new)
                            if tracker_new_url not in tracker_url_old_list:  # 待更新的url不会与已有的冲突
                                torrent.edit_tracker(
                                    orig_url=tracker_url_old,
                                    new_url=tracker_new_url
                                )
                                self.logger_info(f"  替换: {torrent.name} {tracker_url_old} → {tracker_new_url}")
                    for to_remove_item in to_remove_item_list:
                        # tracker 个数大于1个，且配置了删除
                        if len(tracker_url_old_list) > 1 and to_remove_item in tracker_url_old:
                            torrent.remove_trackers(tracker_url_old)
                            self.logger_info(f"  删除: {torrent.name} {tracker_url_old}")
                        else:  # 待更新的url已经存在，判断是否要删除
                            self.logger_info(f"  删除失败：只剩一个tracker，不敢删 {torrent.name} {tracker_url_old}")
        elif dl_type == "transmission":
            self.logger_info(f"{downloader} 开始替换、删除tracker")
            _trarg = ["id", "name", "hashString", "trackerList"]
            tr_client = downloader_obj.trc
            all_torrents = tr_client.get_torrents(arguments=_trarg)
            for torrent in all_torrents:
                tracker_url_old_list = torrent.tracker_list
                tracker_url_new_list = set()
                for tracker_url_old in tracker_url_old_list:
                    for old, new in to_replace_item_map.items():
                        if old in tracker_url_old:
                            tracker_new_url = tracker_url_old.replace(old, new)
                            if tracker_new_url not in tracker_url_old_list:  # 待更新的url不会与已有的冲突
                                tracker_url_new_list.add(tracker_new_url)
                                self.logger_info(f"  替换: {torrent.name} {tracker_url_old} → {tracker_new_url}")
                            else:
                                pass
                        else:
                            tracker_url_new_list.add(tracker_url_old)
                    for to_remove_item in to_remove_item_list:
                        # tracker 个数大于1个，且配置了删除
                        if len(tracker_url_old_list) > 1 and to_remove_item in tracker_url_old:
                            self.logger_info(f"  删除: {torrent.name} {tracker_url_old}")
                        else:  # 待更新的url已经存在，判断是否要删除
                            tracker_url_new_list.add(tracker_url_old)
                            self.logger_info(f"  删除失败：只剩一个tracker，不敢删 {torrent.name} {tracker_url_old}")
                # tr 整体更换tracker
                try:
                    downloader_obj.change_torrent(ids=torrent.hashString, tracker_list=list(tracker_url_new_list))
                    return True
                except Exception as err:
                    logger.error(f"  {torrent.name} 修改tracker出错：{str(err)}")
                return False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_page(self) -> List[dict]:
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
            logger.info(f"QB&TR修改tracker服务重新启动，执行周期 {self._cron}")
            return [{
                "id": "ZYTModifyTracker",
                "name": "QB&TR修改tracker",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run,
                "kwargs": {}
            }]
        logger.info("QB&TR修改tracker服务未开启")
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
                                "props": {"cols": 12, "md": 3},
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VCronField",
                                        "props": {"model": "cron", "label": "执行周期"}
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
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
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次"
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
                                "props": {"cols": 12, "md": 12},
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
                                                      for config in
                                                      self.downloader_helper.get_configs().values()]
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
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "replace_content",
                                            "rows": "2",
                                            "label": "替换配置",
                                            "placeholder": "replace方式替换，例如：abc.com|abc.net，用|分割，一行一条",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "remove_content",
                                            "rows": "2",
                                            "label": "删除配置",
                                            "placeholder": "例如：abc.com，一行一条；种子只有一条tracker并且符合本配置不会删，因为删了就噶屁了",
                                        },
                                    }
                                ],
                            }
                        ],
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
                                            'text': '种子既有abc.com也有abc.net，执行abc.com|abc.net替换无效，报错 New tracker URL already exists 正常现象'
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
            "replace_content": "abc.com|abc.net",
            "remove_content": "remove.com"
        }

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": False,
            "notify": self._notify,
            "cron": self._cron,
            # "downloaders": self._downloaders,
            "downloaders1": self._downloaders1,
            "replace_content": self._replace_content,
            "remove_content": self._remove_content
        })

    def logger_info(self, msg):
        logger.info(msg)

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
