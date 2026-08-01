"""PTDepilerMp 等级规则加载、匹配与差值计算。"""

from __future__ import annotations

import hashlib
import json
import math
import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
    "PB": 1000**5,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
    "PIB": 1024**5,
}

SIZE_FIELDS = {
    "totalTraffic": None,
    "downloaded": "download",
    "trueDownloaded": "true_download",
    "uploaded": "upload",
    "trueUploaded": "true_upload",
    "seedingSize": "seeding_size",
    "specialSeedingSize": "special_seeding_size",
}
NUMBER_FIELDS = {
    "bonus": "bonus",
    "bonusPerHour": "bonus_per_hour",
    "seedingBonus": "seeding_bonus",
    "seedingBonusPerHour": "seeding_bonus_per_hour",
    "seeding": "seeding",
    "uploads": "uploads",
    "leeching": "leeching",
    "snatches": "snatches",
    "posts": "posts",
    "adoptions": "adoptions",
    "perfectFlacs": "perfect_flacs",
    "groups": "groups",
}
DURATION_FIELDS = {
    "seedingTime": "seeding_time",
    "averageSeedingTime": "average_seeding_time",
}
SPECIAL_LEVEL_KEYWORDS = {
    "manager": (
        "retiree", "养老", "退休", "uploader", "发布", "发种", "helper", "assistant",
        "助手", "助理", "forum", "版主", "moderator", "admin", "管理", "sys", "coder",
        "开发", "staff", "主管",
    ),
    "vip": ("vip", "贵宾", "honor", "荣誉", "donor", "捐赠"),
}


@dataclass
class RequirementResult:
    """单个等级条件的三态计算结果。"""

    status: str
    gaps: Dict[str, Any] = field(default_factory=dict)
    unknown: List[str] = field(default_factory=list)


@dataclass
class SiteLevelResult:
    """站点当前等级及下一等级的规范化结果。"""

    rule_id: Optional[str]
    current_level: Optional[Dict[str, Any]]
    current_group: Optional[str]
    next_level: Optional[Dict[str, Any]]
    next_requirement: Optional[RequirementResult]
    retained: Optional[bool]
    reason: Optional[str] = None


def clean_level_name(value: Any) -> str:
    """规范化等级名称，用于处理中英文空格和下划线差异。"""
    return re.sub(r"[\s_]+", "", str(value or "")).lower()


def normalize_host(value: str) -> str:
    """从域名或 URL 中提取规范化主机名。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").lower().removeprefix("www.")


def host_fingerprint(value: str) -> str:
    """生成与规则快照一致的主机名 SHA-256 指纹。"""
    host = normalize_host(value)
    return hashlib.sha256(host.encode("utf-8")).hexdigest() if host else ""


def parse_size(value: Any) -> Optional[float]:
    """将数字或常见十进制/二进制容量字符串转换为字节。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = re.fullmatch(r"\s*([+-]?[\d.]+)\s*([kmgtp]?i?b)?\s*", str(value or ""), re.IGNORECASE)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    unit = (match.group(2) or "B").upper()
    return number * SIZE_UNITS.get(unit, 1)


def _parse_duration(value: Any, base: datetime) -> Optional[datetime]:
    """将 PT-depiler 使用的 ISO 日期区间转换为目标时间。"""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?",
        value.strip().upper(),
    )
    if not match:
        return None
    years, months, weeks, days = (int(item or 0) for item in match.groups())
    month_index = base.month - 1 + months + years * 12
    target_year, target_month = base.year + month_index // 12, month_index % 12 + 1
    target_day = min(base.day, monthrange(target_year, target_month)[1])
    return base.replace(year=target_year, month=target_month, day=target_day) + timedelta(days=weeks * 7 + days)


def _parse_datetime(value: Any) -> Optional[datetime]:
    """解析 MoviePilot 常见的 ISO 或日期时间字符串。"""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    return None


def _seconds_from_duration(value: Any) -> Optional[float]:
    """将秒数或简单 ISO 日期区间转换为秒。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    base = datetime(2000, 1, 1)
    target = _parse_duration(value, base)
    return (target - base).total_seconds() if target else None


def guess_group(level_name: str) -> str:
    """按 PT-depiler 的关键词规则识别普通、VIP 与管理等级。"""
    normalized = clean_level_name(level_name)
    for group, keywords in SPECIAL_LEVEL_KEYWORDS.items():
        if any(clean_level_name(keyword) in normalized for keyword in keywords):
            return group
    return "user"


def find_level(level_name: str, levels: Iterable[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """通过名称、别名和特殊等级关键字定位当前等级。"""
    normalized = clean_level_name(level_name)
    if not normalized:
        return None, "user"
    for level in levels:
        names = [level.get("name"), *(level.get("nameAka") or [])]
        if any(clean_level_name(name) == normalized or clean_level_name(name).find(normalized) >= 0 for name in names if name):
            return level, level.get("groupType") or "user"
    group = guess_group(level_name)
    if group != "user":
        special = next((level for level in levels if level.get("groupType") == group), None)
        return special, group
    return None, "user"


def _known_number(user: Dict[str, Any], key: Optional[str]) -> Optional[float]:
    """读取用户数值；缺失值保持未知而非回落为零。"""
    if key is None:
        upload, download = user.get("upload"), user.get("download")
        if upload is None or download is None:
            return None
        return float(upload) + float(download)
    value = user.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(user: Dict[str, Any], ratio_key: str) -> Optional[float]:
    """读取或按上传下载量补算分享率。"""
    direct = _known_number(user, "ratio" if ratio_key == "ratio" else "true_ratio")
    if direct is not None:
        return direct
    uploaded_key, downloaded_key = (
        ("upload", "download") if ratio_key == "ratio" else ("true_upload", "true_download")
    )
    uploaded, downloaded = _known_number(user, uploaded_key), _known_number(user, downloaded_key)
    if uploaded is None or downloaded is None:
        return None
    if downloaded == 0:
        return math.inf if uploaded > 0 else None
    return uploaded / downloaded


def evaluate_requirement(
    user: Dict[str, Any],
    requirement: Dict[str, Any],
    now: Optional[datetime] = None,
) -> RequirementResult:
    """计算等级条件的已知缺口和未知字段。"""
    now = now or datetime.now().astimezone()
    gaps: Dict[str, Any] = {}
    unknown: List[str] = []

    if requirement.get("interval"):
        try:
            join_at = _parse_datetime(user.get("join_at"))
            if join_at and join_at.tzinfo is None and now.tzinfo:
                join_at = join_at.replace(tzinfo=now.tzinfo)
            elif join_at and join_at.tzinfo and now.tzinfo is None:
                now = now.replace(tzinfo=join_at.tzinfo)
        except (ValueError, TypeError, OverflowError):
            join_at = None
        target = _parse_duration(requirement["interval"], join_at) if join_at else None
        if target is None:
            unknown.append("interval")
        elif target > now:
            gaps["interval"] = {
                "seconds": max(0, int((target - now).total_seconds())),
                "target": target.isoformat(),
            }

    for field_name, user_key in SIZE_FIELDS.items():
        if field_name not in requirement:
            continue
        expected = parse_size(requirement[field_name])
        current = _known_number(user, user_key)
        if expected is None or current is None:
            unknown.append(field_name)
        elif current < expected:
            gaps[field_name] = expected - current

    for ratio_key in ("ratio", "trueRatio"):
        if ratio_key not in requirement:
            continue
        current_ratio = _ratio(user, ratio_key)
        target_value = requirement[ratio_key]
        targets = sorted(target_value) if isinstance(target_value, list) and len(target_value) == 2 else [target_value]
        try:
            min_ratio = float(targets[0])
            max_ratio = float(targets[1]) if len(targets) > 1 else None
        except (TypeError, ValueError):
            unknown.append(ratio_key)
            continue
        if current_ratio is None:
            unknown.append(ratio_key)
            continue
        uploaded_key, downloaded_key = (
            ("upload", "download") if ratio_key == "ratio" else ("true_upload", "true_download")
        )
        uploaded, downloaded = _known_number(user, uploaded_key), _known_number(user, downloaded_key)
        if current_ratio < min_ratio:
            gaps[ratio_key] = min_ratio
            required_download = parse_size(requirement.get("downloaded" if ratio_key == "ratio" else "trueDownloaded")) or 0
            if uploaded is not None and downloaded is not None:
                upload_gap = max(downloaded, required_download) * min_ratio - uploaded
                if upload_gap > 0:
                    field_key = "uploaded" if ratio_key == "ratio" else "trueUploaded"
                    gaps[field_key] = max(float(gaps.get(field_key, 0)), upload_gap)
        if max_ratio is not None and current_ratio > max_ratio:
            gaps[ratio_key] = max_ratio
            if uploaded is not None and downloaded is not None:
                download_gap = uploaded / max_ratio - downloaded
                if download_gap > 0:
                    field_key = "downloaded" if ratio_key == "ratio" else "trueDownloaded"
                    gaps[field_key] = max(float(gaps.get(field_key, 0)), download_gap)

    for field_name, user_key in DURATION_FIELDS.items():
        if field_name not in requirement:
            continue
        expected = _seconds_from_duration(requirement[field_name])
        current = _known_number(user, user_key)
        if expected is None or current is None:
            unknown.append(field_name)
        elif current < expected:
            gaps[field_name] = expected - current

    for field_name, user_key in NUMBER_FIELDS.items():
        if field_name not in requirement:
            continue
        expected = _known_number(requirement, field_name)
        current = _known_number(user, user_key)
        if expected is None or current is None:
            unknown.append(field_name)
        elif current < expected:
            gaps[field_name] = expected - current

    if "hnrUnsatisfied" in requirement:
        expected = _known_number(requirement, "hnrUnsatisfied")
        current = _known_number(user, "hnr_unsatisfied")
        if expected is None or current is None:
            unknown.append("hnrUnsatisfied")
        elif current > expected:
            gaps["hnrUnsatisfied"] = current - expected

    alternatives = requirement.get("alternative")
    if isinstance(alternatives, list) and alternatives:
        results = [evaluate_requirement(user, item, now=now) for item in alternatives if isinstance(item, dict)]
        if any(item.status == "met" for item in results):
            pass
        elif results and all(item.status == "unmet" for item in results):
            gaps["alternative"] = [item.gaps for item in results]
        else:
            unknown.append("alternative")

    unknown = sorted(set(unknown))
    status = "unmet" if gaps else "unknown" if unknown else "met"
    return RequirementResult(status=status, gaps=gaps, unknown=unknown)


class RuleRepository:
    """加载内置快照，并按站点指纹或用户覆盖返回等级规则。"""

    def __init__(self, snapshot_path: Optional[Path] = None):
        path = snapshot_path or Path(__file__).with_name("rules.snapshot.json")
        self.snapshot = json.loads(path.read_text(encoding="utf-8"))
        self.sites: Dict[str, Dict[str, Any]] = self.snapshot.get("sites") or {}
        self._fingerprint_index: Dict[str, str] = {}
        for rule_id, rule in self.sites.items():
            for fingerprint in rule.get("host_fingerprints") or []:
                self._fingerprint_index.setdefault(fingerprint, rule_id)

    @property
    def source_commit(self) -> str:
        """返回规则快照对应的 PT-depiler 提交。"""
        return str(self.snapshot.get("source_commit") or "")

    @staticmethod
    def parse_overrides(raw: Any) -> Dict[str, Dict[str, Any]]:
        """解析以 MoviePilot 站点 ID 为键的完整规则覆盖。"""
        if not raw:
            return {}
        value = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(value, dict):
            raise ValueError("规则覆盖必须是 JSON 对象")
        result = {}
        for site_id, rule in value.items():
            if not isinstance(rule, dict) or not isinstance(rule.get("levels"), list):
                raise ValueError(f"站点 {site_id} 的覆盖必须包含 levels 数组")
            result[str(site_id)] = rule
        return result

    def match(self, site_id: Any, domain: str, overrides: Dict[str, Dict[str, Any]]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """优先返回站点完整覆盖，否则按域名指纹匹配内置规则。"""
        override = overrides.get(str(site_id))
        if override:
            return f"override:{site_id}", override
        rule_id = self._fingerprint_index.get(host_fingerprint(domain))
        return (rule_id, self.sites.get(rule_id)) if rule_id else (None, None)

    def evaluate_site(
        self,
        user: Dict[str, Any],
        rule_id: Optional[str],
        rule: Optional[Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> SiteLevelResult:
        """计算站点当前等级、保号状态和下一等级缺口。"""
        if not rule:
            return SiteLevelResult(rule_id, None, None, None, None, None, "未匹配等级规则")
        if rule.get("is_dead"):
            return SiteLevelResult(rule_id, None, None, None, None, None, "上游规则已标记站点失效")
        levels = sorted(rule.get("levels") or [], key=lambda item: item.get("id", -1))
        current, group = find_level(str(user.get("user_level") or ""), levels)
        if current is None and group == "user":
            return SiteLevelResult(rule_id, None, group, None, None, None, "无法识别当前等级")
        retained = True if group in {"vip", "manager"} else bool(current and current.get("isKept"))
        next_level = None
        next_requirement = None
        if current is not None:
            current_id = current.get("id", -1)
            next_level = next(
                (item for item in levels if (item.get("groupType") or "user") == "user" and item.get("id", -1) > current_id),
                None,
            )
            if next_level:
                next_requirement = evaluate_requirement(user, next_level, now=now)
        return SiteLevelResult(rule_id, current, group, next_level, next_requirement, retained)
