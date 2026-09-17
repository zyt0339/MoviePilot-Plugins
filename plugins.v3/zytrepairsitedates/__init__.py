"""为 V3 SQLite 部署提供有备份、可回滚的历史站点数据修复。"""
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.plugins import _PluginBase
from app.sdk.config import settings
from app.sdk.database import create_backup, verify_backup
from app.sdk.logging import logger
from app.schemas.types import MessageType


class RepairStopped(RuntimeError):
    """修复被停止，当前事务必须回滚。"""


class ZYTRepairSiteDates(_PluginBase):
    """仅修复已有零上传历史记录，不插入记录或覆盖非零数据。"""

    plugin_name = "修复站点数据"
    plugin_desc = "修复站点数据为0的天数,用前一天数据填充"
    plugin_icon = "database.png"
    plugin_version = "2.0.0"
    plugin_author = "zyt"
    author_url = "https://github.com/zyt0339/MoviePilot-Plugins/"
    plugin_config_prefix = "zytrepairsitedates_"
    plugin_order = 4
    auth_level = 2

    _FIELDS = ("upload", "bonus", "download", "ratio", "seeding", "leeching",
               "seeding_size", "seeding_info", "err_msg")
    _MAX_SCAN = 100000

    def __init__(self):
        """建立实例级任务锁和停止信号，不在导入时访问数据库。"""
        super().__init__()
        self._scheduler = None
        self._stop = threading.Event()
        self._run_lock = threading.Lock()
        self._enabled = False
        self._notify = False
        self._include_today = False
        self._max_rows = 1000
        self._cmd = ""
        self._config = {}

    def init_plugin(self, config: dict = None):
        """保留旧配置，待上次任务退出后再创建调度器。"""
        if not self.stop_service():
            logger.warning("上次修复仍在退出，本次不启动新任务")
            return
        self._config = dict(config or {})
        self._enabled = bool(self._config.get("enabled", False))
        self._notify = bool(self._config.get("notify", False))
        self._cmd = str(self._config.get("cmd") or "")
        self._include_today = bool(self._config.get("include_today", False))
        try:
            self._max_rows = min(5000, max(1, int(self._config.get("max_rows", 1000))))
        except (ValueError, TypeError):
            self._max_rows = 1000
        self._stop.clear()
        onlyonce = bool(self._config.get("onlyonce", False))
        cron = self._config.get("cron")
        if not (onlyonce or (self._enabled and cron)):
            return
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)
        try:
            if self._enabled and cron:
                self._scheduler.add_job(self.run, CronTrigger.from_crontab(cron, timezone=settings.TZ),
                                        id="repair", max_instances=1, coalesce=True)
            if onlyonce:
                # 先持久化一次性开关，失败时不启动任务，避免重载重复修复。
                self._config["onlyonce"] = False
                if self.update_config(self._config) is False:
                    raise RuntimeError("一次性配置保存失败")
                self._scheduler.add_job(self.run, "date", id="once", max_instances=1)
            self._scheduler.start()
        except Exception:
            self.stop_service()
            logger.error("站点数据修复调度失败，请检查周期和配置保存状态")

    def _check_stopped(self):
        """在读取、逐行更新和提交前响应停止。"""
        if self._stop.is_set():
            raise RepairStopped("修复已停止")

    def _database_path(self) -> Path:
        """按已核对的 V3 SQLite 配置定位现有库，拒绝其它数据库。"""
        if str(settings.DB_TYPE).lower() != "sqlite":
            raise RuntimeError("仅支持 SQLite 数据库，未执行任何修复")
        path = Path(settings.CONFIG_PATH) / "user.db"
        if not path.is_file():
            raise RuntimeError("宿主数据库不存在，未创建新库")
        return path.resolve()

    def _connect(self, path: Path):
        """只打开已有数据库，等待锁最多五秒，不改变宿主日志模式。"""
        connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                     timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _plan(self, connection, today: date):
        """按原始快照选取七天内最近的有效记录，禁止修复结果级联充当来源。"""
        self._check_stopped()
        required = {"id", "domain", "updated_day", "updated_time", *self._FIELDS}
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(siteuserdata)")}
        if not required.issubset(columns):
            raise RuntimeError("站点数据表结构不匹配，未执行修复")
        fields = ", ".join(("id", "domain", "updated_day", "updated_time", *self._FIELDS))
        rows = connection.execute(
            f"SELECT {fields} FROM siteuserdata ORDER BY updated_day, updated_time, id LIMIT ?",
            (self._MAX_SCAN + 1,)).fetchall()
        if len(rows) > self._MAX_SCAN:
            raise RuntimeError("站点历史记录过多，已停止本次修复")
        ignored = {line.strip() for line in self._cmd.splitlines() if line.strip()}
        valid_rows = []
        sources = {}
        for row in rows:
            self._check_stopped()
            if not row["domain"] or row["domain"] in ignored:
                continue
            try:
                day = date.fromisoformat(row["updated_day"])
            except (ValueError, TypeError):
                continue
            if day > today or (day == today and not self._include_today):
                continue
            valid_rows.append((row, day))
            if isinstance(row["upload"], (int, float)) and row["upload"] > 0 and not row["err_msg"]:
                # 同日多条数据取更新时间、ID 排序后的最后一条。
                sources[(row["domain"], day)] = row
        plan = []
        for row, day in valid_rows:
            if row["upload"] != 0:
                continue
            for delta in range(1, 8):
                previous = sources.get((row["domain"], day - timedelta(days=delta)))
                if previous is not None:
                    plan.append((row, previous))
                    break
        if len(plan) > self._max_rows:
            raise RuntimeError("待修复记录超过单次上限，请调整上限后重试")
        return plan

    def _repair(self, today: date) -> int:
        """备份校验后在单事务内重新计算并修复，异常时全部回滚。"""
        path = self._database_path()
        with closing(self._connect(path)) as connection:
            if not self._plan(connection, today):
                return 0
        self._check_stopped()
        # SDK 的备份含 SQLite WAL 中的已提交内容，不直接复制数据库文件。
        backup = create_backup()
        if backup.db_type != "sqlite" or backup.target != "moviepilot" or not verify_backup(backup.name).valid:
            raise RuntimeError("数据库备份校验失败，未执行修复")
        self._check_stopped()
        with closing(self._connect(path)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                # 拿到写锁后重读，避免覆盖备份期间已由宿主刷新为正常的数据。
                plan = self._plan(connection, today)
                assignments = ", ".join(f"{field} = ?" for field in self._FIELDS)
                sql = (f"UPDATE siteuserdata SET {assignments} "
                       "WHERE id = ? AND domain = ? AND updated_day = ? AND upload = 0")
                for row, source in plan:
                    self._check_stopped()
                    result = connection.execute(sql, tuple(source[key] for key in self._FIELDS)
                                                + (row["id"], row["domain"], row["updated_day"]))
                    if result.rowcount != 1:
                        raise RuntimeError("目标记录已变化，修复已回滚")
                self._check_stopped()
                connection.commit()
                return len(plan)
            except BaseException:
                connection.rollback()
                raise

    def run(self):
        """串行执行修复，仅提交成功后报告成功，不输出站点或数据库路径。"""
        if not self._run_lock.acquire(blocking=False):
            logger.info("已有站点数据修复运行，跳过重复任务")
            return
        try:
            self._check_stopped()
            count = self._repair(datetime.now().date())
            message = f"修复完成，共更新 {count} 条历史记录"
            logger.info(message)
            title = "【修复站点数据完成】"
        except RepairStopped:
            logger.info("站点数据修复已停止，未提交的修改已回滚")
            return
        except Exception:
            # 异常字符串可能包含本地路径、SQL 参数或站点标识，仅输出固定说明。
            logger.error("站点数据修复失败；未提交的修改已回滚。请检查数据库类型、备份、表结构及单次上限")
            message = "未完成修复，请检查 SQLite 配置、备份可用性、表结构及单次修复上限。"
            title = "【修复站点数据失败】"
        finally:
            self._run_lock.release()
        if self._notify and not self._stop.is_set():
            try:
                self.post_message(mtype=MessageType.SiteMessage, title=title, text=message)
            except Exception:
                logger.warning("站点数据修复结果通知发送失败")

    def get_state(self) -> bool:
        """返回插件定时修复开关。"""
        return self._enabled

    @staticmethod
    def get_command() -> list:
        """不注册远程命令。"""
        return []

    def get_api(self) -> list:
        """不公开数据库写入接口。"""
        return []

    def get_form(self) -> tuple:
        """沿用旧配置字段，并展示 SQLite、备份及修复范围限制。"""
        controls = [
            ("VSwitch", "enabled", "启用插件"),
            ("VCronField", "cron", "执行周期"),
            ("VSwitch", "notify", "开启通知"),
            ("VSwitch", "onlyonce", "立即运行一次"),
            ("VSwitch", "include_today", "同时修复当天（可能尚未完成统计）"),
            ("VTextField", "max_rows", "单次修复上限（1–5000）"),
            ("VTextarea", "cmd", "忽略站点域名（一行一条）"),
        ]
        content = [{"component": "VAlert", "props": {"type": "info", "variant": "tonal",
                   "text": "仅支持 SQLite。修复前自动创建并校验数据库备份；仅填充现有上传量为 0 的记录，取之前七天最近有效数据，默认跳过当天。"}}]
        content.extend({"component": component, "props": {"model": model, "label": label}}
                       for component, model, label in controls)
        return [{"component": "VForm", "content": content}], {
            "enabled": False, "cron": "", "notify": False, "onlyonce": False,
            "include_today": False, "max_rows": 1000, "cmd": ""}

    def get_page(self) -> list:
        """没有额外详情页。"""
        return []

    def stop_service(self) -> bool:
        """取消调度并请求事务回滚，未退出时阻止重新初始化。"""
        self._stop.set()
        self._enabled = False
        if self._scheduler:
            self._scheduler.remove_all_jobs()
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._scheduler = None
        if not self._run_lock.acquire(timeout=10):
            logger.warning("修复任务仍在退出，停止信号保持有效")
            return False
        self._run_lock.release()
        return True
