"""PC YOLO, checked selected-mask board response, immutable per-frame evidence."""
import base64
import json
from pathlib import Path
import time
import uuid

import cv2
import numpy as np
import requests

from capture import Camera, now
from wire import (RELEASE, REVISION, VERSION, MAX_REPLY, ENGLISH,
                  digest, png, request_payload, decode_response)


def predictions_from_result(result):
    return [{'class_id': int(cls), 'detection_index': i, 'confidence': float(conf),
             'box_xyxy_pixel': [float(x) for x in box]}
            for i, (box, cls, conf) in enumerate(zip(result.boxes.xyxy.cpu().tolist(),
                result.boxes.cls.cpu().tolist(), result.boxes.conf.cpu().tolist()))]


def check_names(names):
    aliases = [{'crazing', 'crack', 'cracks', '裂纹'}, {'inclusion', '夹杂'}, {'patches', '斑块'},
               {'pitted_surface', '麻面'}, {'rolled_in_scale', '氧化皮压入'}, {'scratches', 'scratch', '划痕'}]
    values = [names[i] for i in range(6)] if isinstance(names, dict) and len(names) == 6 else names
    if len(values) != 6 or any(str(n).lower().replace('-', '_').replace(' ', '_') not in a for n, a in zip(values, aliases)):
        raise ValueError('模型类别顺序必须与当前六类后处理一致；请确认新模型的类别映射')


def overlays(records, masks):
    primary, seeds = np.zeros((512, 512, 4), np.uint8), np.zeros((512, 512, 4), np.uint8)
    for r, row in zip(records, masks):
        x, y, x2, y2 = r['roi_xyxy_pixel']
        for key, a in row.items():
            layer = seeds if key == 'raw_candidate' else primary
            color = (38, 163, 234, 100) if key == 'coarse_extent' else (100, 185, 36, 100)
            if key == 'raw_candidate':
                color = (215, 163, 36, 100)
            layer[y:y2, x:x2][a > 0] = color
            if key == 'candidate':
                seeds[y:y2, x:x2][a > 0] = color
    return primary, seeds


def render(original, predictions, overlay=None, region=None):
    canvas = original.copy()
    h, w = canvas.shape[:2]
    ox, oy, right, bottom = region or (0, 0, w, h)
    rw, rh = right-ox, bottom-oy
    if overlay is not None:
        rgba = cv2.resize(overlay, (rw, rh), interpolation=cv2.INTER_NEAREST)
        alpha = rgba[:, :, 3:4].astype(np.float32)/255
        canvas[oy:bottom, ox:right] = np.rint(canvas[oy:bottom, ox:right]*(1-alpha)+rgba[:, :, :3]*alpha).astype(np.uint8)
    for p in predictions:
        x, y, x2, y2 = p['box_xyxy_pixel']
        left, top, right, bottom = ox+int(x*rw/512), oy+int(y*rh/512), ox+int(x2*rw/512), oy+int(y2*rh/512)
        cv2.rectangle(canvas, (left, top), (right, bottom), (100, 225, 95), 2)
        label = '%d %s %.0f%%' % (p['detection_index']+1, ENGLISH[p['class_id']], p['confidence']*100)
        cv2.putText(canvas, label, (max(0, min(left, w-220)), max(17, top-4)), cv2.FONT_HERSHEY_SIMPLEX, .45, (100, 225, 95), 1, cv2.LINE_AA)
    return canvas


def limited_json(response):
    response.raise_for_status()
    raw = bytearray()
    for chunk in response.iter_content(65536):
        raw.extend(chunk)
        if len(raw) > MAX_REPLY:
            raise RuntimeError('开发板返回数据超过上限')
    return json.loads(raw)


class Pipeline:
    def __init__(self, config):
        self.config = dict(config)
        self.camera = Camera(config)
        self.session = requests.Session()
        self.session.trust_env = False
        self.model, self.model_sha, self.device = None, None, None
        self.health, self.last_capture_seq = None, -1

    def check_board(self):
        self.health = None
        with self.session.get(self.config['board'].rstrip('/')+'/health', timeout=(2, 5), stream=True) as response:
            health = limited_json(response)
        if health.get('status') != 'ready' or any(health.get(k) != v for k, v in
            [('release', RELEASE), ('version', VERSION), ('mask_revision', REVISION)]):
            raise RuntimeError('板端版本不匹配，请运行新的动态掩膜服务（默认端口 8767）')
        self.health = health
        return health

    def load_model(self):
        if self.model is not None:
            return
        path = Path(self.config['weights'])
        if not path.is_file():
            raise RuntimeError('本地模型文件不存在：'+str(path))
        try:
            import torch
            from ultralytics import YOLO
        except ImportError as exc:
            if exc.name in ('torch', 'torchvision', 'ultralytics'):
                raise RuntimeError('电脑端缺少 YOLO 推理依赖。请停止程序，先运行 install_environment.cmd 安装环境，再运行 start_demo.cmd。')
            raise
        model = YOLO(str(path))
        check_names(model.names)
        self.model_sha = digest(path.read_bytes())
        self.device = 0 if torch.cuda.is_available() else 'cpu'
        self.model = model

    def process(self):
        if self.health is None:
            self.check_board()
        self.load_model()
        item = self.camera.take()
        if not self.camera.status()['ready'] or item is None:
            raise RuntimeError('摄像头未就绪或画面超过 2 秒未更新')
        original, captured, observed, seq = item
        if seq == self.last_capture_seq:
            return None  # Never process a duplicate camera frame in fast mode.
        self.last_capture_seq = seq
        start = time.monotonic()
        frame_id = uuid.uuid4().hex
        h, w = original.shape[:2]
        side = min(h, w)
        x, y = (w-side)//2, (h-side)//2
        region = [x, y, x+side, y+side]
        gray = cv2.resize(cv2.cvtColor(original[y:y+side, x:x+side], cv2.COLOR_BGR2GRAY), (512, 512), interpolation=cv2.INTER_AREA)
        prepared = time.monotonic()
        found = self.model.predict(source=cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), imgsz=512,
            conf=self.config['confidence'], iou=.7, max_det=100, device=self.device, save=False, verbose=False)[0]
        predictions = predictions_from_result(found)
        inferred = time.monotonic()
        request = request_payload(frame_id, gray, predictions)
        http_start = time.monotonic()
        try:
            with self.session.post(self.config['board'].rstrip('/')+'/process', json=request, timeout=(3, 120), stream=True) as response:
                returned = limited_json(response)
            received = time.monotonic()
            masks = decode_response(returned, request)
        except Exception:
            self.health = None
            raise
        main, seeds = overlays(returned['records'], masks)
        # The browser renders YOLO and FPGA views from the same source + layers.
        # Encode full-resolution annotated images only when the user saves a frame.
        images = {'original.png': png(original), 'input.png': png(gray),
                  'overlay.png': png(main), 'seeds.png': png(seeds)}
        for i, row in enumerate(masks):
            for key, a in row.items():
                images['masks/roi_%03d_%s.png' % (i, key)] = png(a)
        end = time.monotonic()
        metadata = {'schema': 'steel_dynamic_frame/1.0', 'source': 'live', 'frame_id': frame_id,
            'captured_at': captured, 'completed_at': now(), 'camera_sequence': seq,
            'original_shape': list(original.shape), 'algorithm_shape': [512, 512],
            'processing_region_xyxy': region,
            'coordinate_mapping': 'center square crop, grayscale then 512x512; x_original=region_x+x_512*region_width/512, likewise y. Outside processing region is not inspected.',
            'predictions': predictions, 'board_response': returned,
            'mask_revision': REVISION, 'version': VERSION, 'weights_sha256': self.model_sha,
            'model_name': Path(self.config['weights']).name, 'model_classes': self.model.names,
            'device': str(self.device), 'camera': self.camera.status(),
            'settings': {k: v for k, v in self.config.items() if k not in ('output', 'weights')},
            'timing_ms': {'preprocess': (prepared-start)*1000, 'yolo': (inferred-prepared)*1000,
                'http_roundtrip': (received-http_start)*1000, 'fpga_pipeline': returned['pipeline_ms'],
                'processing_wall': (end-start)*1000, 'read_return_to_result': (end-observed)*1000},
            'timing_scope': 'processing_wall: latest-frame copy through image encoding; excludes model load, initial health, browser transport/render and export. HTTP includes board postprocessing, so do not add those times. Camera read-return is not exposure time.',
            'exposure_latency_measured': False}
        return metadata, images

    def stop_board(self):
        with self.session.post(self.config['board'].rstrip('/')+'/stop', json={'release': RELEASE}, timeout=(3, 5)) as response:
            response.raise_for_status()
            self.health = None
            return response.json()

    def close(self):
        self.camera.stop()
        self.session.close()
