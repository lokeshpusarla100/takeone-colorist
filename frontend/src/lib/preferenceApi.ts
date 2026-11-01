import { request } from './api';
export type Scope={kind:'project'|'category'|'general';project_id:string|null;conditions:Record<string,string>};
export type Preference={
  id:string;owner_id:string;profile_id:string;original_wording:string;interpretation:string;dimension:string;
  preference_key:string;scope:Scope;context:Record<string,string>;basis:string;
  status:'tentative'|'confirmed'|'contradicted'|'deleted';revision:number;created_at:string;updated_at:string;
  event_ids:string[];project_ids:string[];question_ids:string[];version_ids:string[];
  independent_projects:number;history:unknown[];
};
export type MemoryView={
  owner_id:string;profile_id:string;name:string;settings:{capture_enabled:boolean;application_enabled:boolean};
  application_available:boolean;preferences:Preference[];events:Record<string,unknown>[];
};
const base=(profile:string)=>'/api/preferences/profiles/'+encodeURIComponent(profile);
export const profiles=()=>request<{id:string;name:string}[]>('/api/preferences/profiles');
export const createProfile=(name:string)=>request<{id:string;name:string}>('/api/preferences/profiles',{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
export const view=(profile:string)=>request<MemoryView>(base(profile));
export const settings=(profile:string,capture:boolean)=>request(base(profile)+'/settings',{
  method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({capture_enabled:capture})});
export const add=(profile:string,body:Record<string,unknown>)=>request(base(profile)+'/entries',{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const edit=(profile:string,id:string,body:Record<string,unknown>)=>request(base(profile)+'/entries/'+id,{
  method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const remove=(profile:string,id:string,revision:number)=>request(base(profile)+'/entries/'+id+'?revision='+revision,{method:'DELETE'});
export {nextProfile,setNextProfile,nextContext,setNextContext} from './preferenceContext';
