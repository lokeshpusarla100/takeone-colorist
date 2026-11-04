"""The color feature's part of the Service: how a project's source is read, and the person's input profile for it.

`Service` inherits `ColorMixin`; everything that was colour logic inside `service.py` lives here so that feature can grow
(and be read, tested and reviewed) without growing the 2,000-line orchestrator. Nothing here is imported by anything
outside the Service and the color API.
"""
from __future__ import annotations

import base64
import threading

import numpy as np

from ..errors import Problem, safe_problem
from ..media import command, make_previews, probe
from . import domain as color_domain
from . import files as source_color
from . import levels as color_levels
from . import profile as color_profile

PREVIEW_WIDTH = 480


class ColorMixin:
    # Cached pixel evidence of the original and of its proxies. Anything here made before a source was converted
    # (or before its input profile changed) was decoded in the wrong domain and is rebuilt; timing caches
    # (*.frames-v1.json) do not depend on color.
    STALE_WHEN_RECONVERTED = ("source.bin.frames-*x*.npz", "analysis.mp4.frames-*x*.npz", "source-evidence-*.json", "source-review-evidence-*.json",
                              "grade-source-samples.*", "tracking.mp4", "tracking-identity.json")

    # ---- the source's domain -----------------------------------------------------------------------------------

    def adopt_domain(self, state):
        """How this project's source colors are read: the recorded domain, or for a session saved before domains
        existed, resolved from the file now and recorded on `state` (the caller saves it). Raises the specific
        GRADE_DOMAIN_UNSUPPORTED reason when the source cannot be converted honestly."""
        source = state.source
        domain = color_domain.SourceDomain.from_dict(source.domain)
        if domain is None:
            path = self.store.directory(state.id) / "source.bin"
            stream = next(v for v in probe(path, self.settings, threading.Event())["streams"] if v["codec_type"] == "video")
            domain = source_color.domain_for_stream(stream, self.settings)
            source.domain = domain.to_dict()
            source.technical_notes = [*domain.steps] if domain.gradable else [domain.message + " " + domain.action]
        if not domain.gradable:
            raise Problem("GRADE_DOMAIN_UNSUPPORTED", domain.message, domain.action, 422, reason=domain.reason)
        return domain

    def ensure_current_source(self, project_id, cancel):
        """Before any job reads the source: a session saved before color domains existed records how its colors are
        read, and if they need converting its preview and analysis proxy are rebuilt in the converted domain, once.
        The same rebuild repairs proxies that do not match the saved input profile (an interrupted change). A no-op for
        every source prepared by the current code."""
        state = self.store.get(project_id)
        if not state.source or not state.preview:
            return
        if color_domain.SourceDomain.from_dict(state.source.domain) is None:
            self.mutate(project_id, self.adopt_domain)  # records it; raises the specific reason for a source that cannot be converted
            state = self.store.get(project_id)
        domain = color_domain.SourceDomain.from_dict(state.source.domain)
        if not domain.gradable:
            return
        wanted = color_profile.signature(color_profile.read(self.store.directory(project_id) / "source.bin"))
        unconverted = domain.kind in {color_domain.CONVERT, color_domain.TONEMAP} and not state.preview.conformed
        if not unconverted and state.preview.conform_sig == wanted:
            return
        if unconverted:
            self.rebuild_previews(project_id, cancel, "Converting the source to Rec.709",
                                  "This source was prepared before its colors could be converted. Its preview and analysis proxy are rebuilt "
                                  "from the original, which is unchanged.", conversion=domain.kind)
        else:
            self.rebuild_previews(project_id, cancel, "Rebuilding the preview", "The preview did not match the saved input profile, "
                                  "so it is rebuilt from the original, which is unchanged.")

    def rebuild_previews(self, project_id, cancel, title, reason, **details):
        """Rebuild the browser preview and analysis proxy from the original under the source's current domain and saved
        input profile, drop the pixel caches made under the old ones, and record what is now true."""
        directory = self.store.directory(project_id)
        self.mutate(project_id, lambda s: (setattr(s, "stage", "prepare_preview"), self.event(s, title, reason, **details)))
        for pattern in self.STALE_WHEN_RECONVERTED:
            for stale in directory.glob(pattern):
                stale.unlink(missing_ok=True)
        state = self.store.get(project_id)
        preview = make_previews(directory, state.source, project_id, self.settings, cancel)
        saved = color_profile.read(directory / "source.bin")
        domain = color_domain.SourceDomain.from_dict(state.source.domain)

        def done(s):
            s.preview = preview
            s.source.profile = saved.summary() if saved else None
            s.source.technical_notes = [*domain.steps, *(saved.steps if saved else ())]
        self.mutate(project_id, done)
        self.prefetch_frames(directory / "source.bin")

    # ---- the person's input profile ----------------------------------------------------------------------------

    def profile_lock(self, state) -> str | None:
        """Why the input profile can no longer change, or None. Once the footage has been shown to Muse (or anything was
        decided from it) the pictures it saw are part of the result, so changing the colors would make that record false."""
        if not state.source or not state.preview:
            return "The source is not ready yet."
        if state.active_job:
            return "Another operation is running."
        if (state.analysis or state.remote_file_id or state.model_calls or state.rubric or state.brief or state.candidates
                or state.segmentations or state.looks):
            return "This footage has already been analysed with its current colors. Import it again to use a different input profile."
        return None

    def measured_levels(self, state):
        """Levels of this source before any input profile: recorded at import, or measured now (not saved) for a session
        from before they were recorded, as long as its proxy has not been changed by a profile."""
        recorded = color_levels.Levels.from_dict(state.source.levels)
        if recorded is not None:
            return recorded
        if state.source.profile or color_profile.read(self.store.directory(state.id) / "source.bin"):
            return None
        directory = self.store.directory(state.id)
        proxy = directory / "analysis.mp4"
        key = (state.id, proxy.stat().st_mtime_ns if proxy.is_file() else 0)
        cache = self.__dict__.setdefault("_levels_cache", {})
        if key not in cache:
            if len(cache) > 32:
                cache.clear()
            cache[key] = color_levels.measure(proxy, self.settings, threading.Event()) if proxy.is_file() else None
        return cache[key]

    def input_profile_state(self, project_id):
        """Everything the interface needs to offer, explain and show an input profile."""
        state = self.store.get(project_id)
        if not state.source or not state.preview:
            raise Problem("SOURCE_NOT_READY", "The source preview is not ready.", "Wait for source preparation or retry the failed upload.", 409)
        domain = color_domain.SourceDomain.from_dict(state.source.domain)
        ok, why = color_profile.applicable(domain) if domain is not None else (True, "")
        levels = self.measured_levels(state)
        saved = color_profile.read(self.store.directory(project_id) / "source.bin")
        suggest = bool(ok and levels and levels.flat and levels.normalizable and saved is None)
        return {"levels": levels.to_dict() if levels else None, "profile": saved.summary() if saved else None,
                "error": state.source.profile_error, "applicable": ok, "applicable_reason": why,
                "locked": self.profile_lock(state), "suggest": "normalize" if suggest else None}

    def store_candidate_lut(self, project_id, data: bytes, name: str):
        """Validate an uploaded LUT and keep it in the project folder. Nothing changes until the person applies it."""
        state = self.store.get(project_id)
        locked = self.profile_lock(state)
        if locked:
            raise Problem("INPUT_PROFILE_LOCKED", locked, "Import the video again to choose a different input profile.", 409)
        ok, why = color_profile.applicable(color_domain.SourceDomain.from_dict(state.source.domain))
        if not ok:
            raise Problem("INPUT_PROFILE_NOT_APPLICABLE", why, "Remove the LUT.", 422)
        profile, cube = color_profile.lut_profile(data, name)
        color_profile.store_lut(self.store.directory(project_id), data, profile)
        return {"lut_id": profile.lut_sha256[:16], "name": profile.lut_name, **cube.to_dict()}

    def requested_profile(self, state, kind, lut_id, lut_name):
        """The profile a request asks for: None (no profile), or an InputProfile validated against this source."""
        directory = self.store.directory(state.id)
        if kind == "none":
            return None
        if kind == color_profile.NORMALIZE:
            levels = self.measured_levels(state)
            if levels is None:
                raise Problem("INPUT_PROFILE_UNMEASURED", "This clip's levels could not be measured.",
                              "Use a camera LUT instead.", 422)
            return color_profile.normalize(levels.black, levels.white)
        path = directory / f"{color_profile.LUT_PREFIX}{lut_id}{color_profile.LUT_SUFFIX}"
        if not lut_id or not path.is_file():
            raise Problem("INPUT_LUT_MISSING", "That LUT is no longer stored.", "Upload the LUT again.", 409)
        profile, _ = color_profile.lut_profile(path.read_bytes(), lut_name or "lut.cube")
        if profile.lut_file != path.name:
            raise Problem("INPUT_LUT_MISSING", "The stored LUT does not match what was uploaded.", "Upload the LUT again.", 409)
        return profile

    def set_input_profile(self, project_id, kind, lut_id, lut_name, revision, command_id):
        """Apply (or remove) an input profile: re-prepare the preview and analysis proxy under it. Refused once the footage
        has been analysed. A request that changes nothing returns at once."""
        with self.store.lock:
            state = self.store.get(project_id)
            fingerprint = self.fingerprint("input_profile", [kind, lut_id])
            if self.receipt(state, command_id, fingerprint):
                return state
            self.check_revision(state, revision)
            locked = self.profile_lock(state)
            if locked:
                raise Problem("INPUT_PROFILE_LOCKED", locked, "Import the video again to choose a different input profile.", 409)
            domain = self.adopt_domain(state)
            ok, why = color_profile.applicable(domain)
            if not ok:
                raise Problem("INPUT_PROFILE_NOT_APPLICABLE", why, "Keep the footage as it is.", 422)
            wanted = self.requested_profile(state, kind, lut_id, lut_name)
            current = color_profile.read(self.store.directory(project_id) / "source.bin")
            state.receipts[command_id] = fingerprint
            if color_profile.signature(wanted) == color_profile.signature(current):
                self.store.save(state)
                return state
            state.source.profile_error = None
            self.__dict__.setdefault("_profile_requests", {})[project_id] = wanted
            self.event(state, "Input profile chosen" if wanted else "Input profile cleared",
                       wanted.label if wanted else "The footage goes back to being read from its own tags.", actor="user")
            self.submit(state, "reprepare")
            return state

    def run_reprepare(self, project_id, cancel):
        """The job behind set_input_profile. The profile is saved first, so every reader of the original agrees with the
        proxies as soon as they are rebuilt, and put back if the rebuild fails: the project is never left half-changed."""
        wanted = self.__dict__.setdefault("_profile_requests", {}).pop(project_id, None)
        path = self.store.directory(project_id) / "source.bin"
        previous = color_profile.read(path)
        try:
            color_profile.write(path, wanted)
            self.rebuild_previews(project_id, cancel, "Applying the input profile" if wanted else "Removing the input profile",
                                  wanted.label if wanted else "The preview and analysis proxy are rebuilt from the original, which is unchanged.")
            color_profile.prune_luts(self.store.directory(project_id), wanted)
        except Exception as cause:  # noqa: BLE001 - reported on the project, never left as a half-applied change
            try:
                color_profile.write(path, previous)
            except OSError:
                pass
            failure = safe_problem(cause, "reprepare")
            cancelled = failure.code == "JOB_CANCELLED"

            def back(s):
                s.status, s.stage, s.active_job = "ready", "ready", None
                s.source.profile_error = None if cancelled else failure.public()
                self.event(s, "Input profile not applied" if not cancelled else "Input profile change cancelled",
                           "The footage keeps the colors it had." if cancelled else failure.message + " " + failure.action,
                           kind="activity" if cancelled else "error")
            self.mutate(project_id, back)
            return

        def ready(s):
            s.status, s.stage, s.active_job = "ready", "ready", None
            self.event(s, "Input profile applied" if wanted else "Input profile removed",
                       "The preview and analysis proxy were rebuilt. Nothing has been sent to Meta.")
        self.mutate(project_id, ready)

    def measure_source_levels(self, directory, cancel):
        """Levels of the freshly prepared analysis proxy (before any input profile), as a dict, or None."""
        levels = color_levels.measure(directory / "analysis.mp4", self.settings, cancel)
        return levels.to_dict() if levels else None

    def profile_preview(self, project_id, kind, lut_id, lut_name):
        """Two stills of the middle of the clip, as the project reads it now and as it would read under the requested
        profile, with the levels of the second and any reason to doubt it. Nothing is saved."""
        state = self.store.get(project_id)
        if not state.source or not state.preview:
            raise Problem("SOURCE_NOT_READY", "The source preview is not ready.", "Wait for source preparation.", 409)
        domain = self.adopt_domain(state)
        ok, why = color_profile.applicable(domain)
        if not ok:
            raise Problem("INPUT_PROFILE_NOT_APPLICABLE", why, "Keep the footage as it is.", 422)
        path = self.store.directory(project_id) / "source.bin"
        wanted = self.requested_profile(state, kind, lut_id, lut_name)
        before = self.still(path, state, color_profile.conform_chain(domain, color_profile.read(path), path))
        after = self.still(path, state, color_profile.conform_chain(domain, wanted, path))
        luma = self.luma(after)
        levels = color_levels.describe(luma, 1)
        warnings = []
        if levels is not None and wanted is not None:
            if levels.median < 0.08:
                warnings.append("The result is almost black. This LUT may not be made for this camera's footage.")
            elif levels.median > 0.92:
                warnings.append("The result is almost white. This LUT may not be made for this camera's footage.")
            elif levels.span < 0.45:
                warnings.append("The result still looks flat. This LUT may not be made for this camera's footage.")
        return {"before": self.data_url(before), "after": self.data_url(after),
                "after_levels": levels.to_dict() if levels else None, "warnings": warnings}

    def still(self, path, state, chain) -> bytes:
        """One JPEG of the middle of the original, shrunk like the previews and converted by `chain`."""
        graph = (f"scale=w='min({PREVIEW_WIDTH},iw)':h='min({PREVIEW_WIDTH},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1"
                 + (f",{chain},{color_domain.tagged_709()}" if chain else ""))
        return command([self.settings.ffmpeg, "-v", "error", "-nostdin", "-ss", f"{state.source.duration / 2:.3f}", "-i", str(path), "-an",
                        "-vf", graph, "-frames:v", "1", "-pix_fmt", "yuvj420p", "-c:v", "mjpeg", "-q:v", "3", "-f", "image2pipe", "-"],
                       threading.Event(), 60, binary=True)

    @staticmethod
    def luma(jpeg: bytes) -> np.ndarray:
        import cv2
        picture = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if picture is None:
            return np.zeros(0)
        b, g, r = (picture[..., i].astype(np.float64) / 255 for i in range(3))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    @staticmethod
    def data_url(jpeg: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
