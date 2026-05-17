# yuki_core/memory.py
"""
Yuki 记忆系统

负责存储、检索、总结 Yuki 的经历
从 modules/memory/rag.py + core/history_manager.py 提取并重构
"""

import datetime
import json
import os
import uuid
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

try:
    import chromadb
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        import jieba.analyse
    from sentence_transformers import SentenceTransformer
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .models import MemoryEntry

try:
    from utils.logger import get_logger
except ImportError:
    import logging
    def get_logger(name):
        return logging.getLogger(name)

logger = get_logger("memory")


class YukiMemory:
    """
    Yuki 记忆系统
    
    平台无关的记忆管理，支持：
    - 向量检索（语义相似度）
    - 关键词补偿（形似匹配）
    - 日记自动总结
    - 24小时去重
    """
    
    def __init__(
        self,
        vector_db_path: str = "./yuki_memory",
        embed_model_path: str = "./models/text2vec-base-chinese",
        history_file: str = "./data/chat_history.json",
        blacklist_file: str = "./blacklist.txt"
    ):
        self.vector_db_path = vector_db_path
        self.embed_model_path = embed_model_path
        self.history_file = history_file
        self.blacklist_file = blacklist_file
        
        # 延迟初始化（避免导入时报错）
        self._model = None
        self._client = None
        self._collection = None
        self._blacklist: list[str] = []
        self._initialized = False
        
        # 对话历史
        self._history: dict[str, list[dict]] = {}
    
    def _ensure_initialized(self):
        """确保已初始化"""
        if self._initialized:
            return
        
        if not HAS_DEPS:
            logger.warning("[Memory] chromadb/sentence-transformers 未安装，记忆功能降级")
            self._initialized = True
            return
        
        try:
            logger.info("[Memory] 初始化记忆库...")
            
            # 确保目录存在
            os.makedirs(self.vector_db_path, exist_ok=True)
            os.makedirs(os.path.dirname(self.history_file) or ".", exist_ok=True)
            
            # 初始化嵌入模型
            self._model = SentenceTransformer(self.embed_model_path)
            
            # 初始化 ChromaDB
            self._client = chromadb.PersistentClient(path=self.vector_db_path)
            self._collection = self._client.get_or_create_collection(
                name="diaries",
                metadata={"hnsw:space": "cosine"}
            )
            
            # 加载屏蔽词
            self._blacklist = self._load_blacklist()
            
            logger.info(f"[Memory] 记忆库初始化完成，已有 {self._collection.count()} 条记忆")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"[Memory] 记忆库初始化失败: {e}")
            self._initialized = True  # 标记为已初始化，避免重试
    
    def _load_blacklist(self) -> list[str]:
        """加载屏蔽词"""
        default_list = ["yuki", "主人", "哥哥", "人家"]
        
        if not os.path.exists(self.blacklist_file):
            with open(self.blacklist_file, "w", encoding="utf-8") as f:
                f.write("\n".join(default_list))
            return default_list
        
        with open(self.blacklist_file, "r", encoding="utf-8") as f:
            words = [line.strip().lower() for line in f
                     if line.strip() and not line.startswith("#")]
        return list(set(words))
    
    def reload_blacklist(self):
        """热重载屏蔽词"""
        self._blacklist = self._load_blacklist()
        logger.info("[Memory] 屏蔽词库已重载")
    
    # ================= 记忆存储 =================
    
    def remember(
        self,
        content: str,
        session_id: str = None,
        source: str = "",
        metadata: dict = None
    ) -> bool:
        """
        保存一条记忆
        
        Args:
            content: 记忆内容
            session_id: 会话ID（可选）
            source: 来源平台（可选）
            metadata: 附加元数据（可选）
            
        Returns:
            bool: 是否保存成功
        """
        self._ensure_initialized()
        
        if not self._collection:
            return False
        
        # 24小时去重检查
        if self._is_duplicate(content, session_id):
            logger.debug(f"[Memory] 检测到重复内容，跳过: {content[:30]}...")
            return False
        
        try:
            # 生成嵌入向量
            embedding = self._model.encode(content).tolist()
            
            # 生成唯一ID
            doc_id = f"diary_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
            
            # 构建元数据
            meta = {
                "timestamp": datetime.datetime.now().timestamp(),
                "source": source,
            }
            if session_id:
                meta["session_id"] = str(session_id)
            if metadata:
                meta.update(metadata)
            
            # 保存到向量库
            self._collection.add(
                documents=[content],
                embeddings=[embedding],
                metadatas=[meta],
                ids=[doc_id]
            )
            
            logger.info(f"[Memory] 记忆已保存 (session={session_id}): {content[:50]}...")
            return True
            
        except Exception as e:
            logger.error(f"[Memory] 保存记忆失败: {e}")
            return False
    
    def _is_duplicate(self, content: str, session_id: str = None) -> bool:
        """检查是否重复"""
        if not self._collection:
            return False
        
        try:
            time_threshold = datetime.datetime.now().timestamp() - 86400
            where_filter = {"timestamp": {"$gte": time_threshold}}
            
            if session_id:
                where_filter["session_id"] = str(session_id)
            
            existing = self._collection.get(where=where_filter)
            if existing and existing.get('documents'):
                return content in existing['documents']
        except Exception:
            pass
        
        return False
    
    # ================= 记忆检索 =================
    
    def recall(
        self,
        context: str,
        session_id: str = None,
        limit: int = 10
    ) -> list[MemoryEntry]:
        """
        检索相关记忆
        
        使用并行双池检索：语义向量 + 关键词补偿
        
        Args:
            context: 检索上下文（当前对话内容）
            session_id: 会话ID（可选，用于过滤）
            limit: 返回数量上限
            
        Returns:
            list[MemoryEntry]: 相关记忆列表
        """
        self._ensure_initialized()
        
        if not self._collection or not context.strip():
            return []
        
        total_count = self._collection.count()
        if total_count == 0:
            return []
        
        # 构建过滤条件
        where_filter = None
        if session_id:
            where_filter = {"session_id": {"$in": [str(session_id), "global"]}}
        
        # 1. 语义向量检索
        semantic_results = self._semantic_search(context, limit * 2, where_filter)
        
        # 2. 关键词检索
        keyword_results = self._keyword_search(context, where_filter)
        
        # 3. 合并与去重
        combined = self._merge_results(semantic_results, keyword_results)
        
        # 4. 截断并返回
        results = combined[:limit]
        
        logger.debug(f"[Memory] 检索完成: 查询='{context[:30]}...', 返回 {len(results)} 条")
        return results
    
    def _semantic_search(
        self,
        query: str,
        limit: int,
        where_filter: dict = None
    ) -> list[tuple[str, float, dict]]:
        """语义向量检索"""
        try:
            query_embedding = self._model.encode(query).tolist()
            
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=min(limit, self._collection.count()),
                where=where_filter,
                include=["documents", "distances", "metadatas"]
            )
            
            output = []
            if results['documents'] and results['documents'][0]:
                for doc, dist, meta in zip(
                    results['documents'][0],
                    results['distances'][0],
                    results['metadatas'][0]
                ):
                    score = 1.0 - dist  # 转换为相似度分数
                    output.append((doc, score, meta))
            
            return output
            
        except Exception as e:
            logger.warning(f"[Memory] 语义检索失败: {e}")
            return []
    
    def _keyword_search(
        self,
        query: str,
        where_filter: dict = None
    ) -> list[tuple[str, float, dict]]:
        """关键词检索"""
        try:
            # 提取关键词
            raw_keywords = jieba.analyse.extract_tags(query, topK=8, withWeight=True)
            keywords = [(kw, w) for kw, w in raw_keywords 
                       if kw.lower() not in self._blacklist]
            
            if not keywords:
                return []
            
            # 全量扫描匹配
            all_docs = self._collection.get(
                where=where_filter,
                include=["documents", "metadatas"]
            )
            
            output = []
            if all_docs['documents']:
                for doc, meta in zip(all_docs['documents'], all_docs['metadatas']):
                    # 计算关键词匹配分数
                    matched = [kw for kw, _ in keywords if kw in doc]
                    if matched:
                        score = len(matched) / len(keywords) * 0.8  # 最高0.8分
                        output.append((doc, score, meta))
            
            return output
            
        except Exception as e:
            logger.warning(f"[Memory] 关键词检索失败: {e}")
            return []
    
    def _merge_results(
        self,
        semantic: list[tuple[str, float, dict]],
        keyword: list[tuple[str, float, dict]]
    ) -> list[MemoryEntry]:
        """合并两个检索池的结果"""
        # 使用字典去重，保留较高分数
        doc_map: dict[str, tuple[float, dict]] = {}
        
        for doc, score, meta in semantic:
            if doc not in doc_map or score > doc_map[doc][0]:
                doc_map[doc] = (score, meta)
        
        for doc, score, meta in keyword:
            if doc not in doc_map or score > doc_map[doc][0]:
                # 关键词结果加权
                existing_score = doc_map.get(doc, (0, {}))[0]
                doc_map[doc] = (max(score, existing_score), meta)
        
        # 按分数排序
        sorted_items = sorted(doc_map.items(), key=lambda x: x[1][0], reverse=True)
        
        # 转换为 MemoryEntry
        results = []
        for doc, (score, meta) in sorted_items:
            results.append(MemoryEntry(
                id=meta.get("id", ""),
                content=doc,
                source=meta.get("source", ""),
                session_id=meta.get("session_id", ""),
                timestamp=meta.get("timestamp", 0),
                metadata={"score": score, **meta}
            ))
        
        return results
    
    # ================= 对话历史管理 =================
    
    def load_history(self) -> dict:
        """加载对话历史"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"[Memory] 加载历史失败: {e}")
        return {}
    
    def save_history(self, history: dict):
        """保存对话历史"""
        try:
            os.makedirs(os.path.dirname(self.history_file) or ".", exist_ok=True)
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Memory] 保存历史失败: {e}")
    
    def append_message(self, session_id: str, role: str, content: str):
        """追加一条消息到历史"""
        history = self.load_history()
        
        if session_id not in history:
            history[session_id] = []
        
        history[session_id].append({
            "role": role,
            "content": content,
            "time": datetime.datetime.now().strftime("%Y年%m月%d日%H:%M")
        })
        
        self.save_history(history)
    
    def get_recent_messages(self, session_id: str, limit: int = 10) -> list[dict]:
        """获取最近的消息"""
        history = self.load_history()
        messages = history.get(session_id, [])
        
        # 过滤掉 system 消息，取最后 N 条
        non_system = [m for m in messages if m.get("role") != "system"]
        return non_system[-limit:]
    
    def clear_session(self, session_id: str):
        """清空某个会话的历史"""
        history = self.load_history()
        if session_id in history:
            del history[session_id]
            self.save_history(history)
