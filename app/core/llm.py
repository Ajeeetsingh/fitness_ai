"""
Single place to configure which LLM endpoint the app uses.

Default is Ollama (local), but you can switch to any HTTP endpoint by editing
the configuration in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    base_url: str
    model: Optional[str] = None
    api_key: Optional[str] = None


# =============================================================================
# CHANGE LLM HERE (single place)
# =============================================================================
#
# Pick one of:
# - "ollama"  : local Ollama server (`/api/generate`)
# - "generic" : your own endpoint that accepts {"query": "...", "max_new_tokens": N}
#
LLM: LLMConfig = LLMConfig(
    provider="ollama",
    base_url="http://localhost:11434",
    model="llama3.1:8b",
)


def _timeout(timeout_s: float) -> httpx.Timeout:
    t = float(timeout_s)
    return httpx.Timeout(connect=5.0, read=t, write=15.0, pool=t)


def generate_text(prompt: str, max_new_tokens: int, timeout_s: float) -> str:
    """
    Send a prompt to the configured LLM and return raw text output.

    This function is the single integration point used by:
      - workout plan generation
      - orchestrator calls
      - JSON repair agent
    """
    provider = (LLM.provider or "generic").lower().strip()

    if provider == "ollama":
        if not LLM.model:
            raise ValueError("Ollama provider requires LLM.model to be set")

        url = LLM.base_url.rstrip("/") + "/api/generate"
        payload: Dict[str, Any] = {
            "model": LLM.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": int(max_new_tokens),
            },
        }
        headers = {"Content-Type": "application/json"}

    elif provider == "generic":
        url = LLM.base_url
        payload = {
            "query": prompt,
            "max_new_tokens": int(max_new_tokens),
        }
        headers = {"Content-Type": "application/json"}
        if LLM.api_key:
            headers["Authorization"] = f"Bearer {LLM.api_key}"

    else:
        raise ValueError(f"Unknown LLM provider: {LLM.provider!r}")

    with httpx.Client(timeout=_timeout(timeout_s)) as client:
        resp = client.post(url, headers=headers, json=payload)
    resp.raise_for_status()

    ctype = resp.headers.get("content-type", "")
    data: Any = resp.json() if "application/json" in ctype else resp.text

    # Common response shapes
    if isinstance(data, dict):
        for key in ("text", "response", "output", "answer", "content"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # OpenAI-ish shape
        try:
            v = data["choices"][0]["message"]["content"]
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            pass
        return str(data)

    if isinstance(data, str):
        return data.strip()

    return str(data)

