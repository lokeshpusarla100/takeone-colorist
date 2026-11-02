"""Bounded level fitting and highlight feasibility, checked before encoding.

Both are planning checks on actual sampled pixels, not final encoded QC. Neither
raises a limit, picks a look, changes colour/gamma/gain or mutates a saved recipe.

Clipping fit: input black/white cutoffs are minimally restored against the plan's
own declared clipping fractions.

Highlight gate: a layer that darkens the typical pixel but lifts the brightest
ones contradicts a brief that asks for subdued or controlled highlights. It is
measured on each frame's own bright pixels, with perceptual (not footage-fitted)
thresholds; see the constants below. The
operator keeps white at white everywhere except ``highlight_ceiling``, and lowering
``white_point`` or raising ``contrast`` pushes upper tones UP, so such a plan can
never deliver a darker sky. The gate blocks it before any SAM request or encode and
reports the measured cause plus the ``highlight_ceiling`` that would remove the
lift, so a replan (or an honest unfulfilled request) replaces a wasted render. The
suggestion is never applied silently: it is a creative control.
"""
import numpy as np

from . import color_processing
from .errors import Problem

WEIGHTS=np.array([.2126,.7152,.0722],np.float32)
MAX_SEARCH_STEPS=12
# Highlight gate constants live in tonal.py (shared with review evidence and the revision
# convergence rule). None is fitted to a particular video; every measurement is made on the
# footage's own pixels, so bright beaches, night cities and hazy skies are judged alike.
# Whether a layer darkens is a property of the plan, not the footage: it is the layer's change
#   at photographic 18% grey (MID_GREY). Measuring it on the frame instead would mislabel a
#   darkening plan on bright footage, whose "midtones" are sand or sky a lowered white_point lifts.
from .tonal import (VISIBLE_CHANGE,MID_GREY,HIGHLIGHT_QUANTILE,HIGHLIGHT_FLOOR,  # noqa: E402
                    MIN_HIGHLIGHT_FRACTION,highlight_mask)
# Without an explicit subdue policy, a lift is blocked only when the input white cutoff
#   causes at least this share of it: lowering white_point while darkening flattens and
#   brightens highlights on any footage. A contrast-only S-curve lift is a legitimate look.
WHITE_POINT_SHARE=.5
# Policies under which brighter highlights are an explicit, declared intent.
LIFT_ALLOWED_POLICIES={'intentional_lift','intentional_clipping'}


def _frames(source_pixels):
    if not source_pixels or len(source_pixels)>12:
        raise Problem('PREFLIGHT_INPUT_INVALID','Preflight requires one to twelve bounded source frames.',
                      'Prepare the saved representative source samples before rendering.',422)
    frames={}
    for index,pixels in source_pixels.items():
        if not isinstance(index,int) or index<0 or not isinstance(pixels,np.ndarray) or pixels.dtype!=np.uint8 or pixels.ndim!=3 or pixels.shape[2]!=3 or not pixels.size or max(pixels.shape[:2])>256:
            raise Problem('PREFLIGHT_INPUT_INVALID','Preflight source evidence has invalid geometry or pixel representation.',
                          'Use the verified 256-pixel RGB evidence samples.',422)
        rgb=pixels.astype(np.float32)/255
        y=rgb@WEIGHTS
        frames[index]=(rgb,float(np.mean(y<=1/255)),float(np.mean(y>=254/255)))
    return frames


def _assess(parameters,frames,limits):
    rows=[]
    for index,(rgb,black,white) in frames.items():
        result=color_processing.apply_rgb(rgb,parameters);y=result@WEIGHTS
        # One output code on either side is a declared quantization envelope,
        # not an aesthetic threshold. It is deliberately conservative.
        row={'frame_index':index,'new_black_fraction':float(np.mean(y<=1/255))-black,
             'new_white_fraction':float(np.mean(y>=254/255))-white,
             'black_envelope_fraction':float(np.mean(y<=2/255))-black,
             'white_envelope_fraction':float(np.mean(y>=253/255))-white}
        rows.append(row)
    def passes(envelope):
        black_key='black_envelope_fraction' if envelope else 'new_black_fraction'
        white_key='white_envelope_fraction' if envelope else 'new_white_fraction'
        return all((limits.max_new_black_fraction is None or r[black_key]<=limits.max_new_black_fraction)
                   and (limits.max_new_white_fraction is None or r[white_key]<=limits.max_new_white_fraction) for r in rows)
    return {'frames':rows,'predicted_passed':passes(False),'envelope_passed':passes(True)}


def _fit(parameters,frames,limits,counter):
    """Level-only fit for one parameter set; returns (fitted, before, after)."""
    original=parameters.model_copy(deep=True);current=original.model_copy(deep=True)
    def assess(p):
        counter[0]+=1
        return _assess(p,frames,limits)
    before=assess(current)
    if before['envelope_passed']:
        return current,before,before
    def side_ok(result,side):
        limit=limits.max_new_black_fraction if side=='black' else limits.max_new_white_fraction
        return limit is None or all(r[side+'_envelope_fraction']<=limit for r in result['frames'])
    for _ in range(2):
        result=assess(current)
        if not side_ok(result,'black') and current.black_point>0:
            safe=current.model_copy(update={'black_point':0.})
            if side_ok(assess(safe),'black'):
                low=0.;high=current.black_point
                for _ in range(MAX_SEARCH_STEPS):
                    middle=(low+high)/2;trial=current.model_copy(update={'black_point':middle})
                    if side_ok(assess(trial),'black'):low=middle
                    else:high=middle
                current.black_point=low
            else:current.black_point=0.
        result=assess(current)
        if not side_ok(result,'white') and current.white_point<1:
            safe=current.model_copy(update={'white_point':1.})
            if side_ok(assess(safe),'white'):
                low=current.white_point;high=1.
                for _ in range(MAX_SEARCH_STEPS):
                    middle=(low+high)/2;trial=current.model_copy(update={'white_point':middle})
                    if side_ok(assess(trial),'white'):high=middle
                    else:low=middle
                current.white_point=high
            else:current.white_point=1.
        result=assess(current)
        if result['envelope_passed']:break
    after=assess(current)
    if not after['predicted_passed']:
        neutral=current.model_copy(update={'black_point':0.,'white_point':1.})
        neutral_result=assess(neutral)
        if neutral_result['predicted_passed']:current=neutral;after=neutral_result
    return current,before,after


def _prepare_clipping(recipe,source_pixels):
    """Return a typed proposal and measured report; callers must run encoded QC.

    proposal is None for blocked/not_applicable. A needs_encoded_check proposal
    meets the exact mathematical thresholds but not their conservative envelope.
    Even a passed/corrected result is conditional on actual sparse/dense encoding.
    """
    proposal=recipe.proposal.model_copy(deep=True);limits=proposal.execution_limits
    report={'version':1,'method':'v3_rgb_one_output_code_envelope','status':'not_applicable','changed':False,
            'quantization_envelope_codes':1,'limits':limits.model_dump(),'changes':[],'before':None,'after':None,'evaluations':0,
            'limitations':['Bounded RGB samples are a planning estimate, not whole-video or encoded clipping certification.',
                           'LUT interpolation, spatial resampling, chroma subsampling and lossy encoding require the actual encoder probe and final dense QC.']}
    if recipe.tone_operator!='luminance_ocio_v3':
        report['reason']='This mathematical level fitting applies only to v3 execution; other operators retain their own execution/QC path.'
        return {'proposal':None,'report':report}
    if limits.max_new_black_fraction is None and limits.max_new_white_fraction is None:
        report['status']='passed';report['reason']='No clipping fraction bound was declared; no histogram target or level correction was imposed.'
        return {'proposal':proposal,'report':report}
    frames=_frames(source_pixels);report['frame_count']=len(frames)
    counter=[0]
    if proposal.mode=='localized':
        # Conservative envelope: every rendered pixel comes from the base,
        # foreground or background layer (or a feather blend of the latter
        # two, whose luma lies between its endpoints). If each layer, applied
        # to the whole bounded sample, meets the bound, no masked subset can
        # exceed it. This is a planning check only; it runs before any SAM
        # request or encode, and final dense QC remains authoritative.
        report['limitations'].append('Localized preflight evaluates each layer over the full bounded samples as a conservative envelope; masked/feathered pixels are a subset or blend of those layers. No encoded localized probe was run, and final dense QC remains authoritative.')
        layers=[('parameters',proposal.parameters),('foreground',proposal.local.foreground),('background',proposal.local.background),
                *((f'region:{r.concept}',r.treatment) for r in proposal.local.regions)]
        fitted={};report['layers']={}
        for name,params in layers:
            current,before,after=_fit(params,frames,limits,counter)
            fitted[name]=current
            report['layers'][name]={'before':before,'after':after}
            if not after['predicted_passed']:
                report.update(status='blocked',evaluations=counter[0],
                    reason=f'The {name} layer cannot meet the clipping bounds by removing only input level cutoffs. A targeted creative replan is required; no brightness, gamma, palette or limit was silently changed.')
                return {'proposal':None,'report':report}
        changed=[]
        for name,params in layers:
            for field in ['black_point','white_point']:
                start=getattr(params,field);end=getattr(fitted[name],field)
                if abs(start-end)>1e-12:
                    changed.append({'layer':name,'parameter':field,'before':start,'after':end,
                        'reason':'Reduce the input level cutoff for this layer; creative tone, palette and limits are unchanged.'})
        proposal.parameters=fitted['parameters']
        proposal.local.foreground=fitted['foreground']
        proposal.local.background=fitted['background']
        for region in proposal.local.regions:region.treatment=fitted[f'region:{region.concept}']
        if changed:
            proposal.summary=(proposal.summary+' Input level cutoffs adjusted per layer to honor unchanged clipping bounds; encoded review still required.')[:1000]
        proposal=type(proposal).model_validate(proposal.model_dump())
        envelope=all(report['layers'][name]['after']['envelope_passed'] for name,_ in layers)
        report.update(status=('corrected' if changed else 'passed') if envelope else 'needs_encoded_check',
                      changed=bool(changed),changes=changed,evaluations=counter[0])
        return {'proposal':proposal,'report':report}
    original=proposal.parameters.model_copy(deep=True)
    current,before,after=_fit(original,frames,limits,counter)
    report['before']=before
    if not after['predicted_passed']:
        report.update(status='blocked',after=after,evaluations=counter[0],
            reason='The original clipping bounds cannot be met by removing only input level cutoffs. A targeted creative replan is required; no brightness, gamma, palette or limit was silently changed.')
        return {'proposal':None,'report':report}
    changed=[]
    for name in ['black_point','white_point']:
        start=getattr(original,name);end=getattr(current,name)
        if abs(start-end)>1e-12:
            reason=('Reduce the input shadow cutoff; positive black_point clips input shadows and does not lift the output black floor.'
                    if name=='black_point' else 'Restore the input white endpoint to reduce clipping while retaining the declared highlight bound.')
            changed.append({'parameter':name,'before':start,'after':end,'reason':reason})
            decision=next((d for d in proposal.decisions if d.parameter==name),None)
            if decision:decision.reason=(f'Execution-level correction {start:.6f} → {end:.6f}. '+reason)[:500]
    proposal.parameters=current
    if changed:
        proposal.summary=(proposal.summary+' Input level cutoffs adjusted to honor unchanged clipping bounds; encoded review still required.')[:1000]
    proposal=type(proposal).model_validate(proposal.model_dump())
    report.update(status=('corrected' if changed else 'passed') if after['envelope_passed'] else 'needs_encoded_check',
                  changed=bool(changed),changes=changed,after=after,evaluations=counter[0])
    return {'proposal':proposal,'report':report}


def _layer_response(parameters,frames):
    """Paired change of each frame's own highlight pixels and of everything below them.

    Pixel-paired (the same source pixels before and after), not a distribution quantile, so a
    frame already near white (beach, snow) or with tiny bright areas (night city lights) is
    measured as faithfully as a pale sky. Frames without real highlights are not assessable.
    """
    rows=[];clip=[]
    for rgb,_,_ in frames.values():
        before=rgb@WEIGHTS;result=color_processing.apply_rgb(rgb,parameters);after=result@WEIGHTS
        # Informational: luma-based clipping bounds miss a cooled sky whose blue/green channel
        # saturates while luma stays below 254/255. Not a block on its own.
        clip.append(float(np.mean((result>=254/255).any(axis=-1))-np.mean((rgb>=254/255).any(axis=-1))))
        high=highlight_mask(before)
        if high is None:continue
        change=after-before
        rows.append((float(change[~high].mean()),float(change[high].mean()),float(high.mean())))
    response={'assessable_frames':len(rows),'frame_count':len(frames),'new_channel_clip_fraction':float(max(0.,max(clip,default=0.)))}
    if not rows:
        return {**response,'midtone_change':None,'highlight_change':None,'highlight_fraction':0.}
    table=np.array(rows)
    return {**response,'midtone_change':float(table[:,0].mean()),'highlight_change':float(table[:,1].mean()),
            'highlight_fraction':float(table[:,2].mean())}


def _mid_grey_change(parameters):
    grey=np.full((1,3),MID_GREY,np.float32)
    return float((color_processing.apply_rgb(grey,parameters)@WEIGHTS)[0]-MID_GREY)


def _lift_drivers(parameters,frames,current,minimum=VISIBLE_CHANGE/4):
    """Controls whose neutral value would lower the highlight change, with the size of that effect."""
    drivers={}
    for name,neutral in [('white_point',1.),('contrast',1.),('black_point',0.)]:
        value=getattr(parameters,name)
        if abs(value-neutral)<=1e-12:continue
        neutral_change=_layer_response(parameters.model_copy(update={name:neutral}),frames)['highlight_change']
        if current-neutral_change>=minimum:
            drivers[name]={'value':value,'highlight_change_if_neutral':neutral_change,'lift_removed_if_neutral':current-neutral_change}
    return dict(sorted(drivers.items(),key=lambda item:-item[1]['lift_removed_if_neutral']))


def _ceiling_to_pass(parameters,frames,minimum=.6,steps=10):
    """Smallest darkening of highlight_ceiling (others unchanged) that keeps the lift below visibility, or None."""
    def ok(value):
        return _layer_response(parameters.model_copy(update={'highlight_ceiling':value}),frames)['highlight_change']<=VISIBLE_CHANGE
    high=parameters.highlight_ceiling
    if not ok(minimum):return None
    low=minimum
    for _ in range(steps):
        middle=(low+high)/2
        if ok(middle):low=middle
        else:high=middle
    return round(low,4)


def assess_highlights(proposal,frames):
    """Per-layer highlight response; a violation is a darkening layer whose highlights visibly brighten
    against the declared policy. Brightening layers (a "make it brighter" grade, a lifted subject) are
    never violations: brighter highlights are what they ask for, and clipping has its own bound.

    * intentional_lift / intentional_clipping: never blocked.
    * subdue: blocked whenever the darkening layer visibly brightens highlights, by any control.
    * preserve_detail / review: blocked when the lift comes mainly from the input white cutoff, which
      flattens and brightens highlights on any footage; a contrast-only S-curve lift passes.
    """
    policy=proposal.intent.highlight_policy
    layers=[('parameters',proposal.parameters)]
    if proposal.local:
        layers+=[('foreground',proposal.local.foreground),('background',proposal.local.background),
                 *((f'region:{r.concept}',r.treatment) for r in proposal.local.regions)]
    report={'policy':policy,'enforced':policy not in LIFT_ALLOWED_POLICIES,'visible_change':VISIBLE_CHANGE,'mid_grey':MID_GREY,
            'highlight_quantile':HIGHLIGHT_QUANTILE,'highlight_floor':HIGHLIGHT_FLOOR,'white_point_share':WHITE_POINT_SHARE,
            'layers':{},'violations':[]}
    for name,parameters in layers:
        response=_layer_response(parameters,frames)
        grey=_mid_grey_change(parameters)
        darkening=grey<=-VISIBLE_CHANGE
        lifted=darkening and response['assessable_frames']>0 and response['highlight_change']>VISIBLE_CHANGE
        entry={**response,'mid_grey_change':grey,'darkening':darkening,'highlights_lifted':lifted}
        if lifted and report['enforced']:
            drivers=_lift_drivers(parameters,frames,response['highlight_change'])
            share=drivers.get('white_point',{}).get('lift_removed_if_neutral',0.)/response['highlight_change']
            entry.update(drivers=drivers,white_point_share=share)
            rule=('subdue_policy' if policy=='subdue' else 'white_point_cutoff' if share>=WHITE_POINT_SHARE else None)
            if rule:
                entry.update(rule=rule,suggested_highlight_ceiling=_ceiling_to_pass(parameters,frames))
                report['violations'].append(name)
            else:entry['allowed_because']='Contrast-shaped lift without a subdue policy is a legitimate look.'
        report['layers'][name]=entry
    return report


def highlight_reason(report):
    parts=[]
    for name in report['violations']:
        entry=report['layers'][name]
        causes=', '.join(f"{control} {info['value']:g} (neutral removes {info['lift_removed_if_neutral']:.3f} of the lift)"
                         for control,info in entry.get('drivers',{}).items())
        why=('the plan declares highlight_policy=subdue' if entry['rule']=='subdue_policy'
             else f"the input white cutoff causes {entry['white_point_share']:.0%} of it")
        parts.append(f"{name}: 18% grey {entry['mid_grey_change']:+.3f} but the brightest source pixels {entry['highlight_change']:+.3f}, and {why}"
                     +(f"; main drivers {causes}" if causes else '')
                     +(f"; with the other controls unchanged, highlight_ceiling {entry['suggested_highlight_ceiling']:g} only just keeps the lift below visibility (lower values darken highlights further)"
                       if entry.get('suggested_highlight_ceiling') is not None else '; even highlight_ceiling 0.6 does not offset it, so tone controls must change'))
    return ('A darkening layer also brightens highlights, so bright areas cannot become darker or more subdued. '
            +' | '.join(parts)+'. Lowering white_point or raising contrast brightens highlights; highlight_ceiling is the control that '
            'darkens them. Revise the controls, or report the darker-highlight request as unfulfilled.')


def prepare(recipe,source_pixels,*,highlight_gate=True):
    """Clipping fit, then the highlight gate on the resulting proposal.

    A highlight block returns proposal=None with report.blocked_by='highlight_lift';
    nothing is silently adjusted. The gate runs even when no clipping fraction was
    declared. ``highlight_gate=False`` is only for the neutral-level recovery path.
    """
    result=_prepare_clipping(recipe,source_pixels)
    proposal=result['proposal']
    if recipe.tone_operator!='luminance_ocio_v3' or proposal is None or not highlight_gate:return result
    assessment=assess_highlights(proposal,_frames(source_pixels))
    result['report']['highlights']=assessment
    if assessment['violations']:
        result['report'].update(status='blocked',blocked_by='highlight_lift',reason=highlight_reason(assessment))
        return {'proposal':None,'report':result['report']}
    return result
