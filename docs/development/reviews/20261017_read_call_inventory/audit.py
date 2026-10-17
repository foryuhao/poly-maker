"""Index actual source callsites without importing application code or making requests."""
import ast
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
FILES=[
 'rdp_sm_normal.py','rdp_sm_rebalance.py','rdp_sm_loop.py','rdp_sm_lifecycle.py',
 'rdp_sm_execution.py','rdp_sm.py','rdp_sm_entry.py','rdp_sm_marketdata.py',
 'rdp_sm_network.py','rdp_sm_merge.py','rdp_sm_fence.py','rdp_sm_sync.py',
 'rdp_sm_sync_companion.py','rdp_sm_sync_reads.py','rdp_sm_quotes.py',
 'rdp_sm_sizing.py','execution/filltruth.py','execution/gateway.py',
]
READ_NAMES={
 'market','positions','open_orders','balance_allowance','required_collateral_spenders',
 'get_trades','get_order','server_time','get_tick_size','get_order_book',
 '_ClobClient__resolve_version','reconcile_owned_order','reconcile_owned_order_once',
 '_verify_close','verify_close','_snapshot','_recover_pagination',
 '_refresh_canceled_submissions','_verify_canceled_submission','_external_no_fill_terminal',
 'refresh_feed_ticks','ensure_feed','get_version','get_server_time','market_by_slug',
 'exact_market','_account','snapshot','network_health','get_market','_balance',
 'collateral_raw','get_transaction_receipt','wait_after','cancel_detailed','get_open_orders',
 'retry_incomplete_reads','prepare','send_prepared',
}

class Index(ast.NodeVisitor):
 def __init__(self,path,src):
  self.path,self.src=path,src;self.stack=[];self.result=[];self.functions={}
 def visit_ClassDef(self,node):
  self.stack.append(node.name);self.generic_visit(node);self.stack.pop()
 def visit_FunctionDef(self,node):
  self.stack.append(node.name);self.functions['.'.join(self.stack)]=node
  self.generic_visit(node);self.stack.pop()
 visit_AsyncFunctionDef=visit_FunctionDef
 def visit_Call(self,node):
  name=node.func.attr if isinstance(node.func,ast.Attribute) else node.func.id if isinstance(node.func,ast.Name) else ''
  targets=[name] if name in READ_NAMES else []
  # Worker offloads pass the SDK function as a callable rather than invoking it here.
  if name in {'_io','to_thread','run_in_executor'}:
   targets.extend(a.attr for a in node.args if isinstance(a,ast.Attribute) and a.attr in READ_NAMES)
  for target in dict.fromkeys(targets):
   self.result.append({'source':str(self.path.relative_to(ROOT)),'function':'.'.join(self.stack),'line':node.lineno,'target':target,'syntax':' '.join((ast.get_source_segment(self.src,node) or '').split())[:300]})
  self.generic_visit(node)


def main():
 indexes={};sites=[];provenance=[]
 for file in FILES:
  path=ROOT/'src/polymaker'/file;src=path.read_text();idx=Index(path,src);idx.visit(ast.parse(src));indexes[file]=idx;sites+=idx.result
  provenance.append({'path':str(path),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
 # Sanity-check the shared path and multiplicities used in the hand-reviewed accounting.
 def count(file,fn,target):return sum(r['function']==fn and r['target']==target for r in indexes[file].result)
 assert count('rdp_sm_normal.py','NormalController.enter','prepare')==1
 assert count('rdp_sm_rebalance.py','RebalanceController.repair','prepare')==1
 for file in ['rdp_sm_normal.py','rdp_sm_rebalance.py']:
  assert not any(r['target'] in {'market','positions','open_orders','balance_allowance','get_trades','get_order','server_time','get_tick_size'} for r in indexes[file].result)
 assert count('rdp_sm_execution.py','SMWindowExecutor._snapshot','market')==1
 assert count('rdp_sm.py','_account','positions')==1
 assert count('rdp_sm.py','_account','open_orders')==1
 assert count('rdp_sm.py','_account','balance_allowance')==1
 assert count('rdp_sm_entry.py','AccountRiskPolicy.inspect','get_trades')==1
 assert count('rdp_sm_execution.py','SMWindowExecutor._snapshot','open_orders')==1
 assert count('rdp_sm_execution.py','SMWindowExecutor._snapshot','server_time')==1
 assert count('rdp_sm_execution.py','SMWindowExecutor._verify_order','get_order')==2
 assert count('execution/filltruth.py','reconcile_owned_order_once','get_order')==1
 assert count('execution/filltruth.py','reconcile_owned_order_once','get_trades')==1
 assert count('rdp_sm_lifecycle.py','BatchLifecycle._send.cancel_possible','cancel_detailed')==1
 # Record the installed SDK source/version to substantiate cache and hidden-read checks.
 sdk=Path(importlib.util.find_spec('py_clob_client_v2.client').origin)
 provenance.append({'path':str(sdk),'bytes':sdk.stat().st_size,'sha256':hashlib.sha256(sdk.read_bytes()).hexdigest()})
 with (OUT/'call_sites.csv').open('w') as f:
  writer=csv.DictWriter(f,fieldnames=['source','function','line','target','syntax']);writer.writeheader();writer.writerows(sites)
 evidence={
  'scope':'Current production obi_v1 path used by rdp-sm-run; source audit, no live API calls',
  'sdk_version':importlib.metadata.version('py-clob-client-v2'),
  'counting_unit':'Logical complete reads; assumes single-page collections, successful ordinary ACK flow, warm SDK version cache. HTTP pagination, retries and background readers are additional.',
  'controllers_direct_http_calls':{'NORMAL':0,'REBALANCE':0},
  'common_snapshot':{'gamma_market':1,'positions':1,'open_orders':2,'balance_allowance':1,'market_trades':1,'server_time':1,'total':7},
  'new_preparation':{'tick_size':2,'total':2,'cached_slot_reuse_total':0},
  'close_per_known_id':{'order_detail':3,'market_trades':1,'total':4},
  'ordinary_successful_new_window':'9 + 4*N; N is current Session known-ID count including current ACK order; excludes background, retries, extra main-loop iterations, startup, final close and merge',
  'examples':[{'known_ids':n,'base_snapshot':7,'new_preparation':2,'one_close_round':4*n,'total':9+4*n} for n in [1,5,10,15]],
  'conditional_extra_reads':{
   'unproved_known_ids_before_snapshot':'1 order + 1 complete market trade read per unproved ID',
   'strict_canceled_submission_proof_refresh_in_snapshot':'1 complete open-order collection + 1 complete market trade collection, shared across all candidates',
   'strict_canceled_submission_proof_in_close':'1 complete open-order collection + 1 complete market trade collection per qualifying candidate',
   'unknown_intent_close_lookup':'1 complete open-order collection per close round if any placement is UNKNOWN or INTENT',
   'external_disappeared_order':'up to 2 order details + 1 complete trade collection per eligible external ID',
   'pending_confirmations':'sleep up to 2s then another full close round over all known IDs; no cancel=True from this waiter',
   'ws_tick_unverified':'2 tick reads; independent of the 2 reads for a later new preparation',
   'inventory_mismatch_waiter':'1 extra server-time read, sleep up to 1s, then next loop restarts snapshot',
   'pagination_recovery':'restarts each failed collection/filter scope from page one, then normal reads still run',
   'sdk_version':'cold cache: 1 /version read; ordinary repeated resolver calls use local cache',
   'final_close':'one extra complete close round over all known IDs; exception recovery may also close again',
   'eligible_successful_merge':'two extra full close rounds, 8*N single-page reads, plus settlement metadata/on-chain/relayer queries; final merge check also reads server time when elapsed is omitted',
  },
  'background':{
   'legacy_time_warmer':'1 /time per iteration, followed by 0.5s sleep; start-to-start includes HTTP duration',
   'new_sync_orders':'3 + K single-page GETs per target 1s round; K<=4 selected observed pending order IDs',
   'new_sync_account':'2 complete reads per target 2s round',
   'new_sync_startup':'1 derive-api-key GET and 1 initial /time read per companion',
   'new_sync_steady_state':'approximately 4 + average(K) GET/s with single pages and target periods sustained; not included in phase formulas',
  },
  'startup':{'discovery':'1 /time + 1 Gamma market read, plus /time waits if before T15',
   'first_healthy_preflight':'1 market + 1 derive + 2 initial tick + 1 positions + 1 open orders + 1 balance + 1 trades + 1 cold /version + 1 /time = 10 reads',
   'executor_open_healthy_preflight':'same 8 reads without repeated initial ticks, reuses ready feed',
   'executor_client_connect':'1 additional derive GET; cold version occurs at first runtime account read',
   'healthy_minimum_before_runtime_loop':'21 single-page reads when discovery starts after T15, no current-market recovery fence, no tick revalidation or retry',
   'current_market_recovery':'per candidate detail lookup plus market trades and cancellation; only exceptional restart path'},
  'limitations':['call_sites.csv is a syntax index including conditionals, helpers and legacy branches; its row count is not a count of HTTP requests.','No claims of measured latency, request rate improvement or whole-market fixed request count are made.','Only current obi_v1 path is counted in formulas; legacy execute_window and non-OBI book REST are indexed but excluded.'],
  'source_files':provenance,
 }
 (OUT/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'source_files':len(provenance),'call_sites_indexed':len(sites),'sdk_version':evidence['sdk_version'],'examples':evidence['examples']},ensure_ascii=False))

if __name__=='__main__':main()
