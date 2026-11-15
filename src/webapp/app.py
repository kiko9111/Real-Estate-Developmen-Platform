"""ASGI entry point: uvicorn webapp.app:app"""
from .main import create_app

app = create_app()
