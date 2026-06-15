"""
数据库连接管理模块
==================
提供 MongoDB、PostgreSQL、Milvus 三类数据库的统一连接接口，
支持连接池、上下文管理器 (with 语法)、断线重连等特性。

依赖安装:
    pip install pymongo sqlalchemy psycopg2-binary pymilvus python-dotenv
"""

from __future__ import annotations

import os
import time
import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple

from dotenv import load_dotenv
import yaml

# --------------------------------------------------------------------------- #
# 加载全局配置
# --------------------------------------------------------------------------- #

# 从统一配置目录加载敏感信息 (tushare token / deepseek api key 等)
# 路径: d:\mystock\config\.env  — 集中管理，避免在各项目中分散暴露
_SENSITIVE_ENV_PATH: str = r"d:\mystock\config\.env"
if os.path.exists(_SENSITIVE_ENV_PATH):
    load_dotenv(_SENSITIVE_ENV_PATH, override=True)
else:
    # 若统一 env 不存在，则退化为默认路径 (当前目录 .env)，便于本地开发
    load_dotenv()

# 获取 config.yaml 绝对路径（兼容 main.py 作为入口与模块独立运行）
_CONFIG_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "config", "config.yaml",
)

with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
    _RAW_CONFIG: Dict[str, Any] = yaml.safe_load(_f)


def _resolve_env(value: Any) -> Any:
    """递归替换 ${VAR_NAME} 形式的环境变量占位符。"""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            var_name: str = value[2:-1]
            return os.getenv(var_name, value)  # 未找到则保留原字符串
        return value
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


CONFIG: Dict[str, Any] = _resolve_env(_RAW_CONFIG)

logger = logging.getLogger(__name__)


# ============================================================================ #
# 1. MongoDB 连接器 (pymongo)
# ============================================================================ #
class MongoConnector:
    """
    MongoDB 连接管理器，内置连接池与断线重连。

    使用示例:
        >>> with MongoConnector() as db:
        ...     collection = db['themes']
        ...     collection.insert_one({'name': '资源主题'})
    """

    _instance: Optional["MongoConnector"] = None   # 单例
    _client: Optional[Any] = None                  # MongoClient
    _database: Optional[Any] = None                # 当前 DB

    def __new__(cls) -> "MongoConnector":
        """单例模式，全局共用一个连接池。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------ init

    def __init__(self) -> None:
        cfg: Dict[str, Any] = CONFIG["database"]["mongodb"]
        self.host: str = cfg["host"]
        self.port: int = int(cfg["port"])
        self.username: Optional[str] = cfg.get("username")
        self.password: Optional[str] = cfg.get("password")
        self.database: str = cfg["database"]
        self.auth_source: str = cfg.get("auth_source", "admin")
        self.max_pool_size: int = int(cfg.get("max_pool_size", 50))
        self.min_pool_size: int = int(cfg.get("min_pool_size", 5))
        self.connect_timeout_ms: int = int(cfg.get("connect_timeout_ms", 5000))
        self.socket_timeout_ms: int = int(cfg.get("socket_timeout_ms", 30000))
        self._ensure_connected()

    # ------------------------------------------------------------------ core

    def _ensure_connected(self) -> None:
        """懒初始化连接，若连接失效则尝试重建。"""
        from pymongo import MongoClient

        if self._client is not None:
            try:
                # ping 检测连接是否存活
                self._client.admin.command("ping")
                return
            except Exception as exc:
                logger.warning("MongoDB 连接已断开，正在重建: %s", exc)
                self._client = None
                self._database = None

        # 构造 URI，密码为空时不附加认证信息
        if self.username and self.password:
            uri: str = (
                f"mongodb://{self.username}:{self.password}"
                f"@{self.host}:{self.port}/{self.database}"
                f"?authSource={self.auth_source}"
            )
        else:
            uri = f"mongodb://{self.host}:{self.port}/{self.database}"

        self._client = MongoClient(
            uri,
            maxPoolSize=self.max_pool_size,
            minPoolSize=self.min_pool_size,
            connectTimeoutMS=self.connect_timeout_ms,
            socketTimeoutMS=self.socket_timeout_ms,
            serverSelectionTimeoutMS=self.connect_timeout_ms,
        )
        self._database = self._client[self.database]
        logger.info(
            "MongoDB 已连接: mongodb://%s:%s/%s",
            self.host, self.port, self.database,
        )

    # --------------------------------------------------------------- context

    def __enter__(self) -> Any:
        """返回当前 MongoDB Database 对象。"""
        self._ensure_connected()
        return self._database

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """保持连接池存活，不主动关闭。"""
        return None

    # -------------------------------------------------------------- helpers

    @property
    def db(self) -> Any:
        """获取当前 database 对象。"""
        self._ensure_connected()
        return self._database

    def close(self) -> None:
        """手动关闭连接池。"""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._database = None
            logger.info("MongoDB 连接池已关闭")


# ============================================================================ #
# 2. PostgreSQL 连接器 (SQLAlchemy + 连接池)
# ============================================================================ #
class PgConnector:
    """
    PostgreSQL 连接管理器，基于 SQLAlchemy 提供连接池。

    使用示例:
        >>> with PgConnector() as conn:
        ...     result = conn.execute("SELECT 1")
        ...     print(result.fetchone())
    """

    _instance: Optional["PgConnector"] = None
    _engine: Optional[Any] = None
    _session_factory: Optional[Any] = None

    def __new__(cls) -> "PgConnector":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------ init

    def __init__(self) -> None:
        cfg: Dict[str, Any] = CONFIG["database"]["postgresql"]
        self.host: str = cfg["host"]
        self.port: int = int(cfg["port"])
        self.username: str = cfg["username"]
        self.password: str = cfg["password"]
        self.database: str = cfg["database"]
        self.schema: str = cfg.get("schema", "public")
        self.pool_size: int = int(cfg.get("pool_size", 20))
        self.max_overflow: int = int(cfg.get("max_overflow", 10))
        self.pool_timeout: int = int(cfg.get("pool_timeout", 30))
        self.connect_timeout: int = int(cfg.get("connect_timeout", 5))
        self._ensure_connected()

    # ------------------------------------------------------------------ core

    def _ensure_connected(self) -> None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        if self._engine is not None:
            try:
                with self._engine.connect():
                    return
            except Exception as exc:
                logger.warning("PostgreSQL 连接失效，正在重建: %s", exc)
                self._engine = None
                self._session_factory = None

        # 构造 SQLAlchemy URL
        from urllib.parse import quote_plus

        dsn: str = (
            f"postgresql+psycopg2://{self.username}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.database}"
        )

        self._engine = create_engine(
            dsn,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_timeout=self.pool_timeout,
            pool_recycle=3600,               # 每小时回收连接
            pool_pre_ping=True,              # 连接前自动 ping
            echo=False,
            connect_args={
                "connect_timeout": self.connect_timeout,
                "options": f"-c search_path={self.schema},public",
            },
        )
        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        logger.info(
            "PostgreSQL 已连接: %s:%s/%s", self.host, self.port, self.database,
        )

    # --------------------------------------------------------------- context

    @contextmanager
    def get_connection(self) -> Generator[Any, None, None]:
        """获取原生 Connection 对象。"""
        self._ensure_connected()
        conn = self._engine.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def __enter__(self) -> Any:
        self._ensure_connected()
        return self._engine.connect()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    # -------------------------------------------------------------- helpers

    @property
    def engine(self) -> Any:
        self._ensure_connected()
        return self._engine

    @property
    def Session(self) -> Any:
        """返回一个 sessionmaker 工厂 (每次调用创建新 session)。"""
        self._ensure_connected()
        return self._session_factory

    @contextmanager
    def session_scope(self) -> Generator[Any, None, None]:
        """
        ORM Session 上下文，自动 commit/rollback/close。
        """
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("PostgreSQL 连接池已关闭")


# ============================================================================ #
# 3. Milvus 向量数据库连接器
# ============================================================================ #
class MilvusConnector:
    """
    Milvus 向量数据库连接器，基于 pymilvus 2.x。

    使用示例:
        >>> with MilvusConnector() as mc:
        ...     mc.insert(collection_name="thematic_vectors",
        ...               data=[...])
    """

    _instance: Optional["MilvusConnector"] = None
    _connections: Any = None

    def __new__(cls) -> "MilvusConnector":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------ init

    def __init__(self) -> None:
        cfg: Dict[str, Any] = CONFIG["database"]["milvus"]
        self.host: str = cfg["host"]
        self.port: int = int(cfg["port"])
        self.alias: str = cfg.get("alias", "thematic_default")
        self.collection_prefix: str = cfg.get("collection_prefix", "thematic_")
        self.dim: int = int(cfg.get("dim", 1024))
        self.metric_type: str = cfg.get("metric_type", "COSINE")
        self.index_type: str = cfg.get("index_type", "IVF_FLAT")
        self.nlist: int = int(cfg.get("nlist", 1024))
        self.nprobe: int = int(cfg.get("nprobe", 10))
        self._ensure_connected()

    # ------------------------------------------------------------------ core

    def _ensure_connected(self) -> None:
        from pymilvus import connections, utility

        try:
            if connections.has_connection(self.alias):
                connections.connect(self.alias)
                return
        except Exception as exc:
            logger.warning("Milvus 连接失效，重建中: %s", exc)
            try:
                connections.disconnect(self.alias)
            except Exception:
                pass

        connections.connect(
            alias=self.alias,
            host=self.host,
            port=self.port,
        )
        logger.info(
            "Milvus 已连接: %s:%s (alias=%s)", self.host, self.port, self.alias,
        )

    # --------------------------------------------------------------- context

    def __enter__(self) -> "MilvusConnector":
        self._ensure_connected()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    # -------------------------------------------------------------- helpers

    def get_collection_name(self, suffix: str) -> str:
        """拼接完整 collection 名称。"""
        return f"{self.collection_prefix}{suffix}"

    def close(self) -> None:
        from pymilvus import connections
        try:
            connections.disconnect(self.alias)
            logger.info("Milvus 连接已关闭 (alias=%s)", self.alias)
        except Exception:
            pass

    # -------------------------------------------------- collection 操作封装

    def collection_exists(self, name: str) -> bool:
        from pymilvus import utility
        return utility.has_collection(
            self.get_collection_name(name), using=self.alias,
        )

    def search(self,
               collection_name: str,
               vectors: List[List[float]],
               top_k: int = 10,
               expr: Optional[str] = None) -> Any:
        """
        执行向量相似度检索。

        :param collection_name: 集合名 (自动附加前缀)
        :param vectors: 待查询向量列表 [[float,...], ...]
        :param top_k: 最近邻数量
        :param expr: 过滤表达式 (Milvus boolean expr)
        """
        from pymilvus import Collection

        full_name: str = self.get_collection_name(collection_name)
        coll = Collection(full_name, using=self.alias)
        coll.load()
        search_params: Dict[str, Any] = {
            "metric_type": self.metric_type,
            "params": {"nprobe": self.nprobe},
        }
        return coll.search(
            data=vectors,
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=["id", "code", "name", "metadata"],
        )


# ============================================================================ #
# 模块级便捷函数
# ============================================================================ #

def close_all_connections() -> None:
    """
    优雅关闭所有数据库连接池。
    建议在程序退出前调用。
    """
    try:
        MongoConnector().close()
    except Exception as exc:
        logger.error("关闭 MongoDB 失败: %s", exc)

    try:
        PgConnector().close()
    except Exception as exc:
        logger.error("关闭 PostgreSQL 失败: %s", exc)

    try:
        MilvusConnector().close()
    except Exception as exc:
        logger.error("关闭 Milvus 失败: %s", exc)
