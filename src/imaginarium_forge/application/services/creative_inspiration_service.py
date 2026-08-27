"""Offline inspiration for the Creative Launchpad.

The generator intentionally uses curated local fragments.  It never calls a
provider, performs network I/O, writes to the database, or grants mature
content eligibility.  Suggestions are editable draft values; author-provided
names always remain authoritative in the UI.
"""

from __future__ import annotations

import random
import re
from enum import StrEnum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from imaginarium_forge.domain.creative.models import GenreFamily

_ENGLISH_KEYWORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-/]*$")


class InspirationKind(StrEnum):
    STORY = "story"
    CHARACTER = "character"
    WORLD = "world"


class CreativeInspiration(BaseModel):
    """One local suggestion that can be copied into editable Launchpad fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: InspirationKind
    title_suggestion: str = ""
    character_name_suggestion: str = ""
    concept: str = ""
    primary_genre: GenreFamily = GenreFamily.FANTASY
    genre_tags: tuple[str, ...] = ()
    tone: str = ""
    setting: str = ""
    time_period: str = ""
    world_rules: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    social_context: str = ""
    technology_or_magic: str = ""
    central_conflict: str = ""
    themes: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    must_avoid: tuple[str, ...] = ()
    pacing: str = ""
    prose_style_notes: str = ""
    dialogue_density: str = ""
    direction: str = ""
    ending_preference: str = ""
    character_age_suggestion: int | None = Field(default=None, ge=0, le=200)
    character_biography: str = ""
    character_personality: str = ""
    character_voice: str = ""
    character_identity: str = ""
    character_face: str = ""
    character_hair: str = ""
    character_eyes: str = ""
    character_body: str = ""
    character_distinguishing_features: tuple[str, ...] = ()
    character_prohibited_mutations: tuple[str, ...] = ()
    character_action: str = ""
    character_expression: str = ""
    character_motivation: str = ""
    relationship_hooks: tuple[str, ...] = ()
    english_character_keywords: tuple[str, ...] = ()

    @field_validator("english_character_keywords")
    @classmethod
    def _validate_english_keywords(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = normalize_english_keywords(values)
        if normalized != values:
            raise ValueError("英文角色關鍵字必須已正規化且不得重複")
        return values

    @property
    def english_character_prompt(self) -> str:
        return render_english_keywords(self.english_character_keywords)


def normalize_english_keywords(values: tuple[str, ...] | list[str] | str) -> tuple[str, ...]:
    """Return unique English comma-separated prompt tokens in authored order.

    A string is parsed as comma-separated input.  Non-English tokens fail
    closed instead of being silently presented as a translation.
    """

    raw_values = values.split(",") if isinstance(values, str) else values
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = " ".join(raw.strip().split())
        if not value:
            continue
        if "," in value or not value.isascii() or not _ENGLISH_KEYWORD.fullmatch(value):
            raise ValueError(f"角色提示詞必須是英文關鍵字：{value}")
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return tuple(result)


def render_english_keywords(values: tuple[str, ...] | list[str] | str) -> str:
    """Render canonical comma-and-space separated English prompt text."""

    return ", ".join(normalize_english_keywords(values))


_GENRES: tuple[tuple[GenreFamily, tuple[str, ...]], ...] = (
    (GenreFamily.FANTASY, ("奇幻", "古老誓約", "失落文明")),
    (GenreFamily.SCIENCE_FICTION, ("科幻", "未知訊號", "身份邊界")),
    (GenreFamily.MYSTERY_CRIME, ("懸疑", "密室真相", "不可靠證詞")),
    (GenreFamily.HORROR, ("恐怖", "禁忌場所", "逐步異化")),
    (GenreFamily.ROMANCE, ("情感", "信任", "兩難選擇")),
    (GenreFamily.ACTION_ADVENTURE, ("冒險", "限時任務", "危險旅途")),
    (GenreFamily.DRAMA_LITERARY, ("劇情", "記憶", "自我認同")),
)

_SETTINGS = (
    ("被永久暮色籠罩的群島城市", "近未來", "潮汐會改寫城市街道與舊有記憶"),
    ("漂浮在雲海上的列車國度", "架空工業時代", "每座車站只在特定夢境中靠站"),
    ("由巨樹根系連結的地下聚落", "災後百年", "說出口的承諾會在樹皮上留下印記"),
    ("與現代都市重疊的夜間異界", "當代", "午夜後的交易必須以一段真實回憶支付"),
    ("環繞紅色矮星航行的世代船團", "遙遠未來", "每次躍遷都會遺失一份公共歷史"),
)

_CONFLICTS = (
    "主角發現維持社會秩序的核心真相同時也是災難來源",
    "一項看似救人的任務迫使主角在個人承諾與群體生存之間選擇",
    "敵對雙方都握有部分真相，而時間只允許相信其中一人",
    "被抹去的歷史重新出現，證明主角一直在替錯誤的一方工作",
    "世界的規則開始失效，唯一能修復它的人卻拒絕再次付出代價",
)

_DIRECTIONS = (
    "從一個小型異常展開，逐步揭露世界規則與角色過去，最後由主角主動改變代價機制",
    "先建立角色間的合作，再以一次背叛拆散團隊，讓眾人在終局重新決定彼此的信任",
    "以追查失蹤事件為主線，讓每個線索同時改寫讀者對主角身分的理解",
    "從日常願望切入，讓願望實現的副作用逐章擴大，最後回到最初那個看似微小的選擇",
)

_ENDINGS = (
    "主要衝突得到收束，但保留一個足以延伸下一部的真相",
    "角色付出不可逆代價換得有限勝利，世界因此進入新的平衡",
    "表面危機解除，最後一幕揭露先前線索的第二種解讀",
    "以角色完成內在選擇作結，不強迫所有世界謎題一次解完",
)

class _CharacterSeed(TypedDict):
    name: str
    age: int
    identity: str
    bio: str
    personality: str
    voice: str
    face: str
    hair: str
    eyes: str
    body: str
    features: tuple[str, ...]
    prohibited: tuple[str, ...]
    action: str
    expression: str
    motivation: str
    relationships: tuple[str, ...]
    keywords: tuple[str, ...]


_CHARACTERS: tuple[_CharacterSeed, ...] = (
    {
        "name": "沈映",
        "age": 29,
        "identity": "archivist and field investigator",
        "bio": "曾替官方修復被刪除的歷史，現在私下保存不該存在的證詞。",
        "personality": "冷靜、觀察敏銳，面對承諾時近乎固執。",
        "voice": "用詞精確、句子簡短，情緒越強烈時反而越平靜。",
        "face": "angular face, subtle freckles",
        "hair": "short black hair, side-swept bangs",
        "eyes": "amber eyes",
        "body": "lean athletic build",
        "features": ("右手腕留有環形灼痕", "總隨身攜帶一本無字索引冊"),
        "prohibited": ("不可改變琥珀色眼睛", "不可移除右手腕灼痕"),
        "action": "以索引冊比對現場殘留的記憶痕跡",
        "expression": "冷靜而戒備",
        "motivation": "找回被官方刪除的家族證詞，並證明歷史可以由普通人共同保管。",
        "relationships": (
            "一名昔日同僚握有她最需要的檔案，卻仍效忠官方。",
            "她曾救過的街頭情報販子如今要求她償還一個危險人情。",
        ),
        "keywords": (
            "adult woman",
            "archivist",
            "short black hair",
            "amber eyes",
            "subtle freckles",
            "lean athletic build",
            "layered field coat",
            "calm expression",
        ),
    },
    {
        "name": "洛岑",
        "age": 34,
        "identity": "disgraced cartographer and smuggler",
        "bio": "能畫出不存在於官方地圖的道路，卻因此被指控導致一次遠征失蹤。",
        "personality": "機智、務實、對權威不信任，會用玩笑掩飾罪惡感。",
        "voice": "語氣隨意但比喻鮮明，談到地圖時會異常專注。",
        "face": "weathered face, narrow scar over left eyebrow",
        "hair": "wavy dark brown hair",
        "eyes": "gray-green eyes",
        "body": "tall wiry build",
        "features": ("左眉上方有一道窄疤", "腰間掛著會自行更新的黃銅羅盤"),
        "prohibited": ("不可移除左眉疤痕", "不可把黃銅羅盤改成現代電子裝置"),
        "action": "沿著只有自己看得見的路徑標記逃生方向",
        "expression": "帶著試探意味的半笑",
        "motivation": "找出失蹤遠征隊真正走過的路，洗清自己蓄意誤導眾人的罪名。",
        "relationships": (
            "失蹤隊長的妹妹既雇用他帶路，也公開懷疑他就是兇手。",
            "地下運輸網的舊搭檔知道羅盤的來源，並以此勒索他完成最後一趟走私。",
        ),
        "keywords": (
            "adult man",
            "cartographer",
            "wavy dark brown hair",
            "gray-green eyes",
            "eyebrow scar",
            "tall wiry build",
            "weathered travel jacket",
            "confident half-smile",
        ),
    },
    {
        "name": "季遙",
        "age": 27,
        "identity": "signal engineer and reluctant medium",
        "bio": "維修深空通訊時聽見來自已毀殖民地的回覆，從此秘密追查訊號來源。",
        "personality": "理性、耐心，面對無法測量的事物時仍堅持留下紀錄。",
        "voice": "說話溫和，習慣先確認事實，再提出大膽推論。",
        "face": "oval face, soft features",
        "hair": "long silver-black hair, low ponytail",
        "eyes": "dark blue eyes",
        "body": "slender build",
        "features": ("左耳配戴自製訊號接收器", "指尖帶有淡藍色導電紋路"),
        "prohibited": ("不可移除左耳接收器", "不可把導電紋路改為傷疤"),
        "action": "調整接收器並記錄只有自己能聽見的回覆",
        "expression": "專注中帶著一絲不安",
        "motivation": "證明毀滅殖民地仍有人存活，同時弄清那些回覆為何只呼喚自己的名字。",
        "relationships": (
            "理性至上的研究主管想保護季遙，卻也可能隨時終止調查。",
            "訊號中的陌生人知道季遙童年的秘密，逐漸成為無法確認真假的盟友。",
        ),
        "keywords": (
            "adult nonbinary person",
            "signal engineer",
            "long silver-black hair",
            "low ponytail",
            "dark blue eyes",
            "slender build",
            "technical utility suit",
            "focused expression",
        ),
    },
)


class CreativeInspirationService:
    """Generate editable local suggestions from curated, composable banks."""

    @staticmethod
    def story(*, seed: int | str | None = None) -> CreativeInspiration:
        rng = _rng(seed)
        genre, genre_tags = rng.choice(_GENRES)
        setting, period, rule = rng.choice(_SETTINGS)
        conflict = rng.choice(_CONFLICTS)
        direction = rng.choice(_DIRECTIONS)
        ending = rng.choice(_ENDINGS)
        title_prefix = rng.choice(("回聲", "餘燼", "潮痕", "失序", "無名"))
        title_suffix = rng.choice(("城", "門", "航路", "檔案", "誓約"))
        title = f"{title_prefix}之{title_suffix}"
        return CreativeInspiration(
            kind=InspirationKind.STORY,
            title_suggestion=title,
            concept=f"在{setting}，一位普通人因意外取得被隱藏的證據，捲入會改變世界秩序的事件。",
            primary_genre=genre,
            genre_tags=genre_tags,
            tone=rng.choice(
                ("沉浸、神祕、逐步升高壓力", "角色導向、帶有黑色幽默", "冷峻但保留希望")
            ),
            setting=setting,
            time_period=period,
            world_rules=(rule,),
            central_conflict=conflict,
            themes=("選擇的代價", "記憶與身分", "制度與個人責任"),
            must_include=("一項早期線索在後段獲得新解讀", "角色必須主動做出不可逆選擇"),
            must_avoid=("無代價解決核心衝突", "只靠巧合推進結局"),
            pacing="前段以探索建立懸念，中段加速並揭露代價，終局集中處理角色選擇。",
            prose_style_notes="以具體感官細節承載世界資訊，避免一次傾倒設定；關鍵情緒留在動作與對話之間。",
            dialogue_density="中等；對話推進關係與衝突，世界規則以行動結果呈現。",
            direction=direction,
            ending_preference=ending,
        )

    @staticmethod
    def character(*, seed: int | str | None = None) -> CreativeInspiration:
        rng = _rng(seed)
        template = rng.choice(_CHARACTERS)
        return CreativeInspiration(
            kind=InspirationKind.CHARACTER,
            character_name_suggestion=template["name"],
            character_age_suggestion=template["age"],
            character_biography=template["bio"],
            character_personality=template["personality"],
            character_voice=template["voice"],
            character_identity=template["identity"],
            character_face=template["face"],
            character_hair=template["hair"],
            character_eyes=template["eyes"],
            character_body=template["body"],
            character_distinguishing_features=template["features"],
            character_prohibited_mutations=template["prohibited"],
            character_action=template["action"],
            character_expression=template["expression"],
            character_motivation=template["motivation"],
            relationship_hooks=template["relationships"],
            english_character_keywords=normalize_english_keywords(
                template["keywords"]
            ),
        )

    @staticmethod
    def world(*, seed: int | str | None = None) -> CreativeInspiration:
        rng = _rng(seed)
        setting, period, rule = rng.choice(_SETTINGS)
        locations = rng.sample(
            ("中央交換站", "禁航區", "舊城下層", "邊境觀測塔", "沉沒市場"),
            k=3,
        )
        return CreativeInspiration(
            kind=InspirationKind.WORLD,
            title_suggestion=f"{rng.choice(('潮界', '雲軌', '根城', '赤航', '夜層'))}世界設定",
            primary_genre=rng.choice(_GENRES)[0],
            tone=rng.choice(("神祕而可探索", "壯闊且帶有壓迫感", "日常表面下潛藏異常")),
            setting=setting,
            time_period=period,
            world_rules=(
                rule,
                "任何超常能力都必須付出可被故事追蹤的代價。",
                "制度、資源與地理會實際影響角色能做出的選擇。",
            ),
            locations=tuple(locations),
            social_context="社會由掌握交通與記錄權的組織維持秩序，邊緣居民發展出自己的交換網絡。",
            technology_or_magic="科技與超常現象共存，但兩者都有清楚限制，不能無代價解決衝突。",
            central_conflict="維持公共秩序的制度正逐步耗盡世界賴以生存的核心資源。",
            themes=("資源分配", "歷史詮釋權", "中心與邊緣"),
        )


def _rng(seed: int | str | None) -> random.Random:
    if seed is None:
        seed = random.SystemRandom().getrandbits(128)
    return random.Random(seed)
