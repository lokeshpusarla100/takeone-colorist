"""Bounded execution preflight; changes input levels, never creative controls or limits."""
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import numpy as np

from . import evidence,rendering,sam
from .errors import Problem
from .render_preflight import prepare


def source_pixels(source,recipe,original,settings,cancel,cache_dir):
    selection=recipe.evidence_selection
    if not selection:
        selection=evidence.plan(source,original,settings,cancel)
    key=evidence.digest({'source':original.sha256,'selection':selection['selected_indices'],
        'geometry':selection['manifest'],'width':256,'version':1})
    path=Path(cache_dir)/'grade-source-samples.npz';receipt=path.with_suffix('.json')
    if path.is_file() and receipt.is_file():
        try:
            saved=json.loads(receipt.read_text())
            if saved['key']==key and saved['sha256']==sam.checksum(path):
                with np.load(path,allow_pickle=False) as arrays:
                    pixels={i:arrays['p'+str(i)] for i in selection['selected_indices']}
                if sum(p.nbytes for p in pixels.values())<=settings.sample_memory_bytes and all(
                    p.dtype==np.uint8 and p.ndim==3 and p.shape[2]==3 and max(p.shape[:2])<=256 for p in pixels.values()):
                    return selection,pixels,True
        except (KeyError,ValueError,OSError):pass
    pixels=evidence.extract(source,selection['manifest'],selection['selected_indices'],settings,cancel,width=256)
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_name('.'+str(uuid4())+'.npz')
    try:
        with temporary.open('wb') as stream:
            np.savez_compressed(stream,**{'p'+str(i):p for i,p in pixels.items()});stream.flush();os.fsync(stream.fileno())
        temporary.replace(path);sam.atomic(receipt,{'key':key,'sha256':sam.checksum(path)})
    finally:temporary.unlink(missing_ok=True)
    return selection,pixels,False


def prepare_execution(source,output,recipe,original,settings,cancel,cache_dir=None,*,force_neutral_levels=False):
    """Return a new effective recipe/report; caller owns candidate lineage/persistence."""
    started=time.monotonic();limits=recipe.proposal.execution_limits
    if recipe.tone_operator!='luminance_ocio_v3' or (
        limits.max_new_black_fraction is None and limits.max_new_white_fraction is None):
        return recipe,{'status':'not_applicable','reason':'Only bounded v3 clipping with declared limits is preflighted; other execution checks remain authoritative.'}
    source=Path(source);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    rendering.verify_source(source,original,settings,cancel)
    selection,pixels,hit=source_pixels(source,recipe,original,settings,cancel,cache_dir or output)
    decision=prepare(recipe,pixels);proposal=decision['proposal']
    if recipe.proposal.mode=='localized':
        # Mathematical layer-envelope preflight only, before any SAM request
        # or full encode. There is no encoded localized sparse probe, so a
        # pass here never replaces matte/output QC.
        report={'version':'grade-guard-v1','math':decision['report'],'encoded_checks':[],
                'source_sample_cache_hit':hit,'elapsed_seconds':time.monotonic()-started,'limits':limits.model_dump(),
                'uncertainty':['Localized preflight is a per-layer mathematical envelope on bounded samples, not an encoded or masked certification. Final dense QC remains authoritative.']}
        if proposal is None:
            report['status']='blocked';sam.atomic(output/'preflight.json',report)
            raise Problem('GRADE_PREFLIGHT_FAILED','The planned localized grade cannot meet its clipping limits without changing creative controls.',
                          'Revise the grade to preserve more shadow/highlight detail. No SAM request, full render or critic call was made.',422,preflight=report)
        effective=recipe.model_copy(deep=True);effective.proposal=proposal;effective.evidence_selection=selection
        effective.source_samples=[{**evidence.measure(p),'frame_index':i,'requested_time':selection['manifest']['times'][i]} for i,p in pixels.items()]
        changed={}
        for layer,before_params,after_params in [
            ('parameters',recipe.proposal.parameters,proposal.parameters),
            ('foreground',recipe.proposal.local.foreground,proposal.local.foreground),
            ('background',recipe.proposal.local.background,proposal.local.background)]:
            for field in ['black_point','white_point']:
                if getattr(before_params,field)!=getattr(after_params,field):
                    changed[f'{layer}.{field}']={'from':getattr(before_params,field),'to':getattr(after_params,field)}
        report['status']='corrected' if changed else decision['report']['status'];report['changes']=changed
        effective.execution_preflight=report
        if changed:
            effective.execution_adjustment={'kind':'input_level_clipping_recovery','changes':changed,
                'source_recipe_sha256':rendering.fingerprint(recipe.model_dump(mode='json')),
                'reason':'Localized input level cutoffs were reduced per layer to satisfy the existing clipping bounds. Creative tone, palette and limits are unchanged.'}
        sam.atomic(output/'preflight.json',report)
        return effective,report
    base=recipe.model_copy(deep=True);base.evidence_selection=selection
    source_records=[]
    for i,p in pixels.items():
        source_records.append({**evidence.measure(p),'frame_index':i,'requested_time':selection['manifest']['times'][i]})
    base.source_samples=source_records
    # A zero-cut/full-white fallback affects only input levels; never brighten via
    # gamma/gain, invent masks, relax limits, or change the user's palette.
    neutral=base.proposal.model_copy(deep=True)
    neutral.parameters.black_point=0.;neutral.parameters.white_point=1.
    options=[neutral if force_neutral_levels else proposal or neutral]
    if options[0].parameters!=neutral.parameters:options.append(neutral)
    checks=[];effective=None
    for number,option in enumerate(options):
        if cancel.is_set():raise Problem('JOB_CANCELLED','Grade preflight cancelled.','Saved candidates are preserved.',409)
        probe_recipe=base.model_copy(deep=True);probe_recipe.proposal=option
        records=rendering.probe_grade_samples(source,Path(cache_dir or output)/'grade-probe-cache',probe_recipe,original,settings,cancel,selection)
        measured=evidence.compare(source_records,records,option.intent.model_dump())
        violations=[p for p in measured['paired_measurements'] if
            (limits.max_new_black_fraction is not None and p['new_black_fraction']>limits.max_new_black_fraction) or
            (limits.max_new_white_fraction is not None and p['new_white_fraction']>limits.max_new_white_fraction)]
        checks.append({'parameters':option.parameters.model_dump(),'measurements':measured,'violations':violations})
        if not violations:effective=probe_recipe;break
    report={'version':'grade-guard-v1','math':decision['report'],'encoded_checks':checks,'source_sample_cache_hit':hit,
            'elapsed_seconds':time.monotonic()-started,'limits':limits.model_dump(),
            'uncertainty':['Sparse source-resolution encoder checks cannot replace full render QC or creative review.']}
    if effective is None:
        report['status']='blocked';sam.atomic(output/'preflight.json',report)
        raise Problem('GRADE_PREFLIGHT_FAILED','The planned grade cannot meet its clipping limits without changing creative controls.',
                      'Revise the grade to preserve more shadow/highlight detail. No full render or critic call was made.',422,preflight=report)
    changed={k:{'from':getattr(recipe.proposal.parameters,k),'to':getattr(effective.proposal.parameters,k)}
             for k in ['black_point','white_point'] if getattr(recipe.proposal.parameters,k)!=getattr(effective.proposal.parameters,k)}
    report['status']='corrected' if changed else 'passed';report['changes']=changed
    effective.execution_preflight=report
    if changed:
        effective.execution_adjustment={'kind':'input_level_clipping_recovery','changes':changed,
            'source_recipe_sha256':rendering.fingerprint(recipe.model_dump(mode='json')),
            'reason':'Input level cutoffs were reduced to satisfy the existing clipping bounds. Creative tone, palette and limits are unchanged.'}
    sam.atomic(output/'preflight.json',report)
    return effective,report
