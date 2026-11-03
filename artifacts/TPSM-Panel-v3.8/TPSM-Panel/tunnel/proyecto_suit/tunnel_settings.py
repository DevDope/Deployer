import os
from pathlib import Path
from urllib.parse import urlsplit

from .settings import *  # noqa: F403


DEBUG = os.getenv("DEBUG", "False").lower() in {"1", "true", "yes"}
SITE_ORIGIN = (os.getenv("SITE_ORIGIN") or "https://tpsm.suitmx.com").rstrip("/")
PDF_RENDER_ORIGIN = (
    os.getenv("PDF_RENDER_ORIGIN")
    or "http://127.0.0.1:8105"
).rstrip("/")

MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT") or BASE_DIR / "media")  # noqa: F405
STATIC_ROOT = Path(os.getenv("STATIC_ROOT") or BASE_DIR / "staticfiles")  # noqa: F405

_site = urlsplit(SITE_ORIGIN)
if _site.scheme not in {"http", "https"} or not _site.hostname or _site.username or _site.password or _site.path or _site.query or _site.fragment:
    raise ValueError("SITE_ORIGIN debe ser un origen HTTP/HTTPS sin ruta ni credenciales")

ALLOWED_HOSTS = [*globals().get("ALLOWED_HOSTS", []), "tpsm.suitmx.com", _site.hostname]
CSRF_TRUSTED_ORIGINS = [
    *globals().get("CSRF_TRUSTED_ORIGINS", []),
    "https://tpsm.suitmx.com",
    SITE_ORIGIN,
]
