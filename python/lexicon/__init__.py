"""lexicon — RAG vocabulary tutor runtime.

One package shared by two entry points:

* the offline CLI / ingestion pipeline (``python -m lexicon``), and
* the FastAPI service deployed to AWS Lambda (``lexicon.api``).

All AWS access is confined to this package; the React frontend never holds credentials.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
