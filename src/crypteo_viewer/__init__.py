"""Local read-only viewer over pinned exports of stored research (ADR-022).

``crypteo_viewer.export`` reads stored evaluations and their research runs
through ``crypteo``'s public API and writes a bundle. ``crypteo_viewer.render``
reads only a bundle and writes static HTML. This module imports nothing, so the
render path never loads ``crypteo``.
"""
