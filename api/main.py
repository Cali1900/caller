"""
Caller API.

Phase 0: health only. The webhook route arrives in phase 1 - it is the ONE
public route this service will ever expose, and it will do a single insert
and return 200.
"""

from fastapi import FastAPI

from api import db

app = FastAPI(title='caller', docs_url=None, redoc_url=None)


@app.get('/health')
def health():
    return {'ok': True, 'db': db.ping()}
