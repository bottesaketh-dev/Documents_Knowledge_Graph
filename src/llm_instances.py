"""
llm_instances.py - Centralized LLM factory and registry for all models in the repository.

Provides pre-configured instances for Groq and local Ollama models, dynamic discovery
of local Ollama models, and a unified `get_llm(model_key)` accessor.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Resolve and load environment variables from config/settings.env or .env
_FILE_PATH = Path(__file__).resolve()
_PROJECT_ROOT = (
    _FILE_PATH.parent.parent if _FILE_PATH.parent.name == "src" else _FILE_PATH.parent
)

for _env_file in [
    _PROJECT_ROOT / "config" / "settings.env",
    _PROJECT_ROOT / ".env",
    Path("config/settings.env"),
    Path(".env"),
]:
    if _env_file.exists():
        load_dotenv(_env_file, override=False)

# Ensure project root is in sys.path
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# Import ChatOpenAI from langchain_openai if installed; otherwise use native OpenAI wrapper fallback
try:
    from langchain_openai import ChatOpenAI
except ImportError:
    from openai import OpenAI

    class ChatOpenAI:  # type: ignore
        """
        Standalone lightweight fallback wrapper compatible with langchain_openai.ChatOpenAI
        using the installed native openai client.
        """

        def __init__(
            self,
            model: str,
            openai_api_key: str = "dummy",
            openai_api_base: Optional[str] = None,
            temperature: float = 0,
            max_tokens: Optional[int] = None,
            timeout: Optional[float] = None,
            max_retries: int = 2,
            streaming: bool = False,
            **kwargs,
        ):
            self.model = model
            self.temperature = temperature
            self.max_tokens = max_tokens
            self.timeout = timeout
            self.max_retries = max_retries
            self.streaming = streaming
            self._client = OpenAI(
                base_url=openai_api_base, api_key=openai_api_key or "dummy"
            )

        def invoke(self, input_data: Any, **kwargs):
            messages = []
            if isinstance(input_data, str):
                messages = [{"role": "user", "content": input_data}]
            elif isinstance(input_data, list):
                for m in input_data:
                    if hasattr(m, "content"):
                        role = getattr(m, "type", "user")
                        if role == "human":
                            role = "user"
                        elif role == "ai":
                            role = "assistant"
                        messages.append({"role": role, "content": str(m.content)})
                    elif isinstance(m, dict):
                        messages.append(m)
                    elif isinstance(m, tuple) and len(m) == 2:
                        messages.append({"role": m[0], "content": m[1]})
            else:
                messages = [{"role": "user", "content": str(input_data)}]

            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                timeout=self.timeout,
            )
            choice = resp.choices[0]
            content = (
                choice.message.content
                or getattr(choice.message, "reasoning_content", None)
                or getattr(choice.message, "thinking", None)
                or ""
            )

            class _AIMessage:

                def __init__(self, content: str):
                    self.content = content

                def __str__(self):
                    return self.content

                def __repr__(self):
                    return f"AIMessage(content={self.content!r})"

            return _AIMessage(content)

        def __call__(self, input_data: Any, **kwargs):
            return self.invoke(input_data, **kwargs)


LLM_INSTANCES: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------
# 1. Groq Cloud Model Configurations
# ---------------------------------------------------------
try:
    GROQ_API_KEY = os.environ.get(
        "GROQ_API_KEY",
        os.environ.get("LLM_API_KEY", "dummy-token-to-allow-startup"),
    )
    GROQ_ENDPOINT = "https://api.groq.com/openai/v1"

    GROQ_MODELS = {
        # Flagship versatile Groq models
        "GROQ_LLAMA_3_3_70B": "llama-3.3-70b-versatile",
        "llama-3.3-70b-versatile": "llama-3.3-70b-versatile",
        "GROQ_LLAMA_3_1_8B": "llama-3.1-8b-instant",
        "llama-3.1-8b-instant": "llama-3.1-8b-instant",
        # Legacy/custom alias
        "GROQ_GPT_OSS_120B": "openai/gpt-oss-120b",
        "openai/gpt-oss-120b": "openai/gpt-oss-120b",
    }

    configured_model = os.environ.get("LLM_MODEL", "")
    if configured_model and configured_model not in GROQ_MODELS and not configured_model.startswith("qwen"):
        GROQ_MODELS[configured_model] = configured_model

    for key, model_name in GROQ_MODELS.items():
        LLM_INSTANCES[key] = {
            "MODEL": ChatOpenAI(
                model=model_name,
                openai_api_key=GROQ_API_KEY,
                openai_api_base=GROQ_ENDPOINT,
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=2,
                streaming=True,
            )
        }
except Exception as _exp:
    logger.warning("Exception occurred while loading Groq LLM instance: %s", _exp)
    traceback.print_exc()

# ---------------------------------------------------------
# 2. Local Ollama Model Configurations
# ---------------------------------------------------------
OLLAMA_BASE_URL = os.environ.get(
    "OLLAMA_BASE_URL",
    os.environ.get(
        "LLM_BASE_URL", os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    ),
)
# If base url ends in /v1, strip it before appending /v1 or calling /api/tags
_cleaned_ollama_base = re.sub(r"/v1/?$", "", OLLAMA_BASE_URL.rstrip("/"))
OLLAMA_ENDPOINT = f"{_cleaned_ollama_base}/v1"
OLLAMA_API_KEY = os.environ.get(
    "OLLAMA_API_KEY", os.environ.get("LLM_API_KEY", "ollama")
)


def create_ollama_instance(model_name: str, temperature: float = 0) -> Dict[str, Any]:
    """Helper to instantiate a local Ollama ChatOpenAI model wrapper."""
    return {
        "MODEL": ChatOpenAI(
            model=model_name,
            openai_api_key=OLLAMA_API_KEY,
            openai_api_base=OLLAMA_ENDPOINT,
            temperature=temperature,
            max_tokens=None,
            timeout=None,
            max_retries=2,
        )
    }


try:
    # Explicit mapping of local Ollama models (including canonical keys and aliases)
    LOCAL_OLLAMA_MODELS = {
        # Qwen 2.5 VL 3B
        "OLLAMA_QWEN_2_5_VL_3B": "qwen2.5vl:3b",
        "OLLAMA_QWEN2_5_VL_3B": "qwen2.5vl:3b",
        "qwen2.5vl:3b": "qwen2.5vl:3b",
        # Qwen 3.5 4B Hermes
        "OLLAMA_QWEN_3_5_4B_HERMES": "qwen3.5-4b-hermes:latest",
        "OLLAMA_QWEN3_5_4B_HERMES": "qwen3.5-4b-hermes:latest",
        "qwen3.5-4b-hermes:latest": "qwen3.5-4b-hermes:latest",
        "qwen3.5-4b-hermes": "qwen3.5-4b-hermes:latest",
        # Qwen 3.5 9B
        "OLLAMA_QWEN_3_5_9B": "qwen3.5:9b",
        "OLLAMA_QWEN3_5_9B": "qwen3.5:9b",
        "qwen3.5:9b": "qwen3.5:9b",
        # Qwen 3.5 4B
        "OLLAMA_QWEN_3_5_4B": "qwen3.5:4b",
        "OLLAMA_QWEN3_5_4B": "qwen3.5:4b",
        "qwen3.5:4b": "qwen3.5:4b",
    }

    _created_ollama_instances: Dict[str, Dict[str, Any]] = {}
    for key, model_name in LOCAL_OLLAMA_MODELS.items():
        if model_name not in _created_ollama_instances:
            _created_ollama_instances[model_name] = create_ollama_instance(model_name)
        LLM_INSTANCES[key] = _created_ollama_instances[model_name]

    # Dynamic auto-discovery: query running Ollama to register any other installed models
    try:
        req = urllib.request.Request(f"{_cleaned_ollama_base}/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode())
            for item in data.get("models", []):
                tag = item.get("name")
                if tag and tag not in LLM_INSTANCES:
                    sanitized = re.sub(r"[^A-Z0-9]+", "_", tag.upper()).strip("_")
                    key = f"OLLAMA_{sanitized}"
                    inst = create_ollama_instance(tag)
                    if key not in LLM_INSTANCES:
                        LLM_INSTANCES[key] = inst
                    LLM_INSTANCES[tag] = inst
    except Exception:
        # If Ollama service is not running or unreachable at import time, static instances remain available
        pass

except Exception as _exp:
    logger.warning("Exception occurred while loading Ollama LLM instances: %s", _exp)
    traceback.print_exc()


def get_llm(model_key: Optional[str] = None) -> ChatOpenAI:
    """
    Retrieve an LLM instance by name/key, falling back to AI_MODEL, LLM_MODEL,
    GROQ_LLAMA_3_3_70B, GROQ_GPT_OSS_120B, or any available model.
    """
    key = (
        model_key
        or os.environ.get("AI_MODEL")
        or os.environ.get("LLM_MODEL")
        or ("openai/gpt-oss-120b" if os.environ.get("GROQ_API_KEY") else "qwen3.5:4b")
    )
    if key in LLM_INSTANCES:
        return LLM_INSTANCES[key]["MODEL"]

    # Case-insensitive lookup fallback
    for k, v in LLM_INSTANCES.items():
        if k.lower() == key.lower():
            return v["MODEL"]

    if "GROQ_LLAMA_3_3_70B" in LLM_INSTANCES:
        return LLM_INSTANCES["GROQ_LLAMA_3_3_70B"]["MODEL"]
    if "GROQ_GPT_OSS_120B" in LLM_INSTANCES:
        return LLM_INSTANCES["GROQ_GPT_OSS_120B"]["MODEL"]
    if LLM_INSTANCES:
        return next(iter(LLM_INSTANCES.values()))["MODEL"]
    raise ValueError(f"No LLM instance found for '{key}' and LLM_INSTANCES is empty.")


__all__ = [
    "ChatOpenAI",
    "LLM_INSTANCES",
    "create_ollama_instance",
    "get_llm",
]

