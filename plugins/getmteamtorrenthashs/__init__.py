import os
import re
from datetime import datetime, timedelta
from threading import Event
from typing import Any, List, Dict, Tuple, Optional

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.helper.torrent import TorrentHelper
from app.log import logger
from app.modules.qbittorrent import Qbittorrent
from app.modules.transmission import Transmission
from app.plugins import _PluginBase
from app.schemas import NotificationType


class getmteamtorrenthashs(_PluginBase):
    # 插件名称
    plugin_name = "打印馒头torrent"
    # 插件描述
    plugin_desc = "打印馒头torrent_hashs,desc"
    # 插件图标
    plugin_icon = "torrent.png"
    # 插件版本
    plugin_version = "1.0.2"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339"
    # 插件配置项ID前缀
    plugin_config_prefix = "getmteamtorrenthashs_"
    # 加载顺序
    plugin_order = 100
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _scheduler = None
    qb = None
    tr = None
    sites = None
    siteoper = None
    torrent = None
    # 开关
    _enabled = False
    _cron = None
    _skipverify = False
    _onlyonce = False
    _token = None
    _downloaders = []
    _sites = []
    _notify = False
    _nolabels = None
    _nopaths = None
    _labelsafterseed = None
    _categoryafterseed = None
    _addhosttotag = False
    _size = None
    _clearcache = False
    # 退出事件
    _event = Event()
    # 种子链接xpaths
    _torrent_xpaths = [
        "//form[contains(@action, 'download.php?id=')]/@action",
        "//a[contains(@href, 'download.php?hash=')]/@href",
        "//a[contains(@href, 'download.php?id=')]/@href",
        "//a[@class='index'][contains(@href, '/dl/')]/@href",
    ]
    success = 0
    exist = 0
    fail = 0
    cached = 0

    def init_plugin(self, config: dict = None):
        self.sites = SitesHelper()
        self.siteoper = SiteOper()
        self.torrent = TorrentHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._skipverify = config.get("skipverify")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._token = config.get("token")
            self._downloaders = config.get("downloaders")
            self._sites = config.get("sites") or []
            self._notify = config.get("notify")
            self._nolabels = config.get("nolabels")
            self._nopaths = config.get("nopaths")
            self._labelsafterseed = config.get("labelsafterseed") if config.get(
                "labelsafterseed") else "已整理,辅种"
            self._categoryafterseed = config.get("categoryafterseed")
            self._addhosttotag = config.get("addhosttotag")
            self._size = float(config.get("size")) if config.get("size") else 0
            self._clearcache = config.get("clearcache")
            self._permanent_error_caches = [] if self._clearcache else config.get(
                "permanent_error_caches") or []
            self._error_caches = [] if self._clearcache else config.get("error_caches") or []
            self._success_caches = [] if self._clearcache else config.get("success_caches") or []

            # 过滤掉已删除的站点
            all_sites = [site.id for site in self.siteoper.list_order_by_pri()] + [site.get("id")
                                                                                   for site in
                                                                                   self.__custom_sites()]
            self._sites = [site_id for site_id in all_sites if site_id in self._sites]
            self.__update_config()

        # 停止现有任务
        # self.stop_service()

        # 启动定时任务 & 立即运行一次
        if self.get_state() or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            self.qb = Qbittorrent()
            self.tr = Transmission()

            if self._onlyonce:
                logger.info(f"插馒头服务启动，立即运行一次")
                self._scheduler.add_job(self.auto_seed, 'date',
                                        run_date=datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3)
                                        )
                logger.info(f"插馒头服务运行完了")
                # 关闭一次性开关
                # self._onlyonce = False
                # if self._scheduler.get_jobs():
                #     # 追加种子校验服务
                #     self._scheduler.add_job(self.check_recheck, 'interval', minutes=3)
                #     # 启动服务
                #     self._scheduler.print_jobs()
                #     self._scheduler.start()

            # if self._clearcache:
            #     # 关闭清除缓存开关
            #     self._clearcache = False
            #
            # if self._clearcache or self._onlyonce:
            #     # 保存配置
            #     self.__update_config()

    def get_state(self) -> bool:
        return True if self._enabled and self._cron and self._token and self._downloaders else False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

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
        if self.get_state():
            return [{
                "id": "getmteamtorrenthashs",
                "name": "查询馒头种子 hash",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.auto_seed,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 站点的可选项（内置站点 + 自定义站点）
        customSites = self.__custom_sites()

        # 站点的可选项
        site_options = ([{"title": site.name, "value": site.id}
                         for site in self.siteoper.list_order_by_pri()]
                        + [{"title": site.get("name"), "value": site.get("id")}
                           for site in customSites])
        return [
                   {
                       'component': 'VForm',
                       'content': [
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'enabled',
                                                   'label': '启用插件',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'notify',
                                                   'label': '发送通知',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'onlyonce',
                                                   'label': '立即运行一次',
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
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'token',
                                                   'label': 'IYUU Token',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 6
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'cron',
                                                   'label': '执行周期',
                                                   'placeholder': '0 0 0 ? *'
                                               }
                                           }
                                       ]
                                   },
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
                                                   'model': 'downloaders',
                                                   'label': '辅种下载器',
                                                   'items': [
                                                       {'title': 'Qbittorrent',
                                                        'value': 'qbittorrent'},
                                                       {'title': 'Transmission',
                                                        'value': 'transmission'}
                                                   ]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 6
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'size',
                                                   'label': '辅种体积大于(GB)',
                                                   'placeholder': '只有大于该值的才辅种'
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
                                           'cols': 12
                                       },
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'chips': True,
                                                   'multiple': True,
                                                   'model': 'sites',
                                                   'label': '辅种站点',
                                                   'items': site_options
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
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'nolabels',
                                                   'label': '不辅种标签',
                                                   'placeholder': '使用,分隔多个标签'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'labelsafterseed',
                                                   'label': '辅种后增加标签',
                                                   'placeholder': '使用,分隔多个标签,不填写则默认为(已整理,辅种)'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'categoryafterseed',
                                                   'label': '辅种后增加分类',
                                                   'placeholder': '设置辅种的种子分类'
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextarea',
                                               'props': {
                                                   'model': 'nopaths',
                                                   'label': '不辅种数据文件目录',
                                                   'rows': 3,
                                                   'placeholder': '每一行一个目录'
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
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'addhosttotag',
                                                   'label': '将站点名添加到标签中',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'skipverify',
                                                   'label': '跳过校验(仅QB有效)',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'clearcache',
                                                   'label': '清除缓存后运行',
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
                   "skipverify": False,
                   "onlyonce": False,
                   "notify": False,
                   "clearcache": False,
                   "addhosttotag": False,
                   "cron": "",
                   "token": "",
                   "downloaders": [],
                   "sites": [],
                   "nopaths": "",
                   "nolabels": "",
                   "labelsafterseed": "",
                   "categoryafterseed": "",
                   "size": ""
               }

    def get_page(self) -> List[dict]:
        pass

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "skipverify": self._skipverify,
            "onlyonce": self._onlyonce,
            "clearcache": self._clearcache,
            "cron": self._cron,
            "token": self._token,
            "downloaders": self._downloaders,
            "sites": self._sites,
            "notify": self._notify,
            "nolabels": self._nolabels,
            "nopaths": self._nopaths,
            "labelsafterseed": self._labelsafterseed,
            "categoryafterseed": self._categoryafterseed,
            "addhosttotag": self._addhosttotag,
            "size": self._size,
            "success_caches": self._success_caches,
            "error_caches": self._error_caches,
            "permanent_error_caches": self._permanent_error_caches
        })

    def __get_downloader(self, dtype: str):
        """
        根据类型返回下载器实例
        """
        if dtype == "qbittorrent":
            return self.qb
        elif dtype == "transmission":
            return self.tr
        else:
            return None

    def auto_seed(self):
        """
        开始查询
        """
        logger.info("进入 auto_seed,开始查询馒头种子 hash任务 ...")

        # 扫描下载器辅种
        for downloader in self._downloaders:
            logger.info(f"开始扫描下载器 {downloader} ...")
            downloader_obj = self.__get_downloader(downloader)
            # 获取下载器中已完成的种子
            torrents = downloader_obj.get_completed_torrents()
            if torrents:
                logger.info(f"下载器 {downloader} 总种子数：{len(torrents)}")
            else:
                logger.info(f"下载器 {downloader} 没有已完成种子")
                continue
            # hash_strs = []
            hash_strs2 = []
            for torrent in torrents:
                # if self._event.is_set():
                #     logger.info(f"服务停止")
                #     return
                # 获取种子 trackers
                # 获取种子 size,取 10M 到 100M之间的
                if self.__get_torrenttrackers_contains_mteam(torrent, downloader) and self.__get_torrent_size_in_10M_and_100M(torrent, downloader):
                    # 获取种子hash
                    logger.info(f" {downloader} {torrent.name}")
                    hash_str = self.__get_hash(torrent, downloader)
                    hash_strs2.append(hash_str)
                # 获取种子标签
                # torrent_labels = self.__get_label(torrent, downloader)
            if hash_strs2:
                logger.info(f" {downloader} 10M-100M 之间的馒头种子个数：{len(hash_strs2)}")
                logger.info(f" {downloader} 10M-100M 之间的馒头种子 hashs：{hash_strs2}")
            else:
                logger.info(f" {downloader} 没有获取到馒头种子")
        # 发送消息
        if self._notify:
            if self.success or self.fail:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【查询馒头 hash 完成】",
                    text=f"去日志查看"
                )
        logger.info("辅种任务执行完成")






    @staticmethod
    def __get_hash(torrent: Any, dl_type: str):
        """
        获取种子hash
        """
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def __get_label(torrent: Any, dl_type: str):
        """
        获取种子标签
        """
        try:
            return [str(tag).strip() for tag in torrent.get("tags").split(',')] \
                if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            print(str(e))
            return []
    @staticmethod
    def __get_torrent_size_in_10M_and_100M(torrent: Any, dl_type: str) -> bool:
        """
        获取种子size 是否 10-100m 之间
        """
        try:
            m10m = 1024*1024*1024*10
            m100m = m10m * 10
            # size = torrent.size if dl_type == "qbittorrent" else torrent.total_size
            size = torrent.get("total_size") if dl_type == "qbittorrent" else torrent.total_size
            if m10m <= size <= m100m:
                return True
        except Exception as e:
            print(str(e))
        return False
    @staticmethod
    def __get_torrenttrackers_contains_mteam(torrent: Any, dl_type: str) -> bool:
        """
        获取种子 trackers 是否包含 m-team
        """
        try:
            if dl_type == "qbittorrent":
                trackers = torrent.trackers
                for tracker in trackers:
                    if 'm-team' in tracker.msg:
                        return True
            else:
                tracker_list = torrent.tracker_list
                for tracker in tracker_list:
                    if 'm-team' in tracker:
                        return True

        except Exception as e:
            print(str(e))
        return False


    @staticmethod
    def __get_torrent_size(torrent: Any, dl_type: str):
        """
        获取种子大小 int bytes
        """
        try:
            return torrent.get("total_size") if dl_type == "qbittorrent" else torrent.total_size
        except Exception as e:
            print(str(e))
            return ""



    # def stop_service(self):
    #     """
    #     退出插件
    #     """
    #     try:
    #         if self._scheduler:
    #             self._scheduler.remove_all_jobs()
    #             if self._scheduler.running:
    #                 self._event.set()
    #                 self._scheduler.shutdown()
    #                 self._event.clear()
    #             self._scheduler = None
    #     except Exception as e:
    #         print(str(e))

    def __custom_sites(self) -> List[Any]:
        custom_sites = []
        custom_sites_config = self.get_config("CustomSites")
        if custom_sites_config and custom_sites_config.get("enabled"):
            custom_sites = custom_sites_config.get("sites")
        return custom_sites

