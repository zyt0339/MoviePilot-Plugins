from __future__ import annotations

import importlib.util
import ast
import inspect
import json
import sys
import textwrap
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
PLUGIN_DIR = ROOT / "plugins.v2" / "ptdownloaderlimit"
ORIGINAL_PLUGIN = ROOT / "plugins.v2" / "zytlimit" / "__init__.py"


def method_source(path, method_name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    plugin_class = next(item for item in tree.body if isinstance(item, ast.ClassDef))
    method = next(
        item
        for item in plugin_class.body
        if isinstance(item, ast.FunctionDef) and item.name == method_name
    )
    return textwrap.dedent(ast.get_source_segment(source, method))


class FakeScheduler:
    def __init__(self, **kwargs):
        self.running = False
        self.jobs = []

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)

    def start(self):
        self.running = True

    def print_jobs(self):
        pass

    def remove_all_jobs(self):
        self.jobs.clear()

    def shutdown(self, wait=False):
        self.running = False


class FakeCronTrigger:
    @staticmethod
    def from_crontab(value):
        if len(str(value).split()) != 5:
            raise ValueError("invalid cron")
        return ("cron", value)


class FakeEventManager:
    @staticmethod
    def register(event_type):
        return lambda function: function


class FakeEvent:
    def __init__(self, event_data=None):
        self.event_data = event_data or {}


class FakeEventType:
    PluginAction = "plugin.action"


class FakeNotificationType:
    SiteMessage = "site.message"


class FakeServiceInfo:
    def __init__(self, name, instance, service_type):
        self.name = name
        self.instance = instance
        self.type = service_type


class FakeResponse:
    def __init__(self, success, data=None, message=None):
        self.success = success
        self.data = data
        self.message = message


class FakePluginBase:
    def __init__(self):
        self.saved_config = None
        self.messages = []

    def update_config(self, value):
        self.saved_config = value

    def get_config(self, plugin_id=None):
        return None

    def post_message(self, **kwargs):
        self.messages.append(kwargs)


class FakeSiteOper:
    sites = []

    def list_order_by_pri(self):
        return self.sites


class FakeDownloaderHelper:
    configs = {}
    services = {}

    def get_configs(self):
        return self.configs

    def get_services(self, type_filter=None, name_filters=None):
        names = set(name_filters or self.services)
        return {name: service for name, service in self.services.items() if name in names}

    def get_service(self, name):
        return self.services.get(name)


def module(name, **values):
    result = types.ModuleType(name)
    for key, value in values.items():
        setattr(result, key, value)
    return result


def load_plugin(plugin_file=None, module_name="ptdownloaderlimit_test_module"):
    schemas_module = module(
        "app.schemas",
        Response=FakeResponse,
        NotificationType=FakeNotificationType,
        ServiceInfo=FakeServiceInfo,
    )
    fake_modules = {
        "pytz": module("pytz", timezone=lambda value: __import__("datetime").timezone.utc),
        "apscheduler": module("apscheduler"),
        "apscheduler.schedulers": module("apscheduler.schedulers"),
        "apscheduler.schedulers.background": module(
            "apscheduler.schedulers.background", BackgroundScheduler=FakeScheduler
        ),
        "apscheduler.triggers": module("apscheduler.triggers"),
        "apscheduler.triggers.cron": module(
            "apscheduler.triggers.cron", CronTrigger=FakeCronTrigger
        ),
        "app": module("app", schemas=schemas_module),
        "app.core": module("app.core"),
        "app.core.config": module("app.core.config", settings=types.SimpleNamespace(TZ="UTC")),
        "app.core.event": module(
            "app.core.event", Event=FakeEvent, eventmanager=FakeEventManager()
        ),
        "app.db": module("app.db"),
        "app.db.site_oper": module("app.db.site_oper", SiteOper=FakeSiteOper),
        "app.helper": module("app.helper"),
        "app.helper.downloader": module(
            "app.helper.downloader", DownloaderHelper=FakeDownloaderHelper
        ),
        "app.log": module(
            "app.log",
            logger=types.SimpleNamespace(
                info=lambda *args: None,
                debug=lambda *args: None,
                warning=lambda *args: None,
                error=lambda *args: None,
                exception=lambda *args: None,
            ),
        ),
        "app.plugins": module("app.plugins", _PluginBase=FakePluginBase),
        "app.schemas": schemas_module,
        "app.schemas.types": module("app.schemas.types", EventType=FakeEventType),
    }
    with patch.dict(sys.modules, fake_modules):
        spec = importlib.util.spec_from_file_location(
            module_name, plugin_file or PLUGIN_DIR / "__init__.py"
        )
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = loaded
        spec.loader.exec_module(loaded)
    return loaded


class PTDownloaderLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_plugin()
        cls.original_module = load_plugin(
            ORIGINAL_PLUGIN, "zytlimit_test_module"
        )

    def setUp(self):
        FakeSiteOper.sites = [
            types.SimpleNamespace(id=1, name="站点一"),
            types.SimpleNamespace(id=2, name="站点二"),
        ]
        FakeDownloaderHelper.configs = {
            "qb": types.SimpleNamespace(name="qb"),
            "tr": types.SimpleNamespace(name="tr"),
        }
        FakeDownloaderHelper.services = {}
        self.plugin = self.module.PTDownloaderLimit()

    def rule(self, speed, *, time_range="", sites=None, downloaders=None):
        return {
            "id": f"rule-{speed}",
            "mark": f"speed-{speed}",
            "downloaders": downloaders or ["qb"],
            "limit_sites": sites or [1],
            "limit_speed": speed,
            "limit_sites_pause_threshold": 10,
            "active_time_range_site_config": time_range,
        }

    def capture_limit_calls(self, calls, plugin=None):
        argument_names = (
            "all_site_name_id_map",
            "all_site_names",
            "downloader_service_info",
            "limit_site_ids",
            "limit_speed",
            "limit_sites_pause_threshold",
            "is_in_time_range",
            "cancel_limit",
        )
        (plugin or self.plugin).limit_per_downloader = lambda *args: calls.append(
            dict(zip(argument_names, args))
        )

    def test_default_is_one_rule_and_explicit_empty_is_preserved(self):
        self.plugin.init_plugin({})
        self.assertEqual(len(self.plugin._rules), 1)
        self.assertEqual(self.plugin._rules[0]["downloaders"], [])
        self.assertEqual(self.plugin.get_form()[1]["rules"], self.plugin._rules)

        self.plugin.init_plugin({"rules": []})
        self.assertEqual(self.plugin._rules, [])
        self.assertIsNone(self.plugin.get_page())
        page_source = inspect.getsource(self.module.PTDownloaderLimit.get_page)
        page_node = ast.parse(textwrap.dedent(page_source)).body[0]
        self.assertTrue(all(isinstance(item, ast.Pass) for item in page_node.body))

    def test_api_command_service_and_market_contract(self):
        self.plugin.init_plugin({"enabled": True, "cron": "*/10 * * * *", "rules": []})
        self.assertEqual(self.plugin.get_command()[0]["cmd"], "/limit")
        self.assertEqual(self.plugin.get_command()[0]["data"]["action"], "limit")
        self.assertEqual(self.plugin.get_api()[0]["path"], "/options")
        self.assertEqual(self.plugin.get_api()[0]["auth"], "bear")
        self.assertEqual(self.plugin.get_service()[0]["id"], "PTDownloaderLimit")
        response = self.plugin.api_options()
        self.assertTrue(response.success)
        self.assertEqual([item["value"] for item in response.data["downloaders"]], ["qb", "tr"])
        self.assertEqual([item["value"] for item in response.data["sites"]], [1, 2])

        package = json.loads((ROOT / "package.v2.json").read_text(encoding="utf-8"))
        self.assertEqual(package["PTDownloaderLimit"]["version"], "1.0.1")
        self.assertTrue(package["PTDownloaderLimit"]["release"])

    def test_onlyonce_resets_switch_and_stop_is_idempotent(self):
        self.plugin.init_plugin({"onlyonce": True, "rules": [self.rule(20)]})
        self.assertTrue(self.plugin._scheduler.running)
        self.assertFalse(self.plugin.saved_config["onlyonce"])
        self.assertEqual(self.plugin.saved_config["rules"][0]["mark"], "speed-20")
        self.plugin.stop_service()
        self.plugin.stop_service()
        self.assertIsNone(self.plugin._scheduler)

    def test_rules_execute_in_original_order_and_later_inactive_rule_cancels_limit(self):
        service = FakeServiceInfo("qb", types.SimpleNamespace(is_inactive=lambda: False), "qbittorrent")
        FakeDownloaderHelper.services = {"qb": service}
        self.plugin.init_plugin({
            "rules": [self.rule(100), self.rule(200), self.rule(300, time_range="inactive")]
        })
        self.plugin._PTDownloaderLimit__is_current_time_in_range_site_config = (
            lambda value: value != "inactive"
        )
        calls = []
        self.capture_limit_calls(calls)
        self.plugin.limit()

        self.assertEqual([call["limit_speed"] for call in calls], [100, 200, 300, 0])
        self.assertEqual([call["is_in_time_range"] for call in calls], [True, True, False, False])
        self.assertEqual([call["cancel_limit"] for call in calls], [False, False, False, True])
        self.assertEqual([set(call["limit_site_ids"]) for call in calls], [{1}, {1}, {1}, {2}])

    def test_more_than_six_rules_are_supported(self):
        service = FakeServiceInfo("qb", types.SimpleNamespace(is_inactive=lambda: False), "qbittorrent")
        FakeDownloaderHelper.services = {"qb": service}
        self.plugin.init_plugin({"rules": [self.rule(index) for index in range(1, 9)]})
        calls = []
        self.capture_limit_calls(calls)
        self.plugin.limit()
        self.assertEqual([call["limit_speed"] for call in calls], list(range(1, 9)) + [0])

    def test_last_active_zero_speed_rule_cancels_earlier_limit(self):
        service = FakeServiceInfo("qb", types.SimpleNamespace(is_inactive=lambda: False), "qbittorrent")
        FakeDownloaderHelper.services = {"qb": service}
        self.plugin.init_plugin({"rules": [self.rule(100), self.rule(0)]})
        calls = []
        self.capture_limit_calls(calls)

        self.plugin.limit()

        site_one_calls = [call for call in calls if 1 in call["limit_site_ids"]]
        self.assertEqual([call["limit_speed"] for call in site_one_calls], [100, 0])
        self.assertFalse(site_one_calls[0]["cancel_limit"])
        self.assertTrue(site_one_calls[1]["cancel_limit"])

    def test_inactive_rule_restores_all_sites_for_its_downloader(self):
        service = FakeServiceInfo("qb", types.SimpleNamespace(is_inactive=lambda: False), "qbittorrent")
        FakeDownloaderHelper.services = {"qb": service}
        self.plugin.init_plugin({"rules": [self.rule(100, time_range="inactive")]})
        self.plugin._PTDownloaderLimit__is_current_time_in_range_site_config = (
            lambda value: value != "inactive"
        )
        calls = []
        self.capture_limit_calls(calls)

        self.plugin.limit()

        self.assertEqual(len(calls), 2)
        self.assertEqual(set(calls[0]["limit_site_ids"]), {1})
        self.assertEqual(calls[0]["limit_speed"], 100)
        self.assertFalse(calls[0]["is_in_time_range"])
        self.assertFalse(calls[0]["cancel_limit"])
        self.assertEqual(set(calls[1]["limit_site_ids"]), {2})
        self.assertEqual(calls[1]["limit_speed"], 0)
        self.assertFalse(calls[1]["is_in_time_range"])
        self.assertTrue(calls[1]["cancel_limit"])

    def test_time_range_supports_daytime_overnight_and_invalid_as_all_day(self):
        check = self.plugin._PTDownloaderLimit__is_current_time_in_range_site_config

        class FixedDateTime:
            current = datetime(2026, 1, 1, 12, 0)

            @classmethod
            def now(cls):
                return cls.current

            @classmethod
            def strptime(cls, value, pattern):
                return datetime.strptime(value, pattern)

        with patch.object(self.module, "datetime", FixedDateTime):
            self.assertTrue(check("09:00-17:00"))
            FixedDateTime.current = datetime(2026, 1, 1, 18, 0)
            self.assertFalse(check("09:00-17:00"))
            FixedDateTime.current = datetime(2026, 1, 1, 23, 0)
            self.assertTrue(check("22:00-02:00"))
            FixedDateTime.current = datetime(2026, 1, 1, 1, 0)
            self.assertTrue(check("22:00-02:00"))
            self.assertTrue(check("bad value"))

    def test_high_risk_backend_methods_match_original_source_exactly(self):
        for method_name in (
            "get_downloader_service_infos",
            "logger_info",
            "limit_per_downloader",
            "__is_current_time_in_range_site_config",
            "__is_valid_time_range",
        ):
            self.assertEqual(
                method_source(PLUGIN_DIR / "__init__.py", method_name),
                method_source(ORIGINAL_PLUGIN, method_name),
                method_name,
            )

    def test_dynamic_rule_adapter_matches_original_six_rule_execution_trace(self):
        service = FakeServiceInfo("qb", types.SimpleNamespace(is_inactive=lambda: False), "qbittorrent")
        FakeDownloaderHelper.services = {"qb": service}
        rules = [
            self.rule(100, sites=[1]),
            self.rule(200, time_range="inactive", sites=[1]),
            self.rule(0, sites=[2]),
        ]
        self.plugin.init_plugin({"rules": rules})
        self.plugin._PTDownloaderLimit__is_current_time_in_range_site_config = (
            lambda value: value != "inactive"
        )

        original = self.original_module.ZYTLimit()
        for index in range(1, 7):
            rule = rules[index - 1] if index <= len(rules) else {
                "downloaders": [],
                "limit_sites": [],
                "limit_speed": 0,
                "limit_sites_pause_threshold": 0,
                "active_time_range_site_config": "",
            }
            setattr(original, f"_downloaders{index}", rule["downloaders"])
            setattr(original, f"_limit_sites{index}", rule["limit_sites"])
            setattr(original, f"_limit_speed{index}", rule["limit_speed"])
            setattr(
                original,
                f"_limit_sites_pause_threshold{index}",
                rule["limit_sites_pause_threshold"],
            )
            setattr(
                original,
                f"_active_time_range_site_config{index}",
                rule["active_time_range_site_config"],
            )
        original._ZYTLimit__is_current_time_in_range_site_config = (
            lambda value: value != "inactive"
        )

        new_calls = []
        old_calls = []
        self.capture_limit_calls(new_calls, self.plugin)
        self.capture_limit_calls(old_calls, original)
        self.plugin.limit()
        original.limit()

        def trace(calls):
            return [
                (
                    call["downloader_service_info"].name,
                    list(call["limit_site_ids"]),
                    call["limit_speed"],
                    call["limit_sites_pause_threshold"],
                    call["is_in_time_range"],
                    call["cancel_limit"],
                )
                for call in calls
            ]

        self.assertEqual(trace(new_calls), trace(old_calls))

    def test_qb_and_transmission_limit_calls(self):
        qb_calls = []
        qb_client = types.SimpleNamespace(
            torrents_set_upload_limit=lambda speed, hashes: qb_calls.append((speed, hashes)),
            torrents_reannounce=lambda **kwargs: None,
        )
        qb_torrent = types.SimpleNamespace(
            state_enum=types.SimpleNamespace(is_downloading=False),
            tags="站点一",
            state="stalledUP",
            hash="qb-hash",
        )
        qb_instance = types.SimpleNamespace(
            qbc=qb_client,
            get_torrents=lambda: ([qb_torrent], None),
            stop_torrents=lambda hashes: None,
            start_torrents=lambda hashes: None,
            set_torrents_tag=lambda hashes, tags: None,
            remove_torrents_tag=lambda hashes, tags: None,
        )
        self.plugin.limit_per_downloader(
            {"站点一": 1}, {"站点一"}, FakeServiceInfo("qb", qb_instance, "qbittorrent"),
            {1}, 20, 0, True, False,
        )
        self.assertEqual(qb_calls, [(20 * 1024, ["qb-hash"])])

        tr_calls = []
        tr_torrent = types.SimpleNamespace(
            labels=["站点一"],
            status=types.SimpleNamespace(stopped=False, seeding=True),
            rate_upload=0,
            hashString="tr-hash",
        )
        tr_client = types.SimpleNamespace(
            get_torrents=lambda arguments: [tr_torrent],
            change_torrent=lambda **kwargs: tr_calls.append(kwargs),
        )
        tr_instance = types.SimpleNamespace(
            trc=tr_client,
            stop_torrents=lambda hashes: None,
            start_torrents=lambda hashes: None,
        )
        self.plugin.limit_per_downloader(
            {"站点一": 1}, {"站点一"}, FakeServiceInfo("tr", tr_instance, "transmission"),
            {1}, 15, 0, True, False,
        )
        self.assertEqual(tr_calls[0]["upload_limit"], 15)
        self.assertTrue(tr_calls[0]["upload_limited"])

    def test_qb_only_resumes_completed_torrents_like_original_plugin(self):
        resumed = []
        limit_calls = []
        incomplete = types.SimpleNamespace(
            name="未完成任务",
            state_enum=types.SimpleNamespace(is_downloading=False),
            tags="站点一",
            state="pausedUP",
            hash="incomplete",
            total_size=100,
            completed=50,
        )
        complete = types.SimpleNamespace(
            name="已完成任务",
            state_enum=types.SimpleNamespace(is_downloading=False),
            tags="站点一",
            state="pausedUP",
            hash="complete",
            total_size=100,
            completed=100,
        )
        qb_instance = types.SimpleNamespace(
            qbc=types.SimpleNamespace(
                torrents_set_upload_limit=lambda speed, hashes: limit_calls.append((speed, hashes)),
                torrents_reannounce=lambda **kwargs: None,
            ),
            get_torrents=lambda: ([incomplete, complete], None),
            stop_torrents=lambda hashes: None,
            start_torrents=lambda hashes: resumed.extend(hashes),
            set_torrents_tag=lambda hashes, tags: None,
            remove_torrents_tag=lambda hashes, tags: None,
        )

        self.plugin.limit_per_downloader(
            {"站点一": 1},
            {"站点一"},
            FakeServiceInfo("qb", qb_instance, "qbittorrent"),
            {1},
            0,
            0,
            False,
            True,
        )

        self.assertEqual(limit_calls, [(0, ["incomplete", "complete"])])
        self.assertEqual(resumed, ["complete"])

    def test_original_limit_diagnostic_logs_are_preserved(self):
        source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
        for marker in (
            "开始设置限速",
            "下载中，跳过",
            "含有不限速标签",
            "没有添加站点标签",
            "限速{limit_speed}K种子个数",
            "不限速种子个数",
            "非限速区间,解除限速",
            "限速后仍活动,暂停种子个数",
            "重新开始种子个数",
        ):
            self.assertIn(marker, source)

    def test_frontend_dynamic_controls_and_release_assets_exist(self):
        source = (PLUGIN_DIR / "src" / "components" / "Config.vue").read_text(encoding="utf-8")
        for marker in (
            "VExpansionPanels", "addRule", "deleteRule", "moveRule", "新增规则",
            "`${title}：${rule.mark.trim()}`", "padding-inline: 8px",
            "emit('save', payload)", "规则按列表顺序逐条执行",
            "const expanded = ref()", "expanded.value = undefined", "toggleRule(index)",
            "readonly", "hide-actions", "mdi-chevron-right",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("请勿同时启用旧版", source)
        self.assertTrue((PLUGIN_DIR / "dist" / "assets" / "remoteEntry.js").is_file())


if __name__ == "__main__":
    unittest.main()
