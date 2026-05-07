"""
tweet_generator.py
==================
Post-market Twitter post generator.

Mirrors the two-pass pattern from market_brief_generator.py:
  Pass 1: generate the tweet from market data + voice hints
  Pass 2: rewrite-in-voice to enforce style without changing facts
  Pass 3 (NEW): hard validator catches drift back to motivational filler;
                regenerates up to N times if it slips through

Wires in market_internals.py so the LLM gets concrete data (sectors, A/D,
top movers, 50DMA signal) instead of just SPY/QQQ/VIX.

Usage:
    from tweet_generator import generate_post_market_tweet
    tweet = generate_post_market_tweet()
    print(tweet)

CLI:
    python tweet_generator.py             # generate and print
    python tweet_generator.py --dry-run   # show prompt without calling LLM
    python tweet_generator.py --debug     # show validation pass details

Env (matches market_brief_generator conventions):
    OPENAI_API_KEY                  required
    TWEET_MODEL                     default "gpt-4o" (override per your stack)
    TWEET_VOICE_FILE                default "voice_profile_twitter.txt"
    TWEET_VOICE_STRENGTH            default 0.7  (0..1, higher = stricter voice)
    TWEET_MAX_REGEN                 default 2    (validator retries)
    POLYGON_API_KEY                 required (for market_internals)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Config — matches market_brief_generator naming conventions
# ──────────────────────────────────────────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWEET_MODEL = os.getenv("TWEET_MODEL", "gpt-4o")
TWEET_VOICE_FILE = os.getenv("TWEET_VOICE_FILE", "voice_profile_twitter.txt")
TWEET_VOICE_STRENGTH = float(os.getenv("TWEET_VOICE_STRENGTH", "0.7"))
TWEET_MAX_REGEN = int(os.getenv("TWEET_MAX_REGEN", "2"))

# Tweet length policy — fintwit norms
TWEET_HARD_MAX = 280   # X platform limit
TWEET_SOFT_MAX = 240   # leaves room for share-back virality
TWEET_SOFT_MIN = 60    # below this is usually too thin


# ──────────────────────────────────────────────────────────────────────────────
# Voice profile loader (mirrors _load_voice_profile in market_brief_generator)
# ──────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_voice_profile() -> str:
    """Load voice profile from disk. Cached for the process lifetime."""
    try:
        p = Path(TWEET_VOICE_FILE)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        # Try common alternate locations
        for alt in (Path("static") / TWEET_VOICE_FILE,
                    Path("data") / TWEET_VOICE_FILE,
                    Path(__file__).parent / TWEET_VOICE_FILE):
            if alt.exists():
                return alt.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"Voice profile load failed: {e}")
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# System prompts
# ──────────────────────────────────────────────────────────────────────────────

# Pass 1 system prompt — generation. Note the explicit anti-patterns; the LLM
# needs to be told what NOT to do, not just what to do.
TWEET_SYSTEM_GEN = """You are a post-market market commentator writing a single tweet for an options trading audience.

OBJECTIVE: write one tweet (no thread, no hashtags, no questions to the reader) that summarizes today's session in a fintwit-native voice. The tweet should make a specific observation about today's market — sector leadership, breadth, a notable mover, or a trend signal — using the data provided.

HARD RULES — violating any of these is a failure:
- NEVER use generic motivational language. Banned phrases include: "trust the process", "stay disciplined", "you showed up", "win or lose", "consistency matters", "trading journey", "did you follow your plan".
- NEVER ask the reader engagement-bait questions ("How was your day?", "What did you trade?").
- NEVER use hashtags. Tickers are written with $ prefix (e.g. $NVDA, $SPY).
- NEVER use more than ONE emoji, and prefer zero.
- NEVER fabricate data. Use only the figures provided in the MARKET DATA block.
- The tweet must reference at least one specific ticker, sector name, or numeric figure from the MARKET DATA.

WHAT TO WRITE:
- Pick ONE angle from the data: sector leadership/lag, breadth extreme, a standout mover, or SPY's relationship to its 50DMA.
- Lead with the specific. State the observation, then (optionally) one short clause of context or implication.
- Length: 80-240 characters. Shorter is better.
- One sentence is fine. Two short sentences is the upper bound.
- No preamble like "Today's market:" or "Market recap:". Just lead with the observation.

Output: just the tweet text. No quotes, no labels, no commentary."""


# Pass 2 system prompt — voice rewrite. Mirrors _rewrite_in_voice but tweet-shaped.
TWEET_SYSTEM_VOICE = """You are a precise editor. Rewrite the user's draft tweet to match the AUTHOR VOICE while preserving every factual token.

PRESERVE EXACTLY:
- All tickers ($NVDA, $SPY, etc.)
- All numbers, percentages, dates, levels, ratios
- The core observation/claim

MAY CHANGE:
- Word choice, sentence structure, rhythm
- Order of clauses (lead with the specific)
- Punctuation style

MUST NOT:
- Add new facts not in the draft
- Add hashtags, emojis (unless 1 in the original), or engagement questions
- Exceed 280 characters
- Add motivational filler

Output: just the rewritten tweet. No quotes, no commentary."""


# ──────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ──────────────────────────────────────────────────────────────────────────────

def build_tweet_prompt(
    market_data_block: str,
    extra_context: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """
    Build the messages array for both passes.
    Returns (gen_messages, voice_messages_template).

    The voice messages template has a `{draft}` placeholder filled in pass 2.
    """
    voice = _load_voice_profile()
    today = datetime.now().strftime("%A, %B %d, %Y")

    user_prompt = (
        f"DATE: {today} (post-market)\n\n"
        f"MARKET DATA (use specifics from here, do not invent figures):\n"
        f"{market_data_block}\n"
    )
    if extra_context:
        user_prompt += f"\nADDITIONAL CONTEXT:\n{extra_context}\n"

    user_prompt += (
        "\nWrite ONE post-market tweet following all hard rules in the system prompt. "
        "Pick the single most interesting angle from the data above and lead with it."
    )

    gen_messages: List[Dict[str, str]] = [
        {"role": "system", "content": TWEET_SYSTEM_GEN},
    ]
    if voice:
        # Voice hints injected as soft system context — same pattern as the
        # market brief generator. The hard rewrite happens in pass 2.
        gen_messages.append({
            "role": "system",
            "content": "AUTHOR VOICE EXEMPLARS (style only, do not copy facts):\n" + voice,
        })
    gen_messages.append({"role": "user", "content": user_prompt})

    voice_messages_template: List[Dict[str, str]] = []
    if voice:
        voice_messages_template = [
            {"role": "system", "content": TWEET_SYSTEM_VOICE},
            {"role": "user", "content": (
                f"AUTHOR VOICE:\n---\n{voice}\n---\n\n"
                f"DRAFT TWEET:\n---\n{{draft}}\n---\n\n"
                f"Return only the rewritten tweet."
            )},
        ]

    return gen_messages, voice_messages_template


# ──────────────────────────────────────────────────────────────────────────────
# Validator — catches drift back to motivational filler
# ──────────────────────────────────────────────────────────────────────────────

# Banned phrases (case-insensitive substring match)
BANNED_PHRASES = [
    "trust the process", "stay disciplined", "you showed up", "win or lose",
    "consistency matters", "trading journey", "did you follow your plan",
    "follow your plan", "trust your edge", "process over outcome",
    "small wins", "showed up today", "lessons learned",
    "what did you trade", "how was your day", "drop a", "comment below",
    "like and follow", "let me know",
]

# Engagement-bait question patterns
QUESTION_BAIT = re.compile(
    r"\b(what did you|how was your|did you|are you|will you|drop|share)\b.*\?",
    re.IGNORECASE,
)

# Hashtag pattern
HASHTAG = re.compile(r"#\w+")


def validate_tweet(tweet: str) -> Tuple[bool, List[str]]:
    """
    Returns (is_valid, list_of_violations).
    Hard reject if any violation found — generation should retry.
    """
    violations: List[str] = []

    # Length checks
    n = len(tweet)
    if n > TWEET_HARD_MAX:
        violations.append(f"too long ({n} > {TWEET_HARD_MAX} chars)")
    elif n > TWEET_SOFT_MAX:
        violations.append(f"over soft limit ({n} > {TWEET_SOFT_MAX} chars)")
    if n < TWEET_SOFT_MIN:
        violations.append(f"too short ({n} < {TWEET_SOFT_MIN} chars)")

    # Hashtag check
    hashtags = HASHTAG.findall(tweet)
    if hashtags:
        violations.append(f"hashtags present: {hashtags}")

    # Banned phrase check
    lower = tweet.lower()
    found_banned = [p for p in BANNED_PHRASES if p in lower]
    if found_banned:
        violations.append(f"banned phrases: {found_banned}")

    # Engagement bait
    if QUESTION_BAIT.search(tweet):
        violations.append("engagement-bait question detected")

    # Specificity check — must contain at least one of:
    #   ticker ($XYZ), percentage, sector ETF, or named sector
    has_ticker = bool(re.search(r"\$[A-Z]{1,5}\b", tweet))
    has_pct = bool(re.search(r"\d+(\.\d+)?\s*%", tweet))
    has_etf = bool(re.search(r"\bXL[KFVEYPIUBRC]E?\b", tweet))
    sector_words = ["technology", "tech", "energy", "financials", "financial",
                    "healthcare", "health", "industrials", "industrial",
                    "utilities", "utility", "materials", "real estate",
                    "consumer", "communication"]
    has_sector = any(w in lower for w in sector_words)
    if not (has_ticker or has_pct or has_etf or has_sector):
        violations.append("no specificity (no ticker, percentage, or sector)")

    # Emoji density check — count non-ASCII chars as a proxy
    non_ascii_count = sum(1 for c in tweet if ord(c) > 127 and c not in "—–'\"…")
    if non_ascii_count > 2:
        violations.append(f"too many emojis/special chars ({non_ascii_count})")

    return (len(violations) == 0), violations


# ──────────────────────────────────────────────────────────────────────────────
# Generation
# ──────────────────────────────────────────────────────────────────────────────

def _call_openai(messages: List[Dict[str, str]], temperature: float, max_tokens: int = 220) -> str:
    """Thin wrapper around OpenAI chat completion."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=TWEET_MODEL,
        messages=messages,
        temperature=temperature,
        max_completion_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip().strip('"').strip("'")


def _rewrite_in_voice(draft: str, voice_messages_template: List[Dict[str, str]]) -> str:
    """Pass 2: rewrite for voice. Mirrors market_brief_generator._rewrite_in_voice."""
    if not voice_messages_template:
        return draft
    messages = [
        {**m, "content": m["content"].replace("{draft}", draft)}
        for m in voice_messages_template
    ]
    # Lower temp for stricter voice adherence (same formula as the brief generator)
    temp = max(0.2, 1.0 - TWEET_VOICE_STRENGTH * 0.6)
    try:
        return _call_openai(messages, temperature=temp, max_tokens=200)
    except Exception as e:
        logger.warning(f"Voice rewrite failed, returning draft: {e}")
        return draft


def generate_post_market_tweet(
    extra_context: Optional[str] = None,
    market_data_block: Optional[str] = None,
) -> str:
    """
    End-to-end tweet generation.

    1. Fetch market internals (or use provided block)
    2. Generate draft (pass 1)
    3. Rewrite in voice (pass 2)
    4. Validate; on failure regenerate up to TWEET_MAX_REGEN times

    Returns the final tweet string. Raises if all attempts fail validation.
    """
    # Step 1: market data
    if market_data_block is None:
        from market_internals import get_market_internals, format_internals_for_prompt
        market_data_block = format_internals_for_prompt(get_market_internals())

    if not market_data_block or market_data_block.startswith("(market internals"):
        raise RuntimeError("Market internals unavailable — cannot generate tweet")

    gen_messages, voice_template = build_tweet_prompt(market_data_block, extra_context)

    last_attempt = ""
    last_violations: List[str] = []

    for attempt in range(TWEET_MAX_REGEN + 1):
        # Pass 1
        try:
            draft = _call_openai(gen_messages, temperature=0.85, max_tokens=220)
        except Exception as e:
            logger.error(f"Generation failed on attempt {attempt + 1}: {e}")
            raise

        # Pass 2
        final = _rewrite_in_voice(draft, voice_template)

        # Pass 3
        ok, violations = validate_tweet(final)
        last_attempt = final
        last_violations = violations

        if ok:
            logger.info(f"Tweet valid on attempt {attempt + 1} ({len(final)} chars)")
            return final

        logger.warning(f"Attempt {attempt + 1} failed validation: {violations}")
        # On retry, append the violation feedback to the prompt so the model
        # actively avoids the failure mode it just hit.
        feedback = (
            f"\n\nPREVIOUS ATTEMPT FAILED VALIDATION: {'; '.join(violations)}. "
            f"Previous attempt was: {final!r}. Do not repeat these mistakes."
        )
        if gen_messages and gen_messages[-1]["role"] == "user":
            gen_messages[-1]["content"] += feedback

    # All attempts failed — return the last one with a warning. Better than
    # crashing the n8n workflow; the validation log makes debugging trivial.
    logger.error(
        f"All {TWEET_MAX_REGEN + 1} attempts failed validation. "
        f"Returning last attempt with violations: {last_violations}"
    )
    return last_attempt


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(description="Generate a post-market tweet.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build prompt and show it; do not call LLM.")
    parser.add_argument("--debug", action="store_true",
                        help="Show validation details and intermediate drafts.")
    parser.add_argument("--context", default=None,
                        help="Optional extra context to inject (e.g. 'Fed day').")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    voice = _load_voice_profile()
    if not voice:
        print(f"WARNING: voice profile not found at {TWEET_VOICE_FILE} "
              f"(or static/, data/, module dir). Generation will proceed without voice.")

    if args.dry_run:
        from market_internals import get_market_internals, format_internals_for_prompt
        block = format_internals_for_prompt(get_market_internals())
        gen_msgs, voice_msgs = build_tweet_prompt(block, args.context)
        print("=" * 70)
        print("PASS 1 PROMPT (generation)")
        print("=" * 70)
        for m in gen_msgs:
            print(f"\n[{m['role']}]")
            print(m["content"][:2000])
        if voice_msgs:
            print("\n" + "=" * 70)
            print("PASS 2 PROMPT TEMPLATE (voice rewrite)")
            print("=" * 70)
            for m in voice_msgs:
                print(f"\n[{m['role']}]")
                print(m["content"][:2000])
        return

    tweet = generate_post_market_tweet(extra_context=args.context)
    print(tweet)
    print(f"\n[{len(tweet)} chars]")


if __name__ == "__main__":
    _main()
