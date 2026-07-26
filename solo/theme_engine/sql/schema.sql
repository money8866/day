-- ============================================================
-- TERE V1 (Theme & ETF Resonance Engine) 数据库 Schema
-- 兼容 PostgreSQL 和 SQLite
-- 包含8张评分表 + 索引 + 唯一约束
-- ============================================================

-- ── 1. theme_etf_score ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_etf_score (
    id              INTEGER PRIMARY KEY,

    -- 主题标识
    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- ETF 标识
    main_etf        VARCHAR(20)  NOT NULL,
    backup_etf      VARCHAR(20)  DEFAULT NULL,

    -- ETF 各维度评分 (0~100)
    etf_strength    REAL         DEFAULT 0,       -- 综合ETF强度
    trend_score     REAL         DEFAULT 0,
    momentum_score  REAL         DEFAULT 0,
    alpha_score     REAL         DEFAULT 0,
    volume_score    REAL         DEFAULT 0,
    money_flow_score REAL        DEFAULT 0,
    volatility_score REAL        DEFAULT 0,
    relative_strength REAL       DEFAULT 0,
    ma_trend        REAL         DEFAULT 0,
    slope           REAL         DEFAULT 0,
    atr_score       REAL         DEFAULT 0,
    breakout_score  REAL         DEFAULT 0,

    -- 明细
    details         TEXT         DEFAULT NULL,    -- JSON

    -- 时间戳
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    -- 约束
    CONSTRAINT uq_etf_score UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_etf_trade_theme ON theme_etf_score (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_etf_theme_code  ON theme_etf_score (theme_code);
CREATE INDEX IF NOT EXISTS ix_etf_trade_date  ON theme_etf_score (trade_date);


-- ── 2. theme_leader_score ───────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_leader_score (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- 数量统计
    leader_count    INTEGER      DEFAULT 0,
    core_count      INTEGER      DEFAULT 0,
    follower_count  INTEGER      DEFAULT 0,

    -- 龙头评分 (0~100)
    leader_strength REAL         DEFAULT 0,
    leader_trend    REAL         DEFAULT 0,
    leader_alpha    REAL         DEFAULT 0,
    relative_strength REAL       DEFAULT 0,
    volume_score    REAL         DEFAULT 0,
    money_flow_score REAL        DEFAULT 0,
    institution_score REAL       DEFAULT 0,
    macd_score      REAL         DEFAULT 0,
    ma_trend_score  REAL         DEFAULT 0,

    -- 明细数据
    leaders         TEXT         DEFAULT NULL,    -- JSON Array
    cores           TEXT         DEFAULT NULL,    -- JSON Array
    details         TEXT         DEFAULT NULL,    -- JSON

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_leader_score UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_leader_trade_theme ON theme_leader_score (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_leader_theme_code   ON theme_leader_score (theme_code);
CREATE INDEX IF NOT EXISTS ix_leader_trade_date   ON theme_leader_score (trade_date);


-- ── 3. theme_breadth_score ──────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_breadth_score (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- 统计
    total_stocks    INTEGER      DEFAULT 0,

    -- 扩散度评分 (0~100)
    breadth_score   REAL         DEFAULT 0,
    up_ratio        REAL         DEFAULT 0,
    limit_up_ratio  REAL         DEFAULT 0,
    new_high_20d_ratio REAL      DEFAULT 0,
    above_ma20_ratio REAL       DEFAULT 0,
    above_ma60_ratio REAL       DEFAULT 0,
    above_ma120_ratio REAL      DEFAULT 0,
    amount_diffusion REAL       DEFAULT 0,
    return_median   REAL         DEFAULT 0,
    avg_alpha       REAL         DEFAULT 0,
    avg_relative_alpha REAL     DEFAULT 0,

    details         TEXT         DEFAULT NULL,    -- JSON

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_breadth_score UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_breadth_trade_theme ON theme_breadth_score (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_breadth_theme_code   ON theme_breadth_score (theme_code);
CREATE INDEX IF NOT EXISTS ix_breadth_trade_date   ON theme_breadth_score (trade_date);


-- ── 4. theme_resonance_score ────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_resonance_score (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- 共振评分 (0~100)
    resonance_score REAL         DEFAULT 0,
    etf_strength    REAL         DEFAULT 0,
    theme_breadth   REAL         DEFAULT 0,
    leader_score    REAL         DEFAULT 0,
    consistency_score REAL       DEFAULT 0,
    variance_penalty REAL        DEFAULT 0,
    std             REAL         DEFAULT 0,
    correlation     REAL         DEFAULT 0,

    details         TEXT         DEFAULT NULL,    -- JSON

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_resonance_score UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_resonance_trade_theme ON theme_resonance_score (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_resonance_theme_code   ON theme_resonance_score (theme_code);
CREATE INDEX IF NOT EXISTS ix_resonance_trade_date   ON theme_resonance_score (trade_date);


-- ── 5. theme_stage ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_stage (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- 生命周期阶段信息
    current_stage   VARCHAR(20)  NOT NULL,        -- birth/growth/expansion/main_trend/distribution/death
    stage_confidence REAL       DEFAULT 0,
    days_in_stage   INTEGER      DEFAULT 0,
    stage_progress  REAL         DEFAULT 0,        -- 0~1
    next_stage      VARCHAR(20)  DEFAULT NULL,

    indicators      TEXT         DEFAULT NULL,    -- JSON
    details         TEXT         DEFAULT NULL,    -- JSON

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_stage UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_stage_trade_theme ON theme_stage (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_stage_theme_code  ON theme_stage (theme_code);
CREATE INDEX IF NOT EXISTS ix_stage_trade_date  ON theme_stage (trade_date);


-- ── 6. theme_rotation ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_rotation (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- 轮动概率 (0~100)
    rotation_score  REAL         DEFAULT 0,
    prob_3d         REAL         DEFAULT 0,
    prob_5d         REAL         DEFAULT 0,
    prob_10d        REAL         DEFAULT 0,
    etf_momentum    REAL         DEFAULT 0,
    leader_momentum REAL         DEFAULT 0,
    breadth_trend   REAL         DEFAULT 0,
    resonance_trend REAL         DEFAULT 0,

    details         TEXT         DEFAULT NULL,    -- JSON

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_rotation UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_rotation_trade_theme ON theme_rotation (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_rotation_theme_code   ON theme_rotation (theme_code);
CREATE INDEX IF NOT EXISTS ix_rotation_trade_date   ON theme_rotation (trade_date);


-- ── 7. theme_signal ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS theme_signal (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,

    -- 信号信息
    signal          VARCHAR(20)  NOT NULL,         -- STRONG_BUY/BUY/WATCH/REDUCE/EXIT
    signal_strength REAL         DEFAULT 0,

    reasons         TEXT         DEFAULT NULL,    -- JSON Array
    details         TEXT         DEFAULT NULL,    -- JSON

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_signal UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_signal_trade_theme ON theme_signal (trade_date, theme_code);
CREATE INDEX IF NOT EXISTS ix_signal_theme_code  ON theme_signal (theme_code);
CREATE INDEX IF NOT EXISTS ix_signal_trade_date  ON theme_signal (trade_date);


-- ── 8. theme_daily_score (综合排行榜) ───────────────────────
CREATE TABLE IF NOT EXISTS theme_daily_score (
    id              INTEGER PRIMARY KEY,

    theme_code      VARCHAR(50)  NOT NULL,
    trade_date      VARCHAR(8)   NOT NULL,
    theme_name      VARCHAR(100) NOT NULL,

    -- 排名
    rank            INTEGER      DEFAULT 0,

    -- 综合评分 (0~100)
    total_score     REAL         DEFAULT 0,

    -- 各层级评分 (0~100)
    etf_strength    REAL         DEFAULT 0,
    breadth_score   REAL         DEFAULT 0,
    leader_strength REAL         DEFAULT 0,
    purity_score    REAL         DEFAULT 0,
    resonance_score REAL         DEFAULT 0,
    flow_score      REAL         DEFAULT 0,

    -- 元信息
    stage           VARCHAR(20)  DEFAULT 'birth',
    rotation_prob   REAL         DEFAULT 0,
    signal          VARCHAR(20)  DEFAULT 'WATCH',

    -- 明细数据
    top_leaders     TEXT         DEFAULT NULL,    -- JSON Array
    top_stocks      TEXT         DEFAULT NULL,    -- JSON Array
    main_etf        VARCHAR(20)  DEFAULT '',
    backup_etf      VARCHAR(20)  DEFAULT NULL,
    explanations    TEXT         DEFAULT NULL,    -- JSON Array
    summary         TEXT         DEFAULT NULL,    -- 可读总结文本

    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_daily_score UNIQUE (theme_code, trade_date)
);

CREATE INDEX IF NOT EXISTS ix_daily_trade_rank  ON theme_daily_score (trade_date, rank);
CREATE INDEX IF NOT EXISTS ix_daily_trade_score ON theme_daily_score (trade_date, total_score);
CREATE INDEX IF NOT EXISTS ix_daily_theme_code  ON theme_daily_score (theme_code);
CREATE INDEX IF NOT EXISTS ix_daily_trade_date  ON theme_daily_score (trade_date);


-- ============================================================
-- PostgreSQL 特有配置（可选）
-- 如果使用 PostgreSQL，可取消注释以下语句
-- ============================================================

-- ALTER TABLE theme_etf_score ALTER COLUMN id SET DEFAULT nextval('seq_etf_score');
-- ALTER TABLE theme_leader_score ALTER COLUMN id SET DEFAULT nextval('seq_leader_score');
-- ALTER TABLE theme_breadth_score ALTER COLUMN id SET DEFAULT nextval('seq_breadth_score');
-- ALTER TABLE theme_resonance_score ALTER COLUMN id SET DEFAULT nextval('seq_resonance_score');
-- ALTER TABLE theme_stage ALTER COLUMN id SET DEFAULT nextval('seq_stage');
-- ALTER TABLE theme_rotation ALTER COLUMN id SET DEFAULT nextval('seq_rotation');
-- ALTER TABLE theme_signal ALTER COLUMN id SET DEFAULT nextval('seq_signal');
-- ALTER TABLE theme_daily_score ALTER COLUMN id SET DEFAULT nextval('seq_daily_score');
