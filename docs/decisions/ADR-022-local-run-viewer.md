# ADR-022: Stored research is shown by a local read-only viewer over pinned exports

## Status

Accepted

## Context

Stored research runs and evaluations can be read only through the Python API
(`load_evaluation`, `ResearchStore.load_terminal`, `ResearchStore.inspect_run`)
or by trial scripts that print summaries. There is no way to browse a run's
candidate, tool calls, evidence, stage outputs, assessment and cost, or a
trial's windows, without writing code. AGENTS.md allows journal data to be used
for auditing, evaluation, debugging and post-trade analysis.

Two properties of the system constrain any viewer:

- Loading a stored run rebuilds every expected model request from the current
  prompts, schemas and tool definitions and compares them exactly
  ([specification](../v1-specification.md)). A change to any of them makes
  earlier runs fail to load. A viewer that reads stores directly would stop
  working at the next research-policy change.
- The code identity hashes `src/crypteo/**/*.py` and `pyproject.toml`. Code
  placed there changes the source digest and so every later run's identity,
  although a viewer changes no research or replay behaviour.

Stored runs and evaluations contain market data and works derived from it,
obtained from Coinbase under its Market Data Terms of Use, which restrict
redistribution and display to third parties.

## Decision

**Two steps.** An export step reads stored evaluations and their research runs
and writes a bundle. A render step reads only a bundle and writes static HTML.
The render step never reads a research store, an evaluation file or a dataset.

**Export.**

- It reads through the public API only, at a commit where the runs load, and
  fails as a whole if any requested run or evaluation does not load.
- Opening a `ResearchStore` can write to it, so the export never opens an
  original. It copies every input file to a private temporary directory, loads
  only the copies, and deletes them afterwards. It refuses an input that does
  not exist, and fails if any original's SHA-256 differs after the export from
  before it.
- The bundle is canonical JSON with a manifest: bundle format
  `run_viewer_bundle_v1`, the exporting commit and source digest, and the
  SHA-256 of every original input file.
- For each run it records the stored values, not recomputations: the candidate
  and its trade terms, every model call's stage, re-asks and rejections, every
  tool request with its result and evidence, the investigation, thesis,
  critique and assessment (decision, confidence, lean), the journal events, and
  provider-reported token usage. For each evaluation it records the plan, the
  windows, each window's baseline and research outcome, and the metrics.
  Supplied trial files, such as an analysis, are copied verbatim with their
  SHA-256.
- A list-price cost estimate is computed in Python from the token usage and a
  price table that the bundle records, and is labelled an estimate.
- Exports of a pre-registered trial are made only after its analysis is
  written, so the viewer never shows outcomes beside leans while a trial runs.

**Render.** Standard library only. It verifies the manifest's hashes first,
then writes one page per evaluation and per run, and an index. Pages are plain
HTML and CSS: no script, and no external fonts, styles or requests. The same
bundle renders to byte-identical pages.

**Boundaries.**

- The viewer is the package `crypteo_viewer` under `src/`, outside
  `src/crypteo`. `crypteo` never imports it, and a test enforces this. Replay,
  evaluation and research never read a bundle or a page.
- Neither step constructs a model client, reads an API key or makes a network
  request, and both refuse to run while `ANTHROPIC_API_KEY` is set.
- The viewer is local. Bundles and pages are written under `artifacts/`, which
  is ignored, and are never committed, published or hosted. Pages link to each
  other by relative paths and open directly from disk; no step runs a server.

## Rationale

Exporting once, while the runs load, makes the view durable: a bundle keeps
working after the prompts change, and its manifest ties every page to a commit,
a source digest and exact input files, so a page can be checked against a trial
record. Recording stored values instead of recomputing them means the viewer
shows what the system did, and Python keeps the one calculation it adds.

A package beside `crypteo` keeps the source digest and run identities
unchanged, needs no packaging or CI change, and makes the one-way dependency
testable. Working on copies keeps the stores untouched without changing how
`crypteo` opens them. Static pages that open from disk need no server, no
container and no runtime that could reach a store or a key. Keeping bundles and
pages local follows the data provider's terms.

Alternatives considered:

- *A viewer that reads the stores directly.* Simplest at first, but it fails
  at the next prompt or schema change, and it would need the full research
  stack at view time.
- *A web application with a server.* It allows search and filtering, at the
  cost of a running service, more dependencies and an attack surface, for one
  local user.
- *A module inside `src/crypteo`.* It would change the source digest and put
  presentation code inside the research system's identity.
- *A container.* For one local user it adds a build and a runtime for pages a
  browser opens directly.
- *Public hosting of exported pages.* The bundles hold works derived from
  Coinbase market data, whose terms restrict display to third parties.

## Consequences

- The implementing slice adds `crypteo_viewer` (export, render, a command-line
  entry), the bundle format, tests on synthetic runs and evaluations (a
  round trip from store to pages, determinism, originals unchanged, the import
  boundary, no network, refusal with a key set, and a hash mismatch rejected
  before rendering), and a README section.
- The wheel ships `crypteo_viewer` beside `crypteo`.
- Each trial's runs should be exported before any change that stops them
  loading. A run that no longer loads can still be exported from a checkout of
  a commit where it does, if that commit has the viewer.
- Trial bundles stay on the owner's machine; the viewer shows nothing that is
  not already in the owner's stores.
