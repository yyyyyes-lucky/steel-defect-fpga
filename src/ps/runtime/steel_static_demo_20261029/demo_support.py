"""Portable input verification, actual evidence export and batch summaries."""
import base64
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from comparison import summarize

ROOT = Path(__file__).resolve().parent
NAMES = ['裂纹', '夹杂', '斑块', '麻面', '氧化皮压入', '划痕']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def load_inputs(root=ROOT):
    manifest = json.loads((root / 'inputs.json').read_bytes())
    rows, counts, identities = manifest['frames'], [0] * 6, set()
    cases = []
    for row in rows:
        name = row['case']
        if name in identities:
            raise ValueError('Duplicate input identity')
        identities.add(name)
        counts[row['source_class_id']] += 1
        path = (root / row['input_gray']).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise ValueError('Input file missing or outside package: ' + name)
        gray = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
        if gray is None or gray.shape != (512, 512) or gray.dtype != np.uint8:
            raise ValueError('Invalid grayscale input')
        # Runtimes validate the complete detection contract before DMA.
        cases.append((name, gray, row['predictions']))
    if counts != [20] * 6 or len(cases) != 120:
        raise ValueError('Expected six selected groups of twenty')
    return manifest, cases


def encode(value):
    ok, data = cv2.imencode('.png', value)
    if not ok:
        raise RuntimeError('Cannot encode evidence')
    return base64.b64encode(data.tobytes()).decode('ascii')


def frame_sources(inputs, cases):
    """Keep display metadata together with the actual decoded input identity."""
    rows = {row['case']: row for row in inputs['frames']}
    return {name: dict(rows[name],
                       gray_array_sha256=hashlib.sha256(gray.tobytes()).hexdigest())
            for name, gray, _ in cases}


def overlay(result):
    canvas = np.zeros((512, 512, 4), np.uint8)
    # OpenCV encodes BGRA, so these become green, cyan and amber in the browser.
    colors = {'candidate': (100, 185, 36), 'raw_candidate': (215, 163, 36),
              'coarse_extent': (38, 163, 234)}
    for record, masks in zip(result['records'], result['masks']):
        x, y, x2, y2 = record['roi_xyxy_pixel']
        for key, mask in masks.items():
            if mask.shape != (y2-y, x2-x):
                raise ValueError('Mask geometry differs')
            canvas[y:y2, x:x2][mask > 0] = (*colors[key], 100)
    return encode(canvas)


def evidence(item, pair, checked, source):
    name, gray, predictions = item
    row = {'case': name, 'source': source, 'predictions': predictions,
           'input_png': encode(gray), 'audit': checked}
    for variant in ('cpu', 'fpga'):
        value = pair.get(variant)
        row[variant] = None if value is None else {
            'records': value['records'], 'overlay_png': overlay(value),
            'masks': [{k: encode(v) for k, v in masks.items()} for masks in value['masks']]}
    return row


def batch_summary(measurements, inputs, rounds=2):
    result = summarize(measurements, [r['case'] for r in inputs], rounds)
    # Group full-frame costs by the source image class, without assigning shared
    # features to individual predicted ROIs or suppressing wrong/empty detections.
    result['source_classes'] = []
    for cls in range(6):
        names = [r['case'] for r in inputs if r['source_class_id'] == cls]
        subset = summarize([r for r in measurements if r['case'] in names], names, rounds)
        result['source_classes'].append({'class_id': cls, 'frames': len(names),
            'cpu_ms': subset['timings_ms']['cpu']['mean'],
            'fpga_ms': subset['timings_ms']['fpga']['mean'], 'speedup': subset['speedup']})
    return result


def make_html(path, report, frames, template=None):
    if report.get('mode') != 'host_preview':
        if not report.get('completed') or not report.get('hardware_session_safe') or not report.get('pass'):
            raise ValueError('Cannot publish an incomplete board experiment')
        if not report.get('summary') or any(f['fpga'] is None or f['audit'] is None for f in frames):
            raise ValueError('Missing measured FPGA evidence')
    if len(frames) != report['frames_expected'] or len({f['case'] for f in frames}) != len(frames):
        raise ValueError('Evidence coverage differs')
    payload = json.dumps({'report': report, 'frames': frames}, ensure_ascii=False,
                         allow_nan=False, separators=(',', ':')).replace('<', '\\u003c')
    text = (template or ROOT / 'viewer.html').read_text(encoding='utf-8')
    Path(path).write_text(text.replace('__EVIDENCE_JSON__', payload), encoding='utf-8')
