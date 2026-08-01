# 站点等级规则维护指南

本文记录 PT-depiler 维护等级定义的组织方式，并说明如何把有效规则手工整理为 PTDepilerMp 的单站 JSON。它仅供维护时参考；插件不会读取 PT-depiler 源码、访问其仓库或自动同步规则。

## PT-depiler 如何组织等级规则

PT-depiler 的站点定义位于 `src/packages/site/definitions/`，通常每个站点一个 TypeScript 文件，并导出 `siteMetadata`。等级数据位于 `siteMetadata.levelRequirements`。

部分站点不会把全部等级直接写在定义文件中，而是通过对象 spread 继承 `src/packages/site/schemas/` 中的公共规则，再覆盖名称、门槛或保号状态。因此维护时应查看最终展开后的 `levelRequirements`，不能只复制定义文件中肉眼可见的局部数组。

等级字段类型集中定义在 `src/packages/site/types/userinfo.ts` 的 `ILevelRequirement`。常用字段包括：

- `id`：等级顺序标识；普通等级按递增顺序排列。
- `name`、`nameAka`：等级名称和别名，用于匹配 MoviePilot 采集到的当前等级。
- `groupType`：`user`、`vip` 或 `manager`。VIP 和管理等级属于特殊等级。
- `isKept`：达到该普通等级后是否视为保号。
- `interval`：注册时长，使用 ISO 8601 日期区间，例如 `P4W`、`P6M`、`P1Y`。
- `uploaded`、`downloaded`、`totalTraffic`、`seedingSize`：容量条件，可使用 `GiB`、`TiB` 等单位。
- `ratio`：最低分享率；二元素数组表示最小值和最大值区间。
- `bonus`、`seedingBonus`、`seeding`、`uploads`、`snatches` 等：最低数值条件。
- `hnrUnsatisfied`：允许的未解决 H&R 最大值，是上限条件。
- `alternative`：可选条件数组，其中任意一项满足即可；它与同等级的其他顶层条件仍为 AND 关系。

PT-depiler 判断当前等级时，先规范化等级名称并匹配 `name/nameAka`；匹配不到时再识别 VIP/管理关键字。下一等级取当前等级之后的普通等级，再分别计算时间、容量、分享率和其他条件缺口。

## 在 PT-depiler 中维护时的常见步骤

1. 找到对应的 `definitions/<站点标识>.ts` 及其引用的 schema。
2. 修改或补充 `siteMetadata.levelRequirements`，必要时使用 spread 复用公共等级模板。
3. 确保 `id` 顺序、`name/nameAka`、`groupType` 和 `isKept` 与站点实际显示一致。
4. 检查所有容量单位、ISO 时长、分享率区间、H&R 上限和 `alternative` 结构。
5. 若该项目的站点定义使用 `version` 标识定义版本，同步递增版本。
6. 使用其 TypeScript 检查、格式化和站点规则相关测试验证变更。

PT-depiler 还允许用户在站点配置的 `merge.levelRequirements` 中覆盖等级数组。该行为属于浏览器扩展自身的运行时配置，不应直接复制到 PTDepilerMp；本插件以单个 JSON 文件作为一份完整规则。

## 手工更新 PTDepilerMp

1. 复制 `site_rules/1demo.json` 或结构接近的真实站点文件；使用模板时删除全部 `_说明` 字段。
2. 将 TypeScript 中 spread、公共常量和算术表达式展开成最终 JSON 值。
3. 只保留本插件支持的静态字段，不复制抓取选择器、Cookie、用户脚本、Vue 配置或网络请求逻辑。
4. 查询 MoviePilot `site` 表的 `name` 字段，让 JSON 文件名和顶层 `name` 使用完全相同的名称。
5. 顶层至少包含 `name`、`levels`；不要保存站点数字 ID、域名或主机名指纹。
6. 保存后刷新插件详情页，检查规则匹配数、当前等级、下一等级和完整规则列表。
7. 运行 JSON、Python 和目标测试校验。

顶层完整结构：

```json
{
  "name": "MySite",
  "is_dead": false,
  "levels": []
}
```

以上配置必须保存为 `MySite.json`。插件匹配时会忽略名称大小写和首尾空白，但文件名、顶层 `name` 和数据库名称仍建议完全一致。`is_dead: true` 表示保留规则文件但禁止应用。

`1demo.json` 的说明值不是可运行的等级门槛，不能只改文件名后直接使用；必须按说明替换为正确的字符串、数字、布尔值、数组或对象。模板和其他规则一样会被加载，但通常不会匹配到 MoviePilot 站点。

不要把站点地址、Cookie、Passkey、Token 或其他认证信息写入规则文件或提交到仓库。

## 校验命令

```shell
python3 -m json.tool plugins.v2/ptdepilermp/site_rules/<MoviePilot站点名称>.json >/dev/null
python3 -m py_compile plugins.v2/ptdepilermp/__init__.py plugins.v2/ptdepilermp/rules.py
python3 -m pytest tests/v2/ptdepilermp
git diff --check
```
