import streamlit as st

# ── Supabase ──
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")

# ── API Keys ──
DEEPSEEK_KEY = st.secrets.get("default_deepseek_key", "")
TAVILY_KEY = st.secrets.get("default_tavily_key", "")

# ── API Endpoints ──
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
TAVILY_URL = "https://api.tavily.com/search"

# ── Models ──
# deepseek-v4-flash: 快速，适合搜索汇总和报告格式化
# deepseek-v4-pro:  更强推理，适合校准分析
# thinking_mode=True: reasoning_effort="max" 开启深度思考

MODEL_FAST = {"model": "deepseek-v4-flash"}
MODEL_FAST_THINK = {"model": "deepseek-v4-flash", "reasoning_effort": "max", "max_tokens": 4096}
MODEL_PRO = {"model": "deepseek-v4-pro"}
MODEL_PRO_THINK = {"model": "deepseek-v4-pro", "reasoning_effort": "max", "max_tokens": 4096}

# 各模块默认模型分配（基于 16 场 benchmark 结果）
MODEL_SEARCH = MODEL_FAST          # flash: 13/16成功, 48.8s — 最稳定
MODEL_ANALYSIS = MODEL_FAST        # flash: 报告格式化不需要推理
MODEL_CALIBRATE = MODEL_PRO        # pro 非思考: 11/16 成功 — 比 thinking 稳定
# 备选: 如果 pro 失败率高，回退 flash
MODEL_CALIBRATE_FALLBACK = MODEL_FAST

# 旧模型名保留向后兼容
MODEL_DEFAULT = MODEL_FAST
MODEL_REASONING = MODEL_PRO_THINK

# ── Tavily Domain Whitelists ──
QUANT_DOMAINS = [
    "flashscore.com",
    "sofascore.com",
    "fotmob.com",
    "oddsportal.com",
]

QUAL_DOMAINS = [
    "skysports.com",
    "espn.com",
    "bbc.com",
    "transfermarkt.com",
    "dongqiudi.com",
]

CALIBRATE_DOMAINS = [
    "flashscore.com",
    "sofascore.com",
    "fotmob.com",
]

# ── Default λ ──
DEFAULT_LAMBDA = 1.5

# ── Team name mappings ──
TEAM_EN = {
    "挪威": "Norway", "法国": "France", "巴西": "Brazil", "阿根廷": "Argentina",
    "英格兰": "England", "德国": "Germany", "西班牙": "Spain", "葡萄牙": "Portugal",
    "荷兰": "Netherlands", "比利时": "Belgium", "克罗地亚": "Croatia", "乌拉圭": "Uruguay",
    "墨西哥": "Mexico", "美国": "USA", "加拿大": "Canada", "塞内加尔": "Senegal",
    "日本": "Japan", "韩国": "South Korea", "澳大利亚": "Australia", "伊朗": "Iran",
    "卡塔尔": "Qatar", "沙特": "Saudi Arabia", "加纳": "Ghana", "突尼斯": "Tunisia",
    "埃及": "Egypt", "瑞典": "Sweden", "瑞士": "Switzerland", "丹麦": "Denmark",
    "土耳其": "Turkey", "捷克": "Czechia", "苏格兰": "Scotland", "科特迪瓦": "Ivory Coast",
    "南非": "South Africa", "海地": "Haiti", "巴拿马": "Panama", "新西兰": "New Zealand",
    "哥伦比亚": "Colombia", "厄瓜多尔": "Ecuador", "巴拉圭": "Paraguay", "奥地利": "Austria",
    "摩洛哥": "Morocco", "阿尔及利亚": "Algeria", "波黑": "Bosnia-Herzegovina",
    "刚果": "DR Congo", "佛得角": "Cape Verde", "乌兹别克": "Uzbekistan",
    "伊拉克": "Iraq", "库拉索": "Curacao", "约旦": "Jordan",
    "意大利": "Italy", "波兰": "Poland", "乌克兰": "Ukraine", "秘鲁": "Peru",
    "智利": "Chile", "匈牙利": "Hungary", "罗马尼亚": "Romania", "希腊": "Greece",
}

# ── Team Flags (flagcdn codes) ──
TEAM_FLAGS = {
    "挪威": "no", "法国": "fr", "巴西": "br", "阿根廷": "ar",
    "英格兰": "gb-eng", "德国": "de", "西班牙": "es", "葡萄牙": "pt",
    "荷兰": "nl", "比利时": "be", "克罗地亚": "hr", "乌拉圭": "uy",
    "墨西哥": "mx", "美国": "us", "加拿大": "ca", "塞内加尔": "sn",
    "日本": "jp", "韩国": "kr", "澳大利亚": "au", "伊朗": "ir",
    "卡塔尔": "qa", "沙特": "sa", "加纳": "gh", "突尼斯": "tn",
    "埃及": "eg", "瑞典": "se", "瑞士": "ch", "丹麦": "dk",
    "土耳其": "tr", "捷克": "cz", "苏格兰": "gb-sct", "科特迪瓦": "ci",
    "南非": "za", "海地": "ht", "巴拿马": "pa", "新西兰": "nz",
    "哥伦比亚": "co", "厄瓜多尔": "ec", "巴拉圭": "py", "奥地利": "at",
    "摩洛哥": "ma", "阿尔及利亚": "dz", "波黑": "ba",
    "刚果": "cd", "佛得角": "cv", "乌兹别克": "uz",
    "伊拉克": "iq", "库拉索": "cw", "约旦": "jo",
    "意大利": "it", "波兰": "pl", "乌克兰": "ua", "秘鲁": "pe",
    "智利": "cl", "匈牙利": "hu", "罗马尼亚": "ro", "希腊": "gr",
}

# ── Prompts ──
PROMPT_SEARCH = st.secrets.get("search_prompt", """# 足球赛前分析师

## 核心原则
- 仅基于搜索结果，搜不到标「暂无」
- 标注数据来源
- 2026世界杯，不是2022
- **所有时间统一用北京时间(UTC+8)，不要用当地时间**

## 输出
### 比赛信息
赛事 | 日期 | 开赛时间(YYYY-MM-DD HH:MM+时区) | 场地

### 伤病/停赛
**主队:** [球员+状态+来源]
**客队:** [球员+状态+来源]

### 首发预测
**主队 [阵型]:** [球员列表]
**客队 [阵型]:** [球员列表]

### 教练发言
**主队教练:** [一句话摘要]
**客队教练:** [一句话摘要]

### 赔率
主胜 X.XX | 平 X.XX | 客胜 X.XX | 总进球2.5大/小 X.XX/X.XX

### 出线形势
[主队积分/形势] | [客队积分/形势]

### 看点
1. [最关键对位]
2. [第二看点]
3. [第三看点]

---
在报告末尾输出结构化数据（不要改动格式）:
<!--STRUCTURED-->{"match_time":"YYYY-MM-DD HH:MM+08:00","injuries":[{"team":"","player":"","status":"","role":""}],"lineups":{"home":"","away":""},"coach_intent":{"home":"L3","away":"L3"},"scenario":[],"key_players":[{"team":"","player":"","status":""}],"uncertainty":[{"player":"","scenario_a":"","weight_a":0.5,"scenario_b":"","weight_b":0.5}]}<!--END-->""")

PROMPT_ANALYSIS = st.secrets.get("analysis_prompt", """将数学计算结果格式化为推演报告。

## 硬性规则
- 期望进球、胜负概率直接引用 JSON 数值，不要改动
- 锁定比分自行从概率数据中推理
- 不要输出 SCORE_CARD 块（数据卡片由代码渲染）
- 90分钟比分格式: (球队A)X:X(球队B)

## 报告结构 (不超过400字)

### 🎯 推演比分
**(球队A)X:X(球队B)**  90分钟常规时间

## 教练意图评级
| 球队 | 评级 | 依据 |
|------|------|------|
| 主队 | L1-L5 | 1句话 |
| 客队 | L1-L5 | 1句话 |

L1=极度保守/轮换 | L2=防守反击 | L3=均衡 | L4=主动进攻 | L5=全力压上

### ⏱ 关键时间窗口
### 概率
主胜 X% | 平 X% | 客胜 X% | 置信度 X%
### 修正摘要
[触发了哪些修正因子]

<details><summary>📊 完整数据</summary>
修正因子 | 比分概率 top5 | λ主/客
</details>""")

PROMPT_CALIBRATE = st.secrets.get("calibrate_prompt", """你是赛后校准分析师，拥有长期记忆。

## 校准规则
1. 赛后数据为准
2. 不确定标「暂无」
3. 提炼新定律的触发条件必须是赛前可得的

## 你可以用的触发条件 ONLY
✅ keyword — 关键词出现在赛前报告中
✅ team — 特定球队
✅ coach — 教练属性(style/aggression/def_line)
✅ match_type — 淘汰赛/小组赛
✅ always — 始终生效

## 禁止
❌ 赛后结果作为条件 → 开赛前不知
❌ 上半场/下半场事件 → 开赛前不知
❌ 实际射门/xG/技术统计 → 赛后统计

## 输出
### 准确率: XX/100
<small>推演 X-X | 实际 X-X | 偏差 X球 | 比分命中 Y/N | 胜负命中 Y/N</small>

### 差异
✅ [被验证的逻辑, ≤1句]
⚠️ [被推翻的逻辑, ≤1句]

<details><summary>📎 定律更新</summary>
建议新增:
```json
[{"name":"","tree":"","parent_id":null,"trigger_mode":"keyword","trigger_config":{"keywords":[]},"modifier_map":{}}]
```
建议修改(含id):
```json
[{"id":"","name":"","trigger_config":{},"modifier_map":{}}]
```
建议降级(最近5次准确率<30%的定律id):
```json
["law_id_1","law_id_2"]
```
</details>
总字数≤300""")
