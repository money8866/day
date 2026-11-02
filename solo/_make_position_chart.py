# -*- coding: utf-8 -*-
"""
生成 2026 全年每日仓位动态柱状图（HTML）
=========================================
数据：_bt_out/position_2026.csv（V10.1 动态仓位回放，198 个交易日）
输出：report_daily/position_2026.html

图形设计：
  · 底层柱 = 仓位权限上限（市场环境允许的最高仓位，全年 198 天都有）
  · 上层柱 = 实际建议仓位（受合格机会数与组合风险约束）
  · 无选股信号覆盖的区间用斜纹阴影标注 —— **不假装是 0%**
  · 状态切换点用竖线标记，颜色按 S1/S2/S3 区分
  · 悬停显示当日全部指标；支持「按月/按周」密度切换与播放动画
"""
import os
import json
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE_DIR, '_bt_out', 'position_2026.csv')
OUT_DIR = os.path.join(os.path.dirname(BASE_DIR), 'report_daily')
OUT = os.path.join(OUT_DIR, 'position_2026.html')

STATE_COLOR = {
    'S1': '#378ADD',   # 蓝 · 正常
    'S2': '#EF9F27',   # 琥珀 · 退潮
    'S3': '#E24B4A',   # 红 · 恐慌加速
    'S4': '#1D9E75',   # 青 · 卖压衰减
    'S5': '#639922',   # 绿 · 修复确认
    'S6': '#7F77DD',   # 紫 · 强势主升
}


def main():
    df = pd.read_csv(SRC, dtype={'trade_date': str})
    df['has_pick'] = (df['qualified'] > 0).astype(int)

    data = {
        'd': df['trade_date'].tolist(),
        's': [x[1:] for x in df['state']],
        'c': df['state_cn'].tolist(),
        'a': df['authority_max'].tolist(),
        'g': df['suggested'].tolist(),
        'p': df['has_pick'].tolist(),
        'q': df['qualified'].tolist(),
        'b': df['breadth'].round(1).tolist(),
        'zt': df['zt'].fillna(0).astype(int).tolist(),
        'dt': df['dt'].fillna(0).astype(int).tolist(),
        'zb': df['broken_rate'].fillna(0).round(1).tolist(),
        'sw': df['switched'].tolist(),
    }

    # 统计
    vc = df['state'].value_counts()
    stat = {
        'days': len(df),
        'from': df['trade_date'].iloc[0],
        'to': df['trade_date'].iloc[-1],
        'avgAuth': round(df['authority_max'].mean(), 1),
        'avgSug': round(df[df['has_pick'] == 1]['suggested'].mean(), 1) if (df['has_pick'] == 1).any() else 0,
        'maxSug': int(df['suggested'].max()),
        'switches': int(df['switched'].sum()),
        'pickFrom': df[df['has_pick'] == 1]['trade_date'].min() if (df['has_pick'] == 1).any() else '',
        'states': {k: int(vc.get(k, 0)) for k in ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']},
    }

    js_data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    js_stat = json.dumps(stat, ensure_ascii=False)

    html = HTML_TEMPLATE.replace('/*__DATA__*/', js_data).replace('/*__STAT__*/', js_stat)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✓ 已生成 {OUT}（{os.path.getsize(OUT)/1024:.0f} KB）")
    print(f"  区间 {stat['from']} ~ {stat['to']}，{stat['days']} 个交易日")
    print(f"  权限上限均值 {stat['avgAuth']}%  建议仓位均值 {stat['avgSug']}%（仅有信号区间）")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>2026 全年每日仓位变化 · V10.1 动态仓位管理</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.5.0/echarts.min.js"></script>
<style>
  :root{
    --bg:#ffffff; --panel:#fafaf9; --line:rgba(0,0,0,.10); --line2:rgba(0,0,0,.18);
    --tx:#1c1c1a; --tx2:#5f5e5a; --tx3:#8a8983;
    --s1:#378ADD; --s2:#EF9F27; --s3:#E24B4A; --s4:#1D9E75; --s5:#639922; --s6:#7F77DD;
    --auth:#c7dcee; --authLine:#9dbfe0;
  }
  @media (prefers-color-scheme: dark){
    :root{
      --bg:#16161a; --panel:#1e1e23; --line:rgba(255,255,255,.12); --line2:rgba(255,255,255,.20);
      --tx:#e8e8e6; --tx2:#a8a7a2; --tx3:#75746f;
      --auth:#2b3f52; --authLine:#3d5871;
    }
  }
  *{box-sizing:border-box}
  body{margin:0;padding:20px;background:var(--bg);color:var(--tx);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    font-size:13px;line-height:1.6;}
  h1{font-size:17px;font-weight:500;margin:0 0 4px}
  .sub{font-size:12px;color:var(--tx2);margin-bottom:16px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px;margin-bottom:16px}
  .card{background:var(--panel);border-radius:10px;padding:12px 14px}
  .card .l{font-size:11px;color:var(--tx2);margin-bottom:3px}
  .card .v{font-size:22px;font-weight:500;line-height:1.2}
  .card .v small{font-size:12px;font-weight:400;color:var(--tx2)}
  .chartbox{background:var(--bg);border:0.5px solid var(--line);border-radius:12px;padding:14px 10px 6px}
  #chart{width:100%;height:460px}
  .lg{display:flex;flex-wrap:wrap;gap:14px;margin:10px 2px 0;font-size:12px;color:var(--tx2)}
  .lg i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-1px}
  .lg .stripe{background:repeating-linear-gradient(45deg,rgba(0,0,0,.10),rgba(0,0,0,.10) 3px,transparent 3px,transparent 7px);
    border:0.5px solid var(--line2)}
  .ctrl{display:flex;gap:8px;align-items:center;margin:12px 0 2px;flex-wrap:wrap}
  .btn{font-size:12px;padding:5px 12px;border-radius:7px;border:0.5px solid var(--line2);
    background:var(--bg);color:var(--tx);cursor:pointer;font-family:inherit}
  .btn.on{background:var(--tx);color:var(--bg);border-color:var(--tx)}
  .note{font-size:12px;color:var(--tx3);margin-top:10px;line-height:1.75}
  .note b{font-weight:500;color:var(--tx2)}
  table{border-collapse:collapse;width:100%;font-size:12px;margin-top:14px}
  th,td{border-bottom:0.5px solid var(--line);padding:6px 8px;text-align:left}
  th{color:var(--tx2);font-weight:500}
  td.n{text-align:right;font-variant-numeric:tabular-nums}
  .pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;color:#fff}
</style>
</head>
<body>
<h1>2026 全年每日仓位变化</h1>
<p class="sub">V10.1 动态仓位管理 · 市场决定仓位上限，个股信号决定买入机会，组合风险决定实际仓位</p>

<div class="cards" id="cards"></div>

<div class="chartbox">
  <div id="chart"></div>
</div>

<div class="lg">
  <span><i style="background:var(--auth)"></i>仓位权限上限（市场允许的最高仓位）</span>
  <span><i style="background:var(--s1)"></i>实际建议仓位（细柱，覆盖在权限柱内）</span>
  <span><i class="stripe"></i>无选股信号数据（建议仓位不可计算）</span>
  <span><i style="background:var(--s3);width:3px;height:13px;border-radius:1px"></i>状态切换点</span>
</div>

<div class="ctrl">
  <button class="btn" id="bAll">全部交易日</button>
  <button class="btn on" id="bWk">按周</button>
  <button class="btn" id="bMo">按月</button>
  <button class="btn" id="bPlay">播放</button>
  <span style="font-size:12px;color:var(--tx3)" id="playTip"></span>
</div>

<p class="note" id="note"></p>

<div id="tbl"></div>

<script>
var D = /*__DATA__*/;
var S = /*__STAT__*/;
var SC = {S1:'#378ADD',S2:'#EF9F27',S3:'#E24B4A',S4:'#1D9E75',S5:'#639922',S6:'#7F77DD'};
var css = getComputedStyle(document.documentElement);
var C_AUTH = css.getPropertyValue('--auth').trim();
var C_AUTHL = css.getPropertyValue('--authLine').trim();
var C_LINE = css.getPropertyValue('--line').trim();
var C_TX = css.getPropertyValue('--tx').trim();
var C_TX2 = css.getPropertyValue('--tx2').trim();
var C_TX3 = css.getPropertyValue('--tx3').trim();
var C_BG = css.getPropertyValue('--bg').trim();

function mm(d){return d.slice(4,6)+'-'+d.slice(6);}
function md(d){return d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6);}

/* ---- 指标卡 ---- */
(function(){
  var st = S.states;
  var cards = [
    ['交易日', S.days, '天'],
    ['区间', S.from.slice(4)+'~'+S.to.slice(4), ''],
    ['权限上限均值', S.avgAuth, '%'],
    ['建议仓位均值', S.avgSug, '%'],
    ['最高建议仓位', S.maxSug, '%'],
    ['状态切换', S.switches, '次']
  ];
  document.getElementById('cards').innerHTML = cards.map(function(c){
    return '<div class="card"><div class="l">'+c[0]+'</div><div class="v">'+c[1]+
           (c[2]?'<small> '+c[2]+'</small>':'')+'</div></div>';
  }).join('');
})();

/* ---- 数据聚合（按周/月）---- */
function group(mode){
  var idx=[], key=[];
  for(var i=0;i<D.d.length;i++){
    var d=D.d[i], k;
    if(mode==='all') k=d;
    else if(mode==='mo') k=d.slice(0,6);
    else { var dt=new Date(d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6));
           var day=(dt.getDay()+6)%7; dt.setDate(dt.getDate()-day);
           k=dt.toISOString().slice(0,10); }
    if(!key.length || key[key.length-1]!==k){ key.push(k); idx.push([]); }
    idx[idx.length-1].push(i);
  }
  return {keys:key, idx:idx};
}

/* ---- 主图 ---- */
var chart = echarts.init(document.getElementById('chart'), null, {renderer:'canvas'});
var mode='wk', G=group('wk'), playTimer=null, playIdx=0;

function buildOption(g, upto){
  var n=g.keys.length;
  var upto = (upto===undefined)? n : upto;
  var labels=[], auth=[], sug=[], sugColor=[], hasPick=[], switchMark=[], cat=[];
  for(var i=0;i<upto;i++){
    var ii=g.idx[i];
    /* 权限取区间**最小值**（一周内最保守的风险档位），
       建议仓位取**最后一个有信号日**的值 —— 与柱高、颜色同源。 */
    var a=999,sg=0,hp=0,sc=C_AUTH,sw=0;
    for(var j=0;j<ii.length;j++){
      var k=ii[j];
      if(D.a[k]<a)a=D.a[k];
      if(D.p[k])hp=1;
      if(D.sw[k])sw=1;
    }
    if(a===999)a=0;
    /* 建议仓位取该区间最后一个有信号的日子 */
    var last=-1;
    for(var j=ii.length-1;j>=0;j--){ if(D.p[ii[j]]){last=ii[j];break;} }
    var sv = last>=0? D.g[last] : null;
    labels.push(mode==='all'? mm(D.d[ii[0]])
                 : (mode==='wk'? g.keys[i].slice(5) : g.keys[i]));
    auth.push(a);
    sug.push(sv===null? null : sv);
    /* 颜色与柱高同源：取该区间最后一个有信号日子的状态色 */
    sugColor.push(last>=0? (SC['S'+D.s[last]]||'#378ADD') : 'transparent');
    hasPick.push(hp);
    cat.push(g.keys[i]);
  }
  /* 无选股信号区间（markArea 阴影）—— 明确区分「数据缺失」与「建议空仓」 */
  var noPick=[];
  for(var i=0;i<upto;i++){
    var hp=0;
    for(var j=0;j<g.idx[i].length;j++){ if(D.p[g.idx[i][j]]) hp=1; }
    noPick.push(hp?0:1);
  }
  /* 连续段切分（正确写法：逐段扫描，段尾为连续 1 的最后一位） */
  var markAreas=[];
  var i0=0;
  while(i0<noPick.length){
    if(!noPick[i0]){ i0++; continue; }
    var i1=i0;
    while(i1+1<noPick.length && noPick[i1+1]) i1++;
    markAreas.push([
      {xAxis: i0-0.5, itemStyle:{color:'rgba(128,128,128,.09)'}},
      {xAxis: i1+0.5}
    ]);
    i0=i1+1;
  }

  /* 切换标记 */
  for(var i=0;i<upto;i++){
    var mk=null;
    for(var j=0;j<g.idx[i].length;j++){
      var k=g.idx[i][j];
      if(D.sw[k]){ mk={xAxis:i, yAxis:D.a[k], value:D.c[k]+' '+D.a[k]+'%'}; break; }
    }
    switchMark.push(mk);
  }
  return {
    animation: true, animationDuration: 700, animationEasing:'cubicOut',
    grid:{left:52,right:20,top:26,bottom:56},
    tooltip:{trigger:'axis', axisPointer:{type:'shadow'},
      backgroundColor:'rgba(28,28,26,.94)', borderWidth:0, textStyle:{color:'#fff',fontSize:12},
      formatter:function(ps){
        var i=ps[0].dataIndex;
        var sub=[];
        if(mode==='wk'){
          var dd=D.d[g.idx[i][0]], ee=D.d[g.idx[i][g.idx[i].length-1]];
          sub.push('<div style="font-weight:500">'+md(dd)+' ~ '+md(ee)+'</div>');
        } else if(mode==='mo'){
          sub.push('<div style="font-weight:500">'+cat[i]+'</div>');
        }
        var last=-1;
        for(var j=g.idx[i].length-1;j>=0;j--){ if(D.p[g.idx[i][j]]){last=g.idx[i][j];break;} }
        var k = last>=0? last : g.idx[i][0];
        sub.push('<div style="opacity:.75;font-size:11px">'+md(D.d[k])+'</div>');
        sub.push('<div style="margin-top:3px">状态 <b>'+D.c[k]+'</b>（权限 '+D.a[k]+'%）</div>');
        sub.push('<div>建议仓位 <b>'+(last>=0? D.g[last]+'%' : '无信号数据')+'</b></div>');
        sub.push('<div>广度 '+D.b[k]+'%　涨停 '+D.zt[k]+'　跌停 '+D.dt[k]+'</div>');
        sub.push('<div>炸板率 '+D.zb[k]+'%　合格机会 '+D.q[k]+' 只</div>');
        return sub.join('');
      }},
    xAxis:{type:'category', data:labels,
      axisLine:{lineStyle:{color:C_LINE}}, axisTick:{show:false},
      axisLabel:{color:C_TX3, fontSize:11, maxRotation:0, autoSkip:true, maxTicksLimit:24}},
    yAxis:{type:'value', max:100, name:'仓位 %', nameTextStyle:{color:C_TX3,fontSize:11},
      axisLine:{show:false}, axisTick:{show:false},
      splitLine:{lineStyle:{color:C_LINE, type:'dashed'}},
      axisLabel:{color:C_TX3, fontSize:11}},
    series:[
      {name:'权限上限', type:'bar', data:auth, barCategoryGap:'28%',
       itemStyle:{color:C_AUTH, borderColor:C_AUTHL, borderWidth:0.5, borderRadius:[2,2,0,0]},
       animationDelay:function(i){return i*3;},
       markArea:{silent:true, data:markAreas},
       z:1},
      {name:'建议仓位', type:'bar', data:sug, barCategoryGap:'52%',
       itemStyle:{color:function(p){return sugColor[p.dataIndex]||'#378ADD';},
         borderRadius:[2,2,0,0]},
       animationDelay:function(i){return i*3+120;}, z:2},
      {name:'切换', type:'scatter', data:switchMark, symbol:'line', symbolSize:[0,13],
       itemStyle:{color:C_TX3}, z:3, tooltip:{show:false}}
    ]
  };
}

function refresh(upto){
  chart.setOption(buildOption(G, upto), true);
  var note = document.getElementById('note');
  var pkFrom = S.pickFrom;
  note.innerHTML =
    '<b>口径说明</b><br>'+
    '· <b>仓位权限上限</b>（浅色柱）：市场环境允许持有的最高仓位，由状态机决定，'+
    'S1 正常 60% / S2 退潮 30% / S3 恐慌加速 5% / S4 卖压衰减 10% / S5 修复确认 60% / S6 强势主升 100%。全年 198 个交易日均可计算。<br>'+
    '· <b>实际建议仓位</b>（细实色柱，套在权限柱内）：在权限上限内，再按合格买点数量、组合风险预算、现金约束收敛。'+
    '★ <b>'+pkFrom+' 之前没有个股选股信号数据</b>，机会数为 0 故建议仓位不可计算，柱体留空并以斜纹标注 —— '+
    '这不是"建议空仓"，而是数据缺失。<br>'+
    '· 竖线为市场状态切换点。全年切换 '+S.switches+' 次（'+(S.switches/S.days*100).toFixed(1)+'%）。'+
    '状态分布：S1 正常 '+S.states.S1+' 天、S2 退潮 '+S.states.S2+' 天、S3 恐慌加速 '+S.states.S3+' 天'+
    '，S4/S5/S6 全年未触发（卖压衰减与修复确认节点在这段行情里罕见）。<br>'+
    '· 数据来源：V10.1 动态仓位管理，2026-01-05 ~ 2026-10-30 全部 198 个交易日，逐日状态链推进。<br>'+
    '· 视图：当前按<b>周</b>汇总（权限上限取区间内<b>最保守</b>的一档，建议仓位取最后一个有信号日子的值）。'+
    '切换「全部交易日」可看逐日明细，「按月」看月度趋势。';
}

refresh();

/* ---- 按钮 ---- */
function setMode(m){
  mode=m; G=group(m);
  document.getElementById('bAll').className='btn'+(m==='all'?' on':'');
  document.getElementById('bWk').className='btn'+(m==='wk'?' on':'');
  document.getElementById('bMo').className='btn'+(m==='mo'?' on':'');
  refresh();
}
document.getElementById('bAll').onclick=function(){setMode('all');};
document.getElementById('bWk').onclick=function(){setMode('wk');};
document.getElementById('bMo').onclick=function(){setMode('mo');};
document.getElementById('bPlay').onclick=function(){
  var b=this;
  if(playTimer){ clearInterval(playTimer); playTimer=null; b.className='btn'; b.textContent='播放';
    document.getElementById('playTip').textContent=''; refresh(); return; }
  b.className='btn on'; b.textContent='停止';
  playIdx=0;
  playTimer=setInterval(function(){
    playIdx+=Math.max(1,Math.floor(G.keys.length/40));
    if(playIdx>=G.keys.length){ playIdx=G.keys.length; }
    refresh(playIdx);
    document.getElementById('playTip').textContent='播放中 '+playIdx+'/'+G.keys.length;
    if(playIdx>=G.keys.length){ clearInterval(playTimer); playTimer=null;
      b.className='btn'; b.textContent='播放'; document.getElementById('playTip').textContent='播放结束'; }
  }, 90);
};
window.addEventListener('resize', function(){ chart.resize(); });

/* ---- 状态分布表 ---- */
(function(){
  var names={S1:'正常',S2:'退潮',S3:'恐慌加速',S4:'卖压衰减',S5:'修复确认',S6:'强势主升'};
  var rows=Object.keys(names).map(function(k){
    var n=S.states[k];
    return '<tr><td><span class="pill" style="background:'+SC[k]+'">'+k+'</span></td><td>'+names[k]+
      '</td><td class="n">'+n+'</td><td class="n">'+(n/S.days*100).toFixed(1)+'%</td></tr>';
  }).join('');
  document.getElementById('tbl').innerHTML =
    '<table><thead><tr><th>状态</th><th>含义</th><th style="text-align:right">天数</th>'+
    '<th style="text-align:right">占比</th></tr></thead><tbody>'+rows+'</tbody></table>';
})();
</script>
</body>
</html>
"""

if __name__ == '__main__':
    main()
