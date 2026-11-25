import logging
import time

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.config.resources import cache, engine
from src.services import retention
from src.services.execution import JobFailure, fail, service_one

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")


def check():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    heartbeat = cache.get("workbench:heartbeat")
    return heartbeat is not None and time.time() - float(heartbeat) < 90


def work():
    while True:
        try:
            cache.set("workbench:heartbeat", time.time(), ex=90)
            try:
                # 复演与清理共用一个持久队列循环：先把复演批次做到结束，
                # 再推进封存清理；被在途批次挡住的清理任务不能饿死后续资料，
                # 因此每轮把所有可推进的清理任务都巡检一遍。
                progressed = service_one()
                try:
                    while retention.service_one():
                        progressed = True
                except SQLAlchemyError:
                    # 清理中断不丢任务：状态留在库里，下个循环或重启后继续。
                    logger.exception("Retention cleanup failed and will be retried")
                if progressed:
                    continue
            except JobFailure as exc:
                logger.exception("Execution %s failed", exc.run_id)
                fail(exc)
                continue
            cache.blpop("workbench:wake", timeout=1)
        except (RedisError, SQLAlchemyError):
            logger.exception("Worker connection unavailable")
            time.sleep(2)


if __name__ == "__main__":
    work()
