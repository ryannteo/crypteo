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
            "investigate_prompt_v3",
            "bb8387954ea1ede2d2f1c0c23497c2116f40fe8ffdeae6fe29d06fb8027a96e3",
            "10a1bb6d0fbfb18dfac09e4864265aeafccc228c3f9765fea0e3fb6f14bdb84f",
        ),
        (
            "draft_thesis",
            "draft_thesis_prompt_v1",
            "1bee59cc306f41fff1a68ea552e9f739069920fb49daf13ce7e6aa9ef2ff482d",
            "7a62eaa5f8f91a3a94a93b32477db6c287f1a2d586258f071a8fdccc9472da26",
        ),
        (
            "critique",
            "critique_prompt_v1",
            "423fded4cc64cdebcd9c4fe0d9e5da3f2678d7bed02324a9cf9853944d881cca",
            "498c2f78279c95bd030323aa8411a16a7399c61c841480653b47e9e122ed2664",
        ),
        (
            "final_assessment",
            "final_assessment_prompt_v1",
            "e71db55ee6dde565b3777f2f10cbe3d3ec401d005be7b2ffd403bfa180a4af94",
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
