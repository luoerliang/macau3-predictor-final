# 澳门六合彩3分分析（免 Token）

这是一个 Flask 网页版的澳门六合彩3分历史统计工具。

## 数据源
公开的澳门六合彩3分历史页面：
https://maoaujc.com/macaujc2//?id=3&page=3

程序只在解析到完整的 **6个正码 + 1个特码** 时写入数据库，避免把普通澳门彩或错误的第7个号码写进去。

## Render 部署
- Build Command：`pip install -r requirements.txt`
- Start Command：`gunicorn app:app`

项目已包含 `Procfile`，其中启动命令也是 `gunicorn app:app`。

## 功能
- 只处理3分期号格式
- 6个正码与特码分开
- 北京时间 UTC+8
- SQLite 自动去重
- 网页每60秒刷新
- 服务运行时每60秒尝试同步
- 页面可手动立即同步
- 历史频次/近期加权统计候选

“统计候选”仅是历史数据统计，不代表对下一期结果的保证或确定预测。

## 注意
Render 免费实例休眠后，后台线程不能保证持续运行；重新打开网页时会再次尝试同步。
SQLite 在免费实例重启或重新部署后不保证永久保存；如果需要长期保存，应使用持久磁盘或 Postgres。
