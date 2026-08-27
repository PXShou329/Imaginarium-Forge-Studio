"""Immutable author brief for one complete short-story candidate.

The complete-story path deliberately remains a purpose of the existing Story
Studio draft pipeline.  It creates another editable, unaccepted scene draft;
it does not introduce a second persistence model or overwrite accepted prose.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from imaginarium_forge.canonical import canonical_json


class StoryGenerationPurpose(StrEnum):
    """Provider-visible prose unit requested by the author."""

    SCENE = "scene"
    COMPLETE_SHORT_STORY = "complete_short_story"


class CompleteStoryBriefMode(StrEnum):
    """How the bounded author brief was prepared."""

    GUIDED = "guided"
    RANDOM = "random"


class CompleteStoryBrief(BaseModel):
    """Bounded, immutable inputs for one complete short-story candidate.

    The complete canonical JSON is safe to persist in a generation input
    snapshot.  It contains author story material, never credentials, and is
    sufficient to reconstruct provider messages even when raw message storage
    is disabled.
    """

    model_config = ConfigDict(frozen=True)

    mode: CompleteStoryBriefMode = CompleteStoryBriefMode.GUIDED
    world_premise: str = Field(default="", max_length=4_000)
    story_seed: str = Field(default="", max_length=4_000)
    structure_outline: str = Field(default="", max_length=6_000)
    additional_direction: str = Field(default="", max_length=2_000)
    must_include: tuple[str, ...] = Field(default=(), max_length=12)
    must_avoid: tuple[str, ...] = Field(default=(), max_length=12)
    min_visible_chars: int = Field(default=1_800, ge=500, le=30_000)
    max_visible_chars: int = Field(default=5_000, ge=500, le=40_000)
    pacing: str = Field(default="均衡推進，在結尾完整收束", max_length=300)
    random_seed: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @field_validator(
        "world_premise",
        "story_seed",
        "structure_outline",
        "additional_direction",
        "pacing",
    )
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("must_include", "must_avoid")
    @classmethod
    def _bounded_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("brief list items must not be blank")
            if len(text) > 300:
                raise ValueError("brief list items must be at most 300 characters")
            normalized.append(text)
        return tuple(normalized)

    @model_validator(mode="after")
    def _coherent_brief(self) -> CompleteStoryBrief:
        if self.min_visible_chars > self.max_visible_chars:
            raise ValueError("min_visible_chars must not exceed max_visible_chars")
        if self.mode is CompleteStoryBriefMode.RANDOM:
            if self.random_seed is None:
                raise ValueError("random complete-story brief requires random_seed")
            if not any(
                (
                    self.world_premise,
                    self.story_seed,
                    self.structure_outline,
                    self.additional_direction,
                    self.must_include,
                )
            ):
                raise ValueError("random complete-story brief needs generated content")
        return self

    def canonical_payload(self) -> str:
        """Return the exact deterministic JSON stored in the run snapshot."""

        return canonical_json(self.model_dump(mode="json"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()

    def render_traditional_chinese(self) -> str:
        """Render every field as bounded, structured Traditional Chinese data."""

        def text_or_none(value: str) -> str:
            return value if value else "（未提供）"

        def items_or_none(values: tuple[str, ...]) -> str:
            return "；".join(values) if values else "（未提供）"

        mode_label = "引導創作" if self.mode is CompleteStoryBriefMode.GUIDED else "種子隨機創作"
        seed_label = "（不適用）" if self.random_seed is None else str(self.random_seed)
        return "\n".join(
            (
                f"創作方式：{mode_label}",
                f"世界前提：{text_or_none(self.world_premise)}",
                f"故事種子／短故事：{text_or_none(self.story_seed)}",
                f"故事大綱／結構：{text_or_none(self.structure_outline)}",
                f"作者補充方向：{text_or_none(self.additional_direction)}",
                f"必須包含：{items_or_none(self.must_include)}",
                f"必須避免：{items_or_none(self.must_avoid)}",
                f"可見正文長度：{self.min_visible_chars} 至 {self.max_visible_chars} 字元",
                f"節奏：{text_or_none(self.pacing)}",
                f"隨機種子：{seed_label}",
            )
        )


def generate_random_complete_story_brief(
    seed: int | None = None,
) -> CompleteStoryBrief:
    """Create a coherent random brief using only a caller-supplied seed.

    ``random.Random`` is local, so generation never mutates process-global
    randomness.  The small curated scenario frames keep premise, conflict,
    turn and resolution mutually coherent instead of mixing unrelated tags.
    """

    if seed is None:
        seed = secrets.randbelow(2_147_483_648)
    if not 0 <= seed <= 2_147_483_647:
        raise ValueError("seed must be between 0 and 2147483647")
    rng = random.Random(seed)
    frames = (
        (
            "一座每逢雨夜就會交換居民記憶的山城",
            "替人修復舊傘的青年發現亡姊的記憶出現在陌生旅客身上",
            "青年必須在下一場雨前找出交換規律，並決定要追回姊姊，還是讓旅客保有完整人生",
            "旅客主動交出最後一段記憶，揭露姊姊當年自願阻止全城失憶",
            "青年保留姊姊的選擇而非複製她，修好城中央的引雨塔，讓記憶回到各自生命",
            "破傘上的紅線",
        ),
        (
            "海面退去後，沿岸只剩一列每日準時行駛的無人夜車",
            "失去聲音的報站員在空車廂收到一張寫著自己名字的明日車票",
            "她沿線尋找乘客消失的原因，同時阻止列車把最後一座避難城帶入乾涸海床",
            "車票不是死亡預告，而是前任報站員留下的交接訊息；她的聲音被用來維持錯誤路線",
            "她改用車輪節奏完成最後一次廣播，讓列車轉向並在日出時停靠新海岸",
            "永遠慢一分鐘的站鐘",
        ),
        (
            "所有影子都由市政府保管，只有成年那天才能領回的玻璃城",
            "替人校對身分檔案的職員發現一名孩子有兩個影子，卻沒有出生紀錄",
            "職員在年度銷毀前追查被刪除的家庭，並躲避相信無影者不算公民的主管",
            "第二個影子屬於職員自己被抹去的童年，而孩子一直替她保存證據",
            "她公開影庫帳冊，讓居民選擇自己的過去；孩子取得名字，她也接受不完整但真實的身分",
            "會在玻璃上留下指紋的黑色紙鶴",
        ),
        (
            "冬至之後時間停止，村落只能靠說完一個真實故事換取一天黎明",
            "從不說真話的巡迴說書人被迫替村民主持最後一次換日儀式",
            "他必須查出歷年故事為何失效，並在黑夜吞沒火種前說出自己逃離村落的真相",
            "換日要的不是悲劇，而是當事人承認自己改寫過的責任；村長一直替全村承擔謊言",
            "說書人承認背叛並留下修復後果，黎明重新出現，但村民不再把延續世界的責任交給一人",
            "每次謊言都會少一個刻度的日晷",
        ),
    )
    world, opening, conflict, turn, ending, motif = rng.choice(frames)
    pacing = rng.choice(
        (
            "開場迅速建立異常，中段逐步收緊選擇，轉折後留足情感收束",
            "前段懸疑探索，中段以角色關係推進，結尾用具體行動完成閉環",
            "節奏明快但不跳躍，讓關鍵證據各自回收，最後一幕安靜落地",
        )
    )
    min_chars, max_chars = rng.choice(((1_800, 3_500), (2_500, 4_500), (3_000, 5_500)))
    return CompleteStoryBrief(
        mode=CompleteStoryBriefMode.RANDOM,
        world_premise=world,
        story_seed=opening,
        structure_outline=(f"起：{opening}。承：{conflict}。轉：{turn}。合：{ending}。"),
        additional_direction="以角色選擇推動因果，不靠巧合解決核心衝突。",
        must_include=(motif, "前段線索在結局獲得可辨識的回收"),
        must_avoid=("夢醒後一切都沒發生", "以旁白摘要取代關鍵抉擇"),
        min_visible_chars=min_chars,
        max_visible_chars=max_chars,
        pacing=pacing,
        random_seed=seed,
    )
