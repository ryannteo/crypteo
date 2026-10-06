"""Independent review regressions for canonical context, revalidation, and origin."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_replay import BTC, CODE, D, H, T, config, payloads
from test_research_replay import (
    WORKFLOW,
    ReplayModel,
    economics,
    execute,
    fresh,
    frozen,
    inputs,
)

from crypteo.domain.replay import Delivery, ReplayResult
from crypteo.domain.replay_research import (
    FreshReplayOrigin,
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
    fresh_replay_origin,
    opportunity_context_digest,
)
from crypteo.domain.research import Opportunity
from crypteo.domain.research_journal import ResearchArtifact
from crypteo.domain.research_workflow import OpportunityResearchRequest
from crypteo.execution import SimulatedBroker
from crypteo.experiment import CodeIdentity, build_research_manifest, canonical_bytes
from crypteo.market_research import MarketResearchService
from crypteo.replay import run_scanner_only
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import (
    ResearchAdmissionError,
    load_research_replay,
    run_research_replay,
    validate_research_links,
)
from crypteo.research_workflow import run_durable_opportunity_research
from crypteo.risk import RiskService
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway, SimulationClock
from crypteo.trade_terms import TradeTermsService


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("rejected research must not reach financial preparation")


def prohibit_finance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TradeTermsService, "generate", forbidden)
    monkeypatch.setattr(RiskService, "authorize", forbidden)
    monkeypatch.setattr(SimulatedBroker, "submit", forbidden)


@pytest.mark.parametrize(
    "retained,current",
    [
        (1, True),
        (0, False),
        (1, 1.0),
        (0.0, -0.0),
        ([1, {"a": 0}], [True, {"a": False}]),
    ],
)
def test_python_equal_canonical_context_mismatch_rejects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retained: Any,
    current: Any,
) -> None:
    original_scan = OpportunityScanner.scan
    value = retained

    def scan(self: OpportunityScanner, *, pair: Any) -> tuple[Opportunity, ...]:
        return tuple(
            Opportunity.model_validate(
                item.model_dump() | {"trigger_payload": {"flag": value}}
            )
            for item in original_scan(self, pair=pair)
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    store = ResearchStore(tmp_path / "r.sqlite")
    captured = execute(store)
    # Identical canonical context admits, even with nested payloads.
    assert economics(execute(store, settings=frozen(captured))) == economics(captured)
    retained_op = payloads(captured, Delivery)[0].opportunity
    candidate = Opportunity.model_validate(
        retained_op.model_dump() | {"trigger_payload": {"flag": current}}
    )
    assert retained_op == candidate
    assert opportunity_context_digest(retained_op) != opportunity_context_digest(
        candidate
    )
    settings = frozen(captured)
    # A mapping that matches the candidate cannot hide the artifact mismatch.
    settings = FrozenResearchSettings(
        configuration=settings.configuration,
        mapping=(
            settings.mapping[0].model_copy(
                update={"context_digest": opportunity_context_digest(candidate)}
            ),
        ),
    )
    value = current
    prohibit_finance(monkeypatch)
    with pytest.raises(ResearchAdmissionError, match="canonical opportunity context"):
        execute(store, settings=settings)


def publish(
    store: ResearchStore,
    captured: ReplayResult,
    *,
    opportunity: Opportunity,
    code: CodeIdentity = CODE,
) -> ResearchArtifact:
    """Publish a valid Slice 13 history with deliberately incompatible provenance."""
    assert isinstance(captured.manifest, ResearchReplayManifest)
    reference = payloads(captured, ReplayResearchRecord)[0]
    return asyncio.run(
        run_durable_opportunity_research(
            OpportunityResearchRequest(
                research_run_id=reference.research_run_id, opportunity=opportunity
            ),
            MarketResearchService(
                PointInTimeDataGateway(SimulationClock(T), inputs()[0].candles),
                captured.manifest.source_bindings,
            ),
            ReplayModel({BTC.value: "NO_TRADE"}),
            WORKFLOW,
            store=store,
            code=code,
        )
    )


@pytest.mark.parametrize("retained,current", [(1, True), (0, False), (1, 1.0)])
@pytest.mark.parametrize("mode", ["fresh", "frozen"])
def test_imported_links_require_artifact_canonical_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retained: Any,
    current: Any,
    mode: str,
) -> None:
    original_scan = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: Any) -> tuple[Opportunity, ...]:
        return tuple(
            Opportunity.model_validate(
                item.model_dump() | {"trigger_payload": {"flag": current}}
            )
            for item in original_scan(self, pair=pair)
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    original_store = ResearchStore(tmp_path / "original.sqlite")
    captured = execute(
        original_store,
        model=ReplayModel({BTC.value: "NO_TRADE"}),
    )
    if mode == "frozen":
        captured = execute(original_store, settings=frozen(captured))
    candidate = payloads(captured, Delivery)[0].opportunity
    other = Opportunity.model_validate(
        candidate.model_dump() | {"trigger_payload": {"flag": retained}}
    )
    assert candidate == other
    store = ResearchStore(tmp_path / "other.sqlite")
    artifact = publish(store, captured, opportunity=other)
    # Match actual journal checksums/linkage so only the context check can reject.
    data = captured.model_dump()
    projection = next(r["payload"] for r in data["records"] if r["kind"] == "RESEARCH")
    projection.update(
        artifact_checksum=artifact.artifact_checksum,
        ordered_journal_digest=artifact.ordered_journal_digest,
        terminal_sequence=artifact.terminal_sequence,
    )
    if mode == "frozen":
        data["manifest"]["research"]["mapping"][0]["artifact_checksum"] = (
            artifact.artifact_checksum
        )
        data["run_id"] = ResearchReplayManifest.model_validate(data["manifest"]).run_id
    imported = ReplayResult.model_validate(data)
    with pytest.raises(ResearchAdmissionError, match="canonical opportunity context"):
        validate_research_links(imported, store=store)
    with pytest.raises(ResearchAdmissionError, match="canonical opportunity context"):
        load_research_replay(imported.model_dump_json(), store=store)


def assert_all_validation_routes_reject(forged: ReplayResult) -> None:
    # Constructor and instance validation must be as strict as dict/JSON imports.
    fields = {name: getattr(forged, name) for name in ReplayResult.model_fields}
    with pytest.raises(ValidationError):
        ReplayResult(**fields)
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(forged)
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(forged.model_dump(warnings=False))
    with pytest.raises(ValidationError):
        # Canonical replay encoding preserves bool/int distinctions in forgeries;
        # Pydantic's typed JSON serializer can turn an invalid bool into an int.
        ReplayResult.model_validate_json(
            canonical_bytes(forged.model_dump(warnings=False))
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "INTERRUPTED"},
        {"assessment_origin": "RUNTIME"},
        {"artifact_schema_version": "unsupported"},
        {"outcome": "TRADE", "confidence": None},
        {"status": "INTERRUPTED", "outcome": "ABSTAIN", "assessment_origin": "RUNTIME"},
        {"outcome": "ABSTAIN"},  # Incompatible with this replay's broker activity.
        {"artifact_checksum": "not-a-checksum"},
        {"terminal_sequence": True},
        {"ordered_journal_digest": "invalid"},
    ],
)
def test_all_routes_revalidate_nested_research(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    records = tuple(
        record.model_copy(update={"payload": record.payload.model_copy(update=changes)})
        if isinstance(record.payload, ReplayResearchRecord)
        else record
        for record in result.records
    )
    assert_all_validation_routes_reject(result.model_copy(update={"records": records}))


def test_revalidation_reaches_origin_and_strict_metric_contracts(
    tmp_path: Path,
) -> None:
    result = execute(ResearchStore(tmp_path / "r.sqlite"))
    records = tuple(
        record.model_copy(
            update={
                "payload": record.payload.model_copy(
                    update={
                        "origin": record.payload.origin.model_copy(
                            update={"max_research_runs": True}
                        )
                    }
                )
            }
        )
        if isinstance(record.payload, ReplayResearchRecord)
        else record
        for record in result.records
    )
    assert_all_validation_routes_reject(result.model_copy(update={"records": records}))
    # True == 1 must not bypass the strict count contract via equality to metrics.
    assert result.metrics.entry_count == 1
    assert_all_validation_routes_reject(
        result.model_copy(
            update={"metrics": result.metrics.model_copy(update={"entry_count": True})}
        )
    )
    assert result.research_metrics is not None
    assert_all_validation_routes_reject(
        result.model_copy(
            update={
                "research_metrics": result.research_metrics.model_copy(
                    update={"trade_count": True}
                )
            }
        )
    )


@pytest.mark.parametrize("mode", ["baseline", "fresh", "frozen"])
def test_valid_results_round_trip_and_detach_collections(
    tmp_path: Path, mode: str
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    if mode == "baseline":
        result = run_scanner_only(config(), inputs(), code=CODE)
    else:
        result = execute(store)
        if mode == "frozen":
            result = execute(store, settings=frozen(result))
    fields = {name: getattr(result, name) for name in ReplayResult.model_fields}
    copies = [
        ReplayResult(**fields),
        ReplayResult.model_validate(result),
        ReplayResult.model_validate(result.model_dump()),
        ReplayResult.model_validate_json(result.model_dump_json()),
    ]
    for copy in copies:
        assert copy is not result
        assert copy.canonical_bytes() == result.canonical_bytes()
        assert copy.records[0] is not result.records[0]
    mutable = result.model_dump()
    copy = ReplayResult.model_validate(mutable)
    mutable["records"][0]["payload"]["opportunities"][0]["trigger_payload"]["new"] = [
        True
    ]
    assert copy.canonical_bytes() == result.canonical_bytes()
    opportunity = payloads(copy, Delivery)[0].opportunity
    with pytest.raises(TypeError):
        opportunity.trigger_payload["new"] = True  # type: ignore[index]
    with pytest.raises(ValidationError):
        copy.run_id = "changed"


@pytest.mark.parametrize(
    "component",
    [
        "code_commit",
        "code_digest",
        "initial_cash",
        "risk_fraction",
        "holding_limit",
        "interval",
        "execution_fee",
        "terms_tick",
    ],
)
def test_frozen_configuration_reuse_is_not_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    captured = execute(store)
    cfg, code = config(), CODE
    if component == "code_commit":
        code = CODE.model_copy(update={"git_commit": "c" * 40})
    elif component == "code_digest":
        code = CODE.model_copy(update={"source_digest": "sha256:" + "c" * 64})
    elif component == "initial_cash":
        cfg = config(initial_cash=D(2000))
    elif component == "risk_fraction":
        cfg = config(risk=cfg.risk.model_copy(update={"risk_fraction": D("0.03")}))
    elif component == "holding_limit":
        cfg = config(risk=cfg.risk.model_copy(update={"max_holding_bars": 4}))
    elif component == "interval":
        cfg = config(end=T + 4 * H)
    elif component == "execution_fee":
        cfg = config(
            execution=cfg.execution.model_copy(update={"entry_fee_rate": D("0.01")})
        )
    else:
        cfg = config(
            trade_terms=cfg.trade_terms.model_copy(
                update={"price_increment": D("0.01")}
            ),
            execution=cfg.execution.model_copy(update={"price_increment": D("0.01")}),
        )
    # Reject configuration compatibility before loading any candidate or artifact.
    monkeypatch.setattr(ResearchStore, "load_terminal", forbidden)
    monkeypatch.setattr(OpportunityScanner, "scan", forbidden)
    expected = (
        "CodeIdentity mismatch"
        if component.startswith("code")
        else "configuration/manifest mismatch"
    )
    with pytest.raises(ValueError, match="originating replay " + expected):
        asyncio.run(
            run_research_replay(
                cfg, inputs(), research=frozen(captured), store=store, code=code
            )
        )


def test_claimed_origin_cannot_rebase_an_artifact_to_new_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    captured = execute(store)
    assert isinstance(captured.manifest, ResearchReplayManifest)
    cfg = config(initial_cash=D(2000))
    claimed = build_research_manifest(cfg, captured.manifest.datasets, CODE, fresh())
    settings = frozen(captured)
    settings = settings.model_copy(
        update={
            "mapping": (
                settings.mapping[0].model_copy(
                    update={"origin": fresh_replay_origin(claimed)}
                ),
            )
        }
    )
    prohibit_finance(monkeypatch)
    with pytest.raises(ResearchAdmissionError, match="originating replay invocation"):
        execute(store, cfg=cfg, settings=settings)


def test_artifact_producer_code_must_match_claimed_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = execute(
        ResearchStore(tmp_path / "original.sqlite"),
        model=ReplayModel({BTC.value: "NO_TRADE"}),
    )
    store = ResearchStore(tmp_path / "other.sqlite")
    artifact = publish(
        store,
        captured,
        opportunity=payloads(captured, Delivery)[0].opportunity,
        code=CODE.model_copy(update={"source_digest": "sha256:" + "c" * 64}),
    )
    settings = frozen(captured)
    settings = settings.model_copy(
        update={
            "mapping": (
                settings.mapping[0].model_copy(
                    update={"artifact_checksum": artifact.artifact_checksum}
                ),
            )
        }
    )
    prohibit_finance(monkeypatch)
    with pytest.raises(ResearchAdmissionError, match="artifact code differs"):
        execute(store, settings=settings)
    data = captured.model_dump()
    projection = next(r["payload"] for r in data["records"] if r["kind"] == "RESEARCH")
    projection.update(
        artifact_checksum=artifact.artifact_checksum,
        ordered_journal_digest=artifact.ordered_journal_digest,
        terminal_sequence=artifact.terminal_sequence,
    )
    with pytest.raises(ResearchAdmissionError, match="artifact code differs"):
        load_research_replay(
            ReplayResult.model_validate(data).model_dump_json(), store=store
        )


@pytest.mark.parametrize(
    "field", ["trial_id", "max_research_runs", "replay_run_id", "code"]
)
def test_origin_tampering_rejects_all_import_routes(tmp_path: Path, field: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    result = execute(store)
    record = payloads(result, ReplayResearchRecord)[0]
    changes: dict[str, Any] = {
        "trial_id": "other-trial",
        "max_research_runs": 7,
        "replay_run_id": "sha256:" + "c" * 64,
        "code": CODE.model_copy(update={"git_commit": "c" * 40}),
    }
    origin = FreshReplayOrigin.model_validate(
        record.origin.model_dump() | {field: changes[field]}
    )
    records = tuple(
        r.model_copy(update={"payload": record.model_copy(update={"origin": origin})})
        if isinstance(r.payload, ReplayResearchRecord)
        else r
        for r in result.records
    )
    assert_all_validation_routes_reject(result.model_copy(update={"records": records}))
    settings = frozen(result)
    settings = settings.model_copy(
        update={"mapping": (settings.mapping[0].model_copy(update={"origin": origin}),)}
    )
    with pytest.raises(ValueError, match="originating replay"):
        execute(store, settings=settings)
