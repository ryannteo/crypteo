import json
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo import experiment
from crypteo.experiment import (
    ASSET_PRIORITY,
    CodeIdentity,
    DatasetIdentity,
    ExperimentManifest,
    ReplayConfig,
    build_manifest,
    canonical_bytes,
    current_code_identity,
)

T = datetime(2023, 1, 1, tzinfo=UTC)


def manifest() -> ExperimentManifest:
    return build_manifest(
        ReplayConfig.model_validate(
            dict(
                start=T,
                end=T + timedelta(hours=1),
                initial_cash="1000",
                assets=ASSET_PRIORITY,
                trade_terms={"price_increment": "0.1"},
                risk={"risk_fraction": "0.02", "max_holding_bars": 3},
                execution={
                    "entry_fee_rate": "0.01",
                    "exit_fee_rate": "0.02",
                    "adverse_slippage": "0.01",
                    "price_increment": "0.1",
                    "quantity_increment": "0.5",
                },
            )
        ),
        tuple(
            DatasetIdentity(
                pair=p, dataset_version="sha256:" + str(i) * 64, acquisition_id=None
            )
            for i, p in enumerate(ASSET_PRIORITY)
        ),
        CodeIdentity(git_commit="a" * 40, source_digest="sha256:" + "b" * 64),
    )


@pytest.mark.parametrize(
    "path,value",
    [
        (("code", "git_commit"), "c" * 40),
        (("code", "source_digest"), "sha256:" + "c" * 64),
        (("python_version",), "3.12.999"),
        (("pydantic_version",), "2.99.1"),
        (("replay", "start"), T - timedelta(hours=1)),
        (("replay", "end"), T + timedelta(hours=2)),
        (("replay", "initial_cash"), Decimal(1001)),
        (("replay", "risk", "risk_fraction"), Decimal("0.03")),
        (("replay", "risk", "max_holding_bars"), 4),
        (("replay", "execution", "entry_fee_rate"), Decimal("0.03")),
        (("replay", "execution", "exit_fee_rate"), Decimal("0.03")),
        (("replay", "execution", "adverse_slippage"), Decimal("0.03")),
        (("replay", "execution", "quantity_increment"), Decimal("0.3")),
    ],
)
def test_implemented_configuration_changes_manifest_identity(
    path: tuple[str, ...], value: Any
) -> None:
    original = manifest()
    data = original.model_dump()
    cursor = data
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    changed = ExperimentManifest.model_validate(data)
    assert changed.run_id != original.run_id
    assert changed.canonical_bytes() != original.canonical_bytes()


@pytest.mark.parametrize("case", ["tick", "asset", "dataset", "acquisition"])
def test_dataset_asset_and_terms_configuration_change_identity(case: str) -> None:
    original = manifest()
    data = original.model_dump()
    if case == "tick":
        data["replay"]["trade_terms"]["price_increment"] = Decimal("0.25")
        data["replay"]["execution"]["price_increment"] = Decimal("0.25")
    elif case == "asset":
        data["replay"]["assets"] = data["replay"]["assets"][:1]
        data["datasets"] = data["datasets"][:1]
    elif case == "dataset":
        data["datasets"][0]["dataset_version"] = "sha256:" + "f" * 64
    else:
        data["datasets"][0]["acquisition_id"] = "sha256:" + "f" * 64
    assert ExperimentManifest.model_validate(data).run_id != original.run_id


def test_manifest_canonical_bytes_are_minimal_and_identity_excludes_itself() -> None:
    original = manifest()
    encoded = original.canonical_bytes()
    data = json.loads(encoded)
    assert original.run_id == "sha256:" + sha256(encoded).hexdigest()
    assert set(data) == {
        "schema_version",
        "experiment_mode",
        "code",
        "python_version",
        "pydantic_version",
        "replay",
        "datasets",
        "scanner_policy",
        "trade_terms_policy",
    }
    assert data["replay"]["start"] == "2023-01-01T00:00:00.000000Z"
    assert data["replay"]["initial_cash"] == "1000"
    assert b"run_id" not in encoded
    restored = ExperimentManifest.model_validate_json(encoded)
    assert restored == original and restored.canonical_bytes() == encoded
    assert (
        canonical_bytes({"b": Decimal("-0.00"), "a": Decimal("1E+2")})
        == b'{"a":"100","b":"0"}'
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "2"),
        ("experiment_mode", "LLM"),
        ("scanner_policy", "another policy"),
        ("trade_terms_policy", "another policy"),
        ("created_at", T),
        ("run_id", "random"),
    ],
)
def test_unsupported_or_speculative_manifest_fields_rejected(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError):
        ExperimentManifest.model_validate(manifest().model_dump() | {field: value})


def test_manifest_requires_canonical_dataset_identity_order() -> None:
    data = manifest().model_dump()
    data["datasets"] = tuple(reversed(data["datasets"]))
    with pytest.raises(ValidationError, match="canonical ordered"):
        ExperimentManifest.model_validate(data)


def test_source_digest_is_path_mtime_and_discovery_order_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout="a" * 40
        ),
    )
    for root_name, names in [("first", ("a.py", "b.py")), ("second", ("b.py", "a.py"))]:
        root = tmp_path / root_name
        package = root / "src" / "crypteo"
        package.mkdir(parents=True)
        (root / ".git").mkdir()
        for name in names:
            (package / name).write_text(name + "\n")
        (root / "pyproject.toml").write_text("project settings\n")
        (root / "README.md").write_text(root_name)
        (package / "cache.pyc").write_text(root_name)
        monkeypatch.setattr(experiment, "__file__", str(package / "experiment.py"))
        code = current_code_identity()
        assert code.git_commit == "a" * 40
        digests.append(code.source_digest)
    assert digests[0] == digests[1]
    (package / "a.py").write_text("changed source\n")
    changed = current_code_identity()
    assert changed.source_digest != code.source_digest
    (package / "new.py").write_text("new source\n")
    assert current_code_identity().source_digest != changed.source_digest
    before = current_code_identity()
    (root / "pyproject.toml").write_text("changed settings\n")
    assert current_code_identity().source_digest != before.source_digest


def test_executing_checkout_identity_is_stable_and_has_git_commit() -> None:
    first, second = current_code_identity(), current_code_identity()
    assert first == second
    assert first.git_commit is not None
    assert len(first.source_digest) == 71


def test_source_identity_requires_checkout_or_explicit_build_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "src" / "crypteo"
    package.mkdir(parents=True)
    (package / "experiment.py").write_text("example source")
    monkeypatch.setattr(experiment, "__file__", str(package / "experiment.py"))
    with pytest.raises(ValueError, match="supply explicit CodeIdentity"):
        current_code_identity()
