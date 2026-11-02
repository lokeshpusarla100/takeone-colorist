"""Bounded level fitting and highlight feasibility, checked before encoding.

Both are planning checks on actual sampled pixels, not final encoded QC. Neither
raises a limit, picks a look, changes colour/gamma/gain or mutates a saved recipe.

Clipping fit: input black/white cutoffs are minimally restored against the plan's
own declared clipping fractions.

Highlight gate: a layer that darkens the typical pixel but lifts the brightest
ones contradicts a brief that asks for subdued or controlled highlights. The
supported operator keeps white at white, and lowering ``white_point`` or raising
``contrast`` pushes upper tones UP, so such a plan can never deliver a darker sky.
The gate blocks it before any SAM request or encode and reports the measured
cause, so a replan (or an honest unfulfilled request) replaces a wasted render.
"""
import numpy as np

from . import color_processing
from .errors import Problem

WEIGHTS=np.array([.2126,.7152,.0722],np.float32)
MAX_SEARCH_STEPS=12
# A layer "darkens" when its sampled median luma falls by at least this much.
DARKENING_MEDIAN_DROP=.03
# Such a layer must not raise the sampled 95th-percentile luma by more than this.
HIGHLIGHT_LIFT_LIMIT=.03
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
        layers=[('parameters',proposal.parameters),('foreground',proposal.local.foreground),('background',proposal.local.background)]
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
    """Mean sampled change in median/p95 luma, and worst new any-channel clipping, for one layer."""
    rows=[]
    for rgb,_,_ in frames.values():
        before=rgb@WEIGHTS;result=color_processing.apply_rgb(rgb,parameters);after=result@WEIGHTS
        rows.append((float(np.median(after)-np.median(before)),float(np.quantile(after,.95)-np.quantile(before,.95)),
                     float(np.mean((result>=254/255).any(axis=-1))-np.mean((rgb>=254/255).any(axis=-1)))))
    table=np.array(rows)
    return {'median_change':float(table[:,0].mean()),'highlight_change':float(table[:,1].mean()),
            # Informational: luma-based clipping bounds miss a cooled sky whose blue/green channel
            # saturates while luma stays below 254/255. Not a block on its own.
            'new_channel_clip_fraction':float(max(0.,table[:,2].max()))}


def _lift_drivers(parameters,frames,current,minimum=.005):
    """Controls whose neutral value would lower the highlight (p95) change, with the size of that effect."""
    drivers={}
    for name,neutral in [('white_point',1.),('contrast',1.),('black_point',0.)]:
        value=getattr(parameters,name)
        if abs(value-neutral)<=1e-12:continue
        trial=parameters.model_copy(update={name:neutral})
        neutral_change=_layer_response(trial,frames)['highlight_change']
        if current-neutral_change>=minimum:
            drivers[name]={'value':value,'highlight_change_if_neutral':neutral_change,'lift_removed_if_neutral':current-neutral_change}
    return dict(sorted(drivers.items(),key=lambda item:-item[1]['lift_removed_if_neutral']))


def assess_highlights(proposal,frames):
    """Per-layer highlight response on bounded source samples; violations are darkening layers that lift highlights."""
    policy=proposal.intent.highlight_policy
    layers=[('parameters',proposal.parameters)]
    if proposal.local:layers+=[('foreground',proposal.local.foreground),('background',proposal.local.background)]
    report={'policy':policy,'enforced':policy not in LIFT_ALLOWED_POLICIES,'darkening_median_drop':DARKENING_MEDIAN_DROP,
            'highlight_lift_limit':HIGHLIGHT_LIFT_LIMIT,'layers':{},'violations':[]}
    for name,parameters in layers:
        response=_layer_response(parameters,frames)
        darkening=response['median_change']<=-DARKENING_MEDIAN_DROP
        entry={**response,'darkening':darkening}
        if report['enforced'] and darkening and response['highlight_change']>HIGHLIGHT_LIFT_LIMIT:
            entry['drivers']=_lift_drivers(parameters,frames,response['highlight_change']);report['violations'].append(name)
        report['layers'][name]=entry
    return report


def highlight_reason(report):
    parts=[]
    for name in report['violations']:
        entry=report['layers'][name]
        causes=', '.join(f"{control} {info['value']:g} (neutral removes {info['lift_removed_if_neutral']:.3f} of the lift)"
                         for control,info in entry.get('drivers',{}).items())
        parts.append(f"{name}: median luma {entry['median_change']:+.3f} but highlight (p95) luma {entry['highlight_change']:+.3f}"
                     +(f"; main drivers {causes}" if causes else '; no single input-level control explains it, so gamma/brightness_gain or the layer treatment must change'))
    return ('A darkening layer also brightens highlights, so bright sky/cloud/water cannot become darker or more subdued. '
            +' | '.join(parts)+'. Lowering white_point or raising contrast brightens highlights; gamma/brightness_gain below 1 '
            'darken only upper midtones. Revise the controls, or report the darker-highlight request as unfulfilled.')


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
