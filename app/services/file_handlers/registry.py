"""文件处理器注册中心 - 管理所有文件类型处理器并提供自动分发"""

from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from app.services.file_handlers.base import BaseFileHandler


class FileHandlerRegistry:
    """文件处理器注册中心（单例模式）

    负责：
    1. 注册所有文件类型处理器
    2. 根据文件扩展名自动匹配对应的处理器
    3. 提供所有支持的文件扩展名列表
    """

    _instance: Optional["FileHandlerRegistry"] = None

    def __new__(cls) -> "FileHandlerRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handlers: Dict[str, BaseFileHandler] = {}
            cls._instance._initialized = False
        return cls._instance

    def register(self, handler: BaseFileHandler) -> None:
        """注册一个文件处理器"""
        for ext in handler.supported_extensions:
            self._handlers[ext] = handler
            logger.debug(f"注册文件处理器: .{ext} -> {handler.__class__.__name__}")

    def get_handler(self, file_path: str) -> Optional[BaseFileHandler]:
        """根据文件路径获取对应的处理器"""
        ext = Path(file_path).suffix.lower().lstrip(".")
        return self._handlers.get(ext)

    def get_allowed_extensions(self) -> List[str]:
        """获取所有支持的文件扩展名"""
        return sorted(self._handlers.keys())

    @property
    def handlers(self) -> Dict[str, BaseFileHandler]:
        """获取所有已注册的处理器（以扩展名为 key）"""
        return self._handlers


def get_handler_for_file(file_path: str) -> BaseFileHandler:
    """根据文件路径获取对应的处理器（便捷函数）

    Raises:
        ValueError: 当文件类型不被支持时抛出
    """
    registry = FileHandlerRegistry()
    handler = registry.get_handler(file_path)
    if handler is None:
        ext = Path(file_path).suffix.lower().lstrip(".")
        allowed = registry.get_allowed_extensions()
        raise ValueError(
            f"不支持的文件格式: .{ext}，当前支持: {', '.join(allowed)}"
        )
    return handler
