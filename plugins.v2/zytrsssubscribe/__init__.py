import datetime
import re
import traceback
from typing import Optional, Any, List, Dict, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.schemas import NotificationType, TorrentInfo, MediaType, ServiceInfo

from app.helper.rss import RssHelper
from app.log import logger
from app.plugins import _PluginBase
from app.utils.string import StringUtils
from app.utils.http import RequestUtils
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper

class ZYTRssSubscribe(_PluginBase):
    # 插件名称
    plugin_name = "RSS订阅下载"
    # 插件描述
    plugin_desc = "RSS订阅下载,只支持qbittorrent"
    # 插件图标
    plugin_icon = "seed.png"
    # 插件版本
    plugin_version = "1.0.4"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytrsssubscribe_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 2

    # 私有变量
    _scheduler: Optional[BackgroundScheduler] = None
    rsshelper = None
    site = None
    downloader_helper = None

    # 配置属性
    _enabled: bool = False
    _cron: str = ""
    _simulate: bool = True
    _notify: bool = False
    _onlyonce: bool = False
    _address: str = ""
    _include: str = ""
    _exclude: str = ""
    _save_path: str = ""
    _size_range: str = ""
    _downloader = None

    def init_plugin(self, config: dict = None):
        self.rsshelper = RssHelper()
        self.site = SiteOper()
        self.downloader_helper = DownloaderHelper()
        # 停止现有任务
        self.stop_service()
        # 配置
        if config:
            self.__validate_and_fix_config(config=config)
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._simulate = config.get("simulate")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._address = config.get("address")
            self._include = config.get("include")
            self._exclude = config.get("exclude")
            self._save_path = config.get("save_path")
            self._size_range = config.get("size_range")
            self._downloader = config.get("downloader")

        if self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            logger.info(f"RSS订阅下载服务启动，立即运行一次")
            self._scheduler.add_job(func=self.check, trigger='date',
                                    run_date=datetime.datetime.now(
                                        tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                    )

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

        if self._onlyonce:
            # 关闭一次性开关
            self._onlyonce = False
            # 保存设置
            self.__update_config()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
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
        if self._enabled and self._cron:
            return [{
                "id": "ZYTRssSubscribe",
                "name": "RSS订阅下载服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.check,
                "kwargs": {}
            }]
        elif self._enabled:
            return [{
                "id": "ZYTRssSubscribe",
                "name": "RSS订阅下载服务",
                "trigger": "interval",
                "func": self.check,
                "kwargs": {"minutes": 30}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 下载器选项
        downloader_options = [{"title": config.name, "value": config.name}
                              for config in self.downloader_helper.get_configs().values()]
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
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
                                    'md': 3
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
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'simulate',
                                            'label': '模拟下载',
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
                                    'md': 3
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
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'address',
                                            'label': 'RSS地址',
                                            'rows': 3,
                                            'placeholder': '每行一个RSS地址'
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
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式，留空自动'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'downloader',
                                            'label': '下载器',
                                            'items': downloader_options
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
                                            'model': 'save_path',
                                            'label': '保存目录',
                                            'placeholder': '下载时有效，留空自动'
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
                                            'model': 'size_range',
                                            'label': '种子大小(GB)',
                                            'placeholder': '如：3 或 3-5'
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
                                            'model': 'include',
                                            'label': '包含',
                                            'placeholder': '支持正则表达式'
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
                                            'model': 'exclude',
                                            'label': '排除',
                                            'placeholder': '支持正则表达式'
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
            "notify": True,
            "simulate": True,
            "onlyonce": False,
            "cron": "*/30 * * * *",
            "address": "",
            "include": "",
            "exclude": "",
            "save_path": "",
            "size_range": ""
        }

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

    def __update_config(self):
        """
        更新设置
        """
        self.update_config({
            "enabled": self._enabled,
            "simulate": self._simulate,
            "notify": self._notify,
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "address": self._address,
            "include": self._include,
            "exclude": self._exclude,
            "save_path": self._save_path,
            "size_range": self._size_range,
            "downloader": self._downloader,
        })

    @property
    def service_info(self) -> Optional[ServiceInfo]:
        """
        服务信息
        """
        if not self._downloader:
            logger.warning("尚未配置下载器，请检查配置")
            return None
        service = self._downloader_helper.get_service(name=self._downloader)
        if not service or not service.instance:
            self.__log_and_notify_error("RSS任务出错，获取下载器实例失败，请检查配置")
            return None

        if service.instance.is_inactive():
            self.__log_and_notify_error("RSS任务出错，下载器未连接")
            return None
        if not self.downloader_helper.is_downloader("qbittorrent", service=service):
            logger.error(f"当前只支持qbittorrent下载器")
            return
        return service

    def __download(self, service: ServiceInfo, torrent_content: str, torrent_title: str, site_ua: str,
                   site_cookie: str) -> Optional[bool]:  # 是否下载成功
        """
        添加下载任务
        """
        if torrent_content.startswith("["):
            torrent_content = self.__get_redict_url(url=torrent_content,
                                                    ua=site_ua,
                                                    cookie=site_cookie)
            # 目前馒头请求实际种子时，不能传入Cookie
            site_cookie = None
        if not torrent_content:
            logger.error(f"获取下载链接失败：{torrent_title}")
            return False

        # 如果开启代理下载以及种子地址不是磁力地址，则请求种子到内存再传入下载器
        if not torrent_content.startswith("magnet"):
            response = RequestUtils(cookies=site_cookie,
                                    ua=site_ua).get_res(url=torrent_content)
            if response and response.ok:
                torrent_content = response.content
            else:
                logger.error("尝试通过MP下载种子失败，继续尝试传递种子地址到下载器进行下载")
        if torrent_content:
            state = service.instance.add_torrent(content=torrent_content,
                                                 download_dir=self._save_path,
                                                 cookie=site_cookie,
                                                 tag=["RSS", "AUTO-RSS"])
            if state:
                return True
            if not state:
                logger.error("传递种子地址到下载器, 下载种子失败")
                return False
        return False

    def check(self):
        logger.info(f"debug check..., _address={self._address}")

        if not self._address:
            return
        # 下载器实例
        service = self.service_info
        if not service:
            logger.error(f"未获取到下载器实例")
            return
        urls = self._address.split("\n")
        logger.info(f"RSS 返回个数：{len(urls)} ...")
        for url in urls:
            # 处理每一个RSS链接
            if not url:
                continue
            logger.info(f"开始刷新RSS：{url} ...")
            rss_items = self.rsshelper.parse(url, timeout=30)
            if not rss_items:
                logger.error(f"未获取到RSS数据：{url}")
                return
            # 解析数据 组装种子
            for item in rss_items:
                # 获取种子对应站点cookie
                domain = StringUtils.get_url_domain(url)
                if not domain:
                    logger.error(f"RSS {url} 获取站点域名失败，跳过处理")
                    continue
                # 查询站点
                site = self.site.get_by_domain(domain)
                if not site:
                    logger.error(f"RSS {url} 获取站点失败，跳过处理")
                    continue
                if not site.cookie:
                    logger.error(f"RSS {url} 获取站点cookie失败，跳过处理")
                    continue
                if not site.ua:
                    logger.error(f"RSS {url} 获取站点ua失败，跳过处理")
                    continue
                try:
                    if not item.get("title"):
                        continue
                    # 返回对象
                    # tmp_dict = {'title': title,
                    #             'enclosure': enclosure,
                    #             'size': size,
                    #             'description': description,
                    #             'link': link,
                    #             'pubdate': pubdate}
                    title = item.get("title")
                    enclosure = item.get("enclosure")  # 下载链接
                    size = item.get("size")
                    description = item.get("description")
                    link = item.get("link")
                    pubdate: datetime.datetime = item.get("pubdate")

                    # 检查是否处理过 todo
                    # if not title or title in [h.get("key") for h in history]:
                    if not title:
                        logger.error(f"获取title失败,跳过")
                        continue
                    if not enclosure:
                        logger.error(f"获取下载链接失败：{title}")
                        continue
                    # 检查规则
                    if self._include and not re.search(r"%s" % self._include,
                                                       f"{title} {description}", re.IGNORECASE):
                        logger.info(f"{title} - {description} 不符合包含规则")
                        continue
                    if self._exclude and re.search(r"%s" % self._exclude,
                                                   f"{title} {description}", re.IGNORECASE):
                        logger.info(f"{title} - {description} 不符合排除规则")
                        continue
                    if self._size_range:
                        sizes = [float(_size) * 1024 ** 3 for _size in self._size_range.split("-")]
                        if len(sizes) == 1 and float(size) < sizes[0]:
                            logger.info(f"{title} - 种子大小不符合条件")
                            continue
                        elif len(sizes) > 1 and not sizes[0] <= float(size) <= sizes[1]:
                            logger.info(f"{title} - 种子大小不在指定范围")
                            continue
                    # 添加下载任务
                    simulate_prefix = ''
                    if self._simulate:
                        result = True
                        simulate_prefix = '(模拟)'
                    else:
                        result = self.__download(service=service, torrent_content=enclosure, torrent_title=title,
                                                 site_ua=site.ua, site_cookie=site.cookie)
                    if result:
                        logger.info(f"{simulate_prefix}{title} 添加下载成功！")
                    else:
                        logger.warning(f"{simulate_prefix}{title} 添加下载失败！")
                        continue
                    # 存储历史记录,todo 站点 标题 发布时间 大小 做种人数
                    # history.append({
                    #     "title": f"{mediainfo.title} {meta.season}",
                    #     "key": f"{title}",
                    #     "type": mediainfo.type.value,
                    #     "year": mediainfo.year,
                    #     "poster": mediainfo.get_poster_image(),
                    #     "overview": mediainfo.overview,
                    #     "tmdbid": mediainfo.tmdb_id,
                    #     "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    # })
                except Exception as err:
                    logger.error(f'刷新RSS数据出错：{str(err)} - {traceback.format_exc()}')
            logger.info(f"RSS {url} 刷新完成")
        # 保存历史记录
        # self.save_data('history', history)
        logger.info(f"debug check over")

    def __log_and_notify_error(self, message):
        """
        记录错误日志并发送系统通知
        """
        logger.error(message)
        # self.systemmessage.put(message, title="RSS订阅下载")

    def __validate_and_fix_config(self, config: dict = None) -> bool:
        """
        检查并修正配置值
        """
        size_range = config.get("size_range")
        if size_range and not self.__is_number_or_range(str(size_range)):
            self.__log_and_notify_error(f"RSS订阅下载出错，种子大小设置错误：{size_range}")
            config["size_range"] = None
            return False
        return True

    @staticmethod
    def __is_number_or_range(value):
        """
        检查字符串是否表示单个数字或数字范围（如'5', '5.5', '5-10' 或 '5.5-10.2'）
        """
        return bool(re.match(r"^\d+(\.\d+)?(-\d+(\.\d+)?)?$", value))
