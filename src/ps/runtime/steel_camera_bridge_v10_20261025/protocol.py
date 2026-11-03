"""Fixed image/ROI wire contract shared by PC and board. No hardware imports."""
import hashlib
import json
import math
import struct

SCHEMA = 'steel_camera_v10/1.0'
PIXELS = 512 * 512
MAX_BODY = PIXELS + 65536 + 4


def predictions_checked(predictions):
    if not isinstance(predictions, list) or len(predictions) > 100:
        raise ValueError('Expected at most 100 detections')
    seen = set()
    for p in predictions:
        if not isinstance(p, dict) or set(p) != {'class_id', 'detection_index', 'confidence', 'box_xyxy_pixel'}:
            raise ValueError('Unexpected prediction fields')
        if type(p['class_id']) is not int or not 0 <= p['class_id'] <= 5:
            raise ValueError('Invalid class')
        index = p['detection_index']
        if type(index) is not int or index < 0 or index in seen:
            raise ValueError('Invalid detection index')
        seen.add(index)
        confidence = p['confidence']
        if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('Invalid confidence')
        box = p['box_xyxy_pixel']
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError('Invalid box')
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 512 and 0 <= y1 < y2 <= 512):
            raise ValueError('Box outside shared 512x512 coordinate space')
    return predictions


def encode_request(raw, predictions, request_id):
    if len(raw) != PIXELS:
        raise ValueError('Expected 512x512 uint8 image bytes')
    predictions_checked(predictions)
    if not isinstance(request_id, str) or len(request_id) != 32 or any(c not in '0123456789abcdef' for c in request_id):
        raise ValueError('Expected 32 lowercase hex request ID')
    header = {'schema': SCHEMA, 'request_id': request_id, 'image_sha256': hashlib.sha256(raw).hexdigest(),
              'predictions': predictions}
    encoded = json.dumps(header, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > 65536:
        raise ValueError('Header too large')
    return struct.pack('!I', len(encoded)) + encoded + raw


def decode_request(payload):
    if not 4 + PIXELS < len(payload) <= MAX_BODY:
        raise ValueError('Invalid payload size')
    length = struct.unpack('!I', payload[:4])[0]
    if not 0 < length <= 65536 or len(payload) != 4 + length + PIXELS:
        raise ValueError('Invalid header/image lengths')
    header = json.loads(payload[4:4+length].decode('utf-8'))
    if not isinstance(header, dict) or set(header) != {'schema', 'request_id', 'image_sha256', 'predictions'} or header['schema'] != SCHEMA:
        raise ValueError('Unknown wire contract')
    raw = payload[4+length:]
    # Re-encoding checks types, boxes, request identity and finite values.
    encode_request(raw, header['predictions'], header['request_id'])
    if hashlib.sha256(raw).hexdigest() != header['image_sha256']:
        raise ValueError('Image hash mismatch')
    return header, raw
