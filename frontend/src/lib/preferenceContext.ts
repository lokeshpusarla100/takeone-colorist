const PROFILE='take-one.preference-profile.v1',CONTEXT='take-one.preference-context.v1';
export function nextProfile(){try{return localStorage.getItem(PROFILE)||'default';}catch{return'default';}}
export function setNextProfile(id:string){try{localStorage.setItem(PROFILE,id);}catch{/* Server profile remains durable. */}}
export function nextContext():Record<string,string>{
  try{const value=JSON.parse(localStorage.getItem(CONTEXT)||'{}');
    return Object.fromEntries(['category','scene','purpose'].filter(k=>typeof value[k]==='string').map(k=>[k,value[k]]));
  }catch{return{};}
}
export function setNextContext(context:Record<string,string>){
  try{localStorage.setItem(CONTEXT,JSON.stringify(context));}catch{/* Optional upload defaults. */}
}
