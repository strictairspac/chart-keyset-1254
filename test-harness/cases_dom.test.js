// 前端封存/清理界面冒烟测试：用 jsdom 渲染真实页面模块，API 用内存桩替代。
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", {url: "http://localhost/"});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Node = dom.window.Node;
globalThis.sessionStorage = dom.window.sessionStorage;
globalThis.location = dom.window.location;
globalThis.HashChangeEvent = dom.window.HashChangeEvent || dom.window.Event;
globalThis.Event = dom.window.Event;

const FRONT = "file:///workspace/gb-6102/frontend/src";
const config = {
  modules: ["勘误交班", "签发与收录", "翻页回放", "勘误核收单"],
  unit: "勘误收录批次", fixture: "CN-1124-航标勘误",
  parameter_label: "每页条数", max_parameter: 50, default_parameter: 2,
};
const payload = {initial: [{seq: 1, issue_date: "2026-11-20", chart: "C", notice: "n"}], arrivals: []};
const cases = [
  {id: 1, code: "CASE-1", title: "正常资料", description: "在用", payload, created_by: "operator",
   sealed: false, sealed_at: null, sealed_by: null, retention_state: "active",
   retention_requested_at: null, retention_cleaned_at: null},
  {id: 2, code: "CASE-2", title: "封存待清理", description: "等待在途结束", payload, created_by: "operator",
   sealed: true, sealed_at: 1795000000, sealed_by: "operator", retention_state: "pending",
   retention_requested_at: 1795000000, retention_cleaned_at: null},
  {id: 3, code: "CASE-3", title: "已清理资料", description: "原文已清", payload: null, created_by: "operator",
   sealed: true, sealed_at: 1794000000, sealed_by: "operator", retention_state: "cleaned",
   retention_requested_at: 1794000000, retention_cleaned_at: 1794000100},
];
const calls = [];
globalThis.fetch = async (path, options = {}) => {
  calls.push({path, method: options.method || "GET"});
  const url = path.replace(/^\/api/, "");
  if (url === "/cases") return {ok: true, json: async () => cases};
  const seal = url.match(/^\/cases\/(\d+)\/seal$/);
  if (seal) {
    const item = cases.find(c => c.id === Number(seal[1]));
    item.sealed = true;
    item.sealed_at = 1795000200;
    item.sealed_by = "operator";
    item.retention_state = "pending";
    item.retention_requested_at = 1795000200;
    return {ok: true, json: async () => item};
  }
  throw new Error(`未预期的请求 ${url}`);
};

const { casesPage } = await import(`${FRONT}/pages/cases.js`);
const failures = [];
const check = (name, ok) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${name}`);
  if (!ok) failures.push(name);
};
const render = async (user, id) =>
  document.body.replaceChildren(await casesPage(config, user, {name: "cases", id}));

// 操作员查看正常资料：可接单、可封存。
await render({role: "operator"}, 1);
check("目录渲染三种清理状态徽标",
  document.querySelector('[data-retention="active"]')?.textContent === "正常" &&
  document.querySelector('[data-retention="pending"]')?.textContent === "待清理" &&
  document.querySelector('[data-retention="cleaned"]')?.textContent === "已清理");
check("正常资料可提交执行", !!document.querySelector(".queue-form"));
check("正常资料出现封存按钮", document.querySelector(".seal-button")?.textContent.includes("受理封存"));
check("正常资料展示业务原文", !!document.querySelector("pre"));

// 点击封存：调用封存接口。
const before = calls.length;
document.querySelector(".seal-button").click();
await new Promise(resolve => setTimeout(resolve, 0));
check("点击后调用封存接口",
  calls.slice(before).some(c => c.method === "POST" && c.path === "/api/cases/1/seal"));

// 重渲染后，该资料变为待清理、接单与封存入口都消失。
await render({role: "operator"}, 1);
check("封存后状态刷新为待清理",
  document.querySelector(".case-split [data-retention]")?.dataset.retention === "pending");
check("封存后无封存按钮", !document.querySelector(".seal-button"));
check("封存后停止接单且无接单表单",
  !document.querySelector(".queue-form") && document.body.textContent.includes("停止接单"));
check("封存待清理期间原文仍可查看", !!document.querySelector("pre"));

// 已清理资料：原文区改为提示，不能接单。
await render({role: "operator"}, 3);
check("已清理资料展示原文已清提示", !!document.querySelector('[data-testid="payload-purged"]'));
check("已清理资料无原文 pre", !document.querySelector(".case-split pre"));
check("已清理资料无接单表单", !document.querySelector(".queue-form"));

// 只读观察员：看不到封存按钮和接单表单，但状态与已清理提示仍在。
Object.assign(cases[0], {
  sealed: false, sealed_at: null, sealed_by: null, retention_state: "active",
  retention_requested_at: null,
});
await render({role: "viewer"}, 1);
check("观察员无封存按钮", !document.querySelector(".seal-button"));
check("观察员无接单表单", !document.querySelector(".queue-form"));
check("观察员仍可见三种清理状态",
  new Set([...document.querySelectorAll("[data-retention]")].map(n => n.dataset.retention))
    .size === 3);
await render({role: "viewer"}, 3);
check("观察员可见原文已清提示", !!document.querySelector('[data-testid="payload-purged"]'));

if (failures.length) {
  console.error(`\n${failures.length} 项失败：${failures.join("、")}`);
  process.exit(1);
}
console.log("\n前端冒烟测试全部通过");
