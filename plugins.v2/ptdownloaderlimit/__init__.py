import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import schemas
from app.core.config import settings
from app.core.event import Event, eventmanager
from app.db.site_oper import SiteOper
from app.helper.downloader import DownloaderHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, ServiceInfo
from app.schemas.types import EventType


class PTDownloaderLimit(_PluginBase):
    """按 PT 站点标签动态配置 qBittorrent、Transmission 上传限速。"""

    # 插件名称
    plugin_name = "QB&TR上传限速"
    # 插件描述
    plugin_desc = "按下载器、PT站点和时间段动态配置种子上传限速。"
    # 插件图标
    plugin_icon = "upload.png"
    # 插件版本
    plugin_version = "1.0.2"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "ptdownloaderlimit_"
    # 加载顺序
    plugin_order = 4
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _onlyonce = False
    _notify = False
    _cron = ""
    _nolabels = ""  # 不限速标签
    # 原插件的六组固定字段由有序动态规则列表替代。
    _rules: List[Dict[str, Any]] = []
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None
    to_pausedUP_hashs = {}  # 位于限速站点中因活动而暂停的种子hash,value=和最后活动时间

    @staticmethod
    def _empty_rule() -> Dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "mark": "",
            "downloaders": [],
            "limit_sites": [],
            "limit_speed": 0,
            "limit_sites_pause_threshold": 0,
            "active_time_range_site_config": "",
        }

    @classmethod
    def _normalize_rules(cls, rules: Any, *, default_when_missing: bool = False) -> List[Dict[str, Any]]:
        if not isinstance(rules, list):
            return [cls._empty_rule()] if default_when_missing else []
        normalized: List[Dict[str, Any]] = []
        for raw in rules:
            if not isinstance(raw, dict):
                continue
            rule = cls._empty_rule()
            rule.update({
                "id": str(raw.get("id") or rule["id"]),
                "mark": str(raw.get("mark") or ""),
                "downloaders": list(raw.get("downloaders") or []),
                "limit_sites": list(raw.get("limit_sites") or []),
                "limit_speed": cls._to_int(raw.get("limit_speed")),
                "limit_sites_pause_threshold": cls._to_int(
                    raw.get("limit_sites_pause_threshold")
                ),
                "active_time_range_site_config": str(
                    raw.get("active_time_range_site_config") or ""
                ),
            })
            normalized.append(rule)
        return normalized

    @staticmethod
    def _to_int(value: Any) -> int:
        # 与原插件的六组 int(config.get(...) or 0) 保持一致。
        return int(value or 0)

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()
        config = config or {}
        self._enabled = config.get("enabled")
        self._onlyonce = config.get("onlyonce")
        self._notify = config.get("notify")
        self._cron = config.get("cron")
        self._nolabels = config.get("nolabels") or ""
        # 原插件在此分别读取 downloaders1...downloaders6、limit_sites1...limit_sites6、
        # limit_speed1...limit_speed6 等固定字段；联邦动态表单改为等价的有序 rules 列表。
        # self._downloaders = config.get("downloaders")
        self._rules = self._normalize_rules(
            config.get("rules"), default_when_missing="rules" not in config
        )

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

    def _current_config(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "onlyonce": False,
            "notify": self._notify,
            "cron": self._cron,
            "nolabels": self._nolabels,
            "rules": self._rules,
        }

    def __update_config(self):
        self.update_config(self._current_config())

    def get_state(self) -> bool:
        return True if self._enabled and self._cron else False

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
        return [{
            "path": "/options",
            "endpoint": self.api_options,
            "methods": ["GET"],
            "auth": "bear",
            "summary": "获取上传限速的下载器与站点选项",
        }]

    def api_options(self) -> schemas.Response:
        downloader_options = [
            {"title": config.name, "value": config.name}
            for config in DownloaderHelper().get_configs().values()
        ]
        site_options = [
            {"title": site.name, "value": site.id}
            for site in SiteOper().list_order_by_pri()
        ]
        for site in self.__custom_sites():
            if site.get("name") and site.get("id") is not None:
                site_options.append({"title": site.get("name"), "value": site.get("id")})
        return schemas.Response(
            success=True,
            data={"downloaders": downloader_options, "sites": site_options},
        )

    @staticmethod
    def get_render_mode() -> Tuple[str, str]:
        return "vue", "dist/assets"

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [], self._current_config()

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
            logger.info(f"QB&TR上传限速服务重新启动，执行周期 {self._cron}")
            return [{
                "id": "PTDownloaderLimit",
                "name": "QB&TR上传限速",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.run,
                "kwargs": {}
            }]
        logger.info("QB&TR上传限速服务未开启")
        return []

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

    def __custom_sites(self) -> List[Any]:
        custom_sites = []
        custom_sites_config = self.get_config("CustomSites")
        if custom_sites_config and custom_sites_config.get("enabled"):
            custom_sites = custom_sites_config.get("sites")
        return custom_sites

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

    def logger_info(self, cancel_limit, msg):
        if not cancel_limit:
            logger.debug(msg)

    def limit(self):
        """
        开始限速
        """
        infos = [
            (
                rule.get("downloaders"),
                rule.get("limit_sites"),
                rule.get("limit_speed"),
                rule.get("limit_sites_pause_threshold"),
                rule.get("active_time_range_site_config"),
            )
            for rule in self._normalize_rules(self._rules)
        ]
        # 原插件固定六条规则时的 infos 构造方式保留如下，动态版只替换这一数据入口：
        # infos = [
        #     (self._downloaders1, self._limit_sites1, self._limit_speed1, self._limit_sites_pause_threshold1,
        #      self._active_time_range_site_config1),
        #     (self._downloaders2, self._limit_sites2, self._limit_speed2, self._limit_sites_pause_threshold2,
        #      self._active_time_range_site_config2),
        #     (self._downloaders3, self._limit_sites3, self._limit_speed3, self._limit_sites_pause_threshold3,
        #      self._active_time_range_site_config3),
        #     (self._downloaders4, self._limit_sites4, self._limit_speed4, self._limit_sites_pause_threshold4,
        #      self._active_time_range_site_config4),
        #     (self._downloaders5, self._limit_sites5, self._limit_speed5, self._limit_sites_pause_threshold5,
        #      self._active_time_range_site_config5),
        #     (self._downloaders6, self._limit_sites6, self._limit_speed6, self._limit_sites_pause_threshold6,
        #      self._active_time_range_site_config6),
        # ]
        if any(downloaders for downloaders, _, _, _, _ in infos):
            pass
        else:
            logger.warning("未设置下载器,取消执行")
            return
        if any(limit_sites for _, limit_sites, _, _, _ in infos):
            pass
        else:
            logger.warning("未设置限速站点,取消执行")
            return
        logger.debug(f"----------开始执行限速逻辑----------")
        # 站点name:id {}
        all_site_name_id_map = {}
        for site in SiteOper().list_order_by_pri():
            all_site_name_id_map[site.name] = site.id
        for site in self.__custom_sites():
            all_site_name_id_map[site.get("name")] = site.get("id")
        all_site_names = set(all_site_name_id_map.keys())

        # 初始记录{downloader:all_sites,},限速的站点从all_sites中减去,剩余的设置一轮0速度
        downloader_site_record = {}

        # 动态版仅将原来的固定 infos 扩展为任意数量，后续执行逻辑保持原样。
        for index, info in enumerate(infos):  # 5 行限速规则
            downloaders, limit_sites, limit_speed, limit_sites_pause_threshold, active_time_range_site_config = info
            is_in_time_range = self.__is_current_time_in_range_site_config(active_time_range_site_config)
            if not downloaders or not limit_sites:
                continue
            downloader_service_infos = self.get_downloader_service_infos(downloaders)
            if not downloader_service_infos:
                continue

            for downloader_service_info in downloader_service_infos.values():
                if downloader_service_info.name not in downloader_site_record:
                    downloader_site_record[downloader_service_info.name] = all_site_names.copy()
            logger.debug(f"限速{index + 1}")
            for downloader_service_info in downloader_service_infos.values():
                # 创建一个集合，包含所有需要排除的 name
                excluded_names = {name for name, id in all_site_name_id_map.items() if id in limit_sites}
                for excluded_name in excluded_names:  # 移除这些设置了限速的站点
                    downloader_site_record[downloader_service_info.name].discard(excluded_name)
                self.limit_per_downloader(all_site_name_id_map, all_site_names, downloader_service_info,
                                          limit_sites, limit_speed, limit_sites_pause_threshold, is_in_time_range, limit_speed <= 0)
        # 给downloader_site_record中未设置限速的站点,设置不限速
        logger.debug("其余种子不限速")
        for downloader, sites in downloader_site_record.items():
            if sites:  # names
                logger.debug(f"{downloader} {','.join(sites)} 种子不限速")
                downloader_service_info = DownloaderHelper().get_service(name=downloader)
                limit_site_ids = [all_site_name_id_map[site_name] for site_name in sites]
                self.limit_per_downloader(all_site_name_id_map, all_site_names, downloader_service_info, limit_site_ids, 0, 0, False, True)

        # 保存缓存
        # self.__update_config()
        logger.debug(f"限速执行完成")

    def limit_per_downloader(self, all_site_name_id_map, all_site_names, downloader_service_info,
                             limit_site_ids, limit_speed, limit_sites_pause_threshold, is_in_time_range, cancel_limit):
        downloader = downloader_service_info.name
        downloader_obj = downloader_service_info.instance
        dl_type = downloader_service_info.type
        # 设置限速
        to_limit_torrent_hashs = []
        to_cancel_limit_torrent_hashs = []
        # cancel_limit_torrent_hashs_other = []
        # 限速后仍然活动种子处理↓
        # 限速100K中,且活动的种子,本次要暂停
        to_pausedUP_hashs_cur = []
        # 已经暂停,暂停时间超过x分钟的种子,本次要重新开始
        to_cancel_pausedUP_hashs_cur = []
        # 当前时间戳
        current_time = time.time()
        _limit_sites_pause_threshold_s = limit_sites_pause_threshold * 60
        # 限速后仍然活动种子处理↑
        nolabel_set = {label.strip() for label in self._nolabels.split(',') if label.strip()}
        if dl_type == "qbittorrent":
            self.logger_info(cancel_limit, f"{downloader} 开始设置限速")
            all_torrents, _ = downloader_obj.get_torrents()
            for torrent in all_torrents:
                state = torrent.state  # str
                if torrent.state_enum.is_downloading:  # 包含多种下载态
                    logger.info(f"{downloader} {torrent.name} 下载中，跳过 ...")
                    continue
                # 当前种子 tags list
                current_torrent_tag_list = [element.strip() for element in torrent.tags.split(',')]
                torrent_nolabel = nolabel_set & set(current_torrent_tag_list)
                if torrent_nolabel:
                    logger.debug(f"{downloader} {torrent.name} 含有不限速标签{torrent_nolabel}，跳过 ...")
                    continue
                # qb 补充站点标签,交集第一个就是站点标签
                intersection = all_site_names.intersection(current_torrent_tag_list)
                if intersection:
                    site_name = list(intersection)[0]
                    site_id = all_site_name_id_map[site_name] or -1
                else:
                    site_id = -1
                    self.logger_info(cancel_limit, f"{downloader} {torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                if site_id in limit_site_ids:
                    if cancel_limit:
                        to_cancel_limit_torrent_hashs.append(torrent.hash)
                        if state in ['pausedUP', 'stoppedUP'] and torrent.total_size == torrent.completed and ('暂停' not in current_torrent_tag_list):
                            to_cancel_pausedUP_hashs_cur.append(torrent.hash)
                    elif is_in_time_range:
                        to_limit_torrent_hashs.append(torrent.hash)
                        # 限速后还活动就暂停,不限速的除外
                        if limit_sites_pause_threshold > 0 and limit_speed > 0:
                            if "uploading" == state:
                                to_pausedUP_hashs_cur.append(torrent.hash)
                            elif state in ["pausedUP", "stoppedUP"] and torrent.total_size == torrent.completed and ('暂停' not in current_torrent_tag_list):
                                pausedUPTime = self.to_pausedUP_hashs.get(torrent.hash, 0)
                                if (current_time - pausedUPTime) > _limit_sites_pause_threshold_s:
                                    to_cancel_pausedUP_hashs_cur.append(torrent.hash)
                    else:  # 非限速区间,解除限速,解除暂停
                        to_cancel_limit_torrent_hashs.append(torrent.hash)
                        if state in ["pausedUP", "stoppedUP"] and torrent.total_size == torrent.completed and ('暂停' not in current_torrent_tag_list):
                            to_cancel_pausedUP_hashs_cur.append(torrent.hash)
                # else:  # 配置站点外,解除限速,会将前面已经设置的也误解除,如何办? 初始记录{downloader:all_sites,},限速的站点从all_sites中减去,剩余的设置一轮0速度
                #     cancel_limit_torrent_hashs_other.append(torrent.hash)
            if to_limit_torrent_hashs:
                downloader_obj.qbc.torrents_set_upload_limit(1024 * limit_speed, to_limit_torrent_hashs)
                if limit_speed > 0:
                    self.logger_info(cancel_limit, f"{downloader} 限速{limit_speed}K种子个数: {len(to_limit_torrent_hashs)}")
                else:
                    self.logger_info(cancel_limit, f"{downloader} 不限速种子个数: {len(to_limit_torrent_hashs)}")

            # 其他的都是不限速的,塞到一个list吧
            cancel_limit_list_all = to_cancel_limit_torrent_hashs #+ cancel_limit_torrent_hashs_other
            if cancel_limit_list_all:
                reason = "不限速" if cancel_limit else "非限速区间,解除限速"
                self.logger_info(cancel_limit, f"{downloader} {reason}种子个数{len(cancel_limit_list_all)}")
                downloader_obj.qbc.torrents_set_upload_limit(0, cancel_limit_list_all)

            # 限速中仍然有上传就暂停
            if to_pausedUP_hashs_cur:
                # 先强制重新汇报再暂停
                downloader_obj.qbc.torrents_reannounce(torrent_hashes=to_pausedUP_hashs_cur)
                downloader_obj.stop_torrents(to_pausedUP_hashs_cur)
                downloader_obj.set_torrents_tag(to_pausedUP_hashs_cur, ["P"])
                self.logger_info(cancel_limit, f"{downloader} 限速后仍活动,暂停种子个数: {len(to_pausedUP_hashs_cur)}")
                for t_hash in to_pausedUP_hashs_cur:
                    self.to_pausedUP_hashs[t_hash] = current_time
            if to_cancel_pausedUP_hashs_cur:
                downloader_obj.start_torrents(to_cancel_pausedUP_hashs_cur)
                downloader_obj.remove_torrents_tag(to_cancel_pausedUP_hashs_cur, ["P"])
                if not cancel_limit:
                    temp_reason = "到达暂停时间" if is_in_time_range else "非限速区间"
                    self.logger_info(cancel_limit, f"{downloader} {temp_reason},重新开始种子个数: {len(to_cancel_pausedUP_hashs_cur)}")
                for t_hash in to_cancel_pausedUP_hashs_cur:
                    if t_hash in self.to_pausedUP_hashs:
                        del self.to_pausedUP_hashs[t_hash]

        elif dl_type == "transmission":
            self.logger_info(cancel_limit, f"{downloader} 开始设置限速")
            _trarg = ["id", "name", "labels", "hashString", "status", "rateUpload"]
            tr_client = downloader_obj.trc
            all_torrents = tr_client.get_torrents(arguments=_trarg)
            # all_torrents, _ = downloader_obj.get_torrents()
            for torrent in all_torrents:
                # 当前种子 tags list
                current_torrent_tag_list = [element.strip() for element in torrent.labels]
                torrent_nolabel = nolabel_set & set(current_torrent_tag_list)
                if torrent_nolabel:
                    logger.debug(f"{downloader} {torrent.name} 含有不限速标签{torrent_nolabel}，跳过 ...")
                    continue
                # qb 补充站点标签,交集第一个就是站点标签
                intersection = all_site_names.intersection(current_torrent_tag_list)
                if intersection:
                    site_name = list(intersection)[0]
                    site_id = all_site_name_id_map[site_name] or -1
                else:
                    site_id = -1
                    self.logger_info(cancel_limit, f"{torrent.name} 没有添加站点标签{current_torrent_tag_list}")
                if site_id in limit_site_ids:
                    state = torrent.status  # Enum
                    if cancel_limit:
                        to_cancel_limit_torrent_hashs.append(torrent.hashString)
                        if state.stopped and ('暂停' not in current_torrent_tag_list):
                            to_cancel_pausedUP_hashs_cur.append(torrent.hashString)
                    elif is_in_time_range:
                        to_limit_torrent_hashs.append(torrent.hashString)
                        # 限速后还活动就暂停
                        if limit_sites_pause_threshold > 0:
                            if state.seeding and torrent.rate_upload > 0:
                                to_pausedUP_hashs_cur.append(torrent.hashString)
                            elif state.stopped and ('暂停' not in current_torrent_tag_list):
                                pausedUPTime = self.to_pausedUP_hashs.get(torrent.hashString, 0)
                                if (current_time - pausedUPTime) > _limit_sites_pause_threshold_s:
                                    to_cancel_pausedUP_hashs_cur.append(torrent.hashString)
                    else:
                        to_cancel_limit_torrent_hashs.append(torrent.hashString)
                        if state.stopped and ('暂停' not in current_torrent_tag_list):
                            to_cancel_pausedUP_hashs_cur.append(torrent.hashString)
                # else:
                #     cancel_limit_torrent_hashs_other.append(torrent.hashString)
            if to_limit_torrent_hashs:
                tr_client.change_torrent(ids=to_limit_torrent_hashs, upload_limit=limit_speed,
                                         upload_limited=True)
                if limit_speed > 0:
                    self.logger_info(cancel_limit, f"{downloader} 限速{limit_speed}K种子个数: {len(to_limit_torrent_hashs)}")
                else:
                    self.logger_info(cancel_limit, f"{downloader} 不限速种子个数: {len(to_limit_torrent_hashs)}")
            # 其他的都是不限速的,塞到一个list吧
            cancel_limit_list_all = to_cancel_limit_torrent_hashs #+ cancel_limit_torrent_hashs_other
            if cancel_limit_list_all:
                reason = "不限速" if cancel_limit else "非限速区间,解除限速"
                self.logger_info(cancel_limit, f"{downloader} {reason}种子个数{len(cancel_limit_list_all)}")
                tr_client.change_torrent(ids=cancel_limit_list_all, upload_limit=0, upload_limited=False)

            # 限速中仍然有上传就暂停
            if to_pausedUP_hashs_cur:
                downloader_obj.stop_torrents(to_pausedUP_hashs_cur)
                # downloader_obj.set_torrents_tag(to_pausedUP_hashs_cur, ["P"])
                self.logger_info(cancel_limit, f"{downloader} 限速后仍活动,暂停种子个数: {len(to_pausedUP_hashs_cur)}")
                for t_hash in to_pausedUP_hashs_cur:
                    self.to_pausedUP_hashs[t_hash] = current_time
            if to_cancel_pausedUP_hashs_cur:
                downloader_obj.start_torrents(to_cancel_pausedUP_hashs_cur)
                # downloader_obj.remove_torrents_tag(to_cancel_pausedUP_hashs_cur, ["P"])
                if not cancel_limit:
                    temp_reason = "到达暂停时间" if is_in_time_range else "非限速区间"
                    self.logger_info(cancel_limit, f"{downloader} {temp_reason},重新开始种子个数: {len(to_cancel_pausedUP_hashs_cur)}")
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
