"""Model implementations + eval runner for registered training targets.

Named `model_lib` (not `models`) so it doesn't shadow the existing
SQLAlchemy ORM module `models.py`. The spec calls this directory `models/`
but the actual package layout is what's imported here — endpoints,
storage shape, and metrics contract are what matter (see docs/v0-florence.md).
"""
