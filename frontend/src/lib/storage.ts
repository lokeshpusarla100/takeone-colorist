import type { Project } from './types';

const ACTIVE_KEY = 'take-one.active-session.v1';
let database: Promise<IDBDatabase> | undefined;

function openDatabase(): Promise<IDBDatabase> {
  if (!database) {
    database = new Promise((resolve, reject) => {
      const request = indexedDB.open('take-one-workspace', 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('projects', { keyPath: 'id' });
        request.result.createObjectStore('media');
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('Local storage is blocked by another browser tab.'));
    });
  }
  return database;
}

async function read<T>(store: string, key?: string): Promise<T> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readonly');
    const request = key ? tx.objectStore(store).get(key) : tx.objectStore(store).getAll();
    request.onsuccess = () => resolve(request.result as T);
    request.onerror = () => reject(request.error);
  });
}

async function write(store: string, value: unknown, key?: string): Promise<void> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(store, 'readwrite');
    const objectStore = tx.objectStore(store);
    if (key) objectStore.put(value, key);
    else objectStore.put(value);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('Local save was interrupted.'));
  });
}

export const saveProject = (project: Project) => write('projects', project);
export const getProject = (id: string) => read<Project | undefined>('projects', id);
export const getProjects = async () =>
  (await read<Project[]>('projects')).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
export const saveMedia = (id: string, blob: Blob) => write('media', blob, id);
export const getMedia = (id: string) => read<Blob | undefined>('media', id);

export function getActiveId(): string | null {
  try { return localStorage.getItem(ACTIVE_KEY); } catch { return null; }
}

export function setActiveId(id: string): void {
  try { localStorage.setItem(ACTIVE_KEY, id); } catch { /* The project still lives in IndexedDB. */ }
}
