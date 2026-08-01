#!/usr/bin/env python3
"""校验启用 Release 分发的 V2 插件市场版本与源码版本一致。"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def plugin_version(init_file: Path) -> str | None:
    """读取插件类的 plugin_version 字面量。"""
    tree = ast.parse(init_file.read_text(encoding="utf-8"), filename=str(init_file))
    for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        for node in class_node.body:
            value_node = None
            if isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id == "plugin_version" for target in node.targets):
                    value_node = node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "plugin_version"
            ):
                value_node = node.value
            if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
                return value_node.value
    return None


def check_package(package_file: Path) -> list[str]:
    """返回索引中所有 Release 插件的版本错误。"""
    package = json.loads(package_file.read_text(encoding="utf-8"))
    errors = []
    for plugin_id, meta in package.items():
        if not isinstance(meta, dict) or meta.get("release") is not True:
            continue
        expected = str(meta.get("version") or "").strip()
        plugin_dir = package_file.parent / "plugins.v2" / plugin_id.lower()
        init_file = plugin_dir / "__init__.py"
        if not init_file.is_file():
            errors.append(f"{plugin_id}: 缺少 {init_file}")
            continue
        actual = plugin_version(init_file)
        if not actual:
            errors.append(f"{plugin_id}: 未声明类级 plugin_version")
        elif actual != expected:
            errors.append(f"{plugin_id}: package={expected}, plugin_version={actual}")
    return errors


def main() -> int:
    """执行版本门禁。"""
    package_files = [Path(value) for value in sys.argv[1:]] or [Path("package.v2.json")]
    errors = []
    for package_file in package_files:
        errors.extend(check_package(package_file))
    if errors:
        print("插件版本门禁失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("插件版本门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
