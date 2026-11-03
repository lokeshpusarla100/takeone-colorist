"""Decode every SAM mask payload once, on all cores, with the parser's own strict decoder.

meta-sam-parser 0.1.0 (the latest release) is pure Python. While parsing it fully decodes every mask
only to validate it (~0.15 s per mask, ~74 s for 507 masks) and discards the raster; persisting the
masks then decoded every payload again. Masks are independent, so before parsing we pre-decode all
payloads in parallel worker processes with the same strict ``decode_mask_to_raster`` and keep the
results. The parser's inline validation and our persistence then read those results:

* a valid mask returns the identical raster bytes;
* an invalid mask re-raises InvalidSegmentationMaskError with the identical message, so the parser
  records the identical diagnostic and skips it exactly as before;
* a payload the pre-scan did not see is simply decoded inline as before (correct, just slower).

Only installed for the pinned parser version whose internals this relies on.
"""
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
import json
import multiprocessing
import os
import threading

import meta_sam_parser._segmentation as _segmentation
from meta_sam_parser._errors import InvalidSegmentationMaskError
from meta_sam_parser._mask_codec import decode_mask_to_raster as strict_decode
from meta_sam_parser._types import SegmentationMask

SUPPORTED_PARSER = '0.1.0'
MIN_PARALLEL = 16  # below this, worker start-up costs more than it saves
KEEP_RUNS = 8      # results of this many most recent warm() calls are kept (concurrent SAM runs)
_results = {}      # key -> (kind, value, run number)
_runs = 0
_lock = threading.Lock()
_pool = None


def _key(mask):
    return (mask.encoding, mask.payload, mask.width, mask.height)


def masks_in(text):
    """SegmentationMask values in SAM output text, using the parser's own token/data grammar."""
    found = {}
    for match in _segmentation._TOKEN_PATTERN.finditer(text):
        if match.group(1) != 'mask' or match.group(2) is None:
            continue
        fields = dict(part.split('=', 1) for part in match.group(2).split(';') if '=' in part)
        data = _segmentation._MASK_DATA_PATTERN.fullmatch(fields.get('data') or '')
        if data is None:
            continue
        payload = data.group(3)
        mask = SegmentationMask(encoding='lossless' if payload.startswith('~') else 'one_bit', payload=payload,
                                width=int(data.group(2)), height=int(data.group(1)))
        found[_key(mask)] = mask
    return list(found.values())


def _decode_one(mask):
    try:
        return _key(mask), 'ok', strict_decode(mask)
    except InvalidSegmentationMaskError as error:
        return _key(mask), 'invalid', str(error)
    except Exception:  # anything else: leave it to the inline path, which behaves as before
        return _key(mask), 'skip', None


def _workers():
    global _pool
    if _pool is None:
        context = multiprocessing.get_context('forkserver')
        # Workers need only this module; never re-import the host's __main__ (server, test runner, script).
        context.set_forkserver_preload(['takeone.mask_decode'])
        _pool = ProcessPoolExecutor(max_workers=max(1, min(12, (os.cpu_count() or 2) - 1)), mp_context=context)
    return _pool


def _discard_pool():
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


def warm(text):
    """Pre-decode every mask payload in `text` in parallel; replaces results from any earlier run."""
    results = {}
    try:
        masks = [m for m in masks_in(text) if m.width > 0 and m.height > 0]
        if len(masks) >= MIN_PARALLEL:
            chunk = max(1, len(masks) // (4 * (os.cpu_count() or 2)))
            for key, kind, value in _workers().map(_decode_one, masks, chunksize=chunk):
                if kind != 'skip':
                    results[key] = (kind, value)
    except Exception:
        # Purely an accelerator: on any failure fall back to the parser's inline decode (the old path).
        results = {}
        _discard_pool()
    global _runs
    with _lock:
        # Never clear another run's results: SAM requests for several areas parse concurrently.
        _runs += 1
        for key, (kind, value) in results.items():
            _results[key] = (kind, value, _runs)
        for key in [k for k, (_, _, run) in _results.items() if run <= _runs - KEEP_RUNS]:
            del _results[key]
    return len(results)


def warm_from_events(events_path):
    """Pre-decode from a saved SAM event stream (the text the parser will read)."""
    parts = []
    with open(events_path) as stream:
        for row in stream:
            data = json.loads(row)
            if data.get('type') == 'response.output_text.delta':
                parts.append(data.get('delta', ''))
    return warm(''.join(parts))


def decode(mask):
    """Drop-in for decode_mask_to_raster: identical result or identical error, decoded at most once."""
    with _lock:
        hit = _results.get(_key(mask))
    if hit is None:
        return strict_decode(mask)
    kind, value, _ = hit
    if kind == 'invalid':
        raise InvalidSegmentationMaskError(value)
    return value


def take(mask):
    """decode(), then forget the result (persistence is the last reader of each mask)."""
    raster = decode(mask)
    with _lock:
        _results.pop(_key(mask), None)
    return raster


def install():
    """Route the parser's inline validation through decode(); only for the version this was verified on."""
    if version('meta-sam-parser') == SUPPORTED_PARSER:
        _segmentation.decode_mask_to_raster = decode
        return True
    return False
