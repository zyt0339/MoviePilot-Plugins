from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType
import subprocess
import sqlite3


class ZYTRepairSiteDates(_PluginBase):
    # 插件名称
    plugin_name = "修复站点数据"
    # 插件描述
    plugin_desc = "修复站点数据为0的天数,用前一天数据填充"
    # 插件图标
    plugin_icon = "database.png"
    # 插件版本
    plugin_version = "1.0.3"
    # 插件作者
    plugin_author = "zyt"
    # 作者主页
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    # 插件配置项ID前缀
    plugin_config_prefix = "zytrepairsitedates_"
    # 加载顺序
    plugin_order = 4
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
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(
                        func=self.run,
                        trigger=CronTrigger.from_crontab(self._cron),
                        name="修复站点数据",
                    )
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"修复站点数据服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.run,
                    trigger="date",
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ))
                             + timedelta(seconds=3),
                    name="修复站点数据",
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

    def connect_to_database(self, db_path):
        try:
            conn = sqlite3.connect(db_path)
            return conn
        except sqlite3.Error as e:
            print(f"数据库连接出错: {e}")
            return None

    def get_upload_zero_rows(self, conn):
        cursor = conn.cursor()
        query = "SELECT id, domain, updated_day FROM siteuserdata WHERE upload = 0"
        cursor.execute(query)
        rows = cursor.fetchall()
        return rows

    def get_previous_day_row(self, conn, domain, current_date):
        current_date_obj = datetime.strptime(current_date, '%Y-%m-%d')
        for _ in range(3):
            current_date_obj -= timedelta(days=1)
            previous_date = current_date_obj.strftime('%Y-%m-%d')
            cursor = conn.cursor()
            query = "SELECT upload, bonus, download, ratio, seeding, leeching, seeding_size, seeding_info, err_msg " \
                    "FROM siteuserdata WHERE domain =? AND updated_day =?"
            cursor.execute(query, (domain, previous_date))
            row = cursor.fetchone()
            if row and row[0] != 0:
                return row
            else:
                logger.info('     未查询到前一日数据, 继续查询更前一日')
        return None

    def update_rows(self, conn, rows, ignore_domains):
        cursor = conn.cursor()
        for row in rows:
            row_id = row[0]
            domain = row[1]
            if domain in ignore_domains:
                continue
            current_date = row[2]
            logger.info(f'{domain} {current_date} upload = 0, 开始获取前一日数据覆盖到本天')

            prev_row = self.get_previous_day_row(conn, domain, current_date)
            if prev_row:
                logger.info('     查询到前一(迭代)日数据, 执行覆盖成功')
                update_query = "UPDATE siteuserdata SET upload =?, bonus =?, download =?, ratio =?, seeding =?, " \
                               "leeching =?, seeding_size =?, seeding_info =?, err_msg =? WHERE id =?"
                cursor.execute(update_query, (*prev_row, row_id))
            else:
                logger.info('     未查询到前一(迭代)日数据, 取消覆盖')
        conn.commit()

    def run(self):
        msg = ""
        success = True
        try:
            ignore_domains = set()
            for cmd in self._cmd.split("\n"):
                ignore_domains.add(cmd.strip())
            db_path = "/config/user.db"  # 请替换为实际的数据库文件路径
            conn = self.connect_to_database(db_path)
            if conn:
                upload_zero_rows = self.get_upload_zero_rows(conn)
                self.update_rows(conn, upload_zero_rows, ignore_domains)
                conn.close()
                logger.info('SUCCESS')
        except subprocess.CalledProcessError as e:
            success = False
            logger.error(f"修复站点数据出错: {e}")
            msg = f"{e}"

        # 发送通知
        if self._notify:
            if success:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【修复站点数据成功】")
            else:
                self.post_message(
                    mtype=NotificationType.SiteMessage, title=f"【修复站点数据出错】", text=msg
                )

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

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
                                       "props": {"cols": 12, "md": 3},
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
                                       "props": {"cols": 12, "md": 3},
                                       "content": [
                                           {
                                               "component": "VCronField",
                                               "props": {"model": "cron", "label": "执行周期"},
                                           }
                                       ],
                                   },
                                   {
                                       "component": "VCol",
                                       "props": {"cols": 12, "md": 3},
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
                                       "props": {"cols": 12, "md": 3},
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
                                               "component": "VTextarea",
                                               "props": {
                                                   "model": "cmd",
                                                   "rows": "2",
                                                   "label": "忽略站点",
                                                   "placeholder": "配置domain，一行一条,可从日志查看",
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
