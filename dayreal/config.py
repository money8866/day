import yaml
import os
import csv
from datetime import datetime


class Config:
    DEFAULT_CONFIG = {
        'files': {
            'concepts_csv': 'concepts.csv',
            'stocks_csv': 'stocks.csv',
            'position_csv': 'position_config.csv'
        },
        'warning': {
            'ma5_threshold': 0.02,
            'stock_check_interval': 10,
            'concept_check_interval': 60
        },
        'notification': {
            'enabled': False,
            'method': 'print'
        }
    }

    def __init__(self, config_path='config.yaml'):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(self.base_dir, config_path)
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        return self.DEFAULT_CONFIG

    def save_config(self):
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)

    def get(self, key, default=None):
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def load_concepts(self, csv_path=None):
        if csv_path is None:
            csv_path = os.path.join(self.base_dir, self.get('files.concepts_csv', 'concepts.csv'))
        else:
            csv_path = os.path.join(self.base_dir, csv_path)
        
        if not os.path.exists(csv_path):
            return []
        
        concepts = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'concept' in row:
                    concepts.append(row['concept'].strip())
        return concepts

    def load_stocks(self, csv_path=None):
        if csv_path is None:
            csv_path = os.path.join(self.base_dir, self.get('files.stocks_csv', 'stocks.csv'))
        else:
            csv_path = os.path.join(self.base_dir, csv_path)
        
        if not os.path.exists(csv_path):
            return []
        
        stocks = []
        with open(csv_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        if lines:
            first_line = lines[0].strip()
            if first_line.startswith('code') or first_line.startswith('代码'):
                reader = csv.DictReader(lines)
                for row in reader:
                    if 'code' in row:
                        stocks.append(row['code'].strip())
                    elif '代码' in row:
                        stocks.append(row['代码'].strip())
            else:
                for line in lines:
                    code = line.strip()
                    if code and len(code) == 6:
                        stocks.append(code)
        return stocks
    
    def load_base_position(self, csv_path=None, target_date=None):
        """
        加载指定日期的基础仓位配置
        
        Args:
            csv_path: 仓位配置CSV文件路径
            target_date: 目标日期，格式YYYY-MM-DD，None则使用今天
            
        Returns:
            基础仓位比例 (0-1)，如果没有配置则返回默认0.5
        """
        if csv_path is None:
            csv_path = os.path.join(self.base_dir, self.get('files.position_csv', 'position_config.csv'))
        else:
            csv_path = os.path.join(self.base_dir, csv_path)
        
        if not os.path.exists(csv_path):
            return 0.5, "文件不存在，使用默认仓位"
        
        if target_date is None:
            target_date = datetime.now().strftime('%Y-%m-%d')
        
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('date') == target_date:
                        position = float(row.get('base_position', 0.5))
                        notes = row.get('notes', '')
                        return max(0, min(1, position)), notes
            
            # 如果没有找到指定日期，返回最新的
            print(f"未找到 {target_date} 的仓位配置，使用默认值 0.5")
            return 0.5, "使用默认仓位"
            
        except Exception as e:
            print(f"读取仓位配置失败: {e}")
            return 0.5, "配置读取失败，使用默认"

