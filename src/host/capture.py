"""Single owner USB acquisition; never queue camera frames for inference."""
from datetime import datetime, timezone
import threading
import time

import cv2


def now():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


class Camera:
    def __init__(self, config):
        self.config = dict(config)
        self.lock = threading.Lock()
        self.quit = threading.Event()
        self.thread, self.latest, self.error = None, None, None
        self.actual, self.controls, self.sequence = {}, {}, 0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.quit.clear()
        self.error, self.latest = None, None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        cap = None
        try:
            c = self.config
            cap = cv2.VideoCapture(c['camera'], {'dshow': cv2.CAP_DSHOW, 'msmf': cv2.CAP_MSMF}[c['backend']])
            if not cap.isOpened():
                raise RuntimeError('无法打开摄像头，请检查索引并关闭占用相机的软件')
            fmt = c.get('format', 'MJPG')
            accepted = bool(cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fmt)))
            self.controls['format'] = {'requested': fmt, 'driver_accepted': accepted}
            for key, prop in [('width', cv2.CAP_PROP_FRAME_WIDTH), ('height', cv2.CAP_PROP_FRAME_HEIGHT), ('capture_fps', cv2.CAP_PROP_FPS)]:
                requested = c[key]
                accepted = bool(cap.set(prop, requested))
                self.controls[key] = {'requested': requested, 'driver_accepted': accepted, 'readback': cap.get(prop)}
            # Leave the driver's exposure/focus unchanged unless explicitly set.
            if c.get('exposure_mode') == 'auto':
                ok = bool(cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, .75))
                self.controls['auto_exposure'] = {'requested': .75, 'driver_accepted': ok,
                    'readback': cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)}
            if c.get('exposure_mode') == 'manual' and c.get('exposure') is not None:
                auto = bool(cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, .25))
                ok = bool(cap.set(cv2.CAP_PROP_EXPOSURE, c['exposure']))
                self.controls['exposure'] = {'requested': c['exposure'], 'driver_accepted': ok,
                    'auto_disable_accepted': auto, 'readback': cap.get(cv2.CAP_PROP_EXPOSURE),
                    'note': 'Driver-specific units; readback alone does not establish effective manual control.'}
            if c.get('focus') is not None:
                auto = bool(cap.set(cv2.CAP_PROP_AUTOFOCUS, 0))
                ok = bool(cap.set(cv2.CAP_PROP_FOCUS, c['focus']))
                self.controls['focus'] = {'requested': c['focus'], 'driver_accepted': ok,
                    'auto_disable_accepted': auto, 'readback': cap.get(cv2.CAP_PROP_FOCUS)}
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            first = time.monotonic()
            read_count = 0
            while not self.quit.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError('摄像头读取失败，请检查 USB 连接')
                observed = time.monotonic()
                read_count += 1
                self.sequence += 1
                with self.lock:
                    self.latest = (frame.copy(), now(), observed, self.sequence)
                    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
                    self.actual = {'width': frame.shape[1], 'height': frame.shape[0],
                        'format': ''.join(chr((fourcc >> (8*i)) & 255) for i in range(4)),
                        'driver_fps': cap.get(cv2.CAP_PROP_FPS), 'read_fps': read_count/max(observed-first, .001),
                        'backend': cap.getBackendName()}
        except Exception as exc:
            self.error = str(exc)
        finally:
            if cap is not None:
                cap.release()

    def take(self):
        with self.lock:
            if self.latest is None:
                return None
            frame, stamp, observed, seq = self.latest
            return frame.copy(), stamp, observed, seq

    def status(self):
        with self.lock:
            age = None if self.latest is None else time.monotonic()-self.latest[2]
            running = bool(self.thread and self.thread.is_alive())
            return {'running': running, 'ready': running and age is not None and age < 2 and not self.error,
                    'error': self.error, 'age_seconds': age, 'actual': dict(self.actual), 'controls': dict(self.controls)}

    def stop(self):
        self.quit.set()
        if self.thread:
            self.thread.join(3)
            if self.thread.is_alive():
                raise RuntimeError('相机驱动仍在退出，请稍候再试')
        with self.lock:
            self.latest = None
