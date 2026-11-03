import { useEffect, useState } from 'react';
import type { BackendState } from './api';
import { runProgress } from './workflow';
import { processingTimings } from './progressTiming';
import { runPipeline } from './progressModel';
import { runSteps } from './stepper';
import { useLiveProgress } from './liveProgress';

/**
 * Everything the run display needs, derived once from the backend state: the heading and note, the timing markers,
 * the live encode measurement, the pipeline position and the step rows. The status strip over the video and the
 * rail both read this one object, so they cannot disagree and the encode is polled once, not twice.
 */
export function useRunModel(state: BackendState | undefined, localBusy: boolean, localProgress: string | null | undefined, sending: boolean, online: boolean) {
  const progress = runProgress(state, localBusy, localProgress, sending);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (!progress.working || !online) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [progress.working, online, state?.id, state?.revision]);
  const timing = processingTimings(state, now, online);
  const live = useLiveProgress(state, online);
  const pipeline = runPipeline(state, live);
  const rows = runSteps(state, pipeline, timing);
  return { progress, timing, live, pipeline, rows };
}
export type RunModel = ReturnType<typeof useRunModel>;
