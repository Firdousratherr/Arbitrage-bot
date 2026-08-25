from __future__ import annotations

from typing import Iterable, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .scan_diagnostics import get_last_scan_diagnostics, get_last_scan_snapshot

TOP = "╭────────────────────────╮"
BOTTOM = "╰────────────────────────╯"
SECTION_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
THIN_SEPARATOR = "────────────────────"

def _compact_number(value: float | int, digits: int = 8) -> str:
    if abs(float(value)) >= 1_000_000:return f"{float(value):,.0f}"
    if abs(float(value)) >= 1_000:return f"{float(value):,.2f}"
    return format(float(value), f".{digits}f").rstrip("0").rstrip(".")

def _safe_text(value: object) -> str:return str(value).strip() if value is not None else ""
def _escape_html(text: str)->str:return text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
def _panel(title: str, subtitle: str|None=None)->list[str]:
    lines=[TOP,f"│  {title}"]
    if subtitle:lines.append(f"│  {subtitle}")
    lines.extend([BOTTOM, ""]);return lines

def format_error(message: str, action: str="Try again in a moment")->str:return f"❌ {_safe_text(message)[:220]}\n🔧 {_safe_text(action)[:220]}"
def format_success(message: str)->str:return f"╭─ ✅ SUCCESS\n│ {_safe_text(message)[:240]}\n╰────────────────────"

def format_scan_count(count: int)->str:
    diagnostics=get_last_scan_diagnostics();snapshot=get_last_scan_snapshot();summary=snapshot.get("summary",{}) or {}
    lines=_panel("🔍 SCAN COMPLETE","Live market comparison finished")
    lines.append(f"✨ Found {count} opportunities" if count else "📭 No opportunities found")
    lines.append(f"✨ Opportunities shown : <b>{count}</b>")
    if summary:
        selected=summary.get("selected_exchanges") or []
        statuses=summary.get("exchange_status") or {}
        healthy=sum(1 for value in statuses.values() if value.get("status") in {"ok","partial"})
        lines.extend([
            "", "📊 <b>SCAN DIAGNOSTICS</b>", THIN_SEPARATOR,
            f"🌐 Selected exchanges: <b>{len(selected)}</b>",
            f"🛰️ Exchange data: <b>{healthy}/{len(selected)}</b>",
            f"🪙 Common listed markets: <b>{summary.get('common_listed_markets',0)}</b>",
            f"📡 Common markets with bid/ask: <b>{summary.get('common_markets',0)}</b>",
            f"⚡ Positive spreads: <b>{summary.get('positive_spreads',0)}</b>",
            f"🎯 Detected: <b>{summary.get('opportunities_detected',0)}</b>",
            f"🚫 Filtered: <b>{summary.get('opportunities_filtered',0)}</b>",
            f"✅ Returned: <b>{summary.get('opportunities_returned',count)}</b>",
        ])
        if summary.get("opportunities_filtered") and not count:
            lines.append("💡 Positive spreads were found but rejected by configured filters.")
    if count:lines.append("💡 Open a result below for full analysis.")
    if diagnostics:
        lines.extend(["","⚠️ DATA QUALITY",THIN_SEPARATOR]);visible=min(8,len(diagnostics))
        for item in diagnostics[:visible]:
            symbol=_escape_html(str(item.get("symbol","unknown")));gaps=item.get("gaps",{}) or {};gap_text="; ".join(f"{_escape_html(str(e))}: {_escape_html(str(r))[:100]}" for e,r in gaps.items());lines.append(f"• <b>{symbol}</b> — {gap_text}")
        if len(diagnostics)>visible:lines.append(f"• … and {len(diagnostics)-visible} more symbols with data gaps")
    return "\n".join(lines)

def format_opportunity_card(opportunity, identifier: str, card_number: int|str|None=None, tag: str|None=None, trade_size: float|None=None, title: str|None=None)->str:
    if isinstance(card_number,str) and tag is None and title is None:title,card_number=card_number,None
    if title is not None:tag=title
    if tag is None:
        if card_number is not None:
            rank=["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"];n=int(card_number);tag=f"{rank[n-1] if n<=10 else f'#{n}'} 🔍 SCAN RESULT"
        elif getattr(opportunity,"loose_mode",False):tag="⚠️ LOOSE-MODE OPPORTUNITY"
        elif getattr(opportunity,"net_profit",0)>=3.0:tag="🚨 HIGH-MARGIN ARBITRAGE"
        else:tag="🔴 LIVE ARBITRAGE"
    metadata=getattr(opportunity,"metadata",{}) or {};tv=metadata.get("transfer_verification");bt=metadata.get("buy_transfer",{});st=metadata.get("sell_transfer",{})
    if tv=="loose_mode":transfer_line="⚠️ Transfer checks skipped — verify manually"
    elif tv=="not_verified":transfer_line="🛡️ Unverified — manual check recommended"
    else:
        matching=metadata.get("matching_network","");bn=[n.get("network") for n in bt.get("networks",[]) if n.get("deposit")];sn=[n.get("network") for n in st.get("networks",[]) if n.get("withdraw")];transfer_line=f"✅ Verified route • {matching}" if matching and bn and sn else "⏳ Transfer verification pending"
    if trade_size and trade_size>0:
        gross=trade_size*opportunity.raw_spread/100;fees=trade_size*(opportunity.raw_spread-opportunity.net_profit)/100;net=trade_size*opportunity.net_profit/100;gross_text=f"${_compact_number(gross,4)}";fees_text=f"${_compact_number(fees,4)}";net_text=f"${_compact_number(net,4)}";size_text=f"${_compact_number(trade_size,4)}"
    else:gross_text=f"{opportunity.raw_spread:.2f}%";fees_text=f"{opportunity.raw_spread-opportunity.net_profit:.2f}%";net_text=f"{opportunity.net_profit:.2f}%";size_text="$1,000"
    coin=opportunity.symbol.split("/")[0];coin_amount=trade_size/opportunity.buy_price if trade_size and opportunity.buy_price>0 else 1000/opportunity.buy_price if opportunity.buy_price>0 else 0
    lines=_panel(tag,f"<b>{_escape_html(opportunity.symbol)}</b> • live market data");lines.extend(["🟢 <b>BUY HERE</b>",f"   🌐 {_escape_html(opportunity.buy_exchange)}  •  <b>${_compact_number(opportunity.buy_price,8)}</b>","","🔴 <b>SELL HERE</b>",f"   🌐 {_escape_html(opportunity.sell_exchange)}  •  <b>${_compact_number(opportunity.sell_price,8)}</b>","",SECTION_SEPARATOR,"📊 Profit Breakdown",f"   📈 Gross       {gross_text}",f"   💸 Fees        − {fees_text}",f"   🚀 <b>Net        {net_text}</b>",f"   🎯 Spread      <b>{opportunity.net_profit:.2f}%</b>","","📋 <b>TRADE DETAILS</b>",f"   💵 Size        {size_text}",f"   🪙 Amount      {_compact_number(coin_amount,6)} {coin}",f"   🛡️ Transfer    {transfer_line}",BOTTOM]);return "\n".join(lines).replace("\n\n","\n")

def opportunity_buttons(identifier: str)->InlineKeyboardMarkup:return InlineKeyboardMarkup([[InlineKeyboardButton("📖  Order Book & Analysis",callback_data=f"details:{identifier}")],[InlineKeyboardButton("🎮  Paper Trade",callback_data=f"paper:{identifier}")]])
def format_background_alert(opportunity,identifier: str)->str:return format_opportunity_card(opportunity,identifier)

def format_scan_summary(opportunities: Sequence[object], *, exchange_count:int, opportunities_found:int, matching_selected:int, results_shown:int)->str:
    lines=["🔍 <b>SCAN COMPLETE</b>","",f"✨ {len(opportunities)} opportunities ready to review." if opportunities else "📭 No opportunities found",f"🌐 {exchange_count} exchanges checked.",f"📊 {opportunities_found} found • {matching_selected} matched filters • {results_shown} shown."]
    if opportunities:lines.extend(["","🏆 <b>TOP OPPORTUNITIES</b>"]+[f"{['1️⃣','2️⃣','3️⃣'][i-1] if i<=3 else f'{i}.'} {_escape_html(str(getattr(o,'symbol','unknown')))} — {getattr(o,'net_profit',0):.2f}% net" for i,o in enumerate(list(opportunities)[:3],1)])
    return "\n".join(lines)

def format_order_book(levels: Iterable[Sequence[float]], *, title:str)->str:
    rows=[f"{float(l[0]):.8f} × {float(l[1]):.6f}" for l in list(levels)[:5] if len(l)>=2];body="\n".join(rows) if rows else "Unavailable";return f"{title}{body}" if title else body

def format_opportunity_details(row:dict,buy_fill:float,sell_fill:float,buy_fee:float,sell_fee:float,gross_profit:float,net_profit:float,buy_slippage:float,sell_slippage:float,transfer_text:str,buy_book:Sequence[Sequence[float]],sell_book:Sequence[Sequence[float]])->str:
    lines=_panel("📖 DETAILS • ORDER BOOK",f"<b>{_escape_html(row['symbol'])}</b> • execution analysis");lines.extend([f"🟢 <b>{_escape_html(row['buy_exchange'])} — ASKS</b>",format_order_book(buy_book,title=""),f"🔴 <b>{_escape_html(row['sell_exchange'])} — BIDS</b>",format_order_book(sell_book,title=""),SECTION_SEPARATOR,"🧮 <b>EXECUTION ANALYSIS</b>",f"   💵 Gross profit  ${_compact_number(gross_profit,4)}",f"   💸 Buy fee       {buy_fee*100:.4f}%",f"   💸 Sell fee      {sell_fee*100:.4f}%",f"   🚀 <b>Net profit    ${_compact_number(net_profit,4)}</b>",f"   📉 Buy slippage  {buy_slippage:.2f}%",f"   📈 Sell slippage {sell_slippage:.2f}%",f"   💧 Buy volume    {_compact_number(row['volume_buy'])}",f"   💧 Sell volume   {_compact_number(row['volume_sell'])}",SECTION_SEPARATOR,"🛡️ <b>TRANSFER STATUS</b>",transfer_text.replace("\n"," • "),BOTTOM]);return "\n".join(lines).replace("\n\n","\n").strip()

def format_paper_trade(opportunity, *, buy_price:float,sell_price:float,size:float,expected_gross:float,estimated_net:float,profit:float)->str:
    icon="🟢" if profit>=0 else "🔴";lines=_panel("🎮 PAPER TRADE OPENED","Simulation only • no real funds used");lines.extend([f"🪙 <b>{_escape_html(opportunity.symbol)}</b>",f"🟢 {_escape_html(opportunity.buy_exchange)}  ${_compact_number(buy_price,8)}","        ↓  simulated route",f"🔴 {_escape_html(opportunity.sell_exchange)}  ${_compact_number(sell_price,8)}","",SECTION_SEPARATOR,f"💵 Position size  ${_compact_number(size,6)}",f"📈 Gross result   ${_compact_number(expected_gross,6)}",f"{icon} <b>Net P/L         ${_compact_number(estimated_net,6)}</b>",BOTTOM]);return "\n".join(lines)

def format_status_message(vip_status:str,vip_expiry:str|None,exchanges:list[str],loose_mode:bool,paused:bool,filters:dict)->str:
    expiry=f" • until {vip_expiry[:10]}" if vip_expiry else "";lines=_panel("👤 ACCOUNT CENTER","Your arbitrage workspace");lines.extend([f"💎 VIP       {vip_status}{expiry}",f"🌐 Exchanges  {', '.join(exchanges) if exchanges else 'No exchanges selected'}",f"⏯ <b>Alerts</b>    {'PAUSED' if paused else 'LIVE'}",f"⚠️ <b>Loose mode</b> {'ON' if loose_mode else 'OFF'}","",SECTION_SEPARATOR,"🎛️ <b>ACTIVE FILTERS</b>",f"📈 Profit       {filters.get('min_profit',0)}% → {filters.get('max_profit',100)}%",f"📊 Spread       {filters.get('min_spread',0)}% → {filters.get('max_spread',100)}%",f"💧 Min volume   ${_compact_number(filters.get('min_volume',10000))}",f"⏱️ Cooldown     {filters.get('alert_cooldown',300)}s",BOTTOM]);return "\n".join(lines)

def format_filters_message(filters:dict)->str:
    watchlist=', '.join(filters.get('watchlist',[])) if filters.get('watchlist') else 'All pairs';blacklist=', '.join(filters.get('blacklist',[])) if filters.get('blacklist') else 'None';lines=_panel("🎛 YOUR FILTERS","Tune what counts as an opportunity");lines.extend([f"📈 Profit range  {filters.get('min_profit',0)}% → {filters.get('max_profit',100)}%",f"📊 Spread range  {filters.get('min_spread',0)}% → {filters.get('max_spread',100)}%",f"💧 Volume        ≥ ${_compact_number(filters.get('min_volume',10000))}",f"👁 Watchlist      {watchlist}",f"🚫 Blacklist      {blacklist}",f"🕒 Cooldown       {filters.get('alert_cooldown',300)} sec",f"📋 Max results    {filters.get('max_results',10)}",f"⏸ Paused          {'Yes' if filters.get('paused') else 'No'}",f"⚠️ Loose mode     {'On' if filters.get('loose_mode') else 'Off'}","",THIN_SEPARATOR,"Use /setminprofit, /setmaxprofit, /setminspread, /setmaxspread,","/setminvolume, /watchlist, /blacklist and /setalertfreq.",BOTTOM]);return "\n".join(lines)

def format_leaderboard(rows:list,period:str,user_rank:int|None,user_profit:float|None)->str:
    if not rows:return "🏆 <b>LEADERBOARD</b>\n"+SECTION_SEPARATOR+"\n📭 No paper trades yet."
    ranks=["🥇","🥈","🥉"];lines=_panel("🏆 LEADERBOARD",f"{period.upper()} • paper trading")
    for i,row in enumerate(rows[:10],1):lines.append(f"{ranks[i-1] if i<=3 else f'{i:02d}.'} <b>{_escape_html(str(row.get('username') or f'User{row.get('telegram_id')}'))}</b>   ${_compact_number(row.get('total',0),4)}")
    if user_rank is not None and user_profit is not None:lines.extend(["",THIN_SEPARATOR,f"👤 Your rank: #{user_rank} • ${_compact_number(user_profit,4)}"])
    lines.extend(["","🔒 Use /leaderboard hide to hide your ranking.",BOTTOM]);return "\n".join(lines)

def format_portfolio(user_trades:list,total_balance:float,vip_limit:float)->str:
    total_pnl=sum(t.get('profit',0) for t in user_trades);icon='🟢' if total_pnl>=0 else '🔴';lines=_panel("📊 YOUR PORTFOLIO","Your simulated trading dashboard");lines.extend([f"💰 <b>Simulated Balance</b>  ${_compact_number(total_balance,4)}",f"{icon} <b>Total P/L</b>          ${_compact_number(total_pnl,4)}",f"📈 <b>Trades</b>             {len(user_trades)}","🧾 <b>Recent Trades</b>",f"🎯 <b>VIP limit</b>          ${_compact_number(vip_limit,4)}","",SECTION_SEPARATOR])
    if user_trades:
        for t in user_trades[:5]:
            symbol=_escape_html(str(t.get('symbol','unknown')));pnl=t.get('profit',0);lines.append(f"{'🟢' if pnl>=0 else '🔴'} {symbol} • ${_compact_number(t.get('size',0),4)} • {str(t.get('created_at',''))[:10] or 'unknown'} • P/L ${_compact_number(pnl,4)}")
        if len(user_trades)>5:lines.append(f"… +{len(user_trades)-5} more trades")
    else:lines.append("📭 No paper trades yet.")
    lines.append(BOTTOM);return "\n".join(lines)
