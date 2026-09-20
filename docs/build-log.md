# Engineering build log

This log records development objectives and consequential decisions; implementation
detail remains in git. Durable architectural constraints live in
[ADRs](decisions/README.md), and planned scope lives in the
[V1 specification](v1-specification.md).

## Reconstruction basis

History through completed Slice 7 is reconstructed from commits through `758d2de`,
the specification, production modules, and tests. Entries follow commit order;
slice labels follow the specification. Rationale summarizes the documented
invariants and the purpose evident in code and tests, not recovered conversations
or claims about alternatives debated at the time.

For history through Slice 7, test-run totals and review transcripts are not recorded
in the inspected repository history. Verification for those entries identifies
committed coverage, not proof
of a historical passing run. Repairs are evidenced by diffs; their attribution to
particular reviewers is unavailable. AI configuration is recorded separately below.
The Slice 8 and 9 entries and the pre-Slice-10 prerequisite entry use the completed
implementations and tests, with review results and AI workflow supplied explicitly
by the user.

## Development phases

### Slices 1–3: repository and domain foundations

- **Objective:** establish a small, typed research platform with enforceable domain
  boundaries before orchestration or simulation.
- **Decisions and rationale:** use a Python modular monolith, Pydantic contracts,
  and Ruff/strict mypy/pytest CI. Frozen market records use exact Decimal financial
  values and strict UTC timestamps. Research contracts separate deterministic
  opportunities and normalized evidence from qualitative thesis, critique, and
  assessment; this supports future research domains without giving the model
  financial or runtime authority ([ADR-001](decisions/ADR-001-deterministic-vs-llm-boundary.md)).
- **Repairs/clarifications:** reject coercive Python timestamps and row counts;
  lock the manifest schema version. Clarify the live research mission, shared
  research workflow, and bounded long-term orchestration without implementing it.
- **Verification evidence:** package import/version checks; market validation,
  serialization, immutable evidence, grounding, and outcome-specific assessment
  tests in [domain tests](../tests/domain).
- **Commits:** `77fa2f1`, `8a34252`, `1f5b4fe`, `195b0ee` (architecture/product);
  `8874524` (tooling); `1b9ae62`, `a629e8c` (market);
  `2e0d092` (research); `ef80810` (architecture clarification);
  `61cd5ab` (local artifact/secret ignores).

### Slice 4: authoritative simulation time and visibility

- **Objective:** establish historical visibility before introducing real ingestion.
- **Decisions and rationale:** `SimulationClock` advances explicitly and never
  backward; `PointInTimeDataGateway` filters an in-memory candle collection by
  completion and availability. Reads expose no caller cutoff. This makes temporal
  integrity a code boundary rather than a prompt convention
  ([ADR-002](decisions/ADR-002-point-in-time-temporal-boundary.md)).
- **Verification evidence:** [temporal tests](../tests/test_temporal.py) plant
  future and delayed candles, check exact boundaries, reject duplicate identities,
  and check isolation from source-list mutation.
- **Commit:** `6bab7c5`.

### Slice 5: Coinbase ingestion and canonical dataset foundation

- **Objective:** acquire auditable hourly inputs and compose deterministic local
  datasets for later analytics and replay.
- **Decisions and rationale:** bounded requests of at most 300 hourly slots use an
  injectable byte transport; preserve raw responses before normalization. Validate,
  deduplicate, sort, and report gaps without forward-filling. Introduce semantic
  identity for canonical candle content; for non-empty datasets, identity was
  intended to be independent of request chunking and creation provenance. At this
  stage, datasets with no captures derived coverage from `created_at`, while
  captured-but-empty datasets derived coverage from the captured request interval.
  Persist immutable local artifacts to support reproducible inputs without new
  services. The later `9c1df9c` stabilization gave all empty canonical datasets
  `None` coverage bounds and kept requested completeness at the acquisition-record
  level.
- **Verification evidence:** fixture tests cover request chunking, exact numeric
  decoding, duplicate conflicts, gap accounting, version stability, artifact
  round-trips, and raw preservation after normalization failure in the
  `tests/test_coinbase*.py` suites.
- **Commits:** `aefcb15` (retrieval), `6e1a2eb` (normalization),
  `ccbb237` (gap contract), `313dd20` (composition), `a739b78` (identity),
  `4d40da5` (persistence).

### Pre-Slice-6 stabilization: provider extras and acquisition boundary

- **Objective:** preserve exact observations while limiting canonical data to
  explicitly acquired, completed intervals.
- **Decisions/repairs:** replace rejection of provider extras with per-request
  `[start, end)` ownership; excluded rows still require usable structure/time but
  cannot fill another request's missing slots. Require a strict UTC
  `acquisition_started_at`, validate all request ends before HTTP, and independently
  check retained closes and creation time during composition. Yield and persist
  each capture before requesting the next chunk; hash the request, boundary, and
  body digest so revised observations coexist.
- **Rationale:** provider extras must not distort completeness; later failures must
  not erase earlier observations or publish a partial canonical acquisition.
- **Verification evidence:** retrieval, composition, and persistence tests cover
  invalid boundaries, extra-row ownership, capture identity, and second-request
  failure with the first raw artifact already on disk.
- **Commit:** `f3eda18`.

### Pre-Slice-6 stabilization: acquisition completeness and provenance

- **Objective:** distinguish what was requested from what canonical data exists.
- **Decisions/repairs:** introduce an immutable acquisition record with explicit
  requested bounds, exact capture IDs, requested missing slots, and dataset version.
  Empty canonical datasets have `None` coverage and no canonical gaps; acquisition
  records retain leading, internal, trailing, and all-empty missing slots. Publish
  acquisition records after canonical persistence, without mutable backlinks.
- **Rationale:** identical canonical candles can result from different requests;
  canonical identity must not erase those acquisition histories
  ([ADR-003](decisions/ADR-003-canonical-data-and-acquisition-provenance.md)).
- **Verification evidence:** acquisition schema, dataset, and persistence tests
  cover exact linkage, zero-length/all-empty requests, distinct acquisitions sharing
  data, and missing-reference publication failures.
- **Commit:** `9c1df9c`.

### Pre-Slice-6 stabilization: canonical persistence reproducibility

- **Objective:** make persistence obey semantic dataset identity on re-ingestion.
- **Decisions/repairs:** share canonical candle encoding between hashing and writes;
  revalidate incoming and stored models, coverage, and recomputed identity. Reuse
  semantically equivalent artifacts without rewriting them, return the first stored
  manifest, and preserve the current acquisition's own creation time.
- **Rationale:** equivalent Decimal spellings, JSON formatting, and later capture
  times must not conflict with an existing semantic dataset. Corruption must fail
  closed rather than be mistaken for harmless formatting variation.
- **Verification evidence:** persistence tests exercise numeric/context equivalence,
  first-manifest reuse, forged identities, corruption, and incomplete artifacts;
  no automatic repair or migration is performed.
- **Commit:** `fee0cae`.

### Pre-Slice-6 stabilization: final publication integrity

- **Objective:** make acquisition publication validate the complete association.
- **Decisions/repairs:** strengthen existence/reference checks with canonical
  validation, exact stored raw body/envelope comparison, and raw recomposition.
  Recomputed candles and acquisition provenance must match the supplied dataset
  and record before publication.
- **Rationale:** individually valid artifacts and matching IDs are insufficient
  evidence that the referenced captures actually produced that dataset.
- **Verification evidence:** [persistence tests](../tests/test_coinbase_persistence.py)
  reject tampered raw bytes/metadata, false gaps/bounds/times, unrelated captures,
  and corrupt canonical data; successful publication leaves existing artifacts
  unchanged. Multi-file writes remain non-transactional and concurrent writers
  remain uncoordinated.
- **Commit:** `e5f50c4`.

### Foundational test-quality audit and cleanup

- **Objective:** strengthen contract evidence before analytics without changing
  production behavior.
- **Repairs and rationale:** isolate financial field constraints so unrelated OHLC
  validation cannot mask failures; correct gap-fixture row accounting; explicitly
  require opportunity IDs and reject recommendation fields. Replace normalizer
  call-shape coupling with output semantics, add fixed canonical bytes/hash, assert
  acquisition bounds/creation time, and check the gateway API without depending on
  parameter order. These make tests target the intended invariant.
- **Verification evidence:** the commit changes only four test files: market,
  research, dataset, and temporal contracts.
- **Commit:** `ccf689f`.

### Slice 6: deterministic Bollinger analytics

- **Objective:** implement only the calculations required by the locked scanner.
- **Decisions and rationale:** pure rolling statistics use 20 contiguous closes,
  population standard deviation, and `SMA - 2 * standard deviation`, including the
  current candle. A private 50-digit half-even Decimal context isolates numerical
  results from callers. Gaps reset warm-up to `None`; invalid ordering and mixed
  markets fail. Results carry window observation/availability provenance, while
  callers obtain visible candles through the gateway.
- **Verification evidence:** [analytics tests](../tests/test_analytics.py) include
  independent exact and irrational golden examples, rounding, warm-up/gap recovery,
  caller/generator context isolation, and delayed/future gateway integration.
  Separate review findings are not recorded.
- **Commit:** `00b40d7`.

### Slice 7: deterministic OpportunityScanner

- **Objective:** produce the same locked research candidates for future agent and
  baseline evaluation.
- **Decisions and rationale:** scan only gateway-visible history and return every
  qualifying opportunity in that snapshot, with no retained delivery state. Compare
  adjacent closes against their own bands; the first valid band initializes only,
  `None`/gaps reset comparison, and equality or above-band observations re-arm.
  Deterministic IDs and compact trigger facts make repeated snapshots reproducible;
  runtime delivery/deduplication remains deferred
  ([ADR-004](decisions/ADR-004-scanner-snapshot-semantics.md)).
- **Verification evidence:** [scanner tests](../tests/test_scanner.py) cover the
  earliest crossover at candle 21, moving bands, suppression/re-arming, gaps,
  repeated/fresh scanner equivalence, exact IDs, per-pair isolation, and availability
  dependencies in both comparison windows. Separate review findings are not recorded.
- **Commit:** `758d2de`.

### Slice 8: deterministic simulated execution and portfolio accounting

- **Objective:** execute already-authorized LONG terms and maintain deterministic
  simulated portfolio accounting, without an `Opportunity` or `TradeDecision`
  dependency. Risk authorization and sizing remain later work.
- **Decisions and rationale:** reconcile next-open execution only through completed,
  gateway-visible candles; distinguish market phase/time from evidence availability
  and recording time, without inventing intrabar timestamps or a second clock
  ([ADR-005](decisions/ADR-005-completed-bar-execution-reconciliation.md)).
  Observed opening gaps take priority over later intrabar touches; otherwise a
  simultaneous stop/target touch resolves stop-first. Stop/target takes priority
  over the final-bar time exit; the entry candle counts as holding bar one.
- **Accounting:** explicit fees and adverse slippage; reject misaligned quantities
  and stop/target levels, round BUY prices up and SELL prices down to the configured
  increment. Exact Decimal arithmetic reconciles cash, fees, cost basis, and realized
  PnL independently of the caller's Decimal context. Immutable snapshots preserve
  append-only orders, fills, and outcomes; failed processing leaves prior state intact.
- **Missing data and scope:** reject a missing entry candle without rolling entry
  forward or reviving the order on delayed data. Missing holding data leaves an
  unresolved halt, preserving financial state instead of inferring unseen paths.
  Availability after the expected close counts as missing even when processing
  happens later. One pending or open trade at a time is a Slice 8 scope limitation.
- **Review findings and repairs:** strengthen public execution, lifecycle, and
  accounting contract validation; validate internal transitions instead of unchecked
  copies; reject zero-price SELL rounding atomically.
- **Verification:** 854 tests passed, plus Ruff format/check, strict mypy, and
  diff/whitespace checks. Coverage includes
  [execution contracts](../tests/domain/test_execution.py),
  [broker scenarios](../tests/test_simulated_broker.py),
  [portfolio accounting](../tests/test_portfolio.py), and
  [temporal access](../tests/test_temporal.py).
- **Implementation commit:** `22087f12c533d90f14ac48640fc7477b0dd274b3`
  (`Add deterministic simulated execution kernel`).

### Slice 9: deterministic risk authorization and position sizing

- **Objective:** authorize and size LONG proposals deterministically through a
  `TradeProposal` boundary independent of `TradeDecision`.
- **Decisions and rationale:** use current flat cash as capital and the exact
  visible completed candle close ending at `decision_at` as the sizing reference;
  the unknown next-open price cannot inform authorization. Include fees, adverse
  slippage, tick rounding, and stop execution costs through shared Slice 8 pricing.
  Select the largest quantity increment multiple satisfying both risk budget and
  available cash, using exact arithmetic
  ([ADR-006](decisions/ADR-006-decision-time-risk-authorization.md)).
- **Scope:** authorization is a nonmutating estimated nongapped risk bound, not a
  guaranteed maximum loss; it reserves no cash and consumes no IDs. Next-open/gap
  affordability and realized-loss behavior remain Slice 8 responsibilities, without
  resizing. Stop/target levels are validated but not generated; deterministic
  generation remains unresolved before full replay integration.
- **Review findings and repairs:** authorization initially accepted fabricated
  execution estimates; alignment/maximality invariants were incomplete; future
  holding history could be treated as an ordinary rejection. All three were
  repaired and verified: recompute shared execution estimates,
  enforce aligned maximal sizing in public contracts, and reject future holding
  history as an input error before ordinary risk outcomes.
- **Verification:** 1,008 tests passed, plus Ruff format/check, strict mypy, and
  git diff checks. [Risk contract tests](../tests/domain/test_risk_contracts.py)
  and [risk tests](../tests/test_risk.py) cover boundary sizing, an independent
  rational oracle, forged estimates, temporal isolation, nonmutation, and broker
  reconciliation including gaps and affordability rejection.
- **Implementation commit:** `715f36457e52c80e45cfb68e8443076b2af25395`
  (`Add deterministic risk authorization and sizing`).

### Pre-Slice-10 prerequisite: deterministic trade terms and target economics

- **Objective:** complete deterministic market-derived LONG stop/target generation
  before scanner-only replay, and require positive estimated net target reward at
  the risk boundary.
- **Decisions and rationale:** generate only from 21 contiguous visible hourly
  candles ending at authoritative gateway time. Use the arithmetic mean of 20 true
  ranges, a stop two mean true ranges below the reference close, and the shared
  SMA20 target. Floor both levels exactly to the configured price increment and
  reject unavailable history or invalid/collapsed levels without adjustment.
  Immutable terms remain independent of opportunity delivery, research judgment,
  portfolio sizing, and execution
  ([ADR-007](decisions/ADR-007-deterministic-trade-terms.md)).
- **Authorization and contracts:** share execution-cost arithmetic; `RiskService`
  rejects zero or negative estimated net target reward even for direct proposals.
  Public contracts recompute levels and target diagnostics, including on instance
  and JSON revalidation. Decision-time estimates remain separate from actual
  next-open affordability and gap outcomes.
- **Remaining scope:** Slice 10 owns opportunity freshness, deduplication,
  stale-term admission, replay orchestration, proposal construction, metrics, and
  experiment manifests. Generation does not automatically expose terms to the LLM.
- **Independent review:** no findings, as reported by the user; no review repairs
  were required.
- **Notable regression/probe coverage:**
  [analytics tests](../tests/test_analytics.py) exercise all true-range branches,
  the unweighted 20-range mean, and exact arithmetic beyond 50 digits;
  [terms tests](../tests/test_trade_terms.py) probe every missing/delayed window
  position, future next-open isolation, no older-history fallback, arbitrary tick
  sizes against an independent rational oracle, collapsed levels, and hostile
  Decimal contexts. [Terms contracts](../tests/domain/test_trade_terms_contracts.py)
  reject forged levels and unchecked nested configuration.
  [Risk tests](../tests/test_risk.py) cover direct-proposal bypass attempts,
  zero/negative and tiny positive reward, rejection precedence, forged diagnostics,
  and nongapped target-exit reconciliation with the unchanged broker.
- **Final verification:** 1,226 tests passed, plus Ruff format/check, strict mypy,
  and `git diff --check`.
- **Implementation commit:** `97e4ee032f95ec3adc6e43f432a961b77b36d02f`
  (`Add deterministic trade terms and target validation`).
- **AI workflow/configuration:** planning, implementation, and independent review
  used Astra with High reasoning, as supplied by the user. These are engineering
  workflow settings, not Crypteo runtime model configuration.

### Slice 10: deterministic scanner-only historical replay

- **Objective:** connect canonical datasets, snapshot scanner, frozen terms,
  risk authorization, and simulated broker into deterministic hourly replay,
  establishing a reproducible scanner-only baseline for later research comparison.
- **Implementation commit:** `70b8c8e` (`Add deterministic scanner-only replay`).
- **Implemented behavior:** one authoritative clock/gateway; broker reconciliation
  before decisions at hourly boundaries in `[start, end)`; current-boundary
  admission and terminal run-local delivery disposition. Simultaneous candidates
  follow BTC-USD → ETH-USD → SOL-USD, then opportunity ID. Every fresh candidate
  with valid terms is baseline-selected; risk authorization and immediate
  submission are sequential under the single-active-trade limitation
  ([ADR-008](decisions/ADR-008-scanner-only-replay-boundary-delivery-ordering.md)).
  At `end`, reconcile and observe without new decisions, cancellation, or forced
  liquidation. `start == end` is a valid empty replay with one observation.
- **Artifacts and metrics:** immutable in-memory records preserve distinct terms,
  risk, and entry rejections, fills, closed trades, and holding gaps. Canonical
  manifests, runtime-owned run/order IDs, and serialization support byte-stable
  reproduction. Marked-equity/core metrics include `missing_entry_candle_count`.
  Open positions remain open; holding gaps preserve unresolved state, realized
  metrics, and valid prefixes while invalidating affected full-period marked
  metrics and exposure. Read-only canonical loading accepts optional explicit
  acquisition linkage. Durable journaling/recovery, LLM research, the
  `TradeDecision` workflow, walk-forward enforcement, live mode, and real-money
  execution were not added.
- **Initial independent review:** 2 HIGH, 1 MEDIUM, and 1 LOW findings, as recorded
  in the supplied review history. Conflicting opportunity identities could be
  silently deduplicated; serialized `ReplayResult` lifecycle/linkage validation
  was insufficient; serialized metrics could contradict authoritative replay
  state; canonical leaf symlinks could escape the artifact root.
- **Repair cycle:** added canonical ADR-004 `Opportunity.identity_key` collision
  rejection; focused lifecycle/linkage/ledger-prefix validation in
  `replay_validation.py`; metric recomputation with exact equality against the
  canonical calculator during `ReplayResult` validation; and resolved canonical
  leaf-path containment checks before reading. Regression tests exercise identity
  collisions, deleted/reordered/mislinked lifecycle records, forged metrics,
  observation history, and escaping versus contained symlinks.
- **Final independent verification:** all four findings **RESOLVED**; no new
  findings, no Slice 10 scope leakage, and implementation/tests safe to commit,
  as reported in the supplied final review record.
- **Final implementation verification:** 1,438 tests passed; Ruff format/check
  passed; strict mypy passed (49 source files); `git diff --check` passed. These
  are the supplied final implementation results, distinct from this documentation
  reconciliation's checks.
- **AI workflow/configuration:** model variants and reasoning settings for Slice
  10 implementation, review, and repair are not established by the available
  record; none are inferred.

## Backfill verification

On 2026-09-12, the documentation backfill was checked against production and tests
unchanged from `758d2de`, using local Python 3.12.2: Ruff formatting check and lint
passed; strict mypy passed (26 source files); full pytest passed (615 tests);
`git diff --check` passed. These are current verification results, not reconstructed
historical run totals. No production behavior, test, or experiment configuration
changed.

## AI development workflow

The following configurations were explicitly supplied by the user for this
documentation, including the Slice 8 and 9 completion records. They describe
engineering-process traceability, not Crypteo's runtime model configuration.
No model API identifiers or unrecorded reasoning levels are inferred.

| Slice | Activity | Model | Reasoning | Why |
| --- | --- | --- | --- | --- |
| 6 | Planning | Astra | High | Consequential architecture, numerical, and state-machine decisions with downstream effects. |
| 6 | Implementation | Astra | High | High one-pass correctness for consequential deterministic infrastructure. |
| 6 | Independent review | Astra | Medium | Bounded independent verification after behavior was locked. |
| 7 | Planning | Astra | High | Consequential architecture, numerical, and state-machine decisions with downstream effects. |
| 7 | Implementation | Astra | High | High one-pass correctness for consequential deterministic infrastructure. |
| 7 | Independent review | Astra | Medium | Bounded independent verification after behavior was locked. |
| 8 | Planning | Astra | High | Consequential temporal, execution, accounting, and simulation-policy decisions. |
| 8 | Implementation | Astra | High | High one-pass correctness for consequential deterministic execution infrastructure. |
| 8 | Independent review | Astra | High | Adversarial review of temporal safety, accounting, lifecycle contracts, and rounding semantics. |
| 8 | Repair | Astra | Medium | Bounded repair of three concrete review findings. |
| 8 | Repair verification | Astra | Medium | Focused verification of the repaired findings. |
| 9 | Planning | Astra | High | Consequential decision-time risk, cost, and sizing boundaries. |
| 9 | Implementation | Astra | High | Exact sizing and enforceable authorization contracts. |
| 9 | Independent review | Astra | High | Adversarial review of estimates, sizing invariants, and temporal safety. |
| 9 | Repair | Astra | Medium | Bounded repair of three concrete review findings. |
| 9 | Repair verification | Astra | Medium | Focused verification of the repaired findings. |

Earlier slices, stabilization phases, and the foundational test audit: Not recorded.
