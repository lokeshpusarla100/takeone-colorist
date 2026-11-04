"""HTTP endpoints for the input profile (see takeone/color/profile.py). Installed by `app.create_app` like the preferences API."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import FastAPI, File, UploadFile
from pydantic import Field, model_validator
from takeone_rubric_v1.rubric import StrictModel

from . import lut as color_lut


class ProfileChoice(StrictModel):
    kind: Literal["none", "normalize", "lut"]
    lut_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    lut_name: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def lut_needs_a_file(self):
        if (self.kind == "lut") != (self.lut_id is not None):
            raise ValueError("lut_id is required for, and only for, kind 'lut'")
        return self


class ProfileCommand(ProfileChoice):
    command_id: UUID
    revision: int = Field(ge=0)


def install_color(app: FastAPI, service):
    @app.get("/api/projects/{project_id}/input-profile")
    def get_input_profile(project_id: UUID):
        return service().input_profile_state(str(project_id))

    @app.post("/api/projects/{project_id}/input-profile/lut")
    async def upload_lut(project_id: UUID, file: UploadFile = File(...)):
        try:
            data = await file.read(color_lut.MAX_BYTES + 1)
        finally:
            await file.close()
        return service().store_candidate_lut(str(project_id), data, file.filename or "lut.cube")

    @app.post("/api/projects/{project_id}/input-profile/preview")
    def preview_input_profile(project_id: UUID, body: ProfileChoice):
        return service().profile_preview(str(project_id), body.kind, body.lut_id, body.lut_name)

    @app.put("/api/projects/{project_id}/input-profile", status_code=202)
    def set_input_profile(project_id: UUID, body: ProfileCommand):
        return service().set_input_profile(str(project_id), body.kind, body.lut_id, body.lut_name, body.revision, str(body.command_id)).public()
