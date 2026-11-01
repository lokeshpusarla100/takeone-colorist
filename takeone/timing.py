"""Preserve source presentation timestamps; never normalize jitter to nominal FPS."""
from fractions import Fraction
import math

from .errors import Problem


def instants(manifest):
    if 'pts' in manifest and 'time_base' in manifest:
        tick=Fraction(manifest['time_base'])
        return [int(p)*tick for p in manifest['pts']]
    # Compatibility with evidence saved before integer PTS were recorded.
    start=Fraction(str(manifest['start']))
    return [start+Fraction(str(t)) for t in manifest['times']]


def classify(manifest):
    values=instants(manifest);tick=Fraction(manifest['time_base'])
    intervals=[b-a for a,b in zip(values,values[1:])]
    try:rate=Fraction(manifest.get('nominal_rate','0/1'))
    except (ValueError,ZeroDivisionError):rate=0
    nominal=1/rate if rate>0 else sorted(intervals)[len(intervals)//2] if intervals else tick
    jitter=max((abs(d-nominal) for d in intervals),default=Fraction(0))
    lattice=max((abs(t-values[0]-i*nominal) for i,t in enumerate(values)),default=Fraction(0))
    kind='cfr' if lattice<=tick else 'near_cfr' if jitter<=max(2*tick,nominal/100) else 'vfr'
    return {'classification':kind,'classification_only':True,'frame_count':len(values),
            'time_base':str(tick),'first_pts_seconds':float(values[0]),
            'last_normalized_seconds':float(values[-1]-values[0]),
            'nominal_interval_seconds':float(nominal),'max_interval_deviation_seconds':float(jitter),
            'max_nominal_lattice_error_seconds':float(lattice)}


def encoding_policy(metadata):
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    tick=Fraction(video['time_base'])
    if tick<=0 or tick.denominator>2147483647:
        raise Problem('RENDER_TIME_BASE_UNSUPPORTED','The source time base cannot be represented by the MP4 renderer.',
                      'Use a source with a supported presentation time base; no frame-rate normalization was made.',422)
    denominators=[tick.denominator,1000]
    for stream in metadata['streams']:
        if stream['codec_type']=='audio':denominators.append(Fraction(stream['time_base']).denominator)
    movie_scale=math.lcm(*denominators)
    if movie_scale>2147483647:movie_scale=1000000
    return {'policy':'preserve_source_pts','encoder_time_base':str(tick),
            'video_track_timescale':tick.denominator,'movie_timescale':movie_scale,
            'source_origin_seconds':metadata.get('format',{}).get('start_time','0')}


def validate(source,output,*,origin_shift=0,role='master',movie_timescale=1000000):
    a=instants(source);b=instants(output)
    report={'policy':'preserve_source_pts','role':role,'source':classify(source),
            'output_time_base':output['time_base'],'source_frames':len(a),'output_frames':len(b),
            'frame_mapping':'one_to_one_presentation_order'}
    def reject(message,**details):
        raise Problem('RENDER_TIMING_CHANGED',message,
                      'Retry rendering with the saved recipe. The source and earlier versions are preserved.',422,
                      **report,**details)
    if len(a)!=len(b):reject('Rendering changed the decoded frame count.')
    if not a or any(y<=x for x,y in zip(b,b[1:])):reject('Rendered frame timestamps are missing, duplicated or reordered.')
    tick=Fraction(output['time_base'])
    if tick<=0:reject('The output time base is invalid.')
    shortest=min((y-x for x,y in zip(a,a[1:])),default=Fraction(1))
    # Two output ticks cover rounding both ends of a normalized timestamp.
    # The old 2 ms ceiling is retained, never widened. Close frames tighten it.
    bound=min(Fraction(2,1000),2*tick+Fraction(2,1000000),shortest/4)
    errors=[(y-b[0])-(x-a[0]) for x,y in zip(a,b)]
    absolute=[y-x-Fraction(str(origin_shift)) for x,y in zip(a,b)]
    bad=next((i for i,e in enumerate(errors) if abs(e)>bound),None)
    report.update(quantization_bound_ms=float(bound*1000),max_normalized_error_ms=float(max(map(abs,errors))*1000),
                  last_frame_error_ms=float(errors[-1]*1000),first_failing_frame=bad,
                  first_frame_offset_ms=float(absolute[0]*1000))
    if bad is not None:reject('Rendered frame timestamps differ beyond output quantization.')
    # Preview codecs/muxers may shift all tracks together for codec delay. The
    # downloadable master must retain the source's common audio/video origin.
    if role=='master':
        start_bound=min(Fraction(2,1000),tick+Fraction(1,movie_timescale)+Fraction(2,1000000))
        if max(map(abs,absolute))>start_bound:
            reject('Rendering changed the source presentation origin or accumulated timing drift.',
                   max_absolute_error_ms=float(max(map(abs,absolute))*1000))
        before=source.get('durations',[]);after=output.get('durations',[])
        if before and after and before[-1] is not None and after[-1] is not None:
            end_error=absolute[-1]+int(after[-1])*tick-int(before[-1])*Fraction(source['time_base'])
            report['end_error_ms']=float(end_error*1000)
            if abs(end_error)>bound:reject('Rendering changed the final frame presentation duration.')
    return report


def validate_audio(source_meta,output_meta,policy,copied):
    before=next((s for s in source_meta['streams'] if s['codec_type']=='audio'),None)
    after=next((s for s in output_meta['streams'] if s['codec_type']=='audio'),None)
    if not before or not after:return None # Presence is checked by the media contract.
    shift=-Fraction(str(policy['source_origin_seconds']))
    start=lambda s: int(s['start_pts'])*Fraction(s['time_base']) if 'start_pts' in s else Fraction(str(s.get('start_time','0')))
    error=start(after)-start(before)-shift
    bound=Fraction(2,1000)
    report={'start_error_ms':float(error*1000),'copied':copied}
    if abs(error)>bound:
        raise Problem('RENDER_TIMING_CHANGED','Rendering changed the audio/video presentation offset.',
                      'The candidate was not accepted. Inspect audio timestamp handling.',422,audio_timing=report)
    if copied and 'duration_ts' in before and 'duration_ts' in after:
        error=int(after['duration_ts'])*Fraction(after['time_base'])-int(before['duration_ts'])*Fraction(before['time_base'])
        report['duration_error_ms']=float(error*1000)
        if abs(error)>bound:
            raise Problem('RENDER_TIMING_CHANGED','Copied audio duration changed unexpectedly.',
                          'The candidate was not accepted. Inspect the source audio timeline.',422,audio_timing=report)
    return report
