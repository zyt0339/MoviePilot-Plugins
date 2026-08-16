"""PTDepilerMp 宿主集成的静态契约测试。"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins.v2" / "ptdepilermp" / "__init__.py"
PACKAGE = ROOT / "package.v2.json"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
VERSION_GATE = ROOT / ".github" / "scripts" / "check_plugin_versions.py"


class PluginContractTest(unittest.TestCase):
    """在不加载 MoviePilot 服务的前提下检查安全及集成契约。"""

    def test_plugin_has_no_site_refresh_contract(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertNotIn("SiteChain", source)
        self.assertNotIn("SitesHelper", source)
        self.assertNotIn("refresh_site", source)
        self.assertNotIn('"text": "操作"', source)
        self.assertIn("get_userdata_latest()", source)
        self.assertIn("latest_by_name", source)
        self.assertNotIn("latest_by_domain", source)
        self.assertIn("@eventmanager.register(EventType.SiteRefreshed)", source)
        self.assertIn('get("site_id") != "*"', source)
        self.assertNotIn("settings.API_TOKEN", source)
        self.assertNotIn("cookie", source.lower())
        self.assertIn('getattr(site, "url", None)', source)
        self.assertIn('"rel": "noopener noreferrer"', source)

    def test_ui_contains_retention_and_local_recalculation_controls(self):
        source = PLUGIN.read_text(encoding="utf-8")
        for marker in (
            "已保号", '"success"', "陈旧/失败", "立即重新计算一次",
            "保号数据重算周期", '"component": "VCronField"',
            "cron 留空时不创建定时任务",
            "（保号等级）", "mdi-check-circle-outline", "mdi-circle-outline",
            "黄星/捐赠者特殊保号站点", "donor_sites", "黄星（特殊保号）",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("显示仪表盘", source)
        self.assertNotIn("输出一次调查日志", source)
        self.assertNotIn('"text": "刷新全部站点"', source)
        self.assertNotIn("mdi-arrow-right-circle", source)
        self.assertNotIn("非当天快照统一标记为陈旧", source)
        self.assertNotIn('("启用站点", summary["configured"]', source)
        self.assertNotIn('("规则匹配", summary["matched"]', source)

    def test_market_version_matches_plugin(self):
        import json

        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        self.assertEqual(package["PTDepilerMp"]["name"], "PT 站点保号状态")
        self.assertEqual(package["PTDepilerMp"]["version"], "1.38.6")
        self.assertIs(package["PTDepilerMp"]["release"], True)
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertIn('plugin_name = "PT 站点保号状态"', source)
        self.assertIn('plugin_version = "1.38.6"', source)

    def test_release_workflow_uses_moviepilot_asset_contract(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: write", workflow)
        self.assertIn('tag="${plugin_id}_v${plugin_version}"', workflow)
        self.assertIn('asset="${plugin_id_lc}_v${plugin_version}.zip"', workflow)
        self.assertIn('process_package "package.v2.json"', workflow)
        self.assertTrue(VERSION_GATE.is_file())


if __name__ == "__main__":
    unittest.main()
