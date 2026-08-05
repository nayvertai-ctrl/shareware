"""Production WSGI entry point (PythonAnywhere, gunicorn, etc.).

Point your host's WSGI config at this file's `application`. It creates any
missing tables on startup but NEVER wipes data — unlike `python3 app.py`, which
re-seeds demo data and is for local development only.

PythonAnywhere: in the Web tab's WSGI config file, replace the contents with:

    import sys
    path = "/home/YOURUSERNAME/shareware"   # the cloned repo dir
    if path not in sys.path:
        sys.path.insert(0, path)
    from wsgi import application            # noqa: F401
"""
from app import app as application, init_db

init_db()
