"""数据库连接与会话管理."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from theme_kg_v3.config.settings import SQLALCHEMY_DATABASE_URL, SQLITE_ARGS
from theme_kg_v3.schema.models import Base

logger = logging.getLogger(__name__)


def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """每个新连接建立时设置 SQLite 优化参数."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-65536")  # 64MB cache
        cursor.close()
    except Exception:
        pass


class DatabaseManager:
    """数据库连接管理器 (SQLite)."""

    def __init__(self, db_url: Optional[str] = None) -> None:
        if db_url is None:
            db_url = SQLALCHEMY_DATABASE_URL
        self._engine: Engine = create_engine(
            db_url,
            poolclass=NullPool,
            connect_args=SQLITE_ARGS,
        )
        # 注册事件监听：每个新连接自动设置 PRAGMA
        event.listen(self._engine, "connect", _set_sqlite_pragmas)
        self._session_factory: sessionmaker = sessionmaker(
            bind=self._engine,
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    @property
    def engine(self) -> Engine:
        """返回当前引擎实例."""
        return self._engine

    def create_session(self) -> Session:
        """创建一个新的数据库会话."""
        return self._session_factory()

    def close_session(self, session: Session) -> None:
        """关闭指定的数据库会话."""
        session.close()

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """上下文管理器，自动提交/回滚并关闭会话.

        Usage:
            with db_manager.get_session() as session:
                session.execute(...)
        """
        session = self.create_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def init_db(self) -> None:
        """创建所有表（基于 Base 中声明的模型）. """
        logger.info("开始创建数据库表...")
        Base.metadata.create_all(bind=self._engine)
        logger.info("数据库表创建完成.")

    def drop_db(self) -> None:
        """删除所有表（基于 Base 中声明的模型）. """
        logger.warning("即将删除所有数据库表!")
        Base.metadata.drop_all(bind=self._engine)
        logger.info("数据库表已全部删除.")

    def check_connection(self) -> bool:
        """测试数据库连接是否正常.

        Returns:
            True 表示连接正常，False 表示连接失败.
        """
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error("数据库连接测试失败: %s", e)
            return False

    def execute_sql_file(self, filepath: str) -> None:
        """执行指定路径的 SQL 文件.

        Args:
            filepath: SQL 文件绝对路径.
        """
        logger.info("开始执行 SQL 文件: %s", filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            sql_content = f.read()

        # 按分号分割多条语句并逐条执行
        statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
        with self._engine.connect() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
            conn.commit()
        logger.info("SQL 文件执行完成，共执行 %d 条语句.", len(statements))

    def dispose(self) -> None:
        """释放引擎占用的所有连接资源."""
        self._engine.dispose()
        logger.info("数据库引擎已释放.")


# ── 全局单例 ────────────────────────────────────────────────

_default_manager: Optional[DatabaseManager] = None


def get_default_manager() -> DatabaseManager:
    """获取全局默认 DatabaseManager 单例."""
    global _default_manager
    if _default_manager is None:
        _default_manager = DatabaseManager()
    return _default_manager
