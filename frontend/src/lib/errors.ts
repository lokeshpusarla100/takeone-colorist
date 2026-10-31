export type ErrorDetails = Record<string, string | number | boolean | null>;

export class AppError extends Error {
  constructor(readonly code: string, message: string, readonly action: string,
    readonly details: ErrorDetails = {}) { super(message); this.name = 'AppError'; }
}

export function asAppError(cause: unknown, stage: string, details: ErrorDetails = {}): AppError {
  if (cause instanceof AppError) return new AppError(cause.code, cause.message, cause.action, { stage, ...details, ...cause.details });
  const name = cause instanceof Error ? cause.name : 'UnknownError';
  const nativeMessage = cause instanceof Error ? cause.message : String(cause);
  const diagnostic = { stage, ...details, nativeName: name, nativeMessage };
  if (name === 'QuotaExceededError') return new AppError('STORAGE_QUOTA', 'Browser storage is full.',
    'Export the log. Free browser storage before importing more footage or retrying the save.', diagnostic);
  if (stage.startsWith('storage')) return new AppError('STORAGE_FAILED', 'Browser storage could not complete the operation.',
    'Keep this page open. Retry saving, or export the session log to keep a copy.', diagnostic);
  return new AppError('OPERATION_FAILED', 'The operation failed.',
    'Retry the operation. Technical details are included in the session log.', diagnostic);
}

export function mediaFailure(error: Pick<MediaError, 'code' | 'message'> | null, file: string, stage: string): AppError {
  const details = { stage, file, browserCode: error?.code ?? 0, browserMessage: error?.message ?? '' };
  if (error?.code === 3 && error.message.includes('PIPELINE_ERROR_DISCONNECTED')) {
    return new AppError('MEDIA_DECODER_DISCONNECTED', 'The browser’s video decoder disconnected.',
      'Reload the page and retry. If it repeats, use a preview encoded with a different codec. This error does not establish that the source file is damaged.',
      details);
  }
  switch (error?.code) {
    case 1: return new AppError('MEDIA_ABORTED', 'Video loading was interrupted.', 'Retry importing the file.', details);
    case 2: return new AppError('MEDIA_READ_FAILED', 'The browser could not read the video.',
      'Check that the local file is accessible, then import it again.', details);
    case 3: return new AppError('MEDIA_DECODE_FAILED', 'The browser failed to decode the video.',
      'The file may be damaged or use an unsupported encoding. Try a compatible preview and keep the original.', details);
    case 4: return new AppError('MEDIA_UNSUPPORTED', 'The browser could not open this media format.',
      'Inspect the browser diagnostic. For a saved local-only source, use Prepare source to obtain a backend preview. The exact codec has not been identified.', details);
    default: return new AppError('MEDIA_LOAD_FAILED', 'The browser could not load the video.',
      'Retry the file or use a browser-compatible preview. See technical details for the browser response.', details);
  }
}
