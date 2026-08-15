from .base import StorageBackend, StorageRecord
from .manager import DistributedStorageManager
from .backends import MongoDBBackend, PostgreSQLBackend, MySQLBackend

try:
    from .backends import ElasticsearchBackend
except ImportError:
    ElasticsearchBackend = None  # elasticsearch 为可选依赖

__all__ = [
    "StorageBackend",
    "StorageRecord",
    "DistributedStorageManager",
    "MongoDBBackend",
    "PostgreSQLBackend",
    "MySQLBackend",
    "ElasticsearchBackend",
]
