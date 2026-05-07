import os
import aiohttp
import asyncio
from datetime import datetime
import pytz
import logging

logger = logging.getLogger(__name__)

TZ = pytz.timezone(os.getenv("TZ", "America/New_York"))
HOST = os.getenv("TRADIER_HOST", "https://api.tradier.com")
HEADERS = {
    "Authorization": f"Bearer {os.getenv('TRADIER_API_TOKEN')}",
    "Accept": "application/json"
}

class TradierProvider:
    """Thin wrapper around Tradier endpoints we need for gap scans."""

    def __init__(self, rps: int = 90):
        # simple throttle; adjust if you see 429s
        self.sem = asyncio.Semaphore(rps)

    async def _get(self, session, url, params):
        async with self.sem:
            async with session.get(url, params=params, headers=HEADERS, timeout=15) as r:
                r.raise_for_status()
                return await r.json()

    @staticmethod
    def _et(dt_aware: datetime) -> str:
        return dt_aware.strftime("%Y-%m-%d %H:%M")

    async def minute_window(self, symbols, start_ts: int, end_ts: int, prepost: bool = True):
        """
        /v1/markets/timesales: 1m bars for a time window.
        prepost=True -> session_filter='all' (includes extended hours).
        Returns: {symbol: [{t,o,h,l,c,v}, ...]}
        """
        out = {}
        start = datetime.fromtimestamp(start_ts, TZ)
        end = datetime.fromtimestamp(end_ts, TZ)
        params_base = {
            "interval": "1min",
            "start": self._et(start),
            "end": self._et(end),
            "session_filter": "all" if prepost else "open"
        }
        url = f"{HOST}/v1/markets/timesales"
        
        async with aiohttp.ClientSession() as session:
            async def fetch(sym: str):
                p = dict(params_base)
                p["symbol"] = sym
                try:
                    js = await self._get(session, url, p)
                    data = (js.get("series") or {}).get("data") or []
                    out[sym] = [{
                        "t": x["timestamp"], "o": x["open"], "h": x["high"],
                        "l": x["low"], "c": x["close"], "v": x["volume"]
                    } for x in data]
                except Exception as e:
                    logger.warning(f"Failed to fetch minute data for {sym}: {e}")
                    out[sym] = []
            
            await asyncio.gather(*[fetch(s) for s in symbols])
        return out

    async def prev_close(self, symbols):
        """
        /v1/markets/quotes: batch quote lookup.
        We use the 'prevclose' field for gap % baseline.
        Returns: {symbol: prevclose}
        """
        url = f"{HOST}/v1/markets/quotes"
        out = {}
        chunks = [symbols[i:i+200] for i in range(0, len(symbols), 200)]
        
        async with aiohttp.ClientSession() as session:
            async def fetch(batch):
                try:
                    js = await self._get(session, url, {"symbols": ",".join(batch)})
                    q = js.get("quotes", {}).get("quote", [])
                    if isinstance(q, dict):
                        q = [q]
                    for it in q:
                        out[it["symbol"]] = it.get("prevclose")
                except Exception as e:
                    logger.warning(f"Failed to fetch quotes for batch: {e}")
            
            await asyncio.gather(*[fetch(c) for c in chunks])
        return out

    async def news(self, symbol: str, since_hours: int = 24):
        """Use Finnhub for a quick headline to display as 'why'."""
        import datetime as dt
        key = os.getenv("FINNHUB_API_KEY")
        if not key:
            return []
        
        today = datetime.now(TZ).date()
        url = "https://finnhub.io/api/v1/company-news"
        params = {"symbol": symbol, "from": str(today), "to": str(today), "token": key}
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, timeout=10) as r:
                    r.raise_for_status()
                    return await r.json()
            except Exception as e:
                logger.warning(f"Failed to fetch news for {symbol}: {e}")
                return []
