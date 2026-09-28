# -*- coding: utf-8 -*-
"""TL-01 §16 趋势状态识别三方法中的 Model B / Model C

Model A（预注册阈值规则）已由 tl_states.py 完成，落在 tl01_state_daily.parquet。
本脚本补齐另外两种状态识别方法，用于交叉验证「状态是否存在」：

Model B  聚类（KMeans / GaussianMixture）
        特征：ts_raw, slope_20, pos_60, rv_20, rs_20, v_trend, dd_60
        K = 4,5,6,7,8 全测（预注册 cluster_k）；主 K = 6（预注册 cluster_primary_k）
        纪律：标准化参数与聚类模型只在 TRAIN(2018-2022) 子样本上拟合，
              之后对全样本只做「分配」；不使用任何未来数据。

Model C  Markov State Model
        C1 一阶转移 P(S_{t+1} | S_t)（Model A 规则状态，11x11）
           以及 k=5/10/20 步转移 P(S_{t+k} | S_t)
        C2 在 Model B 主 K 聚类状态上的一阶转移与平稳分布
        说明：环境未安装 hmmlearn，故 Model C 采用离散 Markov State Model
              （§16 允许「Markov State Model 或者 Hidden Markov Model」）。

产出（非 §53 交付文件，作为报告素材）：
    out/frag_modelbc.json     结构化结果（供 tl_report.py 读取）
    _tl_model_run.txt         日志
"""
import os
import json
import time
import numpy as np

from tl_common import (HERE, OUTD, PREREG, STATE_NAMES, Log, panel_meta, pget,
                       ic_stats, phase_of, daily_ic)

PRIMARY_H = 20
FEATS = list(PREREG['cluster_feats'])
KS = list(PREREG['cluster_k'])
K_MAIN = int(PREREG['cluster_primary_k'])
SEED = int(PREREG['seed'])
FIT_N = int(PREREG['cluster_fit_n'])


def substate_mean_map(lab, y, valid, K, min_n=200):
    """(类别 -> E[fwd20])，只在 TRAIN 拟合；缺失回退全 TRAIN 均值"""
    s = np.zeros(K)
    c = np.zeros(K)
    m = valid & (lab >= 0) & np.isfinite(y)
    np.add.at(s, lab[m], y[m].astype(np.float64))
    np.add.at(c, lab[m], 1.0)
    mp = np.where(c >= min_n, s / np.maximum(c, 1.0), np.nan)
    gm = float(np.mean(y[m])) if m.sum() else 0.0
    for i in range(K):
        if not np.isfinite(mp[i]):
            mp[i] = gm
    return mp


def main():
    t0 = time.time()
    log = Log('_tl_model_run.txt')
    log('=' * 78)
    log('TL-01 §16 Model B（聚类）/ Model C（Markov State Model）')

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    YR = np.asarray(pget('year'))
    ST = np.asarray(pget('state'))
    Y = np.asarray(pget('fwd_%d' % PRIMARY_H))
    VAL = ST >= 0
    log('  面板 %d x %d   有效样本 %d' % (NCAL, NCODE, int(VAL.sum())))

    ph_d = phase_of(np.array([int(d[:4]) if d else 0
                              for d in meta['dates']], dtype=np.int32))
    train_day = np.broadcast_to((ph_d == 'TRAIN')[:, None], ST.shape)

    # ---------------- 载入聚类特征 ----------------
    X = np.empty((NCAL, NCODE, len(FEATS)), dtype=np.float32)
    for i, c in enumerate(FEATS):
        X[:, :, i] = np.asarray(pget(c))
    okrow = VAL & np.all(np.isfinite(X), axis=2)
    log('  特征 %s  完备行 %d' % ('+'.join(FEATS), int(okrow.sum())))

    # ---------------- Model B 拟合（仅 TRAIN） ----------------
    tr = okrow & train_day
    idx = np.flatnonzero(tr.ravel())
    rng = np.random.default_rng(SEED)
    if len(idx) > FIT_N:
        idx = rng.choice(idx, FIT_N, replace=False)
    Xf = X.reshape(-1, len(FEATS))[idx].astype(np.float64)
    mu, sd = Xf.mean(0), Xf.std(0)
    sd[sd < 1e-12] = 1.0
    Xs = (Xf - mu) / sd
    log('  TRAIN 拟合样本 %d  (%.0fs)' % (len(idx), time.time() - t0))

    from sklearn.cluster import KMeans
    from sklearn.mixture import GaussianMixture
    from sklearn.metrics import silhouette_score

    Xall = ((X.reshape(-1, len(FEATS)).astype(np.float64) - mu) / sd)
    resB = {}
    lab_by_k = {}
    for K in KS:
        t1 = time.time()
        km = KMeans(n_clusters=K, n_init=3, random_state=SEED).fit(Xs)
        lab_flat = np.full(NCAL * NCODE, -1, dtype=np.int8)
        lab_flat[okrow.ravel()] = km.predict(Xall[okrow.ravel()]).astype(np.int8)
        lab = lab_flat.reshape(NCAL, NCODE)
        lab_by_k[K] = lab
        sil = float(silhouette_score(Xs, km.labels_, sample_size=min(20000, len(Xs)),
                                     random_state=SEED))
        mp = substate_mean_map(lab, Y, tr, K)
        sig = np.where(VAL & (lab >= 0), mp[lab], np.nan).astype(np.float32)
        ic = daily_ic(sig, Y, VAL)
        oos_i = ic_stats(ic[ph_d == 'OOS'], PRIMARY_H)
        tr_i = ic_stats(ic[ph_d == 'TRAIN'], PRIMARY_H)
        resB['K%d' % K] = dict(
            K=K, inertia=float(km.inertia_), silhouette=sil,
            sizes=[int((lab == i).sum()) for i in range(K)],
            cluster_mean_fwd20=[round(float(mp[i]), 5) for i in range(K)],
            train_ic=round(tr_i['mean_ic'], 5), train_icir=round(tr_i['icir'], 4),
            oos_ic=round(oos_i['mean_ic'], 5), oos_icir=round(oos_i['icir'], 4),
            oos_n=int(oos_i['n']))
        log('    KMeans K=%d  sil=%.4f  TRAIN_IC=%.4f  OOS_IC=%.4f (n=%d)  %.0fs'
            % (K, sil, tr_i['mean_ic'], oos_i['mean_ic'], oos_i['n'], time.time() - t1))
        del lab_flat
    log('  Model B KMeans 完成  %.0fs' % (time.time() - t0))

    gm = GaussianMixture(n_components=K_MAIN, covariance_type='diag',
                         random_state=SEED, max_iter=200).fit(Xs)
    labg = np.full(NCAL * NCODE, -1, dtype=np.int8)
    labg[okrow.ravel()] = gm.predict(Xall[okrow.ravel()]).astype(np.int8)
    labg = labg.reshape(NCAL, NCODE)
    mpg = substate_mean_map(labg, Y, tr, K_MAIN)
    sigg = np.where(VAL & (labg >= 0), mpg[labg], np.nan).astype(np.float32)
    icg = daily_ic(sigg, Y, VAL)
    resB['GMM_K%d' % K_MAIN] = dict(
        K=K_MAIN, bic=float(gm.bic(Xs)), aic=float(gm.aic(Xs)),
        sizes=[int((labg == i).sum()) for i in range(K_MAIN)],
        cluster_mean_fwd20=[round(float(mpg[i]), 5) for i in range(K_MAIN)],
        train_ic=round(ic_stats(icg[ph_d == 'TRAIN'], PRIMARY_H)['mean_ic'], 5),
        oos_ic=round(ic_stats(icg[ph_d == 'OOS'], PRIMARY_H)['mean_ic'], 5))
    log('  Model B GMM K=%d  BIC=%.0f  OOS_IC=%.4f'
        % (K_MAIN, gm.bic(Xs), resB['GMM_K%d' % K_MAIN]['oos_ic']))

    # ---------------- Model C：Markov State Model ----------------
    log('-' * 78)
    log('  Model C1：Model A 规则状态的多步转移 P(S_t+k | S_t)')
    resC = {'C1': {}, 'C2': {}}
    for k in (1, 5, 10, 20):
        a, b = ST[:-k], ST[k:]
        m = (a >= 0) & (b >= 0)
        cnt = np.zeros((11, 11), dtype=np.int64)
        np.add.at(cnt, (a[m], b[m]), 1)
        p = cnt / np.maximum(cnt.sum(1, keepdims=True), 1)
        resC['C1']['k%d' % k] = dict(
            counts=cnt.tolist(),
            diag=[round(float(p[i, i]), 4) for i in range(11)])
        if k == 1:
            log('    一步对角留存率：' + '  '.join(
                'S%d=%.3f' % (i, p[i, i]) for i in range(11)))
            resC['C1']['p1'] = np.round(p, 5).tolist()
        else:
            log('    %d 步对角留存率：' % k + '  '.join(
                'S%d=%.3f' % (i, p[i, i]) for i in (1, 2, 3, 4, 5, 7, 8)))
        del a, b, m

    log('  Model C2：Model B 主 K 聚类状态的一阶转移与平稳分布')
    lab = lab_by_k[K_MAIN]
    a, b = lab[:-1], lab[1:]
    m = (a >= 0) & (b >= 0)
    cnt = np.zeros((K_MAIN, K_MAIN), dtype=np.int64)
    np.add.at(cnt, (a[m], b[m]), 1)
    P = cnt / np.maximum(cnt.sum(1, keepdims=True), 1)
    pi = np.full(K_MAIN, 1.0 / K_MAIN)
    for _ in range(2000):
        pi2 = pi.dot(P)
        if np.abs(pi2 - pi).max() < 1e-12:
            pi = pi2
            break
        pi = pi2
    resC['C2'] = dict(
        K=K_MAIN, p1=np.round(P, 5).tolist(), stationary=np.round(pi, 5).tolist(),
        diag=[round(float(P[i, i]), 4) for i in range(K_MAIN)],
        sizes=[int((lab == i).sum()) for i in range(K_MAIN)],
        mean_fwd20=[round(float(mpg[i]), 5) for i in range(K_MAIN)])
    log('    对角留存率：' + '  '.join('C%d=%.3f' % (i, P[i, i]) for i in range(K_MAIN)))
    log('    平稳分布  ：' + '  '.join('C%d=%.3f' % (i, pi[i]) for i in range(K_MAIN)))
    log('    聚类类别 E[fwd20]：' + '  '.join(
        'C%d=%+.3f%%' % (i, mpg[i] * 100) for i in range(K_MAIN)))

    frag = dict(model_b=resB, model_c=resC, k_primary=K_MAIN,
                feats=FEATS, fit_n=int(len(idx)), note='fit only on TRAIN 2018-2022')
    with open(os.path.join(OUTD, 'frag_modelbc.json'), 'w', encoding='utf-8') as f:
        json.dump(frag, f, ensure_ascii=False, indent=1)
    log('  已写 out/frag_modelbc.json')
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
