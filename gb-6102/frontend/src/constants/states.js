export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
});

export const RetentionStates = Object.freeze({
  active: "正常",
  pending: "待清理",
  cleaning: "清理中",
  cleaned: "已清理",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  seal_case: "封存受理", clean_case: "清理原文",
});
