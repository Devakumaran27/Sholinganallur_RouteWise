"""
Module: app.ai.rag.token_counter
Purpose: Offline deterministic heuristic token estimator for pipeline sizing.
         Provides an offline heuristic estimation without calling external Gemini APIs
         or requiring native C-extension dependencies.
"""

import math
import re
from typing import Any, Dict

TOKENIZER_METHOD = "heuristic_subword_bpe_regex"
TOKENIZER_NAME = "RouteWise Offline Heuristic Token Estimator"
TOKENIZER_VERSION = "v1.0.0"
CALIBRATION_STANDARD = "Offline Heuristic Estimation (~3.8 chars/alphanumeric cluster + standalone symbols)"


def count_tokens(text: str) -> int:
    """
    Computes an offline heuristic token count estimate for the provided text.
    
    Methodology:
    - Splits text into alphanumeric clusters and standalone punctuation/symbol tokens using regex.
    - Punctuation marks, brackets, arithmetic operators, and Markdown syntax delimiters each count as 1 token.
    - Alphanumeric word clusters are estimated using a heuristic divisor of 3.8 characters per cluster.
    - This is strictly an offline heuristic estimator and does not claim exact Gemini token counts.
    - Official Gemini token counts must be verified using the official Gemini API / SDK tokenizer prior to embedding.
    """
    if not text or not text.strip():
        return 0

    # Match alphanumeric token sequences or single non-whitespace non-alphanumeric symbols
    tokens = re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9\s]", text)
    total_tokens = 0

    for token in tokens:
        if token.isalnum():
            # Heuristic subword cluster divisor: ~3.8 chars per estimated token
            total_tokens += max(1, math.ceil(len(token) / 3.8))
        else:
            # Individual punctuation / markdown symbol
            total_tokens += 1

    return int(total_tokens)


def estimate_tokens(text: str) -> int:
    """Alias for count_tokens."""
    return count_tokens(text)


def get_token_counter_metadata() -> Dict[str, Any]:
    """Returns technical metadata regarding the token counting implementation."""
    return {
        "tokenizer_method": TOKENIZER_METHOD,
        "tokenizer_name": TOKENIZER_NAME,
        "tokenizer_version": TOKENIZER_VERSION,
        "calibration_standard": CALIBRATION_STANDARD,
        "is_exact_api_count": False,
        "api_dependency": None,
        "notes": (
            "This component is an offline heuristic token estimator used for local document chunk sizing. "
            "It does not claim exact Gemini token counts. Official Gemini token counts will be verified "
            "using the official Gemini SDK / API prior to batch embedding in Phase 3 Step 6."
        ),
    }

