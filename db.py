"""Supabase access.

Talks to Supabase's REST API directly over HTTP. No SDK, no blocking calls
inside the event loop, one dependency less to break.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

import config

log = logging.getLogger("db")

_HEADERS = {
    "apikey": config.SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}

_JOBS_URL = f"{config.SUPABASE_URL}/rest/v1/jobs"


async def insert_job(update_id: int, chat_id: int, raw_thought: str):
    """Create a job row.

    telegram_update_id has a unique constraint. If Telegram redelivers the
    same update, the insert is ignored and this returns None, so a retry can
    never produce a second paid render.
    """
    headers = {**_HEADERS, "Prefer": "return=representation,resolution=ignore-duplicates"}
    payload = {
        "telegram_update_id": update_id,
        "chat_id": chat_id,
        "raw_thought": raw_thought,
        "status": "queued",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{_JOBS_URL}?on_conflict=telegram_update_id",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        rows = response.json()
    if not rows:
        return None
    return rows[0]


async def set_status(job_id: str, status: str, error: str = None):
    """Move a job to a new status and touch updated_at."""
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if error is not None:
        payload["error"] = error[:2000]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.patch(
            f"{_JOBS_URL}?id=eq.{job_id}",
            headers={**_HEADERS, "Prefer": "return=minimal"},
            json=payload,
        )
        response.raise_for_status()


async def get_stuck_jobs():
    """Jobs that need restarting after a crash."""
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    
    # Simpler query: just get queued jobs that aren't too old
    query = (
        f"{_JOBS_URL}?select=*"
        f"&status=eq.queued"
        f"&created_at=gte.{cutoff}"
        "&order=created_at.asc"
    )
    
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(query, headers=_HEADERS)
        response.raise_for_status()
        return response.json()

async def count_jobs_today(chat_id: int) -> int:
    """How many jobs this chat has created since midnight UTC."""
    midnight = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    query = (
        f"{_JOBS_URL}?select=id&chat_id=eq.{chat_id}&created_at=gte.{midnight}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(query, headers=_HEADERS)
        response.raise_for_status()
        return len(response.json())
