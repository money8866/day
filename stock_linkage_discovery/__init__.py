"""A股全市场个股动态联动发现引擎 V2.0 (Stock Linkage Discovery)。

模块划分（各自有独立数据接口、配置与测试，禁止写成一个单体）：
    data_layer        全市场数据接口 + 历史时点股票池
    feature_engine    Stock Feature Engine
    residual_engine   市场/行业因子剔除（严格因果滚动回归）
    linkage_engine    Dynamic Linkage Engine（分层预筛 + 稀疏关联网络）
    cluster_engine    Emerging Cluster Engine + 演化/角色/评分
    snapshot_store    历史网络快照与群体生命周期
    attribution_engine  共同驱动归因（仅事后，不反向影响统计发现）
    observation_engine  与 HVT-BULL / W7 / E08 / VSW 交叉验证
    report_engine     CSV / JSON / Markdown 日报
    pipeline          编排与 CLI（每日、历史回放、增量）

重要原则：联动发现负责找机会，不产生 BUY 信号；买点由既有策略判断。
"""
__version__ = "2.0.0-phase1"
