"""
Caller API.

Phase 1: health + the Retell webhook. The webhook is the ONE public route
this service exposes; it does a single insert and returns 200.
"""

from fastapi import FastAPI, Request

from api import db, upload, web, webhooks

app = FastAPI(title='caller', docs_url=None, redoc_url=None)

# The only public route. Everything else in this service is reachable through
# an SSH tunnel only (phase 4).
app.include_router(webhooks.router)

# The CRM. Reachable ONLY through an SSH tunnel - caller-api binds 127.0.0.1
# and the nginx vhost proxies exactly one path. The binding is the auth.
app.include_router(web.router)


@app.get('/health')
def health():
    return {'ok': True, 'db': db.ping()}


@app.post('/upload')
async def upload_csv(request: Request):
    """
    Raw CSV body -> the POOL. UPLOADING NEVER DIALS: rows land with
    pool_status='pool', and the dialer requires 'active' plus membership in a
    STARTED campaign.

    Reachable only through the SSH tunnel; the webhook is the one public route.
    """
    body = (await request.body()).decode('utf-8', errors='replace')
    return upload.upload(body)

