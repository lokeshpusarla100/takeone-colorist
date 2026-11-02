"""Eight brief-derived look previews on one representative frame.

Looks are bounded variations of the planner's own proposal for the current
Creative Brief, rendered with the same v3 colour pipeline the full render
uses (color_processing.apply_rgb / the LUT built from it). They are not
image-model concepts and not fixed presets: the base look is Muse's plan
for this footage and brief, and every variation is a clamped, validated
GradeProposal that the existing renderer can execute unchanged.
"""
from uuid import uuid4

import cv2
import numpy as np

from . import color_processing
from .contracts import GradeParameters, GradeProposal

BOUNDS = {
    'contrast': (0.75, 1.25), 'saturation': (0., 1.5), 'gamma': (0.8, 1.2),
    'brightness_gain': (0.65, 1.35), 'cooling': (-0.15, 0.15),
    'black_point': (0., 0.15), 'white_point': (0.75, 1.), 'highlight_ceiling': (0.6, 1.),
}
FIELDS = list(BOUNDS)


def _clamped(values):
    return GradeParameters(**{k: min(max(float(values[k]), BOUNDS[k][0]), BOUNDS[k][1]) for k in FIELDS})


def _vary(parameters, fn):
    values = parameters.model_dump()
    fn(values)
    return _clamped(values)


def _toward_neutral(factor):
    def fn(v):
        for k in ['contrast', 'gamma', 'brightness_gain', 'highlight_ceiling']:
            v[k] = 1 + (v[k] - 1) * factor
    return fn


def _away_from_neutral(factor):
    def fn(v):
        for k in ['contrast', 'gamma', 'highlight_ceiling']:
            v[k] = 1 + (v[k] - 1) * factor
        v['brightness_gain'] = 1 + (v['brightness_gain'] - 1) * (1 + (factor - 1) * 0.66)
    return fn


def _describe(parameters, base):
    parts = []
    def delta(name, label, threshold=0.005, fmt='{:+.0%}'):
        change = getattr(parameters, name) - getattr(base, name)
        if abs(change) > threshold:
            parts.append(f'{label} {fmt.format(change)}')
    delta('brightness_gain', 'brightness')
    delta('contrast', 'contrast')
    delta('gamma', 'gamma')
    delta('saturation', 'saturation')
    if parameters.cooling - base.cooling > 0.005:
        parts.append('cooler')
    elif base.cooling - parameters.cooling > 0.005:
        parts.append('warmer')
    delta('black_point', 'shadow cutoff', 0.002)
    delta('highlight_ceiling', 'highlight ceiling')
    return ', '.join(parts) if parts else 'as planned'


def variations(base: GradeProposal):
    """Eight (name, tweak) pairs anchored on the brief's own proposal."""
    saturated = base.parameters.saturation >= 1
    colour_name, colour_fn = (('Muted colour', lambda v: v.update(saturation=v['saturation'] * 0.82))
                              if saturated else
                              ('Vivid colour', lambda v: v.update(saturation=v['saturation'] * 1.18)))
    return [
        ('Brief look', lambda v: None),
        ('Gentler', _toward_neutral(0.6)),
        ('Stronger', _away_from_neutral(1.3)),
        ('Deeper', lambda v: v.update(brightness_gain=v['brightness_gain'] * 0.92, gamma=v['gamma'] * 0.97)),
        ('Brighter', lambda v: v.update(brightness_gain=v['brightness_gain'] * 1.08, gamma=v['gamma'] * 1.03)),
        ('Cooler', lambda v: v.update(cooling=v['cooling'] + 0.06)),
        ('Warmer', lambda v: v.update(cooling=v['cooling'] - 0.06)),
        (colour_name, colour_fn),
    ]


def _apply_tweak(proposal: GradeProposal, fn):
    result = proposal.model_copy(deep=True)
    result.parameters = _vary(result.parameters, fn)
    if result.local is not None:
        result.local.background = _vary(result.local.background, fn)
        foreground = _vary(result.local.foreground, fn)
        if result.local.preserve_subject_color:
            # Colour-protected subjects keep neutral saturation/cooling by contract.
            foreground.saturation = result.local.foreground.saturation
            foreground.cooling = result.local.foreground.cooling
        result.local.foreground = foreground
    return result


def _key(proposal: GradeProposal):
    layers = [proposal.parameters] + ([proposal.local.foreground, proposal.local.background] if proposal.local else [])
    return tuple(round(getattr(p, k), 4) for p in layers for k in FIELDS)


def _distinct(proposal: GradeProposal, seen):
    """Nudge brightness/gamma until the look differs from earlier looks."""
    for _ in range(6):
        if _key(proposal) not in seen:
            return proposal
        for layer in [proposal.parameters] + ([proposal.local.background] if proposal.local else []):
            layer.brightness_gain = _clamped({**layer.model_dump(), 'brightness_gain': layer.brightness_gain + 0.02}).brightness_gain
            layer.gamma = _clamped({**layer.model_dump(), 'gamma': layer.gamma + 0.005}).gamma
        proposal = GradeProposal.model_validate(proposal.model_dump())
    return proposal


def render_frame(rgb, proposal: GradeProposal, alpha=None):
    """Grade one RGB frame (HxWx3 uint8) with the renderer's own pipeline."""
    pixels = rgb.astype(np.float32) / 255
    if proposal.local is not None and alpha is not None:
        soft = cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), max(0.5, proposal.local.feather_px))
        soft = np.clip(soft, 0, 1)[..., None]
        foreground = color_processing.apply_rgb(pixels, proposal.local.foreground, proposal.local.preserve_subject_color)
        background = color_processing.apply_rgb(pixels, proposal.local.background)
        result = background * (1 - soft) + foreground * soft
    else:
        result = color_processing.apply_rgb(pixels, proposal.parameters)
    return (np.clip(result, 0, 1) * 255).astype(np.uint8)


def build(base: GradeProposal, frame_rgb, *, brief_sha256, frame_index, frame_time, project_id, alpha=None):
    """Return eight validated LookPreview-ready dicts with graded frames."""
    looks = []
    seen = set()
    for name, fn in variations(base):
        proposal = _distinct(_apply_tweak(base, fn), seen)
        seen.add(_key(proposal))
        proposal = GradeProposal.model_validate(proposal.model_dump())
        look_id = str(uuid4())
        summary = _describe(proposal.parameters, base.parameters)
        if summary == 'as planned' and proposal.local is not None and base.local is not None:
            summary = _describe(proposal.local.background, base.local.background)
        description = ('As planned from your Creative Brief.' if name == 'Brief look'
                       else 'Brief look with ' + summary + '.')
        if proposal.local is not None and proposal.local.regions:
            # The still preview has only the subject mask; region treatments appear in the full render.
            description += ' Regional treatments (' + ', '.join(r.concept for r in proposal.local.regions) + ') are not previewed.'
        image = render_frame(frame_rgb, proposal, alpha)
        looks.append({
            'id': look_id, 'name': name, 'description': description[:300],
            'brief_sha256': brief_sha256, 'frame_index': int(frame_index), 'frame_time': float(frame_time),
            'image_url': f'/api/projects/{project_id}/looks/{look_id}/image',
            'uses_local': proposal.local is not None and alpha is not None,
            'proposal': proposal, 'image': image,
        })
    return looks
