# plugins/platforms/qq_plugin/napcat_ws.py
"""
NapCat WebSocket 连接管理

从 YukiV6 network/ws_connection.py 重构，移除 config 依赖
"""

import json
import asyncio
import logging
from typing import Optional, Dict, AsyncIterator
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
from asyncio import Future

logger = logging.getLogger("napcat_ws")


class NapCatWS:
    """
    NapCat WebSocket 连接管理器
    
    职责：
    - 管理与 NapCat 的 WebSocket 连接
    - 自动重连
    - 请求-响应模式 (echo 机制)
    - 事件流监听
    """
    
    def __init__(self, ws_url: str, ws_token: str = ""):
        self.ws_url = ws_url
        self.ws_token = ws_token
        self.websocket = None
        self._lock = asyncio.Lock()
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
    
    async def connect(self) -> bool:
        """建立连接，返回是否成功"""
        try:
            await self.ensure_connection()
            return True
        except Exception as e:
            logger.error(f"[NapCatWS] 连接失败: {e}")
            return False
    
    async def ensure_connection(self):
        """确保返回一个真正 OPEN 的连接"""
        import websockets
        
        async with self._lock:
            is_alive = False
            if self.websocket is not None:
                try:
                    from websockets.protocol import State
                    is_alive = self.websocket.state == State.OPEN
                except Exception:
                    try:
                        is_alive = not self.websocket.closed
                    except AttributeError:
                        try:
                            is_alive = self.websocket.open
                        except AttributeError:
                            is_alive = False
            
            if not is_alive:
                if self.websocket is not None:
                    logger.warning("[NapCatWS] 检测到连接异常，正在重建...")
                
                connect_url = self._get_connection_url()
                self.websocket = await websockets.connect(
                    connect_url,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=10,
                )
                logger.info(f"[NapCatWS] 连接已建立: {self.ws_url}")
            
            return self.websocket
    
    async def listen(self) -> AsyncIterator[dict]:
        """
        闭环监听：统一接收并分发消息
        
        Yields:
            dict: NapCat 原始事件数据
        """
        while True:
            try:
                ws = await self.ensure_connection()
                async for message in ws:
                    data = json.loads(message)
                    
                    # 检查是否有等待此 echo 的请求
                    echo = data.get("echo")
                    if echo and echo in self._response_futures:
                        future = self._response_futures.pop(echo)
                        if not future.done():
                            future.set_result(data)
                    
                    yield data
                    
            except asyncio.CancelledError:
                logger.info("[NapCatWS] 监听已取消")
                break
            except Exception as e:
                logger.error(f"[NapCatWS] 监听异常: {e}")
                self.websocket = None
                await asyncio.sleep(3)
    
    async def send_raw(self, data: dict):
        """发送原始 JSON 数据"""
        ws = await self.ensure_connection()
        await ws.send(json.dumps(data))
    
    async def send_request(self, action: str, params: dict, echo: str) -> Optional[dict]:
        """
        发送请求并等待响应（echo 机制）
        
        Args:
            action: NapCat API 动作名
            params: 参数
            echo: 唯一标识，用于匹配响应
            
        Returns:
            dict: 响应数据，超时返回 None
        """
        try:
            ws = await self.ensure_connection()
            
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._response_futures[echo] = future
            
            request = {"action": action, "params": params, "echo": echo}
            await ws.send(json.dumps(request))
            
            try:
                return await asyncio.wait_for(future, timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"[NapCatWS] 请求 {action} 超时 (echo: {echo})")
                return None
            finally:
                self._response_futures.pop(echo, None)
                
        except Exception as e:
            logger.error(f"[NapCatWS] 请求异常: {e}")
            self._response_futures.pop(echo, None)
            return None
    
    async def disconnect(self):
        """优雅关闭"""
        async with self._lock:
            if self.websocket:
                await self.websocket.close()
                self.websocket = None
                logger.info("[NapCatWS] 连接已关闭")
