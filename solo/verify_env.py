#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
环境验证脚本
用于检查主线板块 + 中军分析系统的运行环境
"""

import sys
import os

def print_header(title):
    print("\n" + "="*50)
    print(f"  {title}")
    print("="*50)

def check_python():
    print_header("Python 版本检查")
    print(f"Python 版本: {sys.version}")
    major, minor = sys.version_info[:2]
    if major >= 3 and minor >= 8:
        print("✓ Python 版本符合要求 (需要 3.8+)")
        return True
    else:
        print("✗ Python 版本过低，请升级到 3.8+")
        return False

def check_dependencies():
    print_header("依赖包检查")
    dependencies = [
        ("tushare", "1.2.60"),
        ("pandas", "1.5.0"),
        ("numpy", "1.23.0"),
        ("dotenv", "0.21.0"),
    ]
    
    all_ok = True
    for pkg_name, min_version in dependencies:
        try:
            if pkg_name == "dotenv":
                import dotenv
                print(f"✓ {pkg_name} 已安装")
            else:
                module = __import__(pkg_name)
                version = getattr(module, "__version__", "unknown")
                print(f"✓ {pkg_name} {version} 已安装")
        except ImportError:
            print(f"✗ {pkg_name} 未安装")
            all_ok = False
    return all_ok

def check_env_file():
    print_header("配置文件检查")
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "TUSHARE.env")
    if os.path.exists(env_path):
        print(f"✓ 找到配置文件: {env_path}")
        
        from dotenv import load_dotenv
        load_dotenv(env_path)
        token = os.getenv("TUSHARE_TOKEN")
        if token:
            print(f"✓ TUSHARE_TOKEN 已配置")
        else:
            print("✗ TUSHARE_TOKEN 未配置")
            return False
        return True
    else:
        print(f"✗ 未找到配置文件: {env_path}")
        return False

def check_cache_dir():
    print_header("缓存目录检查")
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache_backbone")
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir, exist_ok=True)
        print(f"✓ 创建缓存目录: {cache_dir}")
    else:
        print(f"✓ 缓存目录存在: {cache_dir}")
    return True

def main():
    print("="*50)
    print("  主线板块 + 中军分析系统 - 环境验证")
    print("="*50)
    
    results = []
    results.append(("Python 环境", check_python()))
    results.append(("依赖包", check_dependencies()))
    results.append(("配置文件", check_env_file()))
    results.append(("缓存目录", check_cache_dir()))
    
    print_header("验证结果汇总")
    all_passed = True
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{name}: {status}")
        if not result:
            all_passed = False
    
    print("\n" + "="*50)
    if all_passed:
        print("✓ 环境验证通过！可以开始使用系统")
        print("\n运行命令:")
        print("  python main_with_backbone.py")
    else:
        print("✗ 环境验证失败，请检查上述问题")
        print("\n请运行:")
        print("  setup_env.bat (Windows)")
        print("  或")
        print("  pip install -r requirements.txt")
    print("="*50)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
