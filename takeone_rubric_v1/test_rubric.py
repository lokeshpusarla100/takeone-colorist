import pytest

from takeone_rubric_v1.rubric import (
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


def test_snapshot_and_literal_constraint_validation():
    s = session()
    s.next_question(Proposal(
        candidates=[question()],
        explicit_constraints=["keep the motorcycle recognizable", "Invented permission"],
    ))
    restored = RubricSession.restore(s.snapshot())
    assert restored.pending.id == "q1"
    assert "keep the motorcycle recognizable" in restored.inputs.explicit_constraints
    assert "Invented permission" not in restored.inputs.explicit_constraints
    restored.record_answer(Answer(question_id="q1", response_type="option", response="readable"))
    again = RubricSession.restore(restored.snapshot())
    assert again.brief().answers[0].question_id == "q1"


def tonal_question(**changes):
    base = dict(
        id="shadow_choice", dimension="appearance", target_key="shadow_contrast", scope="0-8s",
        question="Keep night shadows detailed or make them deep and contrasty?",
        decision_fork="Keep shadow detail visible or choose a darker night mood.",
        options=[
            Option(id="detail", label="Keep detail", intended_outcome="Retain visible shadow detail", required_operations=["global_grade"]),
            Option(id="dark", label="Deep shadows", intended_outcome="Darker shadows with cinematic depth", required_operations=["global_grade"]),
        ],
    )
    base.update(changes)
    return question(**base)


@pytest.mark.parametrize("response_type,response", [("option", "dark"), ("skip", ""), ("delegate", ""), ("free_text", "Dark but readable.")])
def test_renamed_brightness_fork_does_not_repeat_shadow_choice(response_type, response):
    s = session()
    s.next_question(Proposal(candidates=[tonal_question()]))
    s.record_answer(Answer(question_id="shadow_choice", response_type=response_type, response=response))
    again = tonal_question(id="brightness_choice", target_key="gamma_brightness", scope="whole clip global",
        question="Keep night brightness dark and moody or lift it for clearer detail?",
        decision_fork="Dark gamma keeps blacks deep; lifted gamma makes details visible.")
    assert s.next_question(Proposal(candidates=[again])) is None
    assert s.asked_count == 1
    assert "already covered" in s.rejected[-1]["reason"]


def test_color_question_remains_available_after_tonal_choice():
    s = session()
    s.next_question(Proposal(candidates=[tonal_question()]))
    s.record_answer(Answer(question_id="shadow_choice", response_type="option", response="dark"))
    color = tonal_question(id="color", target_key="color", preference_family="color_intensity",
        question="Muted colors or richer saturation?", decision_fork="Muted color versus a richer palette.",
        options=[
            Option(id="muted",label="Muted",intended_outcome="Restrained color intensity",required_operations=["global_grade"]),
            Option(id="rich",label="Rich",intended_outcome="Richer color intensity",required_operations=["global_grade"]),
        ])
    assert s.next_question(Proposal(candidates=[color])).id == "color"


def test_independent_contrast_texture_is_not_treated_as_brightness():
    s = session()
    s.next_question(Proposal(candidates=[tonal_question(preference_family="tonal_readability")]))
    s.record_answer(Answer(question_id="shadow_choice", response_type="option", response="dark"))
    texture = tonal_question(id="texture", target_key="texture", preference_family="contrast_texture",
        question="Soft tonal transitions or punchier texture?", decision_fork="Choose gentle transitions or crisp texture.",
        options=[
            Option(id="soft",label="Soft",intended_outcome="Gentle transitions",required_operations=["global_grade"]),
            Option(id="crisp",label="Crisp",intended_outcome="Crisp texture",required_operations=["global_grade"]),
        ])
    assert s.next_question(Proposal(candidates=[texture])).id == "texture"


def test_separate_regional_scopes_do_not_block_each_other():
    s = session()
    options=[
        Option(id="detail",label="Detail",intended_outcome="Visible shadow detail",required_operations=["regional_grade"]),
        Option(id="dark",label="Dark",intended_outcome="Dark shadows",required_operations=["regional_grade"]),
    ]
    s.next_question(Proposal(candidates=[tonal_question(scope="motorcycle", options=options)]))
    s.record_answer(Answer(question_id="shadow_choice", response_type="option", response="dark"))
    regional = tonal_question(id="background",target_key="background",scope="background",options=options)
    assert s.next_question(Proposal(candidates=[regional])).id == "background"


def test_history_survives_restart_and_old_snapshots_still_deduplicate():
    s = session()
    s.next_question(Proposal(candidates=[tonal_question()]))
    s.record_answer(Answer(question_id="shadow_choice", response_type="option", response="dark"))
    snapshot = s.snapshot()
    for legacy in (False, True):
        value = dict(snapshot)
        if legacy:
            value.pop("questions")
        restored = RubricSession.restore(value)
        decision = restored.answered_decisions()[0]
        assert decision["selected_outcome"] == "Darker shadows with cinematic depth"
        repeated = tonal_question(id="new_id",target_key="new_key")
        assert restored.next_question(Proposal(candidates=[repeated])) is None


def test_adapter_sends_question_meaning_not_only_option_ids():
    import json
    from types import SimpleNamespace
    from takeone_rubric_v1.muse_adapter import propose
    s = session()
    s.next_question(Proposal(candidates=[tonal_question()]))
    s.record_answer(Answer(question_id="shadow_choice", response_type="option", response="dark"))
    captured = {}
    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=Proposal()))], usage=None)
    client = SimpleNamespace(beta=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))))
    propose(s, client=client)
    context = json.loads(captured["messages"][1]["content"])
    previous = context["previous_decisions"][0]
    assert previous["question"] == "Keep night shadows detailed or make them deep and contrasty?"
    assert previous["selected_outcome"] == "Darker shadows with cinematic depth"
    assert len(previous["options"]) == 2
    assert "tonal_readability" in previous["preference_families"]


def test_budget_stop_is_disclosed_without_inventing_missing_preferences():
    s = session()
    s.max_decisions = 1
    s.next_question(Proposal(candidates=[tonal_question()]))
    s.record_answer(Answer(question_id="shadow_choice", response_type="option", response="dark"))
    assert s.next_question(Proposal()) is None
    assert any("question limit" in note for note in s.brief().unresolved)
    assert len(s.brief().directives) == 1
