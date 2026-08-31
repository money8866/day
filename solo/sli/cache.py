# -*- coding: utf-8 -*-
"""
SLI 本地缓存层
parquet 文件缓存，按文件 mtime 判断过期；支持断点续跑（已缓存不重复请求）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger("sli.cache")


class SliCache:
    def __init__(self, cache_dir: str, default_expire_hours: float = 6.0) -> None:
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.default_expire = default_expire_hours
        # 内存缓存：key -> DataFrame
        self._mem: dict[str, pd.DataFrame] = {}

    def _path(self, key: str) -> str:
        safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return os.path.join(self.cache_dir, f"{safe}.parquet")

    def load(self, key: str, expire_hours: Optional[float] = None,
             allow_stale: bool = False) -> Optional[pd.DataFrame]:
        """读取缓存。过期时默认失效；allow_stale=True 时过期也返回（断点续跑用）。"""
        if key in self._mem:
            return self._mem[key]
        path = self._path(key)
        if not os.path.exists(path):
            return None
        expire = self.default_expire if expire_hours is None else expire_hours
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if (datetime.now() - mtime) > timedelta(hours=expire) and not allow_stale:
                return None
            df = pd.read_parquet(path)
            self._mem[key] = df
            return df
        except Exception as exc:
            logger.warning("缓存读取失败 [%s]: %s", key, exc)
            return None

    def save(self, key: str, df: Optional[pd.DataFrame]) -> None:
        if df is None or len(df) == 0:
            return
        path = self._path(key)
        try:
            df.to_parquet(path, index=False)
            self._mem[key] = df
        except Exception as exc:
            logger.warning("缓存写入失败 [%s]: %s", key, exc)

    def clear_mem(self) -> None:
        self._mem.clear()
