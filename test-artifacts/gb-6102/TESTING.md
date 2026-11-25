# 验收记录 · 封存与清理（gb-6102）

本环境无 Docker 与第三方 Python 依赖，验证分两层进行：后端状态机用标准库
sqlite3 等价复刻生产表结构与转换逻辑，前端用 jsdom 渲染真实页面模块。
生产代码本身仍面向 MySQL 8 / PostgreSQL，迁移保持幂等。

## 运行方式

```sh
python3 -I test-harness/test_retention_sqlite.py
node test-harness/cases_dom.test.js   # jsdom 依赖安装在隔离的 /tmp/domtest
```

## 后端状态机（26 项，全部通过）

| 验收点 | 对应用户口径 |
|---|---|
| 封存后 `sealed=1`、`retention_state=pending`，再提交返回 409 | 封存申请当场受理并停止该资料接单 |
| 封存 A 不影响 B 入队 | 其他资料照常排队 |
| 同一资料反复封存，`cleanup_jobs` 只有一条 | 重试封存不重复登记 |
| 接单写入 `runs.payload_snapshot`，清理后仍等于原件 | 接下的单用原件做到结束 |
| 有 queued 批次时任务不可认领、原文保留 | 最后一单结束后才能清理 |
| 末单 done/failed 后清空 `cases.payload`，任务 cleaned | 到期原文被清掉 |
| runs 的 result/digest/snapshot 与 reviews 不受影响 | 已签字核收证据仍可查 |
| 认领提交后、清原文前崩溃，重启重入完成清理且不重复登记 | 清理中断可恢复 |
| cleaning 中断时若又有在途批次，复位 pending | 不提前清理 |
| 队头被在途批次挡住时，后续资料任务可被认领清理 | 多资料互不饿死 |

## 前端界面（16 项，全部通过）

- 资料目录与详情均显示清理状态徽标：正常 / 待清理 / 清理中 / 已清理。
- 操作员在未封存资料看到「受理封存并停止接单」，点击调用 `POST /cases/{id}/seal`。
- 封存后接单表单与封存按钮消失，出现停止接单说明；待清理期间原文仍可查看。
- 已清理资料业务 JSON 区替换为「录入原文已清理」提示，核收证据入口保留。
- 只读观察员看不到封存按钮与接单表单，但状态徽标与已清理提示仍可见。

## 未在本环境执行的部分（需 docker compose 栈）

- MySQL 8 真实行锁 `FOR UPDATE SKIP LOCKED` 与 v002 加列迁移的实库验证。
- FastAPI 端到端鉴权（operator/reviewer/viewer）与 OpenAPI 联调。
- worker 进程 kill -9 后由容器重启拾取 `cleaning` 任务的真机演练。
