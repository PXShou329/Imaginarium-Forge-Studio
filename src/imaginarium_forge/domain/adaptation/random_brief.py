"""Bounded offline inspiration for an editable screenplay brief."""

from __future__ import annotations

import random
import secrets
from dataclasses import dataclass

from imaginarium_forge.domain.adaptation.models import (
    DialogueRetention,
    ScreenplayBrief,
    ScreenplayPacing,
)


@dataclass(frozen=True, slots=True)
class RandomScreenplayBrief:
    seed: int
    brief: ScreenplayBrief


def generate_random_screenplay_brief(seed: int | None = None) -> RandomScreenplayBrief:
    """Return a coherent local-only brief that remains author-editable.

    Curated frames keep the positive and prohibited lists separate.  The
    function never calls a provider and never persists or accepts a revision.
    """

    if seed is None:
        seed = secrets.randbelow(2_147_483_648)
    if not 0 <= seed <= 2_147_483_647:
        raise ValueError("seed must be between 0 and 2147483647")
    rng = random.Random(seed)
    frames = (
        (
            "用具體行動與可拍攝的視覺線索推動轉折，讓角色的最後選擇收束核心衝突。",
            ("保留原小說的因果與結局", "讓前段視覺線索在結尾獲得回收"),
            ("新增未經原小說支持的 Canon", "以旁白摘要取代關鍵抉擇"),
        ),
        (
            "把內心敘述轉成表演、走位與環境反應；維持原作情緒，但讓場景節奏適合短片。",
            ("保留主角的核心動機", "安排清楚的場景進出點"),
            ("讓角色無理由改變立場", "用夢境抹除原作後果"),
        ),
        (
            "以角色關係為主軸重組場次，重要資訊透過對白與動作逐步揭露，結局保持完整。",
            ("保留至少一段代表性對白", "讓高潮由角色主動選擇觸發"),
            ("加入與來源無關的新支線", "覆寫原小說的正式事實"),
        ),
    )
    direction, must_include, must_avoid = rng.choice(frames)
    return RandomScreenplayBrief(
        seed=seed,
        brief=ScreenplayBrief(
            target_minutes=rng.choice((5, 8, 12, 15, 20)),
            pacing=rng.choice(tuple(ScreenplayPacing)),
            dialogue_retention=rng.choice(tuple(DialogueRetention)),
            additional_direction=direction,
            must_include=must_include,
            must_avoid=must_avoid,
        ),
    )


__all__ = ["RandomScreenplayBrief", "generate_random_screenplay_brief"]
