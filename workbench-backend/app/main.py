"""ASGI entry point; tests import app.factory without loading local configuration."""

from app.factory import create_app

app = create_app()
