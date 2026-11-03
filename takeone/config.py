from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _rate(name: str, default: float) -> float:
    """A non-negative number from the environment; anything else keeps the published default."""
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 and value == value and value != float("inf") else default


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
    # Critic revisions of sources larger than this (long side, px) render as drafts at this size;
    # the version finally chosen is rendered once in full resolution. 0 disables drafts.
    draft_long_side: int = 1920
    # 'rows' (one object per frame, the verified default) or 'columns' (field names once, same facts):
    # columns needs ~29% fewer JSON tokens; enable only after scripts/critic_layout_ab.py shows equal reviews.
    critic_table_layout: str = 'rows'
    # SAM requests for the subject and each region of one render run concurrently (their time is network
    # latency); 1 restores strictly sequential requests.
    sam_parallel: int = 3
    # Consultation questions: 'batch' asks each round's independent questions together (one call per
    # round); 'one_at_a_time' makes one call per question. Saved sessions keep the mode they started with.
    rubric_mode: str = 'batch'
    # Skip the round-closing call when Muse predicted no follow-up for the options chosen. Off until the
    # recorded predictions (question_proposals) are shown to agree with the closing call.
    rubric_skip_closing: bool = False
    # Single-pass SAM tracking: one request tracks every area of a localized grade instead of one request per area
    # (takeone/sam_single_pass.py). OFF until it has been measured on more footage. A short detection window per area
    # (`window` frames, billed at least 50) seeds one request over the whole range; an area whose tracked masks do not
    # reproduce its detection masks (mean IoU below `gate`) goes back to its own request. Needs at least `min_areas`
    # areas and `min_frames` frames, below which it could not save anything.
    sam_single_pass: bool = False
    sam_single_pass_gate: float = 0.85
    sam_single_pass_window: int = 50
    sam_single_pass_min_frames: int = 150
    sam_single_pass_min_areas: int = 3
    # Meta Model API list prices in USD, used only to ESTIMATE what a video cost from the usage the provider
    # reported (it is not a bill). Source: https://dev.meta.ai/docs/pricing-rate-limits, read 2026-11-03.
    # Cached input is billed at its own rate; reasoning tokens are part of the output count. A SAM video request
    # is billed for at least `sam_min_billed_frames` frames. Override with TAKEONE_PRICE_* if Meta changes them.
    price_muse_input_per_m: float = 1.25
    price_muse_cached_input_per_m: float = 0.15
    price_muse_output_per_m: float = 4.25
    price_sam_per_1k_frames: float = 0.20
    sam_min_billed_frames: int = 50

    def pricing(self) -> dict:
        """The rates the UI uses to turn reported usage into dollars."""
        return {
            "currency": "USD", "basis": "estimate_from_reported_usage",
            "source": "https://dev.meta.ai/docs/pricing-rate-limits", "checked": "2026-11-03",
            "muse": {"model": self.model, "input_per_million": self.price_muse_input_per_m,
                     "cached_input_per_million": self.price_muse_cached_input_per_m,
                     "output_per_million": self.price_muse_output_per_m},
            "sam": {"model": self.sam_model, "per_thousand_frames": self.price_sam_per_1k_frames,
                    "min_billed_frames": self.sam_min_billed_frames},
        }

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
            draft_long_side=max(0,int(os.getenv('TAKEONE_DRAFT_LONG_SIDE','1920'))),
            critic_table_layout='columns' if os.getenv('TAKEONE_CRITIC_TABLE_LAYOUT','rows').strip()=='columns' else 'rows',
            sam_parallel=max(1,min(6,int(os.getenv('TAKEONE_SAM_PARALLEL','3')))),
            rubric_mode='one_at_a_time' if os.getenv('TAKEONE_RUBRIC_MODE','batch').strip()=='one_at_a_time' else 'batch',
            rubric_skip_closing=os.getenv('TAKEONE_RUBRIC_SKIP_CLOSING','').strip().lower() in {'1','true','yes'},
            sam_single_pass=os.getenv('TAKEONE_SAM_SINGLE_PASS','').strip().lower() in {'1','true','yes'},
            sam_single_pass_gate=min(.99,max(.5,_rate('TAKEONE_SAM_SINGLE_PASS_GATE',.85))),
            price_muse_input_per_m=_rate('TAKEONE_PRICE_MUSE_INPUT_PER_M', 1.25),
            price_muse_cached_input_per_m=_rate('TAKEONE_PRICE_MUSE_CACHED_INPUT_PER_M', 0.15),
            price_muse_output_per_m=_rate('TAKEONE_PRICE_MUSE_OUTPUT_PER_M', 4.25),
            price_sam_per_1k_frames=_rate('TAKEONE_PRICE_SAM_PER_1K_FRAMES', 0.20),
            sam_min_billed_frames=int(_rate('TAKEONE_SAM_MIN_BILLED_FRAMES', 50)),
        )
