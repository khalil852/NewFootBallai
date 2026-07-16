"""模型 Benchmark: 16 场推演+校准，对比 4 种模型组合的准确率/速度/质量"""
import json, time, sys
sys.path.insert(0, ".")

from core.search import _deepseek_chat, search_qualitative, search_quantitative, extract_odds, extract_structured, clean_report
from core.config import (
    DEEPSEEK_KEY, PROMPT_SEARCH, PROMPT_ANALYSIS,
    MODEL_FAST, MODEL_FAST_THINK, MODEL_PRO, MODEL_PRO_THINK,
)
from core.engine import predict_match, odds_to_lambda
from core.rules import run_rules, detect_knockout
from core.models import LambdaModifiers

# ── 4 种模型组合 ──
COMBOS = [
    ("flash",          MODEL_FAST),        # flash 非思考
    ("flash+think",    MODEL_FAST_THINK),  # flash 思考模式
    ("pro",            MODEL_PRO),          # pro 非思考
    ("pro+think",      MODEL_PRO_THINK),   # pro 思考模式
]

# ── 16 场真实比赛（已结束、有确定比分） ──
MATCHES = [
    ("阿根廷 vs 法国", "3-3"),     # 2022世界杯决赛(点球4-2,90分钟3-3)
    ("法国 vs 摩洛哥", "2-0"),     # 2022世界杯半决赛
    ("阿根廷 vs 克罗地亚", "3-0"),  # 2022世界杯半决赛
    ("英格兰 vs 法国", "1-2"),     # 2022世界杯1/4决赛
    ("荷兰 vs 阿根廷", "2-2"),     # 2022世界杯1/4决赛(点球3-4)
    ("巴西 vs 克罗地亚", "1-1"),    # 2022世界杯1/4决赛(点球2-4)
    ("葡萄牙 vs 瑞士", "6-1"),     # 2022世界杯1/8决赛
    ("日本 vs 克罗地亚", "1-1"),    # 2022世界杯1/8决赛(点球1-3)
    ("西班牙 vs 德国", "1-1"),     # 2022世界杯小组赛
    ("日本 vs 西班牙", "2-1"),     # 2022世界杯小组赛
    ("韩国 vs 葡萄牙", "2-1"),     # 2022世界杯小组赛
    ("阿根廷 vs 沙特", "1-2"),     # 2022世界杯小组赛
    ("德国 vs 日本", "1-2"),       # 2022世界杯小组赛
    ("巴西 vs 塞尔维亚", "2-0"),    # 2022世界杯小组赛
    ("意大利 vs 英格兰", "1-0"),    # 2024欧洲杯预选赛
    ("西班牙 vs 格鲁吉亚", "4-1"),  # 2024欧洲杯1/8决赛
]

RUNS_PER_COMBO = 1  # 4组合 × 16场 = 64次推演

RESULTS = []
LAWS = []  # 空的，只用教练库+内置修正

print("=" * 65)
print("  Model Benchmark — 16 场推演校准")
print(f"  4 组合 × 16 场 = 64 次推演（每次 search+analysis）")
print("=" * 65)

for combo_name, model_cfg in COMBOS:
    print(f"\n{'='*65}")
    print(f"  [{combo_name}] {model_cfg.get('model','?')} "
          f"think={'max' if 'reasoning_effort' in model_cfg else 'off'}")
    print(f"{'='*65}")

    combo_results = []

    for i, (match, actual_score) in enumerate(MATCHES):
        print(f"  [{combo_name}] {i+1:2d}/16  {match} ...", end=" ", flush=True)

        t_start = time.time()
        search_ok = True
        analysis_ok = True
        pred = None

        try:
            # ── Step 1: Search ──
            quant_data = search_quantitative(match)
            qual_data = search_qualitative(match)
            combined = (quant_data + "\n" + qual_data).strip()

            if combined:
                sys_prompt = PROMPT_SEARCH
                user_prompt = (
                    f"为 {match} 搜集赛前信息并输出结构化数据。\n"
                    f"定量数据(赔率等):\n{quant_data}\n\n"
                    f"定性数据(伤病/阵容):\n{qual_data}"
                )
                sr = _deepseek_chat(sys_prompt, user_prompt, DEEPSEEK_KEY, model_cfg)
            else:
                sr = ""

            if not sr:
                print("搜索失败")
                search_ok = False
                combo_results.append(None)
                continue

            search_report = clean_report(sr)
            structured = extract_structured(sr)
            is_ko = detect_knockout(search_report, structured)

            # ── Step 2: Rules ──
            rules_result = run_rules(search_report, structured, match, LAWS)
            modifiers = rules_result["modifiers"]

            # ── Step 3: Engine ──
            odds_data = extract_odds(quant_data)
            lam_h0, lam_a0 = 1.5, 1.2
            odds_tuple = None

            if odds_data.get("odds_h") and odds_data.get("odds_d") and odds_data.get("odds_a"):
                oh, od, oa = odds_data["odds_h"], odds_data["odds_d"], odds_data["odds_a"]
                raw = [1.0 / oh, 1.0 / od, 1.0 / oa]
                ov = sum(raw)
                p_h, p_d, p_a = raw[0] / ov, raw[1] / ov, raw[2] / ov
                lam_h0, lam_a0 = odds_to_lambda(p_h, p_d, p_a)
                odds_tuple = (oh, od, oa)

            from core.rules import _parse_teams
            parsed = _parse_teams(match)
            t1 = parsed[0] or "" if parsed else ""
            t2 = parsed[1] or "" if parsed[1] else ""

            pred = predict_match(t1, t2, lam_h0, lam_a0, modifiers, odds_tuple)

            # ── Step 4: Analysis ──
            math_json = json.dumps({
                "淘汰赛": is_ko, **pred.to_json(),
                "定律修正因子": {
                    "attack": round(modifiers.attack, 3),
                    "defense": round(modifiers.defense, 3),
                },
                "触发定律": rules_result["triggered"],
            }, ensure_ascii=False)

            report_prompt = (
                f"赛前数据:\n{search_report[:4000]}\n\n"
                f"数学计算结果:\n{json.dumps(pred.to_json(), ensure_ascii=False)}\n"
            )
            analysis = _deepseek_chat(PROMPT_ANALYSIS, report_prompt, DEEPSEEK_KEY, model_cfg)

            if not analysis:
                print("分析失败")
                analysis_ok = False
                combo_results.append(None)
                continue

            # ── Calibration ──
            actual_parts = actual_score.split("-")
            ah, aa = int(actual_parts[0]), int(actual_parts[1])

            score_match = (pred.locked_h == ah and pred.locked_a == aa)
            pred_result = "home" if pred.home_win > max(pred.draw, pred.away_win) \
                else "draw" if pred.draw > max(pred.home_win, pred.away_win) else "away"
            actual_result = "home" if ah > aa else "draw" if ah == aa else "away"
            result_match = pred_result == actual_result
            dev = abs(pred.locked_h - ah) + abs(pred.locked_a - aa)
            accuracy = max(0, min(100, 100 - dev * 15 - (0 if result_match else 25)
                                 - (0 if pred.confidence >= 0.5 else 10)))

            elapsed = time.time() - t_start

            r = {
                "match": match,
                "actual": actual_score,
                "predicted": f"{pred.locked_h}-{pred.locked_a}",
                "accuracy": round(accuracy, 1),
                "score_match": score_match,
                "result_match": result_match,
                "deviation": dev,
                "time_s": round(elapsed, 1),
                "search_ok": search_ok,
                "analysis_ok": analysis_ok,
                "analysis_len": len(analysis) if analysis else 0,
            }
            combo_results.append(r)
            print(f"推演{pred.locked_h}-{pred.locked_a} 实际{actual_score} "
                  f"准确率{accuracy:.0f} 耗时{elapsed:.0f}s")

        except Exception as e:
            elapsed = time.time() - t_start
            print(f"异常: {e}")
            combo_results.append({"match": match, "error": str(e), "time_s": round(elapsed, 1)})

    # ── 汇总本组合 ──
    valid = [r for r in combo_results if r and "error" not in r]
    n = len(valid)
    avg_acc = avg_time = 0.0
    score_hit = result_hit = 0
    if valid:
        avg_acc = sum(r["accuracy"] for r in valid) / n
        avg_time = sum(r["time_s"] for r in valid) / n
        score_hit = sum(1 for r in valid if r["score_match"])
        result_hit = sum(1 for r in valid if r["result_match"])

    RESULTS.append({
        "combo": combo_name,
        "model": model_cfg.get("model", "?"),
        "think": "max" if "reasoning_effort" in model_cfg else "off",
        "total": len(combo_results),
        "success": n,
        "avg_accuracy": round(avg_acc, 1),
        "avg_time_s": round(avg_time, 1),
        "score_hit_rate": f"{score_hit}/{n} = {round(score_hit/n*100)}%" if n else "N/A",
        "result_hit_rate": f"{result_hit}/{n} = {round(result_hit/n*100)}%" if n else "N/A",
    })
    if valid:
        print(f"\n  汇总: 准确率 {avg_acc:.1f} | 速度 {avg_time:.1f}s | "
              f"比分命中 {score_hit}/{n} | 胜负命中 {result_hit}/{n}")

# ── 最终排名 ──
print(f"\n{'='*65}")
print("  最终排名")
print(f"{'='*65}")
print(f"{'组合':<14s} {'准确率':>6s} {'速度':>6s} {'比分命中':>10s} {'胜负命中':>10s}")
print("-" * 50)
for r in sorted(RESULTS, key=lambda x: x["avg_accuracy"], reverse=True):
    print(f"{r['combo']:<14s} {r['avg_accuracy']:>5.1f}  {r['avg_time_s']:>5.1f}s "
          f"{r['score_hit_rate']:>10s} {r['result_hit_rate']:>10s}")

# 保存结果
with open("benchmark_results.json", "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存到 benchmark_results.json")
