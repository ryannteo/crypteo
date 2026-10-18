"""Content snapshots protect stable identities from unversioned prompt drift."""

import pytest

from crypteo.research_prompts import (
    INVESTIGATION_INSTRUCTIONS,
    SHARED_POLICY,
    prompt_identity,
    prompt_messages,
    structured_schema,
)


@pytest.mark.parametrize(
    "stage,prompt_version,prompt_digest,schema_digest",
    [
        (
            "investigate",
            "investigate_prompt_v4",
            "850412dde6c09ce5b9ba74c1deec75cbd5a05a6ab766978aac6c91d530294172",
            "10a1bb6d0fbfb18dfac09e4864265aeafccc228c3f9765fea0e3fb6f14bdb84f",
        ),
        (
            "draft_thesis",
            "draft_thesis_prompt_v2",
            "991afdb1f18f04a095cdaff07577e0a445ac9885a3deaf01685a7e2d1b791792",
            "7a62eaa5f8f91a3a94a93b32477db6c287f1a2d586258f071a8fdccc9472da26",
        ),
        (
            "critique",
            "critique_prompt_v2",
            "9f3261966e7fb217c9de52cb4c00236d344081aee7afc9f8995c82f3665dbd74",
            "498c2f78279c95bd030323aa8411a16a7399c61c841480653b47e9e122ed2664",
        ),
        (
            "final_assessment",
            "final_assessment_prompt_v2",
            "3445cbadac61157d7a500c6ac396a21c3f6429bc7edaa98a4cfda37300d06865",
            "54f953c92fd301620f314d8fefb2b2ed037f0aa812c4bf4e39a8a6264d40adcc",
        ),
    ],
)
def test_v1_prompt_and_schema_snapshots(
    stage: str, prompt_version: str, prompt_digest: str, schema_digest: str
) -> None:
    assert prompt_identity(stage).version == prompt_version
    assert prompt_identity(stage).digest == "sha256:" + prompt_digest
    assert structured_schema(stage).identity.digest == "sha256:" + schema_digest


def test_investigation_requires_nonblank_summary_at_budget_boundary() -> None:
    system = prompt_messages("investigate", {})[0].content
    assert system == SHARED_POLICY + "\n\n" + INVESTIGATION_INSTRUCTIONS
    assert "summary must be a nonblank synthesis of the evidence" in system
    assert "even when stopping because attempts_remaining is zero" in system
    assert "When attempts_remaining is zero no tools are offered; return" in system
    assert "you must complete without requesting a tool" not in system


@pytest.mark.parametrize(
    "stage", ["investigate", "draft_thesis", "critique", "final_assessment"]
)
def test_shared_policy_explains_output_rejection_on_every_stage(stage: str) -> None:
    system = prompt_messages(stage, {})[0].content
    assert system.startswith(SHARED_POLICY)
    assert (
        "When\noutput_rejection is present, your previous output for this stage was "
        "rejected for\nthe listed problems; return a complete, corrected output."
    ) in system
