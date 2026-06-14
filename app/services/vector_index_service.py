"""向量索引服务模块"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from app.services.document_splitter_service import document_splitter_service
from app.services.file_handlers.registry import get_handler_for_file
from app.services.vector_store_manager import vector_store_manager
from app.core.milvus_client import milvus_manager


class IndexingResult:
    """索引结果类"""

    def __init__(self):
        self.success = False
        self.directory_path = ""
        self.total_files = 0
        self.success_count = 0
        self.fail_count = 0
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.error_message = ""
        self.failed_files: Dict[str, str] = {}

    def increment_success_count(self):
        """增加成功计数"""
        self.success_count += 1

    def increment_fail_count(self):
        """增加失败计数"""
        self.fail_count += 1

    def add_failed_file(self, file_path: str, error: str):
        """添加失败文件"""
        self.failed_files[file_path] = error

    def get_duration_ms(self) -> int:
        """获取耗时（毫秒）"""
        if self.start_time and self.end_time:
            return int((self.end_time - self.start_time).total_seconds() * 1000)
        return 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "success": self.success,
            "directory_path": self.directory_path,
            "total_files": self.total_files,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "duration_ms": self.get_duration_ms(),
            "error_message": self.error_message,
            "failed_files": self.failed_files,
        }


class VectorIndexService:
    """向量索引服务 - 负责读取文件、生成向量、存储到 Milvus"""

    def __init__(self):
        """初始化向量索引服务"""
        self.upload_path = "./uploads"
        logger.info("向量索引服务初始化完成")

    @property
    def _allowed_extensions(self) -> list:
        """从 handler 注册中心获取所有支持的文件扩展名"""
        from app.services.file_handlers import FileHandlerRegistry
        return FileHandlerRegistry().get_allowed_extensions()

    def index_directory(self, directory_path: Optional[str] = None) -> IndexingResult:
        """
        索引指定目录下的所有文件

        Args:
            directory_path: 目录路径（可选，默认使用配置的上传目录）

        Returns:
            IndexingResult: 索引结果
        """
        result = IndexingResult()
        result.start_time = datetime.now()

        try:
            # 使用指定目录或默认上传目录
            target_path = directory_path if directory_path else self.upload_path
            dir_path = Path(target_path).resolve()

            if not dir_path.exists() or not dir_path.is_dir():
                raise ValueError(f"目录不存在或不是有效目录: {target_path}")

            result.directory_path = str(dir_path)

            # 获取所有支持的文件
            files = []
            for ext in self._allowed_extensions:
                files.extend(dir_path.glob(f"*.{ext}"))

            if not files:
                logger.warning(f"目录中没有找到支持的文件: {target_path}")
                result.total_files = 0
                result.success = True
                result.end_time = datetime.now()
                return result

            result.total_files = len(files)
            logger.info(f"开始索引目录: {target_path}, 找到 {len(files)} 个文件")

            # 遍历并索引每个文件
            for file_path in files:
                try:
                    self.index_single_file(str(file_path))
                    result.increment_success_count()
                    logger.info(f"✓ 文件索引成功: {file_path.name}")
                except Exception as e:
                    result.increment_fail_count()
                    result.add_failed_file(str(file_path), str(e))
                    logger.error(f"✗ 文件索引失败: {file_path.name}, 错误: {e}")

            result.success = result.fail_count == 0
            result.end_time = datetime.now()

            logger.info(
                f"目录索引完成: 总数={result.total_files}, "
                f"成功={result.success_count}, 失败={result.fail_count}"
            )

            return result

        except Exception as e:
            logger.error(f"索引目录失败: {e}")
            result.success = False
            result.error_message = str(e)
            result.end_time = datetime.now()
            return result

    def index_single_file(self, file_path: str):
        """
        索引单个文件，支持增量更新：对每个分片计算 MD5 hash，
        与 Milvus 中已有分片比对，只对变更分片重新嵌入，未变更分片复用旧向量。

        Args:
            file_path: 文件路径

        Raises:
            ValueError: 文件不存在时抛出
            RuntimeError: 索引失败时抛出
        """
        path = Path(file_path).resolve()

        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {file_path}")

        logger.info(f"开始索引文件: {path}")

        try:
            # 1. 提取文本
            handler = get_handler_for_file(str(path))
            content = handler.extract_text(str(path))
            normalized_path = path.as_posix()
            logger.info(
                f"读取文件: {path}, 处理器: {handler.__class__.__name__}, "
                f"内容长度: {len(content)} 字符"
            )

            # 2. 分片
            documents = document_splitter_service.split_document(content, normalized_path)
            logger.info(f"文档分割完成: {file_path} -> {len(documents)} 个分片")

            if not documents:
                logger.warning(f"文件内容为空或无法分割: {file_path}")
                if normalized_path:
                    _ = vector_store_manager.delete_by_source(normalized_path)
                return

            # 3. 为每个分片添加 hash 和序号
            for i, doc in enumerate(documents):
                doc.metadata["_chunk_hash"] = hashlib.md5(
                    doc.page_content.encode("utf-8")
                ).hexdigest()
                doc.metadata["_chunk_index"] = i

            # 4. 查询旧分片，比对 hash
            old_chunks = vector_store_manager.get_chunks_by_source(normalized_path)
            old_hash_to_id: dict[str, str] = {
                c["chunk_hash"]: c["id"]
                for c in old_chunks
                if c["chunk_hash"]
            }

            # 旧分片存在但缺少 _chunk_hash 元数据 → 无法增量比对，回退全量替换
            if old_chunks and not old_hash_to_id:
                logger.info("旧分片无 hash 元数据（历史数据），回退到全量替换")
                vector_store_manager.delete_by_source(normalized_path)
                vector_store_manager.add_documents(documents)
                logger.info(f"全量索引完成: {file_path}, 共 {len(documents)} 个分片")
                return

            new_hashes = {doc.metadata["_chunk_hash"] for doc in documents}
            old_hashes = set(old_hash_to_id.keys())

            unchanged_hashes = new_hashes & old_hashes
            stale_hashes = old_hashes - new_hashes
            changed_docs = [
                doc for doc in documents
                if doc.metadata["_chunk_hash"] not in unchanged_hashes
            ]
            stale_ids = [old_hash_to_id[h] for h in stale_hashes]

            logger.info(
                f"增量分析: 总分片={len(documents)}, 未变更={len(unchanged_hashes)}, "
                f"新增/变更={len(changed_docs)}, 待删除={len(stale_ids)}"
            )

            # 5. 写入变更分片（嵌入稠密+稀疏向量）
            if changed_docs:
                vector_store_manager.add_documents(changed_docs)
                logger.info(f"变更分片已写入: {len(changed_docs)} 个")

            # 6. 删除过时分片
            if stale_ids:
                vector_store_manager.delete_by_ids(stale_ids)

            saved_calls = len(documents) - len(changed_docs)
            if saved_calls > 0:
                logger.info(
                    f"增量索引完成: {file_path}, 复用 {saved_calls}/{len(documents)} "
                    f"个未变更分片，节省 {saved_calls} 次嵌入调用"
                )
            else:
                logger.info(f"文件索引完成: {file_path}, 共 {len(documents)} 个分片")

        except Exception as e:
            logger.error(f"增量索引失败，尝试全量回退: {file_path}, 错误: {e}")
            try:
                # 回退：全量删除 + 全量重新索引
                path_resolved = Path(file_path).resolve()
                normalized = path_resolved.as_posix()
                vector_store_manager.delete_by_source(normalized)

                handler = get_handler_for_file(str(path_resolved))
                content = handler.extract_text(str(path_resolved))
                documents = document_splitter_service.split_document(content, normalized)
                if documents:
                    vector_store_manager.add_documents(documents)
                logger.info(f"全量回退索引完成: {file_path}")
            except Exception as fallback_error:
                logger.error(f"全量回退索引也失败: {file_path}, 错误: {fallback_error}")
                raise RuntimeError(f"索引文件失败: {fallback_error}") from fallback_error

    def reindex_sparse_vectors(self) -> int:
        """为 Milvus 中所有现有文档重新计算并写入 BM25 稀疏向量。

        Schema 迁移后或 BM25 模型重新拟合后调用。分批处理避免
        内存溢出。

        Returns:
            int: 成功更新的文档数
        """
        from app.services.bm25_embedding_service import bm25_embedding_service

        if not bm25_embedding_service.is_fitted:
            raise RuntimeError("BM25 模型未拟合，无法重索引稀疏向量")

        collection = milvus_manager.get_collection()
        count = 0
        batch_size = 500
        offset = 0

        logger.info("开始重索引稀疏向量...")
        while True:
            results = collection.query(
                expr="",
                output_fields=["id", "content"],
                limit=batch_size,
                offset=offset,
            )
            if not results:
                break

            texts = [r["content"] for r in results]
            sparse_vecs = bm25_embedding_service.encode_documents(texts)
            entities = [
                {"id": r["id"], "sparse_vector": sv}
                for r, sv in zip(results, sparse_vecs)
            ]
            collection.upsert(entities)
            count += len(entities)
            offset += batch_size
            logger.debug(f"重索引进度: {count} 条")

        logger.info(f"重索引稀疏向量完成，共 {count} 条")
        return count


# 全局单例
vector_index_service = VectorIndexService()
