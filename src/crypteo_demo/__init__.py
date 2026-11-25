"""A one-command demo on synthetic data with a scripted model.

``python -m crypteo_demo [OUTPUT]`` runs ``crypteo``'s real pipeline end to end:
synthetic candles ingested through the canonical-dataset path, a ``PLUMBING``
window plan, a durable evaluation with a scripted model under provider
``"fake"``, a frozen reproduction with no model, and the run viewer's export and
render steps. It needs no data download, no API key and no network.

Nothing it produces is research or an evaluation result: the candles are
synthetic and every decision is fixed text from a script. The package sits
beside ``crypteo`` and ``crypteo_viewer``; ``crypteo`` never imports it.
"""

LABEL = (
    "DEMO: synthetic data and a scripted model. Its decisions are fixed text, "
    "not research, and nothing it produces is an evaluation result."
)
