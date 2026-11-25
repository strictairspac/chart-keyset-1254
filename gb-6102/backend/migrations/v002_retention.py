"""封存与清理：为既有库补齐 cases/runs 字段并建立 cleanup_jobs 表。

全新数据库由 v001 的 metadata.create_all 直接建出最新结构；
已有数据卷通过本迁移做幂等加列，列已存在时跳过，可重复执行。
类型写法对 MySQL 8 与 PostgreSQL 均有效。
"""
from sqlalchemy import inspect, text  # noqa: F401  text 保留以便扩展原生语句

from src.models import cleanup  # noqa: F401  注册表定义
from src.models.base import metadata

# (表名, 列名, 通用加列类型)；缺省值与模型 server_default 保持一致。
COLUMNS = {
    "cases": [
        ("sealed", "INT NOT NULL DEFAULT 0"),
        ("sealed_at", "BIGINT NULL"),
        ("sealed_by", "VARCHAR(48) NULL"),
        ("retention_state", "VARCHAR(16) NOT NULL DEFAULT 'active'"),
        ("retention_requested_at", "BIGINT NULL"),
        ("retention_cleaned_at", "BIGINT NULL"),
    ],
    "runs": [
        ("payload_snapshot", "JSON NULL"),
    ],
}


def upgrade(connection):
    inspector = inspect(connection)
    for table, columns in COLUMNS.items():
        present = {column["name"] for column in inspector.get_columns(table)} if inspector.has_table(table) else set()
        for name, column_type in columns:
            if name in present:
                continue
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}"))
    metadata.create_all(connection)
