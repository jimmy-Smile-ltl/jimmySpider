"""
jimmySpider 统一配置管理。

支持三层配置，优先级从高到低：
    1. 环境变量（最高优先级）
    2. YAML 配置文件
    3. 默认值

配置文件查找顺序（找到第一个即停止）：
    1. JIMMYSPIDER_CONFIG_FILE 环境变量指定的路径
    2. 当前工作目录下的 jimmyspider.yaml
    3. 当前工作目录下的 jimmyspider.yml
    4. 用户目录下的 ~/.jimmyspider.yaml
"""

import os
from pathlib import Path
from typing import Optional, Any


def _load_yaml_config(filepath: str) -> dict:
    """加载 YAML 配置文件，返回字典。加载失败返回空字典。"""
    try:
        import yaml
    except ImportError:
        # PyYAML 未安装，尝试手动解析简单 YAML
        return _parse_simple_yaml(filepath)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_simple_yaml(filepath: str) -> dict:
    """不依赖 PyYAML 的简化 YAML 解析器，支持基本键值对。"""
    result = {}
    current_section = None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                # 跳过空行和注释
                if not stripped or stripped.startswith("#"):
                    continue
                # 跳过嵌套结构（不以简单 key: value 开头的）
                if stripped.startswith("-"):
                    continue
                # 检测 section 头
                if ":" in stripped and not stripped[0].isspace():
                    key, _, value = stripped.partition(":")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if value == "":
                        current_section = key
                    else:
                        result[key] = _convert_value(value)
                # 嵌套键
                elif current_section and ":" in stripped:
                    key, _, value = stripped.partition(":")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if value:
                        result[f"{current_section}.{key}"] = _convert_value(value)
    except Exception:
        pass
    return result


def _convert_value(value: str) -> Any:
    """将字符串转为合适的 Python 类型"""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    if value.lower() in ("null", "none", "~"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _resolve_config_path() -> Optional[str]:
    """按优先级查找配置文件路径"""
    # 1. 环境变量指定
    env_path = os.getenv("JIMMYSPIDER_CONFIG_FILE")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2. 当前目录
    cwd = Path.cwd()
    for name in ("jimmyspider.yaml", "jimmyspider.yml"):
        candidate = cwd / name
        if candidate.is_file():
            return str(candidate)

    # 3. 用户目录
    home = Path.home()
    home_config = home / ".jimmyspider.yaml"
    if home_config.is_file():
        return str(home_config)

    return None


class Config:
    """框架配置单例。

    使用方式:
        from jimmyspider.config import get_config
        cfg = get_config()
        print(cfg.MONGO_URI)
    """

    def __init__(self):
        # 加载 YAML 配置文件
        yaml_config = {}
        config_path = _resolve_config_path()
        if config_path:
            yaml_config = _load_yaml_config(config_path)

        def _get(key: str, env_var: str, default: Any) -> Any:
            """三层取值：环境变量 > YAML 配置 > 默认值"""
            env_val = os.getenv(env_var)
            if env_val is not None:
                # 类型转换
                if isinstance(default, bool):
                    return env_val.lower() in ("true", "1", "yes")
                if isinstance(default, int):
                    return int(env_val)
                return env_val
            # YAML 中用点号表示嵌套：mongo.uri, redis.host
            yaml_val = yaml_config.get(key) or yaml_config.get(env_var.lower())
            if yaml_val is not None:
                return yaml_val
            return default

        # --- MongoDB ---
        self.MONGO_URI: str = _get(
            "mongo_uri", "JIMMYSPIDER_MONGO_URI", "mongodb://localhost:27017/"
        )
        self.MONGO_DB: str = _get(
            "mongo_db", "JIMMYSPIDER_MONGO_DB", "jimmyspider"
        )

        # --- Redis ---
        self.REDIS_HOST: str = _get(
            "redis_host", "JIMMYSPIDER_REDIS_HOST", "127.0.0.1"
        )
        self.REDIS_PORT: int = _get(
            "redis_port", "JIMMYSPIDER_REDIS_PORT", 6379
        )
        self.REDIS_PASSWORD: Optional[str] = _get(
            "redis_password", "JIMMYSPIDER_REDIS_PASSWORD", None
        )
        self.REDIS_DB: int = _get(
            "redis_db", "JIMMYSPIDER_REDIS_DB", 0
        )

        # --- 数据存储 ---
        self.DATA_DIR: str = _get(
            "data_dir",
            "JIMMYSPIDER_DATA_DIR",
            str(Path.home() / "spider_files"),
        )

        # --- 代理 ---
        self.PROXY_TUNNEL_URL: Optional[str] = _get(
            "proxy_tunnel_url", "JIMMYSPIDER_PROXY_TUNNEL_URL", None
        )
        self.PROXY_API_URL: Optional[str] = _get(
            "proxy_api_url", "JIMMYSPIDER_PROXY_API_URL", None
        )

        # --- Clash 代理池 ---
        self.CLASH_API_URL: str = _get(
            "clash_api_url", "JIMMYSPIDER_CLASH_API_URL", "http://127.0.0.1:9097"
        )
        self.CLASH_SECRET: str = _get(
            "clash_secret", "JIMMYSPIDER_CLASH_SECRET", ""
        )
        self.CLASH_PROXY_URL: str = _get(
            "clash_proxy_url", "JIMMYSPIDER_CLASH_PROXY_URL", "http://127.0.0.1:7897"
        )
        self.CLASH_POLICY_GROUP: str = _get(
            "clash_policy_group", "JIMMYSPIDER_CLASH_POLICY_GROUP", "自动选择"
        )

        # --- 日志 ---
        self.LOG_DIR: Optional[str] = _get(
            "log_dir", "JIMMYSPIDER_LOG_DIR", None
        )

        # --- SSL ---
        self.SSL_CERT_FILE: Optional[str] = _get(
            "ssl_cert_file", "JIMMYSPIDER_SSL_CERT_FILE", None
        )

        # --- 记录配置来源 ---
        self._config_file: Optional[str] = config_path


_config: Optional[Config] = None


def get_config() -> Config:
    """获取全局配置单例"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reload_config() -> Config:
    """强制重新加载配置（清除缓存）"""
    global _config
    _config = Config()
    return _config
