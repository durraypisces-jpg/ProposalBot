"""Interactive setup: `python -m reporting_assistant.cli setup`.

Asks which AI to use and for its key (typed hidden), finds a model that answers
right now, and saves the settings to .env so every terminal picks them up.
.env is in .gitignore, so the key is never committed.
"""

from __future__ import annotations

import getpass
import os
import re
from pathlib import Path
from typing import Callable

import httpx

from .config import ENV_FILE
from .openai_compatible import PROVIDERS, ProviderError, list_models, probe

# Model names that are not general chat models (images, speech, research agents...).
NOT_CHAT = ("image", "tts", "audio", "live", "embed", "computer-use", "research", "antigravity",
            "aqa", "veo", "imagen", "learnlm", "vision", "guard", "whisper")
MAX_TRIES = 8


def chat_model_candidates(names: list[str]) -> list[str]:
    """Most likely to work on a free tier first: lite models, then flash, then the rest."""
    chat = [n for n in names if not any(word in n.lower() for word in NOT_CHAT)]

    def rank(name: str) -> int:
        words = set(re.split(r"[-_.:/]", name.lower()))  # "gemini" must not count as "mini"
        if "lite" in words:
            return 0
        if words & {"flash", "instant", "mini", "small", "fast"}:
            return 1
        return 2

    return sorted(chat, key=rank)  # stable: keeps newest-first within each group


def find_working_model(provider: str, api_key: str, say: Callable[[str], None],
                       client: httpx.Client | None = None) -> str | None:
    names = list_models(provider, api_key, client)
    candidates = chat_model_candidates(names)[:MAX_TRIES]
    if not candidates:
        say("No chat models were listed for this key.")
        return None
    for model in candidates:
        ok, message = probe(provider, model, api_key, client)
        say(f"  {'OK  ' if ok else 'skip'} {model}: {message}")
        if ok:
            return model
    return None


def write_env(values: dict[str, str], path: Path = ENV_FILE) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def run_setup(ask: Callable[[str], str] = input, ask_secret: Callable[[str], str] = getpass.getpass,
              say: Callable[[str], None] = print, client: httpx.Client | None = None,
              env_path: Path = ENV_FILE) -> bool:
    say("Which AI should the assistant use?")
    say("  1) Google Gemini  (free key from aistudio.google.com)")
    say("  2) Ollama         (free, no key; runs a model in this codespace)")
    say("  3) Claude         (Anthropic API key)")
    choice = ask("Type 1, 2 or 3 and press Enter: ").strip()

    if choice == "3":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip() or \
            ask_secret("Paste your Anthropic API key (it stays hidden) and press Enter: ").strip()
        if not key:
            say("No key entered. Nothing saved.")
            return False
        write_env({"RA_PROVIDER": "claude", "ANTHROPIC_API_KEY": key}, env_path)
        say(f"Saved to {env_path}. Open a NEW terminal, then run: python -m reporting_assistant.cli check")
        return True

    if choice == "2":
        provider, key = "ollama", ""
        try:
            names = list_models(provider, client=client)
        except (ProviderError, httpx.HTTPError):
            say("Ollama isn't running. Run these, then run setup again:")
            say("  curl -fsSL https://ollama.com/install.sh | sh")
            say("  ollama serve > /tmp/ollama.log 2>&1 &")
            say("  ollama pull qwen2.5:3b")
            return False
        if not names:
            say("Ollama is running but has no models. Run: ollama pull qwen2.5:3b   then run setup again.")
            return False
        say("Testing your downloaded models (the first answer can take a minute)...")
    elif choice == "1":
        provider = "gemini"
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if key:
            say("Using the Gemini key already saved (from .env or the terminal).")
        else:
            key = ask_secret("Paste your Gemini API key (it stays hidden) and press Enter: ").strip()
        if not key:
            say("No key entered. Nothing saved.")
            return False
        say("Finding a Gemini model that is answering right now...")
    else:
        say("Please run setup again and type 1, 2 or 3.")
        return False

    try:
        model = find_working_model(provider, key, say, client)
    except (ProviderError, httpx.HTTPError) as exc:
        say(f"Couldn't list models: {exc}")
        if provider == "gemini":
            say("Check the key at aistudio.google.com and run setup again.")
        return False
    if not model:
        say("None of the models answered. They may all be busy: wait a few minutes and run setup again.")
        return False

    values = {"RA_PROVIDER": provider, "RA_LLM_MODEL": model}
    if key:
        values[PROVIDERS[provider].key_env] = key
    write_env(values, env_path)
    say("")
    say(f"Done. Using {PROVIDERS[provider].name} model {model}. Settings saved to {env_path}.")
    say("Open a NEW terminal (so old settings are cleared), then run:")
    say("  python -m eval.run_eval")
    say("  python -m uvicorn reporting_assistant.app:app --port 8000")
    return True
