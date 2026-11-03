"""Local Windows demo server; one inference worker, bounded completed history."""
import argparse
from collections import OrderedDict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
import uuid

import cv2

from exporter import export_frame, validate_directory, save_session_index
from live import Pipeline
from wire import json_bytes, png

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]


def defaults():
    return {'board': 'http://192.168.2.99:8767', 'camera': 0, 'backend': 'dshow',
            'width': 1920, 'height': 1080, 'capture_fps': 30, 'format': 'MJPG',
            'exposure_mode': 'driver', 'exposure': None, 'focus': None,
            'interval': 1.0, 'confidence': .25, 'weights': str(REPOSITORY/'data'/'models'/'best.pt'),
            'output': str(REPOSITORY/'build'/'output'/'dynamic_results')}


def checked_config(raw):
    config = defaults()
    if set(raw)-set(config):
        raise ValueError('未知设置项')
    config.update(raw)
    for key, low, high in [('interval', 0, 60), ('confidence', .01, .99), ('capture_fps', 1, 60)]:
        v = float(config[key])
        if not math.isfinite(v) or not low <= v <= high:
            raise ValueError('参数范围错误：'+key)
        config[key] = v
    for key, low, high in [('camera', 0, 20), ('width', 160, 3840), ('height', 120, 2160)]:
        v = config[key]
        if type(v) is not int or not low <= v <= high:
            raise ValueError('参数范围错误：'+key)
    if config['backend'] not in ('dshow', 'msmf') or config['format'] not in ('MJPG', 'YUY2') or config['exposure_mode'] not in ('driver', 'auto', 'manual'):
        raise ValueError('不支持的相机设置')
    for key in ('exposure', 'focus'):
        if config[key] is not None:
            config[key] = float(config[key])
            if not math.isfinite(config[key]):
                raise ValueError('无效相机控制值')
    if config['exposure_mode'] == 'manual' and config['exposure'] is None:
        raise ValueError('手动曝光需要填写驱动曝光值')
    parsed = urlparse(config['board'])
    if parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('开发板地址应为 http://IP:端口')
    weights = Path(config['weights']).expanduser()
    if not weights.is_absolute() or not weights.is_file() or weights.suffix.lower() != '.pt':
        raise ValueError('请选择存在的本地 .pt 模型文件')
    config['weights'] = str(weights.resolve())
    config['output'] = str(validate_directory(config['output']))
    return config


class App:
    def __init__(self, config, factory=Pipeline, settings_path=None):
        self.config, self.factory = dict(config), factory
        self.settings_path = Path(settings_path) if settings_path else ROOT/'settings.json'
        self.pipeline = factory(config)
        self.condition = threading.Condition(threading.RLock())
        self.quit, self.pending, self.busy = False, None, False
        self.continuous, self.follow_latest = False, True
        self.last_start, self.latest_id, self.display_id = None, None, None
        self.frames, self.completions = OrderedDict(), deque(maxlen=20)
        self.error, self.message = None, '先开始采集，在中心方框内摆放钢板并手动调焦，再进行单帧检测。'
        self.last_saved, self.saved_count = None, 0
        self.saved_results, self.session_entries = {}, {}
        self.unsaved_result, self.saving, self.save_ms, self.sequence = None, False, None, 0
        self.session_name = 'session_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
        self.worker = threading.Thread(target=self._work, name='single-inference-worker', daemon=True)
        self.worker.start()

    def next_delay(self, now):
        return 0 if self.last_start is None else max(0, self.last_start+self.config['interval']-now)

    def status(self):
        with self.condition:
            fps = None
            if self.continuous and len(self.completions) > 1:
                fps = (len(self.completions)-1)/max(self.completions[-1]-self.completions[0], .001)
            return {'config': self.config, 'camera': self.pipeline.camera.status(), 'board': self.pipeline.health,
                'busy': self.busy, 'continuous': self.continuous, 'following': self.follow_latest,
                'latest_id': self.latest_id, 'display_id': self.display_id, 'error': self.error, 'message': self.message,
                'completion_fps': fps, 'last_saved': self.last_saved, 'saved_count': self.saved_count,
                'auto_save': True, 'saving': self.saving, 'save_ms': self.save_ms,
                'unsaved_frame_id': self.unsaved_result[0]['frame_id'] if self.unsaved_result else None,
                'history': [{'frame_id': k, 'captured_at': v[0]['captured_at'], 'detections': len(v[0]['predictions'])} for k, v in reversed(self.frames.items())]}

    def command(self, action, frame_id=None):
        with self.condition:
            if action in ('pause', 'select'):
                if frame_id is not None and frame_id not in self.frames:
                    raise ValueError('该帧不在缓存中，请选择当前历史帧')
                self.continuous, self.follow_latest = False, False
                self.pending = None
                self.display_id = frame_id or self.display_id
                self.message = '已暂停；正在处理的帧会完成并保留在历史中，当前画面保持不变。' if self.busy else '已暂停当前帧。'
                self.condition.notify_all()
                return
            if action == 'latest':
                self.follow_latest, self.display_id = True, self.latest_id
                return
            if action not in ('start', 'stop', 'check', 'single', 'continuous', 'stop_board', 'retry_save'):
                raise ValueError('未知操作')
            if self.busy or self.pending:
                raise ValueError('当前操作尚未完成，请稍候；暂停可以立即生效')
            if action in ('stop', 'stop_board'):
                self.continuous = False
            if action in ('single', 'continuous'):
                if self.unsaved_result:
                    raise ValueError('上一帧尚未完整保存，请先重试保存；需要时修改保存目录')
                if not self.pipeline.camera.status()['ready']:
                    raise ValueError('请先开始采集并等待相机就绪')
                self.follow_latest = True
                if action == 'continuous':
                    self.completions.clear()
                    self.continuous, self.last_start = True, None
            self.error = None
            self.pending = action
            self.condition.notify_all()

    def configure(self, raw):
        with self.condition:
            if self.busy or self.pending or self.continuous:
                raise ValueError('请暂停并等待当前帧完成，再应用设置')
            candidate = checked_config(raw)
            self.pipeline.close()
            self.config, self.pipeline = candidate, self.factory(candidate)
            portable = dict(candidate)
            for key in ('weights', 'output'):
                p = Path(portable[key])
                try:
                    portable[key] = str(p.relative_to(ROOT))
                except ValueError:
                    pass
            self.settings_path.write_bytes(json_bytes(portable))
            self.message, self.error = '设置已保存；请重新开始采集。', None

    def _publish(self, result):
        metadata, images = result
        frame_id = metadata['frame_id']
        if 'sequence' not in metadata:
            self.sequence += 1
            metadata['sequence'] = self.sequence
            metadata['captured_at_local'] = datetime.fromisoformat(metadata['captured_at'].replace('Z', '+00:00')).astimezone().isoformat(timespec='milliseconds')
            metadata['save_policy'] = 'automatic_all_completed_detections_including_empty'
        with self.condition:
            self.unsaved_result, self.saving = result, True
        save_start = time.monotonic()
        audit_dir = Path(self.config['output'])/self.session_name
        try:
            saved = export_frame(audit_dir, metadata, images)
            key = str(audit_dir)
            entries = self.session_entries.setdefault(key, {})
            entries[frame_id] = dict(saved, sequence=metadata['sequence'], captured_at_local=metadata['captured_at_local'], detections=len(metadata['predictions']))
            save_session_index(audit_dir, list(entries.values()))
            # One compact line per successful frame, with full records in frame.json.
            temp = audit_dir/'frames.jsonl.tmp'
            with temp.open('wb') as stream:
                for entry in sorted(entries.values(), key=lambda e: e['sequence']):
                    stream.write(json_bytes(entry)+b'\n')
            temp.replace(audit_dir/'frames.jsonl')
        except Exception as exc:
            raise RuntimeError('自动保存失败，已保留当前帧并停止继续检测。检查磁盘/保存目录后点击“重试保存”：'+str(exc)) from exc
        finally:
            with self.condition:
                self.saving = False
        with self.condition:
            self.unsaved_result = None
            if frame_id not in self.saved_results:
                self.saved_count += 1
            self.saved_results[frame_id] = saved
            self.last_saved, self.save_ms = saved, (time.monotonic()-save_start)*1000
            self.frames[frame_id] = (metadata, images)
            self.latest_id = frame_id
            if self.follow_latest:
                self.display_id = frame_id
            while len(self.frames) > 12:
                key = next(k for k in self.frames if k not in (self.display_id, self.latest_id))
                self.frames.pop(key)
            self.completions.append(time.monotonic())

    def _work(self):
        while True:
            with self.condition:
                if self.quit:
                    return
                action = self.pending
                if action is None and self.continuous:
                    delay = self.next_delay(time.monotonic())
                    if delay:
                        self.condition.wait(min(delay, .2))
                        continue
                    action = 'continuous'
                if action is None:
                    self.condition.wait(.2)
                    continue
                self.pending, self.busy = None, True
            try:
                if action == 'start':
                    self.pipeline.camera.start()
                    message = '相机正在采集；请在左侧实时取景中调焦，将缺陷放入中心检测区。'
                elif action == 'stop':
                    self.pipeline.camera.stop()
                    message = '相机已停止。已完成的检测帧仍可保存。'
                elif action == 'check':
                    self.pipeline.check_board()
                    message = '当前 v10 与已选掩膜动态服务已连接。'
                elif action == 'stop_board':
                    self.pipeline.stop_board()
                    message = '已请求板端正常结束；请在 Jupyter 确认 HARDWARE_SESSION_SAFE=True。'
                elif action == 'retry_save':
                    if self.unsaved_result is None:
                        raise ValueError('没有待重试的保存帧')
                    self._publish(self.unsaved_result)
                    message = '当前帧已完整保存，可以继续检测。'
                else:
                    # Model loading precedes the detection start cadence.
                    self.pipeline.load_model()
                    with self.condition:
                        self.last_start = time.monotonic()
                    result = self.pipeline.process()
                    if result is not None:
                        self._publish(result)
                    else:
                        with self.condition:
                            self.condition.wait(.02)
                    message = '检测完成，全套材料已自动保存。' if action == 'single' else '连续检测中：每帧完整自动保存，按间隔取最新帧。'
                with self.condition:
                    if self.follow_latest or action not in ('single', 'continuous'):
                        self.message = message
            except Exception as exc:
                with self.condition:
                    self.error, self.continuous = str(exc), False
                    self.message = '已停止自动检测。请查看错误，排除原因后手动恢复。'
            finally:
                with self.condition:
                    self.busy = False
                    self.condition.notify_all()

    def frame(self, identity):
        with self.condition:
            if identity not in self.frames:
                raise ValueError('该帧已离开历史缓存')
            return self.frames[identity]

    def save(self, identity):
        # Compatibility endpoint: retrieve existing evidence, never duplicate it.
        with self.condition:
            if identity in self.saved_results:
                return self.saved_results[identity]
            metadata, images = self.frame(identity)
            output = self.config['output']
        result = export_frame(output, metadata, images)
        with self.condition:
            self.last_saved, self.saved_count = result, self.saved_count+1
        return result

    def close(self):
        with self.condition:
            self.quit, self.continuous, self.pending = True, False, None
            self.condition.notify_all()
        self.worker.join(1)
        if self.worker.is_alive():
            return False
        self.pipeline.close()
        return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, value, content_type='application/json; charset=utf-8'):
        data = value if isinstance(value, bytes) else json_bytes(value)
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ('/', '/index.html'):
                body = (ROOT/'index.html').read_text(encoding='utf-8').replace('__TOKEN__', self.server.token)
                self.respond(200, body.encode('utf-8'), 'text/html; charset=utf-8')
            elif path in ('/style.css', '/ui.js'):
                self.respond(200, (ROOT/path[1:]).read_bytes(), 'text/css' if path.endswith('.css') else 'text/javascript')
            elif path == '/api/status':
                self.respond(200, self.server.app.status())
            elif path == '/api/preview':
                item = self.server.app.pipeline.camera.take()
                if item is None:
                    self.respond(404, {'error': '尚无画面'})
                else:
                    frame = item[0]
                    h, w = frame.shape[:2]
                    s = min(w, h)
                    cv2.rectangle(frame, ((w-s)//2, (h-s)//2), ((w+s)//2, (h+s)//2), (80, 230, 130), max(2, w//500))
                    small = cv2.resize(frame, (480, max(1, int(480*h/w))))
                    ok, encoded = cv2.imencode('.jpg', small)
                    self.respond(200, encoded.tobytes(), 'image/jpeg')
            elif path.startswith('/api/frame/'):
                parts = path.split('/')
                metadata, images = self.server.app.frame(parts[3])
                name = '/'.join(parts[4:])
                if name == 'frame.json':
                    self.respond(200, metadata)
                elif name in images:
                    self.respond(200, images[name], 'image/png')
                else:
                    self.respond(404, {'error': 'Unknown frame file'})
            else:
                self.respond(404, {'error': 'Unknown route'})
        except Exception as exc:
            self.respond(400, {'error': str(exc)})

    def do_POST(self):
        if self.headers.get('X-Demo-Token') != self.server.token:
            self.respond(403, {'error': '请从本机演示页操作'})
            return
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 16384 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request size')
            value = json.loads(self.rfile.read(size))
            if self.path == '/api/action':
                self.server.app.command(value['action'], value.get('frame_id'))
                result = {'ok': True}
            elif self.path == '/api/config':
                self.server.app.configure(value)
                result = {'ok': True}
            elif self.path == '/api/save':
                result = self.server.app.save(value['frame_id'])
            elif self.path == '/api/pick':
                kind = value.get('kind')
                if kind not in ('folder', 'model'):
                    raise ValueError('Invalid picker')
                selected = subprocess.run([sys.executable, str(ROOT/'picker.py'), kind], capture_output=True, timeout=120)
                if selected.returncode:
                    raise RuntimeError('无法打开选择窗口，请直接填写完整路径')
                result = json.loads(selected.stdout.decode('utf-8'))
            else:
                raise ValueError('Unknown action')
            self.respond(200, result)
        except Exception as exc:
            self.respond(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8092)
    parser.add_argument('--weights')
    parser.add_argument('--output')
    args = parser.parse_args()
    config = defaults()
    if (ROOT/'settings.json').is_file():
        config.update(json.loads((ROOT/'settings.json').read_bytes()))
        for key in ('weights', 'output'):
            if not Path(config[key]).is_absolute():
                config[key] = str(ROOT/config[key])
        # A copied app can recover from paths that existed only on the old PC.
        if not Path(config['weights']).is_file():
            config['weights'] = defaults()['weights']
        if not Path(config['output']).parent.exists():
            config['output'] = defaults()['output']
    for key in ('weights', 'output'):
        if getattr(args, key):
            config[key] = getattr(args, key)
    config = checked_config(config)
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.token = secrets.token_urlsafe(32)
        server.app = App(config)
        print('DYNAMIC_WEB=http://127.0.0.1:%d' % server.server_port, flush=True)
        print('OUTPUT='+config['output'], flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if not server.app.close():
                print('Waiting for the current frame before closing...', flush=True)
                while server.app.worker.is_alive():
                    server.app.worker.join(1)
                server.app.pipeline.close()


if __name__ == '__main__':
    main()
