"""Shared Jinja2 templates instance.

Lives outside ``api.main`` so routers (e.g. ``auth.routes``) can render pages
without importing the app module and creating an import cycle. The current
user is injected into every template via the ``template_user`` context
processor.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from gridiron.auth.security import template_user

_TEMPLATES_DIR = Path(__file__).resolve().parent / "web" / "templates"

templates = Jinja2Templates(
    directory=str(_TEMPLATES_DIR),
    context_processors=[template_user],
)
