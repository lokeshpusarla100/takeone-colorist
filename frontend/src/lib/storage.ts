import type { Project } from './types';
import { AppError, asAppError } from './errors';
import { parseBackend } from './api';
const ACTIVE_KEY = 'take-one.active-session.v1';
let database: Promise<IDBDatabase> | undefined;

function openDatabase(): Promise<IDBDatabase> {
  if (database) return database;
  const pending = new Promise<IDBDatabase>((resolve, reject) => {
    let settled = false;
    const fail = (cause: unknown) => {
      if (settled) return;
      settled = true; clearTimeout(timer);
      reject(asAppError(cause, 'storage.open'));
    };
    const timer = setTimeout(() => fail(new AppError('STORAGE_TIMEOUT', 'Opening browser storage timed out.',
      'Close other Take One tabs and retry.')), 10000);
    try {
      const request = indexedDB.open('take-one-workspace', 1);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains('projects')) request.result.createObjectStore('projects', { keyPath: 'id' });
        if (!request.result.objectStoreNames.contains('media')) request.result.createObjectStore('media');
      };
      request.onsuccess = () => {
        const db = request.result;
        if (settled) { db.close(); return; }
        settled = true; clearTimeout(timer);
        db.onversionchange = () => { db.close(); database = undefined; };
        db.onclose = () => { database = undefined; };
        resolve(db);
      };
      request.onerror = () => fail(request.error);
      request.onblocked = () => fail(new AppError('STORAGE_BLOCKED', 'Browser storage is blocked by another tab.',
        'Close other Take One tabs and retry.'));
    } catch (cause) { fail(cause); }
  });
  database = pending;
  void pending.catch(() => { if (database === pending) database = undefined; });
  return pending;
}

export function validateProject(value: unknown): asserts value is Project {
  const obj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;
  const text = (v: unknown) => typeof v === 'string';
  const date = (v: unknown) => text(v) && Number.isFinite(Date.parse(v as string));
  const num = (v: unknown) => typeof v === 'number' && Number.isFinite(v);
  const recipe = (v: unknown) => obj(v) && ['exposure','contrast','saturation','red','green','blue'].every(k => num(v[k]));
  const asset = (v: unknown) => obj(v) && text(v.id) && text(v.name) && num(v.size) && (v.size as number) >= 0 && text(v.type)
    && num(v.width) && (v.width as number) > 0 && num(v.height) && (v.height as number) > 0
    && num(v.duration) && (v.duration as number) > 0 && text(v.thumbnail)
    && (v.playbackUrl===undefined || (typeof v.playbackUrl==='string'
      && /^\/api\/projects\/[0-9a-f-]{36}\/(?:candidates\/[0-9a-f-]{36}\/)?preview$/i.test(v.playbackUrl)));
  const valid = obj(value) && value.schemaVersion === 1 && text(value.id) && text(value.name)
    && date(value.createdAt) && date(value.updatedAt) && text(value.direction) && recipe(value.recipe)
    && (value.source === null || asset(value.source)) && Array.isArray(value.assets) && value.assets.every(asset)
    && Array.isArray(value.versions) && value.versions.every(v => obj(v) && text(v.id) && text(v.name)
      && ['source','draft','rendered'].includes(v.kind as string) && date(v.createdAt)
      && (v.previewOnly===undefined||typeof v.previewOnly==='boolean')
      && (v.recipe === null || recipe(v.recipe)))
    && (value.selectedVersionId === null || text(value.selectedVersionId))
    && Array.isArray(value.events) && value.events.every(e => obj(e) && text(e.id) && date(e.timestamp)
      && text(e.title) && ['app','user','muse','renderer'].includes(e.actor as string)
      && ['activity','decision','error'].includes(e.kind as string)
      && (e.reason === undefined || text(e.reason))
      && (e.details === undefined || (obj(e.details) && Object.values(e.details).every(v => v === null || text(v) || num(v) || typeof v === 'boolean'))));
  if (!valid) throw new AppError('STORAGE_CORRUPT', 'A saved session contains invalid or unsupported data.',
    'The saved record has been preserved. Retry or start a new session.');
  try{
    if(value.backend!==undefined)parseBackend(value.backend);
    if(value.pendingBackend!==undefined)parseBackend(value.pendingBackend);
    if(value.pendingAttach!==undefined&&typeof value.pendingAttach!=='boolean')throw new Error('Invalid import state');
  }catch{
    throw new AppError('STORAGE_CORRUPT','Saved backend state is invalid.',
      'The record has been preserved. Reopen a valid session or prepare the source again.');
  }
  const p = value as Project;
  const assetIds = new Set(p.assets.map(a => a.id));
  const versionIds = new Set(p.versions.map(v => v.id));
  if (assetIds.size !== p.assets.length || versionIds.size !== p.versions.length
    || (p.source && !assetIds.has(p.source.id))
    || (p.selectedVersionId && !versionIds.has(p.selectedVersionId))
    || p.versions.some(v => v.kind !== 'draft' && (!v.assetId || !assetIds.has(v.assetId)))) {
    throw new AppError('STORAGE_CORRUPT', 'The saved session has broken media or version references.',
      'The saved record has been preserved. Retry or start a new session.', { projectId:p.id });
  }
}

async function read<T>(store: string, key?: string): Promise<T> {
  try {
    const db = await openDatabase();
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(store, 'readonly');
      const request = key ? tx.objectStore(store).get(key) : tx.objectStore(store).getAll();
      tx.oncomplete = () => resolve(request.result as T);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error ?? new Error('Read transaction aborted'));
    });
  } catch (cause) { throw asAppError(cause, 'storage.read', { store, key: key ?? 'all' }); }
}
async function write(project: Project, media?: { id: string; blob: Blob }, previous?: Project): Promise<void> {
  validateProject(project);
  if(previous)validateProject(previous);
  try {
    const db = await openDatabase();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(media ? ['projects','media'] : ['projects'], 'readwrite');
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error ?? new Error('Save transaction aborted'));
      try {
        if (media) tx.objectStore('media').put(media.blob, media.id);
        if (previous && previous.id!==project.id) tx.objectStore('projects').put(previous);
        tx.objectStore('projects').put(project);
      } catch (cause) { tx.abort(); reject(cause); }
    });
  } catch (cause) { throw asAppError(cause, 'storage.save', { projectId: project.id }); }
}
export const saveProject = (project: Project) => write(project);
export const savePreparedSource = (project: Project, previous?: Project) => write(project, undefined, previous);
export const saveImport = (project: Project, id: string, blob: Blob, previous?:Project) => write(project, { id, blob },previous);
export async function getProject(id: string) {
  const value = await read<unknown>('projects', id);
  if (value === undefined) return undefined;
  validateProject(value);
  return value;
}
export async function getProjects() {
  const values = await read<unknown[]>('projects');
  const projects: Project[] = [];
  const errors: AppError[] = [];
  for (const value of values) {
    try { validateProject(value); projects.push(value); }
    catch (cause) { errors.push(asAppError(cause, 'storage.list')); }
  }
  return { projects: projects.sort((a,b) => b.updatedAt.localeCompare(a.updatedAt)), errors };
}
export const getMedia = (id: string) => read<Blob | undefined>('media', id);
export function getActiveId(): string | null {
  try { return localStorage.getItem(ACTIVE_KEY); } catch { return null; }
}
/** Forget which session opens first; every saved session record stays in IndexedDB. */
export function clearActiveId(): void {
  try { localStorage.removeItem(ACTIVE_KEY); } catch { /* A new session simply opens next time. */ }
}
export function setActiveId(id: string): void {
  try { localStorage.setItem(ACTIVE_KEY, id); } catch { /* Project records remain in IndexedDB. */ }
}
