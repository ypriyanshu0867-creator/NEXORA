"""WSGI entry point for a production server (not the Flask development server).

Windows example (after  python -m pip install waitress):
    waitress-serve --listen=127.0.0.1:8000 wsgi:app
Put an HTTPS reverse proxy in front, set APP_ENV=production and TRUSTED_PROXY_HOPS=1 in .env.
"""
from app import create_app

app = create_app()
