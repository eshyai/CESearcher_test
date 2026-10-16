"""Milvus 调用的统一保护层。

背景：pymilvus 的 ``Collection.load()`` 默认**无限等待**。Milvus 侧一旦异常
（例如 etcd 元数据与 DataNode 实例注册不一致），``load()`` 不会抛错，而是永久挂起，
把整条请求链路一起拖死——上层写好的所有降级分支都永远等不到执行。
本项目里这已经真实发生过一次（检索主入口挂死 49 小时）。

结论：Milvus 对本项目是「增强」而非「必需」。所有 Milvus 调用必须经由本模块收口，
超时即抛 ``MilvusUnavailableError``，让「降级为关键词检索」这个分支真正可达。

用法::

    from service.milvus_guard import safe_load, MilvusUnavailableError

    collection = Collection(name)
    safe_load(collection, name)      # 失败抛 MilvusUnavailableError
"""
import logging
import os
from typing import Optional

from pymilvus import Collection

logger = logging.getLogger(__name__)

# 单次 Milvus 加载超时（秒）。可用环境变量 MILVUS_OP_TIMEOUT 覆盖。
MILVUS_OP_TIMEOUT = int(os.getenv("MILVUS_OP_TIMEOUT", "15"))


class MilvusUnavailableError(RuntimeError):
    """Milvus 不可用（加载 / 查询超时或报错）。

    单独一个异常类型，是为了让调用方能区分「向量库坏了」与「向量库正常但没有结果」：
    前者应退化为关键词检索，后者是正常的空结果。
    """


def safe_load(
    collection: Collection, name: str, timeout: Optional[int] = None
) -> None:
    """带超时地加载集合；失败或超时抛 ``MilvusUnavailableError``。

    Args:
        collection: 已构造的 Collection 对象
        name: 集合名（仅用于错误信息）
        timeout: 覆盖默认超时（秒）
    """
    t = timeout or MILVUS_OP_TIMEOUT
    try:
        collection.load(timeout=t)
    except Exception as e:
        raise MilvusUnavailableError(
            f"集合 {name} 加载失败（超时 {t}s）: {type(e).__name__}: {str(e)[:200]}"
        ) from e


def safe_search(collection: Collection, name: str, **kwargs):
    """带兜底的向量检索；失败抛 ``MilvusUnavailableError``。

    与 ``safe_load`` 同一目的：把「向量库坏了」变成一个可捕获的异常，
    而不是让调用方无限等待。
    """
    try:
        return collection.search(**kwargs)
    except MilvusUnavailableError:
        raise
    except Exception as e:
        raise MilvusUnavailableError(
            f"集合 {name} 向量检索失败: {type(e).__name__}: {str(e)[:200]}"
        ) from e
