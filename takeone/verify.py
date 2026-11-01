"""Opt-in, bounded live Muse integration check using generated footage only."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

from takeone_rubric_v1.rubric import Answer
from .config import Settings
from .contracts import State
from .service import Service


def wait(service: Service, project_id: str):
    deadline = time.monotonic() + 300
    last_stage = ""
    while time.monotonic() < deadline:
        state = service.store.get(project_id)
        if state.stage != last_stage:
            print("Stage:", state.stage, flush=True)
            last_stage = state.stage
        if not state.active_job:
            return state
        time.sleep(0.2)
    raise RuntimeError("Live verification deadline exceeded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Opt in to real Meta inference on generated footage only.")
    parser.add_argument('--phase2',action='store_true',help='Also render/review: at most two renders, two critic calls, five total inference calls; no SAM.')
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this check can incur Meta API usage")
    settings = Settings.from_env()
    with tempfile.TemporaryDirectory(prefix="takeone-live-check-") as temporary:
        settings = replace(settings, workspace=Path(temporary) / "workspace", memory_root=Path(temporary)/'preferences',max_questions=1,
                           max_render_iterations=2,sample_count=4,sample_scan_frames=16,sample_image_bytes=256*1024)
        class NoSAM:
            def available(self):return False
            def segment(self,*_):raise RuntimeError('The bounded live verifier forbids SAM requests.')
        service = Service(settings,sam_provider=NoSAM())
        remote_id = None
        try:
            if not service.provider.available():
                raise RuntimeError("MODEL_API_KEY is not configured")
            project_id = str(uuid4())
            directory = service.store.directory(project_id)
            directory.mkdir()
            fixture = Path(temporary) / "synthetic.mp4"
            subprocess.run([
                settings.ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=192x108:rate=12", "-t", "1", "-vf",
                "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(fixture),
            ], check=True)
            shutil.copyfile(fixture, directory / "source.bin")
            with service.store.lock:
                service.submit(State(id=project_id, status="ingesting", stage="inspect_source",
                                     filename="synthetic.mp4", max_questions=1), "ingest")
            state = wait(service, project_id)
            if state.status == "ready":
                service.start(project_id,
                    "Keep this synthetic color pattern natural. Preserve its colors and geometry. "
                    "Only a restrained global contrast adjustment is permitted.",
                    state.revision, str(uuid4()))
                state = wait(service, project_id)
            if state.status == "awaiting_answer":
                service.answer(project_id, Answer(question_id=state.question.id, response_type="delegate"),
                               state.revision, str(uuid4()))
                state = wait(service, project_id)
            remote_id = state.remote_file_id
            if args.phase2 and state.status=='brief_ready':
                service.generate(project_id,state.revision,str(uuid4()));state=wait(service,project_id)
                ready=next((c for c in reversed(state.candidates) if c.status=='ready'),None)
                if ready and not ready.creative_review and not ready.review_error:
                    service.review_candidate(project_id,ready.id,state.revision,str(uuid4()));state=wait(service,project_id)
            print(json.dumps({
                "status": state.status, "model": settings.model, "model_calls": state.model_calls,
                "analysis_summary": state.analysis.summary if state.analysis else None,
                "observation_count": len(state.analysis.observations) if state.analysis else 0,
                "brief_saved": state.brief is not None, "error": state.error,
                'render_count':len(state.candidates),'critic_calls':state.critic_calls,'sam_calls':state.sam_calls,
                'creative_opinions':[c.creative_review.model_dump(mode='json') for c in state.candidates if c.creative_review],
            }, indent=2), flush=True)
            if state.status != ('render_ready' if args.phase2 else 'brief_ready') or not state.analysis or not state.brief:
                return 1
            if args.phase2 and (len(state.candidates)>2 or state.critic_calls>2 or state.sam_calls):
                raise RuntimeError('Phase 2 verification exceeded its declared budget')
            if state.model_calls > (5 if args.phase2 else 2):
                raise RuntimeError("Live check exceeded its call limit")
            return 0
        finally:
            service.close()
            if remote_id:
                try:
                    with service.provider.client() as client:
                        client.files.delete(remote_id)
                    print("Synthetic remote upload deleted.", flush=True)
                except Exception:
                    print("Synthetic remote upload cleanup was not confirmed.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
