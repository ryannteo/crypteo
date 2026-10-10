"""Minimal scanner-only experiment identity and canonical replay serialization."""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal, Self

import pydantic
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypteo.domain.execution import ExecutionConfig
from crypteo.domain.market import NonNegativeDecimal, TradingPair
from crypteo.domain.risk import RiskConfig
from crypteo.domain.time import UtcDatetime
from crypteo.domain.trade_terms import TradeTermsConfig

if TYPE_CHECKING:
    from crypteo.domain.replay_research import ResearchReplayManifest, ResearchSettings

ASSET_PRIORITY = (TradingPair.BTC_USD, TradingPair.ETH_USD, TradingPair.SOL_USD)
SCANNER_POLICY = (
    "bollinger_lower_cross_v1: completed Coinbase 1h snapshot; 20 contiguous closes; "
    "population standard deviation; lower = SMA20 - 2*std; previous close >= its "
    "lower band and current close < its lower band; first valid band initializes; "
    "gaps reset; dependency availability retained. Replay admits observed_at == T "
    "and available_at <= T once per opportunity_id; allocation priority "
    "BTC-USD, ETH-USD, SOL-USD then opportunity_id; valid terms always selected."
)
TERMS_POLICY: Final = "mean_true_range20_stop2_sma20_target_floor_v1"
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical decimals must be finite")
        fixed = format(value, "f")
        return (
            "0"
            if value == 0
            else fixed.rstrip("0").rstrip(".")
            if "." in fixed
            else fixed
        )
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def canonical_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    """Sorted UTF-8 JSON, exact normalized Decimal strings and UTC timestamps."""
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def identity(value: BaseModel | dict[str, Any]) -> str:
    return "sha256:" + sha256(canonical_bytes(value)).hexdigest()


class CodeIdentity(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")]
    source_digest: Digest


def current_code_identity() -> CodeIdentity:
    """Hash all installed crypteo Python sources by relative name and content.

    Include pyproject.toml when present in the source checkout. Documentation,
    tests, bytecode, paths and mtimes are excluded. Outside a source checkout,
    callers must supply explicit build identity to the replay API.
    """
    package = Path(__file__).resolve().parent
    root = package.parent.parent
    sources = {
        "src/crypteo/" + path.relative_to(package).as_posix(): sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(package.rglob("*.py"))
    }
    if (root / "pyproject.toml").is_file():
        sources["pyproject.toml"] = sha256(
            (root / "pyproject.toml").read_bytes()
        ).hexdigest()
    if not (root / ".git").exists():
        raise ValueError("source checkout unavailable; supply explicit CodeIdentity")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return CodeIdentity(git_commit=commit, source_digest=identity(sources))


class ReplayConfig(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    start: UtcDatetime
    end: UtcDatetime
    initial_cash: NonNegativeDecimal
    assets: tuple[TradingPair, ...]
    trade_terms: TradeTermsConfig
    risk: RiskConfig
    execution: ExecutionConfig

    @field_validator("assets")
    @classmethod
    def canonical_assets(
        cls, value: tuple[TradingPair, ...]
    ) -> tuple[TradingPair, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("assets must be nonempty and unique")
        return tuple(pair for pair in ASSET_PRIORITY if pair in value)

    @model_validator(mode="after")
    def validate_replay(self) -> Self:
        for boundary in (self.start, self.end):
            if boundary.minute or boundary.second or boundary.microsecond:
                raise ValueError("replay boundaries must align to an hour")
        if self.end < self.start:
            raise ValueError("end must not precede start")
        if self.trade_terms.price_increment != self.execution.price_increment:
            raise ValueError("trade terms and execution price increments must match")
        return self


class DatasetIdentity(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    pair: TradingPair
    dataset_version: Digest
    acquisition_id: Digest | None


class ExperimentManifest(BaseModel):
    """Only implemented configuration; run_id hashes this entire manifest."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    schema_version: Literal["1"] = "1"
    experiment_mode: Literal["SCANNER_ONLY"] = "SCANNER_ONLY"
    code: CodeIdentity
    python_version: str = Field(min_length=1)
    pydantic_version: str = Field(min_length=1)
    replay: ReplayConfig
    datasets: tuple[DatasetIdentity, ...]
    scanner_policy: str = SCANNER_POLICY
    trade_terms_policy: Literal["mean_true_range20_stop2_sma20_target_floor_v1"] = (
        TERMS_POLICY
    )

    @model_validator(mode="after")
    def validate_datasets(self) -> Self:
        if self.scanner_policy != SCANNER_POLICY:
            raise ValueError("scanner policy must match the implemented policy")
        if tuple(item.pair for item in self.datasets) != self.replay.assets:
            raise ValueError("dataset identities must match canonical ordered assets")
        return self

    @property
    def run_id(self) -> str:
        return identity(self)

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)


def build_manifest(
    config: ReplayConfig, datasets: tuple[DatasetIdentity, ...], code: CodeIdentity
) -> ExperimentManifest:
    return ExperimentManifest(
        code=code,
        python_version=platform.python_version(),
        pydantic_version=pydantic.__version__,
        replay=config,
        datasets=datasets,
    )


def build_research_manifest(
    config: ReplayConfig,
    datasets: tuple[DatasetIdentity, ...],
    code: CodeIdentity,
    research: ResearchSettings,
) -> ResearchReplayManifest:
    # Local imports avoid coupling baseline identity to research implementation.
    from crypteo.domain.replay_research import (
        ResearchReplayManifest,
        dataset_source_bindings,
        research_policy,
    )

    return ResearchReplayManifest(
        code=code,
        python_version=platform.python_version(),
        pydantic_version=pydantic.__version__,
        replay=config,
        datasets=datasets,
        source_bindings=dataset_source_bindings(datasets),
        research=research,
        policy=research_policy(research.configuration),
    )
