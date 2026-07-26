"""pytest 配置."""
import pytest


def pytest_collection_modifyitems(items):
    """自动为所有异步测试添加 asyncio 标记."""
    for item in items:
        if "async" in item.keywords or _has_async_test_function(item):
            item.add_marker(pytest.mark.asyncio)


def _has_async_test_function(item):
    """检查测试是否包含 async def 函数."""
    if hasattr(item, "obj") and hasattr(item.obj, "__code__"):
        import inspect
        return inspect.iscoroutinefunction(item.obj)
    return False
