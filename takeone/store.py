import json
import os
from pathlib import Path
import threading
from uuid import UUID, uuid4

from .contracts import State, now
from .errors import Problem


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def directory(self, project_id: str) -> Path:
        try:
            clean = str(UUID(project_id))
        except ValueError:
            raise Problem("PROJECT_NOT_FOUND", "Project not found.", "Import the video again.", 404)
        return self.root / clean

    def get(self, project_id: str) -> State:
        with self.lock:
            path = self.directory(project_id) / "state.json"
            if not path.is_file():
                raise Problem("PROJECT_NOT_FOUND", "The backend project was not found.",
                              "Re-upload the original source to prepare this session.", 404)
            try:
                return State.model_validate_json(path.read_text())
            except (ValueError, OSError):
                raise Problem("PROJECT_STATE_INVALID", "The saved backend state could not be read.",
                              "The record has been preserved. Inspect the workspace before retrying.", 500)

    def save(self, state: State):
        with self.lock:
            directory = self.directory(state.id)
            directory.mkdir(parents=True, exist_ok=True)
            state.revision += 1
            state.updated_at = now()
            temporary = directory / (".state-" + str(uuid4()) + ".tmp")
            try:
                with temporary.open("w") as stream:
                    stream.write(state.model_dump_json(indent=2))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, directory / "state.json")
            except OSError as error:
                temporary.unlink(missing_ok=True)
                raise Problem("WORKSPACE_WRITE_FAILED", "Could not save the backend project.",
                              "Check available disk space and workspace permissions.", 507,
                              exception_type=type(error).__name__)

    def artifact(self, project_id: str, filename: str, value: dict):
        # Only application-owned constant artifact names are accepted.
        if filename not in {"metadata.json", "analysis.json", "brief.json"}:
            raise ValueError("Unknown artifact")
        directory = self.directory(project_id)
        temporary = directory / ("." + filename + ".tmp")
        with temporary.open("w") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / filename)

    def candidate_artifact(self, project_id: str, candidate_id: str, filename: str, value: dict):
        if filename not in {"recipe.json", "validation.json", "plan.json"}:
            raise ValueError("Unknown candidate artifact")
        directory = self.directory(project_id) / "candidates" / str(UUID(candidate_id))
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / ("." + filename + ".tmp")
        with temporary.open("w") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / filename)
