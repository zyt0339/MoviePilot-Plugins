# 从 PT-depiler 更新规则

## 更新前准备

每次更新 PT-depiler 源码后，进入工程并同步锁定依赖（不只是首次运行）：

```bash
cd /Users/zhaoyongtao/develop/python/PT-depiler
pnpm install --frozen-lockfile
```

若导出提示 `Cannot find module`（例如 `@noble/hashes/legacy.js`），通常是源码更新后本地依赖尚未同步，请先完成上面的安装，再重新导出。Node.js 和 pnpm 版本应满足 PT-depiler 当前 `package.json` 的 `engines` 要求。

## 更新正式规则

在任意目录运行：

```bash
node /Users/zhaoyongtao/develop/python/MP/MoviePilot-Plugins/plugins.v2/ptdepilermp/tools/export_ptdepiler_rules.mjs --source /Users/zhaoyongtao/develop/python/PT-depiler --apply
```

脚本通过 PT-depiler 自带的 Vite/TypeScript 环境读取 `siteMetadata.levelRequirements`，能够正确展开 import、继承和对象 spread。它只导出插件支持的静态等级字段，不导出域名、Cookie、选择器和抓取函数。

`--apply` 会自动执行以下操作：

- 覆盖正式 `site_rules/` 下所有同名 JSON。
- 将 PT-depiler 新增的等级规则写入正式目录。
- 不删除 PT-depiler 当前未导出的本地文件，因此 `1demo.json` 等本地独有文件会保留；Sunny 等同名规则仍会被覆盖。
- 先验证全部规则，再开始写入，避免某条规则异常时只更新一部分。

## 使用 Git 审核
