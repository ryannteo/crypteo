"""Independent replay association, manifest identity, and metric forgery checks."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_replay import BTC, CODE, ETH, SOL, D, H, T, config, dataset, payloads
from test_research_replay import (
    WORKFLOW,
    ReplayModel,
    execute,
    frozen,
    inputs,
)

from crypteo.domain.replay import ReplayResult
from crypteo.domain.replay_research import (
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchReplayMetrics,
    research_policy,
)
from crypteo.domain.research import Opportunity
from crypteo.replay import run_scanner_only
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import ResearchAdmissionError, validate_research_links
from crypteo.research_tools import tool_definitions
from crypteo.scanner import OpportunityScanner


@pytest.mark.parametrize(
    "field,value",
    [
        ("research_run_id", "forged"),
        ("opportunity_id", "forged"),
        ("context_digest", "sha256:" + "0" * 64),
        ("policy_digest", "sha256:" + "0" * 64),
        ("research_at", T + H),
        ("status", "INTERRUPTED"),
        ("assessment_origin", "RUNTIME"),
        ("outcome", "NO_TRADE"),
        ("outcome", "ABSTAIN"),
    ],
)
def test_pure_replay_validation_rejects_forged_research_associations(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    data = result.model_dump()
    next(r for r in data["records"] if r["kind"] == "RESEARCH")["payload"][field] = (
        value
    )
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("confidence", 99),
        ("artifact_checksum", "sha256:" + "0" * 64),
        ("ordered_journal_digest", "sha256:" + "0" * 64),
        ("terminal_sequence", 999),
    ],
)
def test_external_validation_catches_projection_forgery_not_provable_locally(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    result = execute(store)
    data = result.model_dump()
    next(r for r in data["records"] if r["kind"] == "RESEARCH")["payload"][field] = (
        value
    )
    internal_only = ReplayResult.model_validate(data)
    with pytest.raises(ResearchAdmissionError, match="projection differs"):
        validate_research_links(internal_only, store=store)


def test_external_validation_rejects_coherent_changed_nontrade_outcome(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    result = execute(store, model=ReplayModel({BTC.value: "NO_TRADE"}))
    data = result.model_dump()
    next(r for r in data["records"] if r["kind"] == "RESEARCH")["payload"][
        "outcome"
    ] = "ABSTAIN"
    data["research_metrics"]["no_trade_count"] = 0
    data["research_metrics"]["abstain_count"] = 1
    internal_only = ReplayResult.model_validate(data)
    with pytest.raises(ResearchAdmissionError):
        validate_research_links(internal_only, store=store)


@pytest.mark.parametrize(
    "kind",
    ["RESEARCH", "TERMS", "RESEARCH_SELECTION", "RISK_AUTHORIZATION", "SUBMISSION"],
)
def test_missing_required_research_trade_stage_rejected(
    tmp_path: Path, kind: str
) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    data = result.model_dump()
    data["records"] = [r for r in data["records"] if r["kind"] != kind]
    for index, record in enumerate(data["records"]):
        record["sequence"] = index
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(data)


def test_duplicate_research_record_rejected(tmp_path: Path) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    data = result.model_dump()
    records = list(data["records"])
    record = next(r for r in records if r["kind"] == "RESEARCH")
    records.insert(records.index(record), record.copy())
    for index, item in enumerate(records):
        item["sequence"] = index
    data["records"] = records
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(data)


def test_valid_artifact_for_another_valid_opportunity_cannot_be_remapped(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    cfg = config(assets=(BTC, ETH), end=T + H)
    datasets = [dataset(pair=p) for p in (BTC, ETH)]
    result = execute(store, cfg=cfg, datasets=datasets)
    settings = frozen(result)
    a, b = settings.mapping
    changed = FrozenResearchSettings(
        configuration=WORKFLOW,
        mapping=(
            a.model_copy(
                update={
                    "research_run_id": b.research_run_id,
                    "artifact_checksum": b.artifact_checksum,
                }
            ),
            b.model_copy(
                update={
                    "research_run_id": a.research_run_id,
                    "artifact_checksum": a.artifact_checksum,
                }
            ),
        ),
    )
    with pytest.raises(ResearchAdmissionError, match="mismatch"):
        execute(store, cfg=cfg, datasets=datasets, settings=changed)


@pytest.mark.parametrize("field", list(ResearchReplayMetrics.model_fields))
def test_every_research_metric_is_recomputed(tmp_path: Path, field: str) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    data = result.model_dump()
    value = data["research_metrics"][field]
    data["research_metrics"][field] = (
        [{"reason": "PORTFOLIO_BUSY", "count": 1}]
        if field == "risk_rejection_reasons"
        else (value + 1 if value is not None else D(1))
    )
    with pytest.raises(ValidationError, match="research metrics"):
        ReplayResult.model_validate(data)


def test_mixed_outcome_metric_denominators_and_candidate_order(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    cfg = config(assets=(SOL, ETH, BTC), end=T + H)
    # Research rejection leaves the one allocation slot available to SOL.
    datasets = [dataset(inputs()[0].candles, pair=p) for p in (SOL, ETH, BTC)]
    result = execute(
        store,
        cfg=cfg,
        datasets=datasets,
        model=ReplayModel({BTC.value: "NO_TRADE", ETH.value: "ABSTAIN"}),
    )
    metrics = result.research_metrics
    assert metrics is not None
    assert (
        metrics.opportunities_assessed,
        metrics.trade_count,
        metrics.no_trade_count,
        metrics.abstain_count,
    ) == (3, 1, 1, 1)
    assert metrics.submission_count == metrics.entry_count == 1
    assert metrics.research_to_entry_conversion == D("0." + "3" * 50)
    assert metrics.trade_to_entry_conversion == 1
    assert result.final_portfolio.orders[0].pair == SOL


def test_same_pair_id_order_no_later_candidates_in_model_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: Any) -> tuple[Opportunity, ...]:
        items = original(self, pair=pair)
        return tuple(
            item.model_copy(update={"opportunity_id": name})
            for item in items
            for name in ("z-candidate", "a-candidate")
        )

    seen: list[str] = []

    def inspect(request: Any) -> None:
        if request.stage == "final_assessment":
            data = json.loads(request.messages[-1].content)["research_data"]
            seen.append(data["context"]["request"]["opportunity"]["opportunity_id"])
            assert "opportunities" not in data

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"), model=ReplayModel(hook=inspect)
    )
    assert seen == ["a-candidate", "z-candidate"]
    assert result.research_metrics is not None
    assert result.research_metrics.risk_rejection_count == 1
    assert len({r.research_run_id for r in payloads(result, ReplayResearchRecord)}) == 2


@pytest.mark.parametrize("field", ["trial_id", "max_research_runs", "configuration"])
def test_fresh_input_changes_identity(tmp_path: Path, field: str) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    assert isinstance(result.manifest, ResearchReplayManifest)
    data = result.manifest.model_dump()
    if field == "trial_id":
        data["research"][field] = "another-trial"
    elif field == "max_research_runs":
        data["research"][field] = 100
    else:
        configuration = WORKFLOW.model_copy(
            update={"model": WORKFLOW.model.model_copy(update={"model": "different"})}
        )
        data["research"][field] = configuration
        data["policy"] = research_policy(configuration)
    changed = ResearchReplayManifest.model_validate(data)
    assert changed.run_id != result.run_id
    assert "response_id" not in changed.canonical_bytes().decode()
    assert "artifact_checksum" not in changed.canonical_bytes().decode()


@pytest.mark.parametrize(
    "field",
    [
        "workflow",
        "market_research",
        "model_view",
        "prompts",
        "schemas",
        "tools",
        "configuration_digest",
    ],
)
def test_unsupported_policy_identity_rejected(tmp_path: Path, field: str) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    data = result.manifest.model_dump()
    data["policy"][field] = (
        [] if field in ("prompts", "schemas", "tools") else "sha256:" + "0" * 64
    )
    with pytest.raises(ValidationError):
        ResearchReplayManifest.model_validate(data)


def test_policy_identifies_compact_model_view(tmp_path: Path) -> None:
    assert research_policy(WORKFLOW).model_view == "compact_research_view_v1"
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    assert isinstance(result.manifest, ResearchReplayManifest)
    policy = json.loads(result.manifest.canonical_bytes())["policy"]
    assert policy["model_view"] == "compact_research_view_v1"


def test_policy_enumerates_every_subject_and_bound_benchmark_subset() -> None:
    tools = research_policy(WORKFLOW).tools
    expected = [
        (BTC, ()),
        (BTC, (ETH,)),
        (BTC, (SOL,)),
        (BTC, (ETH, SOL)),
        (ETH, ()),
        (ETH, (BTC,)),
        (ETH, (SOL,)),
        (ETH, (BTC, SOL)),
        (SOL, ()),
        (SOL, (BTC,)),
        (SOL, (ETH,)),
        (SOL, (BTC, ETH)),
    ]
    assert [(t.pair, t.benchmarks) for t in tools] == expected
    for entry in tools:
        assert entry.definitions == tuple(
            tool.identity for tool in tool_definitions(entry.pair, entry.benchmarks)
        )
        assert len(entry.definitions) == (7 if entry.benchmarks else 6)


def test_source_and_acquisition_binding_are_exact(tmp_path: Path) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    for key, value in [
        ("dataset_version", "sha256:" + "0" * 64),
        ("acquisition_id", "sha256:" + "0" * 64),
        ("pair", ETH),
    ]:
        data = result.manifest.model_dump()
        data["source_bindings"][0][key] = value
        with pytest.raises(ValidationError):
            ResearchReplayManifest.model_validate(data)


def test_baseline_remains_sync_inside_event_loop_and_avoids_provider_imports() -> None:
    async def caller() -> ReplayResult:
        return run_scanner_only(config(), inputs(), code=CODE)

    result = asyncio.run(caller())
    assert result.manifest.schema_version == "1"
    assert "research_metrics" not in result.model_dump()
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import crypteo.replay; "
                "assert not {'anthropic', 'httpx2', 'langgraph', "
                "'sqlite3', "
                "'crypteo.research_workflow', "
                "'crypteo.research_persistence'} & set(sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
