#!/usr/bin/env python3
"""Probe: can ONE SAM request track every area of a localized grade, instead of one request per area?

Today each named area (subject, sky, water, ...) is its own request, and each request is billed for every
frame it tracks. SAM also accepts *object prompts*: a box and a point per object, each with an id of our own
choosing, many objects in one request, answered under the same ids. This probe asks whether that reproduces
what the per-area requests already produced.

It reuses a project's saved per-area mask runs as both the source of seeds (each object is seeded at the
first frame it was seen, with the same box + deepest-interior-point seed the repair path uses) and the
reference to compare against. It sends two requests on the FIRST --frames frames only:

  A  ideal seeds   every object seeded where it first appears        (tests the API and mask quality)
  B  window seeds  only objects visible in the first --window frames  (what a cheap detection step could give;
                   shows what late-appearing objects would cost)

Per area it reports how closely the single-pass masks match the saved ones (IoU, recall, precision) and what the
API reported it processed. Nothing is written to the project; output goes to --out (default: a temp folder).

Default: dry run, no API call (prints the plan and the most it can cost).
--yes makes 2 billed SAM requests of at most --frames frames each (150 frames = about $0.03 each).

    python scripts/sam_single_pass_probe.py <project-id> [--frames 150] [--window 50] [--yes]
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, PromptPoint, build_object_prompt, parse_responses_stream, video_segmentation_format  # noqa: E402

from takeone import local_grading, mask_decode  # noqa: E402
from takeone.config import Settings  # noqa: E402
from takeone.sam import SamProvider  # noqa: E402



@dataclass
class Source:
    sha256: str


def area_runs(state):
    """The latest ready, plain (non-repair, non-prompted) mask run per concept, in the order they were made."""
    chosen = {}
    for run in sorted(state['segmentations'], key=lambda r: r['created_at']):
        if run['status'] == 'ready' and not run.get('recovery_parent_id') and not run.get('reverse_tracking') and not run.get('object_prompt'):
            chosen[run['concept']] = run
    return list(chosen.values())


def load_reference(directory, runs, source, frames):
    """Per area: {frame: records} for every object, straight from the saved manifests."""
    out = []
    for run in runs:
        folder = directory / 'segmentations' / run['id']
        manifest = json.loads((folder / 'manifest.json').read_text())
        out.append((run, folder, manifest, local_grading.records_for_range(manifest, folder, source, 0, frames - 1, 'all_matches', require_complete=False)))
    return out


def _peaks(distance, count, separation):
    """Up to `count` well-separated maxima of a distance map (greedy: strongest first, then suppress its surroundings)."""
    distance = distance.copy()
    found = []
    for _ in range(count):
        y, x = np.unravel_index(int(distance.argmax()), distance.shape)
        if distance[y, x] < 3:  # nothing roomy enough left to place a point in
            break
        found.append((int(x), int(y)))
        cv2.circle(distance, (int(x), int(y)), separation, 0, -1)
    return found


def seed_for(record, width, height, rich=False):
    """Box + point(s) for one saved mask.

    Plain: the box and the deepest interior point (the seed shape the repair path already uses).
    Rich: up to 3 well-separated positive points inside the object, and up to 3 negative points in the parts of its
    box that are NOT the object (the protocol's way to say "not this"), so a box that also holds neighbours or
    floating plants does not pull them in.
    """
    binary, _ = local_grading.raster([record], width, height)
    ys, xs = np.where(binary > 0)
    box = PromptBox(int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    inside = cv2.distanceTransform(np.pad(binary, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    if not rich:
        y, x = np.unravel_index(int(inside.argmax()), inside.shape)
        return FramePrompt(frame=0, box=box, points=[PromptPoint(int(x), int(y))])
    separation = max(12, int(inside.max()))
    points = [PromptPoint(x, y) for x, y in _peaks(inside, 3, separation)]
    outside = np.zeros_like(binary)
    outside[box.top:box.bottom, box.left:box.right] = 1 - binary[box.top:box.bottom, box.left:box.right]
    outside_distance = cv2.distanceTransform(np.pad(outside, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    points += [PromptPoint(x, y, label='negative') for x, y in _peaks(outside_distance, 3, 20)]
    return FramePrompt(frame=0, box=box, points=points)


def plan_objects(reference, width, height, last_seed_frame, rich=False):
    """[(object id we assign, area index, saved object id, first frame, FramePrompt)], seeded where first seen."""
    planned = []
    for area, (run, _folder, _manifest, by_frame) in enumerate(reference, start=1):
        first = {}
        for frame in sorted(by_frame):
            for record in by_frame[frame]:
                first.setdefault(record['object_id'], (frame, record))
        for saved_id, (frame, record) in sorted(first.items(), key=lambda kv: int(kv[0])):
            if frame > last_seed_frame:
                continue
            prompt = seed_for(record, width, height, rich)
            planned.append((area * 100 + int(saved_id), area, saved_id, frame, FramePrompt(frame=frame, box=prompt.box, points=prompt.points)))
    return planned


def build_prompt(planned, width, height):
    return build_object_prompt(size=(width, height), objects=[PromptObject(id=oid, prompts=[prompt]) for oid, _a, _s, _f, prompt in planned])


def excerpt(settings, source_video, frames, out):
    target = out / 'excerpt.mp4'
    subprocess.run([settings.ffmpeg, '-v', 'error', '-y', '-i', str(source_video), '-an', '-vf', rf'select=between(n\,0\,{frames - 1}),setpts=PTS-STARTPTS',
                    '-frames:v', str(frames), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26', '-fps_mode', 'passthrough', '-movflags', '+faststart', str(target)],
                   check=True, timeout=120)
    return target


def request(provider, video, prompt_text, folder, remote):
    folder.mkdir(parents=True, exist_ok=True)
    usage, count, file_ids = {}, [0], []
    started = time.monotonic()
    result = provider.segment_prompted(video, 'single-pass-probe', remote, file_ids.append, threading.Event(), lambda snapshot: None,
                                       folder / 'raw.txt', lambda value: usage.update(value), lambda: count.__setitem__(0, count[0] + 1),
                                       object_prompt=prompt_text, propagation_direction='both')
    return result, usage, count[0], (file_ids[0] if file_ids else remote), time.monotonic() - started


def union_by_area(result, width, height):
    """{area: {frame: raster}} and the object ids the API answered with."""
    areas, returned = {}, set()
    for record in result.records:
        if record.kind != 'mask':
            continue
        returned.add(record.object_id)
        x, y, x2, y2 = (int(getattr(record.bounds, k)) for k in ('left', 'top', 'right', 'bottom'))
        raster = np.frombuffer(mask_decode.take(record.mask), np.uint8).reshape(record.mask.height, record.mask.width)
        canvas = areas.setdefault(int(record.object_id) // 100, {}).setdefault(record.frame.frame_index, np.zeros((height, width), np.uint8))
        canvas[y:y2, x:x2] |= (cv2.resize(raster, (x2 - x, y2 - y), interpolation=cv2.INTER_NEAREST) > 0).astype(np.uint8)
    return areas, returned


def compare(reference, areas, frames, width, height):
    rows = []
    for area, (run, _folder, _manifest, by_frame) in enumerate(reference, start=1):
        ious, recalls, precisions, missing, extra = [], [], [], 0, 0
        for frame in range(frames):
            ref = local_grading.raster(by_frame[frame], width, height)[0].astype(bool) if frame in by_frame else np.zeros((height, width), bool)
            new = areas.get(area, {}).get(frame)
            new = new.astype(bool) if new is not None else np.zeros((height, width), bool)
            both, either = np.logical_and(ref, new).sum(), np.logical_or(ref, new).sum()
            if either:
                ious.append(both / either)
            if ref.any():
                recalls.append(both / ref.sum())
                missing += int(not new.any())
            if new.any():
                precisions.append(both / new.sum())
                extra += int(not ref.any())
        rows.append({'area': run['concept'], 'frames_compared': len(ious), 'mean_iou': float(np.mean(ious)) if ious else None, 'min_iou': float(np.min(ious)) if ious else None,
                     'p05_iou': float(np.percentile(ious, 5)) if ious else None, 'mean_recall': float(np.mean(recalls)) if recalls else None,
                     'mean_precision': float(np.mean(precisions)) if precisions else None, 'frames_missing_vs_saved': missing, 'frames_extra_vs_saved': extra})
    return rows


def reparse(folder):
    """Parse a saved response again (no API call): the result plus how long the local decode and parse took."""
    events = folder / 'events.ndjson'
    started = time.monotonic()
    mask_decode.warm_from_events(events)
    decoded = time.monotonic() - started
    async def go():
        async def replay():
            with events.open() as stored:
                for row in stored:
                    yield json.loads(row)
        return await parse_responses_stream(replay(), video_segmentation_format()).final_result()
    result = asyncio.run(go())
    return result, decoded, time.monotonic() - started


def layered(areas, order):
    """Each pixel belongs to one area only: the one listed first in `order` that claims it (smallest areas first)."""
    out, taken = {}, {}
    for area in order:
        for frame, raster in areas.get(area, {}).items():
            free = raster.astype(bool) & ~taken.setdefault(frame, np.zeros(raster.shape, bool))
            out.setdefault(area, {})[frame] = free.astype(np.uint8)
            taken[frame] |= free
    return out


def reanalyze(directory, runs, source, frames, saved):
    reference = load_reference(directory, runs, source, frames)
    proxy = reference[0][2]['frame_map']['proxy']
    width, height = proxy['width'], proxy['height']
    for name in sorted(p.name for p in saved.iterdir() if (p / 'events.ndjson').is_file()):
        folder = saved / name
        result, decoded, parsed = reparse(folder)
        areas, _ = union_by_area(result, width, height)
        size = {a: np.mean([r.sum() for r in frames_.values()]) for a, frames_ in areas.items()}
        order = sorted(areas, key=lambda a: size[a])
        print(f'\n=== {name}: local decode {decoded:.1f}s, decode+parse {parsed:.1f}s, {sum(1 for r in result.records if r.kind == "mask")} masks ===')
        print('mean mask size (pixels):', {runs[a - 1]['concept']: int(size[a]) for a in order}, '-> priority order:', [runs[a - 1]['concept'] for a in order])
        for label, current in (('raw', areas), ('smallest area wins overlaps', layered(areas, order))):
            print(f'-- {label}')
            print(f'{"area":<16}{"mean IoU":>9}{"p05 IoU":>9}{"min IoU":>9}{"recall":>8}{"precision":>10}')
            f = lambda v: '   -   ' if v is None else f'{v:7.3f}'  # noqa: E731
            for r in compare(reference, current, frames, width, height):
                print(f'{r["area"]:<16}{f(r["mean_iou"]):>9}{f(r["p05_iou"]):>9}{f(r["min_iou"]):>9}{f(r["mean_recall"]):>8}{f(r["mean_precision"]):>10}')


def show(title, planned, usage, count, seconds, returned, rows, price):
    seeded = {oid for oid, *_ in planned}
    answered = {int(i) for i in returned}
    frames = usage.get('video_frames_processed')
    print(f'\n=== {title} ===')
    print(f'objects seeded {len(seeded)}, answered {len(answered)}, seeded-but-silent {sorted(seeded - answered)}, unexpected {sorted(answered - seeded)}')
    billed = None if frames is None else max(frames, 50) * price / 1000
    print(f'requests {count} · {seconds:.0f}s · video_frames_processed={frames} · estimated cost ${billed:.4f}' if billed is not None else f'requests {count} · {seconds:.0f}s · usage not reported: {usage}')
    print(f'{"area":<16}{"mean IoU":>9}{"p05 IoU":>9}{"min IoU":>9}{"recall":>8}{"precision":>10}{"frames lost":>12}{"frames extra":>13}')
    f = lambda v: '   -   ' if v is None else f'{v:7.3f}'  # noqa: E731
    for r in rows:
        print(f'{r["area"]:<16}{f(r["mean_iou"]):>9}{f(r["p05_iou"]):>9}{f(r["min_iou"]):>9}{f(r["mean_recall"]):>8}{f(r["mean_precision"]):>10}{r["frames_missing_vs_saved"]:>12}{r["frames_extra_vs_saved"]:>13}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project')
    parser.add_argument('--frames', type=int, default=150, help='frames per request (the whole clip is allowed; the budget below is the guard)')
    parser.add_argument('--window', type=int, default=50, help='variants B and C seed only objects first seen in this many leading frames')
    parser.add_argument('--variants', default='A,B', help='which requests to make, from A (ideal seeds), B (window seeds), C (window seeds, richer points)')
    parser.add_argument('--budget-usd', type=float, default=0.06, help='refuse to run if the requests could cost more than this')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--yes', action='store_true', help='make the billed SAM requests')
    parser.add_argument('--reanalyze', type=Path, help='no API call: re-read the responses saved in this folder by an earlier --yes run')
    args = parser.parse_args()
    if args.frames < 50:
        sys.exit('--frames must be at least 50 (the billing minimum).')
    settings = Settings.from_env()
    directory = settings.workspace / args.project
    state = json.loads((directory / 'state.json').read_text())
    source = Source(state['source']['sha256'])
    runs = area_runs(state)
    if len(runs) < 2:
        sys.exit('This probe needs a project with at least two saved per-area mask runs.')
    if args.reanalyze:
        return reanalyze(directory, runs, source, args.frames, args.reanalyze)
    reference = load_reference(directory, runs, source, args.frames)
    proxy = reference[0][2]['frame_map']['proxy']
    width, height = proxy['width'], proxy['height']
    full_input = directory / 'segmentations' / runs[0]['id'] / 'input.mp4'
    if not full_input.is_file():
        sys.exit(f'Expected the saved proxy excerpt at {full_input}.')
    every = {'A': ('A  ideal seeds', lambda: plan_objects(reference, width, height, args.frames - 1)),
             'B': (f'B  window seeds (first {args.window} frames)', lambda: plan_objects(reference, width, height, args.window - 1)),
             'C': (f'C  window seeds, richer points (first {args.window} frames)', lambda: plan_objects(reference, width, height, args.window - 1, rich=True))}
    chosen = [v.strip().upper() for v in args.variants.split(',') if v.strip()]
    if not chosen or any(v not in every for v in chosen):
        sys.exit('--variants must be a list from A, B, C.')
    plans = {every[v][0]: every[v][1]() for v in chosen}
    price = settings.price_sam_per_1k_frames
    ceiling = len(plans) * max(args.frames, settings.sam_min_billed_frames) * price / 1000
    if ceiling > args.budget_usd + 1e-9:
        sys.exit(f'{len(plans)} request(s) of {args.frames} frames could cost ${ceiling:.3f}, above --budget-usd {args.budget_usd}. Nothing was sent.')
    print(f'project {args.project}: {len(runs)} areas {[r["concept"] for r in runs]}, proxy {width}x{height}, first {args.frames} frames')
    for title, planned in plans.items():
        per_area = {}
        for _o, area, *_r in planned:
            per_area[runs[area - 1]['concept']] = per_area.get(runs[area - 1]['concept'], 0) + 1
        points = sum(len(p.points) for *_x, p in planned)
        print(f'  {title}: {len(planned)} objects {per_area}, {points} points; prompt {len(build_prompt(planned, width, height))} chars')
    print(f'today these areas cost {len(runs)} requests; this makes {len(plans)}, at most ${ceiling:.3f} in total at ${price}/1,000 frames (budget ${args.budget_usd}).')
    if not args.yes:
        print('\nDry run only. Re-run with --yes to make the billed request(s).')
        return
    out = args.out or Path(tempfile.mkdtemp(prefix='sam-single-pass-'))
    out.mkdir(parents=True, exist_ok=True)
    mask_decode.install()
    whole = args.frames >= reference[0][2]['frame_map']['frame_count']
    video = full_input if whole else excerpt(settings, full_input, args.frames, out)
    provider = SamProvider(settings)
    remote, report = None, {}
    for title, planned in plans.items():
        text = build_prompt(planned, width, height)
        folder = out / title.split()[0]
        result, usage, count, remote, seconds = request(provider, video, text, folder, remote)
        areas, returned = union_by_area(result, width, height)
        rows = compare(reference, areas, args.frames, width, height)
        show(title, planned, usage, count, seconds, returned, rows, price)
        report[title] = {'objects_seeded': len(planned), 'objects_answered': len(returned), 'usage': usage, 'seconds': seconds, 'areas': rows}
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(f'\nraw responses and report.json are in {out} (nothing was written to the project)')


if __name__ == '__main__':
    main()
