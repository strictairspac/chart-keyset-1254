from sqlalchemy import JSON, BigInteger, Column, Integer, String, Table
from .base import metadata


cases = Table(
    "cases", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("title", String(120), nullable=False),
    Column("description", String(420), nullable=False),
    Column("payload", JSON, nullable=True),
    Column("created_by", String(48), nullable=False),
    # 封存：受理后停止该资料接单。1 表示已封存。
    Column("sealed", Integer, nullable=False, server_default="0"),
    Column("sealed_at", BigInteger, nullable=True),
    Column("sealed_by", String(48), nullable=True),
    # 清理状态机：pending（待清理）→ cleaning（清理中）→ cleaned（已清理）。
    Column("retention_state", String(16), nullable=False, server_default="active"),
    Column("retention_requested_at", BigInteger, nullable=True),
    Column("retention_cleaned_at", BigInteger, nullable=True),
)
