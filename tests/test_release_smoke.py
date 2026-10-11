"""Run the release smoke script in-process, covering the editable install."""

import pytest
import release_smoke


def test_release_smoke_passes_with_pinned_digests(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert release_smoke.main([]) == 0
    output = capsys.readouterr().out
    for name, digest in release_smoke.GOLDEN.items():
        assert f"{name}: {digest}\n" in output
    assert output.endswith("release smoke: ok\n")
