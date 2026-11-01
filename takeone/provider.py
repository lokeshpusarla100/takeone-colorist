from pathlib import Path
from collections.abc import Callable

import openai
from openai import OpenAI
from pydantic import ValidationError
from dotenv import dotenv_values

from takeone_rubric_v1.muse_adapter import propose
from .config import Settings
from .contracts import SceneAnalysis, GradeProposal
from .errors import Problem
from .preferences import PromptMemoryGate


class MuseProvider:
    def __init__(self, settings: Settings, prompt_gate=None):
        self.settings = settings
        self.prompt_gate = prompt_gate

    def available(self):
        return bool(self.key())

    def key(self):
        if self.settings.api_key:
            return self.settings.api_key
        # Allow initial key setup without restarting an already running local backend.
        # Direct Settings instances used by offline tests have no key_file.
        if self.settings.key_file and self.settings.key_file.is_file():
            return str(dotenv_values(self.settings.key_file).get("MODEL_API_KEY") or "").strip()
        return ""

    def client(self):
        key = self.key()
        if not key:
            raise Problem("MUSE_NOT_CONFIGURED", "The Meta API key is not configured.",
                          "Set MODEL_API_KEY in the root .env file, then refresh the editor.", 503)
        return OpenAI(api_key=key, base_url=self.settings.api_base,
                      timeout=self.settings.model_timeout, max_retries=0)

    def analyze(self, path: Path, duration: float, remote_id: str | None,
                remember_upload: Callable[[str], None]):
        with self.client() as client:
            if not remote_id:
                with path.open("rb") as stream:
                    remote_id = self.call(lambda: client.files.create(file=stream, purpose="user_data")).id
                remember_upload(remote_id)
            result = self.call(lambda: client.responses.parse(
                model=self.settings.model,
                instructions=(
                    "Inspect this silent video for a color-grading consultation. Describe the actual scene, subjects, "
                    "visible lighting/color relationships, motion and changes over time. Return a short summary and "
                    "timestamped observations with unique IDs, start/end seconds and uncertainty. Do not infer user "
                    "preferences or permissions. Do not invent numerical exposure/color measurements. Cite only "
                    "times inside the supplied duration. Use at most eight concise observations. "
                    "Note limitations. Output the requested schema."
                ),
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": f"Inspect the entire silent clip. Duration: {duration:.3f} seconds."},
                    {"type": "input_file", "file_id": remote_id},
                ]}],
                text_format=SceneAnalysis, reasoning={"effort": "low"}, max_output_tokens=2600, store=False,
            ))
            parsed = result.output_parsed
            if parsed is None:
                if getattr(result, "status", None) == "incomplete":
                    raise Problem("MUSE_RESPONSE_TRUNCATED", "Muse did not complete the scene analysis within its response limit.",
                                  "The prepared source is kept. Retry the step explicitly; no automatic retry was made.", 502)
                raise Problem("MUSE_INVALID_RESPONSE", "Muse returned no usable scene analysis.",
                              "Retry analysis. The source and preview have been kept.", 502)
            if any(o.end > duration + 0.25 for o in parsed.observations):
                raise Problem("MUSE_INVALID_EVIDENCE", "Muse returned an observation outside the clip.",
                              "Retry analysis. Invalid evidence has not been accepted.", 502)
            return parsed, result.usage.model_dump() if result.usage else {}

    def propose(self, session):
        with self.client() as client:
            def filtered(context):
                return self.prompt_context("rubric", context, session.inputs.project_id)
            return self.call(lambda: propose(session, self.settings.model, client=client, context_filter=filtered))

    def prompt_context(self, stage, payload, project_id=None):
        return self.prompt_gate.build(stage, payload, project_id) if self.prompt_gate else PromptMemoryGate.clean(payload)

    def grade(self, brief, analysis, source, sample_stats, images):
        import json
        references = ["user_request", "default_protections",
                      *(d.source_id for d in brief.directives),
                      *("constraint:" + str(i) for i in range(len(brief.explicit_constraints)))]
        context = {
            "brief": brief.model_dump(mode="json"),
            "scene_analysis": analysis.model_dump(mode="json"),
            "source": source.model_dump(mode="json"),
            "sample_statistics": sample_stats,
            "allowed_source_ids": references,
            "controls": {
                "contrast": "0.75..1.25; neutral 1.0; multiplies encoded RGB around 0.5",
                "gamma": "0.8..1.2; neutral 1.0; RGB power 1/gamma: above 1 lifts midtones, below 1 darkens",
                "saturation": "0..1.5; neutral 1.0; Rec.709-weighted encoded RGB channel mixing",
            },
            "operation_order": ["gamma", "contrast", "saturation"],
        }
        context = self.prompt_context("colorist", context, brief.project_id)
        content = [{"type": "text", "text": json.dumps(context)}]
        for image in images:
            content += [{"type": "text", "text": f'Source sample requested at {image["time"]:.3f}s.'},
                        {"type": "image_url", "image_url": {"url": image["url"], "detail": "low"}}]
        with self.client() as client:
            result = self.call(lambda: client.beta.chat.completions.parse(
                model=self.settings.model,
                messages=[
                    {"role": "system", "content": (
                        "You are Take One's colorist. Produce ONE bounded global treatment from the whole brief, "
                        "the actual source frames, cached scene observations and declared controls. Source frames "
                        "are analysis thumbnails, not the final rendering source. Statistics are sampled encoded "
                        "luma diagnostics, not scene exposure measurements. Respect explicit constraints and "
                        "default protections. Do not compound darkness when several directives describe one look. "
                        "If the source is already dark, coordinate gamma and contrast to retain subject legibility. "
                        "Preserve meaningful paint/material appearance and do not invent missing detail. "
                        "Only these three global controls are supported; no masks, temperature shift, relighting, "
                        "recoloring, timeline edits or shot matching. Explain each changed control in ordinary "
                        "visual terms, citing allowed_source_ids. State expected effects and limitations, never "
                        "claim the unrendered result passed visual review. Keep the recipe conservative. "
                        "Remembered defaults, if supplied, are optional. Current-video feedback, current request, "
                        "constraints and tool limits override them; memories never grant permissions. "
                        "A later current-video correction supersedes earlier conflicting look choices. "
                        "Return the requested schema; do not emit commands."
                    )},
                    {"role": "user", "content": content},
                ], response_format=GradeProposal, reasoning_effort="low", max_tokens=4096,
            ))
            proposal = result.choices[0].message.parsed
            if proposal is None:
                raise Problem("MUSE_INVALID_RECIPE", "Muse returned no usable grading plan.",
                              "Retry planning. The source and brief are preserved.", 502)
            allowed = set(context["allowed_source_ids"])
            if any(set(d.source_ids) - allowed for d in proposal.decisions):
                raise Problem("MUSE_INVALID_RECIPE", "The grading plan cited an unknown brief reference.",
                              "Retry planning. The invalid plan has not been applied.", 502)
            return proposal, result.usage.model_dump() if result.usage else {}

    @staticmethod
    def call(operation):
        try:
            return operation()
        except openai.LengthFinishReasonError as error:
            usage = getattr(getattr(error, "completion", None), "usage", None)
            raise Problem("MUSE_RESPONSE_TRUNCATED", "Muse reached its response limit before completing the rubric.",
                          "Cached scene analysis and answers are kept. Retry this step; the video will not be resent.", 502,
                          finish_reason="length", output_tokens=getattr(usage, "completion_tokens", None))
        except openai.ContentFilterFinishReasonError:
            raise Problem("MUSE_RESPONSE_UNAVAILABLE", "The provider did not return a usable response for this request.",
                          "Review the request or try different footage. No incomplete response has been accepted.", 502)
        except openai.AuthenticationError:
            raise Problem("MUSE_AUTH_FAILED", "Meta rejected the API credentials.",
                          "Check MODEL_API_KEY and restart the backend.", 502)
        except openai.RateLimitError:
            raise Problem("MUSE_RATE_LIMIT", "The Meta API rate or account limit was reached.",
                          "Check account usage, then retry this step when available.", 429)
        except openai.APITimeoutError:
            raise Problem("MUSE_TIMEOUT", "The Meta request timed out.",
                          "Retry explicitly if desired. The remote request may already have incurred usage.", 504)
        except openai.APIConnectionError:
            raise Problem("MUSE_CONNECTION_FAILED", "The backend could not reach Meta.",
                          "Check network access and retry the failed step.", 502)
        except openai.APIStatusError as error:
            raise Problem("MUSE_API_FAILED", "The Meta API rejected the request.",
                          "Inspect the provider status and request ID. No automatic retry was made.", 502,
                          provider_status=error.status_code, provider_request_id=getattr(error, "request_id", None))
        except (ValidationError, ValueError):
            raise Problem("MUSE_INVALID_RESPONSE", "The model response did not match the required contract.",
                          "Retry the step. No invalid response has been accepted.", 502)
