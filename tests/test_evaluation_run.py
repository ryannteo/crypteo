"""Resumable evaluation runs over real replay and durable research, and reproduction."""

import asyncio
import json
import shutil
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from test_evaluation import OTHER_CODE, comparison_of
from test_evaluation_plan import forged, series, settings
from test_replay import BTC, CODE, ETH, SOL, D, H, T, payloads
from test_research_replay import WORKFLOW, ReplayModel

import crypteo.evaluation as evaluation
import crypteo.experiment as experiment
import crypteo.research_replay as research_replay
from crypteo.domain.evaluation import EvaluationAttempt, EvaluationResult, WindowPlan
from crypteo.domain.replay_research import (
    FreshResearchSettings,
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
)
from crypteo.domain.research_workflow import ResearchWorkflowConfig
from crypteo.evaluation import (
    compare_replays,
    load_evaluation,
    plan_windows,
    reproduce_evaluation,
    run_evaluation,
)
from crypteo.experiment import CodeIdentity, canonical_bytes
from crypteo.market_research import MarketResearchService
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import (
    FailureReply,
    ModelConfiguration,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    ResponseObserver,
)
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import (
    ResearchAdmissionError,
    ResearchBudgetExceeded,
    load_research_replay,
)

# One crossing per pair at T, each followed by a full three-hour window.
DATASETS = (
    series(BTC, ("90", "95", "96", "97")),
    series(ETH, ("90", "85", "84", "83")),
    series(SOL, ("90", "95", "120", "121")),
)
OUTCOMES = {BTC.value: "TRADE", ETH.value: "NO_TRADE", SOL.value: "ABSTAIN"}
PLAN = plan_windows(DATASETS, settings())
TRIAL = "evaluation-trial"


def window_ids(plan: WindowPlan = PLAN) -> list[str]:
    return [
        c.opportunity.opportunity_id
        for c in plan.candidates
        if c.decision == "SELECTED"
    ]


def request_pair(request: ModelRequest) -> str:
    data = json.loads(request.messages[-1].content)["research_data"]
    pair: str = data["context"]["request"]["opportunity"]["pair"]
    return pair


class WindowModel(ReplayModel):
    """Counts calls per pair; optionally fails or interrupts one pair, or checks."""

    def __init__(
        self,
        *,
        fail: str | None = None,
        interrupt: str | None = None,
        check: Callable[[str, ModelRequest], None] | None = None,
    ) -> None:
        super().__init__(OUTCOMES, hook=self.observe)
        self.calls: Counter[str] = Counter()
        self.fail, self.interrupt, self.check = fail, interrupt, check

    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        if (pair := request_pair(request)) == self.interrupt:
            # A typed provider failure: the run ends INTERRUPTED, not ABORTED.
            self.calls[pair] += 1
            return FailureReply(code=ProviderFailureCode.NETWORK, message="offline")
        return await super().invoke_observed(request, observe)

    def observe(self, request: ModelRequest) -> None:
        pair = request_pair(request)
        self.calls[pair] += 1
        if self.check is not None:
            self.check(pair, request)
        if pair == self.fail:
            raise RuntimeError("provider unavailable")


def evaluate(
    directory: Path,
    store: ResearchStore,
    model: WindowModel,
    *,
    plan: WindowPlan = PLAN,
    trial: str = TRIAL,
    cap: int = 10,
    configuration: ResearchWorkflowConfig = WORKFLOW,
    code: CodeIdentity = CODE,
) -> EvaluationResult:
    return asyncio.run(
        run_evaluation(
            plan,
            DATASETS,
            directory=directory,
            store=store,
            model=model,
            configuration=configuration,
            trial=trial,
            max_research_runs=cap,
            code=code,
        )
    )


def attempt(index: int, number: int) -> EvaluationAttempt:
    oid = window_ids()[index]
    return EvaluationAttempt(
        opportunity_id=oid, attempt=number, trial_id=f"{TRIAL}/{oid}/{number}"
    )


class Completed(NamedTuple):
    directory: Path
    store: ResearchStore
    result: EvaluationResult


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> Completed:
    root = tmp_path_factory.mktemp("evaluation-run")
    store = ResearchStore(root / "r.sqlite")
    directory = root / "run"
    return Completed(directory, store, evaluate(directory, store, WindowModel()))


def copied(completed: Completed, tmp_path: Path) -> Path:
    return Path(shutil.copytree(completed.directory, tmp_path / "run"))


def test_happy_path_files_and_rebuild(completed: Completed) -> None:
    directory, store, result = completed
    assert result.plan == PLAN and result.trial == TRIAL
    assert result.max_research_runs == 10
    assert result.attempts == (attempt(0, 1), attempt(1, 1), attempt(2, 1))
    windows = result.comparison.windows
    assert [(w.candidate.pair, w.candidate.outcome) for w in windows] == [
        (BTC, "TRADE"),
        (ETH, "NO_TRADE"),
        (SOL, "ABSTAIN"),
    ]
    assert [w.candidate.opportunity_id for w in windows] == window_ids()
    assert (directory / "plan.json").read_bytes() == PLAN.canonical_bytes()
    assert (directory / "run.json").read_bytes() == canonical_bytes(
        {
            "trial": TRIAL,
            "max_research_runs": 10,
            "configuration": WORKFLOW,
            "code": CODE,
        }
    )
    pairs = []
    for index, window in enumerate(windows, 1):
        folder = directory / "windows" / f"{index:03d}"
        assert sorted(p.name for p in folder.iterdir()) == [
            "attempt-1.json",
            "baseline.json",
            "research.json",
        ]
        baseline = (folder / "baseline.json").read_bytes()
        research = (folder / "research.json").read_bytes()
        assert baseline == window.baseline.canonical_bytes()
        assert research == window.research.canonical_bytes()
        assert (folder / "attempt-1.json").read_bytes() == canonical_bytes(
            result.attempts[index - 1]
        )
        # Each window replays only its own pair's dataset.
        assert window.research.manifest.datasets == (
            next(d for d in PLAN.datasets if d.pair == window.candidate.pair),
        )
        (record,) = payloads(window.research, ReplayResearchRecord)
        assert record.origin.trial_id == result.attempts[index - 1].trial_id
        assert record.origin.max_research_runs == 1
        pairs.append(
            (
                type(window.baseline).model_validate_json(baseline),
                load_research_replay(research, store=store),
            )
        )
    comparison = compare_replays(pairs, store=store)
    assert comparison == result.comparison
    encoded = (directory / "evaluation.json").read_bytes()
    assert encoded == result.canonical_bytes()
    assert load_evaluation(encoded, store=store) == result
    assert result.evaluation_id == experiment.identity(
        {
            "purpose": "evaluation_result_v1",
            "plan_id": PLAN.plan_id,
            "comparison_id": comparison.comparison_id,
            "trial": TRIAL,
            "max_research_runs": 10,
            "attempts": list(result.attempts),
        }
    )


def test_completed_directory_returns_identical_result(
    completed: Completed, tmp_path: Path
) -> None:
    model = WindowModel()
    again = evaluate(copied(completed, tmp_path), completed.store, model)
    assert again == completed.result
    assert again.canonical_bytes() == completed.result.canonical_bytes()
    assert not model.calls


def test_reservation_precedes_inference(tmp_path: Path) -> None:
    directory = tmp_path / "run"
    seen: set[str] = set()

    def check(pair: str, request: ModelRequest) -> None:
        if pair in seen:
            return
        seen.add(pair)
        index = [BTC.value, ETH.value, SOL.value].index(pair) + 1
        folder = directory / "windows" / f"{index:03d}"
        assert (folder / "attempt-1.json").read_bytes() == canonical_bytes(
            attempt(index - 1, 1)
        )
        assert not (folder / "research.json").exists()

    evaluate(directory, ResearchStore(tmp_path / "r.sqlite"), WindowModel(check=check))
    assert seen == set(OUTCOMES)


def test_resume_after_failure_researches_only_unfinished_windows(
    tmp_path: Path,
) -> None:
    directory, store = tmp_path / "run", ResearchStore(tmp_path / "r.sqlite")
    failing = WindowModel(fail=ETH.value)
    with pytest.raises(RuntimeError, match="provider unavailable") as failure:
        evaluate(directory, store, failing)
    assert "evaluation window 002; attempt 1" in "\n".join(failure.value.__notes__)
    assert failing.calls == Counter({BTC.value: 5, ETH.value: 1})
    second = directory / "windows" / "002"
    assert (second / "attempt-1.json").exists()
    assert not (second / "research.json").exists()
    assert not (directory / "windows" / "003").exists()
    assert not (directory / "evaluation.json").exists()
    resumed = WindowModel()
    result = evaluate(directory, store, resumed)
    # Window 1 is loaded, window 2 is researched again, window 3 for the first time.
    assert resumed.calls == Counter({ETH.value: 5, SOL.value: 5})
    assert result.attempts == (
        attempt(0, 1),
        attempt(1, 1),
        attempt(1, 2),
        attempt(2, 1),
    )
    record = payloads(result.comparison.windows[1].research, ReplayResearchRecord)[0]
    assert record.origin.trial_id == attempt(1, 2).trial_id
    assert [w.candidate.outcome for w in result.comparison.windows] == [
        "TRADE",
        "NO_TRADE",
        "ABSTAIN",
    ]


def test_budget_refuses_before_inference_across_resumes(tmp_path: Path) -> None:
    directory, store = tmp_path / "run", ResearchStore(tmp_path / "r.sqlite")
    with pytest.raises(RuntimeError, match="provider unavailable"):
        evaluate(directory, store, WindowModel(fail=ETH.value), cap=3)
    resumed = WindowModel()
    with pytest.raises(ResearchBudgetExceeded, match="max_research_runs=3"):
        evaluate(directory, store, resumed, cap=3)
    assert resumed.calls == Counter({ETH.value: 5})
    third = directory / "windows" / "003"
    assert [p.name for p in third.iterdir()] == ["baseline.json"]
    assert (directory / "windows" / "002" / "research.json").exists()
    assert not (directory / "evaluation.json").exists()


def test_cap_below_window_count_is_refused_before_any_file(tmp_path: Path) -> None:
    model = WindowModel()
    with pytest.raises(ValueError, match="less than the plan's 3 selected windows"):
        evaluate(tmp_path / "run", ResearchStore(tmp_path / "r.sqlite"), model, cap=2)
    assert not (tmp_path / "run").exists()
    assert not model.calls


def test_completed_attempt_is_restored_not_re_researched(
    completed: Completed, tmp_path: Path
) -> None:
    directory = copied(completed, tmp_path)
    second = directory / "windows" / "002"
    (second / "research.json").unlink()
    model = WindowModel()
    with pytest.raises(ValueError, match="attempt 1 completed") as refusal:
        evaluate(directory, completed.store, model)
    assert "window 002 research.json is missing" in str(refusal.value)
    assert "restore it rather than re-research" in str(refusal.value)
    assert not model.calls
    assert sorted(p.name for p in second.iterdir()) == [
        "attempt-1.json",
        "baseline.json",
    ]


def attempt_run_id(index: int, number: int) -> str:
    candidate = [c for c in PLAN.candidates if c.decision == "SELECTED"][index]
    return evaluation._attempt_research_run_id(
        candidate,
        DATASETS[index],
        FreshResearchSettings(
            configuration=WORKFLOW, trial_id=TRIAL, max_research_runs=10
        ),
        CODE,
        attempt(index, number).trial_id,
    )


@pytest.mark.parametrize("prior", ["INTERRUPTED", "ABORTED", "UNREGISTERED"])
def test_incomplete_prior_attempt_allows_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior: str
) -> None:
    directory, store = tmp_path / "run", ResearchStore(tmp_path / "r.sqlite")
    if prior == "UNREGISTERED":

        async def before_registration(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("failed before registration")

        monkeypatch.setattr(evaluation, "run_research_replay", before_registration)
        with pytest.raises(RuntimeError, match="before registration"):
            evaluate(directory, store, WindowModel())
        monkeypatch.undo()
        with pytest.raises(ResearchStorageError, match="run missing"):
            store.inspect_run(attempt_run_id(0, 1))
    else:
        model = WindowModel(
            interrupt=BTC.value if prior == "INTERRUPTED" else None,
            fail=BTC.value if prior == "ABORTED" else None,
        )
        with pytest.raises((ResearchAdmissionError, RuntimeError)):
            evaluate(directory, store, model)
        assert store.inspect_run(attempt_run_id(0, 1)).state == prior
    result = evaluate(directory, store, WindowModel())
    # Window 1 is retried; the first attempt's run is left as it was.
    assert result.attempts == (
        attempt(0, 1),
        attempt(0, 2),
        attempt(1, 1),
        attempt(2, 1),
    )
    assert store.inspect_run(attempt_run_id(0, 2)).state == "COMPLETED"


@pytest.mark.parametrize(
    "changes",
    [
        {"plan": plan_windows(DATASETS, settings(limit=2))},
        {"trial": "other-trial"},
        {"cap": 9},
        {
            "configuration": ResearchWorkflowConfig(
                model=ModelConfiguration(provider="fake", model="other-model")
            )
        },
        {"code": OTHER_CODE},
    ],
)
def test_resume_refuses_changed_plan_or_run(
    completed: Completed, tmp_path: Path, changes: dict[str, Any]
) -> None:
    directory = copied(completed, tmp_path)
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    model = WindowModel()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        evaluate(directory, completed.store, model, **changes)
    assert not model.calls
    assert {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "name,tamper,error",
    [
        (
            "baseline.json",
            lambda b: b.replace(b'"initial_cash":"1000"', b'"initial_cash":"1001"'),
            FileExistsError,
        ),
        (
            "research.json",
            lambda b: b.replace(b'"confidence":5', b'"confidence":6'),
            ResearchAdmissionError,
        ),
        ("research.json", lambda b: b + b" ", ValueError),
        (
            "attempt-1.json",
            lambda b: b.replace(b'/1"', b'/2"').replace(b'"attempt":1', b'"attempt":2'),
            ValueError,
        ),
    ],
)
def test_resume_refuses_tampered_window_files(
    completed: Completed,
    tmp_path: Path,
    name: str,
    tamper: Callable[[bytes], bytes],
    error: type[Exception],
) -> None:
    directory = copied(completed, tmp_path)
    path = directory / "windows" / "001" / name
    original = path.read_bytes()
    path.write_bytes(tamper(original))
    assert path.read_bytes() != original
    model = WindowModel()
    with pytest.raises(error):
        evaluate(directory, completed.store, model)
    assert not model.calls


def test_saved_research_must_belong_to_the_latest_attempt(
    completed: Completed, tmp_path: Path
) -> None:
    directory = copied(completed, tmp_path)
    (directory / "windows" / "001" / "attempt-2.json").write_bytes(
        canonical_bytes(attempt(0, 2))
    )
    with pytest.raises(ValueError, match="latest attempt"):
        evaluate(directory, completed.store, WindowModel())


def test_plan_without_selections_is_refused(tmp_path: Path) -> None:
    empty = plan_windows(DATASETS, settings(planning_start=T + 10 * H))
    assert empty.candidates == ()
    with pytest.raises(ValueError, match="selected candidates"):
        evaluate(
            tmp_path / "run",
            ResearchStore(tmp_path / "r.sqlite"),
            WindowModel(),
            plan=empty,
        )
    assert not (tmp_path / "run").exists()


def test_provider_mismatch_is_refused_before_any_file(tmp_path: Path) -> None:
    other = ResearchWorkflowConfig(
        model=ModelConfiguration(provider="anthropic", model="other-model")
    )
    with pytest.raises(ValueError, match="provider differs"):
        evaluate(
            tmp_path / "run",
            ResearchStore(tmp_path / "r.sqlite"),
            WindowModel(),
            configuration=other,
        )
    assert not (tmp_path / "run").exists()


def test_reproduction_without_model_or_research(
    completed: Completed, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("reproduction attempted inference or research tools")

    monkeypatch.setattr(research_replay, "run_durable_opportunity_research", forbidden)
    monkeypatch.setattr(MarketResearchService, "__init__", forbidden)
    monkeypatch.setattr(ReplayModel, "invoke_observed", forbidden)
    original = completed.result
    reproduced = asyncio.run(
        reproduce_evaluation(original, DATASETS, store=completed.store, code=CODE)
    )
    old, new = original.comparison, reproduced.comparison
    assert new.metrics == old.metrics
    assert [w.candidate for w in new.windows] == [w.candidate for w in old.windows]
    assert [w.baseline for w in new.windows] == [w.baseline for w in old.windows]
    for a, b in zip(new.windows, old.windows, strict=True):
        assert a.research.run_id != b.research.run_id
        assert isinstance(a.research.manifest, ResearchReplayManifest)
        assert isinstance(a.research.manifest.research, FrozenResearchSettings)
    assert (reproduced.plan, reproduced.trial) == (original.plan, original.trial)
    assert reproduced.max_research_runs == original.max_research_runs
    assert reproduced.attempts == original.attempts
    assert reproduced.evaluation_id != original.evaluation_id
    assert load_evaluation(reproduced.canonical_bytes(), store=completed.store) == (
        reproduced
    )


def test_reproduction_and_load_reject_tampering(
    completed: Completed, tmp_path: Path
) -> None:
    result, store = completed.result, completed.store
    encoded = result.canonical_bytes()
    # Pure validation passes; only the store proves the artifact differs.
    with pytest.raises(ResearchAdmissionError, match="differs from artifact"):
        load_evaluation(
            encoded.replace(b'"confidence":5', b'"confidence":6'), store=store
        )
    with pytest.raises(ResearchStorageError):
        load_evaluation(encoded, store=ResearchStore(tmp_path / "empty.sqlite"))
    changed = (series(BTC, ("90", "95", "96", "98")), *DATASETS[1:])
    with pytest.raises(ValueError, match="re-plan of the supplied datasets"):
        asyncio.run(reproduce_evaluation(result, changed, store=store, code=CODE))
    with pytest.raises(ValueError, match="baseline differs"):
        asyncio.run(
            reproduce_evaluation(result, DATASETS, store=store, code=OTHER_CODE)
        )


@pytest.mark.parametrize(
    "old,new",
    [
        (b'"terminal_sequence":', b'"terminal_sequence":1'),
        (b'"confidence":5', b'"confidence":6'),
    ],
)
def test_reproduction_rejects_tampered_saved_result(
    completed: Completed, old: bytes, new: bytes
) -> None:
    encoded = completed.result.canonical_bytes()
    assert old in encoded
    # Every occurrence, so the pure contract stays consistent.
    tampered = EvaluationResult.model_validate_json(encoded.replace(old, new))
    with pytest.raises(ResearchAdmissionError, match="differs from artifact"):
        asyncio.run(
            reproduce_evaluation(tampered, DATASETS, store=completed.store, code=CODE)
        )


def test_reproduction_requires_equal_candidates(
    completed: Completed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without link checks, the frozen replay still restores the stored confidence.
    monkeypatch.setattr(evaluation, "_with_links", lambda comparison, store: comparison)
    encoded = completed.result.canonical_bytes()
    tampered = EvaluationResult.model_validate_json(
        encoded.replace(b'"confidence":5', b'"confidence":6')
    )
    with pytest.raises(ValueError, match="frozen reproduction differs"):
        asyncio.run(
            reproduce_evaluation(tampered, DATASETS, store=completed.store, code=CODE)
        )


def rebuilt(data: dict[str, Any]) -> dict[str, Any]:
    """Data with a recomputed evaluation_id, so only the rule under test fails."""
    return data | {
        "evaluation_id": experiment.identity(
            {
                "purpose": "evaluation_result_v1",
                "plan_id": data["plan"]["plan_id"],
                "comparison_id": data["comparison"]["comparison_id"],
                "trial": data["trial"],
                "max_research_runs": data["max_research_runs"],
                "attempts": data["attempts"],
            }
        )
    }


def with_attempts(
    result: EvaluationResult, attempts: list[EvaluationAttempt]
) -> dict[str, Any]:
    return rebuilt(
        result.model_dump() | {"attempts": [a.model_dump() for a in attempts]}
    )


@pytest.mark.parametrize(
    "tamper,match",
    [
        (
            lambda r: rebuilt(
                r.model_dump()
                | {"comparison": comparison_of(r.comparison.windows[:2]).model_dump()}
            ),
            "selected candidates in order",
        ),
        (
            lambda r: rebuilt(
                r.model_dump()
                | {"plan": plan_windows(DATASETS, settings(limit=2)).model_dump()}
            ),
            "selected candidates in order",
        ),
        (
            lambda r: (
                r.model_dump()
                | {
                    "comparison": r.comparison.model_dump()
                    | {"windows": [w.model_dump() for w in r.comparison.windows[::-1]]}
                }
            ),
            "windows must be ordered",
        ),
        (
            lambda r: rebuilt(
                r.model_dump()
                | {
                    "plan": plan_windows(
                        DATASETS, settings(initial_cash=D(2000))
                    ).model_dump()
                }
            ),
            "replay configs must equal the planned windows",
        ),
        (
            lambda r: rebuilt(
                r.model_dump()
                | {
                    "plan": forged(
                        r.plan,
                        datasets=[
                            r.plan.datasets[0].model_dump()
                            | {"dataset_version": "sha256:" + "0" * 64},
                            *(d.model_dump() for d in r.plan.datasets[1:]),
                        ],
                    )
                }
            ),
            "plan's dataset for the pair",
        ),
        (
            lambda r: with_attempts(r, [attempt(0, 1), attempt(1, 2), attempt(2, 1)]),
            "without gaps",
        ),
        (
            lambda r: with_attempts(
                r, [attempt(0, 1), attempt(0, 1), attempt(1, 1), attempt(2, 1)]
            ),
            "unique per opportunity",
        ),
        (
            lambda r: with_attempts(r, [attempt(1, 1), attempt(0, 1), attempt(2, 1)]),
            "ordered by planned window",
        ),
        (
            lambda r: with_attempts(
                r,
                [
                    attempt(0, 1).model_copy(update={"trial_id": "other/1"}),
                    attempt(1, 1),
                    attempt(2, 1),
                ],
            ),
            "trial_id must be",
        ),
        (
            lambda r: with_attempts(
                r,
                [
                    *r.attempts,
                    EvaluationAttempt(
                        opportunity_id="unknown",
                        attempt=1,
                        trial_id=f"{TRIAL}/unknown/1",
                    ),
                ],
            ),
            "belong to the plan's selected candidates",
        ),
        (
            lambda r: rebuilt(r.model_dump() | {"max_research_runs": 2}),
            "exceed max_research_runs",
        ),
        (
            lambda r: with_attempts(
                r, [attempt(0, 1), attempt(0, 2), attempt(1, 1), attempt(2, 1)]
            ),
            "latest attempt",
        ),
        (
            lambda r: r.model_dump() | {"evaluation_id": "sha256:" + "0" * 64},
            "evaluation_id must match",
        ),
        (
            lambda r: (
                r.model_dump()
                | {
                    "evaluation_id": rebuilt(r.model_dump() | {"trial": "other"})[
                        "evaluation_id"
                    ]
                }
            ),
            "evaluation_id must match",
        ),
    ],
)
def test_contract_rejections(completed: Completed, tamper: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        EvaluationResult.model_validate(tamper(completed.result))


def test_canonical_round_trip_and_revalidation(completed: Completed) -> None:
    result = completed.result
    encoded = result.canonical_bytes()
    loaded = EvaluationResult.model_validate_json(encoded)
    assert loaded == result and loaded.canonical_bytes() == encoded
    assert EvaluationResult.model_validate(result) == result
    with pytest.raises(ValueError, match="evaluation_id must match"):
        EvaluationResult.model_validate(
            result.model_copy(update={"evaluation_id": "sha256:" + "0" * 64})
        )
    with pytest.raises(ValueError, match="trial_id must be"):
        EvaluationResult.model_validate(result.model_copy(update={"trial": "other"}))
    with pytest.raises(ValueError, match="exceed max_research_runs"):
        EvaluationResult.model_validate(
            result.model_copy(update={"max_research_runs": 1})
        )
    with pytest.raises(ValueError, match="greater than 0"):
        EvaluationResult.model_validate(
            result.model_copy(update={"max_research_runs": 0})
        )
    with pytest.raises(ValueError, match="valid integer"):
        EvaluationAttempt.model_validate(attempt(0, 1).model_dump() | {"attempt": True})
