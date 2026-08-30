from __future__ import annotations
import asyncio,json
from arbitrage_terminal.domain.models import AIMode
class TerminalService:
    def __init__(self,repo,scanner,ai,settings,exchanges=None):
        self.repo=repo; self.scanner=scanner; self.ai=ai; self.settings=settings
        self.exchanges=exchanges if exchanges is not None else getattr(scanner,'exchanges',{})
    async def ensure_user(self,user,email=None): await self.repo.ensure_user(user.id,user.username,email)
    async def get_user(self,user_id): return await self.repo.user(user_id)
    async def set_exchanges(self,user_id,exchanges): await self.repo.set_exchanges(user_id,exchanges)
    async def set_ai_mode(self,user_id,mode): await self.repo.set_ai_mode(user_id,mode)
    async def run_scan(self,user_id):
        row=await self.repo.user(user_id);selected=json.loads(row['exchanges'] or '[]');snap=await self.scanner.scan(user_id,selected,self.repo.filters_from_row(row));await self.repo.save_scan(snap);return snap
    async def history(self,user_id): return await self.repo.history(user_id)
    async def scan(self,user_id,scan_id): return await self.repo.get_scan(user_id,scan_id)
    async def ai_scan_analysis(self,user_id,snap):
        row=await self.repo.user(user_id);mode=AIMode(row['result_mode'] or 'off');payload={'scan_id':snap['scan_id'],'opportunities':snap['opportunities'],'healthy_exchanges':snap['healthy_exchanges'],'failed_exchanges':snap['failed_exchanges'],'warnings':snap['warnings']};return await self.ai.analyze(mode,'Analyze only supplied deterministic scan data. Never invent market facts.',payload)
    async def order_route(self,user_id,scan_id,index):
        snap=await self.repo.get_scan(user_id,scan_id);opportunities=snap.get('opportunities',[])
        if index<0 or index>=len(opportunities): raise ValueError('Invalid opportunity')
        o=opportunities[index];buy=self.exchanges.get(str(o['buy_exchange']).lower());sell=self.exchanges.get(str(o['sell_exchange']).lower())
        if not buy or not sell: raise RuntimeError('Required exchange adapter is unavailable')
        buy_book,sell_book=await asyncio.gather(buy.get_orderbook(o['symbol'],limit=10),sell.get_orderbook(o['symbol'],limit=10),return_exceptions=True)
        return {'symbol':o['symbol'],'buy_exchange':o['buy_exchange'],'sell_exchange':o['sell_exchange'],'buy':buy_book,'sell':sell_book}
