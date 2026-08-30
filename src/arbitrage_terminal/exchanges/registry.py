from .ccxt_adapter import CcxtAdapter
from .lbank import LBankAdapter
from .xt import XTAdapter

SPECIAL={'lbank':LBankAdapter,'xt':XTAdapter}

def build_exchanges(names,credentials_provider):
    result={}
    for name in dict.fromkeys(n.lower() for n in names):
        try:
            adapter=SPECIAL.get(name,CcxtAdapter)
            result[name]=adapter(name,public_name=name,credentials=credentials_provider(name))
        except Exception:
            continue
    return result
