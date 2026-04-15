from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.core.event import eventmanager, Event
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo
from app.schemas.types import EventType
from qbittorrentapi import TorrentState, TrackerStatus
from urllib.parse import urlparse
import time


class QBBanIp(_PluginBase):
    # 插件名称
    plugin_name = "QB IP黑名单"
    # 插件描述
    plugin_desc = "自定义时间点执行限速逻辑"
    # 插件图标
    plugin_icon = "upload.png"
    # 插件版本
    plugin_version = "1.0.7"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "qbbanip_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _onlyonce = False
    _notify = False
    _cron = None

    _tracker_ports = []  # 端口白名单
    _nopaths = None  # 自动管理hash
    _tracker_domain = None  # tracker域名过滤
    _nolabels = ""  # 标签过滤
    _no_torrent_size = 0  # 体积过滤

    _downloaders1 = []

    # 定时器¬
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()
        if config:
            self._enabled = config.get("enabled")
            self._onlyonce = config.get("onlyonce")
            self._notify = config.get("notify")
            self._cron = config.get("cron")

            self._tracker_ports = config.get("tracker_ports") or ""  # 端口白名单
            self._nopaths = config.get("nopaths")
            self._tracker_domain = config.get("tracker_domain") or ""  # tracker域名过滤
            self._nolabels = config.get("nolabels") or ""  # 标签过滤
            self._no_torrent_size = config.get("no_torrent_size") or ""  # 体积过滤

            self._downloaders1 = config.get("downloaders1")
            # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._onlyonce:
                logger.info(f"QB IP黑名单服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="QB IP黑名单",
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

        services = DownloaderHelper().get_services(name_filters=downloaders)
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
        logger.debug(f"QB IP黑名单 run...")
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "ban":
                return
            logger.info("收到ban命令，开始更新QB IP黑名单 ...")
            self.post_message(
                mtype=NotificationType.SiteMessage, title=f"开始更新QB IP黑名单 ...")

        # if not self.get_downloader_service_infos:
        #     return

        msg = ""
        try:
            self.limit()
            success = True
        except Exception as e:
            success = False
            logger.error(f"QB IP黑名单更新出错: {e}")
            msg = f"{e}"
        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB IP黑名单更新成功】")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【QB IP黑名单更新出错】", text=msg
                )

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
                定义远程控制命令
                :return: 命令关键字、事件、描述、附带数据
                """
        return [{
            "cmd": "/ban",
            "event": EventType.PluginAction,
            "desc": "QB IP黑名单",
            "data": {
                "action": "ban"
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
            logger.info(f"QB IP黑名单服务重新启动，执行周期 {self._cron}")
            return [{
                "id": "QBBanIp",
                "name": "QB IP黑名单",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run,
                "kwargs": {}
            }]
        logger.info("QB IP黑名单服务未开启")
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
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
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
                                       "content": [
                                           {
                                               "component": "VCronField",
                                               "props": {
                                                   "model": "cron",
                                                   "label": "执行周期"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
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
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
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
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
                                       "content": [
                                           {
                                               "component": "VSelect",
                                               "props": {
                                                   "chips": True,
                                                   "multiple": True,
                                                   "clearable": True,
                                                   "model": "downloaders1",
                                                   "label": "下载器",
                                                   "items": [
                                                       {"title": config.name, "value": config.name}
                                                       for config in
                                                       DownloaderHelper().get_configs().values()
                                                   ]
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "tracker_domain",
                                                   "label": "tracker过滤",
                                                   "placeholder": "只处理tracker域名包含此值的种子,为空全部处理"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "nolabels",
                                                   "label": "标签过滤",
                                                   "placeholder": "只处理标签包含此值的种子,为空全部处理"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 3
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "no_torrent_size",
                                                   "label": "种子体积过滤(GB)",
                                                   "placeholder": "只处理体积大于此值的种子"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                           "md": 12
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {
                                                   "model": "tracker_ports",
                                                   "label": "端口白名单",
                                                   "placeholder": "多个用英文逗号分隔"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                       },
                                       "content": [
                                           {
                                               'component': 'VTextarea',
                                               'props': {
                                                   'model': 'nopaths',
                                                   'label': '自动管理hash值',
                                                   'rows': 2,
                                                   'placeholder': '每一行一个hash'
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
                                           "cols": 12
                                       },
                                       "content": [
                                           {
                                               "component": "VAlert",
                                               "props": {
                                                   "type": "info",
                                                   "variant": "tonal",
                                                   "text": "只处理下载器中,tracker包含输入项,且标签包含输入项,且体积大于输入项的种子"
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12
                                       },
                                       "content": [
                                           {
                                               "component": "VAlert",
                                               "props": {
                                                   "type": "info",
                                                   "variant": "tonal",
                                                   "text": "不在端口白名单中的IP会加入QB黑名单"
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
                   "cron": "*/3 * * * *",
                   "tracker_ports": "63222,63223,63224",
                    "nopaths": "",
                    "tracker_domain": "dmhy",
                   "nolabels": "",
                   "no_torrent_size": "10",
                   "downloaders1": []
               }

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "onlyonce": False,
            "notify": self._notify,
            "cron": self._cron,
            "tracker_ports": self._tracker_ports,
            "nopaths": self._nopaths,
            "tracker_domain": self._tracker_domain,
            "nolabels": self._nolabels,
            "no_torrent_size": self._no_torrent_size,
            "downloaders1": self._downloaders1
        })

    def limit(self):
        """
        开始限速
        """
        if self._downloaders1:
            pass
        else:
            logger.warning("未设置下载器,取消执行")
            return

        downloader_service_infos = self.get_downloader_service_infos(self._downloaders1)
        if not downloader_service_infos:
            logger.warning("下载器链接错误,取消执行")
            return
        logger.info(f"开始执行，扫描标签包含'{self._nolabels}'，tracker包含'{self._tracker_domain}'的种子...")
        logger.info(f"允许的端口连接: {self._tracker_ports}")
        for downloader_service_info in downloader_service_infos.values():
            downloader = downloader_service_info.name
            downloader_obj = downloader_service_info.instance
            dl_type = downloader_service_info.type
            logger.info(f"开始扫描下载器: {downloader}")
            if dl_type == "qbittorrent":
                self.limit_per_downloader(downloader_obj.qbc, downloader)
            else:
                logger.warning(f"暂不支持 {dl_type} 类型下载器 {downloader}")
        logger.info(f"执行完成")

    def readable_file_size(self, file_size, has_frac=True):
        if has_frac:
            if file_size < 1024:
                return f"{file_size}B"
            elif file_size < 1024 * 1024:
                return f"{file_size / 1024:.2f}KB"
            elif file_size < 1024 * 1024 * 1024:
                return f"{file_size / (1024 * 1024):.2f}MB"
            elif file_size < 1024 * 1024 * 1024 * 1024:
                return f"{file_size / (1024 * 1024 * 1024):.2f}GB"
            else:
                return f"{file_size / (1024 * 1024 * 1024 * 1024):.2f}TB"
        else:
            if file_size < 1024:
                return f"{file_size}B"
            elif file_size < 1024 * 1024:
                return f"{file_size / 1024:.0f}KB"
            elif file_size < 1024 * 1024 * 1024:
                return f"{file_size / (1024 * 1024):.0f}MB"
            elif file_size < 1024 * 1024 * 1024 * 1024:
                return f"{file_size / (1024 * 1024 * 1024):.0f}GB"
            else:
                return f"{file_size / (1024 * 1024 * 1024 * 1024):.0f}TB"

    def limit_per_downloader(self, qbt_client, downloader_name):
        TARGET_TORRENT_SIZE = int(self._no_torrent_size) * 1024 * 1024 * 1024  # xGB
        TARGET_TAG = self._nolabels
        TARGET_TRACKER = self._tracker_domain
        # 按逗号分割字符串，去除每个元素的空白字符，转换为整数
        TARGET_PORT_RANGE = [
            int(port.strip())
            for port in self._tracker_ports.split(',')
            if port.strip()  # 过滤空字符串（如",,80,,"的情况）
        ]
        DOWNLOADLIMIT_SPEED = 11 * 1024 * 1024
        # 获取需要屏蔽的IP
        ips_to_block = set()
        # 获取下载中，非下载状态跳过
        torrents = qbt_client.torrents_info(status_filter=TorrentState.DOWNLOADING)
        logger.info(f"🌱 {downloader_name}下载状态共 {len(torrents)} 个种子")

        to_limit_hashs = []
        for torrent in torrents:
            # if torrent.state_enum.is_downloading:
            #     print()
            # 体积 10G 以下跳过
            total_size = torrent.total_size
            if total_size < TARGET_TORRENT_SIZE:
                logger.info(
                    f"---种子'{torrent['name'][:30]}...'体积{self.readable_file_size(total_size)},小于配置值({self.readable_file_size(TARGET_TORRENT_SIZE, False)}),跳过")
                continue
            # 添加时间超过30天跳过
            # added_on_s = int(start - torrent.added_on)
            # if added_on_s > cost:
            # logger.info(f'---种子添加超过 {added_on_s} 秒')

            current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
            if TARGET_TAG and TARGET_TAG not in current_torrent_tag_list:
                logger.info(f"---种子'{torrent['name'][:30]}...'标签不包含{TARGET_TAG},跳过")
                continue
            # logger.info(f'---种子标签 {current_torrent_tag_list}')

            # tracker 不匹配猪跳过
            if TARGET_TRACKER:
                working_trackers = [tracker for tracker in torrent.trackers if
                                    tracker.status != TrackerStatus.DISABLED]
                contanin_tracker = False
                for tracker in working_trackers:
                    domain = urlparse(tracker.url).netloc
                    if TARGET_TRACKER in domain:
                        contanin_tracker = True
                        break
                if not contanin_tracker:
                    logger.info(f"---种子'{torrent['name'][:30]}...'tracker不包含'{TARGET_TRACKER}',跳过")
                    continue
            to_limit_hashs.append(torrent.hash)

            # 获取其peer 连接到的IP端口,不在白名单的添加到 ips_to_block
            peers = qbt_client.sync.torrent_peers(torrent.hash).peers
            cur_to_block_count = 0
            for ip_port, dict1 in peers.items():
                ip = dict1['ip']  # str
                port = dict1['port']  # int
                # country = dict1['country']  # 中国大陆
                # dl_speed = dict1['dl_speed']  # int
                # up_speed = dict1['up_speed']  # int
                # logger.info(f"------peer:{ip}:{port},dl_speed={dl_speed},up_speed={up_speed}")
                if not ip or not port:
                    continue
                # 检查端口是否在允许范围内
                if port not in TARGET_PORT_RANGE:
                    ips_to_block.add(ip)
                    cur_to_block_count = cur_to_block_count + 1
            logger.info(
                f"---种子'{torrent['name'][:30]}...'共{len(peers)}个peer,待屏蔽{cur_to_block_count}个")

            # 自动删除种子内容
            logger.info(f"self._nopaths={self._nopaths}")
            if self._nopaths:
                # for toHash in self._nopaths.split('\n'):
                toHashs = {x.strip() for x in self._nopaths.splitlines() if x.strip()}
                logger.info(f"toHashs={toHashs}, torrent.hash={torrent.hash}")
                if torrent.hash in toHashs:
                    progress = torrent.progress
                    logger.info(f"progress={progress}")
                    if progress > 0.08:
                        # 重新汇报
                        qbt_client.torrents_reannounce(torrent_hashes=torrent.hash)
                        time.sleep(2)
                        # 导出
                        torrent_file = qbt_client.torrents_export(torrent_hash=torrent.hash)
                        # 删除
                        qbt_client.torrents_delete(torrent_hashes=torrent.hash, delete_files=True)
                        # 重新添加
                        qbt_client.torrents_add(
                            torrent_files=torrent_file,
                            save_path=torrent.save_path,
                            category=torrent.category,
                            tags=torrent.tags,
                        )
                        logger.info(
                            f"---种子'{torrent['name'][:30]}...'下载进度{progress * 100:.2f}%,超80%,已清除重来")
                        time.sleep(2)


        if to_limit_hashs:
            qbt_client.torrents_set_download_limit(DOWNLOADLIMIT_SPEED, to_limit_hashs)  # 11M

        if ips_to_block:
            # logger.info(f"🎯 发现 {len(ips_to_block)} 个需要屏蔽的IP:")
            # for ip in sorted(ips_to_block)[:10]:  # 闲情只显示前10个
            #     logger.info(f"  {ip}")
            # if len(ips_to_block) > 10:
            #     logger.info(f"  ... 以及另外 {len(ips_to_block) - 10} 个IP")

            # 更新黑名单
            # 获取当前黑名单
            current_prefs = qbt_client.app_preferences()
            current_blocklist = current_prefs.get("banned_IPs", "")
            current_ips = set(filter(None, current_blocklist.split('\n')))

            # 添加新IP
            updated_ips = current_ips.union(ips_to_block)
            updated_blocklist = "\n".join(updated_ips)

            # 应用更新
            qbt_client.app.set_preferences({"banned_IPs": updated_blocklist})
            logger.info(
                f"✅ {downloader_name}成功更新IP黑名单,本次新增 {len(ips_to_block)} 个IP,下载器中共 {len(updated_ips)} 个IP")
        else:
            logger.info(f"🎯 {downloader_name}未发现需要屏蔽的IP地址")

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
