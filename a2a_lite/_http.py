"""Import unico del client HTTP.

Sulle vostre macchine basta `pip install httpx`. Il fallback su `httpx2`
serve solo nell'ambiente in cui è stato scritto e testato questo codice.
"""
try:  # pragma: no cover
    import httpx  # noqa: F401
except ImportError:  # pragma: no cover
    import httpx2 as httpx  # type: ignore  # noqa: F401

__all__ = ["httpx"]
