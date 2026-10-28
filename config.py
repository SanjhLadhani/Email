"""Shared settings for every step of the email triage agent.

Values come from real environment variables first, then from the local
``.env`` file (git-ignored; see ``.env.example``). ``.env`` is re-read on every
call, so edits take effect without restarting anything.

The model server address is special: when ``NTFY_TOPIC`` is set, the Colab
notebook publishes its current tunnel URL to that topic on ntfy.sh and this
module picks the newest one up automatically (see ``colab_ollama_server.ipynb``).
"""

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date
from typing import NamedTuple
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ENV_PATH = Path(__file__).parent / ".env"
DB_PATH = Path(__file__).parent / "triage.db"

# The only hosts we accept from the remote announcement. Anyone who learns your
# topic name could post a URL there, and that server would then see your emails.
ALLOWED_REMOTE_HOSTS = (".trycloudflare.com",)


def _env(key: str, default: str = "") -> str:
    """Read a setting: a real environment variable wins, then ``.env``."""
    return os.environ.get(key) or dotenv_values(ENV_PATH).get(key) or default


EMAIL_ADDRESS = _env("EMAIL_ADDRESS")
# Gmail shows app passwords in groups of four; the spaces are not part of it.
EMAIL_APP_PASSWORD = _env("EMAIL_APP_PASSWORD").replace(" ", "")
IMAP_HOST = _env("IMAP_HOST", "imap.gmail.com")

_remote = {"checked_at": 0.0, "data": {}}


def refresh() -> None:
    """Forget cached lookups so the next call re-checks the Colab URL and the backend."""
    _remote["checked_at"] = 0.0
    _backend_cache["checked_at"] = 0.0


def _remote_settings(max_age: float = 60.0) -> dict:
    """Return the newest ``{"url", "model"}`` the Colab notebook announced, or ``{}``.

    Reads the messages on the ``NTFY_TOPIC`` topic and takes the newest valid one (ntfy.sh keeps messages
    for 12 hours; the notebook re-announces every few minutes). Cached for
    ``max_age`` seconds. Any failure returns ``{}`` so ``.env`` is used instead.
    """
    topic = _env("NTFY_TOPIC")
    if not topic:
        return {}
    if time.time() - _remote["checked_at"] < max_age:
        return _remote["data"]
    data: dict = {}
    try:
        with urllib.request.urlopen(
            f"https://ntfy.sh/{topic}/json?poll=1&since=12h", timeout=10
        ) as resp:
            lines = resp.read().decode("utf-8").splitlines()
        best = -1.0
        for line in lines:
            event = json.loads(line)
            if event.get("event") != "message":
                continue
            try:
                candidate = json.loads(event["message"])
                host = urlparse(candidate["url"]).hostname or ""
            except (ValueError, KeyError, TypeError):
                continue
            if not host.endswith(ALLOWED_REMOTE_HOSTS):
                continue  # ignore announcements pointing anywhere unexpected
            # Prefer the sender's own timestamp: ntfy does not order messages
            # that arrive within the same second.
            stamp = float(candidate.get("ts") or event.get("time") or 0)
            if stamp >= best:
                best, data = stamp, candidate
    except Exception:
        data = {}
    _remote.update(checked_at=time.time(), data=data)
    return data


# Never run the model on this computer: it is too weak and freezes the machine.
# Anything pointing at localhost is refused and treated as "no model server".
_NO_SERVER = "https://colab-not-connected.invalid/api/generate"


def ollama_url() -> str:
    """The Colab Ollama endpoint to use right now.

    The URL the Colab notebook announced, else ``OLLAMA_URL`` from ``.env``.
    A localhost address is refused (see ``_NO_SERVER``).
    """
    url = _remote_settings().get("url") or _env("OLLAMA_URL")
    host = urlparse(url).hostname or ""
    if not url or host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return _NO_SERVER
    return url


def ollama_model() -> str:
    """The model name to use right now (announced by Colab, else ``OLLAMA_MODEL``)."""
    return _remote_settings().get("model") or _env("OLLAMA_MODEL", "qwen3:8b")


def ollama_server() -> str:
    """Just ``scheme://host[:port]`` of the current Ollama server."""
    parsed = urlparse(ollama_url())
    return f"{parsed.scheme}://{parsed.netloc}"


def ollama_base_url() -> str:
    """Return the OpenAI-compatible base URL for the current Ollama server.

    Pydantic AI talks to Ollama through its OpenAI-compatible API, which lives
    under ``/v1`` on the same host as the native ``/api/...`` endpoints.
    """
    return f"{ollama_server()}/v1"


# --------------------------------------------------------------------------
# Which model answers right now: the Colab GPU, or a hosted API (no setup per session)
# --------------------------------------------------------------------------

# OpenAI-compatible endpoints of hosted providers, with a default model each.
# Override the model with FALLBACK_MODEL in .env (model names change over time).
API_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
}

NO_MODEL_MESSAGE = (
    "No model is available: the Colab GPU is offline and no API key is set. "
    "Add FALLBACK_API_KEY to .env (see README) and nothing needs starting by hand."
)


class Backend(NamedTuple):
    """A reachable model server."""

    kind: str  # "colab" or "api"
    base_url: str  # OpenAI-compatible base URL
    model: str
    api_key: str | None


_backend_cache: dict = {"checked_at": 0.0, "value": None}


def api_provider() -> str:
    """Name of the configured hosted provider (``groq`` by default)."""
    return _env("FALLBACK_PROVIDER", "groq").lower()


def _colab_backend() -> Backend | None:
    """The Colab server if it answers and has the model, else None."""
    server = ollama_server()
    if urlparse(server).hostname == "colab-not-connected.invalid":
        return None
    model = ollama_model()
    try:
        with urllib.request.urlopen(f"{server}/api/tags", timeout=5) as resp:
            names = [m["name"] for m in json.load(resp)["models"]]
    except Exception:
        return None
    if not any(n == model or n.startswith(model + ":") for n in names):
        return None
    return Backend("colab", f"{server}/v1", model, None)


def _api_backend() -> Backend | None:
    """The hosted API if a key is set and the provider accepts it, else None."""
    key = _env("FALLBACK_API_KEY").strip()
    preset = API_PRESETS.get(api_provider())
    if not key or not preset:
        return None
    base_url, default_model = preset
    request = urllib.request.Request(
        base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=8):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):  # the key itself is wrong
            return None
    except Exception:
        return None  # provider unreachable right now
    return Backend("api", base_url, _env("FALLBACK_MODEL", default_model), key)


def backend(max_age: float = 30.0) -> Backend | None:
    """Pick the model server to use right now, or None if nothing is available.

    ``MODEL_ORDER`` in ``.env`` lists what to try first (default ``colab,api``).
    Use ``MODEL_ORDER=api`` to never depend on a Colab session. The result is
    cached for ``max_age`` seconds.
    """
    if time.time() - _backend_cache["checked_at"] < max_age:
        return _backend_cache["value"]
    found = None
    for kind in (k.strip() for k in _env("MODEL_ORDER", "colab,api").split(",")):
        found = {"colab": _colab_backend, "api": _api_backend}.get(kind, lambda: None)()
        if found:
            break
    _backend_cache.update(checked_at=time.time(), value=found)
    return found


USAGE_PATH = Path(__file__).parent / "api_usage.json"


class DailyLimitReached(RuntimeError):
    """Today's hosted-API allowance (``API_DAILY_LIMIT``) is used up."""


def count_api_call() -> None:
    """Count one hosted-API request for today and refuse past ``API_DAILY_LIMIT``.

    Keeps usage inside a free tier's daily quota. Colab is never counted.

    Raises:
        DailyLimitReached: When today's limit (default 100 analyses) is used up.
    """
    limit = int(_env("API_DAILY_LIMIT", "100") or 100)
    today = date.today().isoformat()
    try:
        usage = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        usage = {}
    if usage.get("day") != today:
        usage = {"day": today, "calls": 0}
    if usage["calls"] >= limit:
        raise DailyLimitReached(
            f"Daily API limit reached ({limit} per day, to stay within the free tier). "
            "It resets tomorrow; raise API_DAILY_LIMIT in .env to allow more."
        )
    usage["calls"] += 1
    USAGE_PATH.write_text(json.dumps(usage), encoding="utf-8")


def require_email_credentials() -> tuple[str, str]:
    """Return the Gmail address and app password, or raise if either is missing.

    Raises:
        RuntimeError: If ``EMAIL_ADDRESS`` or ``EMAIL_APP_PASSWORD`` is unset.
    """
    if not EMAIL_ADDRESS or not EMAIL_APP_PASSWORD:
        raise RuntimeError(
            "Set EMAIL_ADDRESS and EMAIL_APP_PASSWORD in your .env file."
        )
    return EMAIL_ADDRESS, EMAIL_APP_PASSWORD
