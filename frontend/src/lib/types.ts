export type Recipe = {
  exposure: number;
  contrast: number;
  saturation: number;
  red: number;
  green: number;
  blue: number;
};

export const NEUTRAL_RECIPE: Recipe = {
  exposure: 0, contrast: 1, saturation: 1, red: 1, green: 1, blue: 1,
};

export type MediaAsset = {
  id: string;
  name: string;
  size: number;
  type: string;
  duration: number;
  width: number;
  height: number;
  thumbnail: string;
  thumbnailError?: string;
};

export type Version = {
  id: string;
  name: string;
  kind: 'source' | 'draft' | 'rendered';
  createdAt: string;
  assetId?: string;
  parentId?: string;
  recipe: Recipe | null;
};

export type SessionEvent = {
  id: string;
  timestamp: string;
  actor: 'app' | 'user' | 'muse' | 'renderer';
  kind: 'activity' | 'decision' | 'error';
  title: string;
  reason?: string;
  versionId?: string;
  details?: Record<string, string | number | boolean | null>;
};

export type Project = {
  schemaVersion: 1;
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  direction: string;
  recipe: Recipe;
  source: MediaAsset | null;
  assets: MediaAsset[];
  versions: Version[];
  selectedVersionId: string | null;
  events: SessionEvent[];
  backend?: BackendState;
  pendingBackend?: BackendState;
  pendingAttach?: boolean;
};

export function makeEvent(event: Omit<SessionEvent, 'id' | 'timestamp'>): SessionEvent {
  return { ...event, id: crypto.randomUUID(), timestamp: new Date().toISOString() };
}

export function makeProject(): Project {
  const now = new Date().toISOString();
  return {
    schemaVersion: 1, id: crypto.randomUUID(), name: 'Untitled session',
    createdAt: now, updatedAt: now, direction: '', recipe: { ...NEUTRAL_RECIPE },
    source: null, assets: [], versions: [], selectedVersionId: null,
    events: [makeEvent({
      actor: 'app', kind: 'activity', title: 'Workspace created',
      reason: 'Session activity is saved locally.',
    })],
  };
}

export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '00:00';
  const whole = Math.floor(seconds);
  return String(Math.floor(whole / 60)).padStart(2, '0') + ':' + String(whole % 60).padStart(2, '0');
}

export function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024
    ? (bytes / 1024).toFixed(0) + ' KB'
    : (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
import type { BackendState } from './api';
