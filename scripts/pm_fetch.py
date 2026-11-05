import requests,json,collections,re,datetime
ms={}
d0=datetime.date(2026,10,18)
for i in range(18):
    a=d0+datetime.timedelta(days=i); b=a+datetime.timedelta(days=1)
    n=0
    for off in range(0,2100,100):
        j=requests.get("https://gamma-api.polymarket.com/markets",params={"closed":"true","end_date_min":f"{a}T00:00:00Z","end_date_max":f"{b}T00:00:00Z","limit":100,"offset":off},timeout=60).json()
        if not isinstance(j,list) or not j: break
        for m in j: ms[m['id']]=m
        n+=len(j)
        if len(j)<100: break
    print(a,n,flush=True)
json.dump(list(ms.values()),open('out/bench2/pm_closed.json','w'))
res=[m for m in ms.values() if m.get('umaResolutionStatus')=='resolved' and m.get('outcomePrices') in ('["1", "0"]','["0", "1"]')]
print(len(ms),'resolved binary',len(res))
c=collections.Counter()
for m in res:
    c[re.sub(r'\d[\d,.]*','N',m['question'])[:40]]+=1
for k,v in c.most_common(70): print(v,k)
