"""Focused equivalence checks for bounded render/QC optimizations."""
from dataclasses import replace
import json
import subprocess
import threading

import pytest
import numpy as np

from takeone import evidence,rendering,sam,timing
from takeone.contracts import GradeParameters,GradeProposal,GradeRecipe
from takeone.errors import Problem
from takeone.media import inspect_source,probe
from tests.test_backend import settings
from tests.test_local_grading import scene
from tests.test_render_timing import clips


def recipe_for(path,settings):
    cancel=threading.Event();source,metadata=inspect_source(path,path.name,sam.checksum(path),settings,cancel)
    selection=evidence.plan(path,source,replace(settings,sample_count=2,sample_scan_frames=8),cancel)
    samples,_=evidence.collect(path,source,settings,cancel,selection=selection)
    plan=GradeProposal(parameters=GradeParameters(contrast=1.05,gamma=1,saturation=1),summary='Performance fixture',
        decisions=[{'parameter':'contrast','reason':'Fixture','source_ids':['user_request']}],limitations=[])
    recipe=GradeRecipe(schema_version='takeone-grade-v3',prompt_version='colorist-v3',tone_operator='luminance_ocio_v3',
        source_sha256=source.sha256,brief_sha256='fixture',model='mock',proposal=plan,source_samples=samples,evidence_selection=selection)
    return source,metadata,recipe


@pytest.mark.parametrize('kind',['cfr','near_cfr','vfr'])
def test_combined_full_decode_preserves_ffprobe_pts_and_final_duration(clips,settings,kind):
    path=clips[kind];cancel=threading.Event();metadata=probe(path,settings,cancel)
    before=evidence.frame_manifest(path,settings,cancel)
    decoded=evidence.decoded_manifest(path,settings,cancel,metadata)
    assert decoded['pts']==before['pts'] and decoded['time_base']==before['time_base']
    assert decoded['durations'][-1]==before['durations'][-1]
    assert decoded['fully_decoded'] is True and decoded['audio_decoded'] is True


def test_combined_decode_does_not_accept_corrupt_media(scene,settings,tmp_path):
    metadata=probe(scene,settings,threading.Event());broken=tmp_path/'broken.mp4';broken.write_bytes(scene.read_bytes()[:100])
    with pytest.raises(Problem) as caught:evidence.decoded_manifest(broken,settings,threading.Event(),metadata)
    assert caught.value.code=='MEDIA_TOOL_FAILED'


@pytest.mark.parametrize('kind',['cfr','near_cfr','vfr'])
def test_combined_qc_samples_are_identical_to_separate_extraction(clips,settings,kind):
    path=clips[kind];cancel=threading.Event();metadata=probe(path,settings,cancel)
    reference=evidence.decoded_manifest(path,settings,cancel,metadata)
    selection={'manifest':reference,'selected_indices':[0,3,len(reference['pts'])-1]}
    before=evidence.extract(path,reference,selection['selected_indices'],settings,cancel)
    actual,frames=evidence.decoded_evidence(path,settings,cancel,metadata,selection)
    assert actual==reference
    for index in before:
        assert np.array_equal(frames[index],before[index])
        assert evidence.measure(frames[index])==evidence.measure(before[index])


def test_legacy_source_manifest_recovers_verified_ticks_and_reuses_cache(clips,settings,tmp_path,monkeypatch):
    path=clips['near_cfr'];cancel=threading.Event();metadata=probe(path,settings,cancel)
    original=evidence.frame_manifest(path,settings,cancel)
    legacy={k:v for k,v in original.items() if k not in {'pts','time_base','nominal_rate','durations'}}
    # Reproduce six-decimal historical ffprobe timestamp storage.
    legacy['times']=[round(float(t),6) for t in legacy['times']]
    cache=tmp_path/'source-timeline.json'
    recovered=evidence.source_manifest(path,settings,cancel,legacy,metadata,sam.checksum(path),cache)
    assert recovered['pts']==original['pts'] and recovered['durations'][-1]==original['durations'][-1]
    monkeypatch.setattr(evidence,'command',lambda *_args,**_kw:(_ for _ in ()).throw(AssertionError('source timing decoded twice')))
    assert evidence.source_manifest(path,settings,cancel,legacy,metadata,sam.checksum(path),cache)['pts']==original['pts']


def test_optimized_render_keeps_quality_and_decode_outputs(scene,settings,tmp_path,monkeypatch):
    source,_,recipe=recipe_for(scene,settings);hashes=[]
    for mode in ['reference','optimized']:
        output=tmp_path/mode
        if mode=='optimized':
            monkeypatch.setattr(evidence,'extract',lambda *_args,**_kw:(_ for _ in ()).throw(AssertionError('master sampled in a second decode')))
        result=rendering.render(scene,output,recipe,source,settings,threading.Event(),qc_mode=mode)
        assert result['performance']['encode_seconds']>0 and result['performance']['total_seconds']>0
        assert result['fully_decoded'] and result['timing']['master']['max_normalized_error_ms']==0
        values={}
        for name in ['grade.mp4','preview.webm']:
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output/name),'-map','0:v:0','-an','-f','framemd5','-'])
            values[name]=[line for line in raw.splitlines() if not line.startswith(b'#')]
        hashes.append(values)
    assert hashes[0]==hashes[1]


def test_sparse_encoder_probe_preserves_indices_and_is_reused(scene,settings,tmp_path,monkeypatch):
    source,_,recipe=recipe_for(scene,settings);selection=recipe.evidence_selection
    selection['selected_indices']=[2,8]
    records=rendering.probe_grade_samples(scene,tmp_path/'probe',recipe,source,settings,threading.Event(),selection)
    assert [r['frame_index'] for r in records]==[2,8]
    assert all(r['preflight_sparse_temporal_context'] for r in records)
    monkeypatch.setattr(rendering,'command',lambda *_args,**_kw:(_ for _ in ()).throw(AssertionError('encoder probe repeated')))
    reworded=recipe.model_copy(deep=True);reworded.created_at='2026-11-03T00:00:00+00:00'
    reworded.proposal.summary='Different prose with identical executable controls'
    reworded.proposal.intent.description='Metadata changes do not alter these pixels.'
    assert rendering.probe_grade_samples(scene,tmp_path/'probe',reworded,source,settings,threading.Event(),selection)==records


def test_paired_evidence_decodes_each_pixels_once(scene,settings,monkeypatch):
    source,_,recipe=recipe_for(scene,settings);calls=[];original=evidence.extract
    def extract(*args,**kw):calls.append(str(args[0]));return original(*args,**kw)
    monkeypatch.setattr(evidence,'extract',extract)
    result=evidence.paired(scene,scene,source,settings,threading.Event(),recipe.evidence_selection)
    assert len(calls)==2
    assert result['source_records']==result['candidate_records']
