"""Single-pass SAM tracking: one request for every area of a localized grade.

A localized grade names several areas (the subject, sky, water, ...). Today each area is its own SAM request and each
request is billed for every frame it tracks, so five areas cost five times the frames. SAM also accepts *object
prompts*: a box and points for each object, each under an id of our choosing, many objects per request, answered
under the same ids (scripts/sam_single_pass_probe.py measured this on a real clip: 16 objects in one request, all
answered under their ids, billed once for the frames).

The flow built from these helpers (service.py, only when enabled):

  1. A short detection window per area, with the ordinary concept request, gives each area's objects and masks.
  2. Those masks seed ONE request over the whole range: every object, at the frame it first appears.
  3. The tracked masks are compared with the detection masks on the window; an area that does not agree (or has
     gaps) is NOT used and goes back to its own ordinary request. Nothing is guessed.

This module is arithmetic and prompt text only: no I/O beyond reading the saved mask files, no SAM calls.
"""
from __future__ import annotations

import cv2
import numpy as np
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, PromptPoint, build_object_prompt

from . import local_grading

ID_SPACING = 1000  # area k's objects are numbered k * 1000 + the object's own id
MAX_POSITIVE, MAX_NEGATIVE = 3, 3


class NotApplicable(Exception):
    """Single-pass does not suit this plan; the ordinary one-request-per-area path is used instead."""


def prompt_id(area: int, object_id: str) -> int:
    value = int(object_id)
    if not 0 <= value < ID_SPACING:
        raise NotApplicable(f'object id {object_id} does not fit the id scheme')
    return (area + 1) * ID_SPACING + value


def area_of(prompt_object_id: str) -> tuple[int, str]:
    number = int(prompt_object_id)
    return number // ID_SPACING - 1, str(number % ID_SPACING)


def _peaks(distance: np.ndarray, count: int, separation: int) -> list[tuple[int, int]]:
    """Up to `count` well-separated maxima of a distance map: strongest first, then clear its surroundings."""
    distance = distance.copy()
    found = []
    for _ in range(count):
        y, x = np.unravel_index(int(distance.argmax()), distance.shape)
        if distance[y, x] < 3:  # nothing roomy enough left to place a point in
            break
        found.append((int(x), int(y)))
        cv2.circle(distance, (int(x), int(y)), separation, 0, -1)
    return found


def seed_for(binary: np.ndarray) -> tuple[PromptBox, list[PromptPoint]]:
    """A box around one object plus points that say which parts are it and which are not.

    Positive points sit well inside the object; negative points sit in the parts of its box that are not the object
    (neighbours, floating things), which is the protocol's way of excluding them. Measured on a real clip this
    brought an ambiguous region from IoU 0.46 to 0.71 against its concept masks and improved every other area.
    """
    ys, xs = np.where(binary > 0)
    box = PromptBox(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    inside = cv2.distanceTransform(np.pad(binary, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    points = [PromptPoint(x, y) for x, y in _peaks(inside, MAX_POSITIVE, max(12, int(inside.max())))]
    outside = np.zeros_like(binary)
    outside[box.top:box.bottom, box.left:box.right] = 1 - binary[box.top:box.bottom, box.left:box.right]
    outside_distance = cv2.distanceTransform(np.pad(outside, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    points += [PromptPoint(x, y, label='negative') for x, y in _peaks(outside_distance, MAX_NEGATIVE, 20)]
    return box, points


def area_objects(by_frame: dict[int, list[dict]], width: int, height: int, first: int, area: int) -> list[PromptObject]:
    """One prompt object per saved object of an area, seeded at the first frame it appears (relative to the request)."""
    seen: dict[str, tuple[int, dict]] = {}
    for frame in sorted(by_frame):
        for record in by_frame[frame]:
            seen.setdefault(record['object_id'], (frame, record))
    objects = []
    for object_id, (frame, record) in sorted(seen.items(), key=lambda item: int(item[0])):
        binary, _ = local_grading.raster([record], width, height)
        box, points = seed_for(binary)
        objects.append(PromptObject(id=prompt_id(area, object_id), prompts=[FramePrompt(frame=frame - first, box=box, points=points)]))
    return objects


def build_prompt(width: int, height: int, objects: list[PromptObject]) -> str:
    return build_object_prompt(size=(width, height), objects=objects)


def split_manifest(manifest: dict, area: int) -> dict:
    """The part of a single-pass manifest that belongs to one area, with the area's own object ids restored."""
    records = []
    for record in manifest['records']:
        owner, own_id = area_of(record['object_id'])
        if owner == area:
            records.append(dict(record, object_id=own_id))
    return {'frame_map': manifest['frame_map'], 'records': records}


def agreement(reference: dict[int, list[dict]], tracked: dict[int, list[dict]], width: int, height: int) -> dict:
    """How well the tracked masks reproduce the detection masks, frame by frame, on the frames the detection covers."""
    overlaps, lost = [], 0
    for frame, records in sorted(reference.items()):
        if frame not in tracked:
            lost += 1
            overlaps.append(0.0)
            continue
        a = local_grading.raster(records, width, height)[0].astype(bool)
        b = local_grading.raster(tracked[frame], width, height)[0].astype(bool)
        either = np.logical_or(a, b).sum()
        overlaps.append(float(np.logical_and(a, b).sum() / either) if either else 1.0)
    if not overlaps:
        return {'frames': 0, 'mean_iou': 0.0, 'poor_fraction': 1.0, 'lost': 0}
    return {'frames': len(overlaps), 'mean_iou': float(np.mean(overlaps)),
            'poor_fraction': float(np.mean([v < .5 for v in overlaps])), 'lost': lost}


def acceptable(result: dict, gate: float) -> bool:
    """Used only if the area is reproduced on average, no frame the detection saw is missing, and few frames are far off."""
    return result['frames'] > 0 and result['lost'] == 0 and result['mean_iou'] >= gate and result['poor_fraction'] <= .1
