# plugins/platforms/qq_plugin/__init__.py
"""
QQ Bot 适配器插件

将 YukiV6 的 QQ 业务逻辑封装为 V7 PlatformPlugin。
保留所有 V6 业务（防抖、精力、欲望、表情包、视觉理解、破冰、日记），
适配 V7 架构：LLM → yuki_core.llm, 记忆 → yuki_core.memory, 总线 → ContextBus。
"""

import asyncio
import json
import os
import re
import time
import datetime
import logging
from typing import AsyncIterator, Optional

from yuki_core.plugin import PlatformPlugin
from yuki_core.config import cfg
from yuki_core.models import PlatformEvent, YukiResponse
from yuki_core.llm import robust_chat

# V6 业务模块（已适配 imports）
# 重度依赖（cv2/chromadb/sentence_transformers）延迟到 connect() 时导入
from plugins.platforms.qq_plugin.core.brain import YukiState
from plugins.platforms.qq_plugin.core.engine import YukiEngine, maid_worker
from plugins.platforms.qq_plugin.core.prompts import (
    sync_system_prompts,
    get_yuki_setting_group,
    get_yuki_setting_private,
)
from plugins.platforms.qq_plugin.modules.message.CQProtocol import smart_truncate, CQProtocol
from plugins.platforms.qq_plugin.modules.message.CQParser import CQCodeParser
from plugins.platforms.qq_plugin.network.ws_connection import BotConnector
from plugins.platforms.qq_plugin.network.ws_sender import MessageSender
from plugins.platforms.qq_plugin.llm_call import yuki_chat

logger = logging.getLogger("qq_plugin")


class QQPlugin(PlatformPlugin):
    """QQ Bot 平台插件 — 封装 YukiV6 全部 QQ 业务逻辑"""

    name = "QQ Bot"
    platform_id = "qq"
    version = "0.2.0"

    def __init__(self, **config):
        super().__init__(**config)
        # 从 plugins.yaml 读取配置
        self.napcat_ws_url = config.get("napcat_ws_url", cfg.NAPCAT_WS_URL)
        self.napcat_ws_token = config.get("napcat_ws_token", cfg.NAPCAT_WS_TOKEN)
        self.target_groups = config.get("target_groups", cfg.TARGET_GROUPS)
        self.target_qq = config.get("target_qq", cfg.TARGET_QQ)
        self.debounce_time = config.get("debounce_time", cfg.DEBOUNCE_TIME)
        self.max_message_length = config.get("max_message_length", cfg.MAX_MESSAGE_LENGTH)

        # 由 main.py inject
        self.master_id = 0
        self.master_name = ""
        self.robot_name = ""
        self.keywords = []

        # V6 业务对象（connect 时初始化，类型用字符串注解避免提前导入）
        self.connector = None  # type: Optional[BotConnector]
        self.sender = None  # type: Optional[MessageSender]
        self.parser = None  # type: Optional[CQCodeParser]
        self.yuki = None  # type: Optional[YukiState]
        self.engine = None  # type: Optional[YukiEngine]
        self.meme_processor = None  # MemeProcessor, 延迟导入
        self.sticker_manager = None  # StickerManager, 延迟导入
        self.memory_rag = None  # MemoryRAG, 延迟导入
        self.history_manager = None  # V7 HistoryManager

        # 群聊开关状态
        self.group_active_state: dict = {}
        self._group_state_file = "data/group_state.json"
        self._real_time_debounce = self.debounce_time

        # 事件队列（receive → bus）
        self._event_queue: Optional[asyncio.Queue] = None

    # ================= 生命周期 =================

    async def connect(self) -> bool:
        """初始化 V6 业务对象并连接 NapCat"""
        logger.info("[QQ] 初始化业务对象...")

        # 群聊开关状态
        self.group_active_state = self._load_group_state()

        # V6 核心对象（延迟导入重度依赖）
        self.yuki = YukiState()

        from plugins.platforms.qq_plugin.modules.vision.processor import MemeProcessor
        from plugins.platforms.qq_plugin.modules.stickers.manager import StickerManager
        from plugins.platforms.qq_plugin.modules.memory.rag import MemoryRAG
        self.meme_processor = MemeProcessor()
        self.sticker_manager = StickerManager()
        self.memory_rag = MemoryRAG()

        # V7 HistoryManager
        from yuki_core.history import HistoryManager
        self.history_manager = HistoryManager(history_file=cfg.HISTORY_FILE)

        # 同步系统提示词
        sync_system_prompts(self.history_manager, self.yuki)

        # V6 引擎（用于后台任务：日记、破冰、小女仆）
        self.engine = YukiEngine(
            self.memory_rag, self.history_manager, self.yuki, self.sender
        )
        self.engine.sticker_manager = self.sticker_manager
        self.engine.process_callback = self._main_process_callback

        # WebSocket 连接
        self.connector = BotConnector(self.napcat_ws_url, self.napcat_ws_token)
        self.sender = MessageSender(self.connector)
        self.parser = CQCodeParser(self.connector)

        # 重新绑定 engine 的 sender
        self.engine.sender = self.sender

        # 事件队列
        self._event_queue = asyncio.Queue()

        # 预热群组
        for cid in self.target_groups:
            self.yuki.last_message_time[str(cid)] = time.time()
            self.yuki.update_energy(str(cid))
            self.yuki.update_desire_to_reply(str(cid))

        logger.info("[QQ] 业务对象初始化完成")
        return True

    async def disconnect(self) -> None:
        if self.connector:
            await self.connector.close()

    async def on_connect(self) -> None:
        logger.info("[QQ] NapCat 已连接")

    async def on_disconnect(self) -> None:
        logger.info("[QQ] NapCat 已断开")

    def translate_in(self, raw_data):
        """V6 已在 receive() 内部完成转换，此方法占位"""
        return raw_data

    def translate_out(self, event, response):
        """V6 已在 send() 内部完成转换，此方法占位"""
        return response.text

    # ================= 消息收发 =================

    async def receive(self) -> AsyncIterator[PlatformEvent]:
        """
        监听 NapCat WebSocket，缓冲防抖，预处理后 yield PlatformEvent。

        保留 V6 的防抖、buffer、图片理解、CQ 码解析、开关拦截逻辑。
        不做 decide_to_reply 和 history 加载 — 这些交给 Mind 处理。
        """
        # 启动后台任务
        self._start_background_tasks()

        logger.info("[QQ] 开始监听 NapCat WebSocket...")
        while True:
            try:
                async for data in self.connector.listen():
                    if data.get("post_type") != "message":
                        continue

                    msg_type = data.get("message_type")
                    raw_msg = data.get("raw_message", "")
                    user_id = data.get("user_id")

                    # === 私聊 ===
                    if msg_type == "private" and user_id == self.target_qq:
                        event = await self._process_private_message(user_id, raw_msg)
                        if event:
                            yield event

                    # === 群聊 ===
                    elif msg_type == "group":
                        group_id = data.get("group_id")
                        gid_str = str(group_id)

                        # 目标群白名单
                        if self.target_groups and group_id not in self.target_groups:
                            continue

                        # /关闭 /开启 拦截
                        intercept = await self._handle_group_command(
                            group_id, user_id, raw_msg, data
                        )
                        if intercept:
                            continue

                        # 群开关检查
                        if not self.group_active_state.get(gid_str, True):
                            continue

                        # 构造消息
                        sender_info = data.get("sender", {})
                        name = sender_info.get("card") or sender_info.get("nickname") or "路人"
                        is_fake = name == self.master_name and user_id != self.master_id
                        if is_fake:
                            name = f"{name}(冒充)"

                        content = f'【"{name}"】说: {raw_msg}'
                        is_bot = "BOT" in name or "机器人" in name

                        # RLHF 正反馈捕捉
                        self._check_meme_feedback(gid_str, raw_msg, is_bot)

                        # 破冰计数重置
                        if not is_bot and self.yuki.ice_break_fail_count.get(gid_str, 0) > 0:
                            self.yuki.ice_break_fail_count[gid_str] = 0

                        # 防抖入队
                        event = await self._enqueue_and_debounce(
                            group_id, content, raw_msg, name, user_id, is_bot, "group"
                        )
                        if event:
                            yield event

            except Exception as e:
                logger.error(f"[QQ] 监听异常: {e}")
                await asyncio.sleep(5)

    async def send(self, event: PlatformEvent, response: YukiResponse) -> bool:
        """发送 Yuki 回复到 QQ"""
        chat_id = event.session_id
        mode = event.session_type or "group"
        text = response.text

        if not text:
            return False

        # 拆分文字和表情包（V6 逻辑）
        parts = re.split(r'(\[CQ:image,[^\]]*?sub_type=1\])', text, flags=re.IGNORECASE)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            await self.sender.send(chat_id, part, mode=mode)
            await asyncio.sleep(1.0)

        # 记录日志
        self.history_manager.append_to_log(chat_id, cfg.ROBOT_NAME.title(), text)

        return True

    # ================= 防抖与缓冲 =================

    async def _enqueue_and_debounce(
        self, chat_id, content, raw_msg, sender_name, user_id, is_bot, mode
    ):
        """
        V6 manage_buffer 逻辑：防抖入队，合并消息后触发处理。
        返回处理后的 PlatformEvent，或 None（未到时间/被拦截）。
        """
        cid_str = str(chat_id)
        self.yuki.last_message_time[cid_str] = time.time()

        content = smart_truncate(content, max_len=self.max_message_length)

        # 帮助指令拦截
        if raw_msg in ['help', '/help', 'yuki帮助', 'yuki功能', '帮助', '功能']:
            await self.sender.send_local_image(chat_id, "utils/yuki_help.png", mode=mode)
            self.history_manager.append_to_log(chat_id, "User/Group", content)
            return None

        # 入队
        if chat_id not in self.yuki.message_buffer:
            self.yuki.message_buffer[chat_id] = []
        if not is_bot or user_id in cfg.BOT_WHITELIST:
            self.yuki.message_buffer[chat_id].append({
                "name": sender_name,
                "content": content,
                "raw_text": raw_msg,
                "is_bot": is_bot,
            })

        # 被召唤时缩短防抖
        if self.robot_name.lower() in raw_msg.lower():
            self._real_time_debounce = 5

        # 取消旧任务，创建新任务
        if chat_id in self.yuki.buffer_tasks:
            self.yuki.buffer_tasks[chat_id].cancel()
        self.yuki.buffer_tasks[chat_id] = asyncio.create_task(
            self._main_process(chat_id, mode)
        )
        return None  # 防抖期间不 yield，等 debounce 完成后由 _main_process 放入队列

    async def _main_process(self, chat_id, mode):
        """
        V6 main_process 逻辑：防抖等待 → 预处理 → 计算预过滤值 → yield PlatformEvent。

        不再做 decide_to_reply 和 history 加载 — 这些交给 Mind。
        """
        await asyncio.sleep(self._real_time_debounce)
        self._real_time_debounce = self.debounce_time

        # 群开关检查
        if mode == "group" and not self.group_active_state.get(str(chat_id), True):
            self.yuki.pop_buffer(chat_id)
            return

        message_objs = self.yuki.pop_buffer(chat_id)
        if not message_objs:
            return

        # 活跃度提升
        await self.yuki.boost_activity(chat_id)

        # 图片理解
        all_contents = [m["content"] for m in message_objs]
        combined_text = "\n".join(all_contents)
        modified_text, images_info = self.meme_processor.extract_urls_from_text(combined_text)

        if images_info:
            understood_contents = []
            for img in images_info:
                url = img["url"]
                result = await self.meme_processor.understand_from_url(url)
                understood_contents.append(result)
            combined_text = modified_text
            for content in understood_contents:
                combined_text = combined_text.replace("[图片占位符]", content, 1)

        # CQ 码解析
        combined_text = await self.parser.parse_all_cq_codes(combined_text)
        combined_text = combined_text.replace("\n", "  ").strip()

        logger.info(f"[{chat_id}] 收到消息: {combined_text[:80]}")

        # 记录到日志（不修改 history，history 由 Mind 管理）
        self.history_manager.append_to_log(chat_id, "User/Group", combined_text)

        # === 计算预过滤值（供 Mind 的 decide_to_reply 使用） ===
        cid_str = str(chat_id)
        current_e = self.yuki.update_energy(chat_id)
        self.yuki.update_desire_to_reply(chat_id)
        desire = self.yuki.desire_to_start_topic.get(cid_str, 0)

        force_reply = self.robot_name.lower() in combined_text.lower()

        human_calling = any(
            not m["is_bot"] and any(kw in m["raw_text"].lower() for kw in self.keywords)
            for m in message_objs
        )

        bot_calling_only = False
        if not human_calling:
            bot_calling_only = all(
                m["is_bot"] for m in message_objs
                if any(kw in m["raw_text"].lower() for kw in self.keywords)
            )

        # 构造 PlatformEvent（不含 history，Mind 会自行加载）
        event = PlatformEvent(
            source="qq",
            event_type="message",
            content=combined_text,
            user_id=str(message_objs[-1].get("name", "")),
            user_name=message_objs[-1].get("name", ""),
            session_id=cid_str,
            session_type=mode,
            metadata={
                "raw_message": combined_text,
                "sender_name": message_objs[-1].get("name", ""),
                "is_bot": message_objs[-1].get("is_bot", False),
                "group_id": chat_id if mode == "group" else None,
                # 预过滤值（供 Mind 的 decide_to_reply 使用）
                "pre_filter": {
                    "force_reply": force_reply,
                    "human_calling": human_calling,
                    "bot_calling_only": bot_calling_only,
                    "desire": desire,
                    "energy": current_e,
                    "keywords": self.keywords,
                    "robot_name": self.robot_name,
                },
            },
        )

        await self._event_queue.put(event)

    # ================= 群聊命令 =================

    async def _handle_group_command(self, group_id, user_id, raw_msg, data):
        """处理 /关闭 /开启 命令，返回 True 表示已拦截"""
        msg_clean = raw_msg.strip()
        gid_str = str(group_id)

        if msg_clean == '/关闭':
            if user_id == self.master_id:
                self.group_active_state[gid_str] = False
                self._save_group_state()
                if group_id in self.yuki.message_buffer:
                    self.yuki.message_buffer[group_id] = []
                if group_id in self.yuki.buffer_tasks:
                    self.yuki.buffer_tasks[group_id].cancel()
                await self.sender.send(group_id, f"{cfg.ROBOT_NAME.title()} 已进入休眠模式", mode="group")
            else:
                await self.sender.send(group_id, "只有哥哥大人才能关掉我哦！", mode="group")
            return True

        elif msg_clean == '/开启':
            if user_id == self.master_id:
                self.group_active_state[gid_str] = True
                self._save_group_state()
                await self.sender.send(group_id, f"{cfg.ROBOT_NAME.title()} 重新上线", mode="group")
            else:
                await self.sender.send(group_id, "只有哥哥大人才能唤醒我哦！", mode="group")
            return True

        return False

    # ================= RLHF 正反馈 =================

    def _check_meme_feedback(self, gid_str, raw_msg, is_bot):
        """V6 RLHF：捕捉群友对表情包的正反馈"""
        if gid_str in self.yuki.last_sent_meme and not is_bot:
            feedback_words = ["哈", "草", "233", "笑", "蚌埠", "确实", "典", "好图", "偷了"]
            if any(fw in raw_msg for fw in feedback_words):
                meme_id = self.yuki.last_sent_meme.pop(gid_str)
                self.sticker_manager.add_preference(meme_id)

    # ================= 私聊处理 =================

    async def _process_private_message(self, user_id, raw_msg):
        """处理私聊消息"""
        content = f'【"主人"】说: {raw_msg}'
        content = smart_truncate(content, max_len=self.max_message_length)

        # 入队
        if user_id not in self.yuki.message_buffer:
            self.yuki.message_buffer[user_id] = []
        self.yuki.message_buffer[user_id].append({
            "name": self.master_name,
            "content": content,
            "raw_text": raw_msg,
            "is_bot": False,
        })

        # 取消旧任务
        if user_id in self.yuki.buffer_tasks:
            self.yuki.buffer_tasks[user_id].cancel()
        self.yuki.buffer_tasks[user_id] = asyncio.create_task(
            self._main_process(user_id, "private")
        )
        return None

    # ================= 群开关状态 =================

    def _load_group_state(self):
        if os.path.exists(self._group_state_file):
            try:
                with open(self._group_state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_group_state(self):
        try:
            os.makedirs("data", exist_ok=True)
            with open(self._group_state_file, 'w', encoding='utf-8') as f:
                json.dump(self.group_active_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[QQ] 保存群聊状态失败: {e}")

    # ================= 后台任务 =================

    def get_background_tasks(self):
        """返回后台常驻任务列表，由 Bus 启动"""
        return [
            self._bg_decay_heartbeat(),
            self._bg_idle_diary_checker(),
            self._bg_ice_break_monitor(),
            self._bg_maid_worker(),
        ]

    def _start_background_tasks(self):
        """在 receive() 启动时注册后台任务"""
        # 由 Bus 的 get_background_tasks() 自动启动
        pass

    async def _bg_decay_heartbeat(self):
        """V6 精力衰减"""
        await self.yuki.decay_heartbeat()

    async def _bg_idle_diary_checker(self):
        """V6 空闲日记检查"""
        await self.engine.idle_diary_checker()

    async def _bg_ice_break_monitor(self):
        """V6 破冰监控"""
        await self.engine.ice_break_monitor()

    async def _bg_maid_worker(self):
        """V6 小女仆 Worker"""
        await maid_worker(self.engine, self.yuki, self.sender, self.history_manager)

    # ================= 辅助 =================

    async def _main_process_callback(self, chat_id, mode, **kwargs):
        """engine.process_callback 的兼容接口"""
        await self._main_process(chat_id, mode)
