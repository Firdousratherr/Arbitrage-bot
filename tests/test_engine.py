from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.arbitrage.engine import pair_opportunity
def t(ex,p):return Ticker(ex,'BTC/USDT','BTC','USDT',p-1,p,100000,datetime.now(timezone.utc))
def test_net_profit_separate():
    o=pair_opportunity(t('a',100),t('b',102),.1,.1);assert o.raw_gap>0;assert o.estimated_net_profit<o.raw_gap
