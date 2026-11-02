#!/usr/bin/env python3
"""Offline render-recovery checks. Real renders are explicit and isolated.

Default: inspect a saved clipping failure, without decoding or running a model.
--quick: focused synthetic tests. --real: one optimized replay. --real --compare:
reference and optimized QC on the same effective recipe, with decoded-pixel hashes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def unpack(value):
    """The UI export preserves nested diagnostics as JSON strings."""
    if isinstance(value, str):
        try:return json.loads(value)
        except ValueError:return value
    return value


def saved_failure(workspace: Path, project: str | None, candidate: str | None):
    if project:
        directory = workspace / str(UUID(project))
    else:
        failures = []
        for path in workspace.glob('*/state.json'):
            try:
                state = json.loads(path.read_text())
                if (state.get('error') or {}).get('code') == 'GRADE_EXECUTION_LIMIT':
                    failures.append((path.stat().st_mtime, path.parent))
            except (OSError, ValueError):continue
        if not failures:
            raise ValueError('No saved clipping failure found. Supply --project PROJECT_UUID, or use --quick.')
        directory = max(failures)[1]
    state = json.loads((directory / 'state.json').read_text())
    if candidate:
        target = next((c for c in state['candidates'] if c['id'] == str(UUID(candidate))), None)
    else:
        target = next((c for c in reversed(state.get('candidates', [])) if c.get('recipe')), None)
    if target is None:raise ValueError('No matching candidate with a saved recipe.')
    return directory, state, target


def inspect_failure(directory, state, candidate):
    details = (state.get('error') or {}).get('details', {})
    recipe = candidate['recipe']
    source = directory / 'source.bin'
    measured = {k:unpack(v) for k,v in details.items()}
    markers=[e for e in state.get('events',[]) if e.get('details',{}).get('progress_timing')]
    intervals=[]
    for a,b in zip(markers,markers[1:]):
        if a['details'].get('progress_active'):
            intervals.append({'stage':a['details']['progress_stage'],
                'seconds':(datetime.fromisoformat(b['timestamp'])-datetime.fromisoformat(a['timestamp'])).total_seconds()})
    return {'mode':'read_only_inspection', 'new_meta_api_calls':0,
            'project_id':state['id'], 'candidate_id':candidate['id'], 'status':state['status'],
            'source_exists':source.is_file(),
            'source_identity_matches':source.is_file() and checksum(source) == state['source']['sha256'],
            'recorded_stage_intervals':intervals,
            'mode_selected':recipe['proposal']['mode'], 'saved_parameters':recipe['proposal']['parameters'],
            'declared_limits':recipe['proposal'].get('execution_limits', {}),
            'failure':{'code':(state.get('error') or {}).get('code'), 'details':measured},
            'next':'Use --quick for synthetic checks; --real renders locally; --real --compare renders both QC modes.'}


def smoke(max_seconds: int) -> int:
    tests = [
        'tests/test_render_preflight.py', 'tests/test_grade_guard_service.py', 'tests/test_render_performance.py', 'tests/test_render_timing.py',
        'tests/test_local_grading.py::test_global_only_does_not_call_sam',
        'tests/test_local_grading.py::test_localized_pixels_differ_and_subject_is_preserved_with_cache_reuse',
    ]
    missing = [p.split('::')[0] for p in tests if not (ROOT / p.split('::')[0]).is_file()]
    if missing:raise ValueError('Recovery test files are missing: ' + ', '.join(missing))
    process = subprocess.Popen([sys.executable, '-m', 'pytest', '-q', *tests], cwd=ROOT, start_new_session=True)
    try:return process.wait(timeout=max_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(process.pid, signal.SIGKILL);process.wait()
        print('Focused checks exceeded their wall-time budget.', file=sys.stderr)
        return 124


def decoded_hashes(path, settings, cancel):
    from takeone.media import command, probe
    metadata=probe(path,settings,cancel)
    time_base=next(s['time_base'] for s in metadata['streams'] if s['codec_type']=='video')
    raw = command([settings.ffmpeg, '-v', 'error', '-xerror', '-threads', '2', '-i', str(path),
                   '-map', '0:v:0', '-an', '-c:v', 'rawvideo', '-pix_fmt', 'yuv420p',
                   '-fps_mode', 'passthrough', '-enc_time_base:v', time_base, '-f', 'framemd5', '-'], cancel, 180)
    hashes = [line.split(',')[-1].strip() for line in raw.splitlines() if line and not line.startswith('#')]
    if not hashes:raise ValueError('Decoded frame hash output is empty.')
    return hashes


def audio_hash(path, settings, cancel, has_audio):
    if not has_audio:return None
    from takeone.media import command
    return command([settings.ffmpeg, '-v', 'error', '-xerror', '-threads', '2', '-i', str(path),
                    '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-f', 'hash', '-hash', 'sha256', '-'], cancel, 120).strip()


def prepare_recipe(recipe, source, source_path, output, settings, cancel):
    """Use the production guard; write a new artifact, never the saved recipe."""
    from takeone.grade_guard import prepare_execution
    if recipe.proposal.mode != 'global' or recipe.tone_operator != 'luminance_ocio_v3':
        raise ValueError('This isolated real-clip runner supports global v3 recovery only. Use the application for localized SAM provenance checks.')
    effective, report = prepare_execution(source_path, output / 'preflight', recipe.model_copy(deep=True),
                                          source, settings, cancel, cache_dir=output / 'source-cache')
    if effective.proposal.execution_limits != recipe.proposal.execution_limits:
        raise ValueError('A recovery changed the declared bounds; benchmark refused.')
    write_json(output / 'preflight.json', report)
    write_json(output / 'effective-recipe.json', effective.model_dump(mode='json'))
    return effective


def real_run(args, directory, state, candidate):
    from takeone import rendering
    from takeone.config import Settings
    from takeone.contracts import Source, GradeRecipe
    from takeone.errors import Problem
    output = (args.output or ROOT / 'outputs' / 'render-recovery' /
              (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(uuid4())[:8])).resolve()
    if output == directory.resolve() or directory.resolve() in output.parents:
        raise ValueError('Output must be outside the original project directory.')
    if output.exists() and any(output.iterdir()):raise ValueError('Output directory is not empty; no existing artifacts were overwritten.')
    output.mkdir(parents=True, exist_ok=True)
    source_path = (directory / 'source.bin').resolve()
    state_path = directory / 'state.json'
    before = {'source':checksum(source_path), 'state':checksum(state_path)}
    source = Source.model_validate(state['source'])
    recipe = GradeRecipe.model_validate(candidate['recipe'])
    if before['source'] != source.sha256 or recipe.source_sha256 != source.sha256:
        raise ValueError('Source identity does not match the saved recipe.')
    settings = Settings(workspace=output / 'unused-workspace') # No Service/provider/key loading.
    cancel = threading.Event();timer = threading.Timer(args.max_seconds, cancel.set);timer.daemon=True;timer.start()
    report = {'new_meta_api_calls':0, 'source_sha256':source.sha256, 'project_id':state['id'],
              'original_candidate_id':candidate['id'], 'comparison_requested':args.compare,
              'quality_policy':'Same effective recipe, source, encoder settings and output formats in both modes.',
              'wall_time_budget_seconds':args.max_seconds, 'runs':{}, 'output':str(output)}
    started = time.monotonic();exit_code=0
    try:
        effective = prepare_recipe(recipe, source, source_path, output, settings, cancel)
        modes = ['reference', 'optimized'] if args.compare else ['optimized']
        for mode in modes:
            target = output / mode;target.mkdir()
            start = time.monotonic()
            validation = rendering.render(source_path, target, effective.model_copy(deep=True), source,
                                          settings, cancel, qc_mode=mode)
            report['runs'][mode] = {'wall_seconds':time.monotonic()-start, 'validation':validation}
            write_json(output / (mode + '-validation.json'), report['runs'][mode])
        if args.compare:
            equality = {}
            for filename in ['grade.mp4', 'preview.webm']:
                left, right = output / 'reference' / filename, output / 'optimized' / filename
                a,b = decoded_hashes(left, settings, cancel),decoded_hashes(right, settings, cancel)
                equality[filename] = {'decoded_frame_count':len(a), 'decoded_frame_hashes_equal':a==b,
                                      'decoded_audio_equal':audio_hash(left,settings,cancel,bool(source.audio_codec)) == audio_hash(right,settings,cancel,bool(source.audio_codec))}
            report['decoded_equivalence'] = equality
            if not all(v['decoded_frame_hashes_equal'] and v['decoded_audio_equal'] for v in equality.values()):exit_code=1
            reference = report['runs']['reference']['wall_seconds'];optimized=report['runs']['optimized']['wall_seconds']
            report['single_pair_performance'] = {'reference_seconds':reference, 'optimized_seconds':optimized,
                'difference_seconds':reference-optimized, 'ratio':reference/optimized,
                'qualification':'One sequential pair, affected by caching, temperature and other machine load; not a statistical speedup claim.'}
        report['status'] = 'passed' if not exit_code else 'decoded_equivalence_failed'
    except Exception as error:
        report['status']='failed'
        report['error']=error.public() if isinstance(error,Problem) else {'type':type(error).__name__, 'message':str(error)}
        exit_code=1
    finally:
        timer.cancel();report['total_wall_seconds']=time.monotonic()-started
        report['original_source_unchanged']=checksum(source_path)==before['source']
        report['original_state_unchanged']=checksum(state_path)==before['state']
        if not report['original_source_unchanged'] or not report['original_state_unchanged']:
            exit_code=1;report['status']='original_identity_changed'
        write_json(output / 'report.json', report)
        print(json.dumps({'status':report['status'], 'report':str(output/'report.json'), 'new_meta_api_calls':0}, indent=2))
    return exit_code


def main():
    parser=argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--quick', action='store_true', help='Run focused synthetic tests, without Meta API calls.')
    mode.add_argument('--real', action='store_true', help='Explicitly run one isolated local render of saved real footage.')
    parser.add_argument('--compare', action='store_true', help='With --real: render reference and optimized modes and compare decoded frames/audio.')
    parser.add_argument('--workspace', type=Path, default=ROOT/'workspace')
    parser.add_argument('--project', help='Project UUID; default selects the latest saved clipping failure.')
    parser.add_argument('--candidate', help='Saved candidate UUID; default selects the latest recipe.')
    parser.add_argument('--output', type=Path, help='New/empty output directory outside the original project.')
    parser.add_argument('--max-seconds', type=int, default=900, help='Test or preflight/render wall-time budget with cancellation (60–3600 seconds; default 900).')
    args=parser.parse_args()
    if args.compare and not args.real:parser.error('--compare requires --real; it performs two full renders.')
    if not 60<=args.max_seconds<=3600:parser.error('--max-seconds must be 60–3600.')
    if args.quick:return smoke(args.max_seconds)
    directory,state,candidate=saved_failure(args.workspace.resolve(),args.project,args.candidate)
    if args.real:return real_run(args,directory,state,candidate)
    print(json.dumps(inspect_failure(directory,state,candidate),indent=2))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,OSError) as error:
        print(str(error),file=sys.stderr);raise SystemExit(2)
