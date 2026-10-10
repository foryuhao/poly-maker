# Offline Python set/float layout diagnosis only. No project imports or network.
import os,subprocess,sys
snippet='''
import json
ids=[f'cycle-{c}-{t}-token' for c in range(1,5) for t in ['yes','no']]
cost={'cycle-1-yes-token':1.25*.55,'cycle-2-yes-token':1*.555,'cycle-3-yes-token':1*.555,'cycle-4-yes-token':1.75*.555}
for c in [3,4]:
 owner={x for x in ids[:2*c]}
 runtime=set(tuple(sorted(owner)))
 a=list(set(runtime));b=list(set(owner))
 sa=sum(cost[x] for x in a if x in cost);sb=sum(cost[x] for x in b if x in cost)
 if sa!=sb:print(json.dumps({'cycles':c,'runtime_inventory_cost':sa,'owner_inventory_cost':sb,'runtime_order':a,'owner_order':b}))
'''
for seed in range(128):
 p=subprocess.run([sys.executable,'-c',snippet],env={**os.environ,'PYTHONHASHSEED':str(seed)},text=True,capture_output=True,check=True)
 if p.stdout:
  print('SEED',seed,p.stdout.strip());break
else:print('No differing layouts in seeds0..127')
