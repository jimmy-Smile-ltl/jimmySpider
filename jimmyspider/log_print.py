"""
jimmySpider - 日志系统

开箱即用的日志类，支持控制台输出 + 按天轮转文件日志。
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Union

from jimmyspider.config import get_config


class LogPrint:
    """融合即用性与灵活性的日志类。

    - 开箱即用：默认配置控制台和文件日志
    - 配置简单：通过参数控制默认行为
    - 便捷调用：实例可直接调用 .print(), .info(), .warning(), .error() 等方法
    """

    def __init__(
        self,
        name: Union[str, Path] = "logger",
        log_dir: Union[str, Path] = "logs",
        console_level: int = logging.INFO,
        file_level: int = logging.DEBUG,
        save_to_file: bool = True,
        backup_count: int = 5,
    ):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        self.formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 控制台 Handler
        self.add_console_handler(console_level)

        # 检查是否配置了自定义日志目录
        config = get_config()
        if config.LOG_DIR:
            log_dir = config.LOG_DIR

        # 文件 Handler
        if save_to_file:
            log_file_name = f"{name.lower()}.log"
            log_path = os.path.join(str(log_dir), log_file_name)
            self.add_timed_rotating_file_handler(
                file_name=log_path,
                level=file_level,
                backup_count=backup_count,
            )

        self.info(
            f"日志系统初始化完成，日志目录: {log_dir}, "
            f"控制台级别: {console_level}, 文件级别: {file_level}, 备份数量: {backup_count}"
        )

    def add_console_handler(self, level: int = logging.INFO):
        """添加控制台 Handler（支持链式调用）"""
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(self.formatter)
        self.logger.addHandler(console_handler)
        return self

    def add_timed_rotating_file_handler(
        self,
        file_name: str,
        level: int = logging.DEBUG,
        backup_count: int = 7,
        when: str = "D",
        interval: int = 1,
    ):
        """添加按时间轮转的文件 Handler（支持链式调用）"""
        log_dir = os.path.dirname(file_name)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=file_name,
            when=when,
            interval=interval,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)
        return self

    def add_rotating_file_handler(
        self, name, log_dir, max_file_size, backup_count, max_total_size, level
    ):
        """添加基于文件大小的轮转 Handler（支持链式调用）"""
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, f"{name.lower()}.log")
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)

        self._cleanup_old_logs(log_dir, name, max_total_size)
        return self

    def _cleanup_old_logs(self, log_dir, name, max_total_size):
        """清理超出总大小限制的旧日志文件"""
        import glob

        pattern = os.path.join(log_dir, f"{name.lower()}.log*")
        log_files = glob.glob(pattern)
        log_files.sort(key=os.path.getmtime, reverse=True)

        total_size = 0
        files_to_keep = []

        for log_file in log_files:
            file_size = os.path.getsize(log_file)
            if total_size + file_size <= max_total_size:
                total_size += file_size
                files_to_keep.append(log_file)
            else:
                try:
                    os.remove(log_file)
                    print(f"删除旧日志文件: {log_file}")
                except OSError as e:
                    print(f"删除文件失败 {log_file}: {e}")

        print(
            f"保留日志文件: {len(files_to_keep)} 个, "
            f"总大小: {total_size / 1024 / 1024:.2f}MB"
        )

    # --- 便捷调用方法 ---
    def log(self, message: str, *args, **kwargs):
        self.logger.info(message, *args, **kwargs)

    def debug(self, message: str, *args, **kwargs):
        self.logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs):
        self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args, **kwargs):
        self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args, **kwargs):
        self.logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args, **kwargs):
        self.logger.critical(message, *args, **kwargs)

    def print(self, message: str, level: int = logging.INFO, *args, **kwargs):
        """打印并保存日志，可动态指定级别"""
        self.logger.log(level, message, *args, **kwargs)
