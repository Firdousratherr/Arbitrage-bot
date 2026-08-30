from .ccxt_adapter import CcxtAdapter,LBankAdapter,XTAdapter
SPECIAL={'lbank':LBankAdapter,'xt':XTAdapter}
def build_exchanges(names,credentials_provider):
    result={}
    for name in dict.fromkeys(n.lower() for n in names):
        try: result[name]=SPECIAL.get(name,CcxtAdapter)(name,public_name=name,credentials=credentials_provider(name))
        except Exception: continue
    return result
