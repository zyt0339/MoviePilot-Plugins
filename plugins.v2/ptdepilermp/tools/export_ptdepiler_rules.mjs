#!/usr/bin/env node

/**
 * 从 PT-depiler 的 siteMetadata.levelRequirements 导出 PTDepilerMp 规则。
 *
 * 这里通过 PT-depiler 自带的 Vite 环境执行 TypeScript 定义，而不是解析源码文本，
 * 因而可以正确处理 import、继承和对象 spread。使用 --apply 时批量覆盖正式
 * site_rules 中的同名规则并加入新规则，随后通过 Git diff 审核人工修正是否需恢复。
 */

import { access, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const pluginDirectory = resolve(scriptDirectory, "..");
const liveRulesDirectory = resolve(pluginDirectory, "site_rules");
const defaultSourceDirectory = resolve(pluginDirectory, "../../../..", "PT-depiler");
const defaultOutputDirectory = resolve(pluginDirectory, "site_rules.exported");
const nameOverridesFile = resolve(scriptDirectory, "site_name_overrides.json");
const sizzleStubFile = resolve(scriptDirectory, "sizzle_stub.mjs");

const LEVEL_FIELDS = [
  "id",
  "name",
  "nameAka",
  "groupType",
  "isKept",
  "privilege",
  "interval",
  "totalTraffic",
  "downloaded",
  "trueDownloaded",
  "uploaded",
  "trueUploaded",
  "ratio",
  "trueRatio",
  "bonus",
  "bonusPerHour",
  "seedingBonus",
  "seedingBonusPerHour",
  "seeding",
  "seedingSize",
  "specialSeedingSize",
  "seedingTime",
  "averageSeedingTime",
  "uploads",
  "leeching",
  "snatches",
  "posts",
  "adoptions",
  "perfectFlacs",
  "groups",
  "hnrUnsatisfied",
  "alternative",
];

function printUsage() {
  console.log(`用法：
  node export_ptdepiler_rules.mjs [--source PT-depiler目录] --apply
  node export_ptdepiler_rules.mjs [--source PT-depiler目录] [--output 审阅目录]

默认 PT-depiler：${defaultSourceDirectory}
默认输出目录：${defaultOutputDirectory}

--apply 会覆盖正式 site_rules 中的同名规则并加入新规则，但不会删除本地独有文件。
不使用 --apply 时，输出目录必须为空且不能是正式 site_rules 目录。`);
}

function parseArguments(argv) {
  const options = {
    source: defaultSourceDirectory,
    output: defaultOutputDirectory,
    outputSpecified: false,
    apply: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      printUsage();
      process.exit(0);
    }
    if (argument === "--apply") {
      options.apply = true;
      continue;
    }
    if (argument !== "--source" && argument !== "--output") {
      throw new Error(`未知参数：${argument}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`${argument} 缺少路径`);
    }
    options[argument.slice(2)] = resolve(value);
    if (argument === "--output") options.outputSpecified = true;
    index += 1;
  }
  if (options.apply && options.outputSpecified) {
    throw new Error("--apply 与 --output 不能同时使用");
  }
  if (options.apply) options.output = liveRulesDirectory;
  return options;
}

function cloneJsonValue(value, path) {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error(`${path} 包含非有限数值`);
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => cloneJsonValue(item, `${path}[${index}]`));
  }
  if (typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .map(([key, item]) => [key, cloneJsonValue(item, `${path}.${key}`)]),
    );
  }
  throw new Error(`${path} 不是可导出的静态 JSON 值`);
}

function cleanLevel(level, siteId, levelIndex) {
  if (!level || typeof level !== "object" || Array.isArray(level)) {
    throw new Error(`${siteId}.levelRequirements[${levelIndex}] 不是对象`);
  }
  const cleaned = {};
  for (const field of LEVEL_FIELDS) {
    if (level[field] !== undefined) {
      cleaned[field] = cloneJsonValue(
        level[field],
        `${siteId}.levelRequirements[${levelIndex}].${field}`,
      );
    }
  }
  if (cleaned.id === undefined || typeof cleaned.name !== "string" || !cleaned.name.trim()) {
    throw new Error(`${siteId}.levelRequirements[${levelIndex}] 缺少 id 或 name`);
  }
  return cleaned;
}

function assertSafeRuleName(name, siteId) {
  if (typeof name !== "string" || !name.trim()) {
    throw new Error(`${siteId} 无有效站点名称`);
  }
  if (/[\\/\0]/u.test(name) || name === "." || name === "..") {
    throw new Error(`${siteId} 的站点名称不能作为安全文件名：${name}`);
  }
}

async function prepareOutputDirectory(options) {
  if (options.apply) {
    await mkdir(liveRulesDirectory, { recursive: true });
    return;
  }
  if (resolve(options.output) === liveRulesDirectory) {
    throw new Error("禁止直接覆盖正式 site_rules，请先导出到审阅目录");
  }
  await mkdir(options.output, { recursive: true });
  const existingFiles = await readdir(options.output);
  if (existingFiles.length > 0) {
    throw new Error(`输出目录不是空目录：${options.output}`);
  }
}

async function loadPtDepilerMetadata(sourceDirectory, definitionIds) {
  const viteEntry = resolve(sourceDirectory, "node_modules/vite/dist/node/index.js");
  try {
    await access(viteEntry, constants.R_OK);
  } catch {
    throw new Error(
      `未找到 PT-depiler 依赖，请先在 ${sourceDirectory} 执行 pnpm install --frozen-lockfile`,
    );
  }

  // 部分定义导入浏览器扩展公共模块；导出等级规则时只需最小运行环境。
  globalThis.chrome ??= { runtime: { id: "ptdepilermp-rule-export" } };
  globalThis.browser ??= globalThis.chrome;

  const { runnerImport } = await import(pathToFileURL(viteEntry).href);
  const virtualModuleId = "virtual:ptdepilermp-rule-export";
  const resolvedVirtualModuleId = "\0ptdepilermp-rule-export";
  const imports = definitionIds.map(
    (siteId, index) =>
      `import { siteMetadata as metadata${index} } from ${JSON.stringify(
        `/src/packages/site/definitions/${siteId}.ts`,
      )};`,
  );
  const exports = definitionIds.map(
    (siteId, index) => `{ id: ${JSON.stringify(siteId)}, metadata: metadata${index} }`,
  );

  const viteConfig = {
    root: sourceDirectory,
    configFile: false,
    appType: "custom",
    optimizeDeps: { noDiscovery: true },
    define: {
      __BROWSER__: JSON.stringify("chrome"),
      __EXT_VERSION__: JSON.stringify("ptdepilermp-rule-export"),
      __GIT_VERSION__: JSON.stringify({
        short: "export",
        long: "export",
        date: 0,
        count: 0,
        branch: "export",
      }),
      __BUILD_TIME__: "0",
      __RESOURCE_SITE_ICONS__: JSON.stringify([]),
    },
    plugins: [
      {
        name: "ptdepilermp-rule-export",
        resolveId(id) {
          if (id === virtualModuleId) return resolvedVirtualModuleId;
          return undefined;
        },
        load(id) {
          if (id === resolvedVirtualModuleId) {
            return `${imports.join("\n")}\nexport default [${exports.join(",")}];`;
          }
          return undefined;
        },
      },
    ],
    resolve: {
      alias: [
        { find: /^sizzle$/, replacement: sizzleStubFile },
        { find: "~", replacement: resolve(sourceDirectory, "src") },
        { find: "@", replacement: resolve(sourceDirectory, "src/entries") },
        { find: "@ptd", replacement: resolve(sourceDirectory, "src/packages") },
      ],
    },
  };

  const result = await runnerImport(virtualModuleId, viteConfig);
  return result.module.default;
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const definitionsDirectory = resolve(options.source, "src/packages/site/definitions");
  const definitionIds = (await readdir(definitionsDirectory))
    .filter((fileName) => fileName.endsWith(".ts"))
    .map((fileName) => fileName.slice(0, -3))
    .sort((left, right) => left.localeCompare(right, "en"));
  const nameOverrides = JSON.parse(await readFile(nameOverridesFile, "utf8"));

  await prepareOutputDirectory(options);
  const definitions = await loadPtDepilerMetadata(options.source, definitionIds);
  const usedNames = new Map();
  const skipped = [];
  const exportedRules = [];

  for (const { id, metadata } of definitions) {
    const levels = metadata?.levelRequirements;
    if (!Array.isArray(levels) || levels.length === 0) {
      skipped.push(id);
      continue;
    }

    const ruleName = nameOverrides[id] ?? metadata.name ?? id;
    assertSafeRuleName(ruleName, id);
    const normalizedName = ruleName.trim().toLocaleLowerCase("en");
    if (usedNames.has(normalizedName)) {
      throw new Error(`站点名称冲突：${usedNames.get(normalizedName)} 与 ${id} 都输出为 ${ruleName}`);
    }
    usedNames.set(normalizedName, id);

    const rule = {
      name: ruleName,
      is_dead: metadata.isDead === true,
      levels: levels.map((level, index) => cleanLevel(level, id, index)),
    };
    exportedRules.push({ ruleName, rule });
  }

  // 全部规则验证成功后再开始写文件，避免单条坏规则造成只更新一半。
  for (const { ruleName, rule } of exportedRules) {
    await writeFile(resolve(options.output, `${ruleName}.json`), `${JSON.stringify(rule, null, 2)}\n`, "utf8");
  }

  console.log(`PT-depiler 定义文件：${definitionIds.length}`);
  console.log(`已${options.apply ? "覆盖或新增" : "导出"}规则：${exportedRules.length}`);
  console.log(`无等级规则并跳过：${skipped.length}`);
  console.log(`输出目录：${options.output}`);
  if (options.apply) {
    console.log("未删除上游缺失的本地规则；请使用 Git status 和 Git diff 审核变更。");
  } else {
    console.log("这是审阅导出，正式 site_rules 未被修改。");
  }
}

main().catch((error) => {
  console.error(`导出失败：${error.stack ?? error.message}`);
  process.exitCode = 1;
});
