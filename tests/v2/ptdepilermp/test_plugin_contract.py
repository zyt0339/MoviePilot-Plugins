"""PTDepilerMp 宿主集成的静态契约测试。"""

from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins.v2" / "ptdepilermp" / "__init__.py"
PACKAGE = ROOT / "package.v2.json"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
VERSION_GATE = ROOT / ".github" / "scripts" / "check_plugin_versions.py"


class PluginContractTest(unittest.TestCase):
    """在不加载 MoviePilot 服务的前提下检查安全及集成契约。"""

    def test_snapshot_reading_keeps_security_boundaries(self):
        source = PLUGIN.read_text(encoding="utf-8")
        self.assertNotIn("SiteChain", source)
        self.assertNotIn("refresh_site", source)
        self.assertIn("get_userdata_latest()", source)
        self.assertIn("latest_by_name", source)
        self.assertNotIn("latest_by_domain", source)
        self.assertIn("@eventmanager.register(EventType.SiteRefreshed)", source)
        self.assertIn('get("site_id") != "*"', source)
        self.assertNotIn("cookie", source.lower())

    def test_market_version_matches_plugin(self):
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        source = PLUGIN.read_text(encoding="utf-8")
        name = re.search(r'^\s*plugin_name\s*=\s*"([^"]+)"', source, re.MULTILINE)
        version = re.search(r'^\s*plugin_version\s*=\s*"([^"]+)"', source, re.MULTILINE)
        self.assertIsNotNone(name)
        self.assertIsNotNone(version)
        self.assertEqual(package["PTDepilerMp"]["name"], name.group(1))
        self.assertEqual(package["PTDepilerMp"]["version"], version.group(1))
        self.assertIs(package["PTDepilerMp"]["release"], True)

    def test_release_workflow_uses_moviepilot_asset_contract(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: write", workflow)
        self.assertIn('tag="${plugin_id}_v${plugin_version}"', workflow)
        self.assertIn('asset="${plugin_id_lc}_v${plugin_version}.zip"', workflow)
        self.assertIn('process_package "package.v2.json"', workflow)
        self.assertTrue(VERSION_GATE.is_file())


if __name__ == "__main__":
    unittest.main()
