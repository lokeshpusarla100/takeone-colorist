import type { BackendState } from './api';
import type { Project, MediaAsset, Version } from './types';

export function withRenderedVersions(project:Project,state:BackendState):Project {
  const assets=[...project.assets],versions=[...project.versions];
  for(const candidate of state.candidates??[]){
    if(candidate.status!=='ready'||!candidate.preview_url||!candidate.download_url
      ||!candidate.width||!candidate.height||!candidate.duration)continue;
    const id='candidate:'+candidate.id;
    if(!assets.some(a=>a.id===id)){
      const asset:MediaAsset={id,name:candidate.name+'-graded.mp4',type:'video/webm',
        width:candidate.width,height:candidate.height,duration:candidate.duration,
        size:candidate.validation?.bytes??0,thumbnail:'',playbackUrl:candidate.preview_url};
      assets.push(asset);
    }
    if(!versions.some(v=>v.id===id)){
      const version:Version={id,name:candidate.name,kind:'rendered',createdAt:candidate.created_at,
        assetId:id,recipe:null,candidateId:candidate.id,downloadUrl:candidate.download_url,
        parentId:versions.find(v=>v.kind==='source')?.id};
      versions.push(version);
    }
  }
  let selectedVersionId=project.selectedVersionId;
  if(state.selected_candidate_id&&state.selected_candidate_id!==project.backend?.selected_candidate_id){
    const candidate=versions.find(v=>v.candidateId===state.selected_candidate_id);
    if(candidate)selectedVersionId=candidate.id;
  }
  return assets.length===project.assets.length&&versions.length===project.versions.length&&selectedVersionId===project.selectedVersionId
    ? project : {...project,assets,versions,selectedVersionId};
}
