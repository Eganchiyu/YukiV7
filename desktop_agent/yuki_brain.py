"""
Yuki Desktop Agent - Brain v0.3
用 HTTP POST + SSE 代替 WebSocket，更稳定。
浏览器插件 POST 页面数据，通过 SSE 下发指令。

用法：
    conda activate ai_env
    python yuki_brain.py
"""

import asyncio
import json
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from queue import Queue

# ==================== 配置 ====================

HOST = '127.0.0.1'
PORT = 8766

BANNER = r"""
  ╔══════════════════════════════════════════════╗
  ║     🍙 Yuki Desktop Agent - Brain v0.3     ║
  ║     HTTP: http://127.0.0.1:8766            ║
  ╚══════════════════════════════════════════════╝
"""

# ==================== 状态 ====================

current_elements = []
current_url = ''
current_title = ''
sse_clients = []           # SSE 连接列表（每条是一个 queue）
pending_commands = Queue() # 待发送的指令队列


# ==================== 页面展示 ====================

def print_page_state(snapshot):
    global current_elements, current_url, current_title

    current_url = snapshot.get('url', '')
    current_title = snapshot.get('title', '')
    current_elements = snapshot.get('elements', [])

    ts = datetime.now().strftime('%H:%M:%S')
    trigger = snapshot.get('trigger', '?')

    print(f"\n{'─' * 60}")
    print(f"  📄 [{ts}] {current_title}")
    print(f"  🔗 {current_url}")
    print(f"  📊 {len(current_elements)} elements (trigger: {trigger})")
    print(f"{'─' * 60}")

    if not current_elements:
        print("  (no interactive elements)")
        print(f"{'─' * 60}\n")
        return

    for el in current_elements:
        icon = get_icon(el)
        state = ''
        if el.get('state'):
            state = f" [{', '.join(el['state'])}]"

        text = el.get('text', '') or el.get('placeholder', '') or el.get('href', '') or ''
        if not text:
            text = f"<{el.get('tag', '?')}>"
        if len(text) > 50:
            text = text[:47] + '...'

        tag_extra = el.get('type', '')
        if tag_extra:
            tag_extra = f":{tag_extra}"

        print(f"  {el['id']:>3}  {icon}  {el['tag']}{tag_extra:8s}  {text}{state}")

    print(f"{'─' * 60}")
    print(f"  click <id> | type <id> <text> | scroll | scan | open <url> | list | quit")
    print()


def get_icon(el):
    tag = el.get('tag', '')
    t = el.get('type', '')
    if tag == 'button': return '🔘'
    if tag == 'a':       return '🔗'
    if tag == 'input':
        if t == 'password': return '🔒'
        if t in ('checkbox', 'radio'): return '☑️'
        return '📝'
    if tag == 'select':  return '📋'
    if tag == 'textarea': return '📄'
    return '▪️'


# ==================== HTTP 服务 ====================

class BrainHandler(BaseHTTPRequestHandler):
    """处理来自浏览器插件的 HTTP 请求"""

    def log_message(self, format, *args):
        pass  # 静默 HTTP 日志

    def do_OPTIONS(self):
        """CORS 预检"""
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        """接收浏览器上报的页面数据"""
        if self.path == '/scan':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                print_page_state(data)
                self._json_response(200, {'ok': True})
            except Exception as e:
                self._json_response(400, {'error': str(e)})

        elif self.path == '/action_result':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                status = '✅' if data.get('ok') else '❌'
                error = f" - {data.get('error')}" if data.get('error') else ''
                print(f"  {status} {data.get('action', '?')}{error}")
                self._json_response(200, {'ok': True})
            except Exception as e:
                self._json_response(400, {'error': str(e)})

        else:
            self._json_response(404, {'error': 'not found'})

    def do_GET(self):
        """SSE 端点：浏览器插件保持连接，实时接收指令"""
        if self.path == '/events':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self._cors()
            self.end_headers()

            # 创建这个客户端的队列
            q = Queue()
            sse_clients.append(q)
            print(f"  📡 SSE client connected ({len(sse_clients)} total)")

            try:
                while True:
                    # 从队列取指令，超时发心跳
                    try:
                        cmd = q.get(timeout=15)
                        cmd_json = json.dumps(cmd)
                        self.wfile.write(f"data: {cmd_json}\n\n".encode())
                        self.wfile.flush()
                    except:
                        # 心跳保活
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if q in sse_clients:
                    sse_clients.remove(q)
                print(f"  📴 SSE client disconnected ({len(sse_clients)} total)")

        elif self.path == '/status':
            self._json_response(200, {
                'elements': len(current_elements),
                'url': current_url,
                'title': current_title,
                'sse_clients': len(sse_clients),
            })
        else:
            self._json_response(404, {'error': 'not found'})

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


def broadcast_command(cmd):
    """向所有 SSE 客户端广播指令"""
    dead = []
    for q in sse_clients:
        try:
            q.put_nowait(cmd)
        except:
            dead.append(q)
    for q in dead:
        sse_clients.remove(q)


# ==================== 用户输入 ====================

def find_element(eid):
    for el in current_elements:
        if el.get('id') == eid:
            return el
    return None


def user_input_loop():
    while True:
        try:
            line = input('> ')
        except (EOFError, KeyboardInterrupt):
            print('\n👋 Bye!')
            return

        line = line.strip()
        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        if cmd in ('quit', 'exit'):
            print('👋 Bye!')
            return

        elif cmd == 'scan':
            broadcast_command({'type': 'scan'})
            print('  📡 Scan requested')

        elif cmd == 'list':
            if current_elements:
                fake = {'url': current_url, 'title': current_title,
                        'elements': current_elements, 'trigger': 'list'}
                print_page_state(fake)
            else:
                print('  (no elements)')

        elif cmd == 'click' and len(parts) >= 2:
            try:
                eid = int(parts[1])
                el = find_element(eid)
                if el:
                    print(f'  🖱️  Clicking: {el.get("text", el.get("tag"))}')
                    broadcast_command({
                        'type': 'action',
                        'action': {'action': 'click', 'target': el['selector']}
                    })
                else:
                    print(f'  ❌ #{eid} not found')
            except ValueError:
                print('  Usage: click <id>')

        elif cmd == 'type' and len(parts) >= 3:
            try:
                eid = int(parts[1])
                text = ' '.join(parts[2:])
                el = find_element(eid)
                if el:
                    print(f'  ⌨️  Typing "{text}"')
                    broadcast_command({
                        'type': 'action',
                        'action': {'action': 'type', 'target': el['selector'], 'text': text}
                    })
                else:
                    print(f'  ❌ #{eid} not found')
            except ValueError:
                print('  Usage: type <id> <text>')

        elif cmd == 'scroll':
            direction = parts[1] if len(parts) >= 2 else 'down'
            amount = int(parts[2]) if len(parts) >= 3 else 500
            if direction == 'up':
                amount = -amount
            broadcast_command({
                'type': 'action',
                'action': {'action': 'scroll', 'target': 'window', 'amount': amount}
            })
            print(f'  📜 Scroll {direction} {abs(amount)}px')

        elif cmd == 'open' and len(parts) >= 2:
            url = parts[1]
            if not url.startswith('http'):
                url = 'https://' + url
            broadcast_command({
                'type': 'action',
                'action': {'action': 'navigate', 'url': url}
            })
            print(f'  🌐 Opening: {url}')

        else:
            print(f'  ❓ click <id> | type <id> <text> | scroll | scan | open <url> | list | quit')


# ==================== 入口 ====================

def main():
    print(BANNER)

    # 启动 HTTP 服务（后台线程）
    server = HTTPServer((HOST, PORT), BrainHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  🌐 HTTP + SSE server on http://{HOST}:{PORT}")
    print(f"  📌 Endpoints:")
    print(f"     POST /scan          ← 插件上报页面数据")
    print(f"     POST /action_result ← 插件上报动作结果")
    print(f"     GET  /events        ← 插件接收指令 (SSE)")
    print(f"     GET  /status        ← 查看状态")
    print()

    # 用户输入循环
    try:
        user_input_loop()
    except KeyboardInterrupt:
        print('\n👋 Bye!')

    server.shutdown()


if __name__ == '__main__':
    main()
