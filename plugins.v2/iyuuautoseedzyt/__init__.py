import os
import re
import time
from datetime import datetime, timedelta
from threading import Event
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from lxml import etree
from ruamel.yaml import CommentedMap

from app.core.config import settings
from app.core.event import eventmanager
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.helper.sites import SitesHelper
from app.helper.torrent import TorrentHelper
from app.log import logger
from app.plugins import _PluginBase
from app.plugins.iyuuautoseedzyt.iyuu_helper import IyuuHelper
from app.schemas import NotificationType, ServiceInfo
from app.schemas.types import EventType
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


class IYUUAutoSeedzyt(_PluginBase):
    # 插件名称
    plugin_name = "IYUU自动辅种zyt"
    # 插件描述
    plugin_desc = "基于IYUU官方Api实现自动辅种。"
    # 插件图标
    plugin_icon = "IYUU.png"
    # 插件版本
    plugin_version = "2.14.4"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "iyuuautoseedzyt_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _scheduler = None
    iyuu_helper = None
    downloader_helper = None
    sites_helper = None
    site_oper = None
    torrent_helper = None
    # 开关
    _enabled = False
    _cron = None
    _skipverify = False
    _onlyonce = False
    _token = None
    _downloaders = []
    _sites = []
    _limit_sites = []
    _limit_sites_pause_threshold = 12 * 60  # 12小时
    _active_time_range_site_config = None
    _notify = False
    _nolabels = None
    _noautostart = None
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
    # 待校全种子hash清单
    _recheck_torrents = {}
    _is_recheck_running = False
    # 辅种缓存，出错的种子不再重复辅种，可清除
    _error_caches = []
    # 辅种缓存，辅种成功的种子，可清除
    _success_caches = []
    # 辅种缓存，出错的种子不再重复辅种，且无法清除。种子被删除404等情况
    _permanent_error_caches = []
    # 辅种计数
    total = 0
    realtotal = 0
    success = 0
    exist = 0
    fail = 0
    cached = 0
    to_pausedUP_hashs = {} # 位于限速站点中因活动而暂停的种子hash,value=和最后活动时间
    def init_plugin(self, config: dict = None):
        self.sites_helper = SitesHelper()
        self.site_oper = SiteOper()
        self.torrent_helper = TorrentHelper()
        self.downloader_helper = DownloaderHelper()
        # 读取配置
        if config:
            self._enabled = config.get("enabled")
            self._skipverify = config.get("skipverify")
            self._onlyonce = config.get("onlyonce")
            self._cron = config.get("cron")
            self._token = config.get("token")
            self._downloaders = config.get("downloaders")
            self._sites = config.get("sites") or []
            self._limit_sites = config.get("limit_sites") or []
            self._limit_sites_pause_threshold = config.get("limit_sites_pause_threshold") or 12 * 60  # 12小时
            self._active_time_range_site_config = config.get("active_time_range_site_config")
            self._notify = config.get("notify")
            self._nolabels = config.get("nolabels")
            self._noautostart = config.get("noautostart")
            self._nopaths = config.get("nopaths")
            self._labelsafterseed = config.get("labelsafterseed") if config.get("labelsafterseed") else "已整理,辅种"
            self._categoryafterseed = config.get("categoryafterseed")
            self._addhosttotag = config.get("addhosttotag")
            self._size = float(config.get("size")) if config.get("size") else 0
            self._clearcache = config.get("clearcache")
            self._permanent_error_caches = [] if self._clearcache else config.get("permanent_error_caches") or []
            self._error_caches = [] if self._clearcache else config.get("error_caches") or []
            self._success_caches = [] if self._clearcache else config.get("success_caches") or []

            # 过滤掉已删除的站点
            all_sites = [site.id for site in self.site_oper.list_order_by_pri()] + [site.get("id") for site in
                                                                                    self.__custom_sites()]
            self._sites = [site_id for site_id in all_sites if site_id in self._sites]
            self._limit_sites = [site_id for site_id in all_sites if site_id in self._limit_sites]
            self.__update_config()

        # 停止现有任务
        self.stop_service()

        # 启动定时任务 & 立即运行一次
        if self.get_state() or self._onlyonce:
            self.iyuu_helper = IyuuHelper(token=self._token)
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._onlyonce:
                logger.info(f"辅种服务启动，立即运行一次")
                self._scheduler.add_job(self.auto_seed, 'date',
                                        run_date=datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3)
                                        )
                # 关闭一次性开关
                self._onlyonce = False

            if self._clearcache:
                # 关闭清除缓存开关
                self._clearcache = False
            # 保存配置
            self.__update_config()

            # 追加种子校验服务
            self._scheduler.add_job(self.check_recheck, 'interval', minutes=3)
            # 启动服务
            self._scheduler.print_jobs()
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
            logger.info(f"IYUU自动辅种服务重新启动，执行周期 {self._cron}")
            return [{
                "id": "IYUUAutoSeedzyt",
                "name": "IYUU自动辅种服务",
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
                         for site in self.site_oper.list_order_by_pri()]
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
                                               'component': 'VCronField',
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
                                                   'clearable': True,
                                                   'model': 'downloaders',
                                                   'label': '下载器',
                                                   'items': [{"title": config.name, "value": config.name}
                                                             for config in self.downloader_helper.get_configs().values()]
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
                                                   'clearable': True,
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
                                           'md': 8
                                       },
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'chips': True,
                                                   'multiple': True,
                                                   'clearable': True,
                                                   'model': 'limit_sites',
                                                   'label': '限速100K站点',
                                                   'items': site_options
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'limit_sites_pause_threshold',
                                                   'label': '限速暂停时间(分钟)',
                                                   'placeholder': "限速后还活动就暂停"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 2
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'active_time_range_site_config',
                                                   'label': '限速时间段',
                                                   'placeholder': '限速后还活动就暂停,如：00:00-08:00,默认全天'
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
                                           'md': 3
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
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'noautostart',
                                                   'label': '不自动开始标签',
                                                   'placeholder': '使用,分隔多个标签'
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
                                           'md': 3
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
                                                   'rows': 2,
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
                   "limit_sites": [],
                   "limit_sites_pause_threshold": 12 * 60,
                   "nopaths": "",
                   "nolabels": "",
                   "noautostart": "",
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
            "limit_sites": self._limit_sites,
            "limit_sites_pause_threshold": self._limit_sites_pause_threshold,
            "active_time_range_site_config": self._active_time_range_site_config,
            "notify": self._notify,
            "nolabels": self._nolabels,
            "noautostart": self._noautostart,
            "nopaths": self._nopaths,
            "labelsafterseed": self._labelsafterseed,
            "categoryafterseed": self._categoryafterseed,
            "addhosttotag": self._addhosttotag,
            "size": self._size,
            "success_caches": self._success_caches,
            "error_caches": self._error_caches,
            "permanent_error_caches": self._permanent_error_caches
        })

    def auto_seed(self):
        """
        开始辅种
        """
        if not self.iyuu_helper or not self.service_infos:
            return
        logger.info("开始辅种任务 ...")

        # 计数器初始化
        self.total = 0
        self.realtotal = 0
        self.success = 0
        self.exist = 0
        self.fail = 0
        self.cached = 0
        # 扫描下载器辅种
        for service in self.service_infos.values():
            downloader = service.name
            downloader_obj = service.instance
            logger.info(f"开始扫描下载器 {downloader} ...")
            # 获取下载器中已完成的种子
            torrents = downloader_obj.get_completed_torrents()
            if torrents:
                logger.info(f"下载器 {downloader} 已完成种子数：{len(torrents)}")
            else:
                logger.info(f"下载器 {downloader} 没有已完成种子")
                continue
            hash_strs = []
            all_hashs_in_cur_downloader = set()
            for torrent in torrents:
                if self._event.is_set():
                    logger.info(f"辅种服务停止")
                    return
                # 获取种子hash
                hash_str = self.__get_hash(torrent=torrent, dl_type=service.type)
                all_hashs_in_cur_downloader.add(hash_str)
                # 获取种子标签
                torrent_labels = self.__get_label(torrent=torrent, dl_type=service.type)
                log_torrent_tag = f"{torrent.name} {torrent_labels}"
                if hash_str in self._error_caches or hash_str in self._permanent_error_caches:
                    logger.info(f"{log_torrent_tag} 辅种失败且已缓存，跳过 ...")
                    continue
                save_path = self.__get_save_path(torrent=torrent, dl_type=service.type)

                if self._nopaths and save_path:
                    # 过滤不需要转移的路径
                    nopath_skip = False
                    for nopath in self._nopaths.split('\n'):
                        if os.path.normpath(save_path).startswith(os.path.normpath(nopath)):
                            logger.info(f"{log_torrent_tag} 保存路径 {save_path} 不需要辅种，跳过 ...")
                            nopath_skip = True
                            break
                    if nopath_skip:
                        continue

                if torrent_labels and self._nolabels:
                    is_skip = False
                    for label in self._nolabels.split(','):
                        if label in torrent_labels:
                            logger.debug(f"{log_torrent_tag} 含有不辅种标签 {label}，跳过 ...")
                            is_skip = True
                            break
                    if is_skip:
                        continue

                # 体积排除辅种
                torrent_size = self.__get_torrent_size(torrent=torrent, dl_type=service.type) / 1024 / 1024 / 1024
                if self._size and torrent_size < self._size:
                    logger.info(f"{log_torrent_tag} 大小:{torrent_size:.2f}GB，小于设定 {self._size}GB，跳过 ...")
                    continue
                # 拆包种子排除辅种
                if service.type == "qbittorrent":
                    if torrent.availability != -1 and torrent.availability < 1:
                        logger.info(f"{log_torrent_tag} 下载不完整，跳过 ...")
                        continue

                hash_strs.append({
                    "hash": hash_str,
                    "save_path": save_path
                })
            if hash_strs:
                logger.info(f"总共需要辅种的种子数：{len(hash_strs)}")
                # 分组处理，减少IYUU Api请求次数
                chunk_size = 200
                for i in range(0, len(hash_strs), chunk_size):
                    # 切片操作
                    chunk = hash_strs[i:i + chunk_size]
                    # 处理分组
                    self.__seed_torrents(hash_strs=chunk,
                                         service=service, all_hashs_in_cur_downloader=all_hashs_in_cur_downloader)
                # 触发校验检查
                logger.info(f"下载器 {downloader} 辅种全部完成。")
                self.check_recheck()
            else:
                logger.info(f"没有需要辅种的种子")
        # 限速需要 开始
        all_site_name_id_map = {}
        for site in self.site_oper.list_order_by_pri():
            all_site_name_id_map[site.name] = site.id
        for site in self.__custom_sites():
            all_site_name_id_map[site.get("name")] = site.get("id")
        all_site_names = set(all_site_name_id_map.keys())
        # 限速需要 结束
        #zyt开始所有辅种后暂停的种子
        logger.info(f"准备自动开始 {self._downloaders} 中暂停的种子 ...")
        noautostart_set = set(self._noautostart.split(',')) if self._noautostart else set()
        noautostart_set_and_P100K = noautostart_set.copy()
        noautostart_set_and_P100K.add("P100K")
        noautostart_set_and_P100K.add("P")
        for service in self.service_infos.values():
            downloader = service.name
            downloader_obj = service.instance
            dl_type = service.type
            # zyt一起开始: 思路先get_torrents 获取所有的,然后 for 取出 非 fail 的,然后一起 start
            if dl_type == "qbittorrent":
                paused_torrents, _ = downloader_obj.get_torrents(status="paused")
                # errored_torrents, _ = downloader_obj.get_torrents(status=["errored"])
                pausedUP_torrent_hashs = []
                for torrent in paused_torrents:
                    # 当前种子 tags list
                    current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                    if torrent.state in ['pausedUP', 'stoppedUP'] and not noautostart_set_and_P100K.intersection(current_torrent_tag_list):
                        pausedUP_torrent_hashs.append(torrent.hash)
                        logger.info(f"{downloader} 自动开始 {torrent.name} {current_torrent_tag_list}")
                for torrent in paused_torrents:
                    # 当前种子 tags list
                    current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                    if torrent.state not in ['pausedUP', 'stoppedUP']:
                        logger.info(f"{downloader} 不自动开始 {torrent.name}, state={torrent.state} {current_torrent_tag_list}")
                    else:
                        intersection = noautostart_set_and_P100K.intersection(current_torrent_tag_list)
                        if intersection:
                            logger.info(f"{downloader} 不自动开始 {torrent.name}, 含有不开始标签 {intersection} {current_torrent_tag_list}")
                if len(pausedUP_torrent_hashs) > 0:
                    downloader_obj.start_torrents(pausedUP_torrent_hashs)
                # 设置限速100K站点
                if self._limit_sites:
                    all_torrents, _ = downloader_obj.get_torrents()
                    # 限速100K站点内的种子
                    to_limit_torrent_hashs = []
                    to_cancel_limit_torrent_hashs = []
                    # 判断当前是否在生效时间段内,如果在就执行限速,如果不在就取消限速
                    if self.__is_current_time_in_range_site_config():
                        # 限速100K中,且活动的种子
                        to_pausedUP_hashs_cur = []
                        # 已经暂停,时间超过 12 小时的种子
                        to_cancel_pausedUP_hashs_cur = []  # 暂停超过 12 小时又可以启动的种子
                        current_time = time.time()  # 当前时间戳
                        _limit_sites_pause_threshold_s = int(self._limit_sites_pause_threshold) * 60
                        for torrent in all_torrents:
                            # 当前种子 tags list
                            current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                            # qb 补充站点标签,交集第一个就是站点标签
                            intersection = all_site_names.intersection(current_torrent_tag_list)
                            site_name = None
                            if intersection:
                                site_name = list(intersection)[0]
                            if site_name:
                                is_in_limit_sites = all_site_name_id_map[site_name] in self._limit_sites
                            else:
                                is_in_limit_sites = False
                                logger.error(f"{torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                            if is_in_limit_sites:
                                to_limit_torrent_hashs.append(torrent.hash)
                            else:
                                if torrent.uploadLimit != 0: # 去了限速标签,仍被被限速中的
                                    to_cancel_limit_torrent_hashs.append(torrent.hash)
                            # 限速100K仍然有上传就暂停:
                            if _limit_sites_pause_threshold_s > 0:
                                state = torrent.state  # str
                                if is_in_limit_sites:
                                    if "uploading" == state:
                                        to_pausedUP_hashs_cur.append(torrent.hash)
                                    elif state in ["pausedUP", "stoppedUP"] and "P100K" in current_torrent_tag_list and not noautostart_set.intersection(current_torrent_tag_list):
                                        pausedUPTime = self.to_pausedUP_hashs.get(torrent.hash, 0)
                                        if (current_time - pausedUPTime) > _limit_sites_pause_threshold_s:
                                            to_cancel_pausedUP_hashs_cur.append(torrent.hash)
                        if to_limit_torrent_hashs:
                            downloader_obj.qbc.torrents_set_upload_limit(102400, to_limit_torrent_hashs)
                            downloader_obj.set_torrents_tag(to_limit_torrent_hashs, ["F100K"])
                            logger.info(f"{downloader} 限速100K种子个数: {len(to_limit_torrent_hashs)}")
                        if to_cancel_limit_torrent_hashs:
                            downloader_obj.qbc.torrents_set_upload_limit(0, to_cancel_limit_torrent_hashs)
                            logger.info(f"{downloader} 从限速站点移除后,解除限速100K种子个数: {len(to_cancel_limit_torrent_hashs)}")
                        # 限速100K仍然有上传就暂停:
                        if to_pausedUP_hashs_cur:
                            downloader_obj.stop_torrents(to_pausedUP_hashs_cur)
                            downloader_obj.set_torrents_tag(to_pausedUP_hashs_cur, ["P100K"])
                            logger.info(f"{downloader} 增加暂停100K种子个数: {len(to_pausedUP_hashs_cur)}")
                            for t_hash in to_pausedUP_hashs_cur:
                                self.to_pausedUP_hashs[t_hash] = current_time
                        if to_cancel_pausedUP_hashs_cur:
                            downloader_obj.start_torrents(to_cancel_pausedUP_hashs_cur)
                            downloader_obj.remove_torrents_tag(to_cancel_pausedUP_hashs_cur, ["P100K"])
                            logger.info(f"{downloader} 重新开始P100K种子个数: {len(to_cancel_pausedUP_hashs_cur)}")
                            for t_hash in to_cancel_pausedUP_hashs_cur:
                                if t_hash in self.to_pausedUP_hashs:
                                    del self.to_pausedUP_hashs[t_hash]
                    else:
                        for torrent in all_torrents:
                            # 当前种子 tags list
                            current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                            # qb 补充站点标签,交集第一个就是站点标签
                            intersection = all_site_names.intersection(current_torrent_tag_list)
                            site_name = None
                            if intersection:
                                site_name = list(intersection)[0]
                            if site_name in all_site_name_id_map:
                                is_in_limit_sites = all_site_name_id_map[site_name] in self._limit_sites
                            else:
                                is_in_limit_sites = None
                                logger.error(f"{site_name} not in {all_site_name_id_map}")
                            if is_in_limit_sites:
                                to_limit_torrent_hashs.append(torrent.hash)
                        # to_limit_torrent_hashs 取消限速,删除标签
                        if to_limit_torrent_hashs:
                            downloader_obj.qbc.torrents_set_upload_limit(0, to_limit_torrent_hashs)
                            downloader_obj.remove_torrents_tag(to_limit_torrent_hashs, ["F100K", "P100K"])
                            self.to_pausedUP_hashs.clear()
                            logger.info(f"在非限速时间区间,{downloader} 解除限速100K种子个数{len(to_limit_torrent_hashs)}")
            elif dl_type == "transmission":
                # logger.info(f"debug service={type(service)},downloader={type(downloader)},downloader_obj={type(downloader_obj)},")
                # downloader_obj=<class 'app.modules.transmission.transmission.Transmission'>
                if "fileStats" not in downloader_obj._trarg:
                    downloader_obj._trarg.append("fileStats")
                if "desiredAvailable" not in downloader_obj._trarg:
                    downloader_obj._trarg.append("desiredAvailable")
                # 返回结果:种子列表, 是否有错误
                paused_torrents, _ = downloader_obj.get_torrents(status="stopped")
                # 继续过滤，只选 torrent.available == 100.0
                pausedUP_torrent_hashs = []
                for torrent in paused_torrents:
                    # 当前种子 tags list
                    current_torrent_tag_list = [element.strip() for element in torrent.labels]
                    available = torrent.available
                    if available == 100.0 and not noautostart_set.intersection(current_torrent_tag_list):
                        pausedUP_torrent_hashs.append(torrent.hashString)
                        logger.info(f"{downloader} 自动开始 {torrent.name} {current_torrent_tag_list}")
                for torrent in paused_torrents:
                    # 当前种子 tags list
                    current_torrent_tag_list = [element.strip() for element in torrent.labels]
                    available = torrent.available
                    if available < 100.0:
                        logger.info(f"{downloader} 不自动开始 {torrent.name}, torrent.available={available} {current_torrent_tag_list}")
                    else:
                        intersection2 = noautostart_set.intersection(current_torrent_tag_list)
                        if intersection2:
                            logger.info(f"{downloader} 不自动开始 {torrent.name}, 含有不开始标签 {intersection2} {current_torrent_tag_list}")
                if len(pausedUP_torrent_hashs) > 0:
                    downloader_obj.start_torrents(ids=pausedUP_torrent_hashs)
                # 设置限速站点
                if self._limit_sites:
                    all_torrents, _ = downloader_obj.get_torrents()
                    to_limit_torrent_hashs = []
                    for torrent in all_torrents:
                        # 当前种子 tags list
                        current_torrent_tag_list = [element.strip() for element in torrent.labels]
                        # tr 补充站点标签,交集第一个就是站点标签
                        intersection = all_site_names.intersection(current_torrent_tag_list)
                        site_name = None
                        if intersection:
                            site_name = list(intersection)[0]
                        if all_site_name_id_map[site_name] in self._limit_sites:
                            to_limit_torrent_hashs.append(torrent.hashString)
                    if to_limit_torrent_hashs:
                        downloader_obj.change_torrent(hash_string=to_limit_torrent_hashs, upload_limit=100)
                        logger.info(f"{downloader} 限速100K种子个数: {len(to_limit_torrent_hashs)}")
        # 保存缓存
        self.__update_config()
        # 发送消息
        if self._notify:
            if self.success or self.fail:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【IYUU自动辅种任务完成】",
                    text=f"服务器返回可辅种总数：{self.total}\n"
                         f"实际可辅种数：{self.realtotal}\n"
                         f"已存在：{self.exist}\n"
                         f"成功：{self.success}\n"
                         f"失败：{self.fail}\n"
                         f"{self.cached} 条失败记录已加入缓存"
                )
        logger.info("辅种任务执行完成")

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

    def __is_current_time_in_range_site_config(self) -> bool:
        """判断当前时间是否在开启限速时间区间内-限速时间段"""
        active_time_range_site_config = self._active_time_range_site_config
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

    def check_recheck(self):
        """
        定时检查下载器中种子是否校验完成，校验完成且完整的自动开始辅种
        """
        if not self.service_infos:
            return
        if not self._recheck_torrents:
            return
        if self._is_recheck_running:
            return
        self._is_recheck_running = True
        for service in self.service_infos.values():
            # 需要检查的种子
            downloader = service.name
            downloader_obj = service.instance
            recheck_torrents = self._recheck_torrents.get(downloader) or []
            if not recheck_torrents:
                continue
            logger.info(f"开始检查下载器 {downloader} 的校验任务 ...")
            # 获取下载器中的种子状态
            torrents, _ = downloader_obj.get_torrents(ids=recheck_torrents)
            if torrents:
                can_seeding_torrents = []
                for torrent in torrents:
                    # 获取种子hash
                    hash_str = self.__get_hash(torrent=torrent, dl_type=service.type)
                    if self.__can_seeding(torrent=torrent, dl_type=service.type):
                        can_seeding_torrents.append(hash_str)
                if can_seeding_torrents:
                    logger.info(f"共 {len(can_seeding_torrents)} 个任务校验完成，开始辅种 ...")
                    # 开始任务
                    downloader_obj.start_torrents(ids=can_seeding_torrents)
                    # 去除已经处理过的种子
                    self._recheck_torrents[downloader] = list(
                        set(recheck_torrents).difference(set(can_seeding_torrents)))
            elif torrents is None:
                logger.info(f"下载器 {downloader} 查询校验任务失败，将在下次继续查询 ...")
                continue
            else:
                logger.info(f"下载器 {downloader} 中没有需要检查的校验任务，清空待处理列表 ...")
                self._recheck_torrents[downloader] = []
        self._is_recheck_running = False

    def __seed_torrents(self, hash_strs: list, service: ServiceInfo, all_hashs_in_cur_downloader):
        """
        执行一批种子的辅种
        """
        if not hash_strs:
            return
        logger.info(f"下载器 {service.name} 开始查询辅种，数量：{len(hash_strs)} ...")
        # 下载器中的Hashs
        hashs = [item.get("hash") for item in hash_strs]
        # 每个Hash的保存目录
        save_paths = {}
        for item in hash_strs:
            save_paths[item.get("hash")] = item.get("save_path")
        # 查询可辅种数据
        seed_list, msg = self.iyuu_helper.get_seed_info(hashs)
        if not isinstance(seed_list, dict):
            # 判断辅种异常是否是由于Token未认证导致的，由于没有解决接口，只能从返回值来判断
            if self._token and msg == '请求缺少token':
                logger.warn(f'IYUU辅种失败，疑似站点未绑定插件配置不完整，请先检查是否完成站点绑定！{msg}')
            else:
                logger.warn(f"当前种子列表没有可辅种的站点：{msg}")
            return
        else:
            logger.info(f"IYUU返回可辅种数：{len(seed_list)}")
        # 遍历
        for current_hash, seed_info in seed_list.items():
            if not seed_info:
                continue
            seed_torrents = seed_info.get("torrent")
            if not isinstance(seed_torrents, list):
                seed_torrents = [seed_torrents]

            # 本次辅种成功的种子
            success_torrents = []

            for seed in seed_torrents:
                if not seed:
                    continue
                if not isinstance(seed, dict):
                    continue
                seed_info_hash = seed.get("info_hash")
                if not seed.get("sid") or not seed_info_hash:
                    continue
                if seed_info_hash in all_hashs_in_cur_downloader:
                    logger.debug(f"{seed_info_hash} 已在下载器中，跳过 ...")
                    continue
                if seed_info_hash in self._success_caches:
                    logger.info(f"{seed_info_hash} 已处理过辅种，跳过 ...")
                    continue
                if seed_info_hash in self._error_caches or seed_info_hash in self._permanent_error_caches:
                    logger.info(f"种子 {seed_info_hash} 辅种失败且已缓存，跳过 ...")
                    continue
                # 添加任务
                success = self.__download_torrent(seed=seed,
                                                  service=service,
                                                  save_path=save_paths.get(current_hash))
                if success:
                    success_torrents.append(seed_info_hash)

            # 辅种成功的去重放入历史
            if len(success_torrents) > 0:
                self.__save_history(current_hash=current_hash,
                                    downloader=service.name,
                                    success_torrents=success_torrents)

        logger.info(f"下载器 {service.name} 辅种部分完成 ...")
    def __save_history(self, current_hash: str, downloader: str, success_torrents: []):
        """
        [
            {
                "downloader":"2",
                "torrents":[
                    "248103a801762a66c201f39df7ea325f8eda521b",
                    "bd13835c16a5865b01490962a90b3ec48889c1f0"
                ]
            },
            {
                "downloader":"3",
                "torrents":[
                    "248103a801762a66c201f39df7ea325f8eda521b",
                    "bd13835c16a5865b01490962a90b3ec48889c1f0"
                ]
            }
        ]
        """
        try:
            # 查询当前Hash的辅种历史
            seed_history = self.get_data(key=current_hash) or []

            new_history = True
            if len(seed_history) > 0:
                for history in seed_history:
                    if not history:
                        continue
                    if not isinstance(history, dict):
                        continue
                    if not history.get("downloader"):
                        continue
                    # 如果本次辅种下载器之前有过记录则继续添加
                    if str(history.get("downloader")) == downloader:
                        history_torrents = history.get("torrents") or []
                        history["torrents"] = list(set(history_torrents + success_torrents))
                        new_history = False
                        break

            # 本次辅种下载器之前没有成功记录则新增
            if new_history:
                seed_history.append({
                    "downloader": downloader,
                    "torrents": list(set(success_torrents))
                })

            # 保存历史
            self.save_data(key=current_hash,
                           value=seed_history)
        except Exception as e:
            print(str(e))

    def __download(self, service: ServiceInfo, content: bytes,
                   save_path: str, site_name: str) -> Optional[str]:

        torrent_tags = self._labelsafterseed.split(',')

        # 辅种 tag 叠加站点名
        if self._addhosttotag:
            torrent_tags.append(site_name)

        """
        添加下载任务
        """
        if service.type == "qbittorrent":
            # 生成随机Tag
            tag = StringUtils.generate_random_str(10)

            torrent_tags.append(tag)

            state = service.instance.add_torrent(content=content,
                                                 download_dir=save_path,
                                                 is_paused=True,
                                                 tag=torrent_tags,
                                                 category=self._categoryafterseed,
                                                 is_skip_checking=self._skipverify)
            if not state:
                return None
            else:
                # 获取种子Hash
                torrent_hash = service.instance.get_torrent_id_by_tag(tags=tag)
                if not torrent_hash:
                    logger.error(f"{service.name} 下载任务添加成功，但获取任务信息失败！")
                    return None
            return torrent_hash
        elif service.type == "transmission":
            # 添加任务
            torrent = service.instance.add_torrent(content=content,
                                                   download_dir=save_path,
                                                   is_paused=True,
                                                   labels=torrent_tags)
            if not torrent:
                return None
            else:
                return torrent.hashString

        logger.error(f"不支持的下载器：{service.type}")
        return None

    def __download_torrent(self, seed: dict, service: ServiceInfo, save_path: str):
        """
        下载种子
        torrent: {
                    "sid": 3,
                    "torrent_id": 377467,
                    "info_hash": "a444850638e7a6f6220e2efdde94099c53358159"
                }
        """

        def __is_special_site(url):
            """
            判断是否为特殊站点（是否需要添加https）
            """
            if "hdsky.me" in url:
                return False
            return True

        self.total += 1
        # 获取种子站点及下载地址模板
        site_url, download_page = self.iyuu_helper.get_torrent_url(seed.get("sid"))
        if not site_url or not download_page:
            # 加入缓存
            self._error_caches.append(seed.get("info_hash"))
            self.fail += 1
            self.cached += 1
            return False
        # 查询站点
        site_domain = StringUtils.get_url_domain(site_url)
        # 站点信息
        site_info = self.sites_helper.get_indexer(site_domain)
        if not site_info or not site_info.get('url'):
            logger.debug(f"没有维护种子对应的站点：{site_url}")
            return False
        if self._sites and site_info.get('id') not in self._sites:
            logger.debug("当前站点不在选择的辅种站点范围，跳过 ...")
            return False
        self.realtotal += 1
        # 查询hash值是否已经在下载器中
        downloader_obj = service.instance
        torrent_info, _ = downloader_obj.get_torrents(ids=[seed.get("info_hash")])
        if torrent_info:
            logger.info(f"{seed.get('info_hash')} 下载前查询已在下载器中，跳过 ...")
            self.exist += 1
            return False
        # 站点流控
        check, checkmsg = self.sites_helper.check(site_domain)
        if check:
            logger.warn(checkmsg)
            self.fail += 1
            return False
        # 下载种子
        torrent_url = self.__get_download_url(seed=seed,
                                              site=site_info,
                                              base_url=download_page)
        if not torrent_url:
            # 加入失败缓存
            self._error_caches.append(seed.get("info_hash"))
            self.fail += 1
            self.cached += 1
            return False
        # 强制使用Https
        if __is_special_site(torrent_url):
            if "?" in torrent_url:
                torrent_url += "&https=1"
            else:
                torrent_url += "?https=1"
        # 下载种子文件
        _, content, _, _, error_msg = self.torrent_helper.download_torrent(
            url=torrent_url,
            cookie=site_info.get("cookie"),
            ua=site_info.get("ua") or settings.USER_AGENT,
            proxy=site_info.get("proxy"))
        if not content:
            # 下载失败
            self.fail += 1
            # 加入失败缓存
            if error_msg and ('无法打开链接' in error_msg or '触发站点流控' in error_msg):
                self._error_caches.append(seed.get("info_hash"))
            else:
                # 种子不存在的情况
                self._permanent_error_caches.append(seed.get("info_hash"))
            logger.error(f"下载种子文件失败：{torrent_url}")
            return False
        # 添加下载，辅种任务默认暂停
        logger.info(f"添加下载任务：{torrent_url} ...")
        download_id = self.__download(service=service,
                                      content=content,
                                      save_path=save_path,
                                      site_name=site_info.get("name"))
        if not download_id:
            # 下载失败
            self.fail += 1
            # 加入失败缓存
            self._error_caches.append(seed.get("info_hash"))
            return False
        else:
            self.success += 1
            if self._skipverify:
                # 跳过校验
                logger.info(f"{download_id} 跳过校验，请自行检查...")
                # 请注意这里是故意不自动开始的
                # 跳过校验存在直接失败、种子目录相同文件不同等异常情况
                # 必须要用户自行二次确认之后才能开始做种
                # 否则会出现反复下载刷掉分享率、做假种的情况
            else:
                # 追加校验任务
                logger.info(f"添加校验检查任务：{download_id} ...")
                if not self._recheck_torrents.get(service.name):
                    self._recheck_torrents[service.name] = []
                self._recheck_torrents[service.name].append(download_id)
                # TR会自动校验
                if service.type == "qbittorrent":
                    # 开始校验种子
                    downloader_obj.recheck_torrents(ids=[download_id])
            # 下载成功
            logger.info(f"成功添加辅种下载，站点：{site_info.get('name')}，种子链接：{torrent_url}")
            # 成功也加入缓存，有一些改了路径校验不通过的，手动删除后，下一次又会辅上
            self._success_caches.append(seed.get("info_hash"))
            return True

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
    def __can_seeding(torrent: Any, dl_type: str):
        """
        判断种子是否可以做种并处于暂停状态
        """
        try:
            return torrent.get("state") in ["pausedUP", "stoppedUP"] if dl_type == "qbittorrent" \
                else (torrent.status.stopped and torrent.percent_done == 1)
        except Exception as e:
            print(str(e))
            return False

    @staticmethod
    def __get_save_path(torrent: Any, dl_type: str):
        """
        获取种子保存路径
        """
        try:
            return torrent.get("save_path") if dl_type == "qbittorrent" else torrent.download_dir
        except Exception as e:
            print(str(e))
            return ""

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

    def __get_download_url(self, seed: dict, site: CommentedMap, base_url: str):
        """
        拼装种子下载链接
        """

        def __is_mteam(url: str):
            """
            判断是否为mteam站点
            """
            return True if "m-team." in url else False

        def __is_monika(url: str):
            """
            判断是否为monika站点
            """
            return True if "monikadesign." in url else False

        def __get_mteam_enclosure(tid: str, apikey: str):
            """
            获取mteam种子下载链接
            """
            if not apikey:
                logger.error("m-team站点的apikey未配置")
                return None

            """
            将mteam种子下载链接域名替换为使用API
            """
            api_url = re.sub(r'//[^/]+\.m-team', '//api.m-team', site.get('url'))
            ua = site.get("ua") or settings.USER_AGENT
            res = RequestUtils(
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': f'{ua}',
                    'Accept': 'application/json, text/plain, */*',
                    'x-api-key': apikey
                }
            ).post_res(f"{api_url}api/torrent/genDlToken", params={
                'id': tid
            })
            if not res:
                logger.warn(f"m-team 获取种子下载链接失败：{tid}")
                return None
            return res.json().get("data")

        def __get_monika_torrent(tid: str, rssurl: str):
            """
            Monika下载需要使用rsskey从站点配置中获取并拼接下载链接
            """
            if not rssurl:
                logger.error("Monika站点的rss链接未配置")
                return None

            rss_match = re.search(r'/rss/\d+\.(\w+)', rssurl)
            rsskey = rss_match.group(1)
            return f"{site.get('url')}torrents/download/{tid}.{rsskey}"

        def __is_special_site(url: str):
            """
            判断是否为特殊站点
            """
            spec_params = ["hash=", "authkey="]
            if any(field in base_url for field in spec_params):
                return True
            if "hdchina.org" in url:
                return True
            if "hdsky.me" in url:
                return True
            if "hdcity.in" in url:
                return True
            if "totheglory.im" in url:
                return True
            return False

        try:
            if __is_mteam(site.get('url')):
                # 调用mteam接口获取下载链接
                return __get_mteam_enclosure(tid=seed.get("torrent_id"), apikey=site.get("apikey"))
            if __is_monika(site.get('url')):
                # 返回种子id和站点配置中所Monika的rss链接
                return __get_monika_torrent(tid=seed.get("torrent_id"), rssurl=site.get("rss"))
            elif __is_special_site(site.get('url')):
                # 从详情页面获取下载链接
                return self.__get_torrent_url_from_page(seed=seed, site=site)
            else:
                download_url = base_url.replace(
                    "id={}",
                    "id={id}"
                ).replace(
                    "/{}",
                    "/{id}"
                ).replace(
                    "/{torrent_key}",
                    ""
                ).format(
                    **{
                        "id": seed.get("torrent_id"),
                        "passkey": site.get("passkey") or '',
                        "uid": site.get("uid") or '',
                    }
                )
                if download_url.count("{"):
                    logger.warn(f"当前不支持该站点的辅助任务，Url转换失败：{seed}")
                    return None
                download_url = re.sub(r"[&?]passkey=", "",
                                      re.sub(r"[&?]uid=", "",
                                             download_url,
                                             flags=re.IGNORECASE),
                                      flags=re.IGNORECASE)
                return f"{site.get('url')}{download_url}"
        except Exception as e:
            logger.warn(
                f"{site.get('name')} Url转换失败，{str(e)}：site_url={site.get('url')}，base_url={base_url}, seed={seed}")
            return self.__get_torrent_url_from_page(seed=seed, site=site)

    def __get_torrent_url_from_page(self, seed: dict, site: dict):
        """
        从详情页面获取下载链接
        """
        if not site.get('url'):
            logger.warn(f"站点 {site.get('name')} 未获取站点地址，无法获取种子下载链接")
            return None
        try:
            page_url = f"{site.get('url')}details.php?id={seed.get('torrent_id')}&hit=1"
            logger.info(f"正在获取种子下载链接：{page_url} ...")
            res = RequestUtils(
                cookies=site.get("cookie"),
                ua=site.get("ua") or settings.USER_AGENT,
                proxies=settings.PROXY if site.get("proxy") else None
            ).get_res(url=page_url)
            if res is not None and res.status_code in (200, 500):
                if "charset=utf-8" in res.text or "charset=UTF-8" in res.text:
                    res.encoding = "UTF-8"
                else:
                    res.encoding = res.apparent_encoding
                if not res.text:
                    logger.warn(f"获取种子下载链接失败，页面内容为空：{page_url}")
                    return None
                # 使用xpath从页面中获取下载链接
                html = etree.HTML(res.text)
                for xpath in self._torrent_xpaths:
                    download_url = html.xpath(xpath)
                    if download_url:
                        download_url = download_url[0]
                        logger.info(f"获取种子下载链接成功：{download_url}")
                        if not download_url.startswith("http"):
                            if download_url.startswith("/"):
                                download_url = f"{site.get('url')}{download_url[1:]}"
                            else:
                                download_url = f"{site.get('url')}{download_url}"
                        return download_url
                logger.warn(f"获取种子下载链接失败，未找到下载链接：{page_url}")
                return None
            else:
                logger.error(f"获取种子下载链接失败，请求失败：{page_url}，{res.status_code if res else ''}")
                return None
        except Exception as e:
            logger.warn(f"获取种子下载链接失败：{str(e)}")
            return None

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def __custom_sites(self) -> List[Any]:
        custom_sites = []
        custom_sites_config = self.get_config("CustomSites")
        if custom_sites_config and custom_sites_config.get("enabled"):
            custom_sites = custom_sites_config.get("sites")
        return custom_sites

    @eventmanager.register(EventType.SiteDeleted)
    def site_deleted(self, event):
        """
        删除对应站点选中
        """
        site_id = event.event_data.get("site_id")
        config = self.get_config()
        if config:
            sites = config.get("sites")
            limit_sites = config.get("limit_sites")
            if sites:
                if isinstance(sites, str):
                    sites = [sites]
                if isinstance(limit_sites, str):
                    limit_sites = [limit_sites]

                # 删除对应站点
                if site_id:
                    sites = [site for site in sites if int(site) != int(site_id)]
                    limit_sites = [site for site in limit_sites if int(site) != int(site_id)]
                else:
                    # 清空
                    sites = []
                    limit_sites = []

                # 若无站点，则停止
                if len(sites) == 0:
                    self._enabled = False

                self._sites = sites
                self._limit_sites = limit_sites
                # 保存配置
                self.__update_config()
