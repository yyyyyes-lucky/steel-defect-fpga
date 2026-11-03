"""Small, bounded transport contract shared by PC and board (Python 3.6+)."""
import base64
import hashlib
import json
import math
import re

import cv2
import numpy as np

RELEASE = 'steel_dynamic_selected_masks/1.0'
REVISION = 'mask_quality_all_sw_2_legacy_scratch'
VERSION = 'v10_v91_q8_selected_masks_direct_1'
MAX_BODY = 4 * 1024 * 1024
MAX_REPLY = 24 * 1024 * 1024
NAMES = ['裂纹', '夹杂', '斑块', '麻面', '氧化皮压入', '划痕']
ENGLISH = ['crazing', 'inclusion', 'patches', 'pitted_surface', 'rolled-in_scale', 'scratches']


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def png(value):
    ok, encoded = cv2.imencode('.png', value)
    if not ok:
        raise ValueError('PNG encoding failed')
    return encoded.tobytes()


def encode_png(value):
    return base64.b64encode(png(value)).decode('ascii')


def decode_png(value, shape):
    raw = base64.b64decode(value, validate=True)
    # Check header dimensions before handing a remotely supplied PNG to OpenCV.
    if len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise ValueError('Invalid PNG')
    w, h = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
    if (h, w) != tuple(shape):
        raise ValueError('PNG geometry mismatch')
    result = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if result is None or result.shape != tuple(shape):
        raise ValueError('PNG decode failed')
    return result


def validate_predictions(predictions):
    if not isinstance(predictions, list) or len(predictions) > 100:
        raise ValueError('Expected at most 100 detections')
    for i, p in enumerate(predictions):
        if type(p.get('class_id')) is not int or p['class_id'] not in range(6) or p.get('detection_index') != i:
            raise ValueError('Invalid class/index')
        conf = p.get('confidence')
        box = p.get('box_xyxy_pixel')
        if not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
            raise ValueError('Invalid confidence')
        if not isinstance(box, list) or len(box) != 4 or any(not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 512 for x in box):
            raise ValueError('Invalid box')
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError('Empty box')


def request_payload(frame_id, gray, predictions):
    validate_predictions(predictions)
    return {'release': RELEASE, 'frame_id': frame_id, 'image_sha256': digest(gray.tobytes()),
            'gray_png': encode_png(gray), 'predictions': predictions}


def decode_request(value):
    if value.get('release') != RELEASE or not re.fullmatch(r'[a-f0-9]{32}', value.get('frame_id', '')):
        raise ValueError('Unexpected request identity/version')
    validate_predictions(value.get('predictions'))
    gray = decode_png(value['gray_png'], (512, 512))
    if digest(gray.tobytes()) != value.get('image_sha256'):
        raise ValueError('Input hash mismatch')
    return gray


def decode_response(value, request):
    for key in ('release', 'frame_id', 'image_sha256'):
        if value.get(key) != request[key]:
            raise ValueError('Board response identity mismatch: ' + key)
    if value.get('mask_revision') != REVISION or value.get('version') != VERSION:
        raise ValueError('Board runtime version mismatch')
    records, masks = value.get('records'), value.get('masks')
    if not isinstance(records, list) or not isinstance(masks, list) or len(records) != len(request['predictions']) or len(masks) != len(records):
        raise ValueError('Board ROI count mismatch')
    decoded = []
    for p, r, row in zip(request['predictions'], records, masks):
        if any(r.get(k) != p[k] for k in ('class_id', 'detection_index', 'confidence', 'box_xyxy_pixel')):
            raise ValueError('Detection changed in transit')
        box = r['roi_xyxy_pixel']
        if len(box) != 4 or any(type(x) is not int or not 0 <= x <= 512 for x in box):
            raise ValueError('Invalid ROI coordinates')
        x, y, x2, y2 = box
        if x2 <= x or y2 <= y:
            raise ValueError('Empty ROI')
        keys = {'raw_candidate', 'coarse_extent'} if p['class_id'] == 2 else ({'candidate'} if p['class_id'] in (1, 4, 5) else set())
        if set(row) != keys:
            raise ValueError('Unexpected mask branch')
        arrays = {k: decode_png(v, (y2-y, x2-x)) for k, v in row.items()}
        if any(not np.all((a == 0) | (a == 255)) for a in arrays.values()):
            raise ValueError('Nonbinary mask')
        if arrays:
            a = arrays['coarse_extent' if p['class_id'] == 2 else 'candidate']
            if int(np.count_nonzero(a)) != r['candidate']['candidate_pixels']:
                raise ValueError('Mask statistics mismatch')
        decoded.append(arrays)
    if not isinstance(value.get('pipeline_ms'), (float, int)) or not math.isfinite(value['pipeline_ms']) or value['pipeline_ms'] <= 0:
        raise ValueError('Invalid board timing')
    return decoded
