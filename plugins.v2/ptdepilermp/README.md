# PT站点等级监控（PTDepilerMp）

基于 MoviePilot V2 已保存的站点用户数据，展示当前等级、上传/下载量、分享率、入站时间、下一等级要求和已知缺口。达到规则中的保号等级，或当前属于 VIP/管理等特殊等级时，会显示绿色“已保号”标识。

## 数据与规则

- 站点数据只读取 MoviePilot 的最新快照，插件不读取 Cookie，也不自行抓取站点。
- 内置等级规则由 PT-depiler 的 TypeScript 定义离线生成，来源提交记录在 `rules.snapshot.json` 中；运行时不访问 PT-depiler 或 GitHub。
- 快照只保存规范化主机名的 SHA-256 指纹，不保存站点域名。
- MoviePilot 未采集的条件显示“数据不足”，不会被当作 0 或错误宣称达标。

## 完整规则覆盖

配置项接受以 MoviePilot 站点 ID 为键的 JSON；该站点规则会完整替换内置规则：

```json
{
  "12": {
    "name": "自定义规则",
    "levels": [
      {"id": 0, "name": "User"},
      {"id": 1, "name": "Power User", "interval": "P4W", "uploaded": "50GiB", "ratio": 1.05, "isKept": true}
    ]
  }
}
```

插件版本变化时会清除全部用户覆盖并恢复新版内置规则，不进行字段合并。

## 刷新说明

单站刷新和全站刷新都复用 MoviePilot 的 `SiteChain`。全站刷新默认关闭；开启后页面才显示红色按钮。刷新会真实访问 PT 站点，并可能正常触发 MoviePilot 的站点消息和低分享率提醒。

## 开发期更新规则

在 PT-depiler 仓库安装锁定依赖后运行：

```shell
node scripts/sync_ptdepiler_rules.mjs /path/to/PT-depiler
```

第三方许可见 `THIRD_PARTY_NOTICES.md`。
