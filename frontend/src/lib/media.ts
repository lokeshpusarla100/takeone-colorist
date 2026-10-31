import type { MediaAsset } from './types';
import { AppError, asAppError, mediaFailure } from './errors';

export function inspectVideo(file: File, options: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<MediaAsset> {
  return new Promise((resolve, reject) => {
    const details = { file: file.name, bytes: file.size, mime: file.type || 'unknown' };
    if (!file.size) {
      reject(new AppError('MEDIA_EMPTY', 'The selected file is empty.', 'Choose a video containing data.', details));
      return;
    }
    if (!file.type.startsWith('video/') && !/\.(mp4|mov|webm|m4v|mkv)$/i.test(file.name)) {
      reject(new AppError('MEDIA_FILE_TYPE', 'The selected file is not a video input.',
        'Choose an MP4, MOV, WebM, M4V or MKV file. Playback depends on the browser.', details));
      return;
    }
    let url: string;
    try { url = URL.createObjectURL(file); } catch (cause) {
      reject(asAppError(cause, 'media.open', details)); return;
    }
    const video = document.createElement('video');
    video.preload = 'auto';
    video.muted = true;
    video.playsInline = true;
    let settled = false;
    let stage = 'media.metadata';
    const cleanup = () => {
      clearTimeout(timeout);
      options.signal?.removeEventListener('abort', abort);
      video.onloadedmetadata = null;
      video.onloadeddata = null;
      video.onerror = null;
      video.removeAttribute('src');
      video.load();
      URL.revokeObjectURL(url);
    };
    const fail = (cause: unknown) => {
      if (settled) return;
      settled = true;
      const error = asAppError(cause, stage, details);
      cleanup();
      reject(error);
    };
    const abort = () => fail(new AppError('MEDIA_ABORTED', 'Video import cancelled.', 'The current session has been kept.', details));
    const timeout = setTimeout(() => fail(new AppError('MEDIA_TIMEOUT',
      stage === 'media.metadata' ? 'Timed out reading video metadata.' : 'Timed out decoding the first frame.',
      'Retry or use a smaller preview. No codec diagnosis was made.',
      { ...details, stage, timeoutMs: options.timeoutMs ?? 20000, readyState: video.readyState, networkState: video.networkState })),
    options.timeoutMs ?? 20000);
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) { abort(); return; }
    video.onerror = () => fail(mediaFailure(video.error, file.name, stage));
    video.onloadedmetadata = () => { stage = 'media.first_frame'; };
    video.onloadeddata = () => {
      if (settled) return;
      try {
        if (!Number.isFinite(video.duration) || video.duration <= 0 || !video.videoWidth || !video.videoHeight) {
          fail(new AppError('MEDIA_METADATA_INVALID', 'The video has no usable duration or picture.',
            'Inspect the source or export a playable preview.', details));
          return;
        }
        let thumbnail = '';
        let thumbnailError: string | undefined;
        try {
          const canvas = document.createElement('canvas');
          const scale = Math.min(320 / video.videoWidth, 180 / video.videoHeight, 1);
          canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
          canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
          const context = canvas.getContext('2d');
          if (!context) throw new Error('Canvas unavailable');
          context.drawImage(video, 0, 0, canvas.width, canvas.height);
          thumbnail = canvas.toDataURL('image/jpeg', 0.75);
        } catch (cause) {
          thumbnailError = cause instanceof Error ? cause.message : String(cause);
        }
        const asset: MediaAsset = {
          id: crypto.randomUUID(), name: file.name, size: file.size, type: file.type,
          duration: video.duration, width: video.videoWidth, height: video.videoHeight,
          thumbnail, ...(thumbnailError ? { thumbnailError } : {}),
        };
        settled = true;
        cleanup();
        resolve(asset);
      } catch (cause) { fail(cause); }
    };
    try { video.src = url; video.load(); } catch (cause) { fail(cause); }
  });
}
