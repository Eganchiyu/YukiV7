# Bus 硬化计划

> **目标：** 在不改变业务逻辑的前提下，强化 ContextBus 的鲁棒性、规范化接口、提高可观测性。

---

## 现状问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | `_listen_platform` 无逐事件错误隔离 | 一条坏消息杀死整个平台监听 |
| 2 | 连接断开后无自动重连 | WebSocket 断了 bot 就死了 |
| 3 | `_decide_to_reply` 依赖 plugin 设置 `is_bot` | plugin 没设就失效 |
| 4 | `receive()` 无事件校验 | 缺字段直接 KeyError |
| 5 | `_make_llm_caller()` 每次调用创建新闭包 | 微小浪费 |
| 6 | action 执行内联在 `receive()` | 不可扩展 |
| 7 | 后台任务无错误处理 | 静默挂掉 |
| 8 | `stop()` 不等待优雅关闭 | 丢失进行中的事件 |
| 9 | 无事件流日志 | 难调试 |

---

## 改动清单

### 改动 1: `_listen_platform` 错误隔离 + 自动重连

**文件:** `yuki_core/bus.py` 方法 `_listen_platform`

**现状:**
```python
async def _listen_platform(self, plugin):
    try:
        connected = await plugin.connect()
        if not connected:
            return
        async for event in plugin.receive():
            response = await self.receive(event)
            if response and response.text:
                await plugin.send(event, response)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(...)
```

**改为:**
```python
async def _listen_platform(self, plugin):
    retry_count = 0
    max_retries = getattr(plugin, 'max_reconnect_retries', 5)

    while self._running:
        try:
            connected = await plugin.connect()
            if not connected:
                raise ConnectionError(f"{plugin.name} connect() returned False")

            await plugin.on_connect()
            retry_count = 0  # 连接成功，重置计数

            async for event in plugin.receive():
                if not self._running:
                    break
                # 逐事件 try/except，一条坏了不影响下一条
                try:
                    response = await self.receive(event)
                    if response and response.text:
                        await plugin.send(event, response)
                except Exception as e:
                    logger.error(f"[Bus] {plugin.name} 事件处理异常: {e}", exc_info=True)

        except asyncio.CancelledError:
            break
        except Exception as e:
            retry_count += 1
            if retry_count > max_retries:
                logger.error(f"[Bus] {plugin.name} 超过最大重连次数({max_retries})，放弃")
                break
            wait = min(2 ** retry_count, 60)
            logger.warning(f"[Bus] {plugin.name} 连接断开({e})，{wait}s 后重连 (#{retry_count})")
            await asyncio.sleep(wait)

    # 清理
    try:
        await plugin.on_disconnect()
        await plugin.disconnect()
    except Exception:
        pass
```

**要点:**
- 外层循环：连接→监听→断开→重连（指数退避，上限60s）
- 内层 try/except：单条事件异常不影响整个监听
- `on_connect()`/`on_disconnect()` 回调被调用
- 超过 max_retries 才放弃

---

### 改动 2: 事件校验

**文件:** `yuki_core/bus.py` 新增方法 `_validate_event`

```python
def _validate_event(self, event: PlatformEvent) -> bool:
    """校验事件必填字段"""
    if not event.source:
        logger.warning("[Bus] 事件缺少 source，跳过")
        return False
    if not event.user_id:
        logger.warning("[Bus] 事件缺少 user_id，跳过")
        return False
    if not event.session_id:
        # 自动填充：私聊=user_id, 群聊从 metadata 取
        event.session_id = event.metadata.get("group_id") or event.user_id
    return True
```

在 `receive()` 开头调用：
```python
async def receive(self, event: PlatformEvent) -> Optional[YukiResponse]:
    if not self._validate_event(event):
        return None
    # ... 原有逻辑
```

---

### 改动 3: 缓存 LLM caller

**文件:** `yuki_core/bus.py`

**现状:** `_make_llm_caller()` 每次 `receive()` 调用都创建新闭包。

**改为:** 在 `start()` 时创建一次，缓存到 `self._llm_caller`。

```python
async def start(self):
    # ... 原有逻辑 ...
    self._llm_caller = self._make_llm_caller()  # 启动时创建一次
    # ... 继续 ...
```

`receive()` 中直接用 `self._llm_caller`。

如果配置热更新需要重建，在 config 变化时重新创建即可（当前不需要）。

---

### 改动 4: 提取 action 执行

**文件:** `yuki_core/bus.py` 新增方法 `_execute_actions`

```python
async def _execute_actions(self, event: PlatformEvent, response: YukiResponse):
    """执行回复中的所有 action"""
    for action in response.actions:
        try:
            if action.type == ActionType.CAPABILITY:
                await self._execute_capability(action)
            elif action.type == ActionType.DELEGATE:
                await self._execute_delegate(action)
            # REPLY / IGNORE 不需要额外执行
        except Exception as e:
            logger.error(f"[Bus] action 执行失败 ({action.type}): {e}")
```

`receive()` 中替换内联逻辑：
```python
if response and response.has_actions:
    await self._execute_actions(event, response)
```

---

### 改动 5: 后台任务错误处理

**文件:** `yuki_core/bus.py` `start()` 方法

**现状:**
```python
for task_coro in plugin.get_background_tasks():
    task = asyncio.create_task(task_coro, name=...)
    self._tasks.append(task)
```

**改为:**
```python
for task_coro in plugin.get_background_tasks():
    task = asyncio.create_task(
        self._wrap_bg_task(plugin, task_coro),
        name=f"bg-{plugin.platform_id}-{task_coro.__name__}"
    )
    self._tasks.append(task)
```

新增：
```python
async def _wrap_bg_task(self, plugin, coro):
    """包装后台任务，捕获异常"""
    try:
        await coro
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[Bus] {plugin.platform_id} 后台任务 {coro.__name__} 异常: {e}", exc_info=True)
```

---

### 改动 6: 事件流日志 + 计时

**文件:** `yuki_core/bus.py` `receive()` 方法

在 `receive()` 中加入关键节点日志和计时：

```python
async def receive(self, event: PlatformEvent) -> Optional[YukiResponse]:
    t0 = time.time()
    if not self._validate_event(event):
        return None

    self._last_message_time[event.session_id] = t0
    logger.debug(f"[Bus] ← {event.source}/{event.session_id} {event.user_name}: {event.content[:60]}")

    if not self._decide_to_reply(event):
        logger.debug(f"[Bus] 跳过 (bot 防环)")
        return None

    # ... mind.process ...

    elapsed = time.time() - t0
    if response:
        logger.info(f"[Bus] → {event.session_id} ({elapsed:.1f}s) {response.text[:60]}")
    else:
        logger.debug(f"[Bus] → {event.session_id} 无回复 ({elapsed:.1f}s)")

    return response
```

---

### 改动 7: 优雅关闭

**文件:** `yuki_core/bus.py` `stop()` 方法

```python
async def stop(self, timeout: float = 5.0):
    self._running = False
    logger.info("[Bus] 正在停止...")

    # 取消所有任务
    for task in self._tasks:
        task.cancel()

    # 等待完成，带超时
    if self._tasks:
        done, pending = await asyncio.wait(self._tasks, timeout=timeout)
        if pending:
            logger.warning(f"[Bus] {len(pending)} 个任务超时未完成，强制取消")
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    self._tasks.clear()

    # 断开插件
    for plugin in self.platform_plugins.values():
        try:
            await plugin.disconnect()
        except Exception as e:
            logger.error(f"[Bus] 断开 {plugin.name} 失败: {e}")

    close_session()
    logger.info("[Bus] 已停止")
```

---

### 改动 8: get_status 增强

**文件:** `yuki_core/bus.py` `get_status()` 方法

```python
def get_status(self) -> dict:
    return {
        "running": self._running,
        "platforms": {
            pid: {
                "name": p.name,
                "version": p.version,
            }
            for pid, p in self.platform_plugins.items()
        },
        "capabilities": list(self.capability_plugins.keys()),
        "active_tasks": len([t for t in self._tasks if not t.done()]),
        "total_tasks": len(self._tasks),
        "last_message_times": dict(self._last_message_time),
    }
```

---

## 不改的东西

- **models.py** — 数据模型不变
- **mind.py** — 决策引擎不变
- **plugin.py** — 插件基类不变（PlatformPlugin/CapabilityPlugin 接口不变）
- **identity.py** — 身份系统不变
- **memory.py** — 记忆系统不变
- **history.py** — 历史管理不变
- **llm.py** — LLM 调用不变
- **maid.py** — 女仆代理不变
- **config.py** — 配置不变
- **logger.py** — 日志不变

---

## 改动文件汇总

| 文件 | 改动类型 | 改动量 |
|------|----------|--------|
| `yuki_core/bus.py` | 修改 | ~80行改动 |

**总计: 只改 1 个文件。**

---

## 验证方式

1. 确认所有现有 import 不变
2. 确认 `main.py` 启动流程不变
3. 确认 `ContextBus.__init__` 签名不变
4. 确认 `register_platform`/`register_capability` 签名不变
5. 确认 `receive(event)` 签名和返回值不变
6. 确认 `_decide_to_reply` 逻辑等价（只是更健壮）
7. 确认 `stop()` 向后兼容（timeout 有默认值）

---

## 风险

- **低:** 所有改动都在 bus.py 内部，不改变对外接口
- **低:** 重连逻辑依赖 plugin 的 `connect()`/`disconnect()` 幂等性（V6 的 BotConnector 已支持）
- **低:** `_validate_event` 自动填充 session_id 可能改变某些边界行为（但更正确）
