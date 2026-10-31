import pytest

from rubric import (
    Answer, Candidate, CapabilityManifest, Dimension, Level,
    Observation, Option, Proposal, RubricInput, RubricSession,
)


def session():
    return RubricSession(RubricInput(
        user_request="Cinematic, but keep the motorcycle recognizable",
        observations=[Observation(id="obs1", scope="00:00-00:08", source="muse_visual",
                                  description="Motorcycle at night with bright signs")],
        capabilities=CapabilityManifest(operations=["global_grade", "regional_grade"]),
        technical_envelope=["SDR output"],
        explicit_constraints=["Keep motorcycle paint appearance recognizable"],
    ))


def question(**kw):
    base = dict(
        id="q1", dimension=Dimension.ATTENTION, target_key="subject_readability",
        scope="00:00-00:08 motorcycle", question="Should the bike stay readable or become a silhouette?",
        evidence_ids=["obs1"], uncertainty_type="preference",
        decision_fork="Readable needs subject isolation; silhouette allows darker overall subject.",
        impact=Level.HIGH, preservation_risk=False, answerability=Level.HIGH,
        options=[
            Option(id="readable", label="Readable", intended_outcome="Keep motorcycle readable",
                   required_operations=["regional_grade"]),
            Option(id="silhouette", label="Silhouette", intended_outcome="Allow a dark motorcycle silhouette",
                   required_operations=["global_grade"]),
        ],
    )
    base.update(kw)
    return Candidate(**base)


def test_select_record_brief():
    s = session()
    q = s.next_question(Proposal(candidates=[question()]))
    assert q.id == "q1"
    s.record_answer(Answer(question_id="q1", response_type="option", response="readable"))
    brief = s.brief()
    assert brief.directives[0].provenance == "user_answer"
    assert brief.directives[0].outcome == "Keep motorcycle readable"
    assert brief.explicit_constraints == ["Keep motorcycle paint appearance recognizable"]
    assert len(s.events) == 2


def test_reject_unavailable_operation():
    s = session()
    q = question(options=[
        Option(id="a", label="A", intended_outcome="With relighting", required_operations=["3d_relighting"]),
        Option(id="b", label="B", intended_outcome="Without relighting", required_operations=["global_grade"]),
    ])
    assert s.next_question(Proposal(candidates=[q])) is None
    assert "unavailable" in s.rejected[0]["reason"]


def test_nonpreference_and_hallucinated_evidence_rejected():
    s = session()
    qs = [question(id="q1", uncertainty_type="execution"), question(id="q2", evidence_ids=["invented"])]
    assert s.next_question(Proposal(candidates=qs)) is None


def test_answer_validation_and_no_reask():
    s = session()
    s.next_question(Proposal(candidates=[question()]))
    with pytest.raises(ValueError):
        s.record_answer(Answer(question_id="q1", response_type="option", response="nonexistent"))
    s.record_answer(Answer(question_id="q1", response_type="delegate"))
    assert s.next_question(Proposal(candidates=[question()])) is None
    assert len(s.brief().delegated_or_skipped) == 1


def test_bounded_interactions():
    s = session()
    s.max_decisions = 0
    assert s.next_question(Proposal(candidates=[question()])) is None


def test_rank_preservation_first_and_reject_duplicate_outcomes():
    s = session()
    unsafe_dup = question(id="bad", options=[
        Option(id="a", label="A", intended_outcome="Same"),
        Option(id="b", label="B", intended_outcome="Same"),
    ])
    high_impact = question(id="impact", target_key="impact", impact=Level.HIGH)
    preservation = question(id="preservation", target_key="paint", preservation_risk=True, impact=Level.MEDIUM)
    selected = s.next_question(Proposal(candidates=[unsafe_dup, high_impact, preservation]))
    assert selected.id == "preservation"
    assert any(x["candidate_id"] == "bad" for x in s.rejected)
