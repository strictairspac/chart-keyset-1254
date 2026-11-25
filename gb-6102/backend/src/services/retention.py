"""封存与清理领域服务。

封存申请当场受理：资料立即停止接单，并登记一条幂等的清理任务。
清理任务由 worker 分两步推进：先在独立事务认领（state=cleaning 并提交），
再在新事务清空录入原文。认领后服务崩溃也不丢任务，重启后按 cases 的
实际状态恢复；执行结果、摘要、签署等核收证据始终保留。
"""
from time import time

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.config.resources import engine
from src.constants.job_state import CleanupJobState, JobState, RetentionState
from src.models.cases import cases
from src.models.cleanup import cleanup_jobs
from src.models.runs import runs
from .audit import record


def _insert_cleanup_job(db, case_id, actor, now):
    # case_id 唯一：同一份资料重试封存不重复登记，冲突时保留原任务。
    values = {"case_id": case_id, "state": CleanupJobState.PENDING, "actor": actor, "created_at": now}
    if db.dialect.name == "postgresql":
        statement = pg_insert(cleanup_jobs).values(**values).on_conflict_do_nothing(index_elements=["case_id"])
    else:
        statement = mysql_insert(cleanup_jobs).values(**values).on_duplicate_key_update(
            case_id=cleanup_jobs.c.case_id
        )
    db.execute(statement)


def seal(db, case_id, actor):
    # 行锁与接单互斥：封存提交后，同一资料的新单一律拒绝。
    entry = db.execute(
        select(cases).where(cases.c.id == case_id).with_for_update()
    ).mappings().first()
    if entry is None:
        raise HTTPException(404, "资料不存在")
    now = int(time())
    if not entry["sealed"]:
        db.execute(update(cases).where(cases.c.id == case_id).values(
            sealed=1, sealed_at=now, sealed_by=actor,
            retention_state=RetentionState.PENDING, retention_requested_at=now,
        ))
        _insert_cleanup_job(db, case_id, actor, now)
        record(db, "seal_case", actor, "case", case_id)
    return dict(db.execute(select(cases).where(cases.c.id == case_id)).mappings().one())


def _queued_count(db, case_id):
    return db.scalar(
        select(func.count()).select_from(runs).where(
            runs.c.case_id == case_id, runs.c.status == JobState.QUEUED
        )
    )


def _actionable_jobs(db):
    """可推进的清理任务，按登记顺序排列。

    cleaning 任务（含崩溃遗留）总是可认领；pending 任务仅当该资料已无
    排队批次时可认领，从而跳过被在途复演挡住的队头，不饿死后续资料。
    """
    queued = (
        select(func.count()).select_from(runs)
        .where(runs.c.case_id == cleanup_jobs.c.case_id, runs.c.status == JobState.QUEUED)
        .scalar_subquery()
    )
    return db.execute(
        select(cleanup_jobs).where(
            cleanup_jobs.c.state.in_((CleanupJobState.PENDING, CleanupJobState.CLEANING)),
            or_(cleanup_jobs.c.state == CleanupJobState.CLEANING, queued == 0),
        ).order_by(cleanup_jobs.c.id).limit(1).with_for_update(skip_locked=True)
    ).mappings().first()


def service_one():
    """认领并推进一个清理任务；没有可推进任务时返回 False。"""
    with engine.begin() as db:
        job = _actionable_jobs(db)
        if job is None:
            return False
        case_id = job["case_id"]
        case_row = db.execute(
            select(cases).where(cases.c.id == case_id).with_for_update()
        ).mappings().first()
        if case_row is None:
            # 资料已不存在，仅收尾任务，避免反复占用 worker。
            _finish_job(db, job, None)
            return True
        if job["state"] == CleanupJobState.CLEANING and _queued_count(db, case_id):
            # 认领后崩溃、重启时又出现排队批次：复位为 pending，等最后一单结束。
            db.execute(update(cleanup_jobs).where(cleanup_jobs.c.id == job["id"]).values(
                state=CleanupJobState.PENDING, claimed_at=None,
            ))
            return True
        if job["state"] == CleanupJobState.PENDING:
            # 第一步：独立事务落盘认领状态，崩溃后重启仍能找到这条 cleaning 任务。
            now = int(time())
            db.execute(update(cleanup_jobs).where(cleanup_jobs.c.id == job["id"]).values(
                state=CleanupJobState.CLEANING, claimed_at=now, attempts=job["attempts"] + 1,
            ))
            job_id = job["id"]
    if job["state"] == CleanupJobState.PENDING:
        _purge(case_id, job_id)
    else:
        # 恢复路径：沿用旧认领时间完成清理。
        _purge(case_id, job["id"], job["claimed_at"])
    return True


def _purge(case_id, job_id, claimed_at=None):
    # 第二步：新事务清空原文并收尾；失败后 cleaning 任务仍在，重启可重入。
    with engine.begin() as db:
        now = int(time())
        case_row = db.execute(select(cases).where(cases.c.id == case_id).with_for_update()).mappings().first()
        if case_row is None:
            _finish_job(db, {"id": job_id, "claimed_at": claimed_at, "attempts": 0, "state": CleanupJobState.CLEANING}, None, now)
            return
        # 双保险：认领到执行之间若出现排队批次（理论上封存已阻止接单），推迟清理。
        if _queued_count(db, case_id):
            db.execute(update(cleanup_jobs).where(cleanup_jobs.c.id == job_id).values(
                state=CleanupJobState.PENDING, claimed_at=None,
            ))
            return
        # 清理录入时留存的原文；结果、digest、复核签署留在 runs/reviews，证据仍可查。
        db.execute(update(cases).where(cases.c.id == case_id).values(
            payload=None, retention_state=RetentionState.CLEANED, retention_cleaned_at=now,
        ))
        job = db.execute(select(cleanup_jobs).where(cleanup_jobs.c.id == job_id)).mappings().one()
        _finish_job(db, job, case_id, now)
        record(db, "clean_case", job["actor"], "case", case_id)


def _finish_job(db, job, case_id, now=None):
    now = now or int(time())
    db.execute(update(cleanup_jobs).where(cleanup_jobs.c.id == job["id"]).values(
        state=CleanupJobState.CLEANED, claimed_at=job["claimed_at"] or now,
        finished_at=now, attempts=job["attempts"] + 1,
    ))
    if job["state"] == CleanupJobState.CLEANING and case_id is not None:
        # 认领后崩溃的恢复路径：任务已完成而资料状态未及更新时补齐。
        db.execute(update(cases).where(
            cases.c.id == case_id, cases.c.retention_state != RetentionState.CLEANED
        ).values(retention_state=RetentionState.CLEANED, retention_cleaned_at=now))
