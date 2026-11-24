"""Development server entry point:  python run.py"""
import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    use_https = os.getenv("FLASK_HTTPS", "0") == "1"
    # The Werkzeug debugger allows code execution, so it is only enabled
    # when the server listens on this computer alone.
    debug = app.config["APP_ENV"] == "development" and host in ("127.0.0.1", "localhost")
    app.run(host=host, port=port, debug=debug, ssl_context="adhoc" if use_https else None)
