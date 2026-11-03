"""Record construction for selected masks, without running the old masks first."""
import numpy as np
from selected_quality import REVISION, make_masks, review_mask


def branch(cls, maps, bounds, texture, runtime):
    masks, info, dark = make_masks(cls, maps, bounds)
    primary = 'coarse_extent' if cls == 2 else 'candidate'
    metrics, reasons = review_mask(cls, masks[primary])
    if cls == 4 and metrics['components'] > 30:
        reasons.append('many_components_requires_review')
    if cls == 1:
        candidate = runtime.inclusion_stats(masks[primary])
        if texture['local_std_sigma2_mean'] >= 5:
            reasons.append('texture_requires_review')
        if metrics['fraction'] >= .25:
            reasons.append('dense_inclusion_candidate')
        if info['response_p99'] < info['high_threshold']:
            reasons.append('weak_signal_relative_to_noise')
        details = {'quality_candidate': info}
        method = 'accepted two-scale inclusion candidate; unchanged'
    elif cls == 2:
        candidate = runtime.mask_stats(masks[primary])
        raw_stats = runtime.mask_stats(masks['raw_candidate'])
        dark_stats = runtime.mask_stats(dark)
        if info['gray_group_contrast'] < info['contrast_floor']:
            reasons.append('low_gray_contrast')
        if not np.any(masks['raw_candidate']):
            reasons.append('empty_raw_support')
        if dark_stats['candidate_fraction'] < .05 or dark_stats['candidate_fraction'] > .85:
            reasons.append('extreme_dark_fraction')
        candidate.update({'supported_otsu_component_count': info['supported_components'],
                          'added_pixels': int(np.count_nonzero((masks[primary] > 0) & (masks['raw_candidate'] == 0)))})
        details = {'raw_candidate': raw_stats, 'otsu_dark_group': dark_stats,
                   'otsu': {k: info[k] for k in ('otsu_threshold', 'dark_group_mean', 'bright_group_mean', 'gray_group_contrast')},
                   'quality_candidate': info}
        method = 'adaptive dark seeds; directly supported sigma1.5 Otsu coarse extent'
    elif cls == 4:
        candidate = runtime.scale_stats(masks[primary], bounds)
        details = {'quality_candidate': info}
        method = 'noise-aware broad dark hysteresis; min20; separate components preserved'
        if info['response_p99'] < info['high_threshold']:
            reasons.append('weak_signal_relative_to_noise')
    else:
        raise ValueError('Only classes 1/2/4 use the revised branch')
    reasons = list(dict.fromkeys(reasons))
    if cls == 2:
        candidate['review_reasons'] = list(reasons)
    review = {'revision': REVISION, 'meaning': 'area_candidate',
              'validated_segmentation': False, 'mask_metrics': metrics,
              'legacy_comparison_performed': False}
    # previous_branch_state and large_change_requires_review are offline OLD/NEW
    # comparisons, not properties of the selected mask. Do not compute a discarded
    # old mask on every real-time frame just to recreate those comparison labels.
    return candidate, details, masks, reasons, review, method
