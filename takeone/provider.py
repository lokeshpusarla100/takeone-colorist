from pathlib import Path
from collections.abc import Callable

import openai
from openai import OpenAI
from pydantic import ValidationError
from dotenv import dotenv_values

from takeone_rubric_v1.muse_adapter import propose
from .config import Settings
from .contracts import SceneAnalysis
from .errors import Problem


class MuseProvider:
    def __init__(self, settings: Settings):
        self.settings = settings

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
                    "times inside the supplied duration. Note limitations. Output the requested schema."
                ),
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": f"Inspect the entire silent clip. Duration: {duration:.3f} seconds."},
                    {"type": "input_file", "file_id": remote_id},
                ]}],
                text_format=SceneAnalysis, max_output_tokens=2600, store=False,
            ))
            parsed = result.output_parsed
            if parsed is None:
                raise Problem("MUSE_INVALID_RESPONSE", "Muse returned no usable scene analysis.",
                              "Retry analysis. The source and preview have been kept.", 502)
            if any(o.end > duration + 0.25 for o in parsed.observations):
                raise Problem("MUSE_INVALID_EVIDENCE", "Muse returned an observation outside the clip.",
                              "Retry analysis. Invalid evidence has not been accepted.", 502)
            return parsed, result.usage.model_dump() if result.usage else {}

    def propose(self, session):
        with self.client() as client:
            return self.call(lambda: propose(session, self.settings.model, client=client))

    @staticmethod
    def call(operation):
        try:
            return operation()
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
