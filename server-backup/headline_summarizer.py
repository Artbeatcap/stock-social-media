import os
import logging
from typing import List, Dict, Any
import requests
import json

logger = logging.getLogger(__name__)

MODEL = os.getenv("SUMMARY_MODEL", "gpt-4o-mini")

# Check if OpenAI is available
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

CLIENT = None  # Lazily initialized to avoid import-time failures

def _ensure_client():
    """Create the OpenAI client if possible; stay silent on failure.
    This avoids noisy proxy-related warnings and lets us fall back to REST.
    """
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

SYSTEM = (
    "You are a professional day trader content creator market writer. For each headline, write a concise 2–5 "
    "sentence summary in plain English suitable for novice traders. No citations or sources. "
    "No ticker spam. Be clear and specific about what happened and why it matters."
)

def _prompt(title: str, seed_summary: str = None) -> str:
    base = f"Headline: {title}\n"
    if seed_summary:
        base += f"Existing blurb: {seed_summary}\n"
    base += (
        "Write exactly 2–5 sentences explaining the news and why traders should care. "
        "Do not include the word 'Source' or link text. Return only the paragraph."
    )
    return base

def call_openai_api_directly(api_key: str, messages: list, model: str = "gpt-5-nano") -> str:
    """Call OpenAI API directly using requests to bypass client issues"""
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": model,
            "messages": messages,
            "temperature": 1.0,
            "max_completion_tokens": 300
        }
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            logger.warning(f"OpenAI API error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.warning(f"Direct API call failed: {e}")
        return None

def summarize_single_headline(headline_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Add 'summary_2to5' to a single headline item"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.info("No OpenAI API key available, skipping headline summarization")
        return {**headline_dict, "summary_2to5": headline_dict.get("summary", "")}
    
    title = headline_dict.get("headline", "").strip()
    seed_summary = headline_dict.get("summary", "").strip()
    
    if not title:
        return {**headline_dict, "summary_2to5": seed_summary}
    
    # Try OpenAI client first (lazy init)
    _ensure_client()
    if CLIENT:
        try:
            messages = [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _prompt(title, seed_summary)}
            ]
            resp = CLIENT.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=1.0,
                max_completion_tokens=300,
            )
            summary_text = resp.choices[0].message.content.strip()
            
            # Ensure we have at least 2 sentences
            if summary_text.count(".") < 1:
                summary_text += " This could influence sector peers and short-term volatility."
            
            return {**headline_dict, "summary_2to5": summary_text}
            
        except Exception:
            # Quietly fall back to REST without noisy warnings
            pass
    
    # Try direct API call as fallback
    try:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(title, seed_summary)}
        ]
        summary_text = call_openai_api_directly(api_key, messages, MODEL)
        
        if summary_text:
            # Ensure we have at least 2 sentences
            if summary_text.count(".") < 1:
                summary_text += " This could influence sector peers and short-term volatility."
            
            return {**headline_dict, "summary_2to5": summary_text}
            
    except Exception as e:
        logger.warning(f"Direct API call failed for headline summarization: {e}")
    
    # Final fallback: use existing summary
    logger.info("Using existing summary as fallback for headline summarization")
    return {**headline_dict, "summary_2to5": seed_summary}

def summarize_headlines(headlines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add 'summary_2to5' to each headline item, using existing 'summary' as a seed if present."""
    if not headlines:
        return []
    
    logger.info(f"Summarizing {len(headlines)} headlines with AI")
    enriched = []
    
    for headline in headlines:
        try:
            enriched_headline = summarize_single_headline(headline)
            enriched.append(enriched_headline)
        except Exception as e:
            logger.warning(f"Failed to summarize headline '{headline.get('headline', '')}': {e}")
            # Fallback to original
            enriched.append({**headline, "summary_2to5": headline.get("summary", "")})
    
    logger.info(f"Successfully processed {len(enriched)} headlines")
    return enriched
