import numpy as np
import pytest

from takeone.contracts import GradeParameters,GradeProposal,GradeRecipe
from takeone.errors import Problem
from takeone.render_preflight import prepare


def recipe(**parameters):
    proposal=GradeProposal(parameters=GradeParameters(contrast=1.,saturation=1.,gamma=1.,**parameters),
        summary='Preserve the intended dark atmosphere.',limitations=[],
        decisions=[{'parameter':'black_point','reason':'Fixture input cutoff','source_ids':['user_request']}],
        intent={'shadow_policy':'intentional_darkness'},execution_limits={'max_new_black_fraction':.03,'max_new_white_fraction':.01})
    return GradeRecipe(source_sha256='source',brief_sha256='brief',model='mock',schema_version='takeone-grade-v3',
        prompt_version='colorist-v3',tone_operator='luminance_ocio_v3',proposal=proposal,source_samples=[])


def test_shadow_cutoff_is_minimally_reduced_without_changing_look_or_limits():
    plan=recipe(black_point=.04,brightness_gain=.94,cooling=-.08)
    original=plan.model_dump()
    frame=np.full((100,100,3),120,np.uint8);frame[:4]=9
    result=prepare(plan,{70:frame})
    adjusted=result['proposal'];report=result['report']
    assert report['status']=='corrected' and report['before']['predicted_passed'] is False
    assert 0<adjusted.parameters.black_point<.04
    assert report['after']['envelope_passed'] is True
    assert adjusted.parameters.brightness_gain==.94 and adjusted.parameters.cooling==-.08
    assert adjusted.execution_limits==plan.proposal.execution_limits and adjusted.intent==plan.proposal.intent
    assert plan.model_dump()==original
    assert 'does not lift' in adjusted.decisions[0].reason


def test_highlight_endpoint_is_restored_without_unrelated_tone_changes():
    plan=recipe(white_point=.9)
    frame=np.full((100,100,3),100,np.uint8);frame[:4]=250
    result=prepare(plan,{3:frame})
    assert result['report']['status']=='corrected'
    assert .9<result['proposal'].parameters.white_point<1
    assert result['proposal'].parameters.gamma==1 and result['proposal'].parameters.saturation==1


def test_dark_intent_without_declared_bound_has_no_invented_histogram_target():
    plan=recipe(black_point=.15)
    plan.proposal.execution_limits.max_new_black_fraction=None
    plan.proposal.execution_limits.max_new_white_fraction=None
    result=prepare(plan,{0:np.full((10,10,3),8,np.uint8)})
    assert result['report']['status']=='passed' and result['report']['changed'] is False
    assert result['proposal'].parameters.black_point==.15


def test_unrepairable_other_controls_do_not_silently_brighten_or_relax_bounds():
    plan=recipe(brightness_gain=.65)
    plan.proposal.parameters.gamma=.8
    plan.proposal.execution_limits.max_new_black_fraction=0
    original=plan.model_dump()
    result=prepare(plan,{0:np.full((10,10,3),2,np.uint8)})
    assert result['report']['status']=='blocked' and result['proposal'] is None
    assert plan.model_dump()==original


def test_neutral_near_black_quantization_uncertainty_requires_actual_encoder_check():
    plan=recipe();plan.proposal.execution_limits.max_new_black_fraction=0
    result=prepare(plan,{0:np.full((10,10,3),2,np.uint8)})
    assert result['report']['status']=='needs_encoded_check'
    assert result['report']['after']['predicted_passed'] is True
    assert result['report']['changed'] is False
    assert result['proposal'].parameters==plan.proposal.parameters


def test_legacy_recipe_is_not_reinterpreted():
    plan=recipe();plan.tone_operator='bounded_sigmoid_v2'
    result=prepare(plan,{})
    assert result['report']['status']=='not_applicable' and result['proposal'] is None


@pytest.mark.parametrize('pixels',[{}, {0:np.zeros((257,1,3),np.uint8)}, {0:np.zeros((10,10,3),np.float32)}])
def test_incomplete_or_unbounded_evidence_is_not_silently_accepted(pixels):
    with pytest.raises(Problem) as caught:prepare(recipe(),pixels)
    assert caught.value.code=='PREFLIGHT_INPUT_INVALID'
