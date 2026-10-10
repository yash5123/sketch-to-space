"""Central settings for the project.

Every value can be overridden with an environment variable, so you can switch
models or turn thinking back on without editing code.

PowerShell examples (current window only):
    $env:READER_MODEL = "gemma4:e2b"
    $env:READER_THINK = "1"
"""

import os


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Where the local Ollama server listens (default Ollama address).
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Model used by the reader. Switch to "gemma4:e2b" if E4B is too slow or too big.
MODEL = os.getenv("READER_MODEL", "gemma4:e4b")

# Thinking mode OFF by default: extraction needs short structured output, and
# reasoning tokens only add delay. Set READER_THINK=1 to compare accuracy.
THINK = _flag("READER_THINK", False)

# How long Ollama keeps the model loaded after the last request.
# A long value avoids the slow cold start during a demo.
KEEP_ALIVE = os.getenv("READER_KEEP_ALIVE", "30m")

# Context window. Larger values use more VRAM, so keep it modest.
NUM_CTX = int(os.getenv("READER_NUM_CTX", "4096"))

# Upper limit on generated tokens per request (keeps replies short and fast).
NUM_PREDICT = int(os.getenv("READER_NUM_PREDICT", "700"))

# Low temperature for repeatable extraction.
TEMPERATURE = float(os.getenv("READER_TEMPERATURE", "0.1"))

# Images are shrunk so the longest side is at most this many pixels.
# Smaller images use less memory and process faster; too small hurts handwriting.
MAX_IMAGE_SIDE = int(os.getenv("READER_MAX_IMAGE_SIDE", "1280"))

# Seconds to wait for a reply before giving up.
TIMEOUT_SECONDS = int(os.getenv("READER_TIMEOUT", "300"))

# Tolerance in mm for dimension chain sum checks (defaults to 152.4 mm / 6 inches).
CHAIN_TOLERANCE_MM = float(os.getenv("CHAIN_TOLERANCE_MM", "152.4"))

# Confidence threshold for rescuing 2D room labels from OCR.
OCR_RESCUE_CONFIDENCE = float(os.getenv("OCR_RESCUE_CONFIDENCE", "0.85"))

