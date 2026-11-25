from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


class RetentionState(StrEnum):
    # 未申请清理，资料正常使用。
    ACTIVE = "active"
    # 封存并等待清理：复演批次仍在排队，原文保留到最后一单结束。
    PENDING = "pending"
    # 已被 worker 认领，服务重启后可继续或重新认领。
    CLEANING = "cleaning"
    # 原文已清理，核收证据（结果、摘要、签署）仍可查。
    CLEANED = "cleaned"


class CleanupJobState(StrEnum):
    PENDING = "pending"
    CLEANING = "cleaning"
    CLEANED = "cleaned"
