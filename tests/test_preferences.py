import copy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from takeone.preferences import FeedbackEvidence, PreferenceScope, PreferenceStore, PromptMemoryGate
from takeone.errors import Problem
from takeone.provider import MuseProvider
from takeone.contracts import GradeProposal, SceneAnalysis, State, VisualObservation
from takeone_rubric_v1.rubric import Directive, Proposal, RubricInput, RubricSession
from tests.test_backend import FakeMuse, client, settings, start, upload, video, wait


def event(project="project-a",event_id=None,**kw):
    return FeedbackEvidence(event_id=event_id or str(uuid4()),project_id=project,kind="feedback",
                            original_wording="Less orange",**kw)


def manual(store,profile="default",text="Preserve original product color",key="product_color",scope=None):
    e=FeedbackEvidence(event_id=str(uuid4()),kind="reusable_instruction",original_wording=text,
                       dimension="boundaries",preference_key=key)
    receipt=store.record(profile,e,scope=scope or PreferenceScope(kind="general"),manual=True)
    return next(p for p in store.view(profile)["preferences"] if p["id"]==receipt["preference_id"])


def test_empty_defaults_and_capture_never_enables_application(tmp_path):
    store=PreferenceStore(tmp_path)
    assert store.view("default")["preferences"]==[]
    assert store.view("default")["events"]==[]
    for _ in range(4):
        store.record("default",event())
    assert store.settings("default").capture_enabled is True
    assert store.settings("default").application_enabled is False
    with pytest.raises(Problem,match="capture-only"):
        store.update_settings("default",application_enabled=True)
    assert store.relevant("default","new",{},opted_in=True)==[]


def test_persistence_dedup_and_one_video_is_one_supporting_project(tmp_path):
    store=PreferenceStore(tmp_path)
    e=event()
    first=store.record("default",e)
    assert store.record("default",e.model_copy(update={"timestamp":"different"}))==first
    store.record("default",event())
    again=PreferenceStore(tmp_path)
    data=again.view("default")
    assert len(data["events"])==2
    assert len(data["preferences"])==1
    assert data["preferences"][0]["independent_projects"]==1
    assert data["preferences"][0]["status"]=="tentative"
    with pytest.raises(Problem):
        store.record("default",e.model_copy(update={"original_wording":"different"}))


def test_same_statement_in_another_video_does_not_become_general(tmp_path):
    store=PreferenceStore(tmp_path)
    store.record("default",event("one"))
    store.record("default",event("two"))
    entries=store.view("default")["preferences"]
    assert len(entries)==2
    assert all(e["scope"]["kind"]=="project" and e["independent_projects"]==1 for e in entries)


def test_selections_skips_and_delegation_are_not_aesthetic_inference(tmp_path):
    store=PreferenceStore(tmp_path)
    for kind in ("version_selected","version_rejected"):
        store.record("default",FeedbackEvidence(event_id=str(uuid4()),project_id="one",kind=kind,version_id="V1"))
    for response in ("skip","delegate"):
        store.record("default",FeedbackEvidence(event_id=str(uuid4()),project_id="one",kind="rubric_answer",
            question_id="q1",details={"response_type":response,"selected_outcome":""}))
    assert len(store.view("default")["events"])==4
    assert store.view("default")["preferences"]==[]
    with pytest.raises(ValidationError):
        FeedbackEvidence(event_id="bad",kind="model_critique")
    with pytest.raises(Problem):
        store.record("default",event(details={"personal_contact":"unrelated"}))


def test_manual_action_is_tentative_contextual_evidence(tmp_path):
    store=PreferenceStore(tmp_path)
    store.record("default",FeedbackEvidence(event_id="manual",project_id="one",kind="manual_lock",
        details={"control":"saturation","value":0.9,"locked":True,"applied":False}))
    entry=store.view("default")["preferences"][0]
    assert entry["basis"]=="inferred" and entry["status"]=="tentative"
    assert entry["scope"]["project_id"]=="one"
    assert "saturation" in entry["interpretation"]


def test_profile_owner_isolation_and_deletion_tombstones(tmp_path):
    store=PreferenceStore(tmp_path/"one")
    second=store.create_profile("Other")
    e=event()
    receipt=store.record("default",e)
    assert store.view(second.profile_id)["preferences"]==[]
    entry=store.view("default")["preferences"][0]
    with pytest.raises(Problem):
        store.delete(second.profile_id,entry["id"],entry["revision"])
    store.delete("default",entry["id"],entry["revision"])
    assert store.view("default")["preferences"]==[]
    store.record("default",e)
    store.record("default",event())
    assert store.view("default")["preferences"]==[]
    assert store.view("default",True)["preferences"][0]["status"]=="deleted"
    foreign=PreferenceStore(tmp_path/"two")
    foreign.atomic(foreign.path("default"),json.loads(store.path("default").read_text()))
    with pytest.raises(Problem,match="another owner"):
        foreign.view("default")


def test_edit_history_stale_revisions_and_old_evidence_does_not_overwrite_correction(tmp_path):
    store=PreferenceStore(tmp_path)
    store.record("default",event())
    original=store.view("default")["preferences"][0]
    updated=store.edit("default",original["id"],original["revision"],
                       {"interpretation":"Preserve warmer colors","status":"confirmed"})
    assert updated.history[-1]["action"]=="edited"
    with pytest.raises(Problem):
        store.edit("default",original["id"],original["revision"],{"status":"tentative"})
    store.record("default",event())
    entry=store.view("default")["preferences"][0]
    assert entry["interpretation"]=="Preserve warmer colors"
    assert len(entry["project_ids"])==1


def test_disabled_capture_does_not_backfill_when_reenabled(tmp_path):
    store=PreferenceStore(tmp_path)
    store.update_settings("default",capture_enabled=False)
    e=event()
    assert store.record("default",e)["captured"] is False
    store.update_settings("default",capture_enabled=True)
    assert store.record("default",e)["captured"] is False
    assert store.view("default")["events"]==[]
    assert store.settings("default").application_enabled is False


def test_future_retrieval_needs_both_flags_context_and_new_project_opt_in(tmp_path):
    store=PreferenceStore(tmp_path,application_available=True)
    manual(store,scope=PreferenceScope(kind="category",conditions={"category":"product"}))
    assert store.relevant("default","new",{"category":"product"},opted_in=True)==[]
    store.update_settings("default",application_enabled=True)
    assert store.relevant("default","new",{"category":"product"},opted_in=False)==[]
    assert store.relevant("default","new",{"category":"portrait"},opted_in=True)==[]
    assert len(store.relevant("default","new",{"category":"product"},opted_in=True))==1
    assert store.relevant("default","new",{"category":"product"},opted_in=True,overridden_keys={"product_color"})==[]
    store.record("default",event("old"))
    assert all(e.scope.kind!="project" for e in store.relevant("default","new",{"category":"product"},opted_in=True))


def test_conflicts_preserved_and_bound_values_do_not_change_on_edit(tmp_path):
    store=PreferenceStore(tmp_path,application_available=True)
    first=manual(store,text="Prefer cool color",key="color_balance")
    store.update_settings("default",application_enabled=True)
    binding=store.bind_new_project("default","new",{},True)
    store.edit("default",first["id"],first["revision"],{"interpretation":"Prefer warm color"})
    frozen=store.relevant("default","new",{},opted_in=True,binding=binding)
    assert frozen[0].interpretation=="Prefer cool color"
    manual(store,text="Prefer neutral color",key="color_balance")
    assert len(store.view("default")["preferences"])==2
    assert store.relevant("default","another",{},opted_in=True)==[]


def test_gate_is_fail_closed_for_all_prompt_stages_and_does_not_mutate_frozen_brief(tmp_path):
    store=PreferenceStore(tmp_path)
    entry=manual(store,text="MEMORY_SENTINEL",key="color_balance")
    state=SimpleNamespace(id="p",owner_id=store.owner_id,profile_id="default",preference_context={},feedback=[],
        memory_binding={"enabled":True,"owner_id":store.owner_id,"profile_id":"default","entries":[entry]})
    gate=PromptMemoryGate(store,lambda _:state)
    payload={"user_request":"CURRENT_REQUEST", "remembered_defaults":"MEMORY_SENTINEL",
        "brief":{"directives":[{"provenance":"memory_default","source_id":"memory:x","outcome":"MEMORY_SENTINEL"},
                               {"provenance":"user_answer","outcome":"CURRENT_FEEDBACK"}]},
        "allowed_source_ids":["memory:x:1","user_request"]}
    original=copy.deepcopy(payload)
    for stage in gate.STAGES|{"analysis"}:
        projected=gate.build(stage,payload,"p")
        assert "MEMORY_SENTINEL" not in json.dumps(projected)
        assert "CURRENT_REQUEST" in json.dumps(projected)
        assert "CURRENT_FEEDBACK" in json.dumps(projected)
    assert payload==original
    payload["ledger"]=store.view("default")
    payload["recipe"]={"proposal":{"parameters":{"contrast":1.0},"summary":"MEMORY_SENTINEL",
        "decisions":[{"source_ids":["memory:"+entry["id"]+":1"],"reason":"MEMORY_SENTINEL"}]}}
    for stage in gate.STAGES:
        projected=gate.build(stage,payload,"p")
        assert "MEMORY_SENTINEL" not in json.dumps(projected)
        assert projected["recipe"]["proposal"]["parameters"]=={"contrast":1.0}


def test_current_key_override_wins_in_prepared_future_interface(tmp_path):
    store=PreferenceStore(tmp_path,application_available=True)
    manual(store,text="Prefer cool colors",key="color_balance")
    store.update_settings("default",application_enabled=True)
    binding=store.bind_new_project("default","new",{},True)
    state=SimpleNamespace(id="new",owner_id=store.owner_id,profile_id="default",preference_context={},feedback=[],rubric=None,memory_binding=binding)
    gate=PromptMemoryGate(store,lambda _:state)
    assert gate.build("rubric",{"user_request":"Make it warm"},"new",{"color_balance"})=={"user_request":"Make it warm"}
    assert gate.build("rubric",{"user_request":""},"new")["remembered_defaults"]["entries"]


def test_answer_capture_and_profile_isolation_api(settings,video):
    with client(settings,FakeMuse()) as c:
        state=start(c,upload(c,video))
        response=c.post("/api/projects/"+state["id"]+"/answers",json={
            "revision":state["revision"],"command_id":str(uuid4()),
            "answer":{"question_id":"q1","response_type":"option","response":"natural"}})
        assert response.status_code==202
        wait(c,state["id"],"brief_ready")
        data=c.get("/api/preferences/profiles/default").json()
        assert len(data["preferences"])==2
        answer_entry=next(p for p in data["preferences"] if p["question_ids"])
        assert answer_entry["original_wording"]=="natural"
        assert answer_entry["scope"]["project_id"]==state["id"]
        assert data["settings"]=={"capture_enabled":True,"application_enabled":False}
        other=c.post("/api/preferences/profiles",json={"name":"Other"}).json()["id"]
        assert c.get("/api/preferences/profiles/"+other).json()["preferences"]==[]
        mismatch=c.post("/api/preferences/profiles/"+other+"/events",json=event(state["id"]).model_dump(mode="json"))
        assert mismatch.status_code==409
        assert c.patch("/api/preferences/profiles/default/settings",json={"application_enabled":True}).status_code==409


def test_current_feedback_revises_brief_while_capture_and_application_are_off(settings,video):
    muse=FakeMuse()
    with client(settings,muse) as c:
        state=start(c,upload(c,video),"explicit: keep original colors")
        old=copy.deepcopy(state["brief"])
        before=c.get("/api/preferences/profiles/default").json()
        c.patch("/api/preferences/profiles/default/settings",json={"capture_enabled":False})
        body={"command_id":str(uuid4()),"revision":state["revision"],"evidence":{
            "kind":"feedback","original_wording":"Less orange. Preserve the shadows."}}
        response=c.post("/api/projects/"+state["id"]+"/feedback",json=body)
        assert response.status_code==200,response.text
        current=response.json()
        assert current["brief"]["directives"][-1]["outcome"]=="Less orange. Preserve the shadows."
        assert current["brief_revision"]>state["brief_revision"]
        assert c.app.state.service.store.get(state["id"]).history[-1]["brief"]==old
        assert c.post("/api/projects/"+state["id"]+"/feedback",json=body).status_code==200
        assert len(c.app.state.service.store.get(state["id"]).feedback)==1
        assert c.get("/api/preferences/profiles/default").json()["preferences"]==before["preferences"]
        assert muse.analysis_calls==1 and muse.proposal_calls==1


def test_version_selection_is_captured_without_creating_likes(settings,video):
    with client(settings,FakeMuse()) as c:
        state=upload(c,video)
        r=c.post("/api/projects/"+state["id"]+"/feedback",json={
            "command_id":str(uuid4()),"revision":0,
            "evidence":{"kind":"version_selected","version_id":"source"}})
        assert r.status_code==200
        data=c.get("/api/preferences/profiles/default").json()
        assert len(data["events"])==1 and data["preferences"]==[]


def test_actual_sdk_prompt_boundaries_exclude_recorded_memory(settings,video):
    with client(settings,FakeMuse()) as c:
        state=start(c,upload(c,video),"explicit: preserve colors")
        svc=c.app.state.service
        entry=manual(svc.preferences,text="MEMORY_SENTINEL_NOT_FOR_MODEL")
        model=svc.store.get(state["id"])
        model.owner_id=svc.preferences.owner_id
        model.memory_binding={"enabled":True,"owner_id":model.owner_id,"profile_id":"default","entries":[entry]}
        model.brief.directives.append(Directive(dimension="appearance",scope="whole clip",outcome="CURRENT_VIDEO_FEEDBACK",
            provenance="user_answer",strength="strong",source_id="current-feedback",verification_hint="Current correction"))
        model.brief.directives.append(Directive(dimension="appearance",scope="whole clip",outcome="MEMORY_SENTINEL_NOT_FOR_MODEL",
            provenance="memory_default",strength="optional",source_id="memory:"+entry["id"]+":1",verification_hint="Default"))
        svc.store.save(model)
        captured=[]
        analysis=SceneAnalysis(summary="Synthetic source",observations=[
            VisualObservation(id="o1",start=0,end=1,description="Pattern",uncertainty="")],caveats=[])
        grade=GradeProposal(parameters={"contrast":1.0,"saturation":1.0,"gamma":1.0},summary="Keep current direction",
            decisions=[{"parameter":"contrast","reason":"Current request","source_ids":["user_request"]}],limitations=[])
        def parse(**kw):
            captured.append(kw)
            parsed=Proposal() if kw.get("response_format") is Proposal else grade
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))],usage=None)
        def parse_responses(**kw):
            captured.append(kw)
            return SimpleNamespace(output_parsed=analysis,usage=None,status="completed")
        class SDK:
            beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
            responses=SimpleNamespace(parse=parse_responses)
            def __enter__(self):return self
            def __exit__(self,*_):pass
        provider=MuseProvider(settings,svc.prompt_gate)
        provider.client=lambda:SDK()
        session=RubricSession(RubricInput(user_request="CURRENT_REQUEST",observations=[],capabilities={},
                                        project_id=state["id"]))
        provider.propose(session)
        provider.analyze(Path("unused"),1,"cached-file",lambda _:None)
        provider.grade(model.brief,model.analysis,model.source,[],[])
        serialized=str(captured)
        assert "MEMORY_SENTINEL_NOT_FOR_MODEL" not in serialized
        assert "CURRENT_REQUEST" in serialized and "CURRENT_VIDEO_FEEDBACK" in serialized


def test_capture_outbox_replay_is_idempotent_even_after_preference_deletion(settings,video):
    with client(settings,FakeMuse()) as c:
        state=start(c,upload(c,video))
        svc=c.app.state.service
        original=svc.preferences.atomic
        def fail_write(*args):raise OSError("fixture write failure")
        svc.preferences.atomic=fail_write
        r=c.post("/api/projects/"+state["id"]+"/answers",json={
            "command_id":str(uuid4()),"revision":state["revision"],
            "answer":{"question_id":"q1","response_type":"option","response":"natural"}})
        assert r.status_code==202
        done=wait(c,state["id"],"brief_ready")
        pending=copy.deepcopy(svc.store.get(state["id"]).memory_outbox)
        assert pending and done["brief"]["answers"]
        svc.preferences.atomic=original
        assert c.post("/api/projects/"+state["id"]+"/preference-capture/retry").status_code==200
        data=svc.preferences.view("default")
        assert len([e for e in data["events"] if e["kind"]=="rubric_answer"])==1
        entry=next(p for p in data["preferences"] if p["question_ids"])
        svc.preferences.delete("default",entry["id"],entry["revision"])
        again=svc.store.get(state["id"]);again.memory_outbox=pending;svc.store.save(again)
        c.post("/api/projects/"+state["id"]+"/preference-capture/retry")
        assert entry["id"] not in {p["id"] for p in svc.preferences.view("default")["preferences"]}


def test_entry_management_api_is_explicit_scoped_and_revision_checked(settings):
    with client(settings,FakeMuse()) as c:
        url="/api/preferences/profiles/default"
        body={"command_id":str(uuid4()),"original_wording":"For product videos preserve original product color.",
              "interpretation":"Preserve original product color","dimension":"boundaries","preference_key":"product_color",
              "scope":{"kind":"category","project_id":None,"conditions":{"category":"product"}}}
        first=c.post(url+"/entries",json=body)
        assert first.status_code==201,first.text
        assert c.post(url+"/entries",json=body).status_code==201
        data=c.get(url).json()
        assert len(data["events"])==len(data["preferences"])==1
        entry=data["preferences"][0]
        assert entry["scope"]["conditions"]=={"category":"product"}
        assert c.patch(url+"/entries/"+entry["id"],json={
            "revision":entry["revision"],"interpretation":"Preserve paint and label appearance"}).status_code==200
        assert c.patch(url+"/entries/"+entry["id"],json={
            "revision":entry["revision"],"status":"contradicted"}).status_code==409
        updated=c.get(url).json()["preferences"][0]
        assert c.delete(url+"/entries/"+entry["id"],params={"revision":updated["revision"]}).status_code==200
        assert c.get(url).json()["preferences"]==[]
        assert c.post(url+"/entries",json=body).status_code==201
        assert c.get(url).json()["preferences"]==[]


def test_capture_read_failure_and_partial_delivery_keep_recoverable_error(settings):
    with client(settings,FakeMuse()) as c:
        svc=c.app.state.service
        state=State(id=str(uuid4()),status="ready",stage="ready",filename="fixture.mp4")
        first=event(state.id)
        second=event(state.id)
        original_settings=svc.preferences.settings
        def unavailable(*_):
            raise OSError("fixture settings read failure")
        svc.preferences.settings=unavailable
        svc.queue_memory(state,first)
        assert state.memory_outbox and state.memory_capture_error
        svc.preferences.settings=original_settings
        svc.queue_memory(state,second)
        svc.store.save(state)
        original_record=svc.preferences.record
        def partially_available(profile,evidence):
            if evidence.event_id==first.event_id:
                raise OSError("fixture single delivery failure")
            return original_record(profile,evidence)
        svc.preferences.record=partially_available
        svc.drain_memory(state)
        assert len(state.memory_outbox)==1 and state.memory_capture_error
        assert svc.store.get(state.id).memory_capture_error
        svc.preferences.record=original_record
        svc.drain_memory(state)
        assert not state.memory_outbox and state.memory_capture_error is None
        assert len(svc.preferences.view("default")["events"])==2


def test_prepared_defaults_are_optional_traceable_and_never_permissions():
    from takeone_rubric_v1.rubric import RememberedDefault
    s=RubricSession(RubricInput(user_request="My current request",observations=[],capabilities={},
        memory_defaults=[RememberedDefault(id="pref",revision=2,dimension="appearance",key="color_balance",outcome="Prefer cool colors")]))
    assert s.next_question(Proposal(memory_used_ids=["pref"])) is None
    brief=s.brief()
    assert brief.directives[0].provenance=="memory_default"
    assert brief.directives[0].strength=="optional"
    assert brief.explicit_constraints==[]
    assert brief.memory_influences==[{"entry_id":"pref","revision":2,"stage":"brief","target":"memory:pref:2"}]
    # User answers in the same dimension take precedence; the memory is not baked in.
    s.directives.append(Directive(dimension="appearance",scope="whole clip",outcome="Make it warm",
        provenance="user_answer",strength="strong",source_id="current",verification_hint="Current choice"))
    assert [d.outcome for d in s.brief().directives]==["Make it warm"]
    disabled=RubricSession(RubricInput(user_request="Current",observations=[],capabilities={}))
    disabled.next_question(Proposal(memory_used_ids=["invented-memory"]))
    assert disabled.brief().memory_influences==[]


def test_future_conflict_question_preserves_current_answer_over_memory():
    from takeone_rubric_v1.rubric import Candidate,Option,RememberedDefault
    s=RubricSession(RubricInput(user_request="Make this warm",observations=[],capabilities={"operations":["global_grade"]},
        memory_defaults=[RememberedDefault(id="pref",revision=1,dimension="appearance",key="color_balance",outcome="Cool look")]))
    q=Candidate(id="conflict",dimension="appearance",target_key="color_balance",preference_family="color_balance",
        scope="whole clip",question="Use the current warm request or the earlier cool default?",evidence_ids=["user_request"],
        uncertainty_type="preference",decision_fork="Choose the current warm direction or the remembered cool direction.",
        impact="high",preservation_risk=False,answerability="high",memory_refs=["pref"],memory_conflict=True,
        options=[Option(id="warm",label="Current warm direction",intended_outcome="Warm current direction",required_operations=["global_grade"]),
                 Option(id="cool",label="Cool default",intended_outcome="Cool direction",required_operations=["global_grade"])])
    assert s.next_question(Proposal(candidates=[q],memory_used_ids=["pref"])).id=="conflict"
    from takeone_rubric_v1.rubric import Answer
    s.record_answer(Answer(question_id=q.id,response_type="option",response="warm"))
    brief=s.brief()
    assert [d.outcome for d in brief.directives]==["Warm current direction"]
    assert brief.directives[0].provenance=="user_answer"
    assert brief.memory_influences==[]


def test_new_upload_profile_and_application_consent_are_checked(settings,video):
    with client(settings,FakeMuse()) as c:
        profile=c.post("/api/preferences/profiles",json={"name":"Products"}).json()["id"]
        with video.open("rb") as stream:
            response=c.post("/api/projects",files={"file":("test.mp4",stream,"video/mp4")},
                            data={"profile_id":profile,"preference_context":json.dumps({"category":"product"})})
        state=wait(c,response.json()["id"],"ready")
        assert state["profile_id"]==profile
        assert state["preference_context"]=={"category":"product"}
        start(c,state,"explicit natural look")
        assert len(c.get("/api/preferences/profiles/"+profile).json()["preferences"])==1
        assert c.get("/api/preferences/profiles/default").json()["preferences"]==[]
        with video.open("rb") as stream:
            denied=c.post("/api/projects",files={"file":("test.mp4",stream,"video/mp4")},
                          data={"profile_id":profile,"use_preferences":"true"})
        assert denied.status_code==409
        assert denied.json()["error"]["code"]=="MEMORY_CAPTURE_ONLY"
