"""Local preference management and ordinary current-project feedback endpoints."""
from uuid import UUID
from typing import Literal
from fastapi import FastAPI
from pydantic import Field
from takeone_rubric_v1.rubric import Dimension, StrictModel
from .preferences import FeedbackEvidence, PreferenceScope
from .errors import Problem


class ProfileCreate(StrictModel):
    name: str = Field(min_length=1,max_length=80)


class SettingsPatch(StrictModel):
    capture_enabled: bool | None = None
    application_enabled: bool | None = None


class EntryCreate(StrictModel):
    command_id: UUID
    original_wording: str = Field(min_length=1,max_length=4000)
    interpretation: str = Field(min_length=1,max_length=2000)
    dimension: Dimension
    preference_key: str = Field(default="appearance",min_length=1,max_length=160)
    scope: PreferenceScope
    context: dict[str,str] = Field(default_factory=dict)
    project_id: UUID | None = None


class EntryPatch(StrictModel):
    revision: int = Field(ge=1)
    interpretation: str | None = Field(default=None,min_length=1,max_length=2000)
    original_wording: str | None = Field(default=None,max_length=4000)
    dimension: Dimension | None = None
    preference_key: str | None = Field(default=None,min_length=1,max_length=160)
    scope: PreferenceScope | None = None
    context: dict[str,str] | None = None
    status: Literal["tentative","confirmed","contradicted"] | None = None


class UserEvidence(StrictModel):
    kind: Literal["feedback","version_selected","version_rejected","manual_adjustment","manual_lock"] = "feedback"
    original_wording: str = Field(default="",max_length=4000)
    dimension: Dimension = Dimension.APPEARANCE
    preference_key: str = Field(default="appearance",min_length=1,max_length=160)
    version_id: str | None = Field(default=None,max_length=180)
    context: dict[str,str] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)


class FeedbackCommand(StrictModel):
    command_id: UUID
    revision: int = Field(ge=0)
    evidence: UserEvidence


def install_preferences(app:FastAPI,service):
    def check_project(profile_id,project_id):
        if not project_id:
            return
        svc=service()
        state=svc.store.get(str(project_id))
        if state.profile_id!=profile_id or state.owner_id not in {None,svc.require_memory().owner_id}:
            raise Problem("MEMORY_PROJECT_MISMATCH","This project belongs to another preference profile.",
                          "Use the profile assigned when the video was uploaded.",409)

    @app.get("/api/preferences/profiles")
    def profiles():
        return service().require_memory().profiles()

    @app.post("/api/preferences/profiles",status_code=201)
    def create_profile(body:ProfileCreate):
        p=service().require_memory().create_profile(body.name)
        return {"id":p.profile_id,"name":p.name}

    @app.get("/api/preferences/profiles/{profile_id}")
    def view(profile_id:str,include_deleted:bool=False):
        return service().require_memory().view(profile_id,include_deleted)

    @app.patch("/api/preferences/profiles/{profile_id}/settings")
    def settings(profile_id:str,body:SettingsPatch):
        return service().require_memory().update_settings(profile_id,**body.model_dump()).model_dump()

    @app.post("/api/preferences/profiles/{profile_id}/events")
    def record_event(profile_id:str,body:FeedbackEvidence):
        check_project(profile_id,body.project_id)
        if not body.project_id:
            raise Problem("MEMORY_PROJECT_REQUIRED","This event needs a supporting project.",
                          "Use entries with an explicit scope for reusable instructions without a project.",422)
        return service().require_memory().record(profile_id,body)

    @app.post("/api/preferences/profiles/{profile_id}/entries",status_code=201)
    def add(profile_id:str,body:EntryCreate):
        project_id=str(body.project_id) if body.project_id else body.scope.project_id
        check_project(profile_id,project_id)
        evidence=FeedbackEvidence(event_id="manual:"+str(body.command_id),project_id=project_id,
            kind="reusable_instruction",original_wording=body.original_wording,dimension=body.dimension,
            preference_key=body.preference_key,context=body.context)
        return service().require_memory().record(profile_id,evidence,scope=body.scope,
            interpretation=body.interpretation,manual=True)

    @app.patch("/api/preferences/profiles/{profile_id}/entries/{entry_id}")
    def edit(profile_id:str,entry_id:UUID,body:EntryPatch):
        changes=body.model_dump(mode="json",exclude={"revision"},exclude_unset=True)
        if any(v is None for v in changes.values()):
            raise Problem("MEMORY_EDIT_INVALID","Preference edits cannot erase required fields.","Provide a value.",422)
        if body.scope and body.scope.project_id:
            check_project(profile_id,body.scope.project_id)
        return service().require_memory().edit(profile_id,str(entry_id),body.revision,changes).model_dump(mode="json")

    @app.delete("/api/preferences/profiles/{profile_id}/entries/{entry_id}")
    def delete(profile_id:str,entry_id:UUID,revision:int):
        service().require_memory().delete(profile_id,str(entry_id),revision)
        return {"deleted":True}

    @app.post("/api/projects/{project_id}/feedback")
    def feedback(project_id:UUID,body:FeedbackCommand):
        evidence=FeedbackEvidence(event_id="feedback:"+str(body.command_id),project_id=str(project_id),
                                  **body.evidence.model_dump(mode="json"))
        return service().feedback_event(str(project_id),evidence,body.revision,str(body.command_id)).public()

    @app.post("/api/projects/{project_id}/preference-capture/retry")
    def retry_capture(project_id:UUID):
        svc=service()
        with svc.store.lock:
            state=svc.store.get(str(project_id))
            svc.drain_memory(state)
            return state.public()
