import asyncio,json,os,socket,sys,time
from pathlib import Path
root=Path('/home/ubuntu/poly-maker')
retained={k:os.environ[k] for k in ('PATH','LANG') if k in os.environ}
os.environ.clear(); os.environ.update(retained)
network_hosts=[]
def guard(event,args):
 if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
  path=Path(os.fsdecode(args[0])).absolute()
  if path==root/'.env' or root/'.aws' in path.parents:
   raise AssertionError('credentials forbidden in public observer')
 if event=='import' and args[0].startswith(('polymaker.execution.gateway','polymaker.rdp_sm_merge','polymaker.settlement_sweep','web3','eth_account')):
  raise AssertionError('execution/RPC/signing dependency forbidden in public observer')
sys.addaudithook(guard)
native_getaddrinfo=socket.getaddrinfo
def official_dns(host,*args,**kwargs):
 name=host.decode() if isinstance(host,bytes) else host
 if name not in ('gamma-api.polymarket.com','ws-subscriptions-clob.polymarket.com'):
  raise AssertionError('unapproved public observation endpoint')
 network_hosts.append(name)
 return native_getaddrinfo(host,*args,**kwargs)
socket.getaddrinfo=official_dns
from polymaker.risk_shadow import observe
slug=f'btc-updown-5m-{(int(time.time())//300+1)*300}'
Path('/tmp/rdp-risk-002-live-slug.txt').write_text(slug)
print(json.dumps({'event':'OBSERVATION_ONLY_START','slug':slug}),flush=True)
result=asyncio.run(observe(slug,root/'research_outputs/rdp_risk_shadow',enabled=True))
receipt={'result':result,'credential_guard':'ENFORCED','execution_rpc_import_guard':'ENFORCED','dns_hosts':network_hosts}
Path('/tmp/rdp-risk-002-live-receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
