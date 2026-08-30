from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    telegram_bot_token:str
    admin_telegram_ids:str=''
    database_path:str='data/arbitrage.sqlite3'
    enabled_exchanges:str='binance,bybit,okx,lbank,xt,mexc,gateio,kucoin,kraken,bitget'
    scan_timeout_seconds:float=Field(20.,ge=3,le=120)
    exchange_concurrency:int=Field(6,ge=1,le=32)
    max_data_age_seconds:float=Field(10.,ge=1,le=120)
    ai_api_url:str=''; ai_api_key:str=''; ai_model:str=''; ai_timeout_seconds:float=Field(30.,ge=3,le=120)
    dry_run:bool=True; require_vip:bool=True; log_level:str='INFO'
    model_config=SettingsConfigDict(env_file='.env',extra='ignore',case_sensitive=False)
    @property
    def exchanges(self): return list(dict.fromkeys(x.strip().lower() for x in self.enabled_exchanges.split(',') if x.strip()))
    @property
    def admin_ids(self): return {int(x.strip()) for x in self.admin_telegram_ids.split(',') if x.strip()}
@lru_cache
def get_settings(): return Settings()
