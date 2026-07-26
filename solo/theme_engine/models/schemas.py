"""TERE V1 JSON Schema 定义 — 用于 API 校验."""

from __future__ import annotations

from typing import Any, Dict

# ── 每日排行榜输出 Schema ──────────────────────────────────
THEME_RANKING_ITEM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "rank": {"type": "integer", "minimum": 1},
        "theme_code": {"type": "string", "minLength": 1, "maxLength": 50},
        "theme_name": {"type": "string", "minLength": 1},
        "total_score": {"type": "number", "minimum": 0, "maximum": 100},
        "etf_strength": {"type": "number", "minimum": 0, "maximum": 100},
        "breadth_score": {"type": "number", "minimum": 0, "maximum": 100},
        "leader_strength": {"type": "number", "minimum": 0, "maximum": 100},
        "purity_score": {"type": "number", "minimum": 0, "maximum": 100},
        "resonance_score": {"type": "number", "minimum": 0, "maximum": 100},
        "flow_score": {"type": "number", "minimum": 0, "maximum": 100},
        "stage": {"type": "string", "enum": ["birth", "growth", "expansion", "main_trend", "distribution", "death"]},
        "rotation_prob": {"type": "number", "minimum": 0, "maximum": 100},
        "signal": {"type": "string", "enum": ["STRONG_BUY", "BUY", "WATCH", "REDUCE", "EXIT"]},
        "top_leaders": {"type": "array", "items": {"type": "string"}},
        "top_stocks": {"type": "array", "items": {"type": "string"}},
        "main_etf": {"type": "string"},
        "backup_etf": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["rank", "theme_code", "theme_name", "total_score", "signal"],
}

ENGINE_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "trade_date": {"type": "string", "pattern": r"^\d{8}$"},
        "themes": {"type": "array", "items": THEME_RANKING_ITEM_SCHEMA},
        "ranking": {"type": "array", "items": THEME_RANKING_ITEM_SCHEMA},
        "top_themes": {"type": "array", "items": THEME_RANKING_ITEM_SCHEMA},
        "generated_at": {"type": "string"},
        "error": {"type": "string"}
    },
    "required": ["trade_date", "ranking"],
}

# ── 引擎配置 Schema ─────────────────────────────────────────
ENGINE_CONFIG_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "trade_date": {"type": "string", "pattern": r"^\d{8}$"},
        "skip_etf": {"type": "boolean"},
        "skip_breadth": {"type": "boolean"},
        "skip_leader": {"type": "boolean"},
        "skip_purity": {"type": "boolean"},
        "skip_resonance": {"type": "boolean"},
        "skip_flow": {"type": "boolean"},
        "skip_stage": {"type": "boolean"},
        "skip_signal": {"type": "boolean"},
        "skip_rotation": {"type": "boolean"},
        "dry_run": {"type": "boolean"},
    },
}
