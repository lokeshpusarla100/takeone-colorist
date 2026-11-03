import type { BackendState, Health } from './api';

const KEY='takeone.autoRender';
/** On by default. Browser storage is a per-viewer convenience: if it is unavailable the default applies. */
export function loadAutoRender():boolean{try{return localStorage.getItem(KEY)!=='off';}catch{return true;}}
export function saveAutoRender(on:boolean){try{localStorage.setItem(KEY,on?'on':'off');}catch{/* the choice just is not remembered */}}

/** Seeing a consultation in progress (questions being asked, or Muse thinking) is what arms the automatic render. */
export const consultationRunning=(state?:BackendState|null)=>!!state&&(state.status==='analyzing'||state.status==='awaiting_answer');

export const autoRenderKey=(state:BackendState)=>state.id+':'+state.brief_sha256;
/** One automatic start per brief; a rejected request may be tried once more, never in a loop. */
export const MAX_AUTO_ATTEMPTS=2;

export type AutoRenderInput={
  state?:BackendState|null;
  /** This tab watched this project's consultation run; a session merely opened or reloaded at "brief ready" never renders by itself. */
  armed:boolean;enabled:boolean;online:boolean;health:Health|null;
  /** The text in the direction box; an edited, unsaved direction is not what the brief was made from. */
  direction:string;attempts:number;busy:boolean;
};

/** Start the first grade once the consultation has produced its brief, exactly when the Generate button would be usable. */
export function shouldAutoRender(i:AutoRenderInput):boolean{
  const state=i.state;
  if(!state||!i.armed||!i.enabled||!i.online||i.busy)return false;
  if(state.status!=='brief_ready'||!state.brief||!state.brief_sha256)return false;
  if(!i.health?.muse_configured||!i.health.grading_connected)return false;
  if(i.direction.trim()!==state.request)return false;
  if(i.attempts>=MAX_AUTO_ATTEMPTS)return false;
  return !state.candidates?.some(c=>c.brief_sha256===state.brief_sha256);
}
