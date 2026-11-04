import { AppError } from '../../lib/errors';
import { parseBackend, request } from '../../lib/api';
import type { BackendState } from '../../lib/api';
import { parseInfo, parseLut, parseStills } from './inputProfile';
import type { Choice, LutInfo, ProfileInfo, StillPair } from './inputProfile';

/** Calls for the input profile (takeone/color/api.py). An answer that does not match the contract is an error, never a guess. */
const base = (id: string) => '/api/projects/' + id + '/input-profile';
const json = (body: unknown): RequestInit => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
function unreadable(): never {
  throw new AppError('API_RESPONSE_INVALID', 'The backend returned an input-profile answer this page cannot read.', 'Restart the backend from this checkout and refresh the page.');
}
const wire = (choice: Choice) => ({ kind: choice.kind, ...(choice.kind === 'lut' ? { lut_id: choice.lut_id, lut_name: choice.lut_name } : {}) });

export async function getInfo(id: string, signal?: AbortSignal): Promise<ProfileInfo> {
  return parseInfo(await request<unknown>(base(id), { signal })) ?? unreadable();
}

export async function uploadLut(id: string, file: File, signal?: AbortSignal): Promise<LutInfo> {
  const form = new FormData();
  form.append('file', file);
  return parseLut(await request<unknown>(base(id) + '/lut', { method: 'POST', body: form, signal }, 60000)) ?? unreadable();
}

export async function stills(id: string, choice: Choice, signal?: AbortSignal): Promise<StillPair> {
  return parseStills(await request<unknown>(base(id) + '/preview', { method: 'POST', ...json(wire(choice)), signal }, 60000)) ?? unreadable();
}

export async function setProfile(id: string, revision: number, choice: Choice): Promise<BackendState> {
  return parseBackend(await request<unknown>(base(id), { method: 'PUT', ...json({ ...wire(choice), revision, command_id: crypto.randomUUID() }) }));
}
