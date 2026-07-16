from dataclasses import dataclass, field


@dataclass
class LambdaModifiers:
    attack: float = 1.0
    defense: float = 1.0
    tactical: float = 1.0
    coach_intent: float = 1.0
    scenario: float = 1.0
    home_adv: float = 1.08
    confidence: float = 1.0

    def apply(self, lam: float, is_home: bool = False) -> float:
        factor = (
            self.attack * self.defense * self.tactical
            * self.coach_intent * self.scenario
        )
        if is_home:
            factor *= self.home_adv
        return max(0.05, lam * factor)


@dataclass
class MatchPrediction:
    home_team: str = ""
    away_team: str = ""
    lam_h: float = 0.0
    lam_a: float = 0.0
    home_win: float = 0.0
    draw: float = 0.0
    away_win: float = 0.0
    exp_h: float = 0.0
    exp_a: float = 0.0
    locked_h: int = 0
    locked_a: int = 0
    top_scores: list = field(default_factory=list)
    confidence: float = 0.0
    result_scores: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "主队": self.home_team,
            "客队": self.away_team,
            "主队λ": round(self.lam_h, 2),
            "客队λ": round(self.lam_a, 2),
            "期望进球": f"{self.exp_h:.2f}-{self.exp_a:.2f}",
            "主胜概率": f"{self.home_win:.1%}",
            "平局概率": f"{self.draw:.1%}",
            "客胜概率": f"{self.away_win:.1%}",
            "锁定比分": f"{self.locked_h}-{self.locked_a}",
            "最可能主胜比分": f"{self.result_scores.get('home', (0,0))[0]}-{self.result_scores.get('home', (0,0))[1]}",
            "最可能平局比分": f"{self.result_scores.get('draw', (0,0))[0]}-{self.result_scores.get('draw', (0,0))[1]}",
            "最可能客胜比分": f"{self.result_scores.get('away', (0,0))[0]}-{self.result_scores.get('away', (0,0))[1]}",
            "比分概率": [f"{h}-{a}({p:.1%})" for (h, a), p in self.top_scores[:5]],
            "模型置信度": f"{self.confidence:.0%}",
        }


@dataclass
class BranchPrediction:
    branches: list = field(default_factory=list)
    blended: MatchPrediction | None = None


@dataclass
class CalibrationResult:
    accuracy_score: float = 0.0
    score_match: bool = False
    result_match: bool = False
    goal_deviation: float = 0.0
    xG_h: float | None = None
    xG_a: float | None = None
