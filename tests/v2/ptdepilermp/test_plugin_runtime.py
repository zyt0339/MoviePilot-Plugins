"""使用完全 mock 的 MoviePilot 宿主验证刷新与生命周期。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = ROOT / "plugins.v2" / "ptdepilermp"


class FakeScheduler:
    """不启动线程的 BackgroundScheduler 替身。"""

    def __init__(self, **kwargs):
        self.jobs = []
        self.running = False

    def start(self):
        self.running = True

    def add_job(self, function, *args, **kwargs):
        self.jobs.append((function, kwargs))

    def remove_all_jobs(self):
        self.jobs.clear()

    def shutdown(self, wait=False):
        self.running = False


class FakeCronTrigger:
    """只验证五段格式的 CronTrigger 替身。"""

    @staticmethod
    def from_crontab(expression):
        if len(str(expression).split()) != 5:
            raise ValueError("cron 必须为五段")
        return ("cron", expression)


class FakeEvent:
    """MoviePilot 广播事件的最小替身。"""

    def __init__(self, event_data=None):
        self.event_data = event_data or {}


class FakeEventManager:
    """保留事件装饰器语义，不启动真实事件线程。"""

    @staticmethod
    def register(event_type):
        return lambda function: function


class FakeEventType:
    SiteRefreshed = "site.refreshed"


class FakePluginBase:
    """记录配置更新的插件基类替身。"""

    data_store = {}

    def __init__(self):
        self.saved_config = None

    def update_config(self, config):
        self.saved_config = config

    def save_data(self, key, value):
        self.data_store[key] = value

    def get_data(self, key=None):
        return self.data_store.get(key)

    def del_data(self, key):
        return self.data_store.pop(key, None)


class FakeSiteOper:
    """提供启用站点和最新快照。"""

    sites = []
    latest = []

    def list_active(self):
        return self.sites

    def get_userdata_latest(self):
        return self.latest


def _module(name, **values):
    module = types.ModuleType(name)
    for key, value in values.items():
        setattr(module, key, value)
    return module


def load_plugin():
    """在 fake 宿主模块下加载插件。"""
    fake_modules = {
        "pytz": _module("pytz", timezone=lambda name: __import__("datetime").timezone.utc),
        "apscheduler": _module("apscheduler"),
        "apscheduler.schedulers": _module("apscheduler.schedulers"),
        "apscheduler.schedulers.background": _module(
            "apscheduler.schedulers.background", BackgroundScheduler=FakeScheduler
        ),
        "apscheduler.triggers": _module("apscheduler.triggers"),
        "apscheduler.triggers.cron": _module(
            "apscheduler.triggers.cron", CronTrigger=FakeCronTrigger
        ),
        "app": _module("app"),
        "app.core": _module("app.core"),
        "app.core.config": _module("app.core.config", settings=types.SimpleNamespace(TZ="UTC")),
        "app.core.event": _module("app.core.event", Event=FakeEvent, eventmanager=FakeEventManager()),
        "app.db": _module("app.db"),
        "app.db.site_oper": _module("app.db.site_oper", SiteOper=FakeSiteOper),
        "app.log": _module("app.log", logger=types.SimpleNamespace(info=lambda *a: None, error=lambda *a: None, warning=lambda *a: None, debug=lambda *a: None)),
        "app.plugins": _module("app.plugins", _PluginBase=FakePluginBase),
        "app.schemas": _module("app.schemas"),
        "app.schemas.types": _module("app.schemas.types", EventType=FakeEventType),
        "app.utils": _module("app.utils"),
        "app.utils.string": _module("app.utils.string", StringUtils=types.SimpleNamespace(str_filesize=lambda value: f"{value}B")),
    }
    package = _module("app.plugins.ptdepilermp")
    package.__path__ = [str(PLUGIN_DIR)]
    fake_modules["app.plugins.ptdepilermp"] = package
    with patch.dict(sys.modules, fake_modules):
        rule_spec = importlib.util.spec_from_file_location(
            "app.plugins.ptdepilermp.rules", PLUGIN_DIR / "rules.py"
        )
        rule_module = importlib.util.module_from_spec(rule_spec)
        sys.modules[rule_spec.name] = rule_module
        rule_spec.loader.exec_module(rule_module)
        plugin_spec = importlib.util.spec_from_file_location("ptdepilermp_runtime", PLUGIN_DIR / "__init__.py")
        plugin_module = importlib.util.module_from_spec(plugin_spec)
        sys.modules[plugin_spec.name] = plugin_module
        plugin_spec.loader.exec_module(plugin_module)
    return plugin_module


class PluginRuntimeTest(unittest.TestCase):
    """验证配置、重算、页面和生命周期。"""

    @classmethod
    def setUpClass(cls):
        cls.module = load_plugin()

    def setUp(self):
        FakePluginBase.data_store = {}
        FakeSiteOper.sites = [types.SimpleNamespace(id=7, name="站点A", domain="x")]
        FakeSiteOper.latest = []
        self.plugin = self.module.PTDepilerMp()

    @staticmethod
    def _embedded_panel(table_row):
        """读取表格站点行中嵌入的等级规则面板。"""
        table_cell = table_row["content"][0]
        expansion_panels = table_cell["content"][0]
        return expansion_panels["content"][0]

    @staticmethod
    def _page_table(page):
        """读取使用原生横向滚动的主表。"""
        return page[0]["content"][1]

    @classmethod
    def _embedded_row_cells(cls, table_row):
        """读取嵌入面板中的八列表格数据。"""
        panel = cls._embedded_panel(table_row)
        return panel["content"][0]["content"]

    def test_only_page_filter_api_and_stop_is_idempotent(self):
        self.plugin.init_plugin({})
        apis = self.plugin.get_api()
        self.assertEqual(len(apis), 1)
        self.assertEqual(apis[0]["path"], "/page_filter")
        self.assertEqual(apis[0]["methods"], ["GET"])
        self.assertEqual(apis[0]["auth"], "bear")
        self.assertFalse(hasattr(self.plugin, "refresh_site"))
        self.assertFalse(hasattr(self.plugin, "refresh_all"))
        self.plugin.stop_service()
        self.plugin.stop_service()
        self.assertIsNone(self.plugin._scheduler)

    def test_page_filter_switches_rows_and_selected_card(self):
        self.plugin.init_plugin({})
        rows = [
            {
                "site_name": "已保号站",
                "site_url": None,
                "user": {"join_at": "2020-01-01"},
                "rule": {},
                "result": self.module.SiteLevelResult("kept", None, "user", None, None, True),
                "stale": False,
            },
            {
                "site_name": "未保号站",
                "site_url": None,
                "user": {"join_at": "2021-01-01"},
                "rule": {},
                "result": self.module.SiteLevelResult("pending", None, "user", None, None, False),
                "stale": True,
            },
        ]
        self.plugin._cached_rows = rows
        self.plugin._has_calculated = True

        page = self.plugin.get_page()
        cards = page[0]["content"][0]["content"][:4]
        self.assertEqual([card["content"][0]["content"][0]["content"][1]["text"] for card in cards], [
            "✓ 当前 · 全部 2", "已保号 1", "未保号 1", "无法判断 0",
        ])
        self.assertEqual(
            cards[2]["content"][0]["events"]["click"]["params"],
            {"status": "unretained"},
        )

        self.assertEqual(self.plugin.set_page_filter("unretained"), {
            "success": True,
            "status": "unretained",
        })
        filtered_rows = self._page_table(self.plugin.get_page())["content"][1]["content"]
        self.assertEqual(len(filtered_rows), 1)
        self.assertEqual(self._embedded_row_cells(filtered_rows[0])[0]["text"], "未保号站")
        self.assertFalse(self.plugin.set_page_filter("invalid")["success"])

        # 热重载后 API 可能仍绑定旧实例；筛选结果必须能被新页面实例读取。
        reloaded_plugin = self.module.PTDepilerMp()
        reloaded_plugin.init_plugin({})
        reloaded_plugin._cached_rows = rows
        reloaded_plugin._has_calculated = True
        self.plugin.set_page_filter("retained")
        reloaded_page = reloaded_plugin.get_page()
        reloaded_rows = self._page_table(reloaded_page)["content"][1]["content"]
        self.assertEqual(len(reloaded_rows), 1)
        self.assertEqual(self._embedded_row_cells(reloaded_rows[0])[0]["text"], "已保号站")

        # 筛选状态已被当前页面消费；模拟关闭后重新打开必须恢复“全部”。
        reopened_rows = self._page_table(reloaded_plugin.get_page())["content"][1]["content"]
        self.assertEqual(len(reopened_rows), 2)

    def test_onlyonce_recalculates_snapshot_without_site_access_and_resets_switch(self):
        with patch.object(self.module.logger, "info") as log_info:
            self.plugin.init_plugin({
                "onlyonce": True,
                "cron": "",
                "donor_sites": ["家园", " 家园 "],
            })
        self.assertEqual(self.plugin.saved_config, {
            "onlyonce": False,
            "cron": "",
            "donor_sites": ["家园"],
        })
        self.assertIsNone(self.plugin._scheduler)
        self.assertTrue(self.plugin._has_calculated)
        message = str(log_info.call_args.args[0])
        self.assertIn("保号数据刷新完成", message)
        self.assertIn("刷新时间=", message)
        self.assertIn("触发源=手动刷新", message)

    def test_blank_cron_does_not_create_scheduled_task(self):
        self.plugin.init_plugin({"cron": "   "})
        self.assertEqual(self.plugin._cron, "")
        self.assertIsNone(self.plugin._scheduler)
        form_payload = json.dumps(self.plugin.get_form(), ensure_ascii=False)
        self.assertIn("VCronField", form_payload)
        self.assertIn("cron 留空时不创建定时任务", form_payload)
        self.assertIn("黄星/捐赠者特殊保号站点", form_payload)
        self.assertIn("donor_sites", form_payload)
        self.assertNotIn("站点规则压缩包 URL", form_payload)

    def test_donor_site_selector_lists_all_active_sites(self):
        FakeSiteOper.sites = [
            types.SimpleNamespace(id=1, name="家园", domain="ignored"),
            types.SimpleNamespace(id=2, name="站点A", domain="ignored"),
        ]
        form, defaults = self.plugin.get_form()
        payload = json.dumps(form, ensure_ascii=False)
        self.assertIn('"title": "家园"', payload)
        self.assertIn('"title": "站点A"', payload)
        self.assertEqual(defaults["donor_sites"], [])

    def test_cron_job_and_dashboard_have_no_frontend_auto_refresh(self):
        self.plugin.init_plugin({"cron": "0 8 * * *"})
        self.assertTrue(self.plugin._scheduler.running)
        self.assertEqual(len(self.plugin._scheduler.jobs), 1)
        function, job_options = self.plugin._scheduler.jobs[0]
        self.assertEqual(job_options["args"], ["cron"])
        with patch.object(self.module.logger, "info") as log_info:
            function(*job_options["args"])
        self.assertIn("触发源=cron", str(log_info.call_args.args[0]))
        _, global_config, _ = self.plugin.get_dashboard()
        self.assertNotIn("refresh", global_config)

    def test_only_full_refresh_completion_event_recalculates(self):
        self.plugin.init_plugin({})
        with patch.object(self.plugin, "_recalculate") as recalculate:
            self.plugin.on_all_sites_refreshed(FakeEvent({"site_id": 7}))
            recalculate.assert_not_called()
            self.plugin.on_all_sites_refreshed(FakeEvent({"site_id": "*"}))
            recalculate.assert_called_once_with("站点全量刷新通知")

    def test_latest_snapshot_is_selected_by_name_and_full_datetime(self):
        FakeSiteOper.sites = [types.SimpleNamespace(id=7, name="PlayLet", domain="当前域名")]
        common = {
            "name": "PlayLet", "user_level": "Power User", "join_at": "2025-10-30 16:27:58",
            "download": 100, "ratio": 2, "bonus": 100, "seeding": 1, "leeching": 0,
            "seeding_size": 100, "err_msg": None,
        }
        FakeSiteOper.latest = [
            types.SimpleNamespace(
                **common, domain="历史域名", upload=100,
                updated_day="2026-03-12", updated_time="21:58:31",
            ),
            types.SimpleNamespace(
                **common, domain="当前域名", upload=200,
                updated_day="2026-08-01", updated_time="08:03:13",
            ),
        ]
        rows = self.plugin._calculate_rows()
        self.assertEqual(rows[0]["user"]["upload"], 200)
        self.assertEqual(rows[0]["user"]["updated_day"], "2026-08-01")

    def test_snapshot_becomes_stale_only_after_seven_calendar_days(self):
        with patch.object(self.module, "datetime") as mock_datetime:
            mock_datetime.now.return_value = __import__("datetime").datetime(
                2026, 8, 9, 1, 0, tzinfo=__import__("datetime").timezone.utc
            )
            mock_datetime.strptime.side_effect = __import__("datetime").datetime.strptime
            self.assertFalse(self.plugin._snapshot_is_stale({
                "updated_day": "2026-08-02", "err_msg": None,
            }))
            self.assertTrue(self.plugin._snapshot_is_stale({
                "updated_day": "2026-08-01", "err_msg": None,
            }))
            self.assertTrue(self.plugin._snapshot_is_stale({
                "updated_day": "2026-08-09", "err_msg": "刷新失败",
            }))
            self.assertTrue(self.plugin._snapshot_is_stale({
                "updated_day": "无效日期", "err_msg": None,
            }))

    def test_page_sorts_sites_by_join_time_with_missing_or_invalid_last(self):
        FakeSiteOper.sites = [
            types.SimpleNamespace(id=1, name="较晚", domain="ignored"),
            types.SimpleNamespace(id=2, name="缺失", domain="ignored"),
            types.SimpleNamespace(id=3, name="最早", domain="ignored"),
            types.SimpleNamespace(id=4, name="异常", domain="ignored"),
        ]
        FakeSiteOper.latest = [
            types.SimpleNamespace(name="较晚", join_at="2025-01-01"),
            types.SimpleNamespace(name="缺失", join_at=None),
            types.SimpleNamespace(name="最早", join_at="2020-01-01"),
            types.SimpleNamespace(name="异常", join_at="not-a-date"),
        ]
        self.plugin.init_plugin({})
        page = self.plugin.get_page()
        table_rows = self._page_table(page)["content"][1]["content"]
        self.assertEqual(
            [self._embedded_row_cells(row)[0]["text"] for row in table_rows],
            ["最早", "较晚", "缺失", "异常"],
        )

    def test_safe_site_links_and_bulk_open_groups(self):
        FakeSiteOper.sites = [
            types.SimpleNamespace(id=1, name="已保号站", url="https://kept.example/"),
            types.SimpleNamespace(id=2, name="未保号站", url="https://pending.example/"),
            types.SimpleNamespace(id=3, name="无法判断站", url="https://unknown.example/"),
            types.SimpleNamespace(id=4, name="异常地址站", url="javascript:alert(1)"),
        ]
        common = {
            "join_at": "2020-01-01", "upload": 100, "download": 50, "ratio": 2,
            "updated_day": "2026-08-01", "updated_time": "12:00:00", "err_msg": None,
        }
        FakeSiteOper.latest = [
            types.SimpleNamespace(name="已保号站", user_level="Power User", **common),
            types.SimpleNamespace(name="未保号站", user_level="User", **common),
            types.SimpleNamespace(name="无法判断站", user_level="Unknown", **common),
            types.SimpleNamespace(name="异常地址站", user_level="Unknown", **common),
        ]
        rule_template = {
            "levels": [
                {"id": 0, "name": "User"},
                {"id": 1, "name": "Power User", "isKept": True},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for site_name in ("已保号站", "未保号站"):
                rule = {"name": site_name, **rule_template}
                (path / f"{site_name}.json").write_text(json.dumps(rule), encoding="utf-8")
            self.plugin._repository = self.module.RuleRepository(path)
            self.plugin.init_plugin({})
            page = self.plugin.get_page()

        bulk_html = page[0]["content"][0]["content"][4]["content"][0]["props"]["srcdoc"]
        self.assertIn("打开未保号站点(2)", bulk_html)
        self.assertIn("打开所有站点(3)", bulk_html)
        self.assertIn(
            '"unretained": ["https://pending.example/", "https://unknown.example/"]',
            bulk_html,
        )
        self.assertNotIn("javascript:alert", bulk_html)
        self.assertIn("window.open(url, '_blank', 'noopener,noreferrer')", bulk_html)

        dashboard_html = self.plugin._dashboard_filter(
            self.plugin._cached_rows,
            self.plugin._summary(self.plugin._cached_rows),
        )["props"]["srcdoc"]
        self.assertIn(
            'class="ptd-site-link ptd-site-default" href="https://kept.example/"',
            dashboard_html,
        )
        self.assertIn(
            'class="ptd-site-link ptd-site-warning" href="https://pending.example/"',
            dashboard_html,
        )
        self.assertIn(
            'class="ptd-site-link ptd-site-grey" href="https://unknown.example/"',
            dashboard_html,
        )
        self.assertIn("text-decoration-color:currentColor", dashboard_html)
        self.assertNotIn("javascript:alert", dashboard_html)

        table_rows = self._page_table(page)["content"][1]["content"]
        site_cells = {}
        for table_row in table_rows:
            site_cell = self._embedded_row_cells(table_row)[0]
            site_name = site_cell.get("text")
            if not site_name:
                child = site_cell["content"][0]
                site_name = (
                    child["content"][0]["text"]
                    if child["component"] == "VBtn"
                    else child["text"]
                )
            site_cells[site_name] = site_cell

        kept_link = site_cells["已保号站"]["content"][0]
        self.assertEqual(kept_link["component"], "a")
        self.assertEqual(kept_link["props"]["href"], "https://kept.example/")
        self.assertEqual(kept_link["props"]["style"]["color"], "inherit")
        self.assertEqual(kept_link["props"]["style"]["font"], "inherit")
        self.assertEqual(kept_link["props"]["style"]["text-decoration-color"], "currentColor")
        pending_link = site_cells["未保号站"]["content"][0]
        self.assertEqual(pending_link["props"]["href"], "https://pending.example/")
        self.assertEqual(pending_link["props"]["color"], "warning")
        self.assertEqual(
            site_cells["无法判断站"]["content"][0]["props"]["href"],
            "https://unknown.example/",
        )
        self.assertEqual(
            site_cells["无法判断站"]["content"][0]["props"]["color"],
            "grey",
        )
        self.assertNotIn("content", site_cells["异常地址站"])
        self.assertIsNone(self.plugin._safe_site_url("https://user:pass@example.com/"))

    def test_invalid_cron_does_not_start_scheduler(self):
        with patch.object(self.module.logger, "error") as log_error:
            self.plugin.init_plugin({"cron": "invalid"})
        self.assertIsNone(self.plugin._scheduler)
        self.assertIn("cron 表达式无效", str(log_error.call_args.args[0]))

    def test_page_is_serializable_and_marks_retained_and_stale(self):
        FakeSiteOper.latest = [types.SimpleNamespace(
            name="站点A", domain="历史域名", user_level="Power User", join_at="2020-01-01",
            upload=200, download=100, ratio=2, bonus=None, seeding=None, leeching=None,
            seeding_size=None, updated_day="2000-01-01", updated_time="00:00:00", err_msg=None,
        )]
        custom = {
            "name": "站点A",
            "levels": [
                {"id": 0, "name": "User"},
                {"id": 1, "name": "Power User", "uploaded": 100, "ratio": 1.5, "isKept": True},
                {"id": 2, "name": "Elite User", "uploaded": 300, "bonus": 1000, "seedingBonus": 2000, "isKept": True},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "站点A.json").write_text(json.dumps(custom), encoding="utf-8")
            self.plugin._repository = self.module.RuleRepository(path)
            self.plugin.init_plugin({})
            page = self.plugin.get_page()
            payload = json.dumps(page, ensure_ascii=False)
            for marker in (
                "已保号", "陈旧/失败", "Elite User", "Power User（保号等级）",
                "魔力", "做种积分", "保号上传/下载/分享率",
            ):
                self.assertIn(marker, payload)
            self.assertEqual(payload.count("（保号等级）"), 1)
            for removed_marker in (
                '"text": "保号总结"', '"text": "入站时间"',
                '"text": "下一等级"', '"text": "所需时间"',
                '"text": "操作"', "refresh_site", '"domain"',
            ):
                self.assertNotIn(removed_marker, payload)

            row = self.plugin._rows()[0]
            panel = self.plugin._level_panel(row)
            self.assertEqual(panel["props"]["style"]["background"], "transparent")
            self.assertEqual(panel["props"]["style"]["border-radius"], "0")
            panel_title = panel["content"][0]
            self.assertEqual(panel_title["content"][0]["text"], "站点A")
            self.assertEqual(panel_title["content"][0]["props"]["class"], "text-success")
            panel_content = panel["content"][1]["content"]
            current_data = panel_content[0]
            current_text = "".join(item["text"] for item in current_data["content"])
            self.assertIn("当前：上传 200.0B；下载 100.0B", current_text)
            self.assertIn("分享率 2.00；魔力 数据不足", current_text)

            level_items = panel_content[1]["content"]
            self.assertEqual(level_items[0]["props"]["prepend-icon"], "mdi-check-circle-outline")
            self.assertEqual(level_items[0]["props"]["base-color"], "info")
            self.assertEqual(level_items[1]["props"]["prepend-icon"], "mdi-check-circle-outline")
            self.assertEqual(level_items[1]["props"]["base-color"], "success")
            self.assertEqual(level_items[2]["props"]["prepend-icon"], "mdi-circle-outline")
            self.assertNotIn("base-color", level_items[2]["props"])
            self.assertEqual(
                "".join(item["text"] for item in level_items[1]["content"][0]["content"]),
                "上传 100.0B；下载 无要求；注册时长 无；分享率 1.50",
            )

            table = self._page_table(page)
            self.assertEqual(table["component"], "VTable")
            header_row = table["content"][0]["content"][0]
            header_cell = header_row["content"][0]
            header_grid = header_cell["content"][0]
            header_cells = header_grid["content"]
            self.assertEqual(
                [cell["text"] for cell in header_cells],
                [
                    "站点", "状态", "当前等级", "保号等级",
                    "上传/下载/分享率", "保号上传/下载/分享率", "总结", "数据时间",
                ],
            )
            table_row = table["content"][1]["content"][0]
            expansion_panels = table_row["content"][0]["content"][0]
            self.assertEqual(expansion_panels["props"]["style"]["border-radius"], "0")
            data_cells = self._embedded_row_cells(table_row)
            self.assertEqual(len(data_cells), 8)
            self.assertEqual(data_cells[1]["component"], "VExpansionPanelTitle")
            self.assertEqual(data_cells[1]["props"]["style"]["border-radius"], "0")
            self.assertEqual(
                sum(cell["component"] == "VExpansionPanelTitle" for cell in data_cells),
                1,
            )
            self.assertEqual(data_cells[4]["text"], "200.0B / 100.0B / 2.00")
            self.assertEqual(data_cells[5]["text"], "100.0B / 无要求 / 1.50")

    def test_current_summary_marks_each_satisfied_retention_condition_green(self):
        user = {
            "upload": 200,
            "download": 100,
            "join_at": "2020-01-01",
            "ratio": 2,
            "bonus": 2000,
        }
        retained_level = {
            "uploaded": 100,
            "downloaded": 80,
            "interval": "P4W",
            "ratio": 1.5,
            "bonus": 3000,
        }
        content = self.plugin._current_level_data_content(user, retained_level)
        fields = {
            item["text"].split(" ", 1)[0]: item
            for item in content
            if item.get("text", "").startswith(("上传 ", "下载 ", "注册时长 ", "分享率 ", "魔力 "))
        }
        for field in ("上传", "下载", "注册时长", "分享率"):
            self.assertEqual(fields[field]["props"]["class"], "text-success")
        self.assertEqual(fields["魔力"]["props"]["class"], "text-warning")

    def test_donor_special_retention_overrides_level_and_renders_green_current_data(self):
        FakeSiteOper.sites = [types.SimpleNamespace(id=7, name="DonorSite", domain="ignored")]
        FakeSiteOper.latest = [types.SimpleNamespace(
            name="DonorSite", domain="another-domain", user_level="User", join_at="2026-01-01",
            upload=10, download=20, ratio=0.5, bonus=30, seeding=None, leeching=None,
            seeding_size=None, updated_day="2026-08-01", updated_time="12:00:00", err_msg=None,
        )]
        custom = {
            "name": "DonorSite",
            "levels": [
                {"id": 0, "name": "User"},
                {"id": 1, "name": "Nexus Master", "downloaded": "10TB", "isKept": True},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "DonorSite.json").write_text(json.dumps(custom), encoding="utf-8")
            self.plugin._repository = self.module.RuleRepository(path)
            self.plugin.init_plugin({"donor_sites": ["donorsite"]})
            row = self.plugin._rows()[0]
            self.assertTrue(row["result"].retained)
            self.assertEqual(row["result"].retention_type, "donor")
            retained_level = self.plugin._retention_level(row)
            self.assertEqual(retained_level["name"], "黄星（特殊保号）")
            requirement = self.module.evaluate_requirement(row["user"], retained_level)
            self.assertEqual(
                self.plugin._retention_summary(row["result"], retained_level, requirement),
                "黄星保号，已保号",
            )

            payload = json.dumps(self.plugin.get_page(), ensure_ascii=False)
            self.assertIn("黄星（特殊保号）", payload)
            self.assertIn("黄星保号，已保号", payload)
            self.assertIn("无要求 / 无要求", payload)

            panel = self.plugin._level_panel(row)
            panel_title = panel["content"][0]
            self.assertEqual(panel_title["content"][0]["text"], "DonorSite")
            self.assertEqual(panel_title["content"][0]["props"]["class"], "text-success")
            self.assertEqual(len(panel_title["content"]), 1)
            current_data = panel["content"][1]["content"][0]["content"]
            fields = [
                item for item in current_data
                if item.get("text", "").startswith(("上传 ", "下载 ", "注册时长 ", "分享率 ", "魔力 "))
            ]
            self.assertEqual(len(fields), 5)
            self.assertTrue(all(item["props"]["class"] == "text-success" for item in fields))
            level_payload = json.dumps(panel["content"][1]["content"][1], ensure_ascii=False)
            self.assertIn("Nexus Master（保号等级）", level_payload)

    def test_first_retention_level_is_green_even_when_not_reached(self):
        rule = {
            "name": "站点A",
            "levels": [
                {"id": 0, "name": "User"},
                {"id": 1, "name": "Power User", "uploaded": 100, "isKept": True},
            ],
        }
        user = {"user_level": "User", "upload": 0}
        result = self.plugin._repository.evaluate_site(user, "站点A", rule)
        row = {"site_name": "站点A", "user": user, "rule": rule, "result": result}
        panel = self.plugin._level_panel(row)
        self.assertEqual(panel["content"][0]["text"], "站点A")
        self.assertNotIn("content", panel["content"][0])
        level_items = panel["content"][1]["content"][1]["content"]
        retention_item = level_items[1]["props"]
        self.assertEqual(retention_item["prepend-icon"], "mdi-circle-outline")
        self.assertEqual(retention_item["base-color"], "success")

    def test_summary_cards_are_horizontal_and_centered(self):
        cards = self.plugin._summary_cards({
            "retained": 18,
            "unretained": 10,
            "unknown": 7,
            "stale": 2,
        })
        self.assertEqual(len(cards["content"]), 4)
        texts = []
        for column in cards["content"]:
            self.assertEqual(column["props"], {"cols": 6, "sm": 3, "md": 3})
            card_text = column["content"][0]["content"][0]
            self.assertEqual(card_text["component"], "VCardText")
            self.assertIn("text-center", card_text["props"]["class"])
            texts.append(card_text["text"])
        self.assertEqual(texts, ["已保号 18", "未保号 10", "无法判断 7", "陈旧/失败 2"])

        dashboard_cards = self.plugin._summary_cards({
            "all": 35,
            "retained": 18,
            "unretained": 10,
            "unknown": 7,
            "stale": 2,
        }, page_layout=True)
        self.assertEqual(dashboard_cards["props"], {"class": "flex-nowrap"})
        self.assertEqual(len(dashboard_cards["content"]), 5)
        dashboard_texts = []
        for column in dashboard_cards["content"]:
            self.assertEqual(column["props"], {
                "sm": "auto",
                "class": "flex-grow-1 flex-shrink-1 pa-1 pa-sm-3",
                "style": {"min-width": "0", "flex-basis": "0"},
            })
            card = column["content"][0]
            self.assertNotIn("events", card)
            self.assertIn("py-2 py-sm-4", card["content"][0]["props"]["class"])
            dashboard_texts.append(card["content"][0]["content"][1]["text"])
        self.assertEqual(dashboard_texts, [
            "全部 35", "已保号 18", "未保号 10", "无法判断 7", "陈旧/失败 2",
        ])

    def test_unretained_status_chip_uses_short_label(self):
        result = types.SimpleNamespace(retained=False)
        chip = self.plugin._status_chip(result)
        self.assertEqual(chip["text"], "未保号")
        self.assertEqual(chip["props"]["color"], "warning")
        self.assertEqual(chip["props"]["size"], "small")

    def test_dashboard_renders_filters_table_theme_sync_and_debug_log(self):
        self.plugin.init_plugin({})
        _, _, dashboard_elements = self.plugin.get_dashboard()
        self.assertEqual(len(dashboard_elements), 1)
        dashboard_filter = dashboard_elements[0]
        self.assertEqual(dashboard_filter["component"], "iframe")
        self.assertEqual(dashboard_filter["props"]["class"], "dashboard-grid-no-drag")
        dashboard_html = dashboard_filter["props"]["srcdoc"]
        for marker in (
            'id="ptd-dashboard-all" checked',
            'for="ptd-dashboard-retained"',
            'for="ptd-dashboard-unretained"',
            'for="ptd-dashboard-unknown"',
            'for="ptd-dashboard-stale"',
            "ptd-filter-table",
            "ptd-rule-detail",
            "展开或收起完整等级规则",
            "保号上传/下载/分享率",
            "站点A",
            "parent.getComputedStyle",
            "new parent.MutationObserver(queueThemeSync)",
        ):
            self.assertIn(marker, dashboard_html)
        form_payload = json.dumps(self.plugin.get_form(), ensure_ascii=False)
        self.assertNotIn("显示仪表盘", form_payload)
        self.assertNotIn("输出一次调查日志", form_payload)
        with patch.object(self.module.logger, "debug") as log_debug:
            self.plugin._recalculate()
        messages = "\n".join(str(call.args[0]) for call in log_debug.call_args_list)
        self.assertIn("PT站点等级监控调查", messages)
        self.assertIn("站点A", messages)
        self.assertNotIn("domain=", messages)


if __name__ == "__main__":
    unittest.main()
