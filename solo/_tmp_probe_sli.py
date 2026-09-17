import os

d = r"d:\mystock\solo\sli\cache"
fs = sorted(os.listdir(d))
print("cache 文件总数:", len(fs))
hit = [f for f in fs if any(x in f for x in ("20260824", "20260825", "20260826", "20260827", "20260828", "20260831"))]
print("0824-0831 相关:")
for f in hit:
    print("  ", f, os.path.getsize(os.path.join(d, f)))
# 按前缀归类
pref = {}
for f in fs:
    k = f.split("_")[0]
    pref[k] = pref.get(k, 0) + 1
print("\n前缀统计:", pref)
print("\n最近 15 个文件:", fs[-15:])
