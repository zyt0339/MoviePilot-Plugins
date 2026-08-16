"""PTDepilerMp 站点规则目录与纯计算测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = ROOT / "plugins.v2" / "ptdepilermp" / "rules.py"
RULES_DIR = RULES_PATH.with_name("site_rules")
SPEC = importlib.util.spec_from_file_location("ptdepilermp_test_rules", RULES_PATH)
rules = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rules
SPEC.loader.exec_module(rules)


class RuleDirectoryTest(unittest.TestCase):
    """验证单站规则目录的可用性和隐私边界。"""

    def test_site_files_are_valid_without_upstream_metadata(self):
        paths = sorted(RULES_DIR.glob("*.json"))
        entries = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
        real_entries = [(path, site) for path, site in entries if path.name != "1demo.json"]
        self.assertGreaterEqual(len(real_entries), 225)
        sites = [site for _, site in real_entries]
        self.assertEqual(
            [site["name"].casefold() for site in sites],
            [path.stem.casefold() for path, _ in real_entries],
        )
        self.assertTrue(all("id" not in site for site in sites))
        self.assertGreaterEqual(
            sum(any(level.get("isKept") for level in site["levels"]) for site in sites),
            100,
        )
        site_payload = json.dumps(sites, ensure_ascii=False)
        self.assertNotIn("://", site_payload)
        self.assertNotIn("urls", site_payload)
        for removed in ("source", "source_commit", "generated_at", "license", "definition_version"):
            self.assertNotIn(f'"{removed}"', site_payload)
        self.assertTrue(all("host_fingerprints" not in site for site in sites))
        self.assertTrue(all("moviepilot_site_ids" not in site for site in sites))

    def test_site_files_have_deterministic_format(self):
        for path in RULES_DIR.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            expected = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            self.assertEqual(path.read_text(encoding="utf-8"), expected)

    def test_directory_reload_accepts_new_file_and_skips_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            repository = rules.RuleRepository(target)
            self.assertEqual(repository.sites, {})
            custom = {
                "name": "custom",
                "levels": [{"id": 0, "name": "User"}],
            }
            (target / "custom.json").write_text(json.dumps(custom), encoding="utf-8")
            (target / "broken.json").write_text("{", encoding="utf-8")
            repository.reload()
            rule_id, matched = repository.match(" CUSTOM ")
            self.assertEqual(rule_id, "custom")
            self.assertEqual(matched, custom)
            self.assertEqual(repository.load_errors, 1)

    def test_filename_case_change_preserves_canonical_rule_name(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            rule = {"name": "CARPT", "levels": [{"id": 0, "name": "User"}]}
            (target / "carpt.json").write_text(json.dumps(rule), encoding="utf-8")
            repository = rules.RuleRepository(target)
            rule_id, matched = repository.match("CARPT")
            self.assertEqual(repository.load_errors, 0)
            self.assertEqual(rule_id, "CARPT")
            self.assertEqual(matched, rule)

    def test_demo_file_is_documented_and_loaded_normally(self):
        demo = json.loads((RULES_DIR / "1demo.json").read_text(encoding="utf-8"))
        self.assertEqual(demo["name"], "1demo")
        self.assertNotIn("is_dead", demo)
        self.assertNotIn("trueUploaded", demo["levels"][0])
        repository = rules.RuleRepository(RULES_DIR)
        self.assertEqual(repository.sites["1demo"], demo)


class RequirementTest(unittest.TestCase):
    """验证等级匹配、阈值、三态和缺口算法。"""

    def test_size_units_and_equal_threshold(self):
        self.assertEqual(rules.parse_size("1GiB"), 1024**3)
        self.assertEqual(rules.parse_size("1 GB"), 1000**3)
        result = rules.evaluate_requirement({"upload": 1024**3}, {"uploaded": "1GiB"})
        self.assertEqual(result.status, "met")
        self.assertEqual(result.gaps, {})

    def test_missing_field_is_unknown_not_zero(self):
        result = rules.evaluate_requirement({}, {"uploaded": "1GiB", "ratio": 1})
        self.assertEqual(result.status, "unknown")
        self.assertCountEqual(result.unknown, ["ratio", "uploaded"])

    def test_seeding_bonus_is_temporarily_treated_as_met(self):
        result = rules.evaluate_requirement({}, {"seedingBonus": 1000000})
        self.assertEqual(result.status, "met")
        self.assertEqual(result.gaps, {})
        self.assertEqual(result.unknown, [])

    def test_ratio_is_derived_and_gap_is_never_negative(self):
        met = rules.evaluate_requirement({"upload": 200, "download": 100}, {"ratio": 2})
        self.assertEqual(met.status, "met")
        unmet = rules.evaluate_requirement({"upload": 100, "download": 100}, {"ratio": 2})
        self.assertEqual(unmet.gaps["uploaded"], 100)
        self.assertTrue(all(value >= 0 for value in unmet.gaps.values() if isinstance(value, (int, float))))

    def test_interval_month_boundary_and_invalid_date(self):
        now = datetime(2024, 2, 29, tzinfo=timezone.utc)
        met = rules.evaluate_requirement({"join_at": "2024-01-31T00:00:00+00:00"}, {"interval": "P1M"}, now)
        self.assertEqual(met.status, "met")
        unknown = rules.evaluate_requirement({"join_at": "not-a-date"}, {"interval": "P1M"}, now)
        self.assertEqual(unknown.status, "unknown")

    def test_alternative_is_or_and_hnr_is_upper_limit(self):
        alternative = rules.evaluate_requirement(
            {"upload": 10, "seeding_size": 200},
            {"alternative": [{"uploaded": 100}, {"seedingSize": 100}]},
        )
        self.assertEqual(alternative.status, "met")
        hnr = rules.evaluate_requirement({"hnr_unsatisfied": 3}, {"hnrUnsatisfied": 1})
        self.assertEqual(hnr.status, "unmet")
        self.assertEqual(hnr.gaps["hnrUnsatisfied"], 2)

    def test_alias_special_groups_retention_and_next_level(self):
        levels = [
            {"id": 0, "name": "User"},
            {"id": 1, "name": "Power User", "nameAka": ["PU"], "isKept": True},
            {"id": 99, "name": "VIP", "groupType": "vip"},
        ]
        current, group = rules.find_level("PU", levels)
        self.assertEqual((current["id"], group), (1, "user"))
        repository = object.__new__(rules.RuleRepository)
        retained = repository.evaluate_site({"user_level": "PU"}, "x", {"levels": levels})
        self.assertTrue(retained.retained)
        vip = repository.evaluate_site({"user_level": "VIP"}, "x", {"levels": levels})
        self.assertTrue(vip.retained)

    def test_level_above_first_retained_level_is_also_retained(self):
        levels = [
            {"id": 0, "name": "User"},
            {"id": 1, "name": "Power User", "isKept": True},
            {"id": 2, "name": "Elite User"},
        ]
        repository = object.__new__(rules.RuleRepository)
        result = repository.evaluate_site({"user_level": "Elite User"}, "x", {"levels": levels})
        self.assertTrue(result.retained)

    def test_donor_retention_only_requires_account_selection_and_ignores_level(self):
        repository = object.__new__(rules.RuleRepository)
        levels = [
            {"id": 0, "name": "User"},
            {"id": 1, "name": "Nexus Master", "isKept": True},
        ]
        donor_rule = {"levels": levels}
        donor = repository.evaluate_site(
            {"user_level": "User", "is_donor": True}, "x", donor_rule
        )
        self.assertTrue(donor.retained)
        self.assertEqual(donor.retention_type, "donor")

        not_selected = repository.evaluate_site({"user_level": "User"}, "x", donor_rule)
        self.assertFalse(not_selected.retained)
        unknown_level = repository.evaluate_site(
            {"user_level": "not-a-level", "is_donor": True}, "x", donor_rule
        )
        self.assertTrue(unknown_level.retained)
        self.assertEqual(unknown_level.retention_type, "donor")
        self.assertIsNone(unknown_level.reason)

        unmatched = repository.evaluate_site({"is_donor": True}, None, None)
        self.assertTrue(unmatched.retained)
        self.assertEqual(unmatched.retention_type, "donor")

        dead = repository.evaluate_site({"is_donor": True}, "x", {"is_dead": True})
        self.assertTrue(dead.retained)
        self.assertEqual(dead.retention_type, "donor")

    def test_pig_generic_nexus_level_is_corrected_by_met_requirements(self):
        repository = rules.RuleRepository(RULES_DIR)
        result = repository.evaluate_site(
            {
                "user_level": "Nexus Master",
                "join_at": "2024-08-30 11:01:38",
                "upload": 45.74 * 1024**4,
                "download": 6 * 1024**4,
                "ratio": 7.62,
            },
            "猪猪",
            repository.sites["猪猪"],
            now=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.current_level["name"], "天蓬元帅")
        self.assertTrue(result.retained)

    def test_generic_level_is_not_promoted_by_levels_without_requirements(self):
        levels = [{"id": index, "name": f"自定义{index}"} for index in range(10)]
        repository = object.__new__(rules.RuleRepository)
        result = repository.evaluate_site({"user_level": "Nexus Master"}, "x", {"levels": levels})
        self.assertEqual(result.current_level["id"], 8)

    def test_decorated_and_standard_level_names_match(self):
        standard = [{"id": index, "name": name} for index, name in enumerate(rules.STANDARD_USER_LEVELS)]
        current, _ = rules.find_level("(头号玩家)Extreme User", standard)
        self.assertEqual(current["name"], "Extreme User")
        decorated = [{"id": index, "name": f"自定义 {name}"} for index, name in enumerate(rules.STANDARD_USER_LEVELS)]
        current, _ = rules.find_level("Ultimate User(站点头衔)", decorated)
        self.assertEqual(current["id"], 7)
        custom = [{"id": index, "name": f"等级{index}"} for index in range(9)]
        current, _ = rules.find_level("Nexus Master", custom)
        self.assertEqual(current["id"], 8)

    def test_repository_loads_builtin_directory(self):
        repository = rules.RuleRepository(RULES_DIR)
        self.assertGreaterEqual(len(repository.sites), 225)
        self.assertEqual(repository.load_errors, 0)

    def test_four_requested_site_rules_are_loaded_by_database_name(self):
        repository = rules.RuleRepository(RULES_DIR)
        for site_name in ("Sunny", "朋友", "青蛙", "馒头"):
            rule_id, rule = repository.match(site_name)
            self.assertEqual(rule_id, site_name)
            self.assertEqual(rule["name"], site_name)
            self.assertTrue(rule["levels"])
        self.assertTrue(any(level.get("isKept") for level in repository.sites["Sunny"]["levels"]))
        self.assertTrue(repository.sites["青蛙"]["levels"][2].get("alternative"))
        self.assertEqual(repository.sites["馒头"]["levels"][-1]["groupType"], "vip")

    def test_ubits_agsvpt_and_carpt_rules_use_moviepilot_database_names(self):
        repository = rules.RuleRepository(RULES_DIR)
        archive_names = {path.stem.casefold() for path in RULES_DIR.glob("*.json")}
        for site_name in ("UBits", "AGSVPT", "CARPT"):
            rule_id, rule = repository.match(site_name)
            self.assertEqual(rule_id, site_name)
            self.assertEqual(rule["name"], site_name)
            self.assertIn(site_name.casefold(), archive_names)

    def test_hdhome_nexus_master_is_retention_level(self):
        repository = rules.RuleRepository(RULES_DIR)
        self.assertNotIn("donorAccountKept", repository.sites["家园"])
        retention_levels = [
            level for level in repository.sites["家园"]["levels"]
            if level.get("isKept")
        ]
        self.assertEqual([level["name"] for level in retention_levels], ["Nexus Master"])

    def test_ttg_has_no_retention_level_or_join_time_override(self):
        repository = rules.RuleRepository(RULES_DIR)
        sky_rule = repository.sites["天空"]
        self.assertNotIn("levelRequirementOverrides", sky_rule)
        self.assertFalse(any(level.get("isKept") for level in sky_rule["levels"]))
        self.assertEqual(
            [level.get("nameAka") for level in sky_rule["levels"]],
            [
                ["临时演员"],
                ["跑龙套"],
                ["配角"],
                ["主演"],
                ["领衔主演"],
                ["明星"],
                ["国际大腕"],
                ["影帝"],
                ["终身影帝"],
            ],
        )

    def test_unmatched_dead_and_unknown_level_reasons(self):
        repository = object.__new__(rules.RuleRepository)
        self.assertIn("未匹配", repository.evaluate_site({}, None, None).reason)
        self.assertIn("失效", repository.evaluate_site({}, "x", {"is_dead": True}).reason)
        result = repository.evaluate_site({"user_level": "mystery"}, "x", {"levels": [{"id": 0, "name": "User"}]})
        self.assertIn("无法识别", result.reason)


if __name__ == "__main__":
    unittest.main()
