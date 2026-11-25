import json
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from migrations import v001_workbench, v002_retention
from src.config.resources import engine
from src.config.settings import DEMO_PASSWORD
from src.constants.roles import Role
from src.domain.scenario import FIXTURES
from src.models.cases import cases
from src.models.users import users
from src.utils.passwords import hash_password


def insert_new(db, table, key, values):
    if db.dialect.name == "postgresql":
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(index_elements=[key])
    else:
        stmt = mysql_insert(table).values(**values).on_duplicate_key_update(**{key: table.c[key]})
    db.execute(stmt)


def setup_schema():
    # 按版本顺序幂等升级；v001 建基础表，v002 补封存/清理结构，已执行的加列会跳过。
    migrations = (v001_workbench, v002_retention)
    for migration in migrations:
        with engine.begin() as db:
            migration.upgrade(db)


def seed():
    with engine.begin() as db:
        existing = set(db.execute(select(users.c.username)).scalars())
        for role in Role:
            if role.value not in existing:
                insert_new(db, users, "username", {
                    "username": role.value, "role": role.value, "password_hash": hash_password(DEMO_PASSWORD),
                })
        for fixture in FIXTURES:
            insert_new(db, cases, "code", {**fixture, "created_by": "seed"})
        return {table.name: db.scalar(select(func.count()).select_from(table)) for table in (users, cases)}


if __name__ == "__main__":
    setup_schema()
    print(json.dumps(seed(), sort_keys=True))
