"""配置加载 / 日志 / 全局常量。

Token 只从环境变量（或本机已有的 tushare 配置）读取，绝不写入日志或报告。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"

DEFAULT_CONFIG_PATH = ROOT / "config.yaml"

_LOGGER_NAME = "etf159531"


def load_config(path: Optional[str] = None) -> dict:
    """读取 YAML 配置，并加载 .env（不覆盖已存在的环境变量）。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    if not cfg_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # .env 查找顺序：项目目录 -> 当前工作目录
    load_dotenv(ROOT / ".env", override=False)
    if Path.cwd() != ROOT:
        load_dotenv(Path.cwd() / ".env", override=False)

    cfg["_config_path"] = str(cfg_path)
    cfg["_project_root"] = str(ROOT)
    return cfg


def resolve_path(cfg: dict, value: str) -> Path:
    """把配置里的相对路径解析到项目根目录下。"""
    p = Path(value)
    if p.is_absolute():
        return p
    root = Path(cfg.get("_project_root", ROOT))
    return (root / p).resolve()


def get_tushare_token() -> tuple[str, str]:
    """读取 TuShare Token。

    返回 (token, source)。source 仅用于日志说明来源，绝不输出 token 内容。
    优先环境变量；若环境中没有，则回退到本机 tushare 已保存的 token（仅用于本机运行）。
    """
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token, "env:TUSHARE_TOKEN"

    try:
        import tushare as ts

        saved = (ts.get_token() or "").strip()
        if saved:
            return saved, "local_tushare_config"
    except Exception:
        pass

    raise RuntimeError(
        "未找到 TUSHARE_TOKEN：请复制 .env.example 为 .env 并填写，"
        "或设置系统环境变量 TUSHARE_TOKEN。"
    )


def setup_logging(cfg: dict, level: int = logging.INFO) -> logging.Logger:
    """日志同时输出到控制台与 logs/ 目录下的日期文件。"""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    log_dir = resolve_path(cfg, cfg.get("project", {}).get("log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(
        log_dir / f"etf159531_{datetime.now():%Y%m%d}.log", encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def now_cn() -> datetime:
    """北京时间（不依赖第三方库）。"""
    from datetime import timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
