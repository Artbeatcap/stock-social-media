import os
import textwrap
from typing import Dict, Any

"""Lightweight GPT summary wrapper with safe fallbacks.

Avoids initializing the OpenAI client at import time to prevent runtime
errors during app/module import (e.g., proxy incompatibilities or console
encoding issues). The client is created lazily inside summarize_brief.
"""

# Check if OpenAI is available
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

MODEL = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")  # default

CLIENT = None  # Lazily initialized

SYSTEM = (
    "You are a pro market writer. Summarize for retail traders at ~8th–10th grade "
    "reading level. Be concise, scannable, and actionable. Avoid penny/illiquid tickers."
)

def _prompt(brief: Dict[str, Any]) -> str:
    # Build a compact, structured context for the model
    ah = brief.get("ah_moves", [])[:8]
    pre = brief.get("premarket_moves", [])[:8]
    earnings = brief.get("earnings", [])[:6]

    return f"""
Write a short subscriber summary (120–180 words) for today's U.S. market brief.

Tone: calm, direct, trader-friendly. No fluff. 
Format:
- Today in one line
- AH & Premarket: 3–5 notable movers with the reason (tickers only)
- Why it matters (macro/sector)
- Levels to watch (SPY ladder from brief)
- Tomorrow (next catalyst in 1 line)

DATA:
Market overview: {brief.get('market_overview','')}
Macro/data: {brief.get('macro_data','')}

AH movers (top):
{[{'t':m['ticker'],'mv':m['move'],'why':m.get('why','')} for m in ah]}

Premarket movers (top):
{[{'t':m['ticker'],'mv':m['move'],'why':m.get('why','')} for m in pre]}

Earnings (bullets):
{[e['ticker']+': '+e['note'] for e in earnings]}

SPY levels: S1 {brief.get('spy_s1')} → {brief.get('spy_s2')} | R {brief.get('spy_r1')}, {brief.get('spy_r2')}, {brief.get('spy_r3')}

Constraints:
- Keep under 180 words
- Use bullets and short sentences
- Never include sub-$1 or illiquid tickers
"""

def _ensure_client() -> None:
    """Create the OpenAI client if possible; stay silent on failure."""
    global CLIENT
    if CLIENT is not None or not OPENAI_AVAILABLE:
        return
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return
    try:
        CLIENT = OpenAI(api_key=api_key)
    except Exception:
        CLIENT = None


def summarize_brief(brief: Dict[str, Any]) -> Dict[str, str]:
    """Returns {'subscriber_summary': markdown, 'preheader': str}"""
    # Lazily initialize client and check availability
    _ensure_client()
    if not OPENAI_AVAILABLE or not CLIENT:
        # Fallback: rule-based summary
        fallback_summary = (
            f"• Today: {brief.get('market_overview','')}\n"
            f"• Movers: " +
            ", ".join([m['ticker'] for m in (brief.get('premarket_moves') or [])[:4]]) +
            f"\n• SPY: S {brief.get('spy_s1')}→{brief.get('spy_s2')} | R {brief.get('spy_r1')},{brief.get('spy_r2')},{brief.get('spy_r3')}"
        )
        return {
            "subscriber_summary": fallback_summary,
            "preheader_ai": f"Market Brief: {brief.get('market_overview','')}"[:140]
        }
    
    try:
        msg = _prompt(brief)
        resp = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": msg}
            ],
            temperature=1.0,
            max_completion_tokens=500,
        )
        
        summary_text = resp.choices[0].message.content.strip()
        
        # Build a tight preheader from the first line
        first_line = summary_text.strip().splitlines()[0][:140]
        
        return {
            "subscriber_summary": summary_text,
            "preheader_ai": first_line
        }
    except Exception as e:
        # Graceful fallback: tiny rule-based summary
        fallback_summary = (
            f"• Today: {brief.get('market_overview','')}\n"
            f"• Movers: " +
            ", ".join([m['ticker'] for m in (brief.get('premarket_moves') or [])[:4]]) +
            f"\n• SPY: S {brief.get('spy_s1')}→{brief.get('spy_s2')} | R {brief.get('spy_r1')},{brief.get('spy_r2')},{brief.get('spy_r3')}"
        )
        return {
            "subscriber_summary": fallback_summary,
            "preheader_ai": f"Market Brief: {brief.get('market_overview','')}"[:140]
        }
