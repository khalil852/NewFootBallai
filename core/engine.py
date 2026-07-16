"""标准泊松推演 + 赔率反推 λ"""
import math
from itertools import product
from core.models import MatchPrediction, LambdaModifiers, BranchPrediction

MAX_GOALS = 8
LAM_MIN, LAM_MAX, LAM_STEP = 0.3, 3.5, 0.05


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _grid_lambdas():
    """生成 λ 搜索网格"""
    vals = []
    x = LAM_MIN
    while x <= LAM_MAX + 0.001:
        vals.append(round(x, 2))
        x += LAM_STEP
    return vals


def odds_to_lambda(p_home: float, p_draw: float, p_away: float,
                   over_under: float | None = None) -> tuple[float, float]:
    """赔率隐含概率 → 暴力搜最优 (λ_h, λ_a) 组合"""
    grid = _grid_lambdas()
    best_lam_h, best_lam_a = 1.5, 1.2
    best_div = float("inf")

    for lam_h, lam_a in product(grid, grid):
        hw, dw, aw = 0.0, 0.0, 0.0
        exp_total = 0.0
        for h, a in product(range(MAX_GOALS + 1), repeat=2):
            p = _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
            exp_total += (h + a) * p
            if h > a:
                hw += p
            elif h == a:
                dw += p
            else:
                aw += p

        div = abs(hw - p_home) + abs(dw - p_draw) + abs(aw - p_away)
        if over_under is not None:
            div += abs(exp_total - over_under) * 0.3

        div /= max(hw + dw + aw, 0.001)
        hw /= max(hw + dw + aw, 0.001)
        dw /= max(hw + dw + aw, 0.001)
        aw /= max(hw + dw + aw, 0.001)

        actual_div = abs(hw - p_home) + abs(dw - p_draw) + abs(aw - p_away)
        if over_under is not None:
            actual_div += abs(exp_total - over_under) * 0.3

        if actual_div < best_div:
            best_div = actual_div
            best_lam_h, best_lam_a = lam_h, lam_a

    return best_lam_h, best_lam_a


def predict_match(home: str, away: str,
                  lam_h0: float, lam_a0: float,
                  modifiers: LambdaModifiers,
                  odds: tuple[float, float, float] | None = None,
                  max_goals: int = MAX_GOALS) -> MatchPrediction:
    """标准泊松推演"""
    lam_h = modifiers.apply(lam_h0, is_home=True)
    lam_a = modifiers.apply(lam_a0, is_home=False)

    probs: dict[tuple[int, int], float] = {}
    for h, a in product(range(max_goals + 1), repeat=2):
        probs[(h, a)] = _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)

    total = sum(probs.values())
    if total > 0:
        probs = {k: v / total for k, v in probs.items()}

    hw, dw, aw, eh, ea = 0.0, 0.0, 0.0, 0.0, 0.0
    for (h, a), p in probs.items():
        eh += h * p
        ea += a * p
        if h > a:
            hw += p
        elif h == a:
            dw += p
        else:
            aw += p

    top_scores = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
    top5 = [((h, a), p) for (h, a), p in top_scores]

    locked_h, locked_a = round(eh), round(ea)

    best_hw = max(((h, a) for (h, a) in probs if h > a),
                  key=lambda x: probs[x], default=(1, 0))
    best_dr = max(((h, a) for (h, a) in probs if h == a),
                  key=lambda x: probs[x], default=(1, 1))
    best_aw = max(((h, a) for (h, a) in probs if h < a),
                  key=lambda x: probs[x], default=(0, 1))

    confidence = 1.0
    if odds:
        oh, od, oa = odds
        raw = [1.0 / oh, 1.0 / od, 1.0 / oa]
        ov = sum(raw)
        ih, id_, ia = raw[0] / ov, raw[1] / ov, raw[2] / ov
        div = abs(hw - ih) + abs(dw - id_) + abs(aw - ia)
        confidence = max(0.0, 1.0 - 0.5 * div)

    return MatchPrediction(
        home_team=home, away_team=away,
        lam_h=lam_h, lam_a=lam_a,
        home_win=hw, draw=dw, away_win=aw,
        exp_h=eh, exp_a=ea,
        locked_h=locked_h, locked_a=locked_a,
        top_scores=top5,
        confidence=confidence,
        result_scores={"home": best_hw, "draw": best_dr, "away": best_aw},
    )


def predict_branched(home: str, away: str,
                     lam_h0: float, lam_a0: float,
                     branches: list[dict]) -> BranchPrediction:
    """分支推演: 多个 modifier set 独立推演后加权融合"""
    results = []
    for b in branches:
        pred = predict_match(home, away, lam_h0, lam_a0, b["modifiers"])
        results.append({**b, "prediction": pred})

    if not results:
        return BranchPrediction(branches=[], blended=None)

    total_w = sum(r.get("weight", 1.0) for r in results)
    if total_w > 0:
        for r in results:
            r["weight"] /= total_w

    first = results[0]["prediction"]
    blended = MatchPrediction(
        home_team=first.home_team, away_team=first.away_team,
    )

    for field in ["home_win", "draw", "away_win", "exp_h", "exp_a", "confidence"]:
        val = sum(r["weight"] * getattr(r["prediction"], field) for r in results)
        setattr(blended, field, val)

    blended.locked_h = round(blended.exp_h)
    blended.locked_a = round(blended.exp_a)
    blended.lam_h = sum(r["weight"] * r["prediction"].lam_h for r in results)
    blended.lam_a = sum(r["weight"] * r["prediction"].lam_a for r in results)

    return BranchPrediction(branches=results, blended=blended)
