from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


# 资料清理任务：封存后排队等待，最后一单结束后由 worker 清理原件。
cleanup_jobs = Table(
    "cleanup_jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), unique=True, nullable=False),
    # pending：等待排队中的复演结束；cleaning：已被 worker 认领；cleaned：原件已清理。
    Column("state", String(16), nullable=False, index=True),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("claimed_at", BigInteger, nullable=True),
    Column("finished_at", BigInteger, nullable=True),
    Column("attempts", Integer, nullable=False, server_default="0"),
)
