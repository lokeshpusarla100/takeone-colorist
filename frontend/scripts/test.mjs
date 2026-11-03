import { build } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
const output=await mkdtemp(join(tmpdir(),'takeone-tests-'));
try {
  await build({configFile:false,logLevel:'silent',build:{
    outDir:output,emptyOutDir:true,minify:false,
    lib:{entry:{errors:'src/lib/errors.ts',media:'src/lib/media.ts',storage:'src/lib/storage.ts',types:'src/lib/types.ts',api:'src/lib/api.ts',preparedSource:'src/lib/preparedSource.ts',tokenUsage:'src/lib/tokenUsage.ts',workflow:'src/lib/workflow.ts',progressTiming:'src/lib/progressTiming.ts',progressModel:'src/lib/progressModel.ts',autoRender:'src/lib/autoRender.ts',playbackSync:'src/lib/playbackSync.ts',renderImport:'src/lib/renderImport.ts',adjustment:'src/lib/adjustment.ts',cost:'src/lib/cost.ts',briefVisual:'src/lib/briefVisual.ts',markdown:'src/lib/markdown.ts',recipeView:'src/lib/recipeView.ts',stepper:'src/lib/stepper.ts',sourceColor:'src/lib/sourceColor.ts'},formats:['es'],fileName:(_,name)=>name+'.mjs'},
  }});
  const result=spawnSync(process.execPath,['tests/failures.test.mjs'],{
    stdio:'inherit',env:{...process.env,TAKEONE_TEST_BUILD:output},
  });
  process.exitCode=result.status??1;
} finally { await rm(output,{recursive:true,force:true}); }
