import os
from datetime import datetime, timedelta
import re
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase


class ZYTCleanLogs(_PluginBase):
    # 插件名称
    plugin_name = "插件日志批量清理"
    # 插件描述
    plugin_desc = "定时清理所有插件产生的日志"
    # 插件图标
    plugin_icon = "clean.png"
    # 插件版本
    plugin_version = "1.1.3"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytcleanlogs_"
    # 加载顺序
    plugin_order = 99
    # 可使用的用户级别
    auth_level = 1

    _enable = False
    _onlyonce = False
    _cron = '30 3 * * *'
    _selected_ids: List[str] = []
    _rows = 300

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enable = config.get('enable', False)
            self._rows = int(config.get('rows', 300))
            self._onlyonce = config.get('onlyonce', False)
            self._cron = config.get('cron', '30 3 * * *')

        # 定时服务
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._onlyonce = False
            self.update_config({
                "onlyonce": self._onlyonce,
                "rows": self._rows,
                "enable": self._enable,
                "cron": self._cron,
            })
            self._scheduler.add_job(func=self._task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=2),
                                    name="插件日志批量清理")
        if self._enable and self._cron:
            try:
                self._scheduler.add_job(func=self._task,
                                        trigger=CronTrigger.from_crontab(self._cron),
                                        name="插件日志批量清理")
            except Exception as err:
                logger.error(f"插件日志批量清理, 定时任务配置错误：{str(err)}")

        # 启动任务
        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def _task(self):
        folder_path = settings.LOG_PATH / Path("plugins")
        # 遍历文件夹下的文件
        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)
            if os.path.isfile(file_path):
                pattern = re.compile(r'\.log\.\d+$')  # 编译一个正则表达式，用于匹配以.log.数字结尾的字符串，\d+ 表示一个或多个数字，$ 表示结尾
                if pattern.search(file_path):  # 使用 search 方法在字符串中查找是否存在匹配的部分
                    os.remove(file_path)  # 使用 os 模块的 remove 方法删除文件
                    logger.info(f"已清理旧文件 {file_name} 全部日志")
                elif file_path.endswith('.log'):
                    # zytcleanlogs自身留 100 行
                    keep_rows = 100 if(file_path.endswith('zytcleanlogs.log')) else self._rows

                    with open(file_path, 'r', encoding='utf-8') as file:
                        lines = file.readlines()

                    if keep_rows == 0:
                        top_lines = []
                    else:
                        top_lines = lines[-min(keep_rows, len(lines)):]

                    with open(file_path, 'w', encoding='utf-8') as file:
                        file.writelines(top_lines)

                    if (len(lines) - keep_rows) > 0:
                        logger.info(f"已清理 {file_name} {len(lines) - keep_rows} 行日志")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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
                                                   'model': 'enable',
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
                                                   'model': 'onlyonce',
                                                   'label': '立即运行一次',
                                               }
                                           }
                                       ]
                                   }, {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VCronField',
                                               'props': {
                                                   'model': 'cron',
                                                   'label': '定时删除日志',
                                                   'placeholder': '5位cron表达式'
                                               }
                                           }
                                       ]
                                   }, {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 3
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextField',
                                               'props': {
                                                   'model': 'rows',
                                                   'label': '保留Top行数',
                                                   'placeholder': '300'
                                               }
                                           }
                                       ]
                                   }

                               ]
                           }
                       ]
                   }
               ], {
                   "enable": self._enable,
                   "onlyonce": self._onlyonce,
                   "rows": self._rows,
                   "cron": self._cron,
                   "selected_ids": [],
               }

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        pass