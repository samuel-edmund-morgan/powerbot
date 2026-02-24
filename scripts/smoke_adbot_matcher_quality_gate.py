#!/usr/bin/env python3
"""
Quality gate for adbot matcher on control scenarios.

Goal:
- keep recall high on real-like short resident questions,
- keep false-positive rate low on noisy/non-question texts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@dataclass(frozen=True)
class Case:
    text: str
    expected: str | None


def _run_gate() -> None:
    from adbot.matcher import analyze_intent_match
    from inline_special_queries import (
        INTENT_ACCOUNTING,
        INTENT_CAR_PASS,
        INTENT_ELECTRICIAN,
        INTENT_ELEVATOR,
        INTENT_LIGHT_STATUS,
        INTENT_PARKING,
        INTENT_PLUMBER,
        INTENT_SECURITY,
    )

    cases: tuple[Case, ...] = (
        Case("Дайте номер електрика будь ласка", INTENT_ELECTRICIAN),
        Case("Підкажіть телефон сантехніка, тече кран", INTENT_PLUMBER),
        Case("Є контакт охорони, треба номер", INTENT_SECURITY),
        Case("Потрібен номер по паркінгу", INTENT_PARKING),
        Case("Де оформити перепустку на авто?", INTENT_CAR_PASS),
        Case("Є номер диспетчера ліфтів?", INTENT_ELEVATOR),
        Case("Куди писати по квитанції за комуналку?", INTENT_ACCOUNTING),
        Case("Чи є світло в Ньюкасл зараз?", INTENT_LIGHT_STATUS),
        Case("Світло відключили чи вже дали?", INTENT_LIGHT_STATUS),
        Case("Нема світла, підкажіть чи аварія", INTENT_LIGHT_STATUS),
        Case("Потрібен телефон електрика", INTENT_ELECTRICIAN),
        Case("Хто знає сантехніка, вода тече", INTENT_PLUMBER),
        Case("Охорона на вишці, дайте контакт", INTENT_SECURITY),
        Case("Питання по паркомісцю, куди звертатись", INTENT_PARKING),
        Case("Як оформити пропуск для машини?", INTENT_CAR_PASS),
        Case("Ліфт застряг, де номер диспетчера?", INTENT_ELEVATOR),
        Case("Підкажіть бухгалтерію по нарахуванню", INTENT_ACCOUNTING),
        Case("Є світло чи ще відсутнє?", INTENT_LIGHT_STATUS),
        # Negatives (anti-false-positive control)
        Case(
            "Сьогодні була довга дискусія про ремонт, сусіди, домашні справи та покупки, "
            "і хтось один раз згадав слово номер, але це не питання до сервісів",
            None,
        ),
        Case(
            "Друзі, просто ділюсь фото двору і погоди, гарний ранок усім мешканцям",
            None,
        ),
        Case(
            "У мене довге повідомлення про шум вночі, паркування, дітей і сусідів без конкретного питання",
            None,
        ),
        Case(
            "Хтось сьогодні був на пошті? цікавлюсь графіком роботи відділення",
            None,
        ),
        Case(
            "Плануємо зустріч у дворі ввечері, приносіть чай та пледи",
            None,
        ),
        Case(
            "Є хто вигулює собаку вранці біля майданчика?",
            None,
        ),
        Case(
            "Сьогодні в нас фото-зустріч у дворі, долучайтесь",
            None,
        ),
        Case(
            "Порадьте, будь ласка, де купити лампу для кімнати, не терміново",
            None,
        ),
    )

    positives = [c for c in cases if c.expected is not None]
    negatives = [c for c in cases if c.expected is None]
    _assert(positives and negatives, "quality gate dataset must contain both positive and negative cases")

    pos_ok = 0
    neg_fp = 0
    misses: list[str] = []
    fps: list[str] = []

    for case in positives:
        diag = analyze_intent_match(case.text, min_len=10, max_len=280, min_confidence=120)
        actual = diag.intent.code if diag.intent else None
        if actual == case.expected:
            pos_ok += 1
        else:
            misses.append(f"{case.text} -> expected={case.expected}, got={actual}, reason={diag.reason}")

    for case in negatives:
        diag = analyze_intent_match(case.text, min_len=10, max_len=280, min_confidence=120)
        if diag.intent is not None:
            neg_fp += 1
            fps.append(f"{case.text} -> got={diag.intent.code}, reason={diag.reason}")

    recall = pos_ok / len(positives)
    fp_rate = neg_fp / len(negatives)

    _assert(
        recall >= 0.85,
        "adbot matcher recall quality gate failed: "
        f"recall={recall:.2%} < 85%.\nMisses:\n- " + "\n- ".join(misses[:8]),
    )
    _assert(
        fp_rate <= 0.15,
        "adbot matcher false-positive quality gate failed: "
        f"fp_rate={fp_rate:.2%} > 15%.\nFalse positives:\n- " + "\n- ".join(fps[:8]),
    )

    print(
        "OK: adbot matcher quality gate passed "
        f"(recall={recall:.2%}, false_positive_rate={fp_rate:.2%}, "
        f"positives={len(positives)}, negatives={len(negatives)})"
    )


def main() -> None:
    _run_gate()


if __name__ == "__main__":
    main()
