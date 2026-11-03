import asyncio
from contextlib import asynccontextmanager
import os
import json
from pathlib import Path
import shutil
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from takeone_rubric_v1.rubric import Answer, StrictModel
from .config import Settings
from .contracts import Event, State
from .errors import Problem, safe_problem
from .service import Service
from .preference_api import install_preferences
from .preferences import context_values


class Command(StrictModel):
    command_id: UUID
    revision: int = Field(ge=0)


class StartCommand(Command):
    request: str = Field(max_length=16000)


class AnswerCommand(Command):
    answer: Answer

class AnswersCommand(Command):
    answers: list[Answer] = Field(min_length=1, max_length=5)

class SegmentCommand(Command):
    concept: str = Field(min_length=1,max_length=120)
    duration_seconds: float | None = Field(default=None,ge=1,le=120)

class ReviewCommand(Command):
    candidate_id: UUID

class SelectLookCommand(Command):
    look_id: UUID


def create_app(settings: Settings | None = None, provider=None, sam_provider=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application):
        application.state.service = Service(settings, provider,sam_provider)
        yield
        await asyncio.to_thread(application.state.service.close)

    application = FastAPI(title="Take One local backend", lifespan=lifespan)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @application.middleware("http")
    async def local_requests(request: Request, call_next):
        from urllib.parse import urlsplit
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            return JSONResponse(status_code=403, content={"error": Problem(
                "ORIGIN_REJECTED", "This local API does not accept requests from that website.",
                "Open the local Take One editor.", 403).public()})
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > settings.max_upload_bytes + 1024 * 1024):
            return JSONResponse(status_code=413, content={"error": Problem(
                "UPLOAD_LIMIT", "The upload exceeds the configured size limit.",
                "Use a smaller clip.", 413).public()})
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(Problem)
    async def problem_handler(request: Request, problem: Problem):
        body = problem.public()
        body["details"]["request_id"] = getattr(request.state, "request_id", "")
        return JSONResponse(status_code=problem.status, content={"error": body})

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": Problem(
            "REQUEST_INVALID", "The request did not match the API contract.",
            "Refresh the project and submit the displayed form again.", 422,
            fields="; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in error.errors())
        ).public()})

    @application.exception_handler(Exception)
    async def unexpected_handler(request: Request, error: Exception):
        problem = safe_problem(error, "api")
        return JSONResponse(status_code=problem.status, content={"error": problem.public()})

    def service() -> Service:
        return application.state.service

    install_preferences(application,service)

    @application.get("/api/health")
    def health():
        return {
            "status": "ok", "model": settings.model, "muse_configured": service().provider.available(),
            "ffmpeg_available": bool(shutil.which(settings.ffmpeg)),
            "ffprobe_available": bool(shutil.which(settings.ffprobe)),
            "max_upload_bytes": settings.max_upload_bytes,
            "max_duration_seconds": settings.max_duration,
            "max_questions": settings.max_questions,
            "grading_connected": True,
            "look_previews_connected": True,
            "native_mask_timing_connected": True,
            "sam_model":settings.sam_model,"segmentation_connected":True,
        }

    @application.post("/api/projects", status_code=202)
    async def upload(file: UploadFile = File(...),profile_id:str=Form("default"),
                     preference_context:str=Form("{}"),use_preferences:bool=Form(False)):
        project_id = str(uuid4())
        try:
            context = context_values(json.loads(preference_context))
        except (ValueError,TypeError,AttributeError):
            raise Problem("MEMORY_CONTEXT_INVALID","Invalid editing context.","Use category, scene and purpose labels.",422)
        prefs=service().preferences
        binding=prefs.bind_new_project(profile_id,project_id,context,use_preferences) if prefs else None
        if prefs is None and (profile_id!="default" or use_preferences):
            service().require_memory()
        directory = service().store.directory(project_id)
        directory.mkdir(parents=True, exist_ok=False)
        temporary = directory / "source.upload"
        size = 0
        try:
            with temporary.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise Problem("UPLOAD_LIMIT", "The upload exceeds the configured size limit.",
                                      "Use a smaller clip.", 413)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise Problem("MEDIA_EMPTY", "The uploaded file is empty.", "Choose a video containing data.", 422)
            temporary.replace(directory / "source.bin")
            state = State(id=project_id, status="ingesting", stage="inspect_source",
                          filename=Path((file.filename or "video").replace("\\", "/")).name[:255],
                          max_questions=settings.max_questions,
                          owner_id=prefs.owner_id if prefs else None,profile_id=profile_id,
                          preference_context=context,memory_binding=binding,
                          events=[Event(title="Source uploaded", details={"bytes": size})])
            with service().store.lock:
                service().submit(state, "ingest")
            return state.public()
        except BaseException:
            temporary.unlink(missing_ok=True)
            # Preserve finalized source.bin if processing failed; remove only incomplete uploads.
            if not (directory / "source.bin").exists():
                directory.rmdir()
            raise
        finally:
            await file.close()

    @application.get("/api/projects/{project_id}")
    def get_state(project_id: UUID):
        return service().store.get(str(project_id)).public()

    @application.get("/api/projects/{project_id}/progress")
    def get_progress(project_id: UUID):
        return {"live": service().live_progress(str(project_id))}

    @application.get("/api/projects/{project_id}/preview")
    def get_preview(project_id: UUID):
        state = service().store.get(str(project_id))
        path = service().store.directory(str(project_id)) / "preview.webm"
        if not state.preview or not path.is_file():
            raise Problem("PREVIEW_NOT_READY", "The preview is not ready.", "Wait or retry source preparation.", 409)
        return FileResponse(path, media_type="video/webm")

    @application.get("/api/projects/{project_id}/analysis-proxy")
    def get_analysis_proxy(project_id: UUID):
        state = service().store.get(str(project_id))
        path = service().store.directory(str(project_id)) / "analysis.mp4"
        if not state.preview or not path.is_file():
            raise Problem("PREVIEW_NOT_READY", "The analysis proxy is not ready.", "Retry source preparation.", 409)
        return FileResponse(path, media_type="video/mp4")

    @application.get("/api/projects/{project_id}/source")
    def get_original(project_id: UUID):
        state = service().store.get(str(project_id))
        path = service().store.directory(str(project_id)) / "source.bin"
        if not path.is_file():
            raise Problem("SOURCE_MISSING", "The original file is missing.", "Re-upload the source.", 404)
        return FileResponse(path, media_type="application/octet-stream", filename=state.filename)

    @application.post("/api/projects/{project_id}/analyze", status_code=202)
    def start(project_id: UUID, body: StartCommand):
        return service().start(str(project_id), body.request, body.revision, str(body.command_id)).public()

    @application.post("/api/projects/{project_id}/answers", status_code=202)
    def answer(project_id: UUID, body: AnswerCommand):
        return service().answer(str(project_id), body.answer, body.revision, str(body.command_id)).public()

    @application.post("/api/projects/{project_id}/answers/round", status_code=202)
    def answer_round(project_id: UUID, body: AnswersCommand):
        return service().answer_all(str(project_id), body.answers, body.revision, str(body.command_id)).public()

    @application.post("/api/projects/{project_id}/retry", status_code=202)
    def retry(project_id: UUID, body: Command):
        return service().retry(str(project_id), body.revision, str(body.command_id)).public()

    @application.post("/api/projects/{project_id}/cancel", status_code=202)
    def cancel(project_id: UUID):
        return service().cancel(str(project_id)).public()

    @application.post("/api/projects/{project_id}/looks", status_code=202)
    def generate_looks(project_id: UUID, body: Command):
        return service().request_looks(str(project_id), body.revision, str(body.command_id)).public()

    @application.post("/api/projects/{project_id}/looks/select")
    def select_look(project_id: UUID, body: SelectLookCommand):
        return service().select_look(str(project_id), str(body.look_id), body.revision, str(body.command_id)).public()

    @application.get("/api/projects/{project_id}/looks/{look_id}/image")
    def look_image(project_id: UUID, look_id: UUID):
        state = service().store.get(str(project_id))
        look = next((item for item in state.looks if item.id == str(look_id)), None)
        path = service().store.directory(str(project_id)) / "looks" / (str(look_id) + ".jpg")
        if not look or not path.is_file():
            raise Problem("LOOK_NOT_READY", "That look preview is not available.", "Generate look previews first.", 409)
        return FileResponse(path, media_type="image/jpeg")

    @application.post("/api/projects/{project_id}/render", status_code=202)
    def generate(project_id: UUID, body: Command):
        return service().generate(str(project_id), body.revision, str(body.command_id)).public()

    @application.post('/api/projects/{project_id}/render-full',status_code=202)
    def render_full(project_id:UUID,body:ReviewCommand):
        return service().render_full(str(project_id),str(body.candidate_id),body.revision,str(body.command_id)).public()

    @application.post('/api/projects/{project_id}/use-all-matches',status_code=202)
    def use_all_matches(project_id:UUID,body:ReviewCommand):
        return service().use_all_matches(str(project_id),str(body.candidate_id),body.revision,str(body.command_id)).public()

    @application.post('/api/projects/{project_id}/render-supported',status_code=202)
    def render_supported(project_id:UUID,body:ReviewCommand):
        return service().render_supported(str(project_id),str(body.candidate_id),body.revision,str(body.command_id)).public()

    @application.post('/api/projects/{project_id}/review',status_code=202)
    def review(project_id:UUID,body:ReviewCommand):
        return service().review_candidate(str(project_id),str(body.candidate_id),body.revision,str(body.command_id)).public()

    @application.post('/api/projects/{project_id}/segment',status_code=202)
    def segment(project_id:UUID,body:SegmentCommand):
        return service().segment(str(project_id),body.concept,body.revision,str(body.command_id),body.duration_seconds).public()

    def segmentation_file(project_id,run_id,filename):
        state=service().store.get(str(project_id))
        run=next((r for r in state.segmentations if r.id==str(run_id)),None)
        path=service().store.directory(str(project_id))/'segmentations'/str(run_id)/filename
        if not run or run.status not in {'ready','empty'} or not path.is_file():
            raise Problem('SAM_RESULT_NOT_READY','This mask result is not available.','Wait for completion or inspect its saved error.',409)
        return path

    @application.get('/api/projects/{project_id}/segmentations/{run_id}/preview')
    def mask_preview(project_id:UUID,run_id:UUID):
        return FileResponse(segmentation_file(project_id,run_id,'overlay.webm'),media_type='video/webm')

    @application.get('/api/projects/{project_id}/segmentations/{run_id}/manifest')
    def mask_manifest(project_id:UUID,run_id:UUID):
        return FileResponse(segmentation_file(project_id,run_id,'manifest.json'),media_type='application/json')

    def candidate_file(project_id: UUID, candidate_id: UUID, filename: str):
        state = service().store.get(str(project_id))
        candidate = next((c for c in state.candidates if c.id == str(candidate_id)), None)
        path = service().store.directory(str(project_id)) / "candidates" / str(candidate_id) / filename
        if not candidate or candidate.status != "ready" or not path.is_file():
            raise Problem("CANDIDATE_NOT_READY", "The requested candidate is not available.",
                          "Wait for validation or inspect its saved failure.", 409)
        return path, candidate

    @application.get("/api/projects/{project_id}/candidates/{candidate_id}/preview")
    def candidate_preview(project_id: UUID, candidate_id: UUID):
        path, _ = candidate_file(project_id, candidate_id, "preview.webm")
        return FileResponse(path, media_type="video/webm")

    @application.get("/api/projects/{project_id}/candidates/{candidate_id}/download")
    def candidate_download(project_id: UUID, candidate_id: UUID):
        path, candidate = candidate_file(project_id, candidate_id, "grade.mp4")
        return FileResponse(path, media_type="video/mp4", filename=candidate.name + "-graded.mp4")

    @application.get("/api/projects/{project_id}/brief")
    def get_brief(project_id: UUID):
        state = service().store.get(str(project_id))
        if not state.brief:
            raise Problem("BRIEF_NOT_READY", "The creative brief is not ready.", "Complete the pending consultation.", 409)
        return state.brief.model_dump(mode="json")

    return application


app = create_app()
