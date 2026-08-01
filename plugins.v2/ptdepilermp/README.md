# PT站点等级监控（PTDepilerMp）

基于 MoviePilot V2 已保存的站点用户数据，展示当前等级、上传/下载量、分享率、入站时间、下一等级要求和已知缺口。达到规则中的保号等级，或当前属于 VIP/管理等特殊等级时，会显示绿色“已保号”标识。

## 数据与规则

- 站点数据只读取 MoviePilot 的最新快照，插件不读取 Cookie，也不自行抓取站点。
- 每个站点的等级配置独立保存在 `site_rules/<MoviePilot站点名称>.json`。
- 插件生成详情页或仪表板数据时会重新扫描 `site_rules`，因此修改、新增或删除 JSON 后刷新页面即可读取；若宿主仍显示缓存，重新加载一次插件。
- 顶层 `name` 必须与 JSON 文件名、MoviePilot 数据库 `site.name` 一致；匹配时忽略大小写和首尾空白。
- 规则不保存站点数字 ID、域名或主机名指纹。
- MoviePilot 未采集的条件显示“数据不足”，不会被当作 0 或错误宣称达标。

站点规则采用完整替换模型，不跨文件合并。无效 JSON 会被跳过，并在详情页显示无效文件数量。

常用字段说明见 `site_rules/1demo.json`。它和其他规则一样会被加载，但只有 MoviePilot 中存在名为 `1demo` 的站点时才会匹配。

## 新增或修改站点规则

复制一份现有 JSON，以 MoviePilot 站点管理中显示的名称命名文件，并把同一名称写入顶层 `name`：

```json
{
  "name": "MySite",
  "is_dead": false,
  "levels": [
    {"id": 0, "name": "User"},
    {"id": 1, "name": "Power User", "interval": "P4W", "uploaded": "50GiB", "ratio": 1.05, "isKept": true}
  ]
}
```

上述配置必须保存为 `MySite.json`。详细方法见 [站点等级规则维护指南](./SITE_RULES_MAINTENANCE.md)。插件更新可能替换安装目录，手工新增的文件应自行备份。

## 刷新说明

单站刷新和全站刷新都复用 MoviePilot 的 `SiteChain`。全站刷新默认关闭；开启后页面才显示红色按钮。刷新会真实访问 PT 站点，并可能正常触发 MoviePilot 的站点消息和低分享率提醒。

首版规则曾参考 PT-depiler，第三方许可见 `THIRD_PARTY_NOTICES.md`；后续规则由本插件目录独立手工维护，不再提供自动同步或运行时关联。
