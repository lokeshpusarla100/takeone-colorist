import { build } from 'vite';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
const output=await mkdtemp(join(tmpdir(),'takeone-tests-'));
try {
  await build({configFile:false,logLevel:'silent',build:{
    outDir:output,emptyOutDir:true,minify:false,
    lib:{entry:{errors:'src/lib/errors.ts',media:'src/lib/media.ts',storage:'src/lib/storage.ts',types:'src/lib/types.ts',api:'src/lib/api.ts',preparedSource:'src/lib/preparedSource.ts',tokenUsage:'src/lib/tokenUsage.ts',workflow:'src/lib/workflow.ts',progressTiming:'src/lib/progressTiming.ts'},formats:['es'],fileName:(_,name)=>name+'.mjs'},
  }});
  const result=spawnSync(process.execPath,['tests/failures.test.mjs'],{
    stdio:'inherit',env:{...process.env,TAKEONE_TEST_BUILD:output},
  });
  process.exitCode=result.status??1;
} finally { await rm(output,{recursive:true,force:true}); }
