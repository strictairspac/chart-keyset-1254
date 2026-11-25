import { api } from "../api/workbench.js";
import { RetentionStates } from "../constants/states.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { table } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { navigate } from "../router/router.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";

function retentionBadge(item) {
  return el("span", {
    class: `status state-retention-${item.retention_state}`,
    "data-retention": item.retention_state,
  }, RetentionStates[item.retention_state] || item.retention_state);
}

function sealButton(selected) {
  if (selected.sealed) {
    return el("p", {class: "seal-note"},
      `封存于 ${time(selected.sealed_at)}（${selected.sealed_by}），已停止接单，等待在途批次结束后清理原文。`);
  }
  const issue = errorText();
  const button = el("button", {type: "button", class: "seal-button"}, "受理封存并停止接单");
  button.addEventListener("click", async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await api.sealCase(selected.id);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (error) {
      issue.textContent = error.message;
      button.disabled = false;
    }
  });
  return el("div", {class: "seal-row"}, button, issue);
}

function queueForm(config, selected) {
  const form = el("form", {class: "queue-form"},
    field(config.parameter_label, el("input", {
      name: "parameter", type: "number", min: 1, max: config.max_parameter, required: true, value: config.default_parameter,
    })),
    el("button", {type: "submit"}, "提交执行"), errorText());
  submit(form, async values => {
    const run = await api.enqueue(selected.id, {parameter: Number(values.get("parameter"))});
    navigate(`runs/${run.id}`);
  });
  return form;
}

function registration(config, selected) {
  const data = selected?.payload ? JSON.stringify(selected.payload, null, 2) : "{}";
  const form = el("form", {id: "case-form"},
    field("名称", el("input", {name: "title", required: true, minlength: 2, maxlength: 120, value: selected ? `${selected.title} · 复演` : ""})),
    field("说明", el("input", {name: "description", maxlength: 420, value: selected?.description || ""})),
    field(`${config.unit}内容`, el("textarea", {name: "payload", spellcheck: "false", required: true, rows: 12}, data),
      selected ? "可以保留演示数据，也可以编辑 JSON 后保存。" : "输入业务 JSON。"),
    errorText(), el("button", {type: "submit"}, "保存资料"));
  submit(form, async values => {
    let payload;
    try {
      payload = JSON.parse(values.get("payload"));
    } catch {
      throw new Error("资料 JSON 无法解析，请检查括号、引号和逗号");
    }
    const entry = await api.createCase({title: values.get("title"), description: values.get("description"), payload});
    navigate(`cases/${entry.id}`);
  });
  return form;
}

function payloadPanel(selected, operator) {
  if (!selected.payload) {
    return el("details", {open: true}, el("summary", {}, "查看业务数据"),
      el("p", {class: "empty", "data-testid": "payload-purged"},
        "录入原文已清理。已完成批次的核收证据、摘要与签署仍可在核收单页面查阅。"));
  }
  const details = el("details", {}, el("summary", {}, "查看业务数据"), el("pre", {}, JSON.stringify(selected.payload, null, 2)));
  if (!operator) return details;
  return el("div", {}, details, sealButton(selected));
}

export async function casesPage(config, user, route) {
  const operator = user.role === "operator";
  async function render() {
    const cases = await api.cases();
    const selected = cases.find(item => item.id === route.id) ?? cases.at(-1);
    return el("div", {class: "page"},
      pageHeading(config.modules[1], `${config.unit} · ${config.fixture}`),
      section("资料目录", cases.length ? table(["编号", "名称", "说明", "清理状态", "操作"], cases.map(item =>
        [item.code, item.title, item.description, retentionBadge(item),
          el("a", {href: `#cases/${item.id}`, "aria-current": item.id === selected?.id ? "true" : null}, "选用")]), "资料目录") :
        empty("这里还没有资料。")),
      el("div", {class: "split case-split"},
        section(selected ? selected.title : "等待选择资料", selected ?
          [el("p", {}, selected.description), retentionBadge(selected),
            selected.sealed ? empty("资料已封存，停止接单；在途复演按原件执行到结束。") :
              operator ? queueForm(config, selected) : empty("当前账号可查看此资料。"),
            payloadPanel(selected, operator)] :
          empty("先登记一条资料，再提交执行。")),
        operator ? section("登记新资料", registration(config, selected)) : null));
  }
  return render();
}
