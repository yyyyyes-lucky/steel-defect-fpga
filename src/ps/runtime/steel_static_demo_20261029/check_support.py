"""Portable output checks and comparison, independent of hardware imports."""
import copy
import hashlib
import json
import math

import numpy as np


def normalized(record):
    r = copy.deepcopy(record)
    for key in ('processing_time_ms', 'base_record_processing_time_ms', 'provenance'):
        r.pop(key, None)
    if r['class_id'] in (1,2,4):
        r['review_reasons'] = [v for v in r['review_reasons'] if v != 'large_change_requires_review']
        r['branch_state'] = 'needs_review' if r['review_reasons'] else 'no_automatic_warning'
        if r['class_id'] == 2:
            r['candidate']['review_reasons'] = list(r['review_reasons'])
        for k in ('previous_branch_state', 'legacy_comparison_performed'):
            r['quality_review'].pop(k, None)
    return r


def array_sha(value):
    return hashlib.sha256(value.tobytes()).hexdigest()


def signature(result):
    records = [normalized(r) for r in result['records']]
    masks = [{k: array_sha(v) for k,v in row.items()} for row in result['masks']]
    return hashlib.sha256(json.dumps([records,masks], sort_keys=True).encode()).hexdigest()


def check_result(result, predictions):
    if len(result['records']) != len(predictions) or len(result['masks']) != len(predictions):
        raise AssertionError('Wrong result count')
    ms = result['timing']['pipeline_ms']
    if not math.isfinite(ms) or ms <= 0:
        raise AssertionError('Invalid direct-pipeline timing')
    for p, r, masks in zip(predictions, result['records'], result['masks']):
        for k in ('detection_index', 'class_id', 'confidence', 'box_xyxy_pixel'):
            if p[k] != r[k]:
                raise AssertionError('Detection changed: ' + k)
        cls = r['class_id']
        keys = {'raw_candidate','coarse_extent'} if cls == 2 else ({'candidate'} if cls in (1,4,5) else set())
        if set(masks) != keys:
            raise AssertionError('Wrong mask contract')
        x1,y1,x2,y2 = r['roi_xyxy_pixel']
        for v in masks.values():
            if v.dtype != np.uint8 or v.shape != (y2-y1,x2-x1) or not np.all((v==0)|(v==255)):
                raise AssertionError('Invalid mask')
        if masks:
            primary = masks['coarse_extent' if cls == 2 else 'candidate']
            pixels = int(np.count_nonzero(primary))
            if r['candidate']['candidate_pixels'] != pixels:
                raise AssertionError('Geometry/mask mismatch')
            if abs(r['candidate']['candidate_fraction'] - pixels/primary.size) > 1e-5:
                raise AssertionError('Area fraction mismatch')


def check_saved(result, expected):
    from comparison import differences
    records = [normalized(r) for r in result['records']]
    record_differences = differences(expected['records'], records)
    mask_differences = []
    for r, masks, old in zip(result['records'], result['masks'], expected['masks']):
        if set(masks) != set(old):
            raise AssertionError('Saved mask keys differ')
        for k,v in masks.items():
            if array_sha(v) != old[k]:
                mask_differences.append({'detection_index':r['detection_index'], 'class_id':r['class_id'], 'mask':k})
    return {'records_exact': not record_differences, 'masks_exact': not mask_differences,
            'record_differences':record_differences, 'mask_differences':mask_differences}
