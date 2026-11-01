"""Application preference evidence and editable interpretations. No model calls."""
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
from typing import Literal
from uuid import uuid4

from pydantic import Field
from takeone_rubric_v1.rubric import Dimension, StrictModel
from .contracts import now
from .errors import Problem

CONTEXT_KEYS = {"category", "scene", "purpose"}


class MemorySettings(StrictModel):
    capture_enabled: bool = True
    application_enabled: bool = False


class PreferenceScope(StrictModel):
    kind: Literal["project", "category", "general"] = "project"
    project_id: str | None = None
    conditions: dict[str, str] = Field(default_factory=dict)


class FeedbackEvidence(StrictModel):
    event_id: str = Field(min_length=1, max_length=180)
    project_id: str | None = None
    kind: Literal["rubric_answer", "feedback", "version_selected", "version_rejected",
                  "manual_adjustment", "manual_lock", "reusable_instruction"]
    original_wording: str = Field(default="", max_length=4000)
    dimension: Dimension = Dimension.APPEARANCE
    preference_key: str = Field(default="appearance", min_length=1, max_length=160)
    question_id: str | None = None
    version_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)
    timestamp: str = Field(default_factory=now)


class PreferenceEntry(StrictModel):
    id: str
    owner_id: str
    profile_id: str
    original_wording: str
    interpretation: str
    dimension: Dimension
    preference_key: str
    scope: PreferenceScope
    context: dict[str, str]
    basis: Literal["explicit_statement", "explicit_choice", "inferred"]
    status: Literal["tentative", "confirmed", "contradicted", "deleted"]
    event_ids: list[str]
    project_ids: list[str]
    question_ids: list[str]
    version_ids: list[str]
    created_at: str
    updated_at: str
    revision: int = 1
    history: list[dict] = Field(default_factory=list)


class MemoryDocument(StrictModel):
    schema_version: int = 1
    owner_id: str
    profile_id: str
    name: str
    revision: int = 0
    settings: MemorySettings = Field(default_factory=MemorySettings)
    events: list[dict] = Field(default_factory=list)
    preferences: list[PreferenceEntry] = Field(default_factory=list)
    processed: dict[str, dict] = Field(default_factory=dict)
    preference_keys: dict[str, str] = Field(default_factory=dict)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def context_values(value: dict[str, str]) -> dict[str, str]:
    if set(value) - CONTEXT_KEYS or any(not isinstance(v, str) or len(v) > 120 for v in value.values()):
        raise Problem("MEMORY_CONTEXT_INVALID", "Memory context contains unsupported fields.",
                      "Use category, scene and purpose labels only.", 422)
    return {k: v.strip().casefold() for k, v in value.items() if v.strip()}


class PreferenceStore:
    def __init__(self, root: Path, *, application_available: bool = False):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.thread_lock = threading.RLock()
        self.application_available = application_available
        with self.locked():
            manifest = self.root / "owner.json"
            if not manifest.exists():
                self.atomic(manifest, {"owner_id": str(uuid4()), "created_at": now()})
            self.owner_id = json.loads(manifest.read_text())["owner_id"]
            if not self.path("default").exists():
                self.save(MemoryDocument(owner_id=self.owner_id, profile_id="default", name="Default"))

    @contextmanager
    def locked(self):
        with self.thread_lock, (self.root / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def path(self, profile_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile_id):
            raise Problem("PROFILE_INVALID", "Invalid profile ID.", "Choose a valid profile.", 422)
        return self.root / "profiles" / (profile_id + ".json")

    @staticmethod
    def atomic(path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + str(uuid4()) + ".tmp")
        try:
            with temporary.open("x") as stream:
                os.chmod(temporary, 0o600)
                json.dump(value, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise Problem("MEMORY_WRITE_FAILED", "Preference capture could not be saved.",
                          "Current-video feedback is kept separately. Check storage and retry capture.", 507,
                          exception_type=type(error).__name__)

    def load(self, profile_id: str):
        path = self.path(profile_id)
        if not path.exists():
            raise Problem("PROFILE_NOT_FOUND", "Preference profile not found.", "Choose another profile.", 404)
        try:
            doc = MemoryDocument.model_validate_json(path.read_text())
        except (OSError, ValueError):
            raise Problem("MEMORY_STATE_INVALID", "The preference profile could not be read.",
                          "The saved file has been preserved. Inspect memory storage.", 500)
        if doc.owner_id != self.owner_id or doc.profile_id != profile_id:
            raise Problem("MEMORY_OWNER_MISMATCH", "The preference record belongs to another owner or profile.",
                          "Do not copy profile files between owners.", 409)
        if any(p.owner_id!=self.owner_id or p.profile_id!=profile_id for p in doc.preferences) or any(
            e.get("owner_id")!=self.owner_id or e.get("profile_id")!=profile_id for e in doc.events):
            raise Problem("MEMORY_OWNER_MISMATCH","A nested memory record belongs to another profile.",
                          "The record has been preserved. Inspect profile storage.",409)
        return doc

    def save(self, doc: MemoryDocument):
        doc.revision += 1
        self.atomic(self.path(doc.profile_id), doc.model_dump(mode="json"))

    def profiles(self):
        with self.locked():
            result=[]
            for path in sorted((self.root / "profiles").glob("*.json")):
                try:
                    doc=self.load(path.stem)
                    result.append({"id":doc.profile_id,"name":doc.name})
                except Problem as problem:
                    result.append({"id":path.stem,"name":"Unavailable profile","error":problem.public()})
            return result

    def create_profile(self, name: str):
        if not name.strip() or len(name) > 80:
            raise Problem("PROFILE_NAME_INVALID", "Enter a short profile name.", "Use 1–80 characters.", 422)
        with self.locked():
            doc = MemoryDocument(owner_id=self.owner_id, profile_id=str(uuid4()), name=name.strip())
            self.save(doc)
            return doc

    def settings(self, profile_id: str):
        with self.locked():
            return self.load(profile_id).settings

    def update_settings(self, profile_id: str, *, capture_enabled=None, application_enabled=None):
        with self.locked():
            doc = self.load(profile_id)
            if application_enabled and not self.application_available:
                raise Problem("MEMORY_CAPTURE_ONLY", "Personalization is unavailable in capture-only mode.",
                              "Capture does not enable application. A future rollout requires explicit new-project opt-in.", 409)
            if capture_enabled is not None:
                doc.settings.capture_enabled = capture_enabled
            if application_enabled is not None:
                doc.settings.application_enabled = application_enabled
            self.save(doc)
            return doc.settings

    def view(self, profile_id: str, include_deleted=False):
        with self.locked():
            doc = self.load(profile_id)
            return {
                "owner_id": doc.owner_id, "profile_id": profile_id, "name": doc.name,
                "settings": doc.settings.model_dump(),
                "application_available": self.application_available,
                "preferences": [dict(p.model_dump(mode="json"), independent_projects=len(p.project_ids))
                                for p in doc.preferences if include_deleted or p.status != "deleted"],
                "events": doc.events,
            }

    def record(self, profile_id: str, evidence: FeedbackEvidence, *, scope: PreferenceScope | None = None,
               interpretation: str | None = None, manual=False):
        evidence.context = context_values(evidence.context)
        detail_fields = {
            "rubric_answer":{"response_type","selected_label","selected_outcome","scope"},
            "feedback":set(),"version_selected":{"origin"},"version_rejected":{"origin"},
            "manual_adjustment":{"control","before","after","scope","applied"},
            "manual_lock":{"control","value","locked","scope","applied"},
            "reusable_instruction":set(),
        }
        if set(evidence.details)-detail_fields[evidence.kind] or any(
            not (v is None or isinstance(v,(str,int,float,bool)))
            or (isinstance(v,float) and not math.isfinite(v))
            or (isinstance(v,str) and len(v)>16000) for v in evidence.details.values()):
            raise Problem("MEMORY_DETAILS_INVALID","Feedback contains unsupported evidence fields.",
                          "Send only editing action details, without model output or unrelated profile data.",422)
        scope = scope or PreferenceScope(project_id=evidence.project_id)
        scope.conditions = context_values(scope.conditions)
        if scope.kind == "project" and (not scope.project_id or scope.project_id != evidence.project_id):
            raise Problem("MEMORY_SCOPE_INVALID", "A project-scoped entry needs its supporting project.",
                          "Use the current project reference.", 422)
        if scope.kind == "category" and not scope.conditions:
            raise Problem("MEMORY_SCOPE_INVALID", "A category-scoped entry needs applicability conditions.",
                          "Specify its category, scene or purpose.", 422)
        body = evidence.model_dump(mode="json")
        # Timestamp is audit metadata, not a second identity for a retried event.
        fingerprint = digest({"event":{k:v for k,v in body.items() if k!="timestamp"},
                              "scope":scope.model_dump(mode="json"),"interpretation":interpretation,"manual":manual})
        with self.locked():
            doc = self.load(profile_id)
            previous = doc.processed.get(evidence.event_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise Problem("MEMORY_EVENT_CONFLICT", "This feedback ID already has different content.",
                                  "Use a new event ID for a new action.", 409)
                return previous
            if not manual and not doc.settings.capture_enabled:
                doc.processed[evidence.event_id] = {"captured":False,"fingerprint":fingerprint,"preference_id":None}
                self.save(doc)
                return {"captured": False}
            raw = dict(body, owner_id=self.owner_id, profile_id=profile_id)
            doc.events.append(raw)
            eligible = evidence.kind in {"feedback", "reusable_instruction", "manual_adjustment", "manual_lock"}
            if evidence.kind=="version_rejected" and evidence.original_wording.strip():
                eligible=True  # Capture the user's stated reason, never infer it from recipe changes.
            basis = "explicit_statement"
            if evidence.kind == "rubric_answer":
                response_type = evidence.details.get("response_type")
                eligible = response_type in {"option", "free_text"}
                basis = "explicit_choice" if response_type == "option" else "explicit_statement"
                interpretation = interpretation or evidence.details.get("selected_outcome")
            if evidence.kind in {"manual_adjustment", "manual_lock"}:
                basis = "inferred"
                # A setting records an action, not approval of a rendered aesthetic.
                action = evidence.original_wording or json.dumps(evidence.details,sort_keys=True)
                interpretation = interpretation or "User requested " + evidence.kind.replace("_", " ") + ": " + action
            interpretation = (interpretation or evidence.original_wording).strip()
            if len(interpretation)>2000:
                interpretation=interpretation[:1999]+"…"
            preference_id = None
            if eligible and interpretation:
                key = digest([evidence.preference_key, evidence.dimension.value, scope.model_dump(mode="json"),
                              evidence.context, " ".join(interpretation.casefold().split())])
                preference_id = doc.preference_keys.get(key)
                entry = next((p for p in doc.preferences if p.id == preference_id), None)
                if entry is not None and manual and (entry.status == "deleted" or digest([
                    entry.preference_key,entry.dimension.value,entry.scope.model_dump(mode="json"),
                    entry.context," ".join(entry.interpretation.casefold().split())]) != key):
                    entry = None
                suppressed = entry is not None and (entry.status == "deleted" or digest([
                    entry.preference_key, entry.dimension.value, entry.scope.model_dump(mode="json"),
                    entry.context, " ".join(entry.interpretation.casefold().split())]) != key)
                if entry is None:
                    stamp = now()
                    entry = PreferenceEntry(id=str(uuid4()), owner_id=self.owner_id, profile_id=profile_id,
                        original_wording=evidence.original_wording, interpretation=interpretation,
                        dimension=evidence.dimension, preference_key=evidence.preference_key, scope=scope,
                        context=evidence.context, basis=basis, status="confirmed" if manual else "tentative",
                        event_ids=[], project_ids=[], question_ids=[], version_ids=[],
                        created_at=stamp, updated_at=stamp)
                    doc.preferences.append(entry)
                    doc.preference_keys[key] = entry.id
                    preference_id = entry.id
                if not suppressed and entry.status != "deleted":
                    if entry.event_ids:
                        entry.revision += 1
                    for attr, ref in [("event_ids", evidence.event_id), ("project_ids", evidence.project_id),
                                      ("question_ids", evidence.question_id), ("version_ids", evidence.version_id)]:
                        values = getattr(entry, attr)
                        if ref and ref not in values:
                            values.append(ref)
                    entry.history.append({"revision": entry.revision, "at": now(), "action": "evidence_added", "event_id": evidence.event_id})
                    entry.updated_at = now()
            receipt = {"captured": True, "fingerprint": fingerprint, "preference_id": preference_id}
            doc.processed[evidence.event_id] = receipt
            self.save(doc)
            return receipt

    def edit(self, profile_id: str, entry_id: str, revision: int, changes: dict):
        allowed = {"interpretation", "original_wording", "dimension", "scope", "context", "preference_key", "status"}
        if set(changes) - allowed or changes.get("status") == "deleted":
            raise Problem("MEMORY_EDIT_INVALID", "Unsupported preference edit.", "Use the delete operation to remove an entry.", 422)
        with self.locked():
            doc = self.load(profile_id)
            entry = next((p for p in doc.preferences if p.id == entry_id and p.status != "deleted"), None)
            if not entry:
                raise Problem("PREFERENCE_NOT_FOUND", "Preference not found in this profile.", "Refresh the memory view.", 404)
            if entry.revision != revision:
                raise Problem("PREFERENCE_STALE", "This preference was edited elsewhere.", "Refresh before editing.", 409)
            updated = PreferenceEntry.model_validate({**entry.model_dump(mode="json"), **changes})
            updated.context = context_values(updated.context)
            updated.scope.conditions = context_values(updated.scope.conditions)
            if not updated.interpretation.strip() or len(updated.interpretation) > 2000 or len(updated.original_wording) > 4000:
                raise Problem("MEMORY_EDIT_INVALID", "Enter a concise preference and its original wording.", "Review the entry.", 422)
            if updated.scope.kind == "project" and updated.scope.project_id not in entry.project_ids:
                raise Problem("MEMORY_SCOPE_INVALID", "The new scope has no supporting project.", "Choose a supporting project.", 422)
            if updated.scope.kind == "category" and not updated.scope.conditions:
                raise Problem("MEMORY_SCOPE_INVALID", "Category scope needs applicability conditions.", "Add a context condition.", 422)
            updated.history = entry.history + [{"revision": entry.revision, "at": now(), "action": "edited",
                                               "before": entry.model_dump(mode="json", exclude={"history"})}]
            updated.revision += 1
            updated.updated_at = now()
            edit_id = "memory-edit:" + str(uuid4())
            doc.events.append({"event_id":edit_id,"owner_id":self.owner_id,"profile_id":profile_id,
                               "kind":"preference_edit","timestamp":now(),"preference_id":entry.id,
                               "changes":changes})
            updated.event_ids.append(edit_id)
            doc.preferences[doc.preferences.index(entry)] = updated
            key = digest([updated.preference_key,updated.dimension.value,updated.scope.model_dump(mode="json"),
                          updated.context," ".join(updated.interpretation.casefold().split())])
            doc.preference_keys[key] = updated.id
            self.save(doc)
            return updated

    def delete(self, profile_id: str, entry_id: str, revision: int):
        with self.locked():
            doc = self.load(profile_id)
            entry = next((p for p in doc.preferences if p.id == entry_id), None)
            if not entry:
                raise Problem("PREFERENCE_NOT_FOUND", "Preference not found in this profile.", "Refresh the memory view.", 404)
            if entry.status == "deleted":
                return
            if entry.revision != revision:
                raise Problem("PREFERENCE_STALE", "This preference changed.", "Refresh before deleting.", 409)
            entry.status, entry.original_wording, entry.interpretation = "deleted", "", ""
            entry.context = {}
            entry.revision += 1
            entry.updated_at = now()
            entry.history = [{"revision": entry.revision, "at": now(), "action": "deleted"}]
            # Keep event receipts and fingerprint tombstones to prevent replay resurrection.
            self.save(doc)

    def relevant(self, profile_id: str, project_id: str, context: dict[str, str], *, opted_in=False,
                 overridden_keys: set[str] | None = None, binding: dict | None = None, include_conflicts=False):
        # Central fail-closed switch, checked before reading or retrieving any preference.
        if not self.application_available or not opted_in:
            return []
        context = context_values(context)
        with self.locked():
            doc = self.load(profile_id)
            if not doc.settings.application_enabled:
                return []
            entries = doc.preferences
            if binding is not None:
                if binding.get("owner_id") != self.owner_id or binding.get("profile_id") != profile_id:
                    return []
                live = {p.id: p for p in entries}
                # Frozen values; later edits never mutate a project's supplied defaults.
                entries = [PreferenceEntry.model_validate(v) for v in binding.get("entries", [])
                           if v.get("owner_id")==self.owner_id and v.get("profile_id")==profile_id
                           and v.get("id") in live and live[v["id"]].status == "confirmed"]
            eligible = []
            for entry in entries:
                if entry.status != "confirmed" or entry.preference_key in (overridden_keys or set()):
                    continue
                scope = entry.scope
                if scope.kind == "project" and scope.project_id != project_id:
                    continue
                if scope.kind == "category" and not scope.conditions:
                    continue
                if any(context.get(k) != v for k, v in scope.conditions.items()):
                    continue
                eligible.append(entry)
            # Preserve conflicts. Ambiguous defaults require clarification, never last-write-wins.
            groups: dict[str, set[str]] = {}
            for e in eligible:
                groups.setdefault(e.preference_key, set()).add(e.interpretation.casefold())
            return eligible if include_conflicts else [e for e in eligible if len(groups[e.preference_key]) == 1]

    def bind_new_project(self, profile_id: str, project_id: str, context: dict[str, str], requested=False):
        settings = self.settings(profile_id)
        if requested and not self.application_available:
            raise Problem("MEMORY_CAPTURE_ONLY", "Personalization is off in this release.",
                          "New projects capture evidence without applying stored preferences.", 409)
        if requested and not settings.application_enabled:
            raise Problem("MEMORY_APPLICATION_DISABLED","This profile has not enabled personalization.",
                          "Enable application explicitly before opting a new project in.",409)
        entries = self.relevant(profile_id, project_id, context, opted_in=requested, include_conflicts=True)
        return {"owner_id": self.owner_id, "profile_id": profile_id,
                "enabled": bool(requested and self.application_available),
                "entries": [e.model_dump(mode="json") for e in entries]}


class PromptMemoryGate:
    """The only boundary through which reusable preferences may enter model prompts."""
    STAGES = {"rubric", "colorist", "critic", "look_selection", "preview_generation"}
    RESERVED = {"remembered_defaults", "remembered_preferences", "memory_context", "memory_defaults",
                "memory_influences", "memory_refs", "memory_used_ids", "memory_binding", "memory_outbox"}

    def __init__(self, memory: PreferenceStore, project_lookup):
        self.memory, self.project_lookup = memory, project_lookup

    def defaults(self, project_id: str | None, overridden_keys: set[str] | None = None):
        if self.memory is None or not self.memory.application_available or not project_id:
            return []
        state = self.project_lookup(project_id)
        if state.owner_id != self.memory.owner_id:
            return []
        binding = state.memory_binding
        if not binding or not binding.get("enabled"):
            return []
        current_keys = set(overridden_keys or set())
        from .contracts import current_feedback
        current_keys.update(f.get("preference_key","") for f in current_feedback(state))
        rubric=state.rubric or {}
        questions={q["id"]:q for q in rubric.get("questions",[])}
        for answer in rubric.get("answers",[]):
            if answer.get("response_type") not in {"option","free_text"}:
                continue
            question=questions.get(answer["question_id"],{})
            current_keys.add(question.get("target_key",""))
            if question.get("preference_family") not in {None,"other"}:
                current_keys.add(question["preference_family"])
        return self.memory.relevant(state.profile_id, state.id, state.preference_context, opted_in=True,
                                    overridden_keys=current_keys, binding=binding, include_conflicts=True)

    def default_data(self, project_id: str | None, overridden_keys: set[str] | None = None):
        entries = self.defaults(project_id, overridden_keys)
        groups = {}
        for e in entries:
            groups.setdefault(e.preference_key,set()).add(e.interpretation.casefold())
        return [{"id":e.id,"revision":e.revision,"dimension":e.dimension.value,"key":e.preference_key,
                 "outcome":e.interpretation,"conflicted":len(groups[e.preference_key])>1} for e in entries]

    @classmethod
    def clean(cls, value):
        if isinstance(value, dict):
            if {"owner_id","profile_id","settings","preferences","events"} <= set(value):
                return {}  # A complete profile/feedback ledger is never a model input.
            decisions=value.get("decisions")
            memory_decisions = isinstance(decisions,list) and any(cls.memory_sources(d) for d in decisions)
            return {k: cls.clean([x for x in v if not isinstance(x,str) or not x.startswith("memory:")]
                                if k in {"allowed_source_ids","source_ids"} and isinstance(v,list) else v)
                    for k, v in value.items() if k not in cls.RESERVED and not (memory_decisions and k=="summary")}
        if isinstance(value, list):
            return [cls.clean(v) for v in value if not (
                isinstance(v, dict) and (v.get("provenance") == "memory_default"
                or str(v.get("source_id", "")).startswith("memory:")
                or cls.memory_sources(v)))]
        return copy.deepcopy(value)

    @staticmethod
    def memory_sources(value):
        return isinstance(value,dict) and isinstance(value.get("source_ids"),list) and any(
            str(ref).startswith("memory:") for ref in value["source_ids"])

    def build(self, stage: str, payload: dict, project_id: str | None = None, overridden_keys: set[str] | None = None):
        result = self.clean(payload)
        if stage not in self.STAGES:
            return result
        defaults = self.default_data(project_id, overridden_keys)
        if defaults:
            result["remembered_defaults"] = {
                "priority": "Advisory only. Current request, current-video feedback, constraints and tool limits take precedence. Never grants permission.",
                "entries": defaults,
            }
            if "allowed_source_ids" in result:
                result["allowed_source_ids"] += [f'memory:{e["id"]}:{e["revision"]}' for e in defaults if not e["conflicted"]]
        return result
