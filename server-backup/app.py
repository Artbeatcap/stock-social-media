from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session, current_app, make_response
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_migrate import Migrate
from config import Config
# Import MarketBriefSubscriber
from models import (
    db,
    User,
    Trade,
    TradeAnalysis,
    TradingJournal,
    UserSettings,
    MarketBriefSubscriber,
)
from forms import (
    LoginForm,
    RegistrationForm,
    TradeForm,
    QuickTradeForm,
    JournalForm,
    EditTradeForm,
    SettingsForm,
    UserSettingsForm,
    BulkAnalysisForm,
    ResetPasswordRequestForm,
    ResetPasswordForm,
    MarketBriefSignupForm,
)
from ai_analysis import TradingAIAnalyzer
from datetime import datetime, timedelta, date
import pandas as pd
import plotly.graph_objs as go
import plotly.utils
import json
import os
import secrets
import requests
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq
import math
from itertools import zip_longest
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message
from pathlib import Path
from market_brief_generator import send_weekly_market_brief_to_subscribers
import io
import base64
import time
import datetime as dt
from PIL import Image
import csv

# === API Blueprints: AI Explain, Options, Usage, Imports ===
from flask import Blueprint
from datetime import timezone
import hashlib
import io as _io

# Options Generator
options_bp = Blueprint('options', __name__, url_prefix='/api/options')

def _ibkr_csv(rows):
    header = "Account,Symbol,SecType,Right,Strike,Expiry,Action,Quantity,OrderType,LmtPrice,TIF,Exchange,Currency"
    return header + "\n" + "\n".join(rows)

def _exp_str(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    tos = dt.strftime("%d %b %y").upper()
    ib = dt.strftime("%Y%m%d")
    return ib, tos, dt.strftime("%Y-%m-%d")

@options_bp.route('/generate', methods=['POST'])
def options_generate():
    payload = request.get_json(force=True) or {}
    symbol = (payload.get('symbol') or 'NVDA').upper()
    under = float(payload.get('underlying_price') or 128.42)
    iv_rank = float((payload.get('market') or {}).get('iv_rank') or 38)
    exp_iso_in = (payload.get('constraints') or {}).get('exp') or '2025-11-15'
    ib_exp, tos_exp, exp_iso = _exp_str(exp_iso_in)

    # Example vertical call debit spread
    buy_k, sell_k = 125, 130
    width = sell_k - buy_k
    mid = 2.05
    cash_flow = -mid * 100
    ib_rows = [
        f",{symbol},OPT,C,{buy_k},{ib_exp},BUY,1,LMT,{mid:.2f},DAY,SMART,USD",
        f",{symbol},OPT,C,{sell_k},{ib_exp},SELL,1,LMT,{mid:.2f},DAY,SMART,USD",
    ]
    strat_vertical = {
        "id": "strat_01",
        "type": "vertical_debit_call",
        "label": "Bullish Call Debit Spread",
        "quality_score": 0.8,
        "legs": [
            {"kind":"option","side":"BUY","right":"C","exp":exp_iso,"strike":buy_k,"qty":1,"multiplier":100},
            {"kind":"option","side":"SELL","right":"C","exp":exp_iso,"strike":sell_k,"qty":1,"multiplier":100}
        ],
        "pricing": {"mid": mid, "nat": round(mid+0.07,2), "cash_flow": cash_flow, "mark_perc_width": round((mid/width)*100,1)},
        "greeks": {"delta": 0.29, "gamma": 0.04, "theta": -3.2, "vega": 7.5, "rho": 0.9},
        "iv_context": {"iv_rank": iv_rank, "skew": (payload.get('market') or {}).get('skew', 0.0)},
        "risk": {"max_profit": width*100 - mid*100, "max_loss": mid*100, "break_evens": [buy_k + mid], "pop": 0.58, "p50": 95.0, "margin_requirement": mid*100, "buying_power_effect": mid*100},
        "payoff": {"grid_start": int(under*0.85), "grid_end": int(under*1.2), "grid_step": 1, "points": [{"underlier": int(under*0.85), "pnl": -round(mid*100,2)}]},
        "order_tickets": {
            "ibkr_basket_csv": _ibkr_csv(ib_rows),
            "thinkorswim_clipboard": f"BUY +1 VERTICAL {symbol} {tos_exp} {buy_k}/{sell_k} CALL @ DEBIT ~{mid:.2f}",
            "tasty_clipboard": f"BUY 1 {symbol} {exp_iso} {buy_k}/{sell_k} CALL DEBIT ~{mid:.2f}",
            "robinhood_clipboard": f"{symbol} • {exp_iso} • {buy_k}/{sell_k} • Call Debit Spread • Buy 1 • Net ~${int(abs(cash_flow))}"
        }
    }

    # Example iron condor (credit)
    ic_put_short, ic_put_long = 120, 115
    ic_call_short, ic_call_long = 135, 140
    ic_mid = 1.10
    ic_cash = ic_mid*100
    ic_width = min(ic_put_short-ic_put_long, ic_call_long-ic_call_short)
    ic_rows = [
        f",{symbol},OPT,P,{ic_put_short},{ib_exp},SELL,1,LMT,{ic_mid:.2f},DAY,SMART,USD",
        f",{symbol},OPT,P,{ic_put_long},{ib_exp},BUY,1,LMT,{ic_mid:.2f},DAY,SMART,USD",
        f",{symbol},OPT,C,{ic_call_short},{ib_exp},SELL,1,LMT,{ic_mid:.2f},DAY,SMART,USD",
        f",{symbol},OPT,C,{ic_call_long},{ib_exp},BUY,1,LMT,{ic_mid:.2f},DAY,SMART,USD",
    ]
    strat_ic = {
        "id": "strat_02",
        "type": "iron_condor",
        "label": "Balanced Iron Condor",
        "quality_score": 0.69,
        "legs": [
            {"kind":"option","side":"SELL","right":"P","exp":exp_iso,"strike":ic_put_short,"qty":1,"multiplier":100},
            {"kind":"option","side":"BUY","right":"P","exp":exp_iso,"strike":ic_put_long,"qty":1,"multiplier":100},
            {"kind":"option","side":"SELL","right":"C","exp":exp_iso,"strike":ic_call_short,"qty":1,"multiplier":100},
            {"kind":"option","side":"BUY","right":"C","exp":exp_iso,"strike":ic_call_long,"qty":1,"multiplier":100}
        ],
        "pricing": {"mid": ic_mid, "nat": round(ic_mid-0.05,2), "cash_flow": ic_cash, "mark_perc_width": round((ic_mid/ic_width)*100,1)},
        "greeks": {"delta": 0.01, "gamma": 0.00, "theta": 5.9, "vega": -3.1, "rho": 0.1},
        "iv_context": {"iv_rank": iv_rank, "skew": (payload.get('market') or {}).get('skew', 0.0)},
        "risk": {"max_profit": ic_cash, "max_loss": ic_width*100 - ic_cash, "break_evens": [ic_put_short - ic_mid, ic_call_short + ic_mid], "pop": 0.62, "p50": 55.0, "margin_requirement": ic_width*100 - ic_cash, "buying_power_effect": ic_width*100 - ic_cash},
        "payoff": {"grid_start": int(under*0.85), "grid_end": int(under*1.2), "grid_step": 1, "points": [{"underlier": int(under), "pnl": round(ic_cash/2,2)}]},
        "order_tickets": {
            "ibkr_basket_csv": _ibkr_csv(ic_rows),
            "thinkorswim_clipboard": f"SELL -1 IRON CONDOR {symbol} {tos_exp} {ic_put_long}/{ic_put_short}/{ic_call_short}/{ic_call_long} @ CREDIT ~{ic_mid:.2f}",
            "tasty_clipboard": f"SELL 1 {symbol} {exp_iso} {ic_put_short}/{ic_put_long}/{ic_call_short}/{ic_call_long} IRON CONDOR CREDIT ~{ic_mid:.2f}",
            "robinhood_clipboard": f"{symbol} • {exp_iso} • {ic_put_short}/{ic_put_long}/{ic_call_short}/{ic_call_long} • Iron Condor • Sell 1 • Credit ~${int(ic_cash)}"
        }
    }

    return jsonify({
        "symbol": symbol,
        "asof": datetime.now(timezone.utc).isoformat(),
        "underlying_price": under,
        "assumptions": {"horizon_days": int((payload.get('bias_window') or {}).get('horizon_days') or 10), "move_window_pct": [-5,0,5,10], "fees_per_contract": 0.65, "slippage_bps": 10},
        "strategies": [strat_vertical, strat_ic]
    })

# AI Explain
ai_bp = Blueprint('ai', __name__, url_prefix='/api/ai')

@ai_bp.route('/explain_chart', methods=['POST'])
def explain_chart():
    # Guest demo gating: allow one explain per session
    if not current_user.is_authenticated:
        if session.get('demo_explain_done'):
            return jsonify({"error":"Login required to run more explains"}), 401
        session['demo_explain_done'] = True

    j = request.get_json(force=True) or {}
    symbol = (j.get('symbol') or 'NVDA').upper()
    tfs = j.get('timeframes') or ['1h','15m','1d']
    risk_pct = (j.get('user_risk_prefs') or {}).get('risk_per_trade_pct', 1.0)

    def fetch(tf, lookback_days=120):
        params = {'symbol': symbol, 'tf': tf}
        if tf == '1d':
            params['start'] = (dt.date.today()-dt.timedelta(days=lookback_days)).isoformat()
        r = requests.get(url_for('api_candles', _external=True), params=params, timeout=12)
        r.raise_for_status()
        return r.json()

    def atr(vals, period=14):
        t,o,h,l,c = vals.get('t',[]), vals.get('o',[]), vals.get('h',[]), vals.get('l',[]), vals.get('c',[])
        if len(c) < period+1: return None
        trs=[]; prev_close = c[0]
        for i in range(1,len(c)):
            tr = max(h[i]-l[i], abs(h[i]-prev_close), abs(l[i]-prev_close))
            trs.append(tr); prev_close = c[i]
        if len(trs) < period: return None
        return sum(trs[-period:])/period

    out = {"symbol": symbol, "timeframes": [], "draft_id": hashlib.sha1(f"{symbol}|{time.time()}".encode()).hexdigest()[:12]}
    for tf in tfs:
        try:
            data = fetch(tf)
        except Exception:
            continue
        if not data or not data.get('c'):
            continue

        last = data['c'][-1]
        _atr = atr(data, 14) or 0.0
        em_mult = 1.0 if tf in ('1m','5m','15m') else (1.5 if tf in ('1h','4h') else 1.8)
        expected_move = {"center": last, "upper": round(last + _atr*em_mult,2), "lower": round(last - _atr*em_mult,2), "atr": round(_atr,2), "atr_mult": em_mult}

        recent = max(50, min(300, len(data['c'])))
        window_c = data['c'][-recent:]
        sr_high = max(window_c); sr_low = min(window_c); mid = (sr_high+sr_low)/2.0

        step = max(0.25, round(_atr*0.25,2))
        bull = {
            "direction":"bull",
            "probability": 0.55,
            "playbook":"Breakout Pullback" if last < sr_high else "EM Reclaim",
            "trigger": f"Reclaim {round(mid,2)} and hold 2 bars",
            "entry_zone": [round(mid-0.25*step,2), round(mid+0.25*step,2)],
            "invalidation": round(max(sr_low, mid - 2*_atr),2),
            "targets": [round(mid+1*step,2), round(mid+2*step,2), round(min(sr_high, mid+3*step),2)],
            "r_r": round((min(sr_high, mid+2*step)-mid) / max(0.01, mid - max(sr_low, mid-2*_atr)),2),
            "notes": "Use pullback to mid for risk-defined entry; avoid entries into EM upper band."
        }
        bear = {
            "direction":"bear",
            "probability": 0.45,
            "playbook":"VWAP Reject" if tf in ('1m','5m','15m') else "Lower High Fade",
            "trigger": f"Lose {round(mid,2)} and reject retest",
            "entry_zone": [round(mid-0.25*step,2), round(mid,2)],
            "invalidation": round(min(sr_high, mid + 2*_atr),2),
            "targets": [round(mid-1*step,2), round(mid-2*step,2), round(max(sr_low, mid-3*step),2)],
            "r_r": round((mid - max(sr_low, mid-2*step)) / max(0.01, min(sr_high, mid+2*_atr)-mid),2),
            "notes": "If EM band breaks, stand down; wait for lower high under mid."
        }

        out["timeframes"].append({
            "tf": tf,
            "levels": {"recent_high": round(sr_high,2), "recent_low": round(sr_low,2), "mid": round(mid,2)},
            "expected_move": expected_move,
            "scenarios": [bull, bear]
        })

    out["playbook_tags"] = list({s["playbook"] for tfb in out["timeframes"] for s in tfb["scenarios"]})[:5]
    out["sizing"] = {"risk_pct": risk_pct}
    return jsonify(out)

# IMPROVED: Beginner-friendly chart explanation endpoint
@ai_bp.route('/explain_chart_simple', methods=['POST'])
def explain_chart_simple():
    """
    Simplified, beginner-friendly chart explanation
    Returns plain English analysis without jargon
    """
    data = request.get_json() or {}
    symbol = data.get('symbol', 'UNKNOWN')
    timeframe = data.get('timeframe', '1d')
    prices = data.get('prices', {})
    candles = data.get('candles', {})
    
    current_price = prices.get('current', 0)
    high_price = prices.get('high', 0)
    low_price = prices.get('low', 0)
    
    # Calculate simple trend (based on last 20 candles)
    closes = candles.get('c', [])
    if len(closes) < 10:
        return jsonify({"error": "Not enough data"}), 400
    
    # Simple trend detection
    recent_closes = closes[-20:] if len(closes) >= 20 else closes
    first_avg = sum(recent_closes[:len(recent_closes)//2]) / (len(recent_closes)//2)
    second_avg = sum(recent_closes[len(recent_closes)//2:]) / (len(recent_closes) - len(recent_closes)//2)
    
    trend_direction = 'up' if second_avg > first_avg * 1.02 else ('down' if second_avg < first_avg * 0.98 else 'sideways')
    
    # Calculate support and resistance (simplified)
    lows = candles.get('l', [])
    highs = candles.get('h', [])
    
    # Find recent significant lows for support
    recent_lows = sorted(lows[-30:])[:5] if len(lows) >= 30 else sorted(lows)[:3]
    support_levels = list(set([round(low, 2) for low in recent_lows]))[:2]
    
    # Find recent significant highs for resistance
    recent_highs = sorted(highs[-30:], reverse=True)[:5] if len(highs) >= 30 else sorted(highs, reverse=True)[:3]
    resistance_levels = list(set([round(high, 2) for high in recent_highs]))[:2]
    
    # Generate beginner-friendly summary
    if trend_direction == 'up':
        summary = f"{symbol} is currently in an upward trend, meaning the price has been generally rising. "
        summary += f"The stock is currently at ${current_price:.2f}. "
        summary += "This upward movement suggests buyers are in control and pushing prices higher."
    elif trend_direction == 'down':
        summary = f"{symbol} is currently in a downward trend, meaning the price has been generally falling. "
        summary += f"The stock is currently at ${current_price:.2f}. "
        summary += "This downward movement suggests sellers are in control and pushing prices lower."
    else:
        summary = f"{symbol} is currently moving sideways (also called 'consolidating'), meaning it's not going up or down strongly. "
        summary += f"The stock is at ${current_price:.2f} and trading in a range. "
        summary += "This often happens when buyers and sellers are equally matched."
    
    # Create simple scenarios with FIXED emojis
    scenarios = []
    
    # Bullish scenario
    if resistance_levels:
        target = resistance_levels[0]
        scenarios.append({
            "type": "bullish",
            "title": "📈 Upward Move Scenario",
            "description": f"If {symbol} can break above ${target:.2f}, it could continue moving higher. This would show that buyers are strong enough to push through this resistance level.",
            "trigger": f"Price breaking and staying above ${target:.2f}"
        })
    
    # Bearish scenario
    if support_levels:
        target = support_levels[0]
        scenarios.append({
            "type": "bearish",
            "title": "📉 Downward Move Scenario",
            "description": f"If {symbol} falls below ${target:.2f}, it could continue moving lower. This would show that sellers are strong enough to push through this support level.",
            "trigger": f"Price breaking and staying below ${target:.2f}"
        })
    
    # Build response
    response = {
        "symbol": symbol,
        "timeframe": timeframe,
        "trend": {
            "direction": trend_direction,
            "description": "Upward" if trend_direction == 'up' else ("Downward" if trend_direction == 'down' else "Sideways")
        },
        "current_price": round(current_price, 2),
        "support_levels": sorted(support_levels),
        "resistance_levels": sorted(resistance_levels, reverse=True),
        "scenarios": scenarios,
        "summary": summary
    }
    
    return jsonify(response)


# IMPROVED: Simplified support/resistance detection
@ai_bp.route('/sr-levels-simple', methods=['POST'])
def sr_levels_simple():
    """
    Simplified support/resistance level detection
    Returns only the 2-3 most important levels
    """
    data = request.get_json() or {}
    highs = data.get('h', [])
    lows = data.get('l', [])
    closes = data.get('c', [])
    
    if not highs or not lows or len(closes) < 20:
        return jsonify({"levels": []})
    
    current_price = closes[-1]
    
    # Simple level detection using recent price action
    lookback = min(50, len(closes))
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    
    # Find peaks (resistance) and troughs (support)
    levels = []
    
    # Method: Find local maxima and minima
    window = 5
    for i in range(window, len(recent_highs) - window):
        # Check if this is a local high
        if recent_highs[i] == max(recent_highs[i-window:i+window+1]):
            levels.append({
                'price': round(recent_highs[i], 2),
                'type': 'resistance',
                'strength': 1
            })
        
        # Check if this is a local low
        if recent_lows[i] == min(recent_lows[i-window:i+window+1]):
            levels.append({
                'price': round(recent_lows[i], 2),
                'type': 'support',
                'strength': 1
            })
    
    # Remove duplicates (levels within 0.5% of each other)
    unique_levels = []
    for level in levels:
        is_duplicate = False
        for existing in unique_levels:
            if abs(level['price'] - existing['price']) / existing['price'] < 0.005:
                is_duplicate = True
                existing['strength'] += 1
                break
        if not is_duplicate:
            unique_levels.append(level)
    
    # Sort by strength and take top 3
    unique_levels.sort(key=lambda x: x['strength'], reverse=True)
    top_levels = unique_levels[:3]
    
    # Ensure we have at least one support below current price and one resistance above
    supports = [l for l in top_levels if l['price'] < current_price]
    resistances = [l for l in top_levels if l['price'] > current_price]
    
    final_levels = []
    if supports:
        final_levels.append(max(supports, key=lambda x: x['price']))
    if resistances:
        final_levels.append(min(resistances, key=lambda x: x['price']))
    
    # If we still don't have enough levels, add simple ones
    if len(final_levels) < 2:
        if not supports:
            final_levels.append({
                'price': round(min(recent_lows), 2),
                'type': 'support',
                'strength': 1
            })
        if not resistances:
            final_levels.append({
                'price': round(max(recent_highs), 2),
                'type': 'resistance',
                'strength': 1
            })
    
    return jsonify({"levels": final_levels})


# ENHANCED VERSION: More detailed explanations with key levels
@ai_bp.route('/explain_chart_detailed', methods=['POST'])
def explain_chart_detailed():
    """
    Detailed chart explanation with technical insights
    Provides more depth while still being accessible
    """
    data = request.get_json() or {}
    symbol = data.get('symbol', 'UNKNOWN')
    timeframe = data.get('timeframe', '1d')
    prices = data.get('prices', {})
    candles = data.get('candles', {})
    
    current_price = prices.get('current', 0)
    high_price = prices.get('high', 0)
    low_price = prices.get('low', 0)
    
    closes = candles.get('c', [])
    opens = candles.get('o', [])
    highs = candles.get('h', [])
    lows = candles.get('l', [])
    volumes = candles.get('v', [])
    
    if len(closes) < 20:
        return jsonify({"error": "Not enough data"}), 400
    
    # Calculate key metrics
    recent_closes = closes[-20:]
    price_change = ((recent_closes[-1] - recent_closes[0]) / recent_closes[0]) * 100
    
    # Volatility (simple ATR approximation)
    recent_ranges = [highs[i] - lows[i] for i in range(max(0, len(highs) - 14), len(highs))]
    avg_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0
    volatility_pct = (avg_range / current_price) * 100 if current_price > 0 else 0
    
    # Volume analysis
    avg_volume = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    recent_volume = volumes[-1] if volumes else 0
    volume_ratio = (recent_volume / avg_volume) if avg_volume > 0 else 1.0
    
    # Trend strength
    trend_strength = abs(price_change)
    if trend_strength < 2:
        strength_desc = "weak"
    elif trend_strength < 5:
        strength_desc = "moderate"
    else:
        strength_desc = "strong"
    
    # Build detailed summary
    direction = "upward" if price_change > 0 else "downward"
    summary = f"{symbol} is showing a {strength_desc} {direction} trend over the recent period. "
    summary += f"The price has moved {abs(price_change):.1f}% "
    summary += f"{'up' if price_change > 0 else 'down'} to ${current_price:.2f}. "
    
    if volume_ratio > 1.5:
        summary += "Trading volume is significantly higher than average, suggesting strong interest. "
    elif volume_ratio < 0.7:
        summary += "Trading volume is lower than average, suggesting reduced interest. "
    
    summary += f"The stock's average daily range is about {volatility_pct:.1f}% of its price. "
    
    # Key levels
    support_levels = sorted(list(set([round(l, 2) for l in sorted(lows[-30:])[:3]])))
    resistance_levels = sorted(list(set([round(h, 2) for h in sorted(highs[-30:], reverse=True)[:3]])), reverse=True)
    
    response = {
        "symbol": symbol,
        "timeframe": timeframe,
        "current_price": round(current_price, 2),
        "trend": {
            "direction": "up" if price_change > 0 else "down",
            "strength": strength_desc,
            "change_pct": round(price_change, 2)
        },
        "volatility": {
            "avg_range": round(avg_range, 2),
            "pct_of_price": round(volatility_pct, 2)
        },
        "volume": {
            "current": int(recent_volume),
            "average": int(avg_volume),
            "ratio": round(volume_ratio, 2),
            "description": "High" if volume_ratio > 1.5 else ("Low" if volume_ratio < 0.7 else "Normal")
        },
        "support_levels": support_levels[:2],
        "resistance_levels": resistance_levels[:2],
        "summary": summary,
        "key_insights": [
            f"💡 Price is currently ${current_price:.2f}",
            f"📊 {abs(price_change):.1f}% {direction} move recently",
            f"📈 Volume is {volume_ratio:.0%} of average" if volume_ratio > 0 else "No volume data",
            f"🎯 Key support near ${support_levels[0]:.2f}" if support_levels else "No clear support identified",
            f"🚧 Key resistance near ${resistance_levels[0]:.2f}" if resistance_levels else "No clear resistance identified"
        ]
    }
    
    return jsonify(response)

# Plans
plans_bp = Blueprint('plans', __name__, url_prefix='/api')

@plans_bp.route('/plans', methods=['POST'])
@login_required
def create_plan():
    from models import db as _db, Plan, PlanAlert
    j = request.get_json(force=True) or {}
    draft_payload = j.get('draft_payload')  # optional: full explain payload
    symbol = (j.get('symbol') or draft_payload and draft_payload.get('symbol') or '').upper()
    scenario_direction = j.get('scenario_direction')
    invalidation = j.get('invalidation')
    targets = j.get('targets') or []
    sizing = j.get('sizing') or {}
    playbook_tags = j.get('playbook_tags') or []
    rr_expected = j.get('rr_expected')
    if not symbol:
        return jsonify({"error":"symbol required"}), 400
    plan = Plan(
        user_id=current_user.id,
        symbol=symbol,
        scenario_direction=scenario_direction,
        invalidation=invalidation,
        targets=targets,
        playbook_tags=playbook_tags,
        rr_expected=rr_expected,
        sizing=sizing,
        draft_payload=draft_payload,
    )
    _db.session.add(plan)
    _db.session.flush()
    alerts = j.get('alerts') or []
    for a in alerts:
        _db.session.add(PlanAlert(plan_id=plan.id, type=a, channel='email', status='active'))
    _db.session.commit()
    return jsonify({"plan_id": plan.id})

# Usage counters
usage_bp = Blueprint('usage', __name__, url_prefix='/api/usage')

def _week_start(dt_utc):
    # Monday-based week
    return (dt_utc - timedelta(days=dt_utc.weekday())).date()

@usage_bp.route('/explain_chart', methods=['GET'])
def usage_explain_chart():
    if not current_user.is_authenticated:
        return jsonify({"weekly_count": 0, "weekly_limit": 1, "reset_at": None})
    from models import UsageCounter
    now = datetime.now(timezone.utc)
    wk = _week_start(now)
    uc = UsageCounter.query.filter_by(user_id=current_user.id, key='explain_chart', week_start=wk).first()
    count = uc.count if uc else 0
    limit = 999999 if current_user.has_pro_access() else 3
    reset_at = datetime.combine(wk + timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc).isoformat()
    return jsonify({"weekly_count": count, "weekly_limit": limit, "reset_at": reset_at})

# Imports (IBKR Flex CSV first)
imports_bp = Blueprint('imports', __name__, url_prefix='/api/imports')

def _parse_ibkr_flex_csv(raw_bytes):
    text = raw_bytes.decode('utf-8', errors='ignore')
    reader = csv.DictReader(_io.StringIO(text))
    out=[]
    for r in reader:
        cls = (r.get('AssetClass') or '').upper()
        if cls not in ('STK','OPT'):
            continue
        trade_date = (r.get('TradeDate') or '').strip()
        trade_time = (r.get('TradeTime') or '00:00:00').strip()
        ts = datetime.strptime(f"{trade_date} {trade_time}", '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).isoformat()
        side = 'BUY' if (r.get('Buy/Sell') or '').upper().startswith('B') else 'SELL'
        base = {
            "timestamp": ts,
            "side": side,
            "quantity": float(r.get('Quantity') or 0),
            "price": float(r.get('TradePrice') or 0),
            "commission": abs(float(r.get('IBCommission') or 0)),
            "fees": abs(float(r.get('ExchangeFees') or 0)),
            "currency": r.get('Currency') or 'USD',
            "broker_trade_id": (r.get('TradeID') or '').strip(),
            "raw": r
        }
        if cls == 'STK':
            base.update({"asset_type":"equity", "symbol": (r.get('Symbol') or r.get('UnderlyingSymbol') or '').upper()})
        else:
            base.update({
                "asset_type":"option",
                "symbol": (r.get('UnderlyingSymbol') or r.get('Symbol') or '').upper(),
                "right": (r.get('Right') or '').strip().upper(),
                "strike": float(r.get('Strike') or 0),
                "expiration": (r.get('Expiry') or '').strip(),
                "multiplier": int(r.get('Multiplier') or 100),
                "occ": (r.get('ConidDescription') or '').strip()
            })
        out.append(base)
    return out

def _normalize_exec(broker, account, r):
    ts = r["timestamp"]
    symbol = (r["symbol"] or '').upper()
    base = {
        "broker": broker,
        "broker_trade_id": r.get("broker_trade_id",""),
        "account_id": account,
        "timestamp_utc": ts,
        "symbol": symbol,
        "asset_type": r["asset_type"],
        "side": r["side"],
        "quantity": abs(float(r["quantity"])),
        "price": float(r["price"]),
        "commission": float(r.get("commission",0)),
        "fees": float(r.get("fees",0)),
        "currency": r.get("currency","USD"),
        "raw_description": str(r.get("raw",""))[:2000]
    }
    if r["asset_type"]=="option":
        base.update({"right": r.get("right"), "strike": float(r.get("strike") or 0), "expiration": r.get("expiration"), "multiplier": int(r.get("multiplier",100)), "occ": r.get("occ")})
    bid = (base.get("broker_trade_id") or '').strip()
    if bid:
        execution_id = hashlib.sha1(f"{broker}|{bid}".encode()).hexdigest()
    else:
        fp = f"{broker}|{account}|{ts}|{symbol}|{base.get('right','')}|{base.get('strike','')}|{base.get('expiration','')}|{base['side']}|{base['quantity']}|{base['price']}"
        execution_id = hashlib.sha1(fp.encode()).hexdigest()
    base["execution_id"] = execution_id
    return base

@imports_bp.route('/upload', methods=['POST'])
@login_required
def imports_upload():
    broker = (request.form.get('broker') or 'IBKR').upper()
    account = request.form.get('account_alias','Account-1')
    files = request.files.getlist('files')
    if not files:
        return jsonify({"error":"no files"}), 400
    raw_rows=[]
    for f in files:
        content = f.read()
        if broker=='IBKR':
            raw_rows += _parse_ibkr_flex_csv(content)
        else:
            return jsonify({"error": f"broker {broker} parser not implemented"}), 400
    normalized = [_normalize_exec(broker, account, r) for r in raw_rows]
    uniq = {r["execution_id"]: r for r in normalized}
    preview_id = hashlib.sha1(f"{current_user.id}|{time.time()}".encode()).hexdigest()[:12]
    # store in session for preview; in prod use DB/Redis
    session[f"import_preview_{preview_id}"] = {"broker": broker, "account": account, "rows": list(uniq.values())}
    stats = {"rows": len(uniq), "symbols": len({r['symbol'] for r in uniq.values()})}
    return jsonify({"import_id": preview_id, "stats": stats, "warnings": []})

@imports_bp.route('/preview/<import_id>', methods=['GET'])
@login_required
def imports_preview(import_id):
    data = session.get(f"import_preview_{import_id}")
    if not data:
        return jsonify({"error":"not found"}), 404
    sample = data["rows"][:50]
    return jsonify({"import_id": import_id, "stats": {"rows": len(data["rows"])}, "sample": sample})

@imports_bp.route('/commit', methods=['POST'])
@login_required
def imports_commit():
    from models import db as _db, Execution
    payload = request.get_json(force=True) or {}
    import_id = payload.get('import_id')
    data = session.get(f"import_preview_{import_id}")
    if not data:
        return jsonify({"error":"not found"}), 404
    rows = data["rows"]
    kept, skipped = 0, 0
    for r in rows:
        if Execution.query.get(r["execution_id"]):
            skipped += 1
            continue
        ex = Execution(
            execution_id=r["execution_id"], user_id=current_user.id, broker=data["broker"], broker_trade_id=r.get("broker_trade_id"), account_id=data["account"],
            timestamp_utc=datetime.fromisoformat(r["timestamp_utc"].replace('Z','+00:00')), symbol=r["symbol"], asset_type=r["asset_type"], side=r["side"], quantity=r["quantity"], price=r["price"], commission=r.get("commission",0), fees=r.get("fees",0), currency=r.get("currency","USD"), right=r.get("right"), strike=r.get("strike"), expiration=(datetime.strptime(r["expiration"], "%Y-%m-%d").date() if r.get("expiration") else None), multiplier=r.get("multiplier"), occ=r.get("occ")
        )
        _db.session.add(ex)
        kept += 1
    _db.session.commit()
    session.pop(f"import_preview_{import_id}", None)
    return jsonify({"import_id": import_id, "kept": kept, "skipped_duplicates": skipped, "total_seen": len(rows)})

# Options payoff
@options_bp.route('/payoff', methods=['POST'])
def options_payoff():
    j = request.get_json(force=True) or {}
    legs = j.get('legs') or []
    grid_start = float(j.get('grid_start') or 50)
    grid_end = float(j.get('grid_end') or 150)
    grid_step = float(j.get('grid_step') or 1)
    points = []
    s = grid_start
    def leg_value(leg, S):
        right = (leg.get('right') or '').upper()
        K = float(leg.get('strike'))
        qty = float(leg.get('qty') or leg.get('quantity') or 1)
        side = (leg.get('side') or 'BUY').upper()
        mult = int(leg.get('multiplier') or 100)
        price = float(leg.get('price') or 0)
        intrinsic = max(0.0, S-K) if right=='C' else max(0.0, K-S)
        val = intrinsic * mult
        # PnL at expiry w.r.t entry price (if provided)
        pnl = (val - price*mult) if side=='BUY' else ((price*mult) - val)
        return pnl * qty
    while s <= grid_end + 1e-9:
        pnl = sum(leg_value(l, s) for l in legs)
        points.append({"underlier": round(s,4), "pnl": round(pnl,2)})
        s += grid_step
    return jsonify({"grid_start": grid_start, "grid_end": grid_end, "grid_step": grid_step, "points": points})

# Market Brief status/admin
brief_bp = Blueprint('brief', __name__, url_prefix='/api/brief')

@brief_bp.route('/status', methods=['GET'])
def brief_status():
    from models import MarketBrief
    last = MarketBrief.query.order_by(MarketBrief.created_at.desc()).first()
    ts = last.created_at.isoformat() if last else None
    stale = None
    if last:
        age = datetime.utcnow() - last.created_at
        stale = age.total_seconds()
    return jsonify({"last_success": ts, "stale_seconds": stale})

@brief_bp.route('/admin_generate', methods=['POST'])
@login_required
def brief_admin_generate():
    admin_email = current_app.config.get('ADMIN_EMAIL')
    if not admin_email or current_user.email != admin_email:
        return jsonify({"error":"forbidden"}), 403
    try:
        # If you have a function to generate the brief, call it here
        # send_weekly_market_brief_to_subscribers()
        return jsonify({"status":"queued"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
# Blueprints will be registered after app initialization below

# Allow OAuth over HTTP for local development
if os.getenv('FLASK_ENV') == 'development' or os.getenv('OAUTHLIB_INSECURE_TRANSPORT') is None:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
from flask_dance.contrib.google import make_google_blueprint, google

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    load_dotenv()
    print("Environment variables loaded from .env file")
except ImportError:
    print("python-dotenv not installed, using system environment variables")
    pass
except Exception as e:
    print(f"Error loading .env file: {e}, using fallback configuration")
    pass

app = Flask(__name__)
app.config.from_object(Config)

# Override SERVER_NAME for local development
if os.environ.get('FLASK_ENV') != 'production':
    app.config['SERVER_NAME'] = None
else:
    # In production, don't set SERVER_NAME to avoid hostname issues
    app.config['SERVER_NAME'] = None

mail = Mail(app)

# Security helper: Validate redirect URLs to prevent open redirects
def is_safe_url(target):
    """
    Validate that a URL is safe for redirect (prevents open redirect attacks).
    Returns True if the URL is relative or on the same host, False otherwise.
    """
    if not target:
        return False
    
    # Allow relative URLs (starting with /)
    if target.startswith('/'):
        return True
    
    # Parse the target URL
    from urllib.parse import urlparse
    ref_url = urlparse(request.host_url)
    test_url = urlparse(target)
    
    # Allow URLs on the same host
    return test_url.scheme in ('http', 'https') and test_url.netloc == ref_url.netloc

# Initialize extensions
db.init_app(app)
with app.app_context():
    try:
        print("DB Engine URL:", db.engine.url)
    except Exception as e:
        print("DB Engine check error:", str(e))
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# ───────── Google OAuth Setup ─────────
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

# Add to app config for template access
app.config['GOOGLE_OAUTH_CLIENT_ID'] = GOOGLE_OAUTH_CLIENT_ID

print(f"Google OAuth Client ID: {'Set' if GOOGLE_OAUTH_CLIENT_ID else 'Not set'}")
print(f"Google OAuth Client Secret: {'Set' if GOOGLE_OAUTH_CLIENT_SECRET else 'Not set'}")

if GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET:
    google_bp = make_google_blueprint(
        client_id=GOOGLE_OAUTH_CLIENT_ID,
        client_secret=GOOGLE_OAUTH_CLIENT_SECRET,
        scope=["openid", "https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email"],
        redirect_to="google_login"  # Redirect to our custom login handler after OAuth
    )
    app.register_blueprint(google_bp, url_prefix="/login")
    print("Google OAuth blueprint registered successfully")
else:
    print("WARNING: Google OAuth not configured. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET env vars.")

# ───────── Tiingo API Configuration ─────────
TIINGO_KEY = os.environ.get("TIINGO_API_KEY")
TIINGO_HEADERS = {"Authorization": f"Token {TIINGO_KEY}"} if TIINGO_KEY else {}

# UI timeframe -> (route, resample, lookback days)
TF_MAP = {
    "1m": ("iex", "1Min", 30),      # 30 days (Tiingo IEX limit)
    "5m": ("iex", "5Min", 90),      # 90 days (Tiingo IEX limit)
    "15m": ("iex", "15Min", 90),    # 90 days (Tiingo IEX limit) - ADDED
    "30m": ("iex", "30Min", 90),    # 90 days (Tiingo IEX limit) - ADDED
    "1h": ("iex", "1Hour", 730),    # 2 years (Tiingo IEX limit)
    "4h": ("iex", "4Hour", 730),    # 2 years (Tiingo IEX limit)
    "1d": ("daily", "daily", 3650), # 10 years (unchanged)
}

# Simple cache + rate limiter
CACHE = {}  # key -> {"data":..., "ts": epoch, "ttl": seconds}
TTL = {"1m": 30, "5m": 60, "1h": 300, "4h": 600, "1d": 900}
BUCKET = {"tokens": 60, "capacity": 60, "refill_rate": 1.0, "last": time.time()}

# Register billing blueprint
try:
    from billing import bp as billing_bp, requires_pro
    app.register_blueprint(billing_bp)
    print("Billing blueprint registered successfully")
except ImportError as e:
    print(f"WARNING: Billing blueprint not available: {e}")
    # Fallback requires_pro decorator
    def requires_pro(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"error": "Authentication required"}), 401
            if not current_user.has_pro_access():
                flash("Pro subscription required for this feature. Please upgrade to Pro.", "warning")
                return redirect(url_for("pricing"))
            return f(*args, **kwargs)
        return wrapper

# Brief routes are now defined directly in app.py

# Register internal API blueprints (after app init)
app.register_blueprint(options_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(plans_bp)
app.register_blueprint(usage_bp)
app.register_blueprint(imports_bp)
app.register_blueprint(brief_bp)

# Page: Imports UI
@app.route('/imports')
@login_required
def imports_page():
    return render_template('imports.html')

def is_pro_user():
    """Check if current user has Pro access for page-level preview gating"""
    return current_user.is_authenticated and current_user.has_pro_access()

# ───────── Google Login Route ─────────
@app.context_processor
def inject_config():
    """Make config available to templates"""
    # Check if Google OAuth is enabled
    # Check if blueprint is registered by trying to see if 'google' blueprint exists
    google_oauth_enabled = False
    try:
        google_oauth_enabled = bool(
            app.config.get('GOOGLE_OAUTH_CLIENT_ID') and
            'google' in app.blueprints
        )
    except:
        # Fallback to just checking config
        google_oauth_enabled = bool(app.config.get('GOOGLE_OAUTH_CLIENT_ID'))
    
    return dict(
        config=app.config,
        google_oauth_enabled=google_oauth_enabled
    )

@app.route("/google_login")
def google_login():
    try:
        print("=== Google Login Debug ===")
        print(f"Request URL: {request.url}")
        print(f"Request args: {dict(request.args)}")
        print(f"Session data: {dict(session)}")
        
        if not google.authorized:
            print("Google not authorized, redirecting to login")
            return redirect(url_for("google.login"))

        print("Google is authorized, fetching user info...")
        resp = google.get("/oauth2/v2/userinfo")
        if not resp.ok:
            print(f"Failed to fetch user info: {resp.status_code}")
            flash("Failed to fetch user info from Google.", "danger")
            return redirect(url_for("login"))

        info = resp.json()
        email = info.get("email")
        username = email.split("@")[0] if email else None
        
        print(f"Google user info - Email: {email}, Username: {username}")

        if not email:
            print("No email found in Google account")
            flash("Google account has no email address", "danger")
            return redirect(url_for("login"))

        print("Checking if user exists in database...")
        user = User.query.filter_by(email=email).first()
        
        if not user:
            print(f"User not found, creating new user with email: {email}")
            
            # Generate a unique username
            base_username = username
            counter = 1
            while User.query.filter_by(username=username).first():
                print(f"Username {username} already exists, trying alternative")
                username = f"{base_username}_{counter}"
                counter += 1
                if counter > 100:  # Prevent infinite loop
                    username = f"{base_username}_{secrets.token_urlsafe(8)}"
                    break
            
            # Auto-create user account with better error handling
            try:
                user = User(username=username, email=email)
                # Set a random password
                user.set_password(secrets.token_urlsafe(16))
                
                print(f"Adding user to database: {username}")
                db.session.add(user)
                db.session.commit()
                print("User created successfully")
                
            except Exception as e:
                db.session.rollback()
                print(f"Database error creating user: {str(e)}")
                print(f"Full error details: {type(e).__name__}: {e}")
                
                # More specific error messages
                if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
                    flash("An account with this email already exists. Please try logging in instead.", "warning")
                elif "not null" in str(e).lower():
                    flash("Missing required information. Please try again.", "danger")
                else:
                    flash("Error creating user account. Please try again or contact support.", "danger")
                
                return redirect(url_for("login"))
        else:
            print(f"User found: {user.username}")

        print("Logging in user...")
        login_user(user)
        print("User logged in successfully")
        flash("Logged in successfully via Google", "success")
        
        # Detect mobile device and ensure proper mobile experience
        user_agent = request.headers.get('User-Agent', '').lower()
        is_mobile = any(device in user_agent for device in ['mobile', 'android', 'iphone', 'ipad', 'ipod'])
        
        # Also check for mobile viewport width in request headers
        viewport_width = request.headers.get('X-Viewport-Width')
        if viewport_width and int(viewport_width) <= 768:
            is_mobile = True
        
        # Additional mobile detection
        if request.args.get('mobile') == '1' or request.args.get('force_mobile') == '1':
            is_mobile = True
        
        if is_mobile:
            print("Mobile device detected, ensuring mobile layout")
            # Store mobile preference in session
            session['mobile_preference'] = True
            
            # For mobile, redirect to a simpler page first to avoid OAuth redirect issues
            try:
                return redirect(url_for("dashboard", mobile=1, force_mobile=1))
            except Exception as redirect_error:
                print(f"Redirect error on mobile: {redirect_error}")
                # Fallback to index page
                return redirect(url_for("index", mobile=1))
        else:
            return redirect(url_for("dashboard"))
        
    except Exception as e:
        # Log the error for debugging
        print(f"Google login error: {str(e)}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        flash("An error occurred during Google login. Please try again.", "danger")
        return redirect(url_for("login"))


@login_manager.user_loader
def load_user(id):
    return User.query.get(int(id))

# Debug route for Google OAuth
@app.route("/debug/google-oauth")
def debug_google_oauth():
    """Debug route to check Google OAuth configuration"""
    debug_info = {
        "google_oauth_client_id": "Set" if GOOGLE_OAUTH_CLIENT_ID else "Not set",
        "google_oauth_client_secret": "Set" if GOOGLE_OAUTH_CLIENT_SECRET else "Not set",
        "google_authorized": google.authorized if 'google' in globals() else "Google not initialized",
        "expected_redirect_uri": "https://optionsplunge.com/login/google/authorized",
        "session_secret_key": "Set" if app.config.get('SECRET_KEY') else "Not set",
        "session_config": {
            "session_type": type(session).__name__,
            "session_available": hasattr(session, 'get')
        }
    }
    return jsonify(debug_info)

# Debug route for OAuth callback
@app.route("/debug/oauth-callback")
def debug_oauth_callback():
    """Debug route to check OAuth callback state"""
    from flask import request
    debug_info = {
        "request_args": dict(request.args),
        "request_url": request.url,
        "google_authorized": google.authorized if 'google' in globals() else "Google not initialized",
        "session_data": dict(session) if hasattr(session, 'get') else "No session"
    }
    return jsonify(debug_info)


# Initialize AI analyzer
ai_analyzer = TradingAIAnalyzer()

# Initialize Tradier API configuration
TRADIER_API_TOKEN = os.getenv("TRADIER_API_TOKEN")
TRADIER_API_BASE_URL = "https://api.tradier.com/v1"  # Production environment
print(f"Tradier API Base URL: {TRADIER_API_BASE_URL}")
print(f"Tradier token configured: {'Yes' if TRADIER_API_TOKEN else 'No'}")

if not TRADIER_API_TOKEN:
    print("Warning: TRADIER_API_TOKEN environment variable not set")


# ──────────────────────────────────────────────────
# MARKET BRIEF ROUTE
# ──────────────────────────────────────────────────


@app.route("/market_brief", methods=["GET", "POST"])
def market_brief():
    """Landing page for the free morning market brief"""
    form = MarketBriefSignupForm()
    subscribed = False
    # If logged in, determine if the user is already a confirmed subscriber
    try:
        from models import MarketBriefSubscriber as _MBSub
        if current_user.is_authenticated:
            _sub = _MBSub.query.filter_by(email=current_user.email).first()
            subscribed = bool(_sub and _sub.confirmed)
    except Exception:
        pass
    
    # Check for preview mode query param
    preview_mode = request.args.get('preview') == '1'
    
    # Determine if user should see preview or full access (ignore preview for Pro users)
    show_preview = not is_pro_user()

    if form.validate_on_submit():
        name = form.name.data.strip()
        email = form.email.data.strip().lower()
        
        # Check if already subscribed
        existing_subscriber = MarketBriefSubscriber.query.filter_by(email=email).first()
        
        if existing_subscriber:
            if existing_subscriber.confirmed:
                flash('You\'re already subscribed and confirmed! Check your inbox for the latest brief.', 'info')
            else:
                # Resend confirmation email (prefer Flask-aware sender)
                from emails import send_confirmation_email
                if send_confirmation_email(existing_subscriber):
                    flash('Confirmation email resent! Please check your inbox and click the confirmation link.', 'info')
                else:
                    flash('Error sending confirmation email. Please try again or contact support.', 'danger')
        else:
            # Create new subscriber
            subscriber = MarketBriefSubscriber(
                name=name, 
                email=email,
                confirmed=False
            )
            db.session.add(subscriber)
            db.session.commit()

            # Send confirmation email (prefer Flask-aware sender)
            from emails import send_confirmation_email, send_admin_notification
            if send_confirmation_email(subscriber):
                send_admin_notification(subscriber)  # Notify admin
                flash('Check your email to confirm your subscription!', 'success')
            else:
                flash('Error sending confirmation email. Please try again.', 'danger')

    # Demo: serve structured brief for layout testing
    if request.args.get("demo") == "1":
        brief = {
            "date_str": datetime.now().strftime("%A, %B %d, %Y"),
            "executive_summary": "Market conditions appear stable with ranges tightening into key data.",
            "headlines": [
                {"title": "Federal Reserve signals potential rate adjustments",
                 "source": "Market News",
                 "summary": "Fed officials discuss the economic outlook and possible policy direction into year-end."},
                {"title": "Market volatility increases ahead of key data",
                 "source": "Economic Data",
                 "summary": "Traders prepare for CPI and jobs data; intraday ranges may expand into the print."}
            ],
            "technical_analysis": "SPY consolidating below resistance; watch the opening drive against overnight highs/lows for continuation or fade setups.",
            "sentiment_outlook": "Risk sentiment remains balanced; breadth neutral, options positioning modestly defensive.",
            "key_levels": {
                "SPY": {"last": "647.24", "S": ["631.06","642.98","638.71"], "R": ["663.42","651.86","656.47"], "weekly_S": ["641.25","637.46"], "weekly_R": ["649.16","653.28"]},
                "QQQ": {"last": "576.06", "S": ["558.78","571.35","566.65"], "R": ["593.34","580.94","585.83"], "weekly_S": ["566.63","562.85"], "weekly_R": ["576.09","581.77"]},
                "VIX": {"last": "15.38"}
            },
            "gappers_note": "Gapping Stocks"
        }

        latest_daily_brief = None
        latest_weekly_brief = None
        historical_daily_briefs = []
        historical_weekly_briefs = []

        return render_template(
            "market_brief.html",
            form=form,
            subscribed=subscribed,
            latest_daily_brief=latest_daily_brief,
            latest_weekly_brief=latest_weekly_brief,
            historical_daily_briefs=historical_daily_briefs,
            historical_weekly_briefs=historical_weekly_briefs,
            show_pro_upsell=False,
            show_demo_data=True,
            feature_name=None,
            limitations=None,
            brief=brief
        )

    # Load briefs from database
    from models import MarketBrief
    from datetime import date, timedelta
    
    # Get latest daily brief
    latest_daily_brief = MarketBrief.query.filter_by(brief_type='daily').order_by(MarketBrief.date.desc()).first()
    
    # Get latest weekly brief
    latest_weekly_brief = MarketBrief.query.filter_by(brief_type='weekly').order_by(MarketBrief.date.desc()).first()
    
    # Get historical briefs (last 14 days)
    cutoff_date = date.today() - timedelta(days=14)
    
    # Historical daily briefs (Pro users only)
    historical_daily_briefs = []
    if current_user.is_authenticated and current_user.has_pro_access():
        historical_daily_briefs = MarketBrief.query.filter(
            MarketBrief.brief_type == 'daily',
            MarketBrief.date >= cutoff_date
        ).order_by(MarketBrief.date.desc()).limit(10).all()
    
    # Historical weekly briefs (all users)
    historical_weekly_briefs = MarketBrief.query.filter(
        MarketBrief.brief_type == 'weekly',
        MarketBrief.date >= cutoff_date
    ).order_by(MarketBrief.date.desc()).limit(5).all()

    return render_template(
        "market_brief.html", 
        form=form, 
        subscribed=subscribed, 
        latest_daily_brief=latest_daily_brief,
        latest_weekly_brief=latest_weekly_brief,
        historical_daily_briefs=historical_daily_briefs,
        historical_weekly_briefs=historical_weekly_briefs,
        show_pro_upsell=show_preview,
        show_demo_data=show_preview,
        feature_name="Daily Market Brief" if show_preview else None,
        limitations=[
            "Sample brief only - no real-time data",
            "Cannot access full brief archive",
            "No email delivery"
        ] if show_preview else None
    )


@app.route("/brief/<int:brief_id>")
def view_brief(brief_id):
    """View a specific market brief"""
    from models import MarketBrief
    
    brief = MarketBrief.query.get_or_404(brief_id)
    
    # Check access permissions
    if brief.brief_type == 'daily':
        if not current_user.is_authenticated or not current_user.has_pro_access():
            flash('Daily briefs are available to Pro users only.', 'warning')
            return redirect(url_for('market_brief'))
    
    return render_template(
        "view_brief.html",
        brief=brief,
        title=f"{brief.brief_type.title()} Brief - {brief.date.strftime('%B %d, %Y')}"
    )

@app.route("/confirm/<token>")
def confirm_subscription(token):
    """Confirm newsletter subscription with token"""
    try:
        # Validate token format
        if not token or len(token) < 20:
            flash('Invalid confirmation link format.', 'danger')
            return redirect(url_for('market_brief'))
        
        subscriber = MarketBriefSubscriber.query.filter_by(token=token).first()
        
        if not subscriber:
            flash('Invalid or expired confirmation link. Please check your email or request a new confirmation.', 'danger')
            return redirect(url_for('market_brief'))
        
        if subscriber.confirmed:
            # Already confirmed - show success page anyway
            return render_template('brief_confirmed.html', name=subscriber.name)
        else:
            # Confirm the subscription
            subscriber.confirm_subscription()
            db.session.commit()
            
            # Send welcome email (don't let this fail the confirmation)
            try:
                from emails import send_welcome_email
                send_welcome_email(subscriber)
            except Exception as e:
                print(f"Warning: Could not send welcome email to {subscriber.email}: {e}")
            
            # Render success page
            return render_template('brief_confirmed.html', name=subscriber.name)
            
    except Exception as e:
        print(f"Error in confirm_subscription: {e}")
        flash('An error occurred while confirming your subscription. Please try again or contact support.', 'danger')
        return redirect(url_for('market_brief'))

@app.route("/unsubscribe/<email>")
def unsubscribe(email):
    """Unsubscribe from newsletter"""
    subscriber = MarketBriefSubscriber.query.filter_by(email=email).first()
    
    if subscriber:
        subscriber.unsubscribe()
        db.session.commit()
        flash('You have been unsubscribed from the Morning Market Brief.', 'info')
    else:
        flash('Email not found in our subscriber list.', 'warning')
    
    return redirect(url_for('market_brief'))

@app.route("/admin/send_brief", methods=["POST"])
@login_required
def send_brief():
    """Admin route to manually trigger market brief sending"""
    # Add admin check
    if current_user.email != 'support@optionsplunge.com':
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("market_brief"))
    
    try:
        from market_brief_generator import send_market_brief_to_subscribers
        success_count = send_market_brief_to_subscribers()
        flash(f"Market brief sent to {success_count} subscribers!", "success")
    except Exception as e:
        flash(f"Error sending market brief: {str(e)}", "danger")
    
    return redirect(url_for("market_brief"))

@app.route("/admin/morning-brief")
@login_required
def admin_morning_brief():
    """Admin page for morning brief management"""
    # Add admin check
    if current_user.email != 'support@optionsplunge.com':
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("market_brief"))
    
    return render_template("admin/morning_brief.html")

@app.route("/admin/generate/daily-noemail", methods=["POST"]) 
@login_required
def admin_generate_daily_noemail():
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403
    try:
        from market_brief_generator import generate_daily_brief_file_only
        path = generate_daily_brief_file_only()
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/admin/generate/weekly-noemail", methods=["POST"]) 
@login_required
def admin_generate_weekly_noemail():
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403
    try:
        from market_brief_generator import generate_weekly_brief_file_only
        path = generate_weekly_brief_file_only(force=True)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/admin/preview/morning-brief", methods=["POST"])
@login_required
def preview_morning_brief():
    """Preview morning brief with provided data"""
    # Add admin check
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403
    
    try:
        data = request.get_json()
        from daily_brief_schema import MorningBrief
        from emailer import render_morning_brief
        
        # Validate data with Pydantic
        brief = MorningBrief(**data)
        context = brief.model_dump()
        
        # Render templates
        html_content, text_content = render_morning_brief(context)
        
        return jsonify({
            "html": html_content,
            "text": text_content,
            "subject": f"Options Plunge Morning Brief — {brief.subject_theme} ({brief.date})"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/admin/send-test/morning-brief", methods=["POST"])
@login_required
def send_test_morning_brief():
    """Send test morning brief email"""
    # Add admin check
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403
    
    try:
        data = request.get_json()
        from daily_brief_schema import MorningBrief
        from emailer import render_morning_brief, send_morning_brief
        
        # Validate data with Pydantic
        brief = MorningBrief(**data)
        context = brief.model_dump()
        
        # Render templates
        html_content, text_content = render_morning_brief(context)
        subject = f"Options Plunge Morning Brief — {brief.subject_theme} ({brief.date})"
        
        # Get test email from environment
        test_email = os.getenv('TEST_EMAIL')
        if not test_email:
            return jsonify({"error": "TEST_EMAIL environment variable not set"}), 400
        
        # Send test email
        success = send_morning_brief(html_content, text_content, subject, [test_email])
        
        if success:
            return jsonify({"message": f"Test email sent to {test_email}"})
        else:
            return jsonify({"error": "Failed to send test email"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/admin/publish/morning-brief", methods=["POST"])
@login_required
def publish_morning_brief():
    """Publish morning brief to all subscribers"""
    # Add admin check
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403
    
    try:
        data = request.get_json()
        from daily_brief_schema import MorningBrief
        from emailer import render_morning_brief, send_morning_brief
        
        # Validate data with Pydantic
        brief = MorningBrief(**data)
        context = brief.model_dump()
        
        # Render templates
        html_content, text_content = render_morning_brief(context)
        subject = f"Options Plunge Morning Brief — {brief.subject_theme} ({brief.date})"
        
        # Get subscribers from database
        subscribers = MarketBriefSubscriber.query.filter_by(confirmed=True).all()
        recipient_emails = [sub.email for sub in subscribers]
        
        if not recipient_emails:
            return jsonify({"error": "No confirmed subscribers found"}), 400
        
        # Send to all subscribers
        success = send_morning_brief(html_content, text_content, subject, recipient_emails)
        
        if success:
            return jsonify({"message": f"Morning brief sent to {len(recipient_emails)} subscribers"})
        else:
            return jsonify({"error": "Failed to send morning brief"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def get_tradier_headers():
    """Get headers for Tradier API requests"""
    if not TRADIER_API_TOKEN:
        print("Tradier API token not configured")
        return None  # Token not configured

    headers = {
        "Authorization": f"Bearer {TRADIER_API_TOKEN}",
        "Accept": "application/json",
    }
    return headers


def get_expiration_dates_tradier(symbol):
    """Return available option expiration dates from Tradier"""
    if not TRADIER_API_TOKEN:
        print("Tradier API token not configured")
        return None

    try:
        headers = get_tradier_headers()
        if not headers:
            print("Tradier API token not configured, skipping Tradier API call")
            return None

        exp_url = f"{TRADIER_API_BASE_URL}/markets/options/expirations"
        exp_params = {"symbol": symbol}

        exp_response = requests.get(exp_url, params=exp_params, headers=headers)
        if exp_response.status_code != 200:
            print(f"Error getting expirations for {symbol}: {exp_response.status_code}")
            return None

        exp_data = exp_response.json()
        if "expirations" not in exp_data or not exp_data["expirations"]:
            print(f"No expirations found for {symbol}")
            return None

        expirations = exp_data["expirations"]["date"]
        if isinstance(expirations, str):
            expirations = [expirations]

        return expirations

    except Exception as e:
        print(f"Error fetching expirations from Tradier for {symbol}: {e}")
        return None


def get_options_chain_tradier(symbol, expiration_date=None):
    """Get options chain data using Tradier API"""
    if not TRADIER_API_TOKEN:
        print("Tradier API token not configured")
        return None, None, None, None

    try:
        headers = get_tradier_headers()
        if not headers:
            print("Tradier API token not configured, skipping Tradier API call")
            return None, None, None, None

        # First get available expiration dates
        exp_url = f"{TRADIER_API_BASE_URL}/markets/options/expirations"
        exp_params = {"symbol": symbol}

        exp_response = requests.get(exp_url, params=exp_params, headers=headers)
        if exp_response.status_code != 200:
            print(f"Error getting expirations for {symbol}: {exp_response.status_code}")
            return None, None, None, None

        exp_data = exp_response.json()
        if "expirations" not in exp_data or not exp_data["expirations"]:
            print(f"No expirations found for {symbol}")
            return None, None, None, None

        expirations = exp_data["expirations"]["date"]
        if isinstance(expirations, str):
            expirations = [expirations]

        # Use provided expiration or first available
        target_date = (
            expiration_date
            if expiration_date and expiration_date in expirations
            else expirations[0]
        )

        # Get options chain for the target date
        chain_url = f"{TRADIER_API_BASE_URL}/markets/options/chains"
        chain_params = {"symbol": symbol, "expiration": target_date}

        chain_response = requests.get(chain_url, params=chain_params, headers=headers)
        if chain_response.status_code != 200:
            print(
                f"Error getting options chain for {symbol}: {chain_response.status_code}"
            )
            return None, None, None, None

        chain_data = chain_response.json()
        if "options" not in chain_data or not chain_data["options"]:
            print(f"No options data for {symbol} on {target_date}")
            return None, None, None, None

        options = chain_data["options"]["option"]
        if not isinstance(options, list):
            options = [options]

        # Separate calls and puts
        calls_data = []
        puts_data = []

        for option in options:
            option_data = {
                "strike": float(option["strike"]),
                "last": float(option.get("last", 0)) if option.get("last") else 0,
                "bid": float(option.get("bid", 0)) if option.get("bid") else 0,
                "ask": float(option.get("ask", 0)) if option.get("ask") else 0,
                "volume": int(option.get("volume", 0)) if option.get("volume") else 0,
                "open_interest": (
                    int(option.get("open_interest", 0))
                    if option.get("open_interest")
                    else 0
                ),
                "implied_volatility": (
                    float(option.get("greeks", {}).get("mid_iv", 0))
                    if option.get("greeks")
                    else 0
                ),
            }

            if option["option_type"] == "call":
                calls_data.append(option_data)
            else:
                puts_data.append(option_data)

        # Convert to DataFrames for compatibility
        calls_df = pd.DataFrame(calls_data) if calls_data else pd.DataFrame()
        puts_df = pd.DataFrame(puts_data) if puts_data else pd.DataFrame()

        # Get current stock price
        current_price, description = get_stock_price_tradier(symbol)

        return calls_df, puts_df, current_price, expirations

    except Exception as e:
        print(f"Error fetching options data from Tradier for {symbol}: {e}")
        return None, None, None, None


def get_stock_price_tradier(symbol):
    """Get current stock price and company name using Tradier API"""
    if not TRADIER_API_TOKEN:
        print("Tradier API token not configured")
        return None, None

    try:
        headers = get_tradier_headers()
        if not headers:
            print("Tradier API token not configured")
            return None, None

        url = f"{TRADIER_API_BASE_URL}/markets/quotes"
        params = {"symbols": symbol}

        print(f"Requesting stock price for {symbol} from Tradier...")
        response = requests.get(url, params=params, headers=headers)

        if response.status_code != 200:
            print(f"Tradier API error: {response.status_code} - {response.text}")
            return None, None

        data = response.json()
        print(f"Tradier response: {data}")

        if "quotes" in data and "quote" in data["quotes"]:
            quote = data["quotes"]["quote"]
            if isinstance(quote, list):
                quote = quote[0]
            price = float(quote.get("last", 0))
            description = quote.get(
                "description", symbol
            )  # Fall back to symbol if no description
            print(f"Got price for {symbol}: {price}, description: {description}")
            return price, description

        print(f"No price data found for {symbol} in Tradier response")
        return None, None

    except Exception as e:
        print(f"Error getting stock price from Tradier for {symbol}: {e}")
        import traceback

        traceback.print_exc()
        return None, None


def get_options_chain(symbol, expiration_date=None):
    """Get options chain data using Tradier API only"""
    if not TRADIER_API_TOKEN:
        print("Tradier API token not configured")
        return None, None, None

    try:
        print(f"Getting options chain for {symbol} using Tradier API...")
        calls, puts, current_price, expirations = get_options_chain_tradier(
            symbol, expiration_date
        )

        if (
            calls is not None
            and puts is not None
            and not calls.empty
            and not puts.empty
        ):
            print(f"Successfully retrieved options data from Tradier for {symbol}")
            return calls, puts, current_price

        print(f"Failed to get options data for {symbol} from Tradier")
        return None, None, None

    except Exception as e:
        print(f"Error in get_options_chain for {symbol}: {e}")
        import traceback

        traceback.print_exc()
        return None, None, None


def black_scholes(S, K, T, r, sigma, option_type="call"):
    """Calculate Black-Scholes option price"""
    try:
        # Handle edge cases
        if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
            return 0

        # Avoid division by zero in d1 calculation
        if sigma * np.sqrt(T) == 0:
            return 0

        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        if option_type == "call":
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        else:  # put
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

        return max(price, 0)
    except:
        return 0


def implied_volatility(price, S, K, T, r, option_type="call"):
    """Estimate implied volatility from option price."""
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return 0.0

    def objective(sigma):
        return black_scholes(S, K, T, r, sigma, option_type) - price

    try:
        return brentq(objective, 1e-6, 5.0, maxiter=100)
    except Exception:
        return 0.0


def calculate_greeks(S, K, T, r, sigma, option_type="call"):
    """Calculate option Greeks"""
    try:
        # Handle edge cases
        if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}

        # Avoid division by zero in d1 calculation
        if sigma * np.sqrt(T) == 0:
            return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}

        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        # Delta
        if option_type == "call":
            delta = norm.cdf(d1)
        else:
            delta = norm.cdf(d1) - 1

        # Gamma
        gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))

        # Theta
        if option_type == "call":
            theta = (
                -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
                - r * K * np.exp(-r * T) * norm.cdf(d2)
            ) / 365
        else:
            theta = (
                -(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
                + r * K * np.exp(-r * T) * norm.cdf(-d2)
            ) / 365

        # Vega
        vega = S * norm.pdf(d1) * np.sqrt(T) / 100

        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 4),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
        }
    except:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}


def allowed_file(filename):
    """Check if file extension is allowed"""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


def save_uploaded_file(file, prefix="chart"):
    """Save uploaded file with secure filename"""
    if file and allowed_file(file.filename):
        # Generate secure filename with random suffix
        filename = secure_filename(file.filename)
        name, ext = os.path.splitext(filename)
        unique_filename = f"{prefix}_{secrets.token_hex(8)}{ext}"

        # Create uploads directory if it doesn't exist
        upload_path = os.path.join(app.config["UPLOAD_FOLDER"])
        os.makedirs(upload_path, exist_ok=True)

        # Save file
        file_path = os.path.join(upload_path, unique_filename)
        file.save(file_path)
        return unique_filename
    return None


@app.route("/")
def index():
    return render_template("index.html", hide_sidebar=True)


@app.route("/home")
def home():
    """Public landing page with logged-in layout"""
    return render_template("index.html", show_logged_in=True)

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            flash("Welcome back!", "success")
            
            # Check if there's a pending trade to save
            if 'pending_trade' in session:
                return redirect(url_for("add_trade"))

            # Check if this is a brand new user (created in last 5 minutes)
            is_new_user = (datetime.utcnow() - user.created_at).total_seconds() < 300 if hasattr(user, 'created_at') else False

            next_page = request.args.get("next")
            if next_page and is_safe_url(next_page):
                return redirect(next_page)
            elif is_new_user:
                return redirect(url_for("welcome"))
            else:
                return redirect(url_for("dashboard"))
        flash("Invalid username or password", "danger")

    return render_template("login.html", form=form, hide_sidebar=True)


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        
        # Generate email verification token
        token = user.generate_email_verification_token()
        
        # Auto-subscribe defaults for new users: weekly yes (free tier), daily no
        try:
            user.is_subscribed_weekly = True
            user.is_subscribed_daily = False
        except Exception:
            # Fields may not exist in older schemas; proceed without failing
            pass
        db.session.add(user)
        db.session.commit()
        
        # Send verification email
        try:
            from emails import send_verification_email
            send_verification_email(user, token)
        except Exception as e:
            app.logger.warning(f"Failed to send verification email to {user.email}: {e}")
        
        login_user(user)
        flash("Welcome to Options Plunge! 🎉 Check your email to verify and unlock all features.", "success")
        # Auto-enroll new user to Market Brief subscribers (confirmed & active)
        try:
            existing = MarketBriefSubscriber.query.filter_by(email=user.email).first()
            if not existing:
                sub = MarketBriefSubscriber(email=user.email, name=user.username)
                sub.confirmed = True
                sub.is_active = True
                db.session.add(sub)
                db.session.commit()
                try:
                    from emails import send_welcome_email, send_welcome_on_register
                    # Send legacy subscriber welcome + account welcome summary
                    send_welcome_email(sub)
                    send_welcome_on_register(user)
                except Exception as e:
                    app.logger.warning(f"Welcome emails failed for {user.email}: {e}")
            else:
                # Ensure active + confirmed for existing record
                updated = False
                if not existing.confirmed:
                    existing.confirmed = True
                    updated = True
                if hasattr(existing, 'is_active') and not existing.is_active:
                    existing.is_active = True
                    updated = True
                if updated:
                    db.session.commit()
                try:
                    from emails import send_welcome_on_register
                    send_welcome_on_register(user)
                except Exception as e:
                    app.logger.warning(f"Welcome summary failed for {user.email}: {e}")
        except Exception as e:
            app.logger.warning(f"Market Brief auto-enroll failed for {user.email}: {e}")
        
        # Check if there's a pending trade to save
        if 'pending_trade' in session:
            flash("Great! Now let's save that trade you were working on.", "info")
            return redirect(url_for("add_trade"))

        # Redirect to welcome page for new users
        return redirect(url_for("welcome"))

    response = make_response(render_template("register.html", form=form, hide_sidebar=True))
    # Prevent caching to ensure Google OAuth button shows
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route("/welcome")
@login_required
def welcome():
    """Welcome page for new users after registration"""
    return render_template("welcome.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    """Dashboard page - shows basic stats and recent trades if logged in"""
    try:
        # Check for mobile preference from session or query parameter
        mobile_preference = session.get('mobile_preference', False) or request.args.get('mobile') == '1' or request.args.get('force_mobile') == '1'
        
        if current_user.is_authenticated:
            # Get recent trades
            recent_trades = current_user.get_recent_trades(10)

            # Get statistics
            stats = {
                "total_trades": Trade.query.filter_by(user_id=current_user.id).count(),
                "win_rate": current_user.get_win_rate(),
                "total_pnl": current_user.get_total_pnl(),
                "trades_analyzed": Trade.query.filter_by(
                    user_id=current_user.id, is_analyzed=True
                ).count(),
            }

            # Get recent journal entries
            recent_journals = (
                TradingJournal.query.filter_by(user_id=current_user.id)
                .order_by(TradingJournal.journal_date.desc())
                .limit(5)
                .all()
            )

            # Check if today's journal exists
            today_journal = TradingJournal.query.filter_by(
                user_id=current_user.id, journal_date=date.today()
            ).first()

            # Onboarding/checklist state
            has_trade = stats["total_trades"] > 0
            has_journal = TradingJournal.query.filter_by(user_id=current_user.id).count() > 0
            has_analysis = stats["trades_analyzed"] > 0
            next_url = None
            if not has_trade:
                next_url = url_for("add_trade")
            elif not has_journal:
                next_url = url_for("add_edit_journal")
            elif not has_analysis:
                next_url = url_for("bulk_analysis")

            onboarding = {
                "has_trade": has_trade,
                "has_journal": has_journal,
                "has_analysis": has_analysis,
                "next_url": next_url,
            }

            # Dismissal state (session + per-user persisted flag)
            onboarding_dismissed = bool(session.get('onboarding_dismissed'))
            try:
                if current_user.is_authenticated:
                    dismiss_dir = os.path.join(app.instance_path, 'onboarding_dismissed')
                    dismiss_path = os.path.join(dismiss_dir, f"{current_user.id}.flag")
                    if os.path.exists(dismiss_path):
                        onboarding_dismissed = True
            except Exception:
                pass

            return render_template(
                "dashboard.html",
                recent_trades=recent_trades,
                stats=stats,
                recent_journals=recent_journals,
                today_journal=today_journal,
                mobile_preference=mobile_preference,
                onboarding=onboarding,
                onboarding_dismissed=onboarding_dismissed,
            )
        else:
            # Show aggregate stats for guests using all available trades
            recent_trades = (
                Trade.query.order_by(Trade.entry_date.desc()).limit(10).all()
            )

            closed_trades = Trade.query.filter(Trade.exit_price.isnot(None)).all()
            win_rate = (
                len([t for t in closed_trades if t.profit_loss and t.profit_loss > 0])
                / len(closed_trades) * 100
                if closed_trades
                else 0
            )
            total_pnl = sum(t.profit_loss for t in closed_trades if t.profit_loss)

            stats = {
                "total_trades": Trade.query.count(),
                "win_rate": win_rate,
                "total_pnl": total_pnl,
                "trades_analyzed": Trade.query.filter_by(is_analyzed=True).count(),
            }

            recent_journals = (
                TradingJournal.query.order_by(TradingJournal.journal_date.desc())
                .limit(5)
                .all()
            )

            onboarding = {
                "has_trade": False,
                "has_journal": False,
                "has_analysis": False,
                "next_url": url_for("add_trade"),
            }

            onboarding_dismissed = bool(session.get('onboarding_dismissed'))

            return render_template(
                "dashboard.html",
                recent_trades=recent_trades,
                stats=stats,
                recent_journals=recent_journals,
                today_journal=None,
                mobile_preference=mobile_preference,
                onboarding=onboarding,
                onboarding_dismissed=onboarding_dismissed,
            )
    except Exception as e:
        # Log the error for debugging
        app.logger.error(f"Dashboard error: {str(e)}")
        app.logger.error(f"Error type: {type(e).__name__}")
        import traceback
        app.logger.error(f"Traceback: {traceback.format_exc()}")
        
        # Return a more specific error message
        return render_template("500.html", error_message=str(e)), 500


@app.route("/onboarding/dismiss", methods=["POST"])
def dismiss_onboarding():
    """Dismiss the onboarding checklist (session + per-user persistent flag)."""
    try:
        session['onboarding_dismissed'] = True
        if current_user.is_authenticated:
            dismiss_dir = os.path.join(app.instance_path, 'onboarding_dismissed')
            try:
                os.makedirs(dismiss_dir, exist_ok=True)
                dismiss_path = os.path.join(dismiss_dir, f"{current_user.id}.flag")
                with open(dismiss_path, 'w') as f:
                    f.write('1')
            except Exception as e:
                app.logger.warning(f"Failed to persist onboarding dismissal: {e}")
        return jsonify({"ok": True})
    except Exception as e:
        app.logger.error(f"Dismiss onboarding failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/trades")
def trades():
    """Display trades. Show real data for authenticated users, sample data for others."""
    
    # Show sample trades for unauthenticated users
    sample_trades = [
        {
            'id': 1,
            'symbol': 'AAPL',
            'trade_type': 'stock',
            'entry_date': datetime.now() - timedelta(days=5),
            'entry_price': 150.25,
            'quantity': 100,
            'exit_date': datetime.now() - timedelta(days=2),
            'exit_price': 155.75,
            'profit_loss': 550.00,
            'setup_type': 'breakout',
            'market_condition': 'bullish',
            'timeframe': 'daily',
            'entry_reason': 'Breakout above resistance with high volume',
            'exit_reason': 'Target reached',
            'notes': 'Strong earnings catalyst',
            'tags': 'earnings, breakout, tech'
        },
        {
            'id': 2,
            'symbol': 'TSLA',
            'trade_type': 'option_call',
            'entry_date': datetime.now() - timedelta(days=10),
            'entry_price': 2.50,
            'quantity': 10,
            'exit_date': datetime.now() - timedelta(days=7),
            'exit_price': 4.25,
            'profit_loss': 1750.00,
            'setup_type': 'momentum',
            'market_condition': 'bullish',
            'timeframe': '4h',
            'entry_reason': 'Strong momentum with high IV',
            'exit_reason': 'IV crush after earnings',
            'notes': 'Earnings play - sold before announcement',
            'tags': 'earnings, options, momentum'
        },
        {
            'id': 3,
            'symbol': 'SPY',
            'trade_type': 'credit_put_spread',
            'entry_date': datetime.now() - timedelta(days=15),
            'entry_price': 1.25,
            'quantity': 5,
            'exit_date': datetime.now() - timedelta(days=12),
            'exit_price': 0.50,
            'profit_loss': 375.00,
            'setup_type': 'income',
            'market_condition': 'sideways',
            'timeframe': 'daily',
            'entry_reason': 'High probability setup in sideways market',
            'exit_reason': 'Early profit taking',
            'notes': 'Theta decay working in our favor',
            'tags': 'income, spreads, theta'
        }
    ]
    
    if current_user.is_authenticated:
        # Check if user has real trades
        user_trades = Trade.query.filter_by(user_id=current_user.id).order_by(Trade.entry_date.desc()).paginate(
            page=request.args.get('page', 1, type=int), per_page=10, error_out=False)
        
        if user_trades.total > 0:
            # User has real trades - show them
            return render_template("trades.html", trades=user_trades, show_login_prompt=False, is_authenticated=True, show_demo_data=False)
        else:
            # User has no trades - show demo data
            return render_template("trades.html", trades=sample_trades, show_login_prompt=False, is_authenticated=True, show_demo_data=True)
    else:
        # For unauthenticated users, show sample data with login prompt
        return render_template("trades.html", trades=sample_trades, show_login_prompt=True, is_authenticated=False)


@app.route("/add_trade", methods=["GET", "POST"])
def add_trade():
    """Display trade form. Login required only when saving."""
    form = TradeForm()

    if request.method == "POST" and not current_user.is_authenticated:
        # Store form data in session for later use
        session['pending_trade'] = {
            'symbol': form.symbol.data.upper(),
            'trade_type': form.trade_type.data,
            'is_planned': form.is_planned.data,
            'entry_date': form.entry_date.data.isoformat() if form.entry_date.data else None,
            'entry_price': form.entry_price.data,
            'quantity': form.quantity.data,
            'stop_loss': form.stop_loss.data,
            'take_profit': form.take_profit.data,
            'risk_amount': form.risk_amount.data,
            'exit_date': form.exit_date.data.isoformat() if form.exit_date.data else None,
            'exit_price': form.exit_price.data,
            'setup_type': form.setup_type.data,
            'market_condition': form.market_condition.data,
            'timeframe': form.timeframe.data,
            'entry_reason': form.entry_reason.data,
            'exit_reason': form.exit_reason.data,
            'notes': form.notes.data,
            'tags': form.tags.data,
            'strike_price': form.strike_price.data,
            'expiration_date': form.expiration_date.data.isoformat() if form.expiration_date.data else None,
            'premium_paid': form.premium_paid.data,
            'underlying_price_at_entry': form.underlying_price_at_entry.data,
            'underlying_price_at_exit': form.underlying_price_at_exit.data,
            'implied_volatility': form.implied_volatility.data,
            'delta': form.delta.data,
            'gamma': form.gamma.data,
            'theta': form.theta.data,
            'vega': form.vega.data,
            'long_strike': form.long_strike.data,
            'short_strike': form.short_strike.data,
            'long_premium': form.long_premium.data,
            'short_premium': form.short_premium.data,
            'net_credit': form.net_credit.data
        }
        
        # Store uploaded files in session if they exist
        if form.entry_chart_image.data:
            entry_chart_filename = save_uploaded_file(form.entry_chart_image.data, "entry")
            session['pending_trade']['entry_chart_image'] = entry_chart_filename
            
        if form.exit_chart_image.data:
            exit_chart_filename = save_uploaded_file(form.exit_chart_image.data, "exit")
            session['pending_trade']['exit_chart_image'] = exit_chart_filename
            
        flash("Trade details saved! Please log in or create an account to save this trade permanently.", "info")
        return redirect(url_for("login", next=url_for("add_trade")))

    if form.validate_on_submit():
        try:
            # Handle file uploads
            entry_chart_filename = None
            exit_chart_filename = None

            if form.entry_chart_image.data:
                entry_chart_filename = save_uploaded_file(form.entry_chart_image.data, "entry")
            elif 'pending_trade' in session and 'entry_chart_image' in session['pending_trade']:
                entry_chart_filename = session['pending_trade']['entry_chart_image']

            if form.exit_chart_image.data:
                exit_chart_filename = save_uploaded_file(form.exit_chart_image.data, "exit")
            elif 'pending_trade' in session and 'exit_chart_image' in session['pending_trade']:
                exit_chart_filename = session['pending_trade']['exit_chart_image']

            # Chart data fields
            chart_annotations_json = request.form.get("chart_annotations_json") or None
            chart_snapshot_path = request.form.get("chart_snapshot_path") or None

            print(f"Creating trade with user_id: {current_user.id}")
            trade = Trade(
                user_id=current_user.id,
                symbol=form.symbol.data.upper(),
                trade_type=form.trade_type.data,
                is_planned=form.is_planned.data,
                entry_date=form.entry_date.data,
                entry_price=form.entry_price.data,
                quantity=form.quantity.data,
                stop_loss=form.stop_loss.data,
                take_profit=form.take_profit.data,
                risk_amount=form.risk_amount.data,
                exit_date=form.exit_date.data,
                exit_price=form.exit_price.data,
                setup_type=form.setup_type.data,
                market_condition=form.market_condition.data,
                timeframe=form.timeframe.data,
                entry_reason=form.entry_reason.data,
                exit_reason=form.exit_reason.data,
                notes=form.notes.data,
                tags=form.tags.data,
                entry_chart_image=entry_chart_filename,
                exit_chart_image=exit_chart_filename,
                # Options-specific fields
                strike_price=form.strike_price.data,
                expiration_date=form.expiration_date.data,
                premium_paid=form.premium_paid.data,
                underlying_price_at_entry=form.underlying_price_at_entry.data,
                underlying_price_at_exit=form.underlying_price_at_exit.data,
                implied_volatility=form.implied_volatility.data,
                delta=form.delta.data,
                gamma=form.gamma.data,
                theta=form.theta.data,
                vega=form.vega.data,
                # Spread-specific fields
                long_strike=form.long_strike.data,
                short_strike=form.short_strike.data,
                long_premium=form.long_premium.data,
                short_premium=form.short_premium.data,
                net_credit=form.net_credit.data,
                # Chart data fields
                chart_annotations=chart_annotations_json,
                chart_snapshot_path=chart_snapshot_path,
            )

            print("Trade object created successfully")

            # Set option type from trade type
            if trade.trade_type == "option_call":
                trade.option_type = "call"
            elif trade.trade_type == "option_put":
                trade.option_type = "put"
            elif trade.trade_type in ["credit_put_spread", "credit_call_spread"]:
                trade.is_spread = True
                trade.spread_type = trade.trade_type
                trade.option_type = "put" if "put" in trade.trade_type else "call"
                # Calculate spread metrics
                trade.calculate_spread_metrics()

            print("Trade type and options set")

            # Calculate P&L if trade is closed
            trade.calculate_pnl()

            print("P&L calculated")

            db.session.add(trade)
            print("Trade added to session")
            db.session.commit()
            print("Trade committed successfully")

            # Clear any pending trade data from session
            if 'pending_trade' in session:
                session.pop('pending_trade')

            # Handle different next_action values
            next_action = request.form.get('next_action', 'save')
            
            if next_action == 'save_planned':
                flash("✓ Planned trade saved successfully! You can now analyze it with AI.", "success")
                return redirect(url_for("view_trade", id=trade.id))
            elif next_action == 'save_add':
                # Save and add another trade
                flash("✓ Trade saved successfully! You can add another trade below.", "success")
                return redirect(url_for("add_trade"))
            elif next_action == 'save_dup':
                # Save and duplicate - redirect to add_trade with trade data pre-filled
                flash("✓ Trade saved successfully! Form cleared for next trade.", "success")
                return redirect(url_for("add_trade"))
            elif next_action == 'save_planned_analyze':
                # Check if user has Pro access for AI analysis
                if not current_user.has_pro_access():
                    flash("✓ Planned trade saved! AI planning requires Pro access. Upgrade to get AI-powered entry and exit recommendations.", "warning")
                    return redirect(url_for("view_trade", id=trade.id))
                
                try:
                    # Get user settings for risk defaults
                    user_settings = None
                    if hasattr(current_user, 'settings'):
                        user_settings = current_user.settings
                    
                    analysis = ai_analyzer.analyze_planned_trade(trade, user_settings)
                    if analysis:
                        flash("✓ Planned trade saved and AI analysis completed! Review the recommendations below.", "success")
                    else:
                        flash("✓ Planned trade saved! AI analysis failed - please try again later.", "warning")
                    return redirect(url_for("view_trade", id=trade.id))
                except Exception as e:
                    print(f"Error in planned trade analysis: {e}")
                    flash("✓ Planned trade saved! AI analysis failed - please try again later.", "warning")
                    return redirect(url_for("view_trade", id=trade.id))
            else:
                # Handle normal trade analysis for closed trades
                if (
                    trade.exit_price
                    and hasattr(current_user, "settings")
                    and current_user.settings.auto_analyze_trades
                ):
                    try:
                        ai_analyzer.analyze_trade(trade)
                        flash("✓ Trade added and analyzed successfully! View it in your trades list.", "success")
                    except Exception as e:
                        print(f"Error in auto-analysis: {e}")
                        flash("✓ Trade added successfully! Analysis will be done later. View it in your trades list.", "success")
                else:
                    flash("✓ Trade added successfully! View it in your trades list.", "success")

                return redirect(url_for("trades"))
            
        except Exception as e:
            print(f"Error in add_trade: {e}")
            import traceback
            traceback.print_exc()
            flash(f"Error adding trade: {str(e)}", "danger")
            return render_template("add_trade.html", form=form)

    # If there's pending trade data in session, populate the form
    if 'pending_trade' in session and not current_user.is_authenticated:
        pending_trade = session['pending_trade']
        for field in form:
            if field.name in pending_trade and pending_trade[field.name] is not None:
                if isinstance(field.data, datetime):
                    field.data = datetime.fromisoformat(pending_trade[field.name])
                else:
                    field.data = pending_trade[field.name]

    return render_template("add_trade.html", form=form)


@app.route("/trade/<int:id>")
@login_required
def view_trade(id):
    trade = Trade.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    analysis = TradeAnalysis.query.filter_by(trade_id=id).first()
    return render_template("view_trade.html", trade=trade, analysis=analysis)


@app.route("/trade/<int:id>/edit", methods=["GET", "POST"])
@login_required
def edit_trade(id):
    trade = Trade.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    form = EditTradeForm(obj=trade)

    if form.validate_on_submit():
        if form.calculate_pnl.data:
            # Just calculate P&L and return to form
            form.populate_obj(trade)
            trade.calculate_pnl()
            db.session.commit()
            flash("P&L calculated!", "info")
            return render_template("edit_trade.html", form=form, trade=trade)
        elif form.submit.data:
            # Save the trade
            form.populate_obj(trade)
            trade.calculate_pnl()
            db.session.commit()
            flash("✓ Trade updated successfully! You can now analyze it with AI.", "success")
            return redirect(url_for("view_trade", id=trade.id))

    return render_template("edit_trade.html", form=form, trade=trade)


@app.route("/trade/<int:id>/analyze", methods=["POST"])
@login_required
@requires_pro
def analyze_trade(id):
    trade = Trade.query.filter_by(id=id, user_id=current_user.id).first_or_404()

    try:
        print(f"DEBUG: Starting AI analysis for trade {trade.id}")
        print(f"DEBUG: About to call ai_analyzer.analyze_trade")
        print(f"DEBUG: OPENAI_API_KEY from env: {os.getenv('OPENAI_API_KEY', 'NOT_FOUND')[:20] if os.getenv('OPENAI_API_KEY') else 'NOT_FOUND'}...")
        analysis = ai_analyzer.analyze_trade(trade)
        print(f"DEBUG: Analysis result: {analysis}")
        print(f"DEBUG: Analysis type: {type(analysis)}")
        
        if analysis is None:
            print(f"DEBUG: Analysis returned None")
            flash("Analysis failed. Please check your OpenAI API key.", "error")
        elif isinstance(analysis, dict) and analysis.get("error"):
            print(f"DEBUG: Analysis error: {analysis['error']}")
            flash(f"Analysis failed: {analysis['error']}", "error")
        elif hasattr(analysis, 'trade_id'):  # TradeAnalysis object
            print(f"DEBUG: Analysis successful, trade_id: {analysis.trade_id}")
            flash("Trade analysis completed!", "success")
        else:
            print(f"DEBUG: Unexpected analysis result type")
            flash("Analysis failed. Please check your OpenAI API key.", "error")
    except Exception as e:
        print(f"DEBUG: Analysis exception: {str(e)}")
        flash(f"Analysis error: {str(e)}", "error")

    return redirect(url_for("view_trade", id=id))


@app.route("/trade/<int:id>/analyze_planned", methods=["POST"])
@login_required
@requires_pro
def analyze_planned_trade(id):
    trade = Trade.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    
    if not trade.is_planned:
        flash("This trade is not a planned trade.", "error")
        return redirect(url_for("view_trade", id=id))

    try:
        print(f"DEBUG: Starting AI planned trade analysis for trade {trade.id}")
        
        # Get user settings for risk defaults
        user_settings = None
        if hasattr(current_user, 'settings'):
            user_settings = current_user.settings
        
        analysis = ai_analyzer.analyze_planned_trade(trade, user_settings)
        if analysis:
            flash("AI planning analysis completed!", "success")
        else:
            flash("AI planning analysis failed - please try again later.", "warning")
    except Exception as e:
        print(f"DEBUG: Planned trade analysis exception: {str(e)}")
        flash(f"Analysis error: {str(e)}", "error")

    return redirect(url_for("view_trade", id=id))


@app.route("/journal")
def journal():
    """Display journal entries. Show real data for authenticated users, sample data for others."""
    page = request.args.get("page", 1, type=int)
    
    # Show sample journal entries for unauthenticated users
    from datetime import datetime, timedelta
    
    sample_journals = [
        {
            'journal_date': datetime.now() - timedelta(days=1),
            'market_notes': 'Market showing strong momentum in tech sector. AAPL and TSLA leading the charge with earnings catalysts. VIX remains low indicating complacency.',
            'trading_notes': 'Executed 3 trades today: AAPL breakout (winner), TSLA momentum (winner), SPY reversal (loser). Overall P&L: +$450. Stuck to my plan and managed risk well.',
            'emotions': 'Felt confident and focused. No FOMO or revenge trading urges. Stayed disciplined with position sizing.',
            'lessons_learned': 'Breakout trades work best with volume confirmation. Need to be more patient with reversal setups.',
            'tomorrow_plan': 'Focus on high-probability setups only. Watch for continuation patterns in tech. Keep position sizes consistent.',
            'daily_pnl': 450.00,
            'daily_score': 8.5,
            'ai_daily_feedback': 'Excellent discipline today! Your risk management was spot-on and you stuck to your trading plan. Consider adding volume analysis to your reversal setups.'
        },
        {
            'journal_date': datetime.now() - timedelta(days=2),
            'market_notes': 'Market choppy with mixed signals. Fed minutes caused some volatility. Sector rotation into defensive names.',
            'trading_notes': 'Only 1 trade: QQQ put spread (small loss). Market conditions weren\'t ideal for my setups. Better to sit out than force trades.',
            'emotions': 'Frustrated with the choppy market but stayed patient. Proud that I didn\'t chase bad setups.',
            'lessons_learned': 'Sometimes the best trade is no trade. Market conditions matter more than individual setups.',
            'tomorrow_plan': 'Wait for clearer market direction. Focus on quality over quantity.',
            'daily_pnl': -75.00,
            'daily_score': 7.0,
            'ai_daily_feedback': 'Great job staying patient in difficult market conditions. Your discipline to avoid forcing trades shows maturity. Consider adding market condition filters to your strategy.'
        },
        {
            'journal_date': datetime.now() - timedelta(days=3),
            'market_notes': 'Strong bullish day with clear trend. All major indices up 1%+. Volume confirming the move.',
            'trading_notes': '2 trades: SPY call (winner), IWM breakout (winner). Both trades followed the trend and had clear setups.',
            'emotions': 'Excited about the clear market direction. Felt in sync with the market rhythm.',
            'lessons_learned': 'Trend following works best in strong trending markets. Don\'t fight the trend.',
            'tomorrow_plan': 'Look for continuation patterns. Consider adding to winning positions if trend continues.',
            'daily_pnl': 325.00,
            'daily_score': 9.0,
            'ai_daily_feedback': 'Outstanding performance! You perfectly aligned with market conditions and executed flawlessly. Your trend-following approach was textbook.'
        }
    ]
    
    # Create a mock pagination object for the sample data
    class MockPagination:
        def __init__(self, items, page, per_page):
            self.items = items
            self.page = page
            self.per_page = per_page
            self.total = len(items)
            self.pages = 1
            self.has_prev = False
            self.has_next = False
            self.prev_num = None
            self.next_num = None
            self.iter_pages = lambda: [1]
    
    if current_user.is_authenticated:
        # Check if user has real journal entries
        user_journals = TradingJournal.query.filter_by(user_id=current_user.id).order_by(TradingJournal.journal_date.desc()).paginate(
            page=page, per_page=20, error_out=False)
        
        if user_journals.total > 0:
            # User has real journal entries - show them
            return render_template("journal.html", journals=user_journals, show_login_prompt=False, show_demo_data=False)
        else:
            # User has no journal entries - show demo data
            journals = MockPagination(sample_journals, page, 20)
            return render_template("journal.html", journals=journals, show_login_prompt=False, show_demo_data=True)
    else:
        # For unauthenticated users, show sample data with login prompt
        journals = MockPagination(sample_journals, page, 20)
        return render_template("journal.html", journals=journals, show_login_prompt=True)


@app.route("/journal/add", methods=["GET", "POST"])
@app.route("/journal/<journal_date>/edit", methods=["GET", "POST"])
def add_edit_journal(journal_date=None):
    """Journal entry page. Allow unauthenticated users to view and fill forms."""

    if journal_date:
        if not current_user.is_authenticated:
            flash("Please log in to edit journal entries.", "warning")
            return redirect(
                url_for(
                    "login", next=url_for("add_edit_journal", journal_date=journal_date)
                )
            )
        # Edit existing journal
        journal_date_obj = datetime.strptime(journal_date, "%Y-%m-%d").date()
        journal = TradingJournal.query.filter_by(
            user_id=current_user.id, journal_date=journal_date_obj
        ).first_or_404()
        form = JournalForm(obj=journal)
        is_edit = True
    else:
        # Add new journal
        journal = None
        form = JournalForm()
        is_edit = False

    if request.method == "POST" and not current_user.is_authenticated:
        # Store form data in session for later use
        session['pending_journal'] = {
            'journal_date': form.journal_date.data.isoformat() if form.journal_date.data else None,
            'market_notes': form.market_notes.data,
            'trading_notes': form.trading_notes.data,
            'emotions': form.emotions.data,
            'lessons_learned': form.lessons_learned.data,
            'tomorrow_plan': form.tomorrow_plan.data,
            'daily_pnl': form.daily_pnl.data,
            'daily_score': form.daily_score.data
        }
        flash("Journal entry saved! Please log in or create an account to save permanently.", "info")
        return redirect(url_for("login", next=url_for("add_edit_journal")))

    if form.validate_on_submit() and current_user.is_authenticated:
        if journal:
            # Update existing
            form.populate_obj(journal)
        else:
            # Create new journal entry. Guests have no user_id
            journal = TradingJournal(
                user_id=current_user.id if current_user.is_authenticated else None
            )
            form.populate_obj(journal)

        # Get trades for this day and analyze daily performance
        day_trades = journal.get_day_trades()
        if day_trades or journal.daily_pnl:
            try:
                daily_analysis = ai_analyzer.analyze_daily_performance(
                    journal, day_trades
                )
                if daily_analysis and not daily_analysis.get("error"):
                    journal.ai_daily_feedback = daily_analysis["feedback"]
                    journal.daily_score = daily_analysis["daily_score"]
                elif daily_analysis and daily_analysis.get("error"):
                    print(f"AI Analysis Error: {daily_analysis['error']}")
                    flash(f"AI Analysis failed: {daily_analysis['error']}", "warning")
            except Exception as e:
                print(f"AI Analysis Exception: {str(e)}")
                flash(f"AI Analysis failed: {str(e)}", "warning")

        db.session.add(journal)
        db.session.commit()

        # Clear any pending journal data from session
        if 'pending_journal' in session:
            session.pop('pending_journal')

        action = "updated" if is_edit else "added"
        flash(f"Journal entry {action} successfully!", "success")
        return redirect(url_for("journal"))

    # If there's pending journal data in session, populate the form
    if 'pending_journal' in session and not current_user.is_authenticated:
        pending_journal = session['pending_journal']
        for field in form:
            if field.name in pending_journal and pending_journal[field.name] is not None:
                if isinstance(field.data, datetime):
                    field.data = datetime.fromisoformat(pending_journal[field.name])
                else:
                    field.data = pending_journal[field.name]

    # Get trades for this day (for context)
    if journal_date and current_user.is_authenticated:
        day_trades = journal.get_day_trades()
    else:
        day_trades = []

    return render_template(
        "add_edit_journal.html",
        form=form,
        journal=journal,
        is_edit=is_edit,
        day_trades=day_trades,
        is_authenticated=current_user.is_authenticated
    )


@app.route("/analytics")
def analytics():
    """Show performance analytics. Show real data for authenticated users, sample data for others."""
    
    if current_user.is_authenticated:
        # Check if user has real trades for analytics
        user_trades = Trade.query.filter_by(user_id=current_user.id).filter(Trade.exit_price.isnot(None)).all()
        
        if len(user_trades) >= 1:  # Show analytics with at least 1 completed trade
            # User has enough real trades - show real analytics
            df = pd.DataFrame([
                {
                    "date": trade.exit_date or trade.entry_date,
                    "pnl": trade.profit_loss or 0,
                    "is_winner": (trade.profit_loss or 0) > 0,
                    "setup_type": trade.setup_type or "unknown"
                }
                for trade in user_trades
            ])
            
            if not df.empty:
                # Calculate real stats
                total_trades = len(df)
                winning_trades = len(df[df["is_winner"] == True])
                losing_trades = len(df[df["is_winner"] == False])
                win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
                total_pnl = df["pnl"].sum()
                avg_win = df[df["pnl"] > 0]["pnl"].mean() if len(df[df["pnl"] > 0]) > 0 else 0
                avg_loss = df[df["pnl"] < 0]["pnl"].mean() if len(df[df["pnl"] < 0]) > 0 else 0
                largest_win = df["pnl"].max() if len(df) > 0 else 0
                largest_loss = df["pnl"].min() if len(df) > 0 else 0
                profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
                
                stats = {
                    "total_trades": total_trades,
                    "winning_trades": winning_trades,
                    "losing_trades": losing_trades,
                    "win_rate": win_rate,
                    "total_pnl": total_pnl,
                    "avg_win": avg_win,
                    "avg_loss": avg_loss,
                    "largest_win": largest_win,
                    "largest_loss": largest_loss,
                    "profit_factor": profit_factor,
                }
                
                # Create charts with real data
                charts = create_analytics_charts(df)
                charts_json = json.dumps(charts, cls=plotly.utils.PlotlyJSONEncoder)
                
                return render_template(
                    "analytics.html", charts_json=charts_json, stats=stats, no_data=False, show_login_prompt=False, show_demo_data=False
                )
        
        # User doesn't have enough real trades - show demo data
        return render_template(
            "analytics.html", charts_json=None, stats=None, no_data=True, show_login_prompt=False, show_demo_data=True
        )
    else:
        # For unauthenticated users, show sample data
        from datetime import datetime, timedelta
        
        # Create sample data for demonstration
        sample_dates = [
            datetime.now() - timedelta(days=30),
            datetime.now() - timedelta(days=25),
            datetime.now() - timedelta(days=20),
            datetime.now() - timedelta(days=15),
            datetime.now() - timedelta(days=10),
            datetime.now() - timedelta(days=5),
            datetime.now() - timedelta(days=1)
        ]
        
        sample_data = [
            {"date": sample_dates[0], "pnl": 250, "is_winner": True, "setup_type": "breakout"},
            {"date": sample_dates[1], "pnl": -150, "is_winner": False, "setup_type": "reversal"},
            {"date": sample_dates[2], "pnl": 400, "is_winner": True, "setup_type": "breakout"},
            {"date": sample_dates[3], "pnl": 175, "is_winner": True, "setup_type": "momentum"},
            {"date": sample_dates[4], "pnl": -200, "is_winner": False, "setup_type": "reversal"},
            {"date": sample_dates[5], "pnl": 300, "is_winner": True, "setup_type": "breakout"},
            {"date": sample_dates[6], "pnl": 125, "is_winner": True, "setup_type": "momentum"}
        ]
        
        df = pd.DataFrame(sample_data)
        
        stats = {
            "total_trades": 7,
            "winning_trades": 5,
            "losing_trades": 2,
            "win_rate": 71.4,
            "total_pnl": 900,
            "avg_win": 250,
            "avg_loss": -175,
            "largest_win": 400,
            "largest_loss": -200,
            "profit_factor": 3.57,
        }

        # Create charts with sample data
        charts = create_analytics_charts(df)
        charts_json = json.dumps(charts, cls=plotly.utils.PlotlyJSONEncoder)
        
        return render_template(
            "analytics.html", charts_json=charts_json, stats=stats, no_data=False, show_login_prompt=True
        )


def create_analytics_charts(df):
    """Create analytics charts"""
    charts = {}

    # P&L over time
    df_sorted = df.sort_values("date")
    # Normalize to date-only for consistent x-axis granularity
    try:
        df_sorted["date"] = pd.to_datetime(df_sorted["date"]).dt.date
    except Exception:
        pass
    df_sorted["cumulative_pnl"] = df_sorted["pnl"].cumsum()

    charts["pnl_over_time"] = {
        "data": [
            {
                "x": df_sorted["date"].tolist(),
                "y": df_sorted["cumulative_pnl"].tolist(),
                "type": "scatter",
                "mode": "lines",
                "name": "Cumulative P&L",
                "line": {"color": "#1f77b4"},
            }
        ],
        "layout": {
            "title": "Cumulative P&L Over Time",
            "xaxis": {"title": "Date", "type": "date", "tickformat": "%Y-%m-%d"},
            "yaxis": {"title": "Cumulative P&L ($)", "rangemode": "tozero", "zeroline": True},
            "height": 400,
        },
    }

    # Win/Loss distribution
    win_loss_counts = df["is_winner"].value_counts()
    charts["win_loss_pie"] = {
        "data": [
            {
                "values": [win_loss_counts.get(True, 0), win_loss_counts.get(False, 0)],
                "labels": ["Wins", "Losses"],
                "type": "pie",
                "colors": ["#2ecc71", "#e74c3c"],
            }
        ],
        "layout": {"title": "Win/Loss Distribution", "height": 400},
    }

    # Setup type performance
    setup_performance = (
        df.groupby("setup_type")["pnl"].sum().sort_values(ascending=False)
    )
    charts["setup_performance"] = {
        "data": [
            {
                "x": setup_performance.index.tolist(),
                "y": setup_performance.values.tolist(),
                "type": "bar",
                "marker": {
                    "color": [
                        "#2ecc71" if x > 0 else "#e74c3c"
                        for x in setup_performance.values
                    ]
                },
            }
        ],
        "layout": {
            "title": "P&L by Setup Type",
            "xaxis": {"title": "Setup Type"},
            "yaxis": {"title": "Total P&L ($)"},
            "height": 400,
        },
    }

    return charts


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    # Get or create user settings
    user_settings = current_user.settings
    if not user_settings:
        user_settings = UserSettings(user_id=current_user.id)
        db.session.add(user_settings)
        db.session.commit()

    # Create form with current user data
    form = SettingsForm(obj=current_user)
    
    # Pre-populate basic user settings fields (only if they exist)
    if user_settings:
        try:
            if hasattr(form, 'auto_analyze_trades'):
                form.auto_analyze_trades.data = user_settings.auto_analyze_trades
            if hasattr(form, 'analysis_detail_level'):
                form.analysis_detail_level.data = user_settings.analysis_detail_level
        except AttributeError:
            pass  # Skip if fields don't exist

    # Accept valid POSTs even if validate_on_submit doesn't trigger for some reason
    if request.method == 'POST':
        try:
            app.logger.info(f"Settings POST received: keys={list(request.form.keys())}")
        except Exception:
            pass
    
    if request.method == 'POST' and form.validate():
        # Update current_user fields from the form (only basic fields)
        current_user.display_name = form.display_name.data
        current_user.dark_mode = form.dark_mode.data
        current_user.daily_brief_email = form.daily_brief_email.data
        current_user.timezone = form.timezone.data
        
        # Only update fields that exist in the form
        try:
            if hasattr(form, 'api_key'):
                current_user.api_key = form.api_key.data
            if hasattr(form, 'account_size'):
                current_user.account_size = form.account_size.data
            if hasattr(form, 'default_risk_percent'):
                current_user.default_risk_percent = form.default_risk_percent.data
        except AttributeError:
            pass

        # Update user settings (only if fields exist)
        if user_settings:
            try:
                if hasattr(form, 'auto_analyze_trades'):
                    user_settings.auto_analyze_trades = form.auto_analyze_trades.data
                if hasattr(form, 'analysis_detail_level'):
                    user_settings.analysis_detail_level = form.analysis_detail_level.data
            except AttributeError:
                pass

        db.session.commit()
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings'))
    else:
        # If POST but not valid, surface errors and log for troubleshooting
        if request.method == 'POST':
            try:
                app.logger.warning(f"Settings form validation failed: {form.errors}")
            except Exception:
                pass
            if form.errors:
                flash('Could not save settings. Please fix the errors highlighted below.', 'error')

    # Prepare billing context for template
    from datetime import datetime, timezone
    billing_ctx = None
    
    # Show billing info for any user with Pro access or subscription status
    if current_user.has_pro_access() or current_user.subscription_status != 'free':
        days_left = None
        if current_user.subscription_status == "trialing" and current_user.trial_end:
            now = datetime.now(timezone.utc)
            days_left = max(0, (current_user.trial_end.replace(tzinfo=timezone.utc) - now).days)
        
        billing_ctx = {
            "status": current_user.subscription_status,
            "plan": current_user.plan_type,
            "trial_days_left": days_left,
            "has_portal": bool(current_user.stripe_customer_id),
        }

    return render_template("settings.html", form=form, billing=billing_ctx)


@app.route("/bulk_analysis", methods=["GET", "POST"])
def bulk_analysis():
    form = BulkAnalysisForm()
    
    # Check for preview mode query param
    preview_mode = request.args.get('preview') == '1'
    
    # Determine if user should see preview or full access (ignore preview for Pro users)
    show_preview = not is_pro_user()
    
    if show_preview:
        # Preview mode - show demo data and upsell
        sample_trade = {
            'symbol': 'AAPL',
            'entry_date': datetime.now() - timedelta(days=5),
            'exit_date': datetime.now() - timedelta(days=1),
            'entry_price': 150.00,
            'exit_price': 155.00,
            'quantity': 100,
            'trade_type': 'long',
            'profit_loss': 500.00
        }
        
        # Provide full fields expected by template to avoid 500s in preview mode
        sample_analysis = {
            'overall_score': 8.5,
            'entry_analysis': 'Entered on pullback to prior resistance acting as support with volume confirmation.',
            'exit_analysis': 'Took profits into prior day high; conservative but aligned with plan.',
            'risk_analysis': '2% risk with defined stop below support; position sizing appropriate.',
            'market_context': 'Uptrend with strong sector bid; breadth supportive of continuation.',
            'strengths': [
                'Clear setup with confluence at support',
                'Disciplined risk with predefined stop',
                'Patience on entry to avoid chasing'
            ],
            'weaknesses': [
                'Exited early relative to trend strength',
                'Could improve scaling-out plan for runners'
            ],
            'key_lessons': [
                'Pullbacks to structure offer favorable R:R',
                'Define partial take-profit vs runner criteria'
            ],
            'recommendations': [
                'Trail a portion using swing lows/EMA',
                'Pre-plan scale-out triggers based on ATR'
            ],
        }
        
        return render_template(
            "bulk_analysis.html",
            form=form,
            unanalyzed_count=0,
            recent_count=0,
            sample_trade=sample_trade,
            sample_analysis=sample_analysis,
            show_login_prompt=False,
            show_pro_upsell=True,
            show_demo_data=True,
            feature_name="AI Analysis",
            limitations=[
                "Sample analysis only - no real trade analysis",
                "Cannot analyze your actual trades",
                "No bulk analysis capabilities"
            ]
        )

    # Full Pro access - original logic
    if current_user.is_authenticated:
        # Populate trade choices for individual analysis
        trades = (
            Trade.query.filter_by(user_id=current_user.id)
            .filter(Trade.exit_price.isnot(None))
            .order_by(Trade.entry_date.desc())
            .all()
        )
        form.trade_id.choices = [
            (0, "Select a trade...")
        ] + [
            (t.id, f"{t.symbol} - {t.entry_date.strftime('%Y-%m-%d')}") for t in trades
        ]

        if form.validate_on_submit():
            # Handle individual trade analysis first
            if form.trade_id.data and form.trade_id.data != 0:
                trade = Trade.query.filter_by(
                    id=form.trade_id.data, user_id=current_user.id
                ).first()
                if trade:
                    try:
                        analysis = ai_analyzer.analyze_trade(trade)
                        if analysis and hasattr(analysis, 'trade_id'):  # TradeAnalysis object
                            flash("Trade analyzed successfully!", "success")
                        elif analysis and isinstance(analysis, dict) and analysis.get("error"):
                            flash(f"Analysis failed: {analysis['error']}", "error")
                        else:
                            flash("Analysis failed. Please check your OpenAI API key.", "error")
                    except Exception as e:
                        flash(f"Analysis failed: {str(e)}", "error")
                    return redirect(url_for("view_trade", id=trade.id))

            trades_to_analyze = []

            if form.analyze_all_unanalyzed.data:
                trades_to_analyze.extend(
                    Trade.query.filter_by(user_id=current_user.id, is_analyzed=False)
                    .filter(Trade.exit_price.isnot(None))
                    .all()
                )

            if form.analyze_recent.data:
                thirty_days_ago = datetime.now() - timedelta(days=30)
                recent_trades = (
                    Trade.query.filter_by(user_id=current_user.id)
                    .filter(Trade.entry_date >= thirty_days_ago)
                    .filter(Trade.exit_price.isnot(None))
                    .all()
                )
                trades_to_analyze.extend(recent_trades)

            # Remove duplicates
            trades_to_analyze = list(set(trades_to_analyze))

            success_count = 0
            for trade in trades_to_analyze:
                try:
                    analysis = ai_analyzer.analyze_trade(trade)
                    if analysis and hasattr(analysis, 'trade_id'):  # TradeAnalysis object
                        success_count += 1
                except Exception as e:
                    print(f"Error analyzing trade {trade.id}: {e}")
                    continue

            flash(
                f"Successfully analyzed {success_count} out of {len(trades_to_analyze)} trades.",
                "success",
            )
            return redirect(url_for("trades"))

        # Get counts for display
        unanalyzed_count = (
            Trade.query.filter_by(user_id=current_user.id, is_analyzed=False)
            .filter(Trade.exit_price.isnot(None))
            .count()
        )

        thirty_days_ago = datetime.now() - timedelta(days=30)
        recent_count = (
            Trade.query.filter_by(user_id=current_user.id)
            .filter(Trade.entry_date >= thirty_days_ago)
            .filter(Trade.exit_price.isnot(None))
            .count()
        )

        return render_template(
            "bulk_analysis.html",
            form=form,
            unanalyzed_count=unanalyzed_count,
            recent_count=recent_count,
            sample_trade=None,
            sample_analysis=None,
            show_login_prompt=False,
            show_pro_upsell=False,
            show_demo_data=False
        )
    else:
        # Show example data for anonymous users
        sample_trade = {
            'symbol': 'AAPL',
            'trade_type': 'stock',
            'entry_date': datetime.now() - timedelta(days=5),
            'entry_price': 150.25,
            'exit_date': datetime.now() - timedelta(days=2),
            'exit_price': 155.75,
            'quantity': 100,
            'profit_loss': 550.00,
            'setup_type': 'breakout',
            'timeframe': 'daily',
            'market_condition': 'bullish',
            'entry_reason': 'Breakout above resistance with high volume',
            'exit_reason': 'Target reached'
        }
        
        sample_analysis = {
            'overall_score': 8.5,
            'entry_analysis': 'Strong breakout above resistance with high volume. Good risk/reward ratio.',
            'exit_analysis': 'Target reached at 155.75. Trade executed according to plan.',
            'risk_management': 'Stop loss was properly placed below support. Position sizing was appropriate.',
            'lessons_learned': 'Breakout trades work well in trending markets. Volume confirmation is key.',
            'improvement_suggestions': 'Consider trailing stops for longer-term breakouts.',
            'strengths': [
                'Excellent entry timing with volume confirmation',
                'Proper risk management with defined stop loss',
                'Clear exit strategy executed as planned'
            ],
            'weaknesses': [
                'Could have used trailing stops for more profit',
                'Position size could have been larger given the setup'
            ],
            'key_lessons': [
                'Volume confirmation is crucial for breakout trades',
                'Having a clear exit plan prevents emotional decisions'
            ],
            'recommendations': [
                'Continue focusing on high-probability setups',
                'Consider implementing trailing stops for winning trades',
                'Review position sizing for similar setups'
            ],
            'risk_analysis': 'Risk was well-managed with proper position sizing and stop loss placement.',
            'market_context': 'Market was in a bullish trend with strong sector rotation into technology stocks.'
        }
        
        return render_template(
            "bulk_analysis.html",
            form=form,
            unanalyzed_count=0,
            recent_count=0,
            sample_trade=sample_trade,
            sample_analysis=sample_analysis,
            show_login_prompt=True,
            show_pro_upsell=False,
            show_demo_data=False
        )


@app.route("/api/platform-stats")
def platform_stats():
    """Return real platform activity statistics for landing page"""
    try:
        stats = {
            "trades_tracked": Trade.query.count(),
            "journal_entries": TradingJournal.query.count(),
            "ai_analyses": TradeAnalysis.query.count(),
            "briefs_sent": MarketBriefSubscriber.query.filter_by(confirmed=True).count() * 52  # Approximate weekly briefs
        }
        
        # Format numbers for display
        def format_number(n):
            if n >= 1000000:
                return f"{n/1000000:.1f}M"
            elif n >= 1000:
                return f"{n/1000:.1f}K"
            else:
                return str(n)
        
        return jsonify({
            "trades_tracked": format_number(stats["trades_tracked"]),
            "journal_entries": format_number(stats["journal_entries"]),
            "ai_analyses": format_number(stats["ai_analyses"]),
            "briefs_sent": format_number(stats["briefs_sent"])
        })
    except Exception as e:
        # Return placeholder stats if database query fails
        return jsonify({
            "trades_tracked": "1K+",
            "journal_entries": "500+",
            "ai_analyses": "2K+",
            "briefs_sent": "10K+"
        })


@app.route("/api/quick_trade", methods=["POST"])
@login_required
@requires_pro
def api_quick_trade():
    """API endpoint for quick trade entry"""
    form = QuickTradeForm()

    if form.validate_on_submit():
        trade = Trade(
            user_id=current_user.id,
            symbol=form.symbol.data.upper(),
            trade_type=form.trade_type.data,
            entry_date=datetime.now(),
            entry_price=form.entry_price.data,
            quantity=form.quantity.data,
            setup_type=form.setup_type.data,
            timeframe="day_trade",  # Default for quick trades
        )

        db.session.add(trade)
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "message": "Trade added successfully!",
                "trade_id": trade.id,
            }
        )

    return jsonify({"success": False, "errors": form.errors})


# ───────── Tiingo API Helper Functions ─────────
def _bucket_allow():
    """Token bucket rate limiter"""
    now = time.time()
    delta = now - BUCKET["last"]
    BUCKET["last"] = now
    BUCKET["tokens"] = min(BUCKET["capacity"], BUCKET["tokens"] + delta * BUCKET["refill_rate"])
    if BUCKET["tokens"] >= 1:
        BUCKET["tokens"] -= 1
        return True
    return False

def _cache_get(key):
    """Get cached data if not expired"""
    row = CACHE.get(key)
    if row and (time.time() - row["ts"] < row["ttl"]):
        return row["data"]
    return None

def _cache_put(key, data, ttl):
    """Store data in cache with TTL"""
    CACHE[key] = {"data": data, "ts": time.time(), "ttl": ttl}

def _iso_to_ms(iso):
    """Convert Tiingo ISO timestamp to milliseconds"""
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    return int(dt.datetime.fromisoformat(iso).timestamp() * 1000)

def _coalesce(v, default=0): 
    return default if v is None else v

def _build_split_adjusters_from_eod_rows(rows):
    """
    Build a piecewise-constant cumulative split factor over time using EOD rows.
    rows: list(dict) from /tiingo/daily/<sym>/prices, each has "date" and maybe "splitFactor".
    Returns a list of (exDate_ms, cumulative_factor_before_that_date)
    Example: 2:1 on 2024-06-10 => factor=0.5 for all bars strictly before 2024-06-10.
    """
    # Sort by ascending date
    by_date = sorted(rows, key=lambda r: r["date"])
    # Walk forward building cumulative factor (latest basis = 1.0)
    # On the day of split, EOD gives splitFactor like 2.0 for 2:1 (per QC docs).
    # We want earlier history scaled by 1/splitFactor.
    cum = 1.0
    adj = []
    for r in by_date:
        sf = float(_coalesce(r.get("splitFactor"), 1.0))
        if sf and sf != 1.0:
            # this split applies to all prior timestamps
            # store the boundary (start-of-day) in ms
            ex_ms = _iso_to_ms(r["date"])
            cum *= 1.0 / sf
            adj.append((ex_ms, cum))
    return adj  # sorted in ascending date; later entries include earlier factors

def _apply_splits_daily(data, eod_rows):
    """
    Adjust daily O/H/L/C/V for splits.
    Always prefer exact per-row adjClose/close ratio when available; for rows
    lacking adjClose, fall back to cumulative split factors built from splitFactor.
    """
    t,o,h,l,c,v = data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]
    # Build map date_ms -> (close, adjClose, splitFactor)
    by_ms = {}
    for r in eod_rows:
        ms = _iso_to_ms(r["date"])
        by_ms[ms] = (
            float(_coalesce(r.get("close"), 0.0)),
            float(_coalesce(r.get("adjClose"), 0.0)),
            float(_coalesce(r.get("splitFactor"), 1.0)),
        )

    # Build cumulative factor timeline once for fallback
    timeline = _build_split_adjusters_from_eod_rows(eod_rows)

    def fallback_factor(ts):
        f = 1.0
        for (ex_ms, cf) in timeline:
            if ex_ms > ts:
                f = cf
                break
        return f

    for i, ts in enumerate(t):
        close, adjC, _sf = by_ms.get(ts, (0.0, 0.0, 1.0))
        if close and adjC:
            factor = adjC/close
        else:
            factor = fallback_factor(ts)
        if factor != 1.0:
            o[i] = round(o[i]*factor, 4); h[i] = round(h[i]*factor, 4)
            l[i] = round(l[i]*factor, 4); c[i] = round(c[i]*factor, 4)
            if i < len(v): v[i] = int(round(v[i]/factor)) if factor != 0 else v[i]

    return {"t":t,"o":o,"h":h,"l":l,"c":c,"v":v}

def _apply_splits_intraday(data, eod_rows):
    """Scale intraday O/H/L/C/V using the same cumulative split factors."""
    adj = _build_split_adjusters_from_eod_rows(eod_rows)
    if not adj: return data
    
    t,o,h,l,c,v = data["t"], data["o"], data["h"], data["l"], data["c"], data["v"]
    
    for i, ts in enumerate(t):
        # Find the first split that came after this bar
        # Use its cumulative factor (which includes all splits up to that point)
        factor = 1.0
        for (ex_ms, f) in adj:  # Forward iteration
            if ex_ms > ts:
                # This is the first split after this bar, use its cumulative factor
                factor = f
                break  # Use first split after this bar
        
        o[i] = round(o[i]*factor, 4); h[i] = round(h[i]*factor, 4)
        l[i] = round(l[i]*factor, 4); c[i] = round(c[i]*factor, 4)
        if i < len(v): v[i] = int(round(v[i]/factor)) if factor != 0 else v[i]
    
    return {"t":t,"o":o,"h":h,"l":l,"c":c,"v":v}

def _tiingo_intraday(symbol, freq, lookback_days):
    """Fetch intraday data from Tiingo IEX API"""
    url = f"https://api.tiingo.com/iex/{symbol}/prices"
    params = {
        "resampleFreq": freq,
        "afterHours": "true",
        "startDate": (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat(),
        "columns": "open,high,low,close,volume"
    }
    r = requests.get(url, headers=TIINGO_HEADERS, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()
    if not js:
        return None
    t, o, h, l, c, v = [], [], [], [], [], []
    for row in js:
        t.append(_iso_to_ms(row["date"]))
        o.append(round(row["open"], 4))
        h.append(round(row["high"], 4))
        l.append(round(row["low"], 4))
        c.append(round(row["close"], 4))
        v.append(int(row.get("volume") or 0))
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}

def _tiingo_daily(symbol, start=None, end=None):
    """Fetch daily EOD data from Tiingo API"""
    url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
    params = {"columns": "open,high,low,close,volume,adjClose"}
    
    # DEFAULT: load ~10 years if caller didn't pass dates
    if not start:
        start = (dt.date.today() - dt.timedelta(days=3650)).isoformat()
    params["startDate"] = start
    
    if end:
        params["endDate"] = end
        
    r = requests.get(url, headers=TIINGO_HEADERS, params=params, timeout=10)
    r.raise_for_status()
    js = r.json()
    if not js:
        return None
    t, o, h, l, c, v = [], [], [], [], [], []
    for row in js:
        t.append(_iso_to_ms(row["date"]))
        o.append(round(row["open"], 4))
        h.append(round(row["high"], 4))
        l.append(round(row["low"], 4))
        c.append(round(row["close"], 4))
        v.append(int(row.get("volume") or 0))
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


# ───────── Chart API Endpoints ─────────
@app.route("/api/clear-cache", methods=["POST"])
def clear_cache():
    """Clear the candles cache"""
    global CACHE
    old_size = len(CACHE)
    CACHE = {}
    return jsonify({"status": "cache cleared", "entries_cleared": old_size})

@app.route("/api/candles")
def api_candles():
    """Get candlestick data from Tiingo API with split adjustment"""
    if not TIINGO_KEY:
        return jsonify({"error": "TIINGO_API_KEY missing"}), 500

    symbol = (request.args.get("symbol") or "").upper().strip()
    tf = request.args.get("tf", "1d")
    start = request.args.get("start")   # ISO "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
    end = request.args.get("end")
    adjust = (request.args.get("adjust") or "1") != "0"  # default ON
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    route, freq, lookback = TF_MAP.get(tf, ("daily", "daily", 3650))
    cache_key = f"{symbol}:{tf}:{start or ''}:{end or ''}:adj{int(adjust)}"

    cached = _cache_get(cache_key)
    if cached:
        return jsonify(cached)

    if not _bucket_allow():
        fb = _cache_get(f"{symbol}:1d")
        if fb:
            return jsonify(fb)
        return jsonify({"error": "rate-limited; try again shortly"}), 429

    try:
        if route == "iex":
            # intraday
            url = f"https://api.tiingo.com/iex/{symbol}/prices"
            params = {
                "resampleFreq": freq,
                "afterHours": "true",
                "columns": "open,high,low,close,volume",
            }
            if start: 
                params["startDate"] = start
            else:     
                params["startDate"] = (dt.date.today()-dt.timedelta(days=lookback)).isoformat()
            if end:   
                params["endDate"] = end
            r = requests.get(url, headers=TIINGO_HEADERS, params=params, timeout=12)
            r.raise_for_status()
            js = r.json() or []
            data = {"t":[],"o":[],"h":[],"l":[],"c":[],"v":[]}
            for row in js:
                data["t"].append(_iso_to_ms(row["date"]))
                data["o"].append(round(row["open"],4))
                data["h"].append(round(row["high"],4))
                data["l"].append(round(row["low"],4))
                data["c"].append(round(row["close"],4))
                data["v"].append(int(_coalesce(row.get("volume"),0)))

            if adjust:
                # fetch EOD rows over the same (or wider) window to discover splits
                eod_params = {}
                if start: eod_params["startDate"] = start
                if end:   eod_params["endDate"]   = end
                eod = requests.get(
                    f"https://api.tiingo.com/tiingo/daily/{symbol}/prices",
                    headers=TIINGO_HEADERS, params=eod_params, timeout=10
                )
                eod.raise_for_status()
                eod_rows = eod.json() or []
                data = _apply_splits_intraday(data, eod_rows)

        else:
            # daily EOD
            url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
            params = {"columns":"open,high,low,close,volume,adjClose,splitFactor"}
            if start: 
                params["startDate"] = start
            else:     
                params["startDate"] = (dt.date.today()-dt.timedelta(days=3650)).isoformat()
            if end:   
                params["endDate"] = end
            r = requests.get(url, headers=TIINGO_HEADERS, params=params, timeout=12)
            r.raise_for_status()
            js = r.json() or []
            data = {"t":[],"o":[],"h":[],"l":[],"c":[],"v":[]}
            for row in js:
                data["t"].append(_iso_to_ms(row["date"]))
                data["o"].append(round(row["open"],4))
                data["h"].append(round(row["high"],4))
                data["l"].append(round(row["low"],4))
                data["c"].append(round(row["close"],4))
                data["v"].append(int(_coalesce(row.get("volume"),0)))
            if adjust:
                data = _apply_splits_daily(data, js)

    except requests.HTTPError as e:
        if route == "iex":
            try:
                data = _tiingo_daily(symbol, start, end)
                tf = "1d"
            except Exception:
                return jsonify({"error": "tiingo error", "detail": str(e)[:200]}), 502
        else:
            return jsonify({"error": "tiingo error", "detail": str(e)[:200]}), 502
    except Exception:
        if route == "iex":
            try:
                data = _tiingo_daily(symbol, start, end)
                tf = "1d"
            except Exception:
                return jsonify({"error": "upstream unavailable"}), 502
        else:
            return jsonify({"error": "upstream unavailable"}), 502

    if not data:
        return jsonify({"error": "no data"}), 404

    _cache_put(cache_key, data, TTL.get(tf, 300))
    return jsonify(data)

@app.route("/api/upload-chart", methods=["POST"])
def api_upload_chart():
    """Upload chart snapshot as base64 PNG"""
    data = request.get_json() or {}
    data_url = data.get("dataUrl", "")
    if not data_url.startswith("data:image/"):
        return jsonify({"error": "bad image"}), 400
    
    header, b64 = data_url.split(",", 1)
    img = Image.open(io.BytesIO(base64.b64decode(b64)))
    
    # Create charts directory if it doesn't exist
    charts_dir = "static/uploads/charts"
    os.makedirs(charts_dir, exist_ok=True)
    
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = f"{charts_dir}/chart_{ts}.png"
    img.save(path, "PNG")
    
    return jsonify({"path": "/" + path})

@app.route("/api/ai/sr-levels", methods=["POST"])
def api_sr_levels():
    """Enhanced major-levels S/R detector with prominence scoring and clustering"""
    import math
    
    try:
        j = request.get_json() or {}
        t = j.get("t", [])   # ms since epoch
        h = j.get("h", [])
        l = j.get("l", [])
        c = j.get("c", [])

        # Params (can expose via query later)
        window = int((request.args.get("w") or 5))           # pivot window
        max_levels = int((request.args.get("max") or 4))     # 3–5 recommended
        style = (request.args.get("style") or "zone")        # 'line' | 'zone'
        circle_pivots = (request.args.get("circles") or "1") == "1"
        major_only = (request.args.get("major") or "1") == "1"

        n = len(c)
        if n == 0:
            return jsonify({"level_shapes": [], "pivot_circles": [], "levels": []})

        # --- ATR for scale (simplified, period 14) ---
        tr = []
        for i in range(n):
            prev_c = c[i-1] if i>0 else c[0]
            tr.append(max(h[i]-l[i], abs(h[i]-prev_c), abs(l[i]-prev_c)))
        atrp = 14
        atr = sum(tr[-atrp:]) / max(1, min(atrp, len(tr)))
        if atr <= 0:
            atr = (max(h) - min(l)) / 100.0 or 1.0

        # --- find pivots (swing highs/lows) ---
        pivots = []  # (idx, price, kind)
        for i in range(window, n-window):
            hi = h[i] == max(h[i-window:i+window+1])
            lo = l[i] == min(l[i-window:i+window+1])
            if hi:
                pivots.append((i, h[i], "H"))
            if lo:
                pivots.append((i, l[i], "L"))

        if not pivots:
            return jsonify({"level_shapes": [], "pivot_circles": [], "levels": []})

        # --- prominence score per pivot ---
        # local excursion vs neighbors, normalized by ATR; weight recency
        scores = []
        for (i, px, kind) in pivots:
            left = max(0, i-window)
            right = min(n-1, i+window)
            nbhd_hi = max(h[left:right+1])
            nbhd_lo = min(l[left:right+1])
            if kind == "H":
                prom = (px - nbhd_lo) / atr
            else: # "L"
                prom = (nbhd_hi - px) / atr
            # recency: newer bars matter slightly more
            rec = 1.0 + 0.25 * (i / max(1, n-1))
            scores.append(((i, px, kind), prom * rec))

        # --- cluster pivots into price levels (ATR-band) ---
        # band ≈ 0.6*ATR to merge close pivots; keeps only strong clusters
        band = 0.6 * atr
        # sort by price to cluster
        piv_sorted = [p for p,_ in sorted(zip(pivots, [s for _,s in scores]), key=lambda x:x[0][1])]
        sc_by_idx = {tuple(p): s for (p, s) in scores}

        clusters = []  # each: dict(level, members, score)
        for p in piv_sorted:
            i, px, kind = p
            if not clusters:
                clusters.append({"prices":[px], "members":[p], "score":sc_by_idx[tuple(p)]})
                continue
            # attach to nearest cluster by |px - level|
            # compute current level as weighted median ~ average
            best_k = -1
            best_d = 1e18
            for k, cl in enumerate(clusters):
                lvl = sum(cl["prices"])/len(cl["prices"])
                d = abs(px - lvl)
                if d < best_d:
                    best_d, best_k = d, k
            if best_d <= band:
                cl = clusters[best_k]
                cl["prices"].append(px)
                cl["members"].append(p)
                cl["score"] += sc_by_idx[tuple(p)]
            else:
                clusters.append({"prices":[px], "members":[p], "score":sc_by_idx[tuple(p)]})

        # compress to level price & zone width
        levels = []
        for cl in clusters:
            prices = cl["prices"]
            lvl = sum(prices)/len(prices)
            spread = max(atr*0.25, (max(prices)-min(prices)) * 0.5)  # zone half-width
            levels.append({
                "level": lvl,
                "half": spread,
                "score": cl["score"] * (1 + 0.15*len(prices)),  # reward more touches
                "members": cl["members"]
            })

        # pick top-k by score (major only) or return all
        levels = sorted(levels, key=lambda x: x["score"], reverse=True)
        if major_only:
            levels = levels[:max_levels]

        # --- build Plotly shapes ---
        x0, x1 = t[0], t[-1]
        level_shapes = []
        pivot_circles = []

        for lv in levels:
            y = lv["level"]
            half = lv["half"]
            if style == "line":
                level_shapes.append({
                    "type":"line","xref":"x","yref":"y",
                    "x0":x0,"x1":x1,"y0":y,"y1":y,
                    "line":{"color":"rgba(0,120,255,0.9)","width":2}
                })
            else:  # zone
                level_shapes.append({
                    "type":"rect","xref":"x","yref":"y",
                    "x0":x0,"x1":x1,"y0":y-half,"y1":y+half,
                    "line":{"color":"rgba(0,120,255,0.0)","width":0},
                    "fillcolor":"rgba(0,120,255,0.12)"
                })

            if circle_pivots:
                # circle a few strongest member pivots within this cluster
                members = sorted(lv["members"], key=lambda m: sc_by_idx[tuple(m)], reverse=True)[:2]
                for (i, px, kind) in members:
                    # ~visual radius: few bars wide
                    dt_ms = max( (t[min(n-1, i+3)] - t[max(0, i-3)]), (t[-1]-t[0])//200 )
                    pivot_circles.append({
                        "type":"circle","xref":"x","yref":"y",
                        "x0": t[i]-dt_ms, "x1": t[i]+dt_ms,
                        "y0": px - half*0.6, "y1": px + half*0.6,
                        "line":{"color":"rgba(255,140,0,0.8)","width":2},
                        "fillcolor":"rgba(255,140,0,0.10)"
                    })

        payload = {
            "level_shapes": level_shapes,
            "pivot_circles": pivot_circles,
            "levels": [round(lv["level"], 4) for lv in levels]
        }
        return jsonify(payload)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# IMPROVED: Beginner-friendly chart explanation endpoint
@app.route('/api/ai/explain_chart_simple', methods=['POST'])
def explain_chart_simple():
    """
    Simplified, beginner-friendly chart explanation
    Returns plain English analysis without jargon
    """
    data = request.get_json() or {}
    symbol = data.get('symbol', 'UNKNOWN')
    timeframe = data.get('timeframe', '1d')
    prices = data.get('prices', {})
    candles = data.get('candles', {})
    
    current_price = prices.get('current', 0)
    high_price = prices.get('high', 0)
    low_price = prices.get('low', 0)
    
    # Calculate simple trend (based on last 20 candles)
    closes = candles.get('c', [])
    if len(closes) < 10:
        return jsonify({"error": "Not enough data"}), 400
    
    # Simple trend detection
    recent_closes = closes[-20:] if len(closes) >= 20 else closes
    first_avg = sum(recent_closes[:len(recent_closes)//2]) / (len(recent_closes)//2)
    second_avg = sum(recent_closes[len(recent_closes)//2:]) / (len(recent_closes) - len(recent_closes)//2)
    
    trend_direction = 'up' if second_avg > first_avg * 1.02 else ('down' if second_avg < first_avg * 0.98 else 'sideways')
    
    # Calculate support and resistance (simplified)
    lows = candles.get('l', [])
    highs = candles.get('h', [])
    
    # Find recent significant lows for support
    recent_lows = sorted(lows[-30:])[:5] if len(lows) >= 30 else sorted(lows)[:3]
    support_levels = list(set([round(low, 2) for low in recent_lows]))[:2]
    
    # Find recent significant highs for resistance
    recent_highs = sorted(highs[-30:], reverse=True)[:5] if len(highs) >= 30 else sorted(highs, reverse=True)[:3]
    resistance_levels = list(set([round(high, 2) for high in recent_highs]))[:2]
    
    # Generate beginner-friendly summary
    if trend_direction == 'up':
        summary = f"{symbol} is currently in an upward trend, meaning the price has been generally rising. "
        summary += f"The stock is currently at ${current_price:.2f}. "
        summary += "This upward movement suggests buyers are in control and pushing prices higher."
    elif trend_direction == 'down':
        summary = f"{symbol} is currently in a downward trend, meaning the price has been generally falling. "
        summary += f"The stock is currently at ${current_price:.2f}. "
        summary += "This downward movement suggests sellers are in control and pushing prices lower."
    else:
        summary = f"{symbol} is currently moving sideways (also called 'consolidating'), meaning it's not going up or down strongly. "
        summary += f"The stock is at ${current_price:.2f} and trading in a range. "
        summary += "This often happens when buyers and sellers are equally matched."
    
    # Create simple scenarios
    scenarios = []
    
    # Bullish scenario
    if resistance_levels:
        target = resistance_levels[0]
        scenarios.append({
            "type": "bullish",
            "title": "📈 Upward Move Scenario",
            "description": f"If {symbol} can break above ${target:.2f}, it could continue moving higher. This would show that buyers are strong enough to push through this resistance level.",
            "trigger": f"Price breaking and staying above ${target:.2f}"
        })
    
    # Bearish scenario
    if support_levels:
        target = support_levels[0]
        scenarios.append({
            "type": "bearish",
            "title": "📉 Downward Move Scenario",
            "description": f"If {symbol} falls below ${target:.2f}, it could continue moving lower. This would show that sellers are strong enough to push through this support level.",
            "trigger": f"Price breaking and staying below ${target:.2f}"
        })
    
    # Build response
    response = {
        "symbol": symbol,
        "timeframe": timeframe,
        "trend": {
            "direction": trend_direction,
            "description": "Upward" if trend_direction == 'up' else ("Downward" if trend_direction == 'down' else "Sideways")
        },
        "current_price": round(current_price, 2),
        "support_levels": sorted(support_levels),
        "resistance_levels": sorted(resistance_levels, reverse=True),
        "scenarios": scenarios,
        "summary": summary
    }
    
    return jsonify(response)


# IMPROVED: Simplified support/resistance detection
@app.route('/api/ai/sr-levels-simple', methods=['POST'])
def sr_levels_simple():
    """
    Simplified support/resistance level detection
    Returns only the 2-3 most important levels
    """
    data = request.get_json() or {}
    highs = data.get('h', [])
    lows = data.get('l', [])
    closes = data.get('c', [])
    
    if not highs or not lows or len(closes) < 20:
        return jsonify({"levels": []})
    
    current_price = closes[-1]
    
    # Simple level detection using recent price action
    lookback = min(50, len(closes))
    recent_highs = highs[-lookback:]
    recent_lows = lows[-lookback:]
    
    # Find peaks (resistance) and troughs (support)
    levels = []
    
    # Method: Find local maxima and minima
    window = 5
    for i in range(window, len(recent_highs) - window):
        # Check if this is a local high
        if recent_highs[i] == max(recent_highs[i-window:i+window+1]):
            levels.append({
                'price': round(recent_highs[i], 2),
                'type': 'resistance',
                'strength': 1
            })
        
        # Check if this is a local low
        if recent_lows[i] == min(recent_lows[i-window:i+window+1]):
            levels.append({
                'price': round(recent_lows[i], 2),
                'type': 'support',
                'strength': 1
            })
    
    # Remove duplicates (levels within 0.5% of each other)
    unique_levels = []
    for level in levels:
        is_duplicate = False
        for existing in unique_levels:
            if abs(level['price'] - existing['price']) / existing['price'] < 0.005:
                is_duplicate = True
                existing['strength'] += 1
                break
        if not is_duplicate:
            unique_levels.append(level)
    
    # Sort by strength and take top 3
    unique_levels.sort(key=lambda x: x['strength'], reverse=True)
    top_levels = unique_levels[:3]
    
    # Ensure we have at least one support below current price and one resistance above
    supports = [l for l in top_levels if l['price'] < current_price]
    resistances = [l for l in top_levels if l['price'] > current_price]
    
    final_levels = []
    if supports:
        final_levels.append(max(supports, key=lambda x: x['price']))
    if resistances:
        final_levels.append(min(resistances, key=lambda x: x['price']))
    
    # If we still don't have enough levels, add simple ones
    if len(final_levels) < 2:
        if not supports:
            final_levels.append({
                'price': round(min(recent_lows), 2),
                'type': 'support',
                'strength': 1
            })
        if not resistances:
            final_levels.append({
                'price': round(max(recent_highs), 2),
                'type': 'resistance',
                'strength': 1
            })
    
    return jsonify({"levels": final_levels})


@app.route("/tools")
def tools():
    """Tools and calculators main page"""
    return render_template("tools/index.html")


@app.route("/education")
def education():
    """Display curated educational resources."""
    return render_template("education.html")


@app.route("/pricing")
def pricing():
    """Display pricing page."""
    return render_template("pricing.html")


@app.route("/privacy")
def privacy():
    """Display privacy policy page."""
    return render_template("privacy.html")


@app.route("/tools/options-calculator", methods=["GET", "POST"])
def options_calculator():
    """Options calculator with Tradier data only"""
    
    # Check for preview mode query param
    preview_mode = request.args.get('preview') == '1'
    
    # Determine if user should see preview or full access (ignore preview for Pro users)
    show_preview = not is_pro_user()
    
    if show_preview:
        # Preview mode - show demo data
        demo_context = {
            "symbol": "AAPL",
            "current_price": 150.25,
            "stock_name": "Apple Inc.",
            "expiration_dates": ["2024-01-19", "2024-02-16", "2024-03-15"],
            "selected_date": "2024-01-19",
            "calls": [
                {"strike": 145, "bid": 6.50, "ask": 6.60, "last": 6.55, "volume": 1250, "open_interest": 3450},
                {"strike": 150, "bid": 2.15, "ask": 2.25, "last": 2.20, "volume": 890, "open_interest": 2100},
                {"strike": 155, "bid": 0.45, "ask": 0.50, "last": 0.48, "volume": 567, "open_interest": 1200}
            ],
            "puts": [
                {"strike": 145, "bid": 0.30, "ask": 0.35, "last": 0.32, "volume": 234, "open_interest": 890},
                {"strike": 150, "bid": 2.10, "ask": 2.20, "last": 2.15, "volume": 456, "open_interest": 1560},
                {"strike": 155, "bid": 6.40, "ask": 6.50, "last": 6.45, "volume": 123, "open_interest": 890}
            ],
            "options_rows": [
                {"call": {"strike": 145, "bid": 6.50, "ask": 6.60, "last": 6.55, "volume": 1250, "open_interest": 3450}, 
                 "put": {"strike": 145, "bid": 0.30, "ask": 0.35, "last": 0.32, "volume": 234, "open_interest": 890}},
                {"call": {"strike": 150, "bid": 2.15, "ask": 2.25, "last": 2.20, "volume": 890, "open_interest": 2100}, 
                 "put": {"strike": 150, "bid": 2.10, "ask": 2.20, "last": 2.15, "volume": 456, "open_interest": 1560}},
                {"call": {"strike": 155, "bid": 0.45, "ask": 0.50, "last": 0.48, "volume": 567, "open_interest": 1200}, 
                 "put": {"strike": 155, "bid": 6.40, "ask": 6.50, "last": 6.45, "volume": 123, "open_interest": 890}}
            ],
            "error_message": None
        }
        
        return render_template(
            "tools/options_calculator.html", 
            context=demo_context,
            show_pro_upsell=True,
            show_demo_data=True,
            feature_name="Options Calculator",
            limitations=[
                "Demo data only - no real-time quotes",
                "Cannot search for other symbols",
                "No P&L calculations"
            ]
        )

    # Full Pro access - original logic
    context = {
        "symbol": None,
        "current_price": None,
        "options_data": None,
        "expiration_dates": None,
        "selected_date": None,
        "stock_name": None,
        "calls": None,
        "puts": None,
        "error_message": None,  # Add error_message field
    }

    if request.method == "POST":
        symbol = request.form.get("symbol", "").upper()
        expiration_date = request.form.get("expiration_date")
        context["symbol"] = symbol

        if not symbol:
            context["error_message"] = "Please enter a stock symbol."
        else:
            try:
                # Get current price from Tradier
                current_price, description = get_stock_price_tradier(symbol)
                stock_name = description  # Use description as stock name

                if not current_price:
                    print(f"Could not get current price for {symbol} from Tradier")
                    context["error_message"] = f"Could not get current price for {symbol}. Please check the symbol and try again."
                else:
                    context["stock_name"] = stock_name
                    context["current_price"] = current_price

                    # Always fetch available expiration dates first
                    expirations = get_expiration_dates_tradier(symbol)
                    if expirations:
                        context["expiration_dates"] = expirations

                    # Require user to choose expiration before fetching chain
                    if context["expiration_dates"] and not expiration_date:
                        context["error_message"] = "Please select an expiration date before retrieving the options chain."
                    elif expiration_date:
                        # If user selected a date, fetch chain for that date
                        calls, puts, price, _ = get_options_chain_tradier(
                            symbol, expiration_date
                        )

                        if (
                            calls is not None
                            and puts is not None
                            and not calls.empty
                            and not puts.empty
                        ):
                            # Sort by strike to ensure correct ordering
                            calls_sorted = calls.sort_values("strike")
                            puts_sorted = puts.sort_values("strike")

                            context["calls"] = calls_sorted.to_dict("records")
                            context["puts"] = puts_sorted.to_dict("records")
                            context["selected_date"] = expiration_date

                            # Combine calls and puts so template can iterate safely even if lengths differ
                            options_rows = []
                            for c, p in zip_longest(context["calls"], context["puts"]):
                                options_rows.append({"call": c, "put": p})
                            context["options_rows"] = options_rows
                        else:
                            context["error_message"] = f"No options data available for {symbol}. Please check the symbol and try again."

            except Exception as e:
                print(f"Error in options calculator: {e}")
                context["error_message"] = f"Error: {str(e)}"

    return render_template(
        "tools/options_calculator.html", 
        context=context,
        show_pro_upsell=False,
        show_demo_data=False
    )


@app.route("/tools/options-pnl", methods=["POST"])
@requires_pro
def calculate_options_pnl():
    """Calculate comprehensive options P&L analysis"""
    try:
        data = request.get_json()

        option_type = data.get("option_type")  # 'call' or 'put'
        strike = float(data.get("strike"))
        current_price = float(data.get("current_price"))
        expiration_date = data.get("expiration_date")
        premium = float(data.get("premium", 0))
        quantity = int(data.get("quantity", 1))

        # Calculate days to expiration
        exp_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
        days_to_exp = (exp_date - datetime.now().date()).days
        if days_to_exp <= 0:
            return jsonify({"success": False, "error": "Option already expired"})

        # Convert to years for pricing models
        time_to_exp = days_to_exp / 365.0

        # Calculate time points starting at 100% of time remaining
        fractions = [1.0, 0.75, 0.5, 0.25, 0.0]
        # Round to whole days, remove duplicates, and sort descending
        time_points = sorted(
            {max(0, int(round(days_to_exp * f))) for f in fractions},
            reverse=True,
        )

        # Estimate implied volatility from the option's market price
        implied_vol = 0.2
        if premium > 0 and strike > 0 and days_to_exp > 0:
            est_iv = implied_volatility(
                premium, current_price, strike, time_to_exp, 0.02, option_type
            )
            if est_iv > 0:
                implied_vol = est_iv

        # Calculate price scenarios spanning below current price and
        # extending past the strike price to properly show profits for
        # far OTM options moving in-the-money
        min_price = min(current_price, strike) * 0.85
        max_price = max(current_price, strike) * 1.15
        price_steps = [round(p, 2) for p in np.linspace(min_price, max_price, 7)]
        time_slices = [
            round(t, 3)
            for t in [
                time_to_exp,
                max(time_to_exp * 0.75, 1 / 365),
                max(time_to_exp * 0.50, 1 / 365),
                max(time_to_exp * 0.25, 1 / 365),
                0,
            ]
        ]

        pnl_rows = []
        for Px in price_steps:
            row = {"stock_price": Px, "time_data": []}
            for t in time_slices:
                if t == 0:
                    # At expiration use intrinsic value instead of Black-Scholes
                    if option_type == "call":
                        theo = max(Px - strike, 0)
                    else:
                        theo = max(strike - Px, 0)
                else:
                    theo = black_scholes(Px, strike, t, 0.02, implied_vol, option_type)
                pnl = (theo - premium) * quantity * 100

                ret_pct = (pnl / (premium * quantity * 100)) * 100 if premium else 0
                row["time_data"].append({
                    "pnl": round(pnl, 2),
                    "return_percent": round(ret_pct, 2)
                })
            pnl_rows.append(row)



        # Create the analysis object
        analysis = {
            "option_info": {
                "type": option_type,
                "strike": strike,
                "premium": premium,
                "days_to_expiration": days_to_exp,
                "implied_volatility": round(implied_vol * 100, 2),
                "time_points": time_points,
                "center_price": round(current_price, 2),
                "standard_deviation": round(implied_vol * current_price, 2),
            },

            "pnl_data": pnl_rows,

        }

        return jsonify({"success": True, "analysis": analysis})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/tools/black-scholes")
def black_scholes_calculator():
    """Black-Scholes options pricing calculator"""
    return render_template("tools/black_scholes.html")


@app.route("/tools/calculate-bs", methods=["POST"])
@requires_pro
def calculate_black_scholes():
    """Calculate Black-Scholes price and Greeks"""
    try:
        data = request.get_json()

        S = float(data.get("stock_price"))
        K = float(data.get("strike_price"))
        T = float(data.get("time_to_expiration")) / 365.0
        r = float(data.get("risk_free_rate")) / 100.0
        sigma = float(data.get("volatility")) / 100.0
        option_type = data.get("option_type")

        # Calculate price
        price = black_scholes(S, K, T, r, sigma, option_type)

        # Calculate Greeks
        greeks = calculate_greeks(S, K, T, r, sigma, option_type)

        return jsonify({"success": True, "price": round(price, 4), "greeks": greeks})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/tools/stock-lookup")
def stock_lookup():
    """Stock information lookup tool"""
    return render_template("tools/stock_lookup.html")


@app.route("/search_stocks")
def search_stocks():
    """Search for stock symbols and company names - comprehensive options-enabled stocks"""
    query = request.args.get("q", "").strip().upper()

    if len(query) < 1:
        return jsonify([])

    # List of most common stocks to prioritize
    most_common = [
        "AAPL",
        "MSFT",
        "SPY",
        "TSLA",
        "NVDA",
        "AMZN",
        "GOOGL",
        "META",
        "QQQ",
        "JPM",
        "V",
        "MA",
        "BAC",
        "NFLX",
        "AMD",
        "DIS",
        "WMT",
        "BRK.B",
        "XOM",
        "UNH",
        "VTI",
        "VOO",
        "IWM",
        "DIA",
        "GS",
        "PYPL",
        "COST",
        "HD",
        "T",
        "PFE",
        "MRK",
        "KO",
        "PEP",
        "MCD",
        "NKE",
        "SBUX",
        "INTC",
        "CSCO",
        "ORCL",
        "CRM",
        "ADBE",
        "IBM",
        "TXN",
        "QCOM",
        "MU",
        "WFC",
        "C",
        "AXP",
        "MS",
        "GME",
        "AMC",
        "BB",
        "NOK",
        "F",
        "GE",
        "PLTR",
        "NIO",
        "RIOT",
        "MARA",
    ]

    # Extended stock database with name mappings
    stocks_db = [
        {"symbol": "AAPL", "name": "Apple Inc"},
        {"symbol": "MSFT", "name": "Microsoft Corporation"},
        {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust"},
        {"symbol": "TSLA", "name": "Tesla Inc"},
        {"symbol": "NVDA", "name": "NVIDIA Corporation"},
        {"symbol": "AMZN", "name": "Amazon.com Inc"},
        {"symbol": "GOOGL", "name": "Alphabet Inc Class A"},
        {"symbol": "META", "name": "Meta Platforms Inc"},
        {"symbol": "QQQ", "name": "Invesco QQQ Trust"},
        {"symbol": "JPM", "name": "JPMorgan Chase & Co"},
        {"symbol": "V", "name": "Visa Inc"},
        {"symbol": "MA", "name": "Mastercard Incorporated"},
        {"symbol": "BAC", "name": "Bank of America Corporation"},
        {"symbol": "NFLX", "name": "Netflix Inc"},
        {"symbol": "AMD", "name": "Advanced Micro Devices Inc"},
        {"symbol": "DIS", "name": "The Walt Disney Company"},
        {"symbol": "WMT", "name": "Walmart Inc"},
        {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc Class B"},
        {"symbol": "XOM", "name": "Exxon Mobil Corporation"},
        {"symbol": "UNH", "name": "UnitedHealth Group Incorporated"},
        {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF"},
        {"symbol": "VOO", "name": "Vanguard S&P 500 ETF"},
        {"symbol": "IWM", "name": "iShares Russell 2000 ETF"},
        {"symbol": "DIA", "name": "SPDR Dow Jones Industrial Average ETF Trust"},
        {"symbol": "GS", "name": "The Goldman Sachs Group Inc"},
        {"symbol": "PYPL", "name": "PayPal Holdings Inc"},
        {"symbol": "COST", "name": "Costco Wholesale Corporation"},
        {"symbol": "HD", "name": "The Home Depot Inc"},
        {"symbol": "T", "name": "AT&T Inc"},
        {"symbol": "PFE", "name": "Pfizer Inc"},
        {"symbol": "MRK", "name": "Merck & Co Inc"},
        {"symbol": "KO", "name": "The Coca-Cola Company"},
        {"symbol": "PEP", "name": "PepsiCo Inc"},
        {"symbol": "MCD", "name": "McDonald's Corporation"},
        {"symbol": "NKE", "name": "NIKE Inc"},
        {"symbol": "SBUX", "name": "Starbucks Corporation"},
        {"symbol": "INTC", "name": "Intel Corporation"},
        {"symbol": "CSCO", "name": "Cisco Systems Inc"},
        {"symbol": "ORCL", "name": "Oracle Corporation"},
        {"symbol": "CRM", "name": "Salesforce Inc"},
        {"symbol": "ADBE", "name": "Adobe Inc"},
        {"symbol": "IBM", "name": "International Business Machines Corporation"},
        {"symbol": "TXN", "name": "Texas Instruments Incorporated"},
        {"symbol": "QCOM", "name": "QUALCOMM Incorporated"},
        {"symbol": "MU", "name": "Micron Technology Inc"},
        {"symbol": "WFC", "name": "Wells Fargo & Company"},
        {"symbol": "C", "name": "Citigroup Inc"},
        {"symbol": "AXP", "name": "American Express Company"},
        {"symbol": "MS", "name": "Morgan Stanley"},
        {"symbol": "GME", "name": "GameStop Corp"},
        {"symbol": "AMC", "name": "AMC Entertainment Holdings Inc"},
        {"symbol": "BB", "name": "BlackBerry Limited"},
        {"symbol": "NOK", "name": "Nokia Corporation"},
        {"symbol": "F", "name": "Ford Motor Company"},
        {"symbol": "GE", "name": "General Electric Company"},
        {"symbol": "PLTR", "name": "Palantir Technologies Inc"},
        {"symbol": "NIO", "name": "NIO Inc"},
        {"symbol": "RIOT", "name": "Riot Platforms Inc"},
        {"symbol": "MARA", "name": "Marathon Digital Holdings Inc"},
        {"symbol": "AA", "name": "Alcoa Corp"},
        {"symbol": "BA", "name": "Boeing Co"},
    ]

    # Find matches
    matches = []

    # Prioritize exact symbol matches from most common list
    for stock in stocks_db:
        if stock["symbol"] in most_common and stock["symbol"].startswith(query):
            matches.append(stock)

    # Add partial symbol matches
    for stock in stocks_db:
        if stock not in matches and query in stock["symbol"]:
            matches.append(stock)

    # Add name matches
    for stock in stocks_db:
        if stock not in matches and query in stock["name"].upper():
            matches.append(stock)

    # Limit results to 15 for performance
    matches = matches[:15]

    # Format for jQuery UI autocomplete
    results = []
    for stock in matches:
        results.append(
            {"label": f"{stock['symbol']} — {stock['name']}", "value": stock["symbol"]}
        )

    return jsonify(results)


@app.route("/test-options/<symbol>")
def test_options(symbol):
    """Test endpoint to debug options chain issues"""
    symbol = symbol.upper()
    print(f"Testing options for {symbol}")

    # Test Tradier API
    result = {
        "symbol": symbol,
        "tradier_configured": TRADIER_API_TOKEN != "your_tradier_token_here"
        and TRADIER_API_TOKEN,
        "tradier_result": None,
        "errors": [],
    }

    # Test Tradier API
    try:
        calls, puts, price, expirations = get_options_chain_tradier(symbol)
        result["tradier_result"] = {
            "success": calls is not None and puts is not None,
            "calls_count": len(calls) if calls is not None else 0,
            "puts_count": len(puts) if puts is not None else 0,
            "current_price": price,
            "expiration_count": len(expirations) if expirations else 0,
        }
    except Exception as e:
        result["errors"].append(f"Tradier error: {str(e)}")

    return jsonify(result)


def send_password_reset_email(user):
    token = user.get_reset_password_token()
    msg = Message('Reset Your Password',
                  recipients=[user.email])
    msg.body = f'''To reset your password, visit the following link:
{url_for('reset_password', token=token, _external=True)}

If you did not make this request then simply ignore this email and no changes will be made.
'''
    mail.send(msg)

@app.route("/reset_password_request", methods=["GET", "POST"])
def reset_password_request():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    form = ResetPasswordRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            send_password_reset_email(user)
        flash("If an account exists with that email, you will receive password reset instructions.", "info")
        return redirect(url_for("login"))
    return render_template("reset_password_request.html", form=form, hide_sidebar=True)

@app.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    
    user = User.verify_reset_password_token(token)
    if not user:
        flash("The password reset link is invalid or has expired.", "danger")
        return redirect(url_for("reset_password_request"))
    
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.reset_token = None
        user.reset_token_expiration = None
        db.session.commit()
        flash("Your password has been reset.", "success")
        return redirect(url_for("login"))
    
    return render_template("reset_password.html", form=form, hide_sidebar=True)


# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template("500.html"), 500


# ───────── In-House Education Routes ─────────
@app.route("/education/greeks")
def education_greeks():
    """Understanding the Greeks page"""
    return render_template("education/greeks.html")

@app.route("/education/strategies")
def education_strategies():
    """Options Strategies Guide page"""
    return render_template("education/strategies.html")

@app.route("/education/risk-management")
def education_risk_management():
    """Risk Management Guide page"""
    return render_template("education/risk_management.html")

@app.route("/education/position-sizing")
def education_position_sizing():
    """Position Sizing for Options page"""
    return render_template("education/position_sizing.html")

@app.route("/education/implied-volatility")
def education_implied_volatility():
    """Implied Volatility Guide page"""
    return render_template("education/implied_volatility.html")

@app.route("/education/advanced-options")
def education_advanced_options():
    """Advanced Options Education page"""
    return render_template("education/advanced_options.html")

# ──────────────────────────────────────────────────
# BRIEF ROUTES
# ──────────────────────────────────────────────────

@app.route("/brief/latest")
def latest_brief():
    """Serve the latest market brief from static files"""
    from pathlib import Path
    
    # Resolve paths relative to the Flask app root to avoid CWD issues
    base_dir = Path(current_app.root_path)
    brief_file = base_dir / 'static' / 'uploads' / 'brief_latest.html'
    date_file = base_dir / 'static' / 'uploads' / 'brief_latest_date.txt'
    
    if not brief_file.exists():
        return "No brief available", 404
    
    try:
        with open(brief_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # Date is optional; kept for potential future use
        if date_file.exists():
            try:
                _ = date_file.read_text(encoding='utf-8').strip()
            except Exception:
                pass
        
        return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
    except Exception as e:
        return f"Error reading brief: {str(e)}", 500

@app.route("/brief/weekly")
def weekly_brief():
    """Serve the latest weekly brief from static files, fallback to daily if missing."""
    from pathlib import Path
    
    base_dir = Path(current_app.root_path)
    weekly_file = base_dir / 'static' / 'uploads' / 'brief_weekly_latest.html'
    daily_file = base_dir / 'static' / 'uploads' / 'brief_latest.html'
    
    target_file = weekly_file if weekly_file.exists() else (daily_file if daily_file.exists() else None)
    if target_file is None:
        return "No weekly brief available", 404
    
    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # If we fell back to daily, adjust the heading so the UI reads correctly
        if target_file == daily_file:
            html_content = html_content.replace('Morning Market Brief', 'Weekly Market Brief')
        
        return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
    except Exception as e:
        return f"Error reading weekly brief: {str(e)}", 500

# --- Weekly brief page & admin trigger ---

@app.route("/weekly-brief")
def weekly_brief_public():
    """
    Serves the last generated weekly brief HTML.
    """
    try:
        with open("static/uploads/brief_weekly_latest.html", "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        html = "<p>No weekly brief has been generated yet.</p>"
    return html

@app.route("/admin/send_weekly_brief")
def admin_send_weekly_brief():
    """
    Triggers the weekly brief generation.
    Only runs on Sunday (NY). To override, pass ?force=1
    """
    from market_brief_generator import send_weekly_market_brief_to_subscribers
    force = request.args.get("force") == "1"
    path_or_msg = send_weekly_market_brief_to_subscribers(force=force)
    return jsonify({"result": path_or_msg})

@app.route("/admin/email-diagnostics")
@login_required
def email_diagnostics():
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403

    out = {}
    try:
        # Config presence (no secrets)
        out["MAIL_SERVER"] = bool(app.config.get("MAIL_SERVER"))
        out["MAIL_PORT"] = app.config.get("MAIL_PORT")
        out["MAIL_USE_TLS"] = app.config.get("MAIL_USE_TLS")
        out["MAIL_USE_SSL"] = app.config.get("MAIL_USE_SSL")
        out["MAIL_DEFAULT_SENDER"] = bool(app.config.get("MAIL_DEFAULT_SENDER"))
        out["MAIL_SUPPRESS_SEND"] = app.config.get("MAIL_SUPPRESS_SEND")

        # Popular providers
        out["SENDGRID_KEY"] = bool(os.getenv("SENDGRID_KEY"))
        out["MAILGUN_CONFIG"] = bool(os.getenv("MAILGUN_DOMAIN") and os.getenv("MAILGUN_API_KEY"))
        out["SES_CONFIG"] = bool(os.getenv("AWS_SES_ACCESS_KEY_ID") and os.getenv("AWS_SES_SECRET_ACCESS_KEY"))

        # Subscriber counts
        from models import MarketBriefSubscriber, db
        out["subs_total"] = db.session.query(MarketBriefSubscriber).count()
        try:
            out["subs_confirmed"] = db.session.query(MarketBriefSubscriber).filter_by(confirmed=True).count()
        except Exception:
            out["subs_confirmed"] = "unknown"
        try:
            out["subs_unsubscribed"] = db.session.query(MarketBriefSubscriber).filter_by(unsubscribed=True).count()
        except Exception:
            out["subs_unsubscribed"] = "unknown"

        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/email-test", methods=["POST"])
@login_required
def email_test():
    """Sends a minimal test email through the SAME function used by daily sends."""
    if current_user.email != 'support@optionsplunge.com':
        return jsonify({"error": "Access denied"}), 403

    test_to = request.json.get("to") if request.is_json else None
    if not test_to:
        test_to = os.getenv("TEST_EMAIL")
    if not test_to:
        return jsonify({"error": "Provide JSON {'to': 'you@example.com'} or set TEST_EMAIL"}), 400

    try:
        # Reuse the direct sender your daily job uses
        from emails import send_daily_brief_direct
        html = "<h3>OptionsPlunge Email Health Check</h3><p>If you received this, SMTP/provider config works.</p>"
        ok = send_daily_brief_direct(html)  # Fallback signature without recipients
        return jsonify({"sent": bool(ok), "to": test_to})
    except Exception as e:
        return jsonify({"sent": False, "error": str(e)}), 500


@app.route("/verify_email/<token>")
def verify_email(token):
    """Verify email with token from email link"""
    if current_user.is_authenticated and current_user.email_verified:
        flash("Your email is already verified!", "info")
        return redirect(url_for("dashboard"))
    
    # Verify the token and get user
    user = User.verify_email_token(token)
    
    if not user:
        flash("Invalid or expired verification link. Please request a new one.", "error")
        return redirect(url_for("resend_verification"))
    
    # Mark email as verified
    user.email_verified = True
    user.email_verification_token = None
    user.token_generated_at = None
    db.session.commit()
    
    # Log user in if not already
    if not current_user.is_authenticated:
        login_user(user)
    
    flash("Email verified successfully! 🎉 Welcome to Options Plunge!", "success")
    return redirect(url_for("dashboard"))


@app.route("/resend_verification", methods=["GET", "POST"])
@login_required
def resend_verification():
    """Resend email verification"""
    if current_user.email_verified:
        flash("Your email is already verified! 🎉", "success")
        return redirect(url_for("dashboard"))
    
    if request.method == "POST":
        try:
            from emails import send_verification_email
            token = current_user.generate_email_verification_token()
            db.session.commit()
            send_verification_email(current_user, token)
            
            flash("Verification email sent! Check your inbox (and spam folder).", "success")
            
            # Return JSON if AJAX request
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": True, "message": "Verification email sent!"})
            
        except Exception as e:
            app.logger.error(f"Failed to resend verification email to {current_user.email}: {e}")
            flash("Failed to send verification email. Please try again later.", "error")
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({"success": False, "error": "Failed to send email"}), 500
    
    # Redirect back to previous page or dashboard (validate referrer for security)
    referrer = request.referrer
    if referrer and is_safe_url(referrer):
        return redirect(referrer)
    return redirect(url_for("dashboard"))


@app.route("/verify_email_required")
@login_required
def verify_email_required():
    """Show email verification required page"""
    if current_user.email_verified:
        return redirect(url_for("dashboard"))
    return render_template("verify_email_required.html")


if __name__ == "__main__":
    app.run(debug=True)
