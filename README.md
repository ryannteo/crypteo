# Crypteo

Crypteo is an agentic crypto research copilot that gathers and evaluates evidence
to support informed trading decisions. Trading decisions remain with the user.

This repository currently contains only the Python project and quality-tooling
foundation. It does not contain market, trading, research-agent, simulation, or
other application logic.

## Local development

Crypteo requires Python 3.12 or newer. On macOS or Linux, create an isolated
environment and install the package with its development tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the quality checks from the repository root:

```bash
python -m ruff format .
python -m ruff check .
python -m mypy
python -m pytest
```

To check formatting without modifying files, as CI does, run:

```bash
python -m ruff format --check .
```

