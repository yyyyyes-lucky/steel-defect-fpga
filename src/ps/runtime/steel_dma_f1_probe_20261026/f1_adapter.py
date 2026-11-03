"""F1 software consumer: rank endpoints and edge count, no full-old-function merge."""
import numpy as np

def scalar_from_ranks(raw, rank_values):
    raw=np.asarray(raw)
    if raw.dtype!=np.uint8 or raw.ndim!=2 or not raw.size or max(raw.shape)>512:
        raise ValueError('Expected nonempty uint8 ROI <=512x512')
    endpoints=np.asarray(rank_values)
    if endpoints.shape!=(4,2) or not np.issubdtype(endpoints.dtype,np.integer) or np.any(endpoints<0) or np.any(endpoints>255) or np.any(endpoints[:,0]>endpoints[:,1]):
        raise ValueError('Bad F1 rank endpoints')
    positions=(raw.size-1)*np.asarray([.5,.1,.9,.95],np.float64)
    weight=positions-np.floor(positions)
    a,b=endpoints[:,0].astype(np.float64),endpoints[:,1].astype(np.float64)
    quantiles=np.where(weight>=.5,b-(b-a)*(1-weight),a+(b-a)*weight)
    # Preserve qualified float32 algorithm for fields not replaced.
    values=np.asarray(raw,dtype=np.float32)
    rounded=lambda v:round(float(v),6)
    return {'mean':rounded(values.mean()),'median':rounded(quantiles[0]),
            'p10':rounded(quantiles[1]),'p90':rounded(quantiles[2]),
            'p95':rounded(quantiles[3]),'std':rounded(values.std())}

def descriptors_from_count(roi, std2, std4, sobel, edge_count):
    if isinstance(edge_count, (bool, np.bool_)) or not isinstance(edge_count, (int, np.integer)) or not 0 <= edge_count <= sobel.size:
        raise ValueError('Bad F1 edge count')
    grid = grid_metrics(std2)
    orientation = orientation_metrics(roi)
    mean2, mean4 = (float(std2.mean()), float(std4.mean()))
    return {'local_std_sigma2_mean': rounded(mean2), 'local_std_sigma2_p90': rounded(np.percentile(std2, 90)), 'local_std_sigma4_mean': rounded(mean4), 'local_std_sigma4_p90': rounded(np.percentile(std4, 90)), 'coarse_to_fine_std_ratio': rounded(mean4 / (mean2 + 1e-09)), 'edge_density_ge_64': rounded(edge_count / sobel.size), 'gradient_orientation_entropy': orientation['entropy'], 'gradient_orientation_coherence': orientation['coherence'], 'strong_edge_pixel_count': orientation['strong_edge_pixel_count'], 'grid_cell_mean_cv': grid['cell_mean_cv'], 'grid_cell_mean_range': grid['cell_mean_range'], 'grid_active_fraction_ge_4': grid['active_fraction_ge_4'], 'grid': grid}
