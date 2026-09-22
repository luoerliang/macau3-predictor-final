import os, re, sqlite3, threading, time
from datetime import datetime, timedelta, timezone
from collections import Counter
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string

APP = Flask(__name__)
DB = os.getenv('DB_PATH', 'macau3.db')
SOURCE_URL = 'https://maoaujc.com/macaujc2//?id=3&page=3'
TZ8 = timezone(timedelta(hours=8))
HEADERS = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1'}

HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>澳门六合彩3分分析</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f5f6f8;margin:0;color:#222}.wrap{max-width:760px;margin:auto;padding:14px}.card{background:#fff;border-radius:14px;padding:16px;margin:10px 0;box-shadow:0 2px 10px #00000010}h1{font-size:22px;margin:4px 0 8px}.muted{color:#777;font-size:13px}.status{padding:9px 11px;border-radius:10px;background:#eef6ff;font-size:13px}.nums{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}.ball{width:38px;height:38px;border-radius:50%;background:#eee;display:flex;align-items:center;justify-content:center;font-weight:700}.special{background:#222;color:#fff}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.item{padding:10px;border:1px solid #eee;border-radius:10px;text-align:center}.tag{font-size:12px;color:#777}.big{font-size:19px;font-weight:700}.row{display:flex;justify-content:space-between;gap:8px}.table{width:100%;border-collapse:collapse;font-size:13px}.table td{padding:8px 3px;border-bottom:1px solid #eee}.right{text-align:right}.btn{display:inline-block;padding:9px 12px;border-radius:9px;background:#111;color:#fff;text-decoration:none}.warn{color:#a15c00}.err{color:#b00020}
</style></head><body><div class="wrap"><div class="card"><h1>澳门六合彩3分分析</h1><div class="muted">数据源：澳门六合彩3分公开历史页 · 北京时间 UTC+8</div></div>
<div class="card"><div class="row"><b>最新开奖</b><span class="muted">{{ latest_time }}</span></div><p><b>第{{ latest_issue or '—' }}期</b></p><div class="nums">{% for n in latest_main %}<span class="ball">{{n}}</span>{% endfor %}{% if latest_special %}<span class="ball special">{{latest_special}}</span>{% endif %}</div><div class="muted">前6个为正码，黑色为特码</div></div>
<div class="card"><b>统计候选（仅统计，不保证结果）</b><p class="muted">根据最近 {{ sample_n }} 期号码频次，并对近期记录加权。</p><div class="grid">{% for n,c in candidates %}<div class="item"><div class="big">{{n}}</div><div class="tag">{{c}}分</div></div>{% endfor %}</div></div>
<div class="card"><div class="row"><b>同步状态</b><span class="muted">{{ now }}</span></div><p class="{{'err' if error else ''}}">{{ status }}</p><a class="btn" href="/sync">立即同步</a></div>
<div class="card"><b>最近记录</b><table class="table"><tbody>{% for r in rows %}<tr><td>第{{r.issue}}期<br><span class="muted">{{r.time}}</span></td><td>{{' '.join(r.main)}} <b>+ {{r.special}}</b></td></tr>{% endfor %}</tbody></table></div>
</div></body></html>'''

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db(); c.execute('''CREATE TABLE IF NOT EXISTS draws(issue TEXT PRIMARY KEY, open_time TEXT, nums TEXT, special TEXT)'''); c.commit(); c.close()

def clean_num(x):
    m=re.search(r'(?<!\d)(\d{1,2})(?!\d)', x)
    return f'{int(m.group(1)):02d}' if m else None

def parse_source(html):
    soup=BeautifulSoup(html,'html.parser')
    text='\n'.join(s.strip() for s in soup.stripped_strings if s.strip())
    # Use line-based parsing first: issue -> time -> 6 numbers -> + -> special.
    lines=[x.strip() for x in soup.stripped_strings if x.strip()]
    out=[]
    for i,line in enumerate(lines):
        im=re.fullmatch(r'第?(20\d{10})期?', line)
        if not im: continue
        issue=im.group(1)
        window=lines[i+1:i+30]
        ti=None; nums=[]; special=None
        for j,x in enumerate(window):
            tm=re.search(r'(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\s+(\d{1,2}:\d{2}:\d{2})',x)
            if tm: ti=tm.group(1).replace('/','-')+' '+tm.group(2); continue
            if ti and re.fullmatch(r'\d{1,2}[\s\-]*[\u4e00-\u9fff]{0,3}',x):
                n=clean_num(x)
                if n: nums.append(n)
                continue
            if ti and x=='+':
                for z in window[j+1:j+4]:
                    n=clean_num(z)
                    if n: special=n; break
                break
        if ti and len(nums)>=6 and special:
            out.append((issue,ti,nums[:6],special))
    # Fallback: regex on normalized text.
    if not out:
        t=' '.join(lines)
        pat=re.compile(r'第(20\d{10})期.*?(20\d{2}[-/]\d{1,2}[-/]\d{1,2})\s+(\d{1,2}:\d{2}:\d{2}).*?(\d{1,2}).*?(\d{1,2}).*?(\d{1,2}).*?(\d{1,2}).*?(\d{1,2}).*?(\d{1,2}).*?\+.*?(\d{1,2})')
        for m in pat.finditer(t):
            out.append((m.group(1),m.group(2)+' '+m.group(3),[f'{int(m.group(k)):02d}' for k in range(4,10)],f'{int(m.group(10)):02d}'))
    return out

def sync():
    r=requests.get(SOURCE_URL,headers=HEADERS,timeout=20); r.raise_for_status()
    draws=parse_source(r.text)
    if not draws: raise RuntimeError('页面已打开，但没有解析到完整的6正码+1特码，已停止写入，避免污染数据。')
    c=db(); added=0
    for issue,ot,nums,special in draws:
        # only genuine 3-minute issue format
        if not re.fullmatch(r'20\d{10}',issue): continue
        c.execute('INSERT OR REPLACE INTO draws(issue,open_time,nums,special) VALUES(?,?,?,?)',(issue,ot,','.join(nums),special)); added+=1
    c.commit(); c.close(); return len(draws),added

def rows(limit=80):
    c=db(); rs=c.execute('SELECT * FROM draws ORDER BY open_time DESC LIMIT ?', (limit,)).fetchall(); c.close()
    return [{'issue':r['issue'],'time':r['open_time'],'main':r['nums'].split(','),'special':r['special']} for r in rs]

def candidates():
    rs=rows(80); score=Counter()
    for idx,r in enumerate(rs):
        weight=max(1,80-idx)
        for n in r['main']+[r['special']]: score[n]+=weight
    return sorted(score.items(), key=lambda x:(-x[1],x[0]))[:10]

init_db(); state={'status':'尚未同步','error':False}

def bg():
    while True:
        try:
            sync(); state.update(status='自动同步正常',error=False)
        except Exception as e: state.update(status='同步失败：'+str(e),error=True)
        time.sleep(60)
threading.Thread(target=bg,daemon=True).start()

@app.route('/')
def home():
    rs=rows(30); latest=rs[0] if rs else None
    now=datetime.now(TZ8).strftime('%Y-%m-%d %H:%M:%S')
    return render_template_string(HTML,latest_issue=latest['issue'] if latest else None,latest_time=latest['time'] if latest else '—',latest_main=latest['main'] if latest else [],latest_special=latest['special'] if latest else None,candidates=candidates(),sample_n=min(80,len(rs)),rows=rs,now=now,status=state['status'],error=state['error'])

@app.route('/sync')
def manual_sync():
    try:
        n,a=sync(); state.update(status=f'同步完成：读取 {n} 期，写入/更新 {a} 期',error=False)
    except Exception as e: state.update(status='同步失败：'+str(e),error=True)
    return home()

@app.route('/api/draws')
def api_draws(): return jsonify(rows(100))

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
