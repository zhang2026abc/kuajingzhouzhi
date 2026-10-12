#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨境周知 · 每日情报 AI 自动抓取流水线
==========================================
全自动流程：
    抓取新闻源 → 去重清洗 → AI 筛选与解读 → 写入 content.json

零基础用法：
    1. 安装依赖：  pip3 install requests
    2. 填入 API Key（见 CONFIG 区）
    3. 运行：      python3 auto_fetch.py
    4. 定时运行：  见文件末尾「定时任务配置」

没有 API Key 时，脚本仍会完成抓取，并输出「待处理新闻清单」供你手动处理。
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime

# ============================================================
#  CONFIG · 配置区（你只需要改这里）
# ============================================================

# --- 大模型 API（推荐智谱 glm-4-flash，免费）---
#   智谱AI     https://open.bigmodel.cn           ← 推荐
#   DeepSeek   https://platform.deepseek.com
#   通义千问   https://dashscope.aliyun.com
#
# 本地运行：直接填在下面的 API_KEY 里
# GitHub 运行：不要填在这里！用仓库的 Secrets 配置（见部署教程），
#              脚本会自动从环境变量读取，更安全。
API_PROVIDER = os.environ.get("API_PROVIDER", "zhipu")
API_KEY = os.environ.get("API_KEY", "")   # ← 本地调试时才填在引号里
API_BASE = {
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "zhipu":    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "qwen":     "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
}
API_MODEL = {
    "deepseek": "deepseek-chat",
    "zhipu":    "glm-4-flash",
    "qwen":     "qwen-plus",
}

DAILY_COUNT = 10
FREE_COUNT = 3

# --- 新闻源（已实测可用，可自行增删）---
NEWS_SOURCES = [
    {"name": "人民网财经", "url": "http://www.people.com.cn/rss/finance.xml"},
    {"name": "人民网国际", "url": "http://www.people.com.cn/rss/world.xml"},
    {"name": "新华网财经", "url": "http://www.xinhuanet.com/fortune/news_fortune.xml"},
    {"name": "中新网国际", "url": "https://www.chinanews.com.cn/rss/world.xml"},
    {"name": "东方财富",   "url": "http://rss.eastmoney.com/rss_partener.xml"},
]

# --- 相关性关键词（用于预筛，减少 AI 调用量）---
KEYWORDS = [
    "跨境电商", "跨境", "外贸", "出口", "进口", "关税", "海关", "自贸",
    "一带一路", "RCEP", "海外仓", "物流", "清关", "退税", "综试区",
    "亚马逊", "TikTok", "Temu", "SHEIN", "速卖通", "eBay", "独立站",
    "EPR", "VAT", "SABER", "合规", "支付", "结算", "汇率", "人民币",
    "非洲", "东盟", "欧盟", "中东", "东南亚", "沙特", "南非",
    "广交会", "进博会", "贸易", "通关",
]

# --- 教材清单（改成你们学校真实教材，这是护城河）---
TEXTBOOKS = [
    {"book": "跨境电商服务概论", "chapters": ["第4章 跨境电商物流与通关", "第5章 国际物流模式与选择", "第6章 跨境电商支付与结算"]},
    {"book": "跨境电商运营实务", "chapters": ["第6章 主流平台运营规则", "第7章 跨境电商平台模式对比", "第8章 数字化运营工具"]},
    {"book": "跨境电子商务概论", "chapters": ["第3章 全球主要跨境电商市场", "第5章 跨境电商选品与供应链"]},
    {"book": "国际贸易实务",     "chapters": ["第8章 关税与非关税壁垒", "第9章 出口退税与税收优惠", "第10章 区域经济一体化与自由贸易协定"]},
]

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE      = os.path.join(HERE, "content.json")
TEMPLATE_FILE = os.path.join(HERE, "app", "content.json")
PENDING_FILE  = os.path.join(HERE, "pending_news.json")
LOG_FILE      = os.path.join(HERE, "fetch.log")

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ============================================================
#  1. 抓取
# ============================================================

def _fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        raw = r.read()
    for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _parse_rss(xml):
    """解析 RSS/Atom：返回 [{title, desc}]"""
    out = []
    blocks = re.findall(r"<(?:item|entry)\b.*?</(?:item|entry)>", xml, re.S)
    for b in blocks:
        def pick(tag):
            m = re.search(
                rf"<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>", b, re.S)
            if not m:
                return ""
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
        title = pick("title")
        desc = pick("description") or pick("summary") or pick("content")
        if title and len(title) > 6:
            out.append({"title": title, "desc": desc[:400]})
    return out


def fetch_news():
    """抓取所有新闻源，去重后返回。"""
    all_items, seen = [], set()
    for src in NEWS_SOURCES:
        try:
            xml = _fetch(src["url"])
            items = _parse_rss(xml)
            added = 0
            for it in items:
                key = it["title"][:24]
                if key in seen:
                    continue
                seen.add(key)
                it["source"] = src["name"]
                all_items.append(it)
                added += 1
            log(f"  ✅ {src['name']}：{len(items)} 条（新增 {added}）")
        except Exception as e:
            log(f"  ⚠️  {src['name']} 抓取失败：{type(e).__name__} {str(e)[:50]}")
    log(f"📥 共抓取去重后 {len(all_items)} 条原始新闻")
    return all_items


# ============================================================
#  2. 相关性预筛（省 AI 调用成本）
# ============================================================

def prefilter(items):
    """按关键词过滤，保留与跨境电商相关的新闻。"""
    hits = []
    for it in items:
        text = it["title"] + it["desc"]
        score = sum(1 for k in KEYWORDS if k in text)
        if score >= 1:
            it["score"] = score
            hits.append(it)
    hits.sort(key=lambda x: -x["score"])
    log(f"🎯 相关性预筛后剩 {len(hits)} 条（关键词命中）")
    return hits


# ============================================================
#  3. AI 处理
# ============================================================

PROMPT = """你是跨境电商行业分析师，同时熟悉大学跨境电商专业课程体系。

今日抓取的原始新闻（JSON 数组，已按相关性排序）：
{news}

请从中筛选出 {count} 条最适合「跨境电商专业学生」的每日情报。每条必须包含：

- category: 三选一（政策关税 / 区域合作 / 合规数智）
- region: 五选一（非洲 / 中东 / 东盟 / 欧盟 / 全部区域）
- title: 客观精简标题，30字内
- source: 来源媒体名
- date: 形如 "2026年10月"
- hotword: 一个核心热词，4-6字
- route: {{"from":"中国","to":"具体国家或地区","label":"标签如 关税/合规/物流"}}
- facts: 3条关键事实，每条一句完整陈述，必须基于原文，严禁编造数字
- insight_student: 面向学生，说明对应哪门课哪一章、期末考点或实习价值（80-120字）
- insight_seller: 面向从业者，说明对做跨境电商意味着什么（60-100字）
- textbook: {{"book":"教材名","chapter":"章节名","point":"知识点"}}
- url: 原文链接（没有则填 ""）

教材清单（textbook 只能从这里选）：
{textbooks}

严格要求：
1. 所有 facts 必须来自提供的原始新闻，不许编造。
2. 若新闻不足 {count} 条，按实际数量输出。
3. 只输出 JSON 数组，不要 markdown 代码块，不要任何解释。
"""


def call_llm(news):
    if not API_KEY:
        return None
    try:
        import requests
    except ImportError:
        log("⚠️  未安装 requests：pip3 install requests")
        return None

    tb = "\n".join(f"- 《{t['book']}》：{'、'.join(t['chapters'])}" for t in TEXTBOOKS)
    prompt = PROMPT.format(
        news=json.dumps(news[:80], ensure_ascii=False, indent=1),
        count=DAILY_COUNT, textbooks=tb)

    payload = {
        "model": API_MODEL[API_PROVIDER],
        "messages": [
            {"role": "system", "content": "你是严谨的跨境电商分析师，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7, "max_tokens": 8000,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    log(f"🤖 调用 {API_PROVIDER} / {API_MODEL[API_PROVIDER]} ……")
    for attempt in range(3):
        try:
            r = requests.post(API_BASE[API_PROVIDER], json=payload, headers=headers, timeout=180)
            r.raise_for_status()
            c = r.json()["choices"][0]["message"]["content"]
            c = re.sub(r"^```(?:json)?|```$", "", c.strip(), flags=re.M).strip()
            data = json.loads(c)
            log(f"✅ AI 返回 {len(data)} 条情报")
            return data
        except Exception as e:
            log(f"  ⚠️  第 {attempt+1} 次调用失败：{type(e).__name__} {str(e)[:80]}")
            time.sleep(3)
    return None


# ============================================================
#  4. 写入
# ============================================================

def read_template():
    for p in (TEMPLATE_FILE, OUT_FILE):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return None


def main():
    log("=" * 54)
    log("  跨境周知 · 每日情报自动生成")
    log("=" * 54)

    news = fetch_news()
    if not news:
        log("❌ 未抓到任何新闻，请检查网络")
        return 1

    hits = prefilter(news)

    # 保存待处理清单（没配 Key 时你也能手动挑）
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(hits[:80], f, ensure_ascii=False, indent=2)
    log(f"💾 待处理清单已存至 {os.path.basename(PENDING_FILE)}")

    items = call_llm(hits) if hits else None

    base = read_template()
    if not base:
        log("❌ 找不到模板文件，请确认 app/content.json 存在")
        return 1

    if items:
        base["items"] = items[:DAILY_COUNT]
        for i, it in enumerate(base["items"], 1):
            it["id"] = i
            it.setdefault("url", "")
            it.setdefault("facts", [])
        base["meta"]["week"] = f"今日 · 精选 {len(base['items'])} 条"
        base["meta"]["data_note"] = f"情报数据截至 {datetime.now().strftime('%Y年%m月%d日')}"
        log(f"✅ 已生成 {len(base['items'])} 条情报")
    else:
        log("ℹ️  未调用 AI（缺少 API Key），content.json 保持不变")
        log(f"👉 请配置 API_KEY 后重跑，或手动处理 {os.path.basename(PENDING_FILE)}")

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
    log(f"✅ 输出 → {OUT_FILE}")
    log("📌 下一步：cp content.json app/content.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ============================================================
#  定时任务配置（配好 API Key 后再用）
# ============================================================
#
# 【Windows】任务计划程序
#   1. 搜索「任务计划程序」→ 创建基本任务
#   2. 触发器：每天 06:30
#   3. 操作：启动程序 → python.exe，参数填 auto_fetch.py 的完整路径
#
# 【macOS / Linux】crontab -e  （每天 06:30 运行）
#   30 6 * * * cd /你的路径/cbec-daily && /usr/bin/python3 auto_fetch.py && cp content.json app/content.json
#
# 【安卓手机】用 Pydroid 3 或 Termux，同样配置 cron
#
# 【零运维方案】把脚本部署到 GitHub Actions（免费），
#   每天定时抓取并自动提交，网页用 GitHub Pages 托管。
# ============================================================
