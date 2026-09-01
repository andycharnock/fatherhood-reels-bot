```python
"""The pipeline. One thought in, one Reel out.

Six steps, run in order, in a background task:
  1. Enhance   - Claude turns the raw thought into a script, caption, hashtags
  2. Voice     - ElevenLabs turns the script into an MP3
  3. Ingest    - the MP3 is uploaded to Shotstack so it has a hosted URL
  4. Render    - Shotstack assembles the 1080x1920 Reel with auto-captions
  5. Poll      - wait for the render, checking every 15 seconds
  6. Deliver   - send the MP4, caption and hashtags back to Telegram

If any step raises, the job is marked failed, the error is stored, and
Telegram is told exactly which step broke.
"""

import asyncio
import io
import json
import logging
import math

import httpx
from mutagen.mp3 import MP3

import backgrounds
import config
import db

log = logging.getLogger("pipeline")

SYSTEM_PROMPT = """You are a spoken-word poetry editor for a fatherhood Reels channel. \
The author is a British dad in Abu Dhabi, ex-publican, father of two daughters.

Rules:
- Preserve the meaning, facts, viewpoint, humour and emotional truth.
- Do not invent events, feelings, lessons or details the author did not provide.
- Edit for spoken rhythm and clarity rather than rewriting into generic \
inspirational content.
- Target 30-60 seconds when read aloud.
- Return strict JSON only: {enhanced_text, caption, hashtags}"""


class StepError(Exception):
    """Raised when a pipeline step fails. Carries the step name."""

    def __init__(self, step: str, message: str):
        self.step = step
        super().__init__(message)


# --------------------------------------------------------------------------
# Step 1: Enhance
# --------------------------------------------------------------------------
async def enhance(raw_thought: str) -> dict:
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": raw_thought}],
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise StepError("enhancement", f"Claude returned {exc.response.status_code}: "
                                       f"{exc.response.text[:300]}") from exc
    except Exception as exc:
        raise StepError("enhancement", str(exc)) from exc

    text = "".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    ).strip()

    # Strip markdown fences if the model wrapped the JSON in them.
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StepError("enhancement", f"Claude did not return valid JSON: {text[:300]}") from exc

    script = (parsed.get("enhanced_text") or "").strip()
    if not script:
        raise StepError("enhancement", "Claude returned no enhanced_text")

    hashtags = parsed.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = hashtags.split()
    hashtags = [
        tag if str(tag).startswith("#") else f"#{tag}" for tag in hashtags
    ]

    return {
        "enhanced_text": script,
        "caption": (parsed.get("caption") or "").strip(),
        "hashtags": " ".join(hashtags),
    }


# --------------------------------------------------------------------------
# Step 2: Voice
# --------------------------------------------------------------------------
async def synthesise(script: str) -> bytes:
    url = (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        f"{config.ELEVENLABS_VOICE_ID}"
    )
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": script,
        "model_id": config.ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            audio = response.content
    except httpx.HTTPStatusError as exc:
        raise StepError("voice generation", f"ElevenLabs returned "
                                            f"{exc.response.status_code}: "
                                            f"{exc.response.text[:300]}") from exc
    except Exception as exc:
        raise StepError("voice generation", str(exc)) from exc

    if not audio or len(audio) < 1000:
        raise StepError("voice generation", "ElevenLabs returned an empty audio file")
    return audio


def audio_duration(mp3_bytes: bytes) -> float:
    """Read the real length of the MP3 so the video can be sized to it."""
    try:
        return float(MP3(io.BytesIO(mp3_bytes)).info.length)
    except Exception as exc:
        raise StepError("voice generation", f"Could not read MP3 duration: {exc}") from exc


# --------------------------------------------------------------------------
# Step 3: Ingest the MP3 into Shotstack
# --------------------------------------------------------------------------
async def ingest_audio(mp3_bytes: bytes) -> str:
    headers = {"x-api-key": config.SHOTSTACK_API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            # Ask for a signed upload URL.
            response = await client.post(
                f"{config.SHOTSTACK_INGEST_URL}/upload", headers=headers
            )
            response.raise_for_status()
            attributes = response.json()["data"]["attributes"]
            source_id = attributes["id"]
            signed_url = attributes["url"]

            # Push the raw bytes to that URL. No extra headers: the signature
            # was generated without them.
            upload = await client.put(signed_url, content=mp3_bytes)
            upload.raise_for_status()

            # Wait for Shotstack to finish processing the upload.
            for _ in range(40):  # up to ~2 minutes
                await asyncio.sleep(3)
                status = await client.get(
                    f"{config.SHOTSTACK_INGEST_URL}/sources/{source_id}",
                    headers=headers,
                )
                status.raise_for_status()
                source = status.json()["data"]["attributes"]
                if source.get("status") == "ready":
                    return source["source"]
                if source.get("status") == "failed":
                    raise StepError("audio upload", "Shotstack rejected the audio file")
    except StepError:
        raise
    except httpx.HTTPStatusError as exc:
        raise StepError("audio upload", f"Shotstack ingest returned "
                                        f"{exc.response.status_code}: "
                                        f"{exc.response.text[:300]}") from exc
    except Exception as exc:
        raise StepError("audio upload", str(exc)) from exc

    raise StepError("audio upload", "Shotstack did not finish processing the audio in time")


# --------------------------------------------------------------------------
# Step 4: Render
# --------------------------------------------------------------------------
def build_timeline(audio_url: str, background_url: str, duration: float) -> dict:
    """1080x1920, captions on top, voiceover in the middle, footage underneath.

    The background is tiled in fixed-length pieces so it covers the whole
    voiceover no matter how long the audio runs. The final piece is trimmed
    so the video ends when the voice does, rather than paying to render
    silence.
    """
    total = duration + 0.5
    tile = config.BACKGROUND_TILE_SECONDS
    tile_count = max(1, math.ceil(total / tile))

    background_clips = []
    for index in range(tile_count):
        start = index * tile
        length = min(tile, total - start)
        if length <= 0:
            break
        background_clips.append(
            {
                "asset": {"type": "video", "src": background_url, "volume": 0},
                "start": round(start, 2),
                "length": round(length, 2),
                "fit": "cover",
            }
        )

    caption_asset = {
        "type": "caption",
        "src": "alias://narration",
        "font": {"size": config.CAPTION_SIZE, "color": "#ffffff"},
        "background": {"color": "#000000", "opacity": 0.35, "padding": 12, "borderRadius": 8},
        "margin": {"left": 0.1, "right": 0.1},
        "alignment": {"horizontal": "center", "vertical": "center"},
    }
    if config.CAPTION_FONT:
        caption_asset["font"]["family"] = config.CAPTION_FONT

    return {
        "timeline": {
            "background": "#000000",
            "tracks": [
                # Track order is top to bottom. Captions must be first.
                {"clips": [{"asset": caption_asset, "start": 0, "length": "end"}]},
                {
                    "clips": [
                        {
                            "alias": "narration",
                            "asset": {"type": "audio", "src": audio_url},
                            "start": 0,
                            "length": "auto",
                        }
                    ]
                },
                {"clips": background_clips},
            ],
        },
        "output": {
            "format": "mp4",
            "size": {"width": 1080, "height": 1920},
        },
    }


async def submit_render(timeline: dict) -> str:
    headers = {"x-api-key": config.SHOTSTACK_API_KEY, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{config.SHOTSTACK_EDIT_URL}/render", headers=headers, json=timeline
            )
            response.raise_for_status()
            return response.json()["response"]["id"]
    except httpx.HTTPStatusError as exc:
        raise StepError("video assembly", f"Shotstack returned "
                                          f"{exc.response.status_code}: "
                                          f"{exc.response.text[:400]}") from exc
    except Exception as exc:
        raise StepError("video assembly", str(exc)) from exc


# --------------------------------------------------------------------------
# Step 5: Poll
# --------------------------------------------------------------------------
async def wait_for_render(render_id: str) -> str:
    headers = {"x-api-key": config.SHOTSTACK_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            for _ in range(20):  # 20 x 15s = 5 minutes
                await asyncio.sleep(15)
                response = await client.get(
                    f"{config.SHOTSTACK_EDIT_URL}/render/{render_id}", headers=headers
                )
                response.raise_for_status()
                body = response.json()["response"]
                status = body.get("status")
                log.info("Render %s status: %s", render_id, status)
                if status == "done":
                    return body["url"]
                if status == "failed":
                    raise StepError("rendering", body.get("error") or "Shotstack render failed")
    except StepError:
        raise
    except Exception as exc:
        raise StepError("rendering", str(exc)) from exc

    raise StepError("rendering", "Render did not finish within 5 minutes")


# --------------------------------------------------------------------------
# Step 6: Deliver
# --------------------------------------------------------------------------
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024


async def deliver(bot, chat_id: int, video_url: str, enhanced: dict):
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.get(video_url)
            response.raise_for_status()
            video_bytes = response.content
    except Exception as exc:
        raise StepError("delivery", f"Could not download the finished video: {exc}") from exc

    try:
        if len(video_bytes) < TELEGRAM_FILE_LIMIT:
            await bot.send_video(
                chat_id=chat_id,
                video=io.BytesIO(video_bytes),
                filename="reel.mp4",
                supports_streaming=True,
                read_timeout=300,
                write_timeout=300,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=f"Video is too large for Telegram. Download it here:\n{video_url}",
            )

        if enhanced["caption"]:
            await bot.send_message(chat_id=chat_id, text=enhanced["caption"])
        if enhanced["hashtags"]:
            await bot.send_message(chat_id=chat_id, text=enhanced["hashtags"])
        await bot.send_message(chat_id=chat_id, text=config.POSTING_TIME_LINE)
    except Exception as exc:
        raise StepError("delivery", str(exc)) from exc


# --------------------------------------------------------------------------
# The whole thing
# --------------------------------------------------------------------------
async def run_pipeline(job: dict, bot):
    job_id = job["id"]
    chat_id = job["chat_id"]
    raw_thought = job["raw_thought"]
    log.info("Job %s starting", job_id)

    try:
        await db.set_status(job_id, "processing")

        enhanced = await enhance(raw_thought)
        log.info("Job %s enhanced (%d chars)", job_id, len(enhanced["enhanced_text"]))

        mp3_bytes = await synthesise(enhanced["enhanced_text"])
        duration = audio_duration(mp3_bytes)
        log.info("Job %s voiced (%.1f seconds)", job_id, duration)

        audio_url = await ingest_audio(mp3_bytes)
        background_url = backgrounds.pick()

        timeline = build_timeline(audio_url, background_url, duration)
        render_id = await submit_render(timeline)
        log.info("Job %s submitted render %s", job_id, render_id)

        await db.set_status(job_id, "rendering")
        video_url = await wait_for_render(render_id)

        await deliver(bot, chat_id, video_url, enhanced)
        await db.set_status(job_id, "complete")
        log.info("Job %s complete", job_id)

    except StepError as exc:
        log.exception("Job %s failed at %s", job_id, exc.step)
        await db.set_status(job_id, "failed", f"{exc.step}: {exc}")
        try:
            await bot.send_message(
                chat_id=chat_id, text=f"Failed at {exc.step}: {exc}"
            )
        except Exception:
            log.exception("Could not report failure to Telegram")

    except Exception as exc:  # noqa: BLE001 - last resort
        log.exception("Job %s failed unexpectedly", job_id)
        await db.set_status(job_id, "failed", str(exc))
        try:
            await bot.send_message(chat_id=chat_id, text=f"Failed unexpectedly: {exc}")
        except Exception:
            log.exception("Could not report failure to Telegram")
```
