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


def localized_recipe(**background):
    params={'contrast':1.,'saturation':1.,'gamma':1.,'brightness_gain':1.,'cooling':0.}
    local={'concept':'subject','selection':'single_instance','intervals':[{'start':0,'end':1}],
           'foreground':dict(params),'background':{**params,**background},'preserve_subject_color':True,'feather_px':1.}
    proposal=GradeProposal(mode='localized',parameters=GradeParameters(**params),local=local,
        summary='Fixture localized treatment',limitations=[],
        decisions=[{'parameter':'local_treatment','reason':'Fixture','source_ids':['user_request']}],
        execution_limits={'max_new_black_fraction':.03,'max_new_white_fraction':.01})
    return GradeRecipe(source_sha256='source',brief_sha256='brief',model='mock',schema_version='takeone-grade-v3',
        prompt_version='colorist-v3',tone_operator='luminance_ocio_v3',proposal=proposal,source_samples=[])


def test_localized_preflight_corrects_only_the_offending_layer_levels():
    plan=localized_recipe(black_point=.1)
    original=plan.model_dump()
    frame=np.full((100,100,3),120,np.uint8);frame[:4]=9
    result=prepare(plan,{70:frame})
    adjusted=result['proposal'];report=result['report']
    assert report['status']=='corrected' and set(report['layers'])=={'parameters','foreground','background'}
    assert 0<=adjusted.local.background.black_point<.1
    assert adjusted.local.foreground.black_point==0 and adjusted.parameters.black_point==0
    assert adjusted.local.background.brightness_gain==1 and adjusted.local.background.cooling==0
    assert adjusted.execution_limits==plan.proposal.execution_limits
    assert plan.model_dump()==original
    assert {c['layer'] for c in report['changes']}=={'background'}


def test_localized_preflight_blocks_an_unrepairable_layer_before_encoding():
    plan=localized_recipe(black_point=.05)
    plan.proposal.local.background.brightness_gain=.65
    plan.proposal.local.background.gamma=.8
    plan.proposal.execution_limits.max_new_black_fraction=0
    result=prepare(plan,{0:np.full((10,10,3),2,np.uint8)})
    assert result['report']['status']=='blocked' and result['proposal'] is None
    assert 'background' in result['report']['reason']


# ---- Highlight feasibility gate -------------------------------------------------------------

def hazy_sky_frame():
    """A pale sky over mid-tone ground: the footage class where lowering white_point brightens the sky."""
    frame=np.full((144,256,3),120,np.uint8)
    frame[:58]=np.linspace(205,235,256,dtype=np.uint8)[None,:,None]
    frame[58:]=np.linspace(95,150,256,dtype=np.uint8)[None,:,None]
    return frame


def highlight_recipe(policy='preserve_detail',limits=True,**controls):
    base=dict(contrast=1.,saturation=1.,gamma=1.)
    proposal=GradeProposal(parameters=GradeParameters(**{**base,**controls}),summary='Fixture darkening grade.',limitations=[],
        decisions=[{'parameter':'gamma','reason':'Fixture','source_ids':['user_request']}],
        intent={'shadow_policy':'intentional_darkness','highlight_policy':policy},
        execution_limits={'max_new_black_fraction':.03,'max_new_white_fraction':.01} if limits else {})
    return GradeRecipe(source_sha256='source',brief_sha256='brief',model='mock',schema_version='takeone-grade-v3',
        prompt_version='colorist-v3',tone_operator='luminance_ocio_v3',proposal=proposal,source_samples=[])


# The V3 background grade from the monsoon run: median down, pale sky up and cyan-white.
SUBDUE_THE_SKY=dict(contrast=1.25,gamma=.8,brightness_gain=.78,cooling=.15,black_point=.05,white_point=.88)


def test_darkening_layer_that_lifts_highlights_is_blocked_and_the_cause_is_measured():
    plan=highlight_recipe(**SUBDUE_THE_SKY);original=plan.model_dump()
    result=prepare(plan,{70:hazy_sky_frame()})
    report=result['report']
    assert result['proposal'] is None and report['status']=='blocked' and report['blocked_by']=='highlight_lift'
    layer=report['highlights']['layers']['parameters']
    assert layer['darkening'] and layer['mid_grey_change']<-.02 and layer['highlight_change']>.02 and layer['rule']=='white_point_cutoff'
    assert list(layer['drivers'])[0]=='white_point' # largest single cause comes first
    assert layer['drivers']['white_point']['lift_removed_if_neutral']>.05
    assert 'white_point' in report['reason'] and 'unfulfilled' in report['reason']
    assert plan.model_dump()==original # nothing was silently adjusted


def test_neutral_endpoint_and_gentler_tone_pass_the_gate():
    plan=highlight_recipe(contrast=1.,gamma=.85,brightness_gain=.85)
    result=prepare(plan,{70:hazy_sky_frame()})
    layer=result['report']['highlights']['layers']['parameters']
    assert result['proposal'] is not None and layer['darkening'] and layer['highlight_change']<=0
    assert result['report']['highlights']['violations']==[]


def test_only_the_lowered_white_point_is_removed_when_everything_else_is_kept():
    kept=dict(SUBDUE_THE_SKY,white_point=1.,black_point=0.,contrast=1.)
    assert prepare(highlight_recipe(**kept),{70:hazy_sky_frame()})['proposal'] is not None


def test_gate_runs_without_any_declared_clipping_bound():
    blocked=prepare(highlight_recipe(limits=False,**SUBDUE_THE_SKY),{70:hazy_sky_frame()})
    assert blocked['proposal'] is None and blocked['report']['blocked_by']=='highlight_lift'
    passed=prepare(highlight_recipe(limits=False,gamma=.85,brightness_gain=.85),{70:hazy_sky_frame()})
    assert passed['proposal'] is not None and passed['report']['status']=='passed'
    assert passed['proposal'].parameters==highlight_recipe(limits=False,gamma=.85,brightness_gain=.85).proposal.parameters # nothing adjusted


@pytest.mark.parametrize('policy',['intentional_lift','intentional_clipping'])
def test_declared_highlight_lift_is_respected(policy):
    result=prepare(highlight_recipe(policy=policy,**SUBDUE_THE_SKY),{70:hazy_sky_frame()})
    assert result['proposal'] is not None and result['report']['highlights']['enforced'] is False
    assert result['report']['highlights']['violations']==[] and result['report']['highlights']['layers']['parameters']['highlight_change']>.03


def test_undeclared_and_preserve_detail_policies_are_both_enforced():
    for policy in ['review','preserve_detail']:
        assert prepare(highlight_recipe(policy=policy,**SUBDUE_THE_SKY),{70:hazy_sky_frame()})['proposal'] is None


def test_gate_is_skipped_only_when_explicitly_disabled_for_neutral_recovery():
    plan=highlight_recipe(**SUBDUE_THE_SKY)
    assert prepare(plan,{70:hazy_sky_frame()},highlight_gate=False)['proposal'] is not None


def test_a_brightening_subject_layer_is_exempt_but_the_surroundings_are_checked():
    plan=localized_recipe(gamma=.85,brightness_gain=.85)
    plan.proposal.local.foreground=GradeParameters(contrast=1.25,saturation=1.,gamma=1.,brightness_gain=1.3)
    plan.proposal.execution_limits.max_new_white_fraction=None;plan.proposal.execution_limits.max_new_black_fraction=None
    ok=prepare(plan,{70:hazy_sky_frame()})
    layers=ok['report']['highlights']['layers']
    assert ok['proposal'] is not None and layers['foreground']['darkening'] is False and layers['foreground']['highlight_change']>.03
    plan.proposal.local.background=GradeParameters(saturation=1.,**SUBDUE_THE_SKY)
    blocked=prepare(plan,{70:hazy_sky_frame()})
    assert blocked['proposal'] is None and blocked['report']['highlights']['violations']==['background']


def test_new_channel_clipping_is_reported_but_does_not_block_by_itself():
    plan=highlight_recipe(cooling=.15)
    result=prepare(plan,{70:hazy_sky_frame()})
    layer=result['report']['highlights']['layers']['parameters']
    assert result['proposal'] is not None and layer['darkening'] is False
    assert layer['new_channel_clip_fraction']>.05 # luma-based white_fraction misses this cyan-white clip


def test_block_suggests_a_highlight_ceiling_that_actually_passes_without_applying_it():
    plan=highlight_recipe(**SUBDUE_THE_SKY)
    blocked=prepare(plan,{70:hazy_sky_frame()})
    suggestion=blocked['report']['highlights']['layers']['parameters']['suggested_highlight_ceiling']
    assert suggestion is not None and .6<=suggestion<1 and 'highlight_ceiling' in blocked['report']['reason']
    assert plan.proposal.parameters.highlight_ceiling==1 # suggestion only; creative controls are never changed silently
    repaired=highlight_recipe(**SUBDUE_THE_SKY,highlight_ceiling=suggestion)
    assert prepare(repaired,{70:hazy_sky_frame()})['proposal'] is not None


def test_a_darkening_plan_that_uses_the_ceiling_subdues_highlights_and_passes():
    plan=highlight_recipe(contrast=1.15,gamma=.9,brightness_gain=.85,cooling=.15,highlight_ceiling=.62)
    result=prepare(plan,{70:hazy_sky_frame()})
    layer=result['report']['highlights']['layers']['parameters']
    assert result['proposal'] is not None and layer['darkening'] and layer['highlight_change']<-.15
    assert layer['new_channel_clip_fraction']==0 # the cooled sky no longer clips to cyan-white


# ---- Generic scene classes: nothing above is specific to one video --------------------------

def _ramp(h,w,a,b,rng):return (np.linspace(a,b,w)[None,:]+rng.normal(0,4,(h,w))).clip(0,255)


def beach(rng):   # bright sky and sand fill most of the frame; p95 is already near white
    f=np.zeros((144,256,3));f[:50]=_ramp(50,256,215,240,rng)[...,None]*[.95,1,1.05]
    f[50:93]=_ramp(43,256,90,140,rng)[...,None]*[.7,1,1.1];f[93:]=_ramp(51,256,190,225,rng)[...,None]*[1.05,1,.85]
    return f.clip(0,255).astype(np.uint8)


def city_day(rng):
    f=np.zeros((144,256,3));f[:30]=_ramp(30,256,200,230,rng)[...,None];f[30:110]=_ramp(80,256,70,150,rng)[...,None]
    f[110:]=_ramp(34,256,25,60,rng)[...,None];f[40:100:12,10:250:16]=245
    return f.clip(0,255).astype(np.uint8)


def city_night(rng,lights=(245,245,240)):  # dark frame; bright lights cover only a few percent
    f=np.zeros((144,256,3));f[:]=_ramp(144,256,15,70,rng)[...,None]*[.9,.95,1.15]
    ys,xs=rng.integers(0,144,1200),rng.integers(0,256,1200);f[ys,xs]=lights
    return f.clip(0,255).astype(np.uint8)


SCENES={'beach':beach,'city_day':city_day,'city_night':city_night,'hazy_sky':lambda rng:hazy_sky_frame()}
WHITE_POINT_BUG=dict(contrast=1.2,gamma=.85,brightness_gain=.85,white_point=.9,black_point=.03)
BRIGHTEN=dict(contrast=1.05,gamma=1.12,brightness_gain=1.2,cooling=-.03)
PUNCHY=dict(contrast=1.2,gamma=.95,brightness_gain=.95)
SUBDUED=dict(contrast=1.15,gamma=.9,brightness_gain=.85,cooling=.1,highlight_ceiling=.65)


def scene(kind):
    rng=np.random.default_rng(3);return {i:SCENES[kind](rng) for i in range(4)}


def verdict(controls,kind,policy='review'):
    result=prepare(highlight_recipe(policy=policy,limits=False,**controls),scene(kind))
    return 'block' if result['proposal'] is None else 'pass'


@pytest.mark.parametrize('kind',list(SCENES))
@pytest.mark.parametrize('policy',['review','preserve_detail','subdue','intentional_lift','intentional_clipping'])
def test_a_brighten_request_is_never_blocked_by_the_highlight_gate_on_any_footage(kind,policy):
    assert verdict(BRIGHTEN,kind,policy)=='pass'


@pytest.mark.parametrize('kind',['beach','city_day','city_night','hazy_sky'])
def test_lowering_white_point_on_a_darkening_grade_is_caught_on_every_footage_class(kind):
    assert verdict(WHITE_POINT_BUG,kind)=='block'


@pytest.mark.parametrize('kind',list(SCENES))
def test_a_contrast_only_punchy_look_passes_unless_the_plan_declares_subdue(kind):
    assert verdict(PUNCHY,kind,'review')=='pass' and verdict(PUNCHY,kind,'intentional_lift')=='pass'


def test_subdue_policy_rejects_contrast_lift_where_it_is_visible():
    assert verdict(PUNCHY,'city_day','subdue')=='block' and verdict(PUNCHY,'hazy_sky','subdue')=='block'


@pytest.mark.parametrize('kind',list(SCENES))
@pytest.mark.parametrize('policy',['review','subdue'])
def test_subduing_with_the_highlight_ceiling_passes_on_every_footage(kind,policy):
    assert verdict(SUBDUED,kind,policy)=='pass'


def test_darkening_is_judged_from_the_plan_not_from_bright_footage():
    """On a beach, a lowered white_point lifts the sand that fills the 'midtones'; the plan still darkens 18% grey."""
    result=prepare(highlight_recipe(limits=False,**WHITE_POINT_BUG),scene('beach'))
    layer=result['report']['highlights']['layers']['parameters']
    assert layer['midtone_change']>0 and layer['mid_grey_change']<-.02 and layer['darkening']


def test_small_bright_areas_are_measured_from_their_own_pixels():
    layer=prepare(highlight_recipe(limits=False,**WHITE_POINT_BUG),scene('city_night'))['report']['highlights']['layers']['parameters']
    assert layer['assessable_frames']==4 and layer['highlight_fraction']<.05 and layer['highlight_change']>.02


def test_amber_lights_a_lowered_white_point_barely_changes_are_not_flagged():
    rng=np.random.default_rng(3);frames={i:city_night(rng,lights=(250,200,120)) for i in range(4)}
    layer=prepare(highlight_recipe(limits=False,**WHITE_POINT_BUG),frames)['report']['highlights']['layers']['parameters']
    assert layer['highlight_change']<=.02 and not layer['highlights_lifted']


def test_frames_without_real_highlights_are_not_assessed():
    dark={0:np.full((64,64,3),40,np.uint8),1:np.full((64,64,3),60,np.uint8)}
    result=prepare(highlight_recipe(limits=False,**WHITE_POINT_BUG),dark)
    layer=result['report']['highlights']['layers']['parameters']
    assert result['proposal'] is not None and layer['assessable_frames']==0 and layer['highlight_change'] is None
