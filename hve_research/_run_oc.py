import time,os,hve_lib as L
t=time.time()
p,m,cal=L.load_panel()
print('loaded',round(time.time()-t,1),p.shape,flush=True)
t=time.time()
oc=L.compute_outcomes(p)
print('outcomes',round(time.time()-t,1),oc.shape,flush=True)
print(oc.head(3).to_string())
