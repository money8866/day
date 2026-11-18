# -*- coding: utf-8 -*-
G = open('t_g.html', encoding='utf-8').read()
idx = G.find('<table')
print('first <table at', idx)
print('--- G[6000:9000] ---')
print(G[6000:9000])
print('--- around first table ---')
print(G[idx:idx+1500] if idx >= 0 else 'NO TABLE')
