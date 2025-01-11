from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pathlib import Path
from app.core.event import eventmanager, Event
from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas.types import EventType
from app.schemas import NotificationType
import subprocess
import shlex


class TorrentMarkCmd(_PluginBase):
    # 插件名称
    plugin_name = "下载器添加标签"
    # 插件描述
    plugin_desc = "定时执行,定制开发qb_torrent_mark.py"
    # 插件图标
    plugin_icon = "clean.png"
    # 插件版本
    plugin_version = "1.0.3"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "torrentmarkcmd_"
    # 加载顺序
    plugin_order = 97
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _onlyonce = False
    _notify = False
    _cmd = None

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._cmd = config.get("cmd")

            # 加载模块
        if self._enabled:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._cron:
                try:
                    self._scheduler.add_job(
                        func=self.run,
                        trigger=CronTrigger.from_crontab(self._cron),
                        name="执行命令行",
                    )
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"执行命令行服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                             + timedelta(seconds=3),
                    name="执行命令行",
                )
                # 关闭一次性开关
                self._onlyonce = False
                self.update_config(
                    {
                        "onlyonce": False,
                        "cron": self._cron,
                        "enabled": self._enabled,
                        "notify": self._notify,
                        "cmd": self._cmd,
                    }
                )

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    @eventmanager.register(EventType.PluginAction)
    def run(self, event: Event = None):
        success = True
        if event:
            event_data = event.event_data
            if not event_data or event_data.get("action") != "torrentmarkcmd":
                return
        # 清理旧日志
        try:
            log_path = settings.LOG_PATH / Path("plugins") / "torrentmarkcmd.log"
            if log_path.exists():
                file = open(log_path, 'r+', encoding='utf-8')
                file.truncate(0)
                file.close()
        except Exception as e:
            logger.error(f"清理旧日志出错: {e}")
        try:
            for cmd in self._cmd.split("\n"):
                logger.info(f"执行命令行: {cmd}")
                cmd_list = shlex.split(cmd)
                result = subprocess.run(
                    cmd_list, capture_output=True, text=True, check=True
                )
                logger.info(result.stdout)
        except subprocess.CalledProcessError as e:
            success = False
            logger.error(f"执行命令行出错: {e}")

        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【下载器添加标签成功】", text="请前往下载器查看")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【下载器添加标签失败】")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        :return: 命令关键字、事件、描述、附带数据
        """
        return [
            {
                "cmd": "/torrentmarkcmd",
                "event": EventType.PluginAction,
                "desc": "下载器添加标签",
                "category": "",
                "data": {"action": "torrentmarkcmd"},
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        pass

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
                                       "component": "VCol",
                                       "props": {
                                           "cols": 12,
                                       },
                                       "content": [
                                           {
                                               "component": "VTextField",
                                               "props": {"model": "cron", "label": "执行周期"},
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
                                                   "model": "cmd",
                                                   "rows": "2",
                                                   "label": "命令行",
                                                   "placeholder": "命令行，一行一条",
                                               },
                                           }
                                       ],
                                   }
                               ],
                           },
                       ],
                   }
               ], {"enabled": False, "request_method": "POST", "webhook_url": ""}

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