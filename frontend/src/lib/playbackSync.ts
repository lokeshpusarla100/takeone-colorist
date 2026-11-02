/**
 * Coordinated playback for the main and comparison ("before") videos.
 *
 * Browsers cancel a pending HTMLMediaElement.play() promise with an
 * AbortError whenever pause(), load() or a seek interrupts it. That is an
 * expected cancellation, not a playback failure: it must neither surface an
 * error nor tear down the comparison. Only one play request per comparison
 * element is ever in flight, and a generation counter makes results from a
 * superseded request inert.
 */

export type PlaybackElement = {
  currentTime: number;
  duration: number;
  playbackRate: number;
  paused: boolean;
  readyState: number;
  play: () => Promise<void>;
  pause: () => void;
};

export function isPlaybackAbort(cause: unknown): boolean {
  return typeof cause === 'object' && cause !== null && 'name' in cause
    && (cause as { name?: unknown }).name === 'AbortError';
}

export type ComparisonSync = {
  /** Align the comparison element to the main element. Returns true if it sought. */
  sync: (force?: boolean) => boolean;
  /** Start the comparison element if the main element is playing. AbortErrors are swallowed. */
  play: () => Promise<'played' | 'aborted' | 'failed' | 'idle'>;
  /** Pause the comparison element and invalidate any in-flight play request. */
  pause: () => void;
  /** Invalidate any in-flight play request without pausing (e.g. source change). */
  invalidate: () => void;
};

export function createComparisonSync(
  getMain: () => PlaybackElement | null,
  getBefore: () => PlaybackElement | null,
  onFailure: (cause: unknown) => void,
): ComparisonSync {
  let generation = 0;
  let pending: Promise<'played' | 'aborted' | 'failed' | 'idle'> | null = null;
  let pendingGeneration = -1;

  const sync = (force = false): boolean => {
    const main = getMain();
    const before = getBefore();
    if (!main || !before) return false;
    before.playbackRate = main.playbackRate;
    if (!Number.isFinite(main.currentTime)) return false;
    const drift = Math.abs(main.currentTime - before.currentTime);
    if (!force && drift <= 0.1) return false;
    // Setting currentTime before any media data is meaningless and can
    // abort a pending play; seek only once the element has data/metadata.
    if (before.readyState === 0 || !Number.isFinite(before.duration)) return false;
    before.currentTime = Math.min(Math.max(main.currentTime, 0), before.duration || main.currentTime);
    return true;
  };

  const play = (): Promise<'played' | 'aborted' | 'failed' | 'idle'> => {
    const main = getMain();
    const before = getBefore();
    if (!main || !before || main.paused) return Promise.resolve('idle');
    sync();
    if (!before.paused) return Promise.resolve('played');
    if (pending && pendingGeneration === generation) return pending;
    const ticket = ++generation;
    pendingGeneration = ticket;
    let request: Promise<void>;
    try {
      request = before.play();
    } catch (cause) {
      pending = null;
      if (ticket === generation && !isPlaybackAbort(cause)) onFailure(cause);
      return Promise.resolve(isPlaybackAbort(cause) ? 'aborted' : 'failed');
    }
    pending = request.then(
      () => (ticket === generation ? 'played' : 'aborted'),
      (cause: unknown) => {
        if (ticket !== generation || isPlaybackAbort(cause)) return 'aborted';
        onFailure(cause);
        return 'failed';
      },
    ).finally(() => {
      if (pendingGeneration === ticket) pending = null;
    });
    return pending;
  };

  const pause = (): void => {
    generation += 1;
    pending = null;
    getBefore()?.pause();
  };

  const invalidate = (): void => {
    generation += 1;
    pending = null;
  };

  return { sync, play, pause, invalidate };
}
