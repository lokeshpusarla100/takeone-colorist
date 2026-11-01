from uuid import uuid4

import pytest

from takeone.contracts import State
from takeone.errors import Problem
from takeone.usage import usage_details
from tests.test_backend import FakeMuse, client, settings, start, upload, video


@pytest.mark.parametrize("usage",[
    {"input_tokens":120,"output_tokens":30,"total_tokens":150,
     "input_tokens_details":{"cached_tokens":40},"output_tokens_details":{"reasoning_tokens":20}},
    {"prompt_tokens":120,"completion_tokens":30,
     "prompt_tokens_details":{"cached_tokens":40},"completion_tokens_details":{"reasoning_tokens":20}},
])
def test_endpoint_accounting_shapes(usage):
    assert usage_details(usage)==dict(input_tokens=120,output_tokens=30,total_tokens=150,cached_tokens=40,reasoning_tokens=20)


def test_missing_usage_stays_unknown_and_explicit_zero_is_valid():
    assert all(value is None for value in usage_details({}).values())
    assert usage_details({"input_tokens":True,"output_tokens":-1,"total_tokens":"3"})["total_tokens"] is None
    assert usage_details({"input_tokens":0,"output_tokens":0})["total_tokens"]==0


def test_request_receipt_is_idempotent_persisted_and_independent_of_reset_call_numbers(settings):
    with client(settings,FakeMuse()) as c:
        svc=c.app.state.service
        state=State(id=str(uuid4()),status="ready",stage="ready",filename="fixture.mp4")
        svc.store.save(state)
        first=svc.count_call(state.id,"Inspecting video with Muse")
        svc.record_usage(state.id,first,"analysis",{"input_tokens":12,"output_tokens":20},1)
        svc.record_usage(state.id,first,"analysis",{"input_tokens":12,"output_tokens":20},1)
        svc.mutate(state.id,lambda s:setattr(s,"model_calls",0))
        second=svc.count_call(state.id,"Selecting a useful clarification")
        svc.record_usage(state.id,second,"rubric",{"prompt_tokens":5,"completion_tokens":7},1)
        events=c.get("/api/projects/"+state.id).json()["events"]
        receipts=[e["details"] for e in events if e.get("details",{}).get("token_usage")]
        assert len(receipts)==2 and first!=second
        assert [e["call"] for e in receipts]==[1,1]
        assert sum(e["total_tokens"] for e in receipts)==44


def test_usage_saved_before_controller_rejects_an_invalid_proposal(settings,video):
    class InvalidProposal(FakeMuse):
        def propose(self,session):
            super().propose(session)
            session.events.append({"event":"muse_proposal","usage":{"prompt_tokens":31,"completion_tokens":17}})
            return None
    with client(settings,InvalidProposal()) as c:
        state=start(c,upload(c,video))
        assert state["status"]=="failed"
        receipts=[e["details"] for e in state["events"] if e.get("details",{}).get("token_usage")]
        assert [r["total_tokens"] for r in receipts]==[32,48]


def test_reported_usage_on_failed_request_is_recorded_without_response_text(settings,video):
    class Truncated(FakeMuse):
        def analyze(self,*_):
            raise Problem("MUSE_RESPONSE_TRUNCATED","Fixture limit","Retry",502,
                          usage_reported=True,input_tokens=10,output_tokens=2400,total_tokens=2410)
    with client(settings,Truncated()) as c:
        state=start(c,upload(c,video))
        assert state["status"]=="failed"
        receipts=[e["details"] for e in state["events"] if e.get("details",{}).get("token_usage")]
        assert len(receipts)==1 and receipts[0]["total_tokens"]==2410
        assert receipts[0]["usage_stage"]=="analysis"
