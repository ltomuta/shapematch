"""Proper similarity transforms and bidirectional CAD-surface registration.

All transforms use target = scale * (rotation @ source) + translation.
No benchmark placement metadata or source filenames are used to recover a fit.
"""
from itertools import permutations, product

import numpy as np


def fit_points(source, target, estimate_scale=True):
    """Fit corresponding N-by-3 points using their means, never model centroids."""
    source, target = np.asarray(source, dtype=float), np.asarray(target, dtype=float)
    if (source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3
            or len(source) < 3 or not np.isfinite(source).all() or not np.isfinite(target).all()):
        raise ValueError("Alignment requires at least three finite corresponding 3D points")
    ca, cb = source.mean(axis=0), target.mean(axis=0)
    a, b = source - ca, target - cb
    if np.linalg.matrix_rank(a) < 2 or np.linalg.matrix_rank(b) < 2:
        raise ValueError("Collinear points cannot determine a 3D rotation")
    u, singular, vt = np.linalg.svd(a.T @ b)
    signs = np.ones(3)
    signs[-1] = 1 if np.linalg.det(vt.T @ u.T) > 0 else -1
    rotation = vt.T @ np.diag(signs) @ u.T
    scale = float(np.dot(singular, signs) / np.sum(a * a)) if estimate_scale else 1.0
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Alignment scale must be positive")
    translation = cb - scale * rotation @ ca
    return rotation, translation, scale


def transform(points, rotation, translation, scale=1):
    return scale * np.asarray(points) @ np.asarray(rotation).T + translation


def surface_errors(source, target, rotation, translation, scale, source_points, target_points):
    """Both directions expressed in target units; interior points are not free."""
    placed = transform(source_points, rotation, translation, scale)
    forward = target.closest_points(placed)[1]
    inverse = (np.asarray(target_points) - translation) @ rotation / scale
    reverse = source.closest_points(inverse)[1] * scale
    return forward, reverse


def surface_rmse(errors):
    forward, reverse = errors
    return float(np.sqrt((np.mean(forward ** 2) + np.mean(reverse ** 2)) / 2))


def _subset(points, count):
    return points[np.linspace(0, len(points) - 1, min(count, len(points)), dtype=int)]


def _refine(source, target, initial, a, b, estimate_scale=True):
    """Trimmed closest-surface refinement; only fitting trims, reporting does not."""
    rotation, translation, scale = initial
    initial_scale = scale
    previous = float("inf")
    for _ in range(25):
        placed = transform(a, rotation, translation, scale)
        closest_b, forward = target.closest_points(placed)
        inverse = (b - translation) @ rotation / scale
        closest_a, reverse = source.closest_points(inverse)
        errors = np.concatenate((forward, reverse * scale))
        keep = errors <= np.quantile(errors, .8)
        paired_a = np.concatenate((a, closest_a))[keep]
        paired_b = np.concatenate((closest_b, b))[keep]
        try:
            candidate = fit_points(paired_a, paired_b, estimate_scale=estimate_scale)
        except ValueError:
            break
        if not .5 <= candidate[2] / initial_scale <= 2:
            break
        error = float(np.sqrt(np.mean(errors[keep] ** 2)))
        rotation, translation, scale = candidate
        if abs(previous - error) <= target.diagonal * 1e-9:
            break
        previous = error
    return rotation, translation, scale


def register(source, target, candidates=(), sample_count=96, estimate_scale=True):
    """Evaluate feature and principal-frame candidates, then refine the best fits.

    Principal frames supply missing degrees of freedom when extraction returns
    only two points. All 24 proper signed axis permutations handle axis/sign
    ambiguity without allowing mirror transforms. With estimate_scale=False,
    every hypothesis and refinement is rigid, including caller-supplied fits.
    """
    a, b = source.surface_samples(sample_count), target.surface_samples(sample_count)
    volume_a, ca, axes_a = source.principal_frame()
    volume_b, cb, axes_b = target.principal_frame()
    scale = (volume_b / volume_a) ** (1 / 3) if estimate_scale else 1.0
    hypotheses = list(candidates) if estimate_scale else [
        (r, cb - r @ ca, 1.0) for r, t, s in candidates]
    frames = [(axes_a, axes_b)]
    planar_a, planar_b = source.planar_frame(), target.planar_frame()
    if planar_a is not None and planar_b is not None:
        frames.append((planar_a, planar_b))
    for frame_a, frame_b in frames:
        for order in permutations(range(3)):
            for signs in product((-1, 1), repeat=3):
                rotation = frame_b[:, order] @ np.diag(signs) @ frame_a.T
                if np.linalg.det(rotation) > 0:
                    hypotheses.append((rotation, cb - scale * rotation @ ca, scale))
    # Identity orientation is also a geometric candidate, with no knowledge of
    # original placements. This helps axis-degenerate shapes already overlapping.
    hypotheses.append((np.eye(3), cb - scale * ca, scale))
    coarse_a, coarse_b = _subset(a, 16), _subset(b, 16)
    ranked = sorted(
        ((surface_rmse(surface_errors(source, target, *candidate, coarse_a, coarse_b)), index, candidate)
         for index, candidate in enumerate(hypotheses)), key=lambda item: (item[0], item[1]))
    best = None
    # Exact symmetric alternatives need no iterative refinement. Otherwise try
    # multiple starts rather than returning the first plausible correspondence.
    for coarse_error, _, candidate in ranked[:3]:
        alternatives = [candidate]
        if coarse_error > target.tolerance:
            alternatives.append(_refine(source, target, candidate, _subset(a, 64), _subset(b, 64), estimate_scale=estimate_scale))
        # Trimming can improve the dominant surface while worsening total RMS.
        # Keep the original fit available when selecting the reported result.
        for fit in alternatives:
            errors = surface_errors(source, target, *fit, a, b)
            score = surface_rmse(errors)
            if best is None or score < best[0]:
                best = score, fit, errors
        if best[0] <= target.tolerance:
            break
    return best
