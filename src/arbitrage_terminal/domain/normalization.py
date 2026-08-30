from __future__ import annotations
import re
from .models import MarketType
SEPARATORS=re.compile(r"[-_:\s]+")
def normalize_symbol(symbol:str,market_type:MarketType=MarketType.SPOT):
    raw=str(symbol or "").strip().upper(); raw=raw.split(":",1)[0]; compact=SEPARATORS.sub("/",raw).strip("/")
    if "/" in compact: base,quote=compact.split("/",1)
    else:
        quotes=("USDT","USDC","FDUSD","BUSD","BTC","ETH","EUR","USD")
        found=next((q for q in quotes if compact.endswith(q) and len(compact)>len(q)),None)
        if not found: raise ValueError(f"unparseable symbol: {symbol}")
        base,quote=compact[:-len(found)],found
    if not base or not quote or not base.isalnum() or not quote.isalnum(): raise ValueError(f"invalid symbol: {symbol}")
    return f"{base}/{quote}",base,quote,market_type
