"""封存/清理状态机验证（纯标准库 sqlite3，无第三方依赖）。

运行：python3 -I test_retention_sqlite.py
表结构、状态转换与 services/retention.py、services/runs.py 的生产逻辑一一对应：
- 封存：sealed=1、retention_state=pending，并幂等登记 cleanup_jobs；
- 接单：封存/已清理拒绝，接单时把原文写入 runs.payload_snapshot；
- 清理 worker：仅当该资料无 queued 批次时清空 cases.payload，证据保留；
- 中断恢复：cleaning 任务重启后继续，在途批次未结束则复位 pending。
"""
import json
import sqlite3
import sys
from time import time

PAYLOAD = {
    "initial": [
        {"seq": 101, "issue_date": "2026-11-19", "chart": "CN-1", "notice": "甲"},
        {"seq": 102, "issue_date": "2026-11-20", "chart": "CN-1", "notice": "乙"},
    ],
    "arrivals": [{"seq": 201, "issue_date": "2026-11-24", "chart": "CN-2", "notice": "丙"}],
}
failures = []


def check(name, condition, detail=""):
    if not condition:
        failures.append(name)
        print(f"FAIL {name} {detail}")
    else:
        print(f"ok   {name}")


def db():
    con = sqlite3.connect(":memory:")
    con.execute("""CREATE TABLE cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
        description TEXT NOT NULL, payload TEXT, created_by TEXT NOT NULL,
        sealed INTEGER NOT NULL DEFAULT 0, sealed_at INTEGER, sealed_by TEXT,
        retention_state TEXT NOT NULL DEFAULT 'active',
        retention_requested_at INTEGER, retention_cleaned_at INTEGER)""")
    con.execute("""CREATE TABLE runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL REFERENCES cases(id),
        status TEXT NOT NULL, parameter INTEGER NOT NULL, actor TEXT NOT NULL,
        created_at INTEGER NOT NULL, finished_at INTEGER, result TEXT, failure TEXT,
        digest TEXT, payload_snapshot TEXT)""")
    con.execute("""CREATE TABLE cleanup_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL UNIQUE REFERENCES cases(id),
        state TEXT NOT NULL, actor TEXT NOT NULL, created_at INTEGER NOT NULL,
        claimed_at INTEGER, finished_at INTEGER, attempts INTEGER NOT NULL DEFAULT 0)""")
    return con


def make_case(con, code):
    cur = con.execute(
        "INSERT INTO cases (code, title, description, payload, created_by, sealed, retention_state) "
        "VALUES (?, ?, ?, ?, ?, 0, 'active')",
        (code, code, "d", json.dumps(PAYLOAD), "operator"))
    return cur.lastrowid


class Conflict(Exception):
    def __init__(self, status):
        self.status = status


def enqueue(con, case_id, actor="operator"):
    # 对应 runs.enqueue：行锁读资料 → 封存/已清理拒绝 → 快照原件后入队。
    row = con.execute("SELECT sealed, retention_state, payload FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise Conflict(404)
    sealed, retention_state, payload = row
    if sealed:
        raise Conflict(409)
    if retention_state == "cleaned":
        raise Conflict(409)
    cur = con.execute(
        "INSERT INTO runs (case_id, status, parameter, actor, created_at, payload_snapshot) "
        "VALUES (?, 'queued', 2, ?, ?, ?)",
        (case_id, actor, int(time()), payload))
    return cur.lastrowid


def seal(con, case_id, actor="operator"):
    # 对应 retention.seal：幂等，case_id 唯一保证清理任务不重复登记。
    row = con.execute("SELECT sealed FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise Conflict(404)
    now = int(time())
    if not row[0]:
        con.execute(
            "UPDATE cases SET sealed = 1, sealed_at = ?, sealed_by = ?, "
            "retention_state = 'pending', retention_requested_at = ? WHERE id = ?",
            (now, actor, now, case_id))
        con.execute(
            "INSERT OR IGNORE INTO cleanup_jobs (case_id, state, actor, created_at) "
            "VALUES (?, 'pending', ?, ?)", (case_id, actor, now))


def service_one(con, crash_after_claim=False):
    # 对应 retention.service_one：独立事务认领（pending→cleaning 提交），
    # 再用新事务清空原文；crash_after_claim 模拟两步之间服务重启。
    job = con.execute(
        "SELECT j.id, j.case_id, j.state, j.actor, j.claimed_at, j.attempts "
        "FROM cleanup_jobs j WHERE j.state IN ('pending','cleaning') AND ("
        "j.state = 'cleaning' OR 0 = ("
        "SELECT COUNT(*) FROM runs r WHERE r.case_id = j.case_id AND r.status = 'queued')) "
        "ORDER BY j.id LIMIT 1").fetchone()
    if job is None:
        return False
    job_id, case_id, state, actor, claimed_at, attempts = job
    case = con.execute("SELECT retention_state FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        con.execute("UPDATE cleanup_jobs SET state = 'cleaned', claimed_at = COALESCE(claimed_at, ?), "
                    "finished_at = ?, attempts = attempts + 1 WHERE id = ?", (int(time()), int(time()), job_id))
        return True
    if state == "cleaning" and con.execute(
            "SELECT COUNT(*) FROM runs WHERE case_id = ? AND status = 'queued'", (case_id,)).fetchone()[0]:
        con.execute("UPDATE cleanup_jobs SET state = 'pending', claimed_at = NULL WHERE id = ?", (job_id,))
        return True
    if state == "pending":
        claim_time = int(time())
        con.execute("UPDATE cleanup_jobs SET state = 'cleaning', claimed_at = ?, attempts = attempts + 1 WHERE id = ?",
                    (claim_time, job_id))
        claimed_at = claim_time
        con.commit()
        if crash_after_claim:
            return "crashed"
    # 第二事务：双保险复查在途批次，再清原文。
    if con.execute("SELECT COUNT(*) FROM runs WHERE case_id = ? AND status = 'queued'", (case_id,)).fetchone()[0]:
        con.execute("UPDATE cleanup_jobs SET state = 'pending', claimed_at = NULL WHERE id = ?", (job_id,))
        return True
    now = int(time())
    con.execute("UPDATE cases SET payload = NULL, retention_state = 'cleaned', retention_cleaned_at = ? WHERE id = ?",
                (now, case_id))
    con.execute("UPDATE cleanup_jobs SET state = 'cleaned', claimed_at = ?, finished_at = ?, "
                "attempts = attempts + 1 WHERE id = ?", (claimed_at, now, job_id))
    return True


def drain(con):
    # 对应 worker 每轮巡检：被挡住的 pending 不在可认领集合内，循环必然收敛。
    moved = False
    while service_one(con):
        moved = True
    return moved


def finish(con, run_id, status, result=None, digest=None, failure=None):
    con.execute("UPDATE runs SET status = ?, result = ?, digest = ?, failure = ?, finished_at = ? WHERE id = ?",
                (status, json.dumps(result) if result is not None else None, digest, failure, int(time()), run_id))


# 1. 封存当场受理并停止接单；其他资料照常排队。
con = db()
c1, c2 = make_case(con, "A"), make_case(con, "B")
seal(con, c1)
row = con.execute("SELECT sealed, retention_state FROM cases WHERE id = ?", (c1,)).fetchone()
check("封存后立即停止接单标记", row == (1, "pending"))
try:
    enqueue(con, c1)
    check("封存资料拒绝新单", False)
except Conflict as exc:
    check("封存资料拒绝新单", exc.status == 409)
rid = enqueue(con, c2)
check("其他资料照常排队", con.execute("SELECT status FROM runs WHERE id = ?", (rid,)).fetchone()[0] == "queued")

# 2. 重试封存不重复登记。
seal(con, c1)
seal(con, c1)
check("重试封存不重复登记", con.execute(
    "SELECT COUNT(*) FROM cleanup_jobs WHERE case_id = ?", (c1,)).fetchone()[0] == 1)

# 3. 复演与封存并发：接单快照原件，最后一单结束后才清理。
c3 = make_case(con, "C")
r1, r2 = enqueue(con, c3), enqueue(con, c3)
snap = con.execute("SELECT payload_snapshot FROM runs WHERE id = ?", (r1,)).fetchone()[0]
check("接单时留存原件快照", json.loads(snap) == PAYLOAD)
seal(con, c3)
drain(con)  # c3 有在途批次，其任务不在可认领集合；其它资料的任务照常推进
check("等待期间原文保留", con.execute("SELECT payload FROM cases WHERE id = ?", (c3,)).fetchone()[0] is not None)
check("等待任务保持 pending", con.execute(
    "SELECT state FROM cleanup_jobs WHERE case_id = ?", (c3,)).fetchone()[0] == "pending")
finish(con, r1, "done", {"summary": "done"}, "abc")
drain(con)
check("最后一单未结束不清理", con.execute("SELECT payload FROM cases WHERE id = ?", (c3,)).fetchone()[0] is not None)
finish(con, r2, "failed", failure="输入不合法")
drain(con)
case_row = con.execute("SELECT payload, retention_state FROM cases WHERE id = ?", (c3,)).fetchone()
job_row = con.execute("SELECT state, finished_at FROM cleanup_jobs WHERE case_id = ?", (c3,)).fetchone()
done_run = con.execute("SELECT result, digest, payload_snapshot FROM runs WHERE id = ?", (r1,)).fetchone()
check("最后一单结束后清理原文", case_row[0] is None and case_row[1] == "cleaned")
check("清理任务已收尾", job_row[0] == "cleaned" and job_row[1] is not None)
check("已签字核收证据仍可查", json.loads(done_run[0]) == {"summary": "done"} and done_run[1] == "abc")
check("批次仍保留原件快照", json.loads(done_run[2]) == PAYLOAD)

# 4. 已清理资料不能再接单。
try:
    enqueue(con, c3)
    check("已清理资料拒绝接单", False)
except Conflict as exc:
    check("已清理资料拒绝接单", exc.status == 409)

# 5. 清理中断（任务停在 cleaning）后重启恢复。
c4 = make_case(con, "D")
seal(con, c4)
con.execute("UPDATE cleanup_jobs SET state = 'cleaning', claimed_at = 1 WHERE case_id = ?", (c4,))
service_one(con)
row = con.execute("SELECT retention_state, payload FROM cases WHERE id = ?", (c4,)).fetchone()
job = con.execute("SELECT state FROM cleanup_jobs WHERE case_id = ?", (c4,)).fetchone()
check("中断重启后完成清理", row == ("cleaned", None) and job[0] == "cleaned")

# 5b. 认领已提交、清原文前崩溃：重启后重入，不重复登记。
c4b = make_case(con, "DB")
seal(con, c4b)
check("认领后崩溃返回中断信号", service_one(con, crash_after_claim=True) == "crashed")
mid = con.execute("SELECT state, claimed_at FROM cleanup_jobs WHERE case_id = ?", (c4b,)).fetchone()
check("崩溃点任务停在 cleaning 且已认领", mid[0] == "cleaning" and mid[1] is not None)
check("崩溃点原文尚未清理", con.execute("SELECT payload FROM cases WHERE id = ?", (c4b,)).fetchone()[0] is not None)
service_one(con)  # 模拟重启后的 worker 循环
check("重启重入后完成清理", con.execute(
    "SELECT retention_state, payload FROM cases WHERE id = ?", (c4b,)).fetchone() == ("cleaned", None))
check("重入不重复登记任务", con.execute(
    "SELECT COUNT(*) FROM cleanup_jobs WHERE case_id = ?", (c4b,)).fetchone()[0] == 1)

# 6. cleaning 中断但仍有在途批次：复位 pending，不提前清理。
c5 = make_case(con, "E")
r5 = enqueue(con, c5)
seal(con, c5)
con.execute("UPDATE cleanup_jobs SET state = 'cleaning', claimed_at = 1 WHERE case_id = ?", (c5,))
service_one(con)
row = con.execute("SELECT payload FROM cases WHERE id = ?", (c5,)).fetchone()
job = con.execute("SELECT state, claimed_at FROM cleanup_jobs WHERE case_id = ?", (c5,)).fetchone()
check("在途未结束中断后复位 pending", job == ("pending", None) and row[0] is not None)
finish(con, r5, "done", {"summary": "ok"}, "dig")
service_one(con)
check("在途结束后补做清理", con.execute(
    "SELECT retention_state FROM cases WHERE id = ?", (c5,)).fetchone()[0] == "cleaned")

# 7. 空闲巡检。
check("无任务时巡检返回 False", service_one(con) is False)

# 8. 封存时无在途批次：下一次巡检立即清理。
c6 = make_case(con, "F")
seal(con, c6)
service_one(con)
check("无在途批次封存后即清理", con.execute(
    "SELECT retention_state FROM cases WHERE id = ?", (c6,)).fetchone()[0] == "cleaned")

# 9. 多资料封存互不影响，各自独立排队清理。
c7, c8 = make_case(con, "G"), make_case(con, "H")
r7 = enqueue(con, c7)
seal(con, c7)
seal(con, c8)  # c8 无在途批次
drain(con)  # 队头 c7 在途等待，不能阻塞 c8 的清理
state7 = con.execute("SELECT retention_state FROM cases WHERE id = ?", (c7,)).fetchone()[0]
state8 = con.execute("SELECT retention_state FROM cases WHERE id = ?", (c8,)).fetchone()[0]
check("在途资料保持待清理", state7 == "pending")
check("无在途资料独立完成清理", state8 == "cleaned")
finish(con, r7, "done", {"summary": "g"}, "g")
drain(con)
check("在途资料随后完成清理", con.execute(
    "SELECT retention_state FROM cases WHERE id = ?", (c7,)).fetchone()[0] == "cleaned")

print()
if failures:
    print(f"{len(failures)} 项失败：{failures}")
    sys.exit(1)
print("全部通过")
