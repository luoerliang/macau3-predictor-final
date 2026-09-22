import os, re, sqlite3, threading, time
from datetime import datetime, timedelta, timezone
from collections import Counter
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
DB = os.getenv('DB_PATH', 'macau3.db')
BASE_URL = 'https://maoaujc.com/macaujc2//'
TZ8 = timezone(timedelta(hours=8))
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1'
}

HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>澳门六合彩3分分析</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f5f6f8;margin:0;color:#222}.wrap{max-width:760px;margin:auto;padding:14px}.card{background:#fff;border-radius:14px;padding:16px;margin:10px 0;box-shadow:0 2px 10px #00000010}h1{font-size:22px;margin:4px 0 8px}.muted{color:#777;font-size:13px}.status{padding:9px 11px;border-radius:10px;background:#eef6ff;font-size:13px}.nums{display:flex;gap:7px;flex-wrap:wrap;margin:12px 0}.ball{width:38px;height:38px;border-radius:50%;background:#eee;display:flex;align-items:center;justify-content:center;font-weight:700}.special{background:#222;color:#fff}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.item{padding:10px;border:1px solid #eee;border-radius:10px;text-align:center}.tag{font-size:12px;color:#777}.big{font-size:19px;font-weight:700}.row{display:flex;justify-content:space-between;gap:8px}.table{width:100%;border-collapse:collapse;font-size:13px}.table td{padding:8px 3px;border-bottom:1px solid #eee}.right{text-align:right}.btn{display:inline-block;padding:9px 12px;border-radius:9px;background:#111;color:#fff;text-decoration:none}.warn{color:#a15c00}.err{color:#b00020}
</style></head><body><div class="wrap"><div class="card"><h1>澳门六合彩3分分析</h1><div class="muted">数据源：澳门六合彩3分公开历史页 · 北京时间 UTC+8</div></div>
<div class="card"><div class="row"><b>最新开奖</b><span class="muted">{{ latest_time }}</span></div><p><b>第{{ latest_issue or '—' }}期</b></p><div class="nums">{% for n in latest_main %}<span class="ball">{{n}}</span>{% endfor %}{% if latest_special %}<span class="ball special">{{latest_special}}</span>{% endif %}</div><div class="muted">前6个为正码，黑色为特码</div></div>
<div class="card"><b>统计候选（仅统计，不保证结果）</b><p class="muted">根据当天已收集记录的号码频次，并对近期记录加权。</p><div class="grid">{% for n,c in candidates %}<div class="item"><div class="big">{{n}}</div><div class="tag">{{c}}分</div></div>{% endfor %}</div></div>
<div class="card"><div class="row"><b>同步状态</b><span class="muted">{{ now }}</span></div><p class="{{'err' if error else ''}}">{{ status }}</p><a class="btn" href="/sync">立即同步</a></div>
<div class="card"><b>最近记录</b><table class="table"><tbody>{% for r in rows %}<tr><td>第{{r.issue}}期<br><span class="muted">{{r.time}}</span></td><td>{{' '.join(r.main)}} <b>+ {{r.special}}</b></td></tr>{% endfor %}</tbody></table></div>
</div></body></html>'''


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()
    c.execute('''CREATE TABLE IF NOT EXISTS draws(issue TEXT PRIMARY KEY, open_time TEXT, nums TEXT, special TEXT)''')
    c.commit()
    c.close()


def clean_num(x):
    m = re.search(r'(?<!\d)(\d{1,2})(?!\d)', x)
    return f'{int(m.group(1)):02d}' if m else None


def parse_source(html):
    soup = BeautifulSoup(html, 'html.parser')
    lines = [x.strip() for x in soup.stripped_strings if x.strip()]
    out = []
    issue_re = re.compile(r'^第(20\d{9})期$')  # 3分期号如 20260723449，共11位
    time_re = re.compile(r'^(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})$')

    for i, line in enumerate(lines):
        m_issue = issue_re.fullmatch(line)
        if not m_issue:
            continue
        issue = m_issue.group(1)
        window = lines[i + 1:i + 25]
        open_time = None
        nums = []
        special = None
        saw_plus = False

        for x in window:
            tm = time_re.fullmatch(x)
            if tm:
                open_time = f'{tm.group(1)}-{int(tm.group(2)):02d}-{int(tm.group(3)):02d} {tm.group(4)}:{tm.group(5)}:{tm.group(6)}'
                continue
            if not open_time:
                continue
            if x == '+':
                saw_plus = True
                continue
            if saw_plus:
                n = clean_num(x)
                if n:
                    special = n
                    break
            if len(nums) < 6 and re.fullmatch(r'\d{1,2}[\u4e00-\u9fff]{0,3}', x):
                n = clean_num(x)
                if n:
                    nums.append(n)

        if open_time and len(nums) == 6 and special:
            out.append((issue, open_time, nums, special))

    return out


def fetch_page(page):
    params = {'id': '3', 'page': str(page)}
    r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return parse_source(r.text)


def latest_db_date():
    c = db()
    row = c.execute('SELECT open_time FROM draws ORDER BY open_time DESC LIMIT 1').fetchone()
    c.close()
    if not row:
        return None
    try:
        return datetime.strptime(row['open_time'], '%Y-%m-%d %H:%M:%S').date()
    except Exception:
        return None


def save_draws(draws, today_only=True):
    today = datetime.now(TZ8).date()
    c = db()
    added = 0
    for issue, open_time, nums, special in draws:
        try:
            d = datetime.strptime(open_time, '%Y-%m-%d %H:%M:%S').date()
        except Exception:
            continue
        if today_only and d != today:
            continue
        if len(nums) != 6 or not special:
            continue
        c.execute('INSERT OR REPLACE INTO draws(issue,open_time,nums,special) VALUES(?,?,?,?)',
                  (issue, open_time, ','.join(nums), special))
        added += 1
    c.commit()
    c.close()
    return added


def sync(full=False):
    today = datetime.now(TZ8).date()
    all_draws = []
    pages = range(1, 81) if full else range(1, 3)

    for page in pages:
        draws = fetch_page(page)
        if not draws:
            if page == 1:
                raise RuntimeError('公开页面没有返回可解析的3分开奖记录。')
            break
        all_draws.extend(draws)

        # 页面按时间倒序；当已经翻到今天以前，后面无需继续。
        dates = []
        for d in draws:
            try:
                dates.append(datetime.strptime(d[1], '%Y-%m-%d %H:%M:%S').date())
            except Exception:
                pass
        if dates and min(dates) < today:
            break

    added = save_draws(all_draws, today_only=True)
    return len(all_draws), added


def rows(limit=80):
    c = db()
    rs = c.execute('SELECT * FROM draws ORDER BY open_time DESC LIMIT ?', (limit,)).fetchall()
    c.close()
    return [{'issue': r['issue'], 'time': r['open_time'], 'main': r['nums'].split(','), 'special': r['special']} for r in rs]


def candidates():
    rs = rows(80)
    score = Counter()
    for idx, r in enumerate(rs):
        weight = max(1, 80 - idx)
        for n in r['main'] + [r['special']]:
            score[n] += weight
    return sorted(score.items(), key=lambda x: (-x[1], x[0]))[:10]


init_db()
state = {'status': '正在首次同步…', 'error': False}


def bg():
    first = True
    while True:
        try:
            # 首次启动或跨日时补齐当天00:00后的全部记录；平时只检查前两页。
            full = first or latest_db_date() != datetime.now(TZ8).date()
            n, a = sync(full=full)
            state.update(status=f'自动同步正常：读取 {n} 条，写入/更新 {a} 条', error=False)
            first = False
        except Exception as e:
            state.update(status='同步失败：' + str(e), error=True)
        time.sleep(60)


threading.Thread(target=bg, daemon=True).start()


@app.route('/')
def home():
    rs = rows(30)
    latest = rs[0] if rs else None
    now = datetime.now(TZ8).strftime('%Y-%m-%d %H:%M:%S')
    return render_template_string(
        HTML,
        latest_issue=latest['issue'] if latest else None,
        latest_time=latest['time'] if latest else '—',
        latest_main=latest['main'] if latest else [],
        latest_special=latest['special'] if latest else None,
        candidates=candidates(),
        rows=rs,
        now=now,
        status=state['status'],
        error=state['error']
    )


@app.route('/sync')
def manual_sync():
    try:
        n, a = sync(full=True)
        state.update(status=f'同步完成：读取 {n} 条，写入/更新 {a} 条', error=False)
    except Exception as e:
        state.update(status='同步失败：' + str(e), error=True)
    return home()


@app.route('/api/draws')
def api_draws():
    return jsonify(rows(100))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '10000')))
