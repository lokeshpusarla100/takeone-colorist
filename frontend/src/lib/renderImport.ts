import type { MediaAsset } from './types';
import { AppError } from './errors';

export function validateRenderImport(source:Pick<MediaAsset,'width'|'height'|'duration'>,render:Pick<MediaAsset,'width'|'height'|'duration'>){
  const valid=[source.width,source.height,source.duration,render.width,render.height,render.duration].every(v=>Number.isFinite(v)&&v>0);
  const sameSize=render.width===source.width&&render.height===source.height;
  const scale=Math.min(render.width/source.width,render.height/source.height);
  // Permit proportional downscales, including our encoder's even-pixel rounding.
  // Geometry compatibility does not establish identity or grading provenance.
  const scaled=render.width<=source.width&&render.height<=source.height
    &&Math.abs(render.width-source.width*scale)<=2&&Math.abs(render.height-source.height*scale)<=2
    &&Math.abs(render.width/render.height-source.width/source.height)/(source.width/source.height)<=.01;
  if(!valid||(!sameSize&&!scaled)||Math.abs(render.duration-source.duration)>.15){
    throw new AppError('RENDER_MISMATCH','This imported video does not match the source framing or duration.',
      'Use the full-resolution render or a proportionally downscaled preview with matching duration.',
      {sourceWidth:source.width,sourceHeight:source.height,renderWidth:render.width,renderHeight:render.height,
        sourceDuration:source.duration,renderDuration:render.duration});
  }
  return {previewOnly:!sameSize};
}
