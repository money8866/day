-- ============================================================================
-- Theme Knowledge Graph - PostgreSQL 17 DDL
-- 主题知识图谱系统 - 数据库表结构定义
-- ============================================================================

-- ============================================================================
-- 1. theme - 主题表（二级分类）
-- ============================================================================
CREATE TABLE theme (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(32)     NOT NULL,
    name_cn         VARCHAR(64)     NOT NULL,
    description     TEXT,
    level           INTEGER         NOT NULL DEFAULT 2,
    status          VARCHAR(16)     DEFAULT 'active',
    lifecycle_stage VARCHAR(16),
    main_etf_code   VARCHAR(16),
    created_at      TIMESTAMPTZ     DEFAULT now(),
    updated_at      TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_theme_code UNIQUE (code),
    CONSTRAINT uq_theme_name UNIQUE (name_cn),
    CONSTRAINT chk_theme_status CHECK (status IN ('active', 'inactive', 'deprecated')),
    CONSTRAINT chk_theme_lifecycle CHECK (lifecycle_stage IN ('birth', 'growth', 'main_trend', 'distribution', 'death'))
);

COMMENT ON TABLE     theme           IS '主题表 - 二级分类，如 AI算力、半导体等';
COMMENT ON COLUMN    theme.id                IS '主键 UUID';
COMMENT ON COLUMN    theme.code              IS '主题代码，如 AI_COMPUTE、SEMICONDUCTOR';
COMMENT ON COLUMN    theme.name_cn           IS '主题中文名称';
COMMENT ON COLUMN    theme.description       IS '主题描述';
COMMENT ON COLUMN    theme.level             IS '层级，默认为2';
COMMENT ON COLUMN    theme.status            IS '状态：active 活跃 / inactive 非活跃 / deprecated 已废弃';
COMMENT ON COLUMN    theme.lifecycle_stage   IS '生命周期阶段：birth 诞生 / growth 成长 / main_trend 主升 / distribution 分化 / death 退潮';
COMMENT ON COLUMN    theme.main_etf_code     IS '主要ETF代码';
COMMENT ON COLUMN    theme.created_at        IS '创建时间';
COMMENT ON COLUMN    theme.updated_at        IS '更新时间';

CREATE INDEX idx_theme_status      ON theme (status);
CREATE INDEX idx_theme_lifecycle   ON theme (lifecycle_stage);


-- ============================================================================
-- 2. industry_chain - 产业链节点表（三级分类）
-- ============================================================================
CREATE TABLE industry_chain (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id        UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    code            VARCHAR(64)     NOT NULL,
    name_cn         VARCHAR(64)     NOT NULL,
    description     TEXT,
    sort_order      INTEGER         DEFAULT 0,
    created_at      TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_chain_code UNIQUE (code),
    CONSTRAINT uq_chain_theme_name UNIQUE (theme_id, name_cn)
);

COMMENT ON TABLE     industry_chain       IS '产业链节点表 - 三级分类，如 GPU、PCB 等';
COMMENT ON COLUMN    industry_chain.id            IS '主键 UUID';
COMMENT ON COLUMN    industry_chain.theme_id      IS '关联主题ID';
COMMENT ON COLUMN    industry_chain.code          IS '节点代码，如 AI_COMPUTE_GPU';
COMMENT ON COLUMN    industry_chain.name_cn       IS '节点中文名称';
COMMENT ON COLUMN    industry_chain.description   IS '节点描述';
COMMENT ON COLUMN    industry_chain.sort_order    IS '排序序号';
COMMENT ON COLUMN    industry_chain.created_at    IS '创建时间';

CREATE INDEX idx_chain_theme ON industry_chain (theme_id);


-- ============================================================================
-- 3. concept_tags - 概念标签表（四级分类）
-- ============================================================================
CREATE TABLE concept_tags (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    code            VARCHAR(64)     NOT NULL,
    name_cn         VARCHAR(64)     NOT NULL,
    category        VARCHAR(32),
    created_at      TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_concept_code UNIQUE (code),
    CONSTRAINT uq_concept_name UNIQUE (name_cn),
    CONSTRAINT chk_concept_category CHECK (category IN ('brand', 'tech', 'policy', 'other'))
);

COMMENT ON TABLE     concept_tags        IS '概念标签表 - 四级分类，如华为、苹果等品牌/技术/政策标签';
COMMENT ON COLUMN    concept_tags.id         IS '主键 UUID';
COMMENT ON COLUMN    concept_tags.code       IS '标签代码，如 HUAWEI、APPLE';
COMMENT ON COLUMN    concept_tags.name_cn    IS '标签中文名称';
COMMENT ON COLUMN    concept_tags.category   IS '标签类别：brand 品牌 / tech 技术 / policy 政策 / other 其他';
COMMENT ON COLUMN    concept_tags.created_at IS '创建时间';

CREATE INDEX idx_concept_category ON concept_tags (category);


-- ============================================================================
-- 4. theme_etf - 主题ETF映射表
-- ============================================================================
CREATE TABLE theme_etf (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id        UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    etf_code        VARCHAR(16)     NOT NULL,
    etf_name        VARCHAR(128)    NOT NULL,
    is_main         BOOLEAN         DEFAULT false,
    weight          FLOAT           DEFAULT 1.0,
    created_at      TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_theme_etf UNIQUE (theme_id, etf_code)
);

COMMENT ON TABLE     theme_etf           IS '主题ETF映射表 - 主题关联的ETF基金';
COMMENT ON COLUMN    theme_etf.id        IS '主键 UUID';
COMMENT ON COLUMN    theme_etf.theme_id  IS '关联主题ID';
COMMENT ON COLUMN    theme_etf.etf_code  IS 'ETF代码，如 159819.SZ';
COMMENT ON COLUMN    theme_etf.etf_name  IS 'ETF名称';
COMMENT ON COLUMN    theme_etf.is_main   IS '是否为主要ETF';
COMMENT ON COLUMN    theme_etf.weight    IS '权重';
COMMENT ON COLUMN    theme_etf.created_at IS '创建时间';

CREATE INDEX idx_etf_theme  ON theme_etf (theme_id);
CREATE INDEX idx_etf_code   ON theme_etf (etf_code);


-- ============================================================================
-- 5. theme_keywords - 主题关键词表
-- ============================================================================
CREATE TABLE theme_keywords (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id        UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    keyword         VARCHAR(128)    NOT NULL,
    weight          FLOAT           DEFAULT 1.0,
    keyword_type    VARCHAR(32),
    is_exclude      BOOLEAN         DEFAULT false,
    created_at      TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_theme_keyword UNIQUE (theme_id, keyword),
    CONSTRAINT chk_keyword_type CHECK (keyword_type IN ('core', 'industry', 'product', 'concept', 'brand'))
);

COMMENT ON TABLE     theme_keywords       IS '主题关键词表 - 用于主题分类打标的关键词';
COMMENT ON COLUMN    theme_keywords.id            IS '主键 UUID';
COMMENT ON COLUMN    theme_keywords.theme_id      IS '关联主题ID';
COMMENT ON COLUMN    theme_keywords.keyword       IS '关键词';
COMMENT ON COLUMN    theme_keywords.weight        IS '权重';
COMMENT ON COLUMN    theme_keywords.keyword_type  IS '关键词类型：core 核心 / industry 产业 / product 产品 / concept 概念 / brand 品牌';
COMMENT ON COLUMN    theme_keywords.is_exclude    IS '是否为排除词（true表示包含此关键词应排除该主题）';
COMMENT ON COLUMN    theme_keywords.created_at    IS '创建时间';

CREATE INDEX idx_keyword_theme  ON theme_keywords (theme_id);
CREATE INDEX idx_keyword_text   ON theme_keywords (keyword);


-- ============================================================================
-- 6. stock_theme - 个股-主题分配表
-- ============================================================================
CREATE TABLE stock_theme (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    stock_code          VARCHAR(16)     NOT NULL,
    stock_name          VARCHAR(64)     NOT NULL,
    primary_theme_id    UUID            NOT NULL REFERENCES theme (id) ON DELETE RESTRICT,
    confidence          FLOAT           NOT NULL,
    confidence_reason   TEXT,
    secondary_theme_ids UUID[],
    is_leader           BOOLEAN         DEFAULT false,
    leader_type         VARCHAR(16),
    industry_chain_ids  UUID[],
    concept_tag_ids     UUID[],
    is_active           BOOLEAN         DEFAULT true,
    assigned_at         TIMESTAMPTZ     DEFAULT now(),
    updated_at          TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_stock_theme UNIQUE (stock_code),
    CONSTRAINT chk_confidence CHECK (confidence >= 0 AND confidence <= 100),
    CONSTRAINT chk_leader_type CHECK (leader_type IN ('leader', 'core', 'follower', 'catch_up', 'eliminated'))
);

COMMENT ON TABLE     stock_theme             IS '个股-主题分配表 - 个股与主题的关联关系';
COMMENT ON COLUMN    stock_theme.id                  IS '主键 UUID';
COMMENT ON COLUMN    stock_theme.stock_code           IS '股票代码，如 300476.SZ';
COMMENT ON COLUMN    stock_theme.stock_name           IS '股票名称';
COMMENT ON COLUMN    stock_theme.primary_theme_id     IS '主主题ID（一只股票只能有一个主主题）';
COMMENT ON COLUMN    stock_theme.confidence           IS '主题置信度评分（0-100）';
COMMENT ON COLUMN    stock_theme.confidence_reason    IS '置信度分解说明（JSON格式）';
COMMENT ON COLUMN    stock_theme.secondary_theme_ids  IS '次主题ID数组（仅标签级主题）';
COMMENT ON COLUMN    stock_theme.is_leader            IS '是否为龙头股';
COMMENT ON COLUMN    stock_theme.leader_type          IS '龙头类型：leader 总龙头 / core 中军 / follower 跟风 / catch_up 补涨 / eliminated 掉队';
COMMENT ON COLUMN    stock_theme.industry_chain_ids   IS '所属产业链节点ID数组';
COMMENT ON COLUMN    stock_theme.concept_tag_ids      IS '所属概念标签ID数组';
COMMENT ON COLUMN    stock_theme.is_active             IS '是否活跃';
COMMENT ON COLUMN    stock_theme.assigned_at          IS '分配时间';
COMMENT ON COLUMN    stock_theme.updated_at           IS '更新时间';

CREATE INDEX idx_stock_theme_primary        ON stock_theme (primary_theme_id);
CREATE INDEX idx_stock_theme_leader         ON stock_theme (is_leader);
CREATE INDEX idx_stock_theme_active         ON stock_theme (is_active);
CREATE INDEX idx_stock_secondary            ON stock_theme USING GIN (secondary_theme_ids);
CREATE INDEX idx_stock_industry_chain       ON stock_theme USING GIN (industry_chain_ids);
CREATE INDEX idx_stock_concept_tags         ON stock_theme USING GIN (concept_tag_ids);


-- ============================================================================
-- 7. theme_relation - 主题间关系表
-- ============================================================================
CREATE TABLE theme_relation (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    source_theme_id     UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    target_theme_id     UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    relation_type       VARCHAR(32)     NOT NULL,
    strength            FLOAT           DEFAULT 0.0,
    created_at          TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_theme_relation UNIQUE (source_theme_id, target_theme_id, relation_type),
    CONSTRAINT chk_relation_type CHECK (relation_type IN ('correlation', 'conflict', 'chain_upstream', 'chain_downstream')),
    CONSTRAINT chk_relation_strength CHECK (strength >= 0 AND strength <= 1)
);

COMMENT ON TABLE     theme_relation              IS '主题间关系表 - 记录主题之间的关联、冲突、产业链上下游关系';
COMMENT ON COLUMN    theme_relation.id                   IS '主键 UUID';
COMMENT ON COLUMN    theme_relation.source_theme_id      IS '源主题ID';
COMMENT ON COLUMN    theme_relation.target_theme_id      IS '目标主题ID';
COMMENT ON COLUMN    theme_relation.relation_type        IS '关系类型：correlation 关联 / conflict 冲突 / chain_upstream 产业链上游 / chain_downstream 产业链下游';
COMMENT ON COLUMN    theme_relation.strength             IS '关系强度（0-1）';
COMMENT ON COLUMN    theme_relation.created_at           IS '创建时间';


-- ============================================================================
-- 8. theme_history - 主题历史快照表
-- ============================================================================
CREATE TABLE theme_history (
    id                      UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id                UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    trade_date              DATE            NOT NULL,
    lifecycle_stage         VARCHAR(16),
    momentum_5d             FLOAT,
    momentum_20d            FLOAT,
    momentum_60d            FLOAT,
    volume_ratio            FLOAT,
    leader_count            INTEGER,
    total_market_cap_billion FLOAT,
    avg_return_5d           FLOAT,
    avg_return_20d          FLOAT,
    turnover_rate           FLOAT,
    sentiment_score         FLOAT,
    created_at              TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_theme_history UNIQUE (theme_id, trade_date)
);

COMMENT ON TABLE     theme_history                   IS '主题历史快照表 - 主题各维度的历史时序数据';
COMMENT ON COLUMN    theme_history.id                 IS '主键 UUID';
COMMENT ON COLUMN    theme_history.theme_id           IS '关联主题ID';
COMMENT ON COLUMN    theme_history.trade_date         IS '交易日';
COMMENT ON COLUMN    theme_history.lifecycle_stage    IS '生命周期阶段';
COMMENT ON COLUMN    theme_history.momentum_5d        IS '5日动量';
COMMENT ON COLUMN    theme_history.momentum_20d       IS '20日动量';
COMMENT ON COLUMN    theme_history.momentum_60d       IS '60日动量';
COMMENT ON COLUMN    theme_history.volume_ratio       IS '量比';
COMMENT ON COLUMN    theme_history.leader_count       IS '龙头股数量';
COMMENT ON COLUMN    theme_history.total_market_cap_billion IS '总市值（十亿）';
COMMENT ON COLUMN    theme_history.avg_return_5d      IS '5日平均收益率';
COMMENT ON COLUMN    theme_history.avg_return_20d     IS '20日平均收益率';
COMMENT ON COLUMN    theme_history.turnover_rate      IS '换手率';
COMMENT ON COLUMN    theme_history.sentiment_score    IS '情绪评分';
COMMENT ON COLUMN    theme_history.created_at         IS '创建时间';

CREATE INDEX idx_theme_hist_date        ON theme_history (trade_date);
CREATE INDEX idx_theme_hist_lifecycle   ON theme_history (lifecycle_stage);


-- ============================================================================
-- 9. theme_stage - 主题生命周期阶段转换记录表
-- ============================================================================
CREATE TABLE theme_stage (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id        UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    trade_date      DATE            NOT NULL,
    stage_before    VARCHAR(16),
    stage_after     VARCHAR(16),
    reason          TEXT,
    created_at      TIMESTAMPTZ     DEFAULT now()
);

COMMENT ON TABLE     theme_stage             IS '主题生命周期阶段转换记录表 - 跟踪主题阶段变化';
COMMENT ON COLUMN    theme_stage.id           IS '主键 UUID';
COMMENT ON COLUMN    theme_stage.theme_id     IS '关联主题ID';
COMMENT ON COLUMN    theme_stage.trade_date   IS '转换日期';
COMMENT ON COLUMN    theme_stage.stage_before IS '转换前阶段';
COMMENT ON COLUMN    theme_stage.stage_after  IS '转换后阶段';
COMMENT ON COLUMN    theme_stage.reason       IS '转换原因';
COMMENT ON COLUMN    theme_stage.created_at   IS '创建时间';


-- ============================================================================
-- 10. leader_stock - 主题龙头股跟踪表
-- ============================================================================
CREATE TABLE leader_stock (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id            UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    stock_code          VARCHAR(16)     NOT NULL,
    stock_name          VARCHAR(64)     NOT NULL,
    leader_type         VARCHAR(16),
    assigned_date       DATE            NOT NULL,
    consecutive_limit_up INTEGER        DEFAULT 0,
    cumulative_return   FLOAT,
    market_cap_billion  FLOAT,
    is_active           BOOLEAN         DEFAULT true,
    created_at          TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_leader_date UNIQUE (theme_id, stock_code, assigned_date),
    CONSTRAINT chk_leader_stock_type CHECK (leader_type IN ('leader', 'core', 'follower', 'catch_up', 'eliminated'))
);

COMMENT ON TABLE     leader_stock                IS '主题龙头股跟踪表 - 记录各主题的龙头股变化';
COMMENT ON COLUMN    leader_stock.id              IS '主键 UUID';
COMMENT ON COLUMN    leader_stock.theme_id        IS '关联主题ID';
COMMENT ON COLUMN    leader_stock.stock_code      IS '股票代码';
COMMENT ON COLUMN    leader_stock.stock_name      IS '股票名称';
COMMENT ON COLUMN    leader_stock.leader_type     IS '龙头类型：leader 总龙头 / core 中军 / follower 跟风 / catch_up 补涨 / eliminated 掉队';
COMMENT ON COLUMN    leader_stock.assigned_date   IS '分配日期';
COMMENT ON COLUMN    leader_stock.consecutive_limit_up IS '连续涨停天数';
COMMENT ON COLUMN    leader_stock.cumulative_return    IS '累计收益率';
COMMENT ON COLUMN    leader_stock.market_cap_billion   IS '市值（十亿）';
COMMENT ON COLUMN    leader_stock.is_active             IS '是否当前活跃';
COMMENT ON COLUMN    leader_stock.created_at       IS '创建时间';

CREATE INDEX idx_leader_theme ON leader_stock (theme_id);
CREATE INDEX idx_leader_type  ON leader_stock (leader_type);


-- ============================================================================
-- 11. stock_relation - 个股间关系表（主题内）
-- ============================================================================
CREATE TABLE stock_relation (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    source_stock_code   VARCHAR(16)     NOT NULL,
    target_stock_code   VARCHAR(16)     NOT NULL,
    relation_type       VARCHAR(32)     NOT NULL,
    strength            FLOAT           DEFAULT 0.0,
    theme_id            UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT chk_stock_relation_type CHECK (relation_type IN ('correlation', 'supply_chain', 'competitor', 'peer')),
    CONSTRAINT chk_stock_relation_strength CHECK (strength >= 0 AND strength <= 1)
);

COMMENT ON TABLE     stock_relation              IS '个股间关系表 - 主题内股票之间的关联关系';
COMMENT ON COLUMN    stock_relation.id                   IS '主键 UUID';
COMMENT ON COLUMN    stock_relation.source_stock_code    IS '源股票代码';
COMMENT ON COLUMN    stock_relation.target_stock_code    IS '目标股票代码';
COMMENT ON COLUMN    stock_relation.relation_type        IS '关系类型：correlation 关联 / supply_chain 供应链 / competitor 竞争 / peer 同业';
COMMENT ON COLUMN    stock_relation.strength             IS '关系强度（0-1）';
COMMENT ON COLUMN    stock_relation.theme_id             IS '所属主题ID';
COMMENT ON COLUMN    stock_relation.created_at           IS '创建时间';


-- ============================================================================
-- 12. theme_score_daily - 主题每日评分表
-- ============================================================================
CREATE TABLE theme_score_daily (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    theme_id            UUID            NOT NULL REFERENCES theme (id) ON DELETE CASCADE,
    trade_date          DATE            NOT NULL,
    total_score         FLOAT           NOT NULL,
    momentum_score      FLOAT,
    volume_score        FLOAT,
    breadth_score       FLOAT,
    sentiment_score     FLOAT,
    leader_score        FLOAT,
    etf_corr_score      FLOAT,
    capital_flow_score  FLOAT,
    detail_json         JSONB,
    created_at          TIMESTAMPTZ     DEFAULT now(),

    CONSTRAINT uq_score_daily UNIQUE (theme_id, trade_date)
);

COMMENT ON TABLE     theme_score_daily           IS '主题每日评分表 - 主题多维度打分数据';
COMMENT ON COLUMN    theme_score_daily.id                IS '主键 UUID';
COMMENT ON COLUMN    theme_score_daily.theme_id          IS '关联主题ID';
COMMENT ON COLUMN    theme_score_daily.trade_date        IS '交易日';
COMMENT ON COLUMN    theme_score_daily.total_score       IS '总分';
COMMENT ON COLUMN    theme_score_daily.momentum_score    IS '动量评分';
COMMENT ON COLUMN    theme_score_daily.volume_score      IS '量能评分';
COMMENT ON COLUMN    theme_score_daily.breadth_score     IS '宽度评分';
COMMENT ON COLUMN    theme_score_daily.sentiment_score   IS '情绪评分';
COMMENT ON COLUMN    theme_score_daily.leader_score      IS '龙头评分';
COMMENT ON COLUMN    theme_score_daily.etf_corr_score    IS 'ETF相关性评分';
COMMENT ON COLUMN    theme_score_daily.capital_flow_score IS '资金流评分';
COMMENT ON COLUMN    theme_score_daily.detail_json       IS '详细评分明细（JSONB）';
COMMENT ON COLUMN    theme_score_daily.created_at        IS '创建时间';

CREATE INDEX idx_score_theme_date   ON theme_score_daily (theme_id, trade_date);
CREATE INDEX idx_score_date         ON theme_score_daily (trade_date);


-- ============================================================================
-- Trigger Function: 自动更新 updated_at 字段
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION fn_set_updated_at() IS '自动更新 updated_at 时间戳的触发器函数';


-- ============================================================================
-- Triggers: 为需要自动更新 updated_at 的表绑定触发器
-- ============================================================================
CREATE TRIGGER trg_theme_updated_at
    BEFORE UPDATE ON theme
    FOR EACH ROW
    EXECUTE FUNCTION fn_set_updated_at();

CREATE TRIGGER trg_stock_theme_updated_at
    BEFORE UPDATE ON stock_theme
    FOR EACH ROW
    EXECUTE FUNCTION fn_set_updated_at();
