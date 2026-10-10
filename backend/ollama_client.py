"""Small client for talking to a local Ollama server.

Responsibilities:
  - shrink and encode images before sending them
  - always send the thinking setting from config.py
  - keep the model loaded between requests
  - return timing numbers so speed problems are visible
"""

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image, ImageOps

import config


class OllamaError(RuntimeError):
    """Raised when the Ollama server cannot be reached or returns an error."""


@dataclass
class ChatResult:
    text: str
    thinking: str = ""
    stats: dict = field(default_factory=dict)


def _prepare_image(path: str, max_side: int) -> str:
    """Load an image, orient via EXIF, shrink if needed, and return base64-encoded JPEG."""
    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw)
        if image is None:
            image = raw.copy()
        image = image.convert("RGB")
    longest = max(image.size)
    if longest > max_side:
        scale = max_side / longest
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _seconds(value: Optional[int]) -> Optional[float]:
    """Ollama reports durations in nanoseconds."""
    return round(value / 1e9, 2) if value else None


def _build_stats(data: dict) -> dict:
    eval_count = data.get("eval_count")
    eval_duration = data.get("eval_duration")
    prompt_count = data.get("prompt_eval_count")
    prompt_duration = data.get("prompt_eval_duration")
    return {
        "total_seconds": _seconds(data.get("total_duration")),
        "load_seconds": _seconds(data.get("load_duration")),
        "prompt_tokens": prompt_count,
        "prompt_tokens_per_s": (
            round(prompt_count / (prompt_duration / 1e9), 1)
            if prompt_count and prompt_duration
            else None
        ),
        "generated_tokens": eval_count,
        "generated_tokens_per_s": (
            round(eval_count / (eval_duration / 1e9), 1)
            if eval_count and eval_duration
            else None
        ),
    }


def chat(
    prompt: str,
    image_path: Optional[str] = None,
    json_schema: Optional[dict] = None,
    system: Optional[str] = None,
) -> ChatResult:
    """Send one request to the model and return the reply with timing stats.

    json_schema: optional JSON schema; Ollama then forces the reply to match it.
    """
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})

    user_message: dict[str, Any] = {"role": "user", "content": prompt}
    if image_path:
        if not Path(image_path).is_file():
            raise FileNotFoundError(f"Image not found: {image_path}")
        user_message["images"] = [_prepare_image(image_path, config.MAX_IMAGE_SIDE)]
    messages.append(user_message)

    payload: dict[str, Any] = {
        "model": config.MODEL,
        "messages": messages,
        "stream": False,
        "think": config.THINK,
        "keep_alive": config.KEEP_ALIVE,
        "options": {
            "num_ctx": config.NUM_CTX,
            "num_predict": config.NUM_PREDICT,
            "temperature": config.TEMPERATURE,
        },
    }
    if json_schema is not None:
        payload["format"] = json_schema

    try:
        response = requests.post(
            f"{config.OLLAMA_URL}/api/chat",
            json=payload,
            timeout=config.TIMEOUT_SECONDS,
        )
    except requests.ConnectionError as exc:
        raise OllamaError(
            "Cannot reach Ollama. Start the Ollama app (or run `ollama serve`) "
            "and try again."
        ) from exc
    except requests.Timeout as exc:
        raise OllamaError(
            f"No reply within {config.TIMEOUT_SECONDS}s. The model may be loading "
            "or the image may be too large."
        ) from exc

    if response.status_code != 200:
        raise OllamaError(f"Ollama returned {response.status_code}: {response.text}")

    data = response.json()
    message = data.get("message", {})
    return ChatResult(
        text=message.get("content", ""),
        thinking=message.get("thinking", "") or "",
        stats=_build_stats(data),
    )


def warm_up() -> dict:
    """Load the model into memory with a tiny request. Call before a demo."""
    result = chat("Reply with the single word: ready")
    return result.stats


def list_local_models() -> list[str]:
    """Return the names of models Ollama can see."""
    try:
        response = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(f"Cannot list models: {exc}") from exc
    return [item["name"] for item in response.json().get("models", [])]
