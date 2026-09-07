"""
Caller API.

Phase 1: health + the Retell webhook. The webhook is the ONE public route
this service exposes; it does a single insert and returns 200.
"""

from fastapi import FastAPI

from api import db, webhooks

app = FastAPI(title='caller', docs_url=None, redoc_url=None)

# The only public route. Everything else in this service is reachable through
# an SSH tunnel only (phase 4).
app.include_router(webhooks.router)


@app.get('/health')
def health():
    return {'ok': True, 'db': db.ping()}
