#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日主题-个股对应关系映射生成器
使用 theme_pattern_stock_picker.py 中的 match_theme_stocks 算法，
生成所有主题与个股的对应关系 JSON 文件。

输出文件：d:/mystock/cache_daily/theme_stock_map_{TRADE_DATE}.json

JSON 结构：
{
    "trade_date": "20260618",
    "update_time": "2026-06-18T15:30:00",
    "themes": {
        "光通信": [
            {"code": "300308.SZ", "name": "中际旭创", "via": "leader_company", "chain_distance": 0, "score": 35},
            ...
        ]
    },
    "stocks": {
        "300308.SZ": {
            "name": "中际旭创",
            "themes": ["光通信", "AI算力链"]
        }
    }
}
"""

import sys
import os
import json
from datetime import datetime

# Windows GBK 控制台输出修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 添加项目路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)
sys.path.append(BASE_DIR)

# 导入所需模块
from tushare_quant import pro, TRADE_DATE
import theme_trend_sentiment_score as theme_ts

CACHE_DIR = r"d:\mystock\cache_daily"
os.makedirs(CACHE_DIR, exist_ok=True)


def build_theme_stock_map():
    """
    构建主题-个股对应关系映射
    """
    print(f"[开始] 构建主题-个股映射: {TRADE_DATE}")
    
    # 1. 加载主题配置
    theme_path = os.path.join(BASE_DIR, 'theme.json')
    with open(theme_path, 'r', encoding='utf-8') as f:
        hot_themes = json.load(f)['HOT_THEMES']
    print(f"[加载] 共 {len(hot_themes)} 个主题配置")
    
    # 2. 获取东财成分股数据和股票基本信息
    dc_df = theme_ts.get_dc_members()
    try:
        stock_basic_df = pro.stock_basic(fields='ts_code,industry,name')
    except Exception as e:
        print(f"[错误] 获取 stock_basic 失败: {e}")
        return None
    
    # 3. 调用 match_theme_stocks 进行匹配
    # 返回: theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts
    theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_ts.match_theme_stocks(
        hot_themes, dc_df, stock_basic_df
    )
    
    print(f"[匹配] 共 {len(theme_stock_map)} 个主题匹配到成份股")
    
    # 4. 构建正向映射: theme -> [stock, ...]
    MAX_STOCKS_PER_THEME = 300
    MAX_THEMES_PER_STOCK = 5
    # 超低分过滤阈值（分数低于此值的concept_fallback/industry_alias股票被清理）
    LOW_SCORE_THRESHOLD = 5

    # 加载主营业务数据用于基本面验证
    mainbiz_path = os.path.join(CACHE_DIR, 'stock_company_mainbiz.json')
    stock_mainbiz = {}
    if os.path.exists(mainbiz_path):
        with open(mainbiz_path, 'r', encoding='utf-8') as f:
            stock_mainbiz = json.load(f)
        print(f"[加载] 主营业务数据: {len(stock_mainbiz)} 只")

    # 主题→主营业务验证关键词（用于过滤行业泛化误判）
    # 对于 concept_fallback / industry_alias 匹配的股票，main_business 必须包含至少1个关键词
    THEME_MAINBIZ_KEYWORDS = {
        'AI算力基建': ['算力', '数据中心', '服务器', '云计算', 'IDC', '光模块', '芯片', '散热', '液冷', '电源', '机柜', '带宽', 'ICT', 'ICT基础设施', '信息通信', '网络设备', '交换机', '路由器', '网络基础设施', 'IT基础设施', '数据中心建设', '机房', '布线', '光纤通信', '铜连接', 'AEC', 'DAC', '高速互联', '系统集成', '信息技术服务', '数据服务'],
        'AI应用与模型': ['人工智能', 'AI', '大模型', '机器学习', '自然语言', '智能', '软件', '算法', '数据', '云计算', '智能应用', '智能化', '解决方案', '信息技术', '软件开发', '计算机', '智慧', '认知', '识别', '语音', '图像', 'NLP', 'OCR', '知识图谱', '数据挖掘', '数据分析', 'SaaS', 'PaaS', '平台', '数据平台', 'AI平台', '智能中台', '行业大模型', '通用大模型', 'Agent', '智能体'],
        'AI芯片': ['芯片', '半导体', 'GPU', '处理器', '集成电路', '算力', '设计'],
        'AI文娱内容': ['游戏', '影视', '文娱', '传媒', '直播', '短视频', '内容', '文化', '娱乐', '动漫', '数字', '出版'],
        'AI新消费': ['营销', '广告', '传媒', '直播', '电商', '品牌', 'IP', '潮玩', '谷子', '文具', '玩具', '动漫', '文创', '创意', '设计', '衍生品', '潮流', '盲盒', '手办', '卡牌', '游戏游艺', '数字营销', '互动营销', '网红', 'KOL', '社交', '直播电商', '跨境电商', '内容营销', 'IP运营', 'IP授权', '文化用品', '礼品', '精品', '模型', '手办', '扭蛋'],
        '金融科技': ['金融', '银行', '支付', '证券', '保险', '信贷', '区块链', '数字货币', ' fintech', '征信', '清算'],
        '光通信': ['光', '光纤', '光模块', '通信', '激光', '光电', '网络'],
        '特高压': ['特高压', '输电', '变电', '变压器', '换流阀', '高压开关', '绝缘子', '避雷器', '互感器', '组合电器', '断路器', '隔离开关', '电力铁塔', '电缆', '输变电', '输配电设备', '变电站', '直流输电', '交流输电', '开关柜'],
        '电网智能化': ['智能电网', '电网', '虚拟电厂', '配电', '电力软件', '电表', '用电信息', '调度自动化', '电力信息化', '能源管理', '电力物联网', '微电网', '源网荷储', '需求侧响应'],
        '充电桩': ['充电桩', '充电模块', '充电枪', '充电堆', '充电运营', '充电站', '换电站', '换电', 'V2G', '车网互动', '高压快充', '超充', '液冷超充', '充电设备', '充电服务', '有序充电'],
        '半导体制造': ['晶圆代工', '晶圆制造', '芯片制造', '集成电路制造', 'Foundry', '中芯', '华虹', '晶合', '粤芯', '积塔', '燕东微', '士兰微', '华润微', '晶圆生产', '半导体制造', 'IDM'],
        '半导体封测与先进封装': ['封装', '封测', '测试', '先进封装', 'TSV', 'Bumping', 'Chiplet', 'CoWoS', '封装测试', '晶圆级封装', '倒装'],
        '半导体材料': ['硅片', '靶材', '电子特气', '特气', '光刻胶', '抛光液', '抛光垫', '前驱体', '湿电子化学品', 'CMP材料', '电子化学', '光刻气体', '掺杂气体', '硅烷'],
        '半导体设备': ['刻蚀设备', '薄膜沉积', 'PVD', 'CVD', 'ALD', '光刻机', '清洗设备', 'CMP设备', '离子注入', '涂胶显影', '测试机', '探针台', '分选机', '量测设备', '缺陷检测', '半导体设备', '晶圆传输'],
        '功率半导体': ['IGBT', 'MOSFET', '功率半导体', '功率模块', 'SiC', 'GaN', '晶闸管', '二极管', '功率器件', '功率芯片'],
        '存储芯片': ['存储芯片', '存储器', 'DRAM', 'NAND', '闪存', '内存', 'Flash', 'HBM', '高带宽存储', '存算一体', 'SSD', '固态硬盘', 'NOR', 'SRAM', '存储模组'],
        'IC设计': ['芯片设计', '集成电路设计', '模拟芯片', '射频芯片', 'MCU', 'SoC', 'ASIC', 'FPGA', 'DSP', 'Fabless', '无晶圆', '设计服务'],
        '光刻机链': ['光刻机', '光刻镜头', '光学系统', '双工件台', '浸没式', 'DUV', 'EUV', 'ASML', '蔡司', '光刻胶涂布', '对准系统', '光刻机零部件', '光刻机维修', '极紫外光刻'],
        '化工链': ['化工', '化学', '原料', '材料', '肥料', '农药', '塑料', '橡胶', '涂料', '染料'],
        '氢能': ['氢', '燃料电池', '电解水', '储氢', '加氢'],
        '煤炭链': ['煤', '煤炭', '焦煤', '焦炭', '动力煤'],
        '合成生物': ['合成生物', '生物制造', '发酵', '生物', '酶', '基因', '细胞'],
        '人形机器人': ['人形机器人', '机器人', '减速器', '丝杠', '电机', '传感器', '执行器', '关节', '驱动器', '控制器', '伺服', '精密减速', '滚珠丝杠', '行星滚柱', '空心杯电机', '无框电机', '力矩电机', '灵巧手', '线性执行器', '旋转执行器', '运动控制', '机电'],
        '工业母机与自动化': ['机床', '数控', '工控', '自动化', '伺服', '变频器', 'PLC', '工业控制', '机器人', '减速机', '加工中心', '车床', '铣床', '磨床', '钻床', '刨床', '齿轮', '刀具', '夹具', '主轴', '数控系统', '运动控制', '工业软件', '智能制造', '智能装备', '成套设备', '专机', '磨削', '铣削', '车削'],
        '工业智能': ['工业AI', '工业互联网', '工业物联网', 'IIoT', '数字孪生', '工业软件', '智能矿山', '智慧矿山', 'MES', '制造执行系统', 'SCADA', '数据采集与监视控制', 'PLC', '可编程逻辑控制器', 'DCS', '分散控制系统', '智能工厂', '数字化转型', '工业数字化', '工业智能化', '工业信息化', '智能制造', '工业4.0', '工厂自动化', '工业自动化', '工业控制', '工业操作系统', '工业互联网平台', '工业大数据', '工业云', '工业边缘计算', '工业AI平台', '工业智能体', '工业大模型', '工业视觉', '工业质检', '工业安全', '工控安全', '数字工厂', '黑灯工厂', '无人工厂', '智能产线', '自动化产线', '工业数据中台', '工业互联网标识', '工业软件国产化'],
        '工程机械与重型装备': ['工程机械', '挖掘机', '起重机', '矿山机械', '重型装备', '装载机', '推土机', '压路机', '混凝土机械', '叉车', '升降平台', '高空作业', '破碎', '筛分', '输送机械', '带式输送', '水泥机械', '冶金机械', '起重机械', '专用机械', '重型机械', '装备制造', '专用设备', '大型铸件', '铸件'],
        '低空经济': ['低空', '无人机', '飞行器', '航空', '直升', 'eVTOL', '飞行汽车', '通航', '通用航空', '航空器', '飞行控制', '航空电子', '导航', '起飞', '着陆', '飞行', '航空装备', '航空零部件', '螺旋桨', '涡扇', '航电', '空管', '飞行管理', '低空经济', '低空空域'],
        '固态电池': ['电池', '固态', '锂', '正极', '负极', '电解质', '电芯', '电解液', '隔膜', '电池材料', '电池组件', '聚合物电池', '半固态', '凝聚态', '锂电', '锂离子', '动力电池', '电池系统', '电池包', '储能电池', '电池制造'],
        '新型储能': ['储能', '电池', '锂电', '逆变', 'PCS', '充放', '储能系统', '储能变流', '储能集成', '储能项目', '储能装备', '储能产品', '电力储能', '电化学储能', '储能电池', '电池储能', '储能电站', '新能源储能', '移动储能', '工商业储能', '户储', '便携储能', '储能柜', '储能集装箱', '电力电子'],
        '新能源汽车链': ['汽车', '新能源', '电池', '电机', '电控', '充电', '车载', '整车', '乘用车', '商用车', '客车', '专用车', '电动', '混动', '插电', '燃料电池汽车', '汽车零部件', '汽车电子', '汽车配件', '动力电池', '驱动电机', '电驱', '电控系统', 'BMS', '热管理', '汽车空调', '线束', '连接器', '轻量化', '铝合金', '镁合金', '车用材料'],
        '小金属': ['稀土', '钨', '钼', '锂', '钴', '镍', '钛', '锆', '小金属', '钒', '铬', '锰', '铟', '镓', '锗', '铋', '锑', '锡', '钽', '铌', '铍', '锂矿', '钴矿', '镍矿', '钨矿', '钼矿', '稀土矿', '冶炼', '采选', '矿权', '矿产', '勘查', '有色金属'],
        '贵金属': ['金', '银', '铂', '贵金属'],
        '工业金属': ['铜', '铝', '铅', '锌', '锡', '镍', '金属'],
        # ===== 扩展24个主题mainbiz关键词（基于mainbiz采样文本设计）=====
        '商业航天': ['卫星', '航天', '宇航', '运载火箭', '空间段', '火箭', '太空', '航天器', '卫星通信', '卫星导航', '卫星应用', '航天电子', '航天科技', '通信天线', '射频器件', '通信系统', '光纤器件', '红外', 'MEMS', '工业以太网', '通信基站', '宽带移动通信', '雷达', '测控', '航空航天'],
        '核聚变': ['聚变', '超导', '托卡马克', '第一壁', '偏滤器', '核聚变', '人造太阳', '超导磁体', '聚变堆', '高温超导', '超导带材', '核电', '核能', '核承压', '能源装备', '余热锅炉', '清洁能源', '核电专用', '电力电子'],
        '军工': ['航空', '军用', '军品', '导弹', '战车', '战机', '武器', '舰船', '坦克', '雷达', '军事', '国防', '弹药', '战斗机', '发动机', '军用飞机', '兵器', '军械', '舰艇', '卫星', '仿真测试', '特殊应用', '粉末冶金', '锻铸', '液压', '航空产品', '军工', '军用车', '装甲', '导弹', '火箭弹', '智能弹药', '军工电子'],
        '消费电子与AI终端': ['手机', '消费电子', '终端', '屏幕', '显示屏', '摄像头', '连接器', '耳机', '音箱', '可穿戴', '智能终端', '声学', '电声', '视窗', '玻璃', '精密结构件', '模具', '机壳', '射频前端', 'LCD', 'OLED', '显示模组', '触摸屏', '液晶', 'LED外延', 'LED芯片', '光学镜头', '触控显示', '平板显示', '显示组件', '精密金属', '粉末注射', 'MIM', '精密模组', '结构模组'],
        '氟化工制冷剂': ['氟', '制冷剂', '氟化工', '氟碳', '氟材料', '氟化学', '氟精细', '六氟', '氟化', '制冷', '能源化工', '绿色轮胎', '精细化工', '氟精细化学'],
        '光学光电子': ['光学', '光电', '显示', 'LED', '面板', '镜头', '液晶', 'OLED', 'Mini LED', '微纳', '光电器件', '光学元件', '光学镜头', '显示模组', '触摸屏', '光纤器件', '液晶显示', '显示模组'],
        '智能驾驶': ['智能驾驶', '汽车电子', '车载', '自动驾驶', '车联网', 'ADAS', '座舱', '导航', '智能座舱', '域控制', '雷达', '汽车配件', '汽车零部件', '线控', '车载导航', '精密轴', '切削件', '内饰件', '泵', '光学镜头', '称重', '短程通信', '连接器', '屏蔽罩'],
        '医药产业链': ['药', '医药', '制药', '医疗', '器械', '诊断', '生物制品', '疫苗', '原料药', '制剂', '中成药', '中药', '化学药', '生物药', '药用', '药店', '医药商业', 'POCT', '抗体', '胰岛素', '医药中间体', '青霉素', '链霉素', '维生素', '血液制品', '大输液', '肝素', '生物技术', '健康', '医学', '制药业'],
        '发电与电源设备': ['电力', '发电', '电源', '火电', '水电', '核电', '风电', '光伏', '电站', '电网', '输电', '变电', '配电', '电力生产', '热力', '新能源发电', '核能', '风力', '太阳能', '储能', '能源投资', '能源开发', '能源装备', '锻件', '能源资产', '能源服务', '清洁能源', '发电业务', '电力销售'],
        '能源金属': ['锂', '钴', '镍', '铜', '铝', '铅', '锌', '矿', '金属', '采选', '冶炼', '锂矿', '锂盐', '碳酸锂', '氢氧化锂', '钴新材料', '有色金属', '矿权', '矿产', '勘查'],
        '红利公用事业': ['电力', '水务', '燃气', '供热', '热力', '路桥', '高速', '港口', '机场', '公用', '环保', '轨道交通', '天然气', '供水', '污水处理', '发电', '公共交通', '水务', '供气'],
        '石油石化': ['石油', '石化', '油气', '炼化', '原油', '天然气', '成品油', '石油加工', '石油开采', '石油贸易', '石油天然气', '炼油', '化工', '聚酯', '化纤', '丙烯酸', '油田', '钻井', '测录井', '井下作业', '瓦斯', '煤层气', '液化气', '石化产品', '油气田'],
        '脑机接口': ['脑机', '神经', '脑科', '神经康复', '疼痛', '康复', '医疗器械', '智能识别', '医疗'],
        '商超零售链': ['超市', '百货', '零售', '连锁', '商业', '电商', '便利店', '商品零售', '购销', '商业零售', '批发', '商贸', '贸易', '购物', '电子商务', '跨境电商', '母婴', '供应链', '免税', '商品销售', '商业零售及批发', '婴童', '包装印刷'],
        '必选消费红利链': ['酒', '食品', '乳', '调味品', '肉', '家电', '电器', '饮料', '粮油', '日用', '纸', '超市', '零售', '百货', '纺织', '服装', '烟', '白酒', '啤酒', '食用油', '方便食品', '速冻', '罐头', '屠宰'],
        '餐饮食品链': ['食品', '肉', '乳', '调味品', '酒', '饮料', '速冻', '火腿', '罐头', '屠宰', '食用', '餐饮', '啤酒', '白酒', '葡萄酒', '酱油', '食醋', '面米', '火锅', '卤', '烘焙', '饲料', '添加剂', '香精', '奶酪', '黄油', '复合调味料', '鸡粉', '鸡精', '盐', '半成品菜', '发酵肉制品', '低温肉制品', '酱卤', '蛋制品', '水产品', '农副食品'],
        '消费白马': ['家电', '酒', '食品', '乳', '医药', '医疗', '调味品', '厨具', '炊具', '电器', '饮料', '奶', '肉', '白酒', '啤酒', '保健品', '烟草', '中成药', '化学药', '生物药', '医疗器械', '日化', '服装', '纺织', '空调器', '照明', '软饮料', '林业', '盐化工'],
        '家电家居链': ['家电', '空调', '冰箱', '洗衣机', '家居', '家具', '卫浴', '家纺', '插座', '电器', '小家电', '大家电', '厨房', '炊具', '床垫', '软体家具', '毛巾', '纺织品', '家用电器', '智能家居', '照明', '灯具', '厨卫', '净水', '保温器皿', '遮阳材料', '数字智能终端', '智能终端', '通信模块', 'App应用', '鞋服', '贴身服饰', '床用纺织品'],
        '交通运输物流': ['运输', '物流', '港口', '机场', '铁路', '高速', '航运', '船运', '快递', '仓储', '货运', '客运', '航空', '水运', '海运', '路桥', '轨道交通', '高速公路', '码头', '装卸', '供应链', '集装箱', '船舶', '出租车', '高铁', '铁路运输', '物流服务', '综合物流', '保税', '流通加工', '通关', '配送'],
        '大农业': ['农业', '种植', '养殖', '饲料', '化肥', '种业', '农药', '水产', '畜牧', '屠宰', '农作物', '种子', '渔业', '农产品', '生猪', '家禽', '食用菌', '动物营养', '疫苗', '兽药', '果园', '林木', '对虾', '罗非鱼', '玉米', '蔬菜', '水稻', '预混料', '配合饲料', '营养添加剂'],
        '培育钻石': ['金刚石', '钻石', '超硬材料', '超硬刀具', '超硬制品', '人造金刚石', '金刚石线', '金刚石工具', '粉末冶金', '电镀金刚石'],
        '被动元件': ['电容器', '电容', '电感', '电阻', '电子元器件', '电子陶瓷', '压敏', '石英晶体', '载带', '片式', '薄膜电容', '铝电解', '超级电容', '谐振器', '传感器', '电子元件', '功能陶瓷', '陶瓷材料'],
        '基建地产链': ['建筑', '工程', '基建', '地产', '房地产', '水泥', '建材', '施工', '勘察', '开发', '基础设施建设', '房屋建筑', '工程承包', '建筑装饰', '装修', '钢结构', '管材', '玻璃', '防水', '涂料', '瓷砖', '门窗', '五金', '工程装备', '模块', '公路', '桥梁', '隧道', '高等级公路', '养护', '园区运营'],
        'PCB电子电路': ['PCB', '印刷电路', '电路板', '印制电路', '覆铜板', '电子装联', '印制线路板', '高密度印制', '电子树脂', '铜箔', '玻纤布', '线路板', '电感器', '压敏电阻', '钽电容', '电子元器件', '化学试剂', '环氧树脂', '油墨', '电子材料', '专用装备', '电子级'],
    }

    # ST股票灰名单过滤
    ST_FILTER_ENABLED = True
    # 主题-行业互斥规则：主题名 -> 不应包含的行业列表
    THEME_INDUSTRY_EXCLUDE = {
        '工业金属': ['煤炭开采', '造纸', '钢加工', '化学原料'],
        '小金属': ['煤炭开采', '造纸', '钢加工'],
        '贵金属': ['铜', '铅锌'],
        '券商': [],
        '钢铁': [],
        '银行': [],
        '保险': [],
        '合成生物': ['养殖业', '生猪', '畜禽饲料', 'CDMO', '医疗服务'],
        '功率半导体': ['汽车零部件', '汽车配件', '燃油', '内燃机', '矿物制品'],
        'AI应用与模型': ['教育', '职业教育', '培训'],
        '金融科技': ['游戏', '移动游戏', '网红经济', '营销服务'],
        'AI文娱内容': ['基建', '勘察', '交通工程', '建筑设计'],
        'AI算力基建': ['电力设备', '输变电', '配电设备'],
        '煤炭链': ['化学制品', '化学原料', '化工原料', '化工', '塑料'],
        # 特高压/充电桩排除软件IT类（归入电网智能化），保留电气设备/汽车配件类
        '特高压': ['软件服务', 'IT设备', '互联网', '出版业', '影视音像', '广告包装'],
        '充电桩': ['软件服务', 'IT设备', '互联网', '出版业', '影视音像', '广告包装'],
        # 半导体8主题统一排除非半导体相关行业（北交所低质量股票常属这些行业）
        # 注：半导体材料不排除化工原料（华特气体/金宏气体等属化工原料行业）
        '半导体制造': ['矿物制品', '汽车配件', '汽车零部件'],
        '半导体封测与先进封装': ['化工原料', '矿物制品', '汽车配件', '汽车零部件'],
        '半导体材料': ['矿物制品', '汽车配件', '汽车零部件'],
        '半导体设备': ['化工原料', '矿物制品', '汽车配件', '汽车零部件'],
        '存储芯片': ['化工原料', '矿物制品', '汽车配件', '汽车零部件'],
        'IC设计': ['化工原料', '矿物制品', '汽车配件', '汽车零部件'],
        '光刻机链': ['矿物制品', '汽车配件', '汽车零部件'],
    }
    # 主题-行业白名单：主题只允许特定行业的股票
    THEME_INDUSTRY_WHITELIST = {
        '券商': ['证券', '资本市场服务'],
        '银行': ['银行'],
        '保险': ['保险'],
        '钢铁': ['普钢', '特钢', '钢铁'],
        # 电网智能化强制只保留软件/IT类公司，与电气设备类（特高压/充电桩）分离
        # 注：tushare东财行业分类中"电气设备"涵盖电网设备+电源设备+自动化设备，
        # 无法用行业白名单区分特高压和充电桩，改用跨主题互斥规则处理
        '电网智能化': ['软件服务', 'IT设备'],
    }
    # 主题-行业白名单+mainbiz关键词组合约束：行业属白名单OR mainbiz含主题关键词
    # 金融科技：纯IT软件公司大量混入，要求行业属多元金融OR mainbiz含金融关键词
    THEME_WHITELIST_OR_MAINBIZ = {
        '金融科技': {
            'whitelist': ['多元金融', '资本市场服务'],
            'keywords': ['金融', '银行', '支付', '证券', '保险', '信贷', '区块链', '数字货币', '征信', '清算', '互联网金融', '券商IT'],
        },
    }
    # 主题-股票黑名单：明确不应归入该主题的股票（基于AI分析）
    THEME_STOCK_BLACKLIST = {
        'AI算力基建': ['思源电气', '中国宝安', '诺德股份'],
        '消费电子与AI终端': ['禾盛新材', '慧谷新材'],
        'AI应用与模型': ['中公教育', '霍莱沃'],
        '金融科技': ['汤姆猫', '天下秀'],
        'AI文娱内容': ['华设集团'],
        '合成生物': ['牧原股份', '凯莱英', '双成药业'],
        '功率半导体': ['威孚高科'],
        # 新增6只行业明显不匹配的误判（mainbiz与主题完全无关）
        '医药产业链': ['利民股份', '富邦科技'],
        '商超零售链': ['珠免集团'],
        '必选消费红利链': ['泉阳泉'],
        '消费白马': ['泉阳泉'],
        '石油石化': ['首华燃气'],
        '家电家居链': ['辰奕智能'],
        '固态电池': ['德新科技'],
    }

    themes_output_raw = {}
    total_stock_refs_raw = 0
    for theme_name, stocks in theme_stock_map.items():
        stock_list = []
        for code, meta in stocks.items():
            stock_name = name_map_basic.get(code, code)
            stock_industry = stock_basic_industry.get(code, "")
            if not isinstance(stock_industry, str):
                stock_industry = ""
            stock_via = meta.get("via", "")

            # ST股票过滤
            if ST_FILTER_ENABLED and ('ST' in stock_name or '*ST' in stock_name or 'ST' in stock_name.upper()):
                continue

            # 主题-股票黑名单过滤
            if theme_name in THEME_STOCK_BLACKLIST:
                if stock_name in THEME_STOCK_BLACKLIST[theme_name]:
                    continue

            # 主题-行业白名单强制约束（core/leader公司豁免，保留主题龙头）
            if theme_name in THEME_INDUSTRY_WHITELIST and stock_via not in ('core_company', 'leader_company'):
                whitelist = THEME_INDUSTRY_WHITELIST[theme_name]
                if not any(w in stock_industry for w in whitelist):
                    continue

            # 主题-行业白名单+mainbiz组合约束（core/leader豁免）
            # 行业属白名单 OR mainbiz含主题关键词，否则排除
            if theme_name in THEME_WHITELIST_OR_MAINBIZ and stock_via not in ('core_company', 'leader_company'):
                rule = THEME_WHITELIST_OR_MAINBIZ[theme_name]
                in_whitelist = any(w in stock_industry for w in rule['whitelist'])
                if not in_whitelist:
                    mb = stock_mainbiz.get(code, '')
                    if not mb or not any(kw in mb for kw in rule['keywords']):
                        continue

            # 北交所股票质量过滤：非core/leader要求score≥15
            # 北交所小盘股quality参差，提高门槛过滤低质量成份股
            if code.endswith('.BJ') and stock_via not in ('core_company', 'leader_company'):
                stock_score_preview = meta.get("score", 0)
                if stock_score_preview < 15:
                    continue

            # 主题-行业互斥规则
            if theme_name in THEME_INDUSTRY_EXCLUDE:
                excluded = THEME_INDUSTRY_EXCLUDE[theme_name]
                if any(ex in stock_industry for ex in excluded):
                    continue

            # 超低分过滤：concept_fallback和industry_alias来源的低分股票清理
            stock_score = meta.get("score", 0)
            if stock_via in ('concept_fallback', 'stock_basic_industry_alias') and stock_score < LOW_SCORE_THRESHOLD:
                continue

            # 主营业务验证：对非核心来源的股票，用主营业务文本验证主题相关性
            # 电力三主题和半导体主题对dc_industry_board来源也强制验证
            # 半导体主题对stock_basic_industry来源也强制验证（避免LED/被动元件等混入）
            mainbiz_check_vias = ('concept_fallback', 'stock_basic_industry_alias', 'concept_as_industry')
            if theme_name in ('特高压', '电网智能化', '充电桩',
                              '半导体制造', '半导体封测与先进封装', '半导体材料', '半导体设备',
                              '功率半导体', '存储芯片', 'IC设计', '光刻机链'):
                mainbiz_check_vias = mainbiz_check_vias + ('dc_industry_board', 'stock_basic_industry')
            # 半导体主题对北交所股票无mainbiz数据时强制清理（core/leader豁免）
            # 北交所小盘股quality参差，无mainbiz数据无法验证主题相关性
            semi_themes = ('半导体制造', '半导体封测与先进封装', '半导体材料', '半导体设备',
                           '功率半导体', '存储芯片', 'IC设计', '光刻机链')
            if theme_name in semi_themes and code.endswith('.BJ') and stock_via not in ('core_company', 'leader_company'):
                if not stock_mainbiz.get(code, ''):
                    continue
            if stock_via in mainbiz_check_vias:
                if theme_name in THEME_MAINBIZ_KEYWORDS:
                    mainbiz_text = stock_mainbiz.get(code, '')
                    keywords = THEME_MAINBIZ_KEYWORDS[theme_name]
                    if mainbiz_text:
                        # 有主营业务数据：必须匹配关键词
                        if not any(kw in mainbiz_text for kw in keywords):
                            continue
                        # 存储芯片特殊处理：mainbiz含"晶圆代工"的股票要求同时含明确存储关键词
                        # 避免华虹宏力等代工企业（仅工艺平台含存储器）被误归入存储芯片
                        if theme_name == '存储芯片' and ('晶圆代工' in mainbiz_text or '晶圆制造' in mainbiz_text):
                            strict_kws = ['存储芯片', 'DRAM', 'NAND', '闪存', 'Flash', 'HBM', '存算一体', 'SSD', '固态硬盘', 'NOR', 'SRAM', '存储模组']
                            if not any(kw in mainbiz_text for kw in strict_kws):
                                continue
                    elif theme_name in ('特高压', '电网智能化', '充电桩',
                                        '半导体制造', '存储芯片', '光刻机链') and stock_via == 'dc_industry_board':
                        # 重点主题无mainbiz数据的dc_industry_board来源：要求较高分数（≥20）
                        if stock_score < 20:
                            continue

            stock_list.append({
                "code": code,
                "name": stock_name,
                "via": stock_via,
                "chain_distance": meta.get("chain_distance", 2),
                "industry_match": meta.get("industry_match", False),
                "score": stock_score,
                "industry": stock_industry,
                "concepts": stock_concepts.get(code, []),
            })
            total_stock_refs_raw += 1
        stock_list.sort(key=lambda x: -x['score'])
        themes_output_raw[theme_name] = stock_list

    stocks_output_raw = {}
    for theme_name, stock_list in themes_output_raw.items():
        for s in stock_list:
            code = s['code']
            if code not in stocks_output_raw:
                stocks_output_raw[code] = {
                    "name": s['name'],
                    "industry": s['industry'],
                    "concepts": s['concepts'],
                    "themes": [],
                    "scores": {},
                    "vias": {},
                }
            stocks_output_raw[code]["themes"].append(theme_name)
            stocks_output_raw[code]["scores"][theme_name] = s['score']
            stocks_output_raw[code]["vias"][theme_name] = s['via']

    # 限制每只股票最多保留 MAX_THEMES_PER_STOCK 个主题，按优先级排序
    # 跨主题审核：归属3+主题且多为concept_fallback的股票，限制最多3个主题
    # 跨主题互斥：某些主题对不应同时出现在同一股票上
    THEME_MUTEX_PAIRS = [
        ('AI文娱内容', '特高压'),
        ('AI文娱内容', '电网智能化'),
        ('AI文娱内容', '充电桩'),
        ('AI文娱内容', '发电与电源设备'),
        ('AI文娱内容', '基建地产链'),
        ('AI文娱内容', '交通运输物流'),
        ('金融科技', 'AI文娱内容'),
        ('金融科技', 'AI新消费'),
        ('合成生物', '大农业'),
        ('AI算力基建', 'AI文娱内容'),
        ('AI算力基建', 'AI新消费'),
        ('AI应用与模型', '金融科技'),
        ('人形机器人', '工业母机与自动化'),
        # 人形机器人↔新能源汽车链互斥：减速器/丝杠/电机公司优先归入人形机器人
        # 注：核心电池/整车/汽配公司仍归新能源汽车链，通过via_priority和score自然选择
        ('人形机器人', '新能源汽车链'),
        # 电力三主题互斥：按主导业务强制归属单一主题
        ('特高压', '电网智能化'),
        ('特高压', '充电桩'),
        ('电网智能化', '充电桩'),
        # 半导体主题互斥：制造/封测/材料/设备/功率/存储/IC设计/光刻机链 两两互斥
        # 确保一只半导体股票只归入最匹配的子主题
        ('半导体制造', '半导体封测与先进封装'),
        ('半导体制造', '半导体材料'),
        ('半导体制造', '半导体设备'),
        ('半导体制造', '功率半导体'),
        ('半导体制造', '存储芯片'),
        ('半导体制造', 'IC设计'),
        ('半导体制造', '光刻机链'),
        ('半导体封测与先进封装', '半导体材料'),
        ('半导体封测与先进封装', '半导体设备'),
        ('半导体封测与先进封装', '光刻机链'),
        ('半导体材料', '半导体设备'),
        ('半导体材料', '光刻机链'),
        ('半导体设备', '光刻机链'),
        ('存储芯片', '半导体设备'),
        ('存储芯片', '半导体材料'),
        ('存储芯片', '半导体封测与先进封装'),
        ('存储芯片', '光刻机链'),
        ('功率半导体', '半导体封测与先进封装'),
        ('IC设计', '半导体封测与先进封装'),
        ('IC设计', '半导体制造'),
        ('IC设计', '存储芯片'),
        ('IC设计', '光刻机链'),
    ]
    
    stocks_output = {}
    via_priority = {'leader_company': 4, 'core_company': 3, 'dc_industry_board': 2, 'stock_basic_industry': 2, 'stock_basic_industry_alias': 1, 'concept_as_industry': 1, 'concept_fallback': 0}
    for code, info in stocks_output_raw.items():
        theme_items = [(t, info['scores'][t], info['vias'][t]) for t in info['themes']]
        theme_items.sort(key=lambda x: (-via_priority.get(x[2], -1), -x[1]))
        
        # 跨主题审核：如果前5个主题中concept_fallback占比≥60%，则限制最多3个主题
        top_candidates = theme_items[:MAX_THEMES_PER_STOCK]
        fallback_count = sum(1 for t in top_candidates if t[2] == 'concept_fallback')
        if len(top_candidates) >= 3 and fallback_count / len(top_candidates) >= 0.6:
            max_for_this_stock = 3
        elif len(top_candidates) >= 4:
            # 跨4+主题股票强制精简到3个，避免"万金油"公司主题标签过多
            max_for_this_stock = 3
        else:
            max_for_this_stock = MAX_THEMES_PER_STOCK
        
        # 跨主题互斥：按优先级依次选主题，若与已选主题互斥则跳过
        selected_themes = []
        for t in theme_items:
            if len(selected_themes) >= max_for_this_stock:
                break
            is_mutex = False
            for existing_theme, _, _ in selected_themes:
                for pair in THEME_MUTEX_PAIRS:
                    if (t[0] == pair[0] and existing_theme == pair[1]) or \
                       (t[0] == pair[1] and existing_theme == pair[0]):
                        is_mutex = True
                        break
                if is_mutex:
                    break
            if not is_mutex:
                selected_themes.append(t)
        
        stocks_output[code] = {
            "name": info["name"],
            "industry": info["industry"],
            "concepts": info["concepts"],
            "themes": [t[0] for t in selected_themes],
        }

    # 根据过滤后的股票→主题映射，重新构建主题→股票映射，并限制最大成份股数
    themes_output = {}
    total_stock_refs = 0
    for code, info in stocks_output.items():
        for theme_name in info["themes"]:
            if theme_name not in themes_output:
                themes_output[theme_name] = []
            meta = theme_stock_map[theme_name].get(code, {})
            stock_name = name_map_basic.get(code, code)
            themes_output[theme_name].append({
                "code": code,
                "name": stock_name,
                "via": meta.get("via", ""),
                "chain_distance": meta.get("chain_distance", 2),
                "industry_match": meta.get("industry_match", False),
                "score": meta.get("score", 0),
                "industry": stock_basic_industry.get(code, ""),
                "concepts": stock_concepts.get(code, []),
            })

    for theme_name in themes_output:
        themes_output[theme_name].sort(key=lambda x: -x['score'])
        themes_output[theme_name] = themes_output[theme_name][:MAX_STOCKS_PER_THEME]

    # 重新构建股票→主题映射（确保一致性）
    stocks_output = {}
    for theme_name, stocks in themes_output.items():
        for s in stocks:
            code = s["code"]
            if code not in stocks_output:
                stocks_output[code] = {
                    "name": s["name"],
                    "industry": s["industry"],
                    "concepts": s["concepts"],
                    "themes": [],
                }
            stocks_output[code]["themes"].append(theme_name)

    for code in stocks_output:
        theme_list = stocks_output[code]["themes"]
        theme_with_score = []
        for t in theme_list:
            if t in theme_stock_map and code in theme_stock_map[t]:
                theme_with_score.append((t, theme_stock_map[t][code].get("score", 0)))
            else:
                theme_with_score.append((t, 0))
        theme_with_score.sort(key=lambda x: -x[1])
        stocks_output[code]["themes"] = [t[0] for t in theme_with_score]

    total_stock_refs = sum(len(stocks) for stocks in themes_output.values())
    
    # 6. 组装最终 JSON
    output = {
        "trade_date": TRADE_DATE,
        "update_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "n_themes": len(themes_output),
        "n_stocks": len(stocks_output),
        "n_stock_refs": total_stock_refs,
        "themes": themes_output,
        "stocks": stocks_output,
    }
    
    # 7. 保存到缓存目录
    output_file = os.path.join(CACHE_DIR, f"theme_stock_map_{TRADE_DATE}.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # 同时更新最新版本（无日期后缀，方便引用）
    latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"[保存] {output_file}")
    print(f"[保存] {latest_file}")
    print(f"[统计] {len(themes_output)} 个主题, {len(stocks_output)} 只个股, {total_stock_refs} 条映射关系")
    
    return output


def load_theme_stock_map(trade_date=None):
    """加载指定日期的主题-个股映射"""
    if trade_date is None:
        latest_file = os.path.join(CACHE_DIR, "theme_stock_map_latest.json")
        if os.path.exists(latest_file):
            with open(latest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    cache_file = os.path.join(CACHE_DIR, f"theme_stock_map_{trade_date}.json")
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def get_stock_themes(ts_code, trade_date=None):
    """查询某只股票所属的所有主题"""
    data = load_theme_stock_map(trade_date)
    if data and ts_code in data.get("stocks", {}):
        return data["stocks"][ts_code]
    return None


def get_theme_stocks(theme_name, trade_date=None):
    """查询某个主题的所有成份股"""
    data = load_theme_stock_map(trade_date)
    if data and theme_name in data.get("themes", {}):
        return data["themes"][theme_name]
    return None


if __name__ == '__main__':
    build_theme_stock_map()
    
    # 测试查询
    print("\n=== 测试查询 ===")
    data = load_theme_stock_map()
    if data:
        # 测试个股查询
        test_codes = ['600487.SH', '300308.SZ']
        for code in test_codes:
            info = get_stock_themes(code)
            if info:
                print(f"{info['name']}({code}): 主题={info['themes']}")
        
        # 测试主题查询
        test_themes = ['光通信', '人形机器人']
        for theme in test_themes:
            stocks = get_theme_stocks(theme)
            if stocks:
                print(f"{theme}: {len(stocks)} 只成份股")
                for s in stocks[:5]:
                    print(f"  {s['code']} {s['name']} via={s['via']} score={s['score']}")
