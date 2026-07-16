"""UI 工具：队旗、比分展示"""
from core.config import TEAM_FLAGS

# 英文名 → flagcdn 代码
TEAM_FLAGS_EN = {
    "Norway": "no", "France": "fr", "Brazil": "br", "Argentina": "ar",
    "England": "gb-eng", "Germany": "de", "Spain": "es", "Portugal": "pt",
    "Netherlands": "nl", "Belgium": "be", "Croatia": "hr", "Uruguay": "uy",
    "Mexico": "mx", "USA": "us", "Canada": "ca", "Senegal": "sn",
    "Japan": "jp", "South Korea": "kr", "Australia": "au", "Iran": "ir",
    "Qatar": "qa", "Saudi Arabia": "sa", "Ghana": "gh", "Tunisia": "tn",
    "Egypt": "eg", "Sweden": "se", "Switzerland": "ch", "Denmark": "dk",
    "Turkey": "tr", "Czechia": "cz", "Scotland": "gb-sct",
    "Ivory Coast": "ci", "South Africa": "za", "Haiti": "ht",
    "Panama": "pa", "New Zealand": "nz", "Colombia": "co",
    "Ecuador": "ec", "Paraguay": "py", "Austria": "at",
    "Morocco": "ma", "Algeria": "dz", "Bosnia-Herzegovina": "ba",
    "DR Congo": "cd", "Cape Verde": "cv", "Uzbekistan": "uz",
    "Iraq": "iq", "Curacao": "cw", "Jordan": "jo",
    "Italy": "it", "Poland": "pl", "Ukraine": "ua", "Peru": "pe",
    "Chile": "cl", "Hungary": "hu", "Romania": "ro", "Greece": "gr",
}


def flag_img(team_name: str, size: int = 20) -> str:
    """返回国旗 <img> HTML"""
    code = TEAM_FLAGS.get(team_name) or TEAM_FLAGS_EN.get(team_name, "")
    if code:
        return (
            f'<img src="https://flagcdn.com/24x18/{code}.png" '
            f'width="{size}" style="vertical-align:middle;border-radius:2px"> '
        )
    return ""


def goals_bar(home: str, away: str, eh: float, ea: float) -> str:
    """期望进球可视化条"""
    total = max(eh + ea, 0.1)
    h_pct = eh / total * 100
    a_pct = ea / total * 100
    return (
        f'<div style="margin-bottom:1em">'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:.85rem;color:#8899bb;margin-bottom:4px">'
        f'<span>{home}</span>'
        f'<span style="font-weight:700;color:#90caf9">{eh:.2f}</span></div>'
        f'<div style="height:9px;background:#1a2340;border-radius:4px;'
        f'overflow:hidden;margin-bottom:4px">'
        f'<div style="height:100%;width:{h_pct:.1f}%;'
        f'background:linear-gradient(90deg,#4a8cff,#64b5f6);border-radius:4px">'
        f'</div></div>'
        f'<div style="display:flex;justify-content:space-between;'
        f'font-size:.85rem;color:#8899bb;margin-bottom:4px">'
        f'<span>{away}</span>'
        f'<span style="font-weight:700;color:#90caf9">{ea:.2f}</span></div>'
        f'<div style="height:9px;background:#1a2340;border-radius:4px;'
        f'overflow:hidden">'
        f'<div style="height:100%;width:{a_pct:.1f}%;'
        f'background:linear-gradient(90deg,#2a3a5a,#4a6a9a);border-radius:4px">'
        f'</div></div></div>'
    )


def score_card_html(home: str, away: str, score: str,
                    hw: str, dw: str, aw: str, conf: str,
                    is_fresh: bool = False) -> str:
    """比分卡片 HTML"""
    h_flag = flag_img(home)
    a_flag = flag_img(away)
    cls = "score-card result-new" if is_fresh else "score-card"

    hwp = float(str(hw).rstrip("%")) if hw and hw != "?" else 0
    dwp = float(str(dw).rstrip("%")) if dw and dw != "?" else 0
    awp = float(str(aw).rstrip("%")) if aw and aw != "?" else 0
    tot = max(hwp + dwp + awp, 1)

    return f'''<div class="{cls}">
<div class="score">
<span class="score-fade" style="font-size:1.6rem">{h_flag}{home}</span>
 {score.replace("-", ":")}
<span class="score-fade" style="font-size:1.6rem">{a_flag}{away}</span>
</div>
<div class="probs">{hw} | {dw} | {aw} &nbsp; 置信度 {conf}</div>
<div class="prob-bar">
<div class="seg-home" style="width:{hwp/tot*100:.1f}%"></div>
<div class="seg-draw" style="width:{dwp/tot*100:.1f}%"></div>
<div class="seg-away" style="width:{awp/tot*100:.1f}%"></div>
</div></div>'''
