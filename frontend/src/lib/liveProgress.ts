import { useEffect, useState } from 'react';
import { activeJob } from './api';
import type { BackendState } from './api';
import { isEncoding, parseLive } from './progressModel';
import type { Live } from './progressModel';

/**
 * The measured position of the encode while it runs (polled once a second, only then). A backend without the
 * endpoint, a failed request or a malformed answer is simply "no live measurement"; the bar then shows elapsed time.
 */
export function useLiveProgress(state?:BackendState,online=true):Live|null{
  const [live,setLive]=useState<Live|null>(null);
  const id=state?.id;const stage=state?.stage;
  const watching=!!state&&online&&activeJob(state)&&isEncoding(stage);
  useEffect(()=>{
    setLive(null);
    if(!id||!watching)return;
    const aborter=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
    const poll=async()=>{
      try{
        const response=await fetch('/api/projects/'+id+'/progress',{signal:aborter.signal,credentials:'same-origin'});
        if(response.ok){const body=await response.json() as {live?:unknown};if(!aborter.signal.aborted)setLive(parseLive(body?.live));}
      }catch{/* no live measurement */}
      if(!aborter.signal.aborted)timer=setTimeout(()=>void poll(),1000);
    };
    void poll();
    return()=>{aborter.abort();clearTimeout(timer);};
  },[id,watching,stage]);
  return live;
}
