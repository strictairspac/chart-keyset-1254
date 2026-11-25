from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select

from src.constants.job_state import JobState, RetentionState
from src.domain.scenario import CONFIG
from src.models.cases import cases
from src.models.runs import runs
from . import audit


def query():
    return select(runs, cases.c.title.label("case_title")).join(cases, cases.c.id == runs.c.case_id)


def get(db, run_id):
    run = db.execute(query().where(runs.c.id == run_id)).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    return dict(run)


def list_entries(db):
    return [dict(row) for row in db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()]


def enqueue(db, case_id, parameter, actor):
    if parameter > CONFIG["max_parameter"]:
        raise HTTPException(422, f"参数不能超过 {CONFIG['max_parameter']}")
    # 行锁串行化接单与封存受理，避免封存申请漏拦同一时刻提交的新单。
    entry = db.execute(
        select(cases).where(cases.c.id == case_id).with_for_update()
    ).mappings().first()
    if entry is None:
        raise HTTPException(404, "资料不存在")
    if entry["sealed"]:
        raise HTTPException(409, "资料已封存，停止接单")
    if entry["retention_state"] == RetentionState.CLEANED:
        raise HTTPException(409, "资料原文已清理，无法再提交执行")
    # 接单时留存原件：封存/清理后该单仍按原件做到结束。
    change = db.execute(insert(runs).values(
        case_id=case_id, status=JobState.QUEUED, parameter=parameter, actor=actor,
        created_at=int(time()), payload_snapshot=entry["payload"],
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id)
