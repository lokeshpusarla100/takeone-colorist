import { parseBackend } from './api';
import type { BackendState } from './api';
import { AppError } from './errors';
import type { MediaAsset } from './types';

/** FFprobe owns source acceptance. Browser playback is an independent, optional view. */
export function preparedSource(state: BackendState): MediaAsset {
  parseBackend(state);
  if (!state.source || !state.preview) {
    throw new AppError('SOURCE_NOT_READY', 'The backend has not prepared this source.',
      'Wait for source preparation or retry the failed step.');
  }
  return {
    id: crypto.randomUUID(),
    name: state.source.name,
    size: state.source.bytes,
    type: 'video/webm',
    width: state.source.width,
    height: state.source.height,
    duration: state.source.duration,
    thumbnail: '',
    playbackUrl: state.preview.url,
  };
}
