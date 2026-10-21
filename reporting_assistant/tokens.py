"""Token estimates (proposal: Token Metering, "Tokenizer (estimates only)").

Before a request, the service estimates its size to check the client's quota.
It loads the deployed model's own tokenizer with Hugging Face transformers
when that library and the tokenizer files are available on the server, and
otherwise uses a rough four-characters-per-token estimate.

Estimates are never used for billing: billing uses the token counts the model
server returns with each response (see llm.Usage).
"""

from __future__ import annotations

import math
from functools import lru_cache


@lru_cache(maxsize=8)
def _tokenizer(model: str):
    try:
        from transformers import AutoTokenizer  # optional dependency
    except ImportError:
        return None
    try:
        # local_files_only: the servers are offline; tokenizers are copied with the model weights.
        return AutoTokenizer.from_pretrained(model, local_files_only=True)
    except Exception:
        return None


def count_tokens(text: str, model: str = "") -> int:
    if not text:
        return 0
    tokenizer = _tokenizer(model) if model else None
    if tokenizer is not None:
        return len(tokenizer.encode(text, add_special_tokens=False))
    return math.ceil(len(text) / 4)
