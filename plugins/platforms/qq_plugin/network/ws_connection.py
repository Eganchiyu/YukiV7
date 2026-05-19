# ws_connection.py
import json
import asyncio
import websockets
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from typing import Optional, Dict
from yuki_core.config import cfg
from asyncio import Future
import logging

def get_logger(name):
    return logging.getLogger(name)

logger = get_logger("ws_connection")

class BotConnector:
    def __init__(self, ws_url: str = cfg.NAPCAT_WS_URL, ws_token: str = None):
        self.ws_url = ws_url
        self.ws_token = ws_token if ws_token is not None else cfg.NAPCAT_WS_TOKEN
        self.websocket = None
        self._lock = asyncio.Lock()
        # 新增：用于存放等待响应的 Future 对象
        self._response_futures: Dict[str, Future] = {}

    def _get_connection_url(self) -> str:
        """如有 token，将其作为 access_token 拼接到 URL"""
        if not self.ws_token:
            return self.ws_url
        parsed = urlparse(self.ws_url)
        query_params = parse_qs(parsed.query)
        if "access_token" not in query_params:
            query_params["access_token"] = [self.ws_token]
        new_query = urlencode(query_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    async def ensure_connection(self):
        """确保返回一个真正 OPEN 的连接（兼容 websockets 12.x ~ 16.x）"""
        async with self._lock:
            is_alive = False
            if self.websocket is not None:
                try:
                    # websockets 16.x: ClientConnection 有 .open / .closed 属性
                    # websockets 12-14: 同样有 .open / .closed
                    # 最安全的跨版本检测：优先 .open，回退 .closed
                    if hasattr(self.websocket, 'open'):
                        is_alive = self.websocket.open
                    elif hasattr(self.websocket, 'closed'):
                        is_alive = not self.websocket.closed
                except Exception:
                    is_alive = False

            if not is_alive:
                if self.websocket is not None:
                    logger.warning("[Network] 检测到连接状态异常，正在重建...")

                connect_url = self._get_connection_url()
                self.websocket = await websockets.connect(
                    connect_url,
                    ping_interval=None,  # 禁用自动ping，NapCat本地连接不需要
                    close_timeout=10
                )
                logger.info(f"[Network] 全局连接已建立: {self.ws_url}")

            return self.websocket

    async def listen(self):
        """闭环监听：统一接收并分发消息"""
        while True:
            try:
                ws = await self.ensure_connection()
                async for message in ws:
                    data = json.loads(message)

                    # 关键逻辑：检查是否有正在等待这个 echo 的请求
                    echo = data.get("echo")
                    if echo and echo in self._response_futures:
                        future = self._response_futures.pop(echo)
                        if not future.done():
                            future.set_result(data)

                    # 正常的事件流抛出
                    yield data
            except Exception as e:
                logger.error(f"[Network] 监听异常: {e}")
                self.websocket = None
                await asyncio.sleep(3)

    async def close(self):
        """优雅关闭"""
        async with self._lock:
            if self.websocket:
                await self.websocket.close()
                self.websocket = None

    async def send_request(self, action: str, params: dict, echo: str) -> Optional[Dict]:
        try:
            ws = await self.ensure_connection()

            # 1. 注册 Future
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._response_futures[echo] = future

            request = {"action": action, "params": params, "echo": echo}
            await ws.send(json.dumps(request))

            try:
                # 2. 等待结果 (这里才需要 await)
                return await asyncio.wait_for(future, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"请求 {action} 超时 (echo: {echo})")
                return None
            finally:
                # 3. 无论成功还是超时，都要清理字典
                # pop 是同步操作，不需要 await
                self._response_futures.pop(echo, None)

        except Exception as e:
            logger.error(f"网络异常: {e}")
            self._response_futures.pop(echo, None)
            return None

