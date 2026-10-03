"""Content snapshots protect stable identities from unversioned prompt drift."""

import pytest

from crypteo.research_prompts import prompt_identity, structured_schema


@pytest.mark.parametrize(
    "stage,prompt_digest,schema_digest",
    [
        (
            "investigate",
            "ed888c6bf3ec27d7cdcad77ba44fd1ce75c5bc08b77006fe04fbeae77c3f962a",
            "10a1bb6d0fbfb18dfac09e4864265aeafccc228c3f9765fea0e3fb6f14bdb84f",
        ),
        (
            "draft_thesis",
            "1bee59cc306f41fff1a68ea552e9f739069920fb49daf13ce7e6aa9ef2ff482d",
            "7a62eaa5f8f91a3a94a93b32477db6c287f1a2d586258f071a8fdccc9472da26",
        ),
        (
            "critique",
            "423fded4cc64cdebcd9c4fe0d9e5da3f2678d7bed02324a9cf9853944d881cca",
            "498c2f78279c95bd030323aa8411a16a7399c61c841480653b47e9e122ed2664",
        ),
        (
            "final_assessment",
            "e71db55ee6dde565b3777f2f10cbe3d3ec401d005be7b2ffd403bfa180a4af94",
            "54f953c92fd301620f314d8fefb2b2ed037f0aa812c4bf4e39a8a6264d40adcc",
        ),
    ],
)
def test_v1_prompt_and_schema_snapshots(
    stage: str, prompt_digest: str, schema_digest: str
) -> None:
    assert prompt_identity(stage).digest == "sha256:" + prompt_digest
    assert structured_schema(stage).identity.digest == "sha256:" + schema_digest
