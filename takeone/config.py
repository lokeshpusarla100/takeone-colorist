from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    workspace: Path
    api_key: str = field(default="", repr=False)
    api_base: str = "https://api.meta.ai/v1"
    model: str = "muse-spark-1.3"
    max_upload_bytes: int = 256 * 1024 * 1024
    max_duration: float = 120
    model_timeout: float = 90
    max_questions: int = 3
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    key_file: Path | None = None
    memory_root: Path | None = None
    # Rollout capability, deliberately not enabled by an environment variable.
    memory_application_available: bool = False
    sam_model: str = "sam-3.1"
    sample_scan_frames: int = 96
    sample_count: int = 8
    sample_width: int = 160
    sample_memory_bytes: int = 16 * 1024 * 1024
    sample_image_bytes: int = 1024 * 1024
    max_render_iterations: int = 3

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env", override=False)
        workspace = Path(os.getenv("TAKEONE_WORKSPACE", str(ROOT / "workspace"))).expanduser()
        if not workspace.is_absolute():
            workspace = ROOT / workspace
        return cls(
            workspace=workspace,
            api_key=os.getenv("MODEL_API_KEY", "").strip(),
            api_base=os.getenv("MODEL_API_BASE", "https://api.meta.ai/v1").rstrip("/"),
            model=os.getenv("MUSE_MODEL", "muse-spark-1.3"),
            max_upload_bytes=int(os.getenv("TAKEONE_MAX_UPLOAD_MB", "256")) * 1024 * 1024,
            max_duration=float(os.getenv("TAKEONE_MAX_DURATION_SECONDS", "120")),
            model_timeout=float(os.getenv("TAKEONE_MODEL_TIMEOUT_SECONDS", "90")),
            max_questions=max(0, min(5, int(os.getenv("TAKEONE_MAX_QUESTIONS", "3")))),
            ffmpeg=shutil.which("ffmpeg") or "ffmpeg",
            ffprobe=shutil.which("ffprobe") or "ffprobe",
            key_file=ROOT / ".env",
            memory_root=ROOT / ".takeone-memory",
            sample_scan_frames=max(8,min(512,int(os.getenv('TAKEONE_SAMPLE_SCAN_FRAMES','96')))),
            sample_count=max(2,min(12,int(os.getenv('TAKEONE_SAMPLE_COUNT','8')))),
            sample_width=max(64,min(256,int(os.getenv('TAKEONE_SAMPLE_WIDTH','160')))),
            sample_memory_bytes=max(1,min(64,int(os.getenv('TAKEONE_SAMPLE_MEMORY_MB','16'))))*1024*1024,
            sample_image_bytes=max(64,min(2048,int(os.getenv('TAKEONE_SAMPLE_IMAGE_KB','1024'))))*1024,
            max_render_iterations=max(1,min(3,int(os.getenv('TAKEONE_MAX_RENDER_ITERATIONS','3')))),
        )
