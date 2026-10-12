// 等级定义不会执行 DOM 查询；该桩只用于阻止 sizzle 在 Node.js 中访问 window。
export default function sizzle() {
  return [];
}
