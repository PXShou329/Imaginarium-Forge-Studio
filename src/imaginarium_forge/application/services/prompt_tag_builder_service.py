"""Curated Traditional-Chinese tags for building English image prompts.

This module has no Streamlit or persistence dependency.  A UI can render the
catalog, retain selections in any scratch workflow, and hand the canonical
English output to another feature only when the author chooses to do so.

Body-shape tags are deliberately adult-only: every character prompt starts
with either ``adult woman`` or ``adult man`` and youth-coded custom keywords
are rejected instead of being silently combined with adult body descriptors.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

from imaginarium_forge.domain.character.biography_draft import (
    normalize_english_image_prompt,
)
from imaginarium_forge.domain.character.gender import CharacterGender

type SelectionMode = Literal["single", "multi"]
type SelectionValue = str | Sequence[str] | None
type SelectionResult = dict[str, str | tuple[str, ...]]
type CharacterIdentityMode = Literal["standard", "beast_humanoid", "furry"]
type SpeciesTranslationHook = Callable[[str], str]

CHARACTER_IDENTITY_MODES: Final[tuple[CharacterIdentityMode, ...]] = (
    "standard",
    "beast_humanoid",
    "furry",
)


@dataclass(frozen=True, slots=True)
class TagOption:
    """One author-facing Chinese label and its canonical English fragment."""

    key: str
    label_zh: str
    prompt_en: str
    adult_only: bool = False


@dataclass(frozen=True, slots=True)
class TagCategory:
    """Rendering and randomization metadata for one group of options."""

    key: str
    label_zh: str
    group: str
    options: tuple[TagOption, ...]
    selection_mode: SelectionMode = "single"
    help_text: str = ""
    default_keys: tuple[str, ...] = ()
    random_min: int = 1
    random_max: int = 1
    applicable_gender: CharacterGender | None = None
    selection_max: int | None = None

    def __post_init__(self) -> None:
        option_keys = tuple(option.key for option in self.options)
        option_labels = tuple(option.label_zh for option in self.options)
        option_prompts = tuple(option.prompt_en.casefold() for option in self.options)
        if not self.key or not self.label_zh or not self.group:
            raise ValueError("標籤分類的 key、中文標題與群組不可留白")
        if not self.options or len(option_keys) != len(set(option_keys)):
            raise ValueError(f"標籤分類 {self.key!r} 必須有不重複的選項")
        if len(option_labels) != len(set(option_labels)):
            raise ValueError(f"標籤分類 {self.key!r} 的繁中標籤不可重複")
        if len(option_prompts) != len(set(option_prompts)):
            raise ValueError(f"標籤分類 {self.key!r} 的英文提示詞不可重複")
        if not set(self.default_keys).issubset(option_keys):
            raise ValueError(f"標籤分類 {self.key!r} 的預設選項不存在")
        if self.selection_mode == "single" and len(self.default_keys) > 1:
            raise ValueError(f"單選分類 {self.key!r} 只能有一個預設值")
        if self.random_min < 0 or self.random_max < self.random_min:
            raise ValueError(f"標籤分類 {self.key!r} 的隨機範圍無效")
        maximum = 1 if self.selection_mode == "single" else len(self.options)
        if self.random_max > maximum:
            raise ValueError(f"標籤分類 {self.key!r} 的隨機上限超過選項數")
        if self.selection_max is not None:
            if self.selection_max < 1 or self.selection_max > maximum:
                raise ValueError(f"標籤分類 {self.key!r} 的手動選擇上限無效")
            if self.random_max > self.selection_max:
                raise ValueError(f"標籤分類 {self.key!r} 的隨機上限超過手動選擇上限")
            if len(self.default_keys) > self.selection_max:
                raise ValueError(f"標籤分類 {self.key!r} 的預設值超過手動選擇上限")


def _option(
    key: str,
    label_zh: str,
    prompt_en: str,
    *,
    adult_only: bool = False,
) -> TagOption:
    return TagOption(
        key=key,
        label_zh=label_zh,
        prompt_en=prompt_en,
        adult_only=adult_only,
    )


def _adult_option(key: str, label_zh: str, prompt_en: str) -> TagOption:
    """Create an opt-in adult option without repeating the global boundary.

    Older catalog entries carried an age-and-consent phrase on every option.
    Keeping that phrase at option level made a multi-tag prompt repeat the same
    wording many times.  The character prompt builder now owns that boundary,
    while each option retains only the visual or action semantics unique to it.
    """

    normalized = prompt_en.strip()
    lowered = normalized.casefold()
    female_with_prefix = "consensual adult female with "
    female_prefix = "consensual adult female "
    adult_prefix = "consensual adult "
    if lowered.startswith(female_with_prefix):
        normalized = normalized[len(female_with_prefix) :]
        lowered = normalized.casefold()
        for article in ("a ", "an "):
            if lowered.startswith(article):
                normalized = normalized[len(article) :]
                break
    elif lowered.startswith(female_prefix):
        normalized = normalized[len(female_prefix) :]
    elif lowered.startswith(adult_prefix):
        normalized = normalized[len(adult_prefix) :]
    elif lowered.startswith("consensual "):
        normalized = normalized[len("consensual ") :]

    words = normalized.split()
    for index, word in enumerate(words[:3]):
        if word.casefold() == "adult":
            del words[index]
            break
    normalized = " ".join(words)
    consenting_suffix = " consenting adult"
    if normalized.casefold().endswith(consenting_suffix):
        normalized = normalized[: -len(consenting_suffix)].rstrip()
    if not normalized:
        raise ValueError(f"成人限定標籤 {key!r} 缺少實際提示詞語意")
    return _option(key, label_zh, normalized, adult_only=True)


def _single(
    key: str,
    label_zh: str,
    group: str,
    *options: tuple[str, str, str],
    help_text: str = "",
    default: str | None = None,
    applicable_gender: CharacterGender | None = None,
) -> TagCategory:
    return TagCategory(
        key=key,
        label_zh=label_zh,
        group=group,
        options=tuple(_option(*option) for option in options),
        help_text=help_text,
        default_keys=(default,) if default else (),
        applicable_gender=applicable_gender,
    )


def _multi(
    key: str,
    label_zh: str,
    group: str,
    *options: tuple[str, str, str],
    help_text: str = "",
    default: tuple[str, ...] = (),
    random_min: int = 0,
    random_max: int = 2,
    applicable_gender: CharacterGender | None = None,
    selection_max: int | None = None,
) -> TagCategory:
    return TagCategory(
        key=key,
        label_zh=label_zh,
        group=group,
        options=tuple(_option(*option) for option in options),
        selection_mode="multi",
        help_text=help_text,
        default_keys=default,
        random_min=random_min,
        random_max=random_max,
        applicable_gender=applicable_gender,
        selection_max=selection_max,
    )


def _adult_optional(
    key: str,
    label_zh: str,
    group: str,
    *options: tuple[str, str, str],
    help_text: str = "",
    applicable_gender: CharacterGender | None = CharacterGender.FEMALE,
    random_max: int = 1,
    selection_max: int = 1,
) -> TagCategory:
    """Create an optional adult category hidden outside 18+ mode."""

    return TagCategory(
        key=key,
        label_zh=label_zh,
        group=group,
        options=tuple(_adult_option(*option) for option in options),
        selection_mode="multi",
        help_text=help_text,
        random_min=0,
        random_max=random_max,
        applicable_gender=applicable_gender,
        selection_max=selection_max,
    )


def _optional_single_options(
    key: str,
    label_zh: str,
    group: str,
    options: Sequence[TagOption],
    *,
    help_text: str = "",
) -> TagCategory:
    """Create an optional category with exactly zero or one selected value."""

    return TagCategory(
        key=key,
        label_zh=label_zh,
        group=group,
        options=tuple(options),
        selection_mode="multi",
        help_text=help_text,
        random_min=0,
        random_max=1,
        selection_max=1,
    )


def _beast_humanoid_option(
    key: str,
    label_zh: str,
    _legacy_traits: str,
) -> TagOption:
    """Create a bipedal half-beast adult while keeping human-first proportions."""

    lineage = "cat" if key == "domestic_cat" else key.replace("_", " ")
    label = (
        "貓咪"
        if key == "domestic_cat"
        else ("犬族" if key == "dog" else label_zh.removesuffix("半獸人"))
    )
    return _option(
        key,
        label,
        _beast_lineage_prompt(lineage),
    )


def _beast_lineage_prompt(lineage: str) -> str:
    return (
        f"adult {lineage}-lineage half-beast humanoid with predominantly human facial "
        "anatomy and human body proportions and a bipedal human silhouette plus one "
        "anatomically consistent species-appropriate hearing configuration in place of "
        "human ears with no additional human ears or duplicate ears"
    )


def _custom_beast_lineage_prompt(lineage: str) -> str:
    return (
        f"adult half-beast humanoid of {lineage} lineage with predominantly human facial "
        "anatomy and human body proportions and a bipedal human silhouette plus one "
        "anatomically consistent species-appropriate hearing configuration in place of "
        "human ears with no additional human ears or duplicate ears"
    )


def _furry_species_option(key: str, label_zh: str, species: str) -> TagOption:
    """Create a sapient adult anthropomorphic species with a fixed bipedal form."""

    label = (
        "貓咪"
        if key == "domestic_cat"
        else ("犬族" if key == "dog" else label_zh.removesuffix("福瑞"))
    )
    return _option(
        key,
        label,
        _furry_lineage_prompt(species),
    )


def _furry_lineage_prompt(species: str) -> str:
    return (
        f"adult anthropomorphic sapient bipedal {species} furry with humanoid proportions "
        "and a fully anthropomorphic head plus species-appropriate full-body covering"
    )


_BASE_CHARACTER_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    _single(
        "age_impression",
        "成年年齡感",
        "身分與輪廓",
        ("young_adult", "年輕成年人", "young adult appearance"),
        ("adult", "成熟成年人", "mature adult appearance"),
        ("middle_aged", "中年", "middle-aged adult appearance"),
        ("older_adult", "年長成年人", "older adult appearance"),
        help_text="只提供成年外觀，不含兒童或青少年選項。",
        default="young_adult",
    ),
    _single(
        "fantasy_race",
        "種族／血統",
        "身分與輪廓",
        ("human", "人類", "human"),
        ("elf", "精靈", "elf"),
        ("dark_elf", "黑暗精靈", "dark elf"),
        ("half_elf", "半精靈", "half-elf"),
        ("dwarf", "矮人", "dwarf"),
        ("orc", "歐克族", "orc"),
        ("goblin", "哥布林", "goblin"),
        ("tiefling", "魔裔", "tiefling"),
        ("demon", "惡魔族", "demon"),
        ("angel", "天使族", "angelic being"),
        ("vampire", "吸血鬼", "vampire"),
        ("werewolf", "狼人", "werewolf"),
        ("merfolk", "人魚族", "merfolk"),
        ("fairy", "妖精", "fairy folk"),
        ("dragonkin", "龍裔", "dragonkin"),
        ("catfolk", "貓族獸人", "catfolk"),
        ("foxfolk", "狐族獸人", "foxfolk"),
        ("wolffolk", "狼族獸人", "wolffolk"),
        ("rabbitfolk", "兔族獸人", "rabbitfolk"),
        ("automaton", "魔導人偶", "arcane automaton"),
        ("android", "仿生人", "android"),
        ("undead", "不死族", "sentient undead"),
        ("elemental", "元素精靈", "elemental humanoid"),
        default="human",
    ),
    _single(
        "height",
        "身高",
        "身分與輪廓",
        ("very_short", "非常嬌小", "very short stature"),
        ("short", "嬌小", "short stature"),
        ("average", "平均身高", "average height"),
        ("tall", "高挑", "tall stature"),
        ("very_tall", "非常高挑", "very tall stature"),
        default="average",
    ),
    _single(
        "build",
        "整體身材",
        "身分與輪廓",
        ("petite", "纖小", "petite adult build"),
        ("slender", "纖細", "slender build"),
        ("lean", "精瘦", "lean build"),
        ("toned", "勻稱結實", "toned build"),
        ("athletic", "運動型", "athletic build"),
        ("curvy", "曲線型", "curvy adult figure"),
        ("voluptuous", "豐滿型", "voluptuous adult figure"),
        ("stocky", "厚實型", "stocky build"),
        ("muscular", "肌肉型", "muscular build"),
        ("heavyset", "大尺碼", "heavyset adult build"),
        default="slender",
    ),
    _single(
        "body_proportions",
        "身體比例",
        "身分與輪廓",
        ("balanced", "均衡比例", "balanced body proportions"),
        ("long_legged", "修長腿型", "long-legged proportions"),
        ("compact", "緊湊比例", "compact body proportions"),
        ("hourglass", "沙漏型", "hourglass proportions"),
        ("pear", "梨形", "pear-shaped proportions"),
        ("inverted_triangle", "倒三角型", "inverted-triangle proportions"),
        ("rectangular", "直筒型", "rectangular proportions"),
        default="balanced",
    ),
    _single(
        "female_bust",
        "胸部大小",
        "身材細節",
        ("extremely_large", "超巨乳", "extremely large breasts"),
        ("very_large", "巨乳", "very large breasts"),
        ("large", "大奶／豐滿", "large breasts"),
        ("medium", "平均大小", "medium breasts"),
        ("modest", "偏小胸型", "modest bust"),
        ("small", "貧乳", "small breasts"),
        help_text="只在性別選擇「女」時出現，角色一律為成年人。",
        default="medium",
        applicable_gender=CharacterGender.FEMALE,
    ),
    _single(
        "male_chest",
        "胸膛／胸肌",
        "身材細節",
        ("flat", "平坦胸膛", "flat chest"),
        ("lean", "精瘦胸膛", "lean chest"),
        ("defined", "結實胸肌", "defined pectorals"),
        ("broad", "寬闊胸膛", "broad chest"),
        ("muscular", "厚實胸肌", "broad muscular chest"),
        help_text="只在性別選擇「男」時出現。",
        default="defined",
        applicable_gender=CharacterGender.MALE,
    ),
    _single(
        "waist",
        "腰圍線條",
        "身材細節",
        ("straight", "直腰", "straight waistline"),
        ("soft", "柔和腰線", "soft waistline"),
        ("defined", "明顯腰線", "defined waistline"),
        ("narrow", "纖腰", "narrow waist"),
        ("very_narrow", "極纖腰", "very narrow waist"),
        ("thick", "厚實腰身", "thick waist"),
        default="defined",
    ),
    _single(
        "hips",
        "臀胯比例",
        "身材細節",
        ("narrow", "窄臀", "narrow hips"),
        ("balanced", "均衡臀胯", "balanced hips"),
        ("wide", "寬臀", "wide hips"),
        ("full", "豐滿臀型", "full hips"),
        default="balanced",
    ),
    _single(
        "shoulders",
        "肩膀",
        "身材細節",
        ("narrow", "窄肩", "narrow shoulders"),
        ("sloping", "柔和斜肩", "gently sloping shoulders"),
        ("balanced", "均衡肩寬", "balanced shoulders"),
        ("broad", "寬肩", "broad shoulders"),
        default="balanced",
    ),
    _single(
        "legs",
        "腿部線條",
        "身材細節",
        ("slender", "纖細雙腿", "slender legs"),
        ("long", "修長雙腿", "long legs"),
        ("toned", "勻稱腿型", "toned legs"),
        ("thick", "肉感腿型", "thick legs"),
        ("powerful", "強健腿型", "powerful legs"),
        default="toned",
    ),
    _single(
        "skin_tone",
        "膚色",
        "身材細節",
        ("porcelain", "瓷白", "porcelain skin"),
        ("fair", "白皙", "fair skin"),
        ("light", "淺膚色", "light skin"),
        ("warm_beige", "暖米色", "warm beige skin"),
        ("tan", "小麥色", "tan skin"),
        ("olive", "橄欖色", "olive skin"),
        ("brown", "棕色", "brown skin"),
        ("deep_brown", "深棕色", "deep brown skin"),
        ("ebony", "烏木色", "ebony skin"),
        ("pale_blue", "淡藍奇幻膚色", "pale blue skin"),
        ("lavender", "薰衣草紫膚色", "lavender skin"),
        ("emerald", "翡翠綠膚色", "emerald green skin"),
        default="fair",
    ),
    _single(
        "face_shape",
        "臉型",
        "臉部與眼睛",
        ("oval", "鵝蛋臉", "oval face"),
        ("round", "圓臉", "round face"),
        ("heart", "心形臉", "heart-shaped face"),
        ("square", "方臉", "square face"),
        ("angular", "稜角臉", "angular face"),
        ("diamond", "菱形臉", "diamond-shaped face"),
        ("long", "長臉", "long face"),
        default="oval",
    ),
    _single(
        "eye_shape",
        "眼型",
        "臉部與眼睛",
        ("round", "圓眼", "round eyes"),
        ("almond", "杏眼", "almond-shaped eyes"),
        ("upturned", "上挑眼", "upturned eyes"),
        ("downturned", "下垂眼", "downturned eyes"),
        ("hooded", "內雙眼", "hooded eyes"),
        ("narrow", "細長眼", "narrow eyes"),
        default="almond",
    ),
    _multi(
        "eye_color",
        "眼睛顏色",
        "臉部與眼睛",
        ("brown", "棕色", "brown eyes"),
        ("amber", "琥珀色", "amber eyes"),
        ("hazel", "榛果色", "hazel eyes"),
        ("green", "綠色", "green eyes"),
        ("blue", "藍色", "blue eyes"),
        ("gray", "灰色", "gray eyes"),
        ("violet", "紫羅蘭色", "violet eyes"),
        ("red", "緋紅色", "crimson eyes"),
        ("gold", "金色", "golden eyes"),
        ("silver", "銀色", "silver eyes"),
        help_text="可選一至兩種虹膜顏色；多色會自動合成自然英文片語。",
        default=("brown",),
        random_min=1,
        random_max=2,
        selection_max=2,
    ),
    _single(
        "hair_length",
        "頭髮長度",
        "髮型與髮色",
        ("shaved", "剃髮", "shaved hair"),
        ("pixie", "精靈短髮", "pixie-cut hair"),
        ("chin", "下巴長度", "chin-length hair"),
        ("shoulder", "及肩", "shoulder-length hair"),
        ("mid_back", "背中長度", "mid-back-length hair"),
        ("waist", "及腰", "waist-length hair"),
        ("floor", "曳地超長髮", "floor-length hair"),
        default="shoulder",
    ),
    _single(
        "hair_style",
        "髮型",
        "髮型與髮色",
        ("loose_straight", "自然直髮", "loose straight hair"),
        ("loose_wavy", "自然波浪髮", "loose wavy hair"),
        ("curly", "捲髮", "curly hair"),
        ("bob", "鮑伯頭", "bob haircut"),
        ("ponytail", "馬尾", "ponytail"),
        ("high_ponytail", "高馬尾", "high ponytail"),
        ("twin_tails", "雙馬尾", "twin tails"),
        ("side_ponytail", "側馬尾", "side ponytail"),
        ("single_braid", "單辮", "single braid"),
        ("double_braids", "雙辮", "double braids"),
        ("crown_braid", "冠狀編髮", "crown braid"),
        ("bun", "髮髻", "hair bun"),
        ("double_buns", "雙丸子頭", "double hair buns"),
        ("buzz_cut", "平頭／極短髮", "buzz cut"),
        ("undercut", "側削髮", "undercut hairstyle"),
        ("mohawk", "莫霍克頭", "mohawk hairstyle"),
        ("messy", "凌亂髮", "messy hair"),
        default="loose_straight",
    ),
    _single(
        "bangs",
        "瀏海",
        "髮型與髮色",
        ("none", "無瀏海", "exposed forehead"),
        ("straight", "齊瀏海", "straight bangs"),
        ("side_swept", "側分瀏海", "side-swept bangs"),
        ("curtain", "窗簾瀏海", "curtain bangs"),
        ("wispy", "空氣瀏海", "wispy bangs"),
        ("hime", "姬髮式側鬢", "hime-cut sidelocks"),
        default="side_swept",
    ),
    _multi(
        "hair_color",
        "髮色",
        "髮型與髮色",
        ("black", "黑色", "black hair"),
        ("dark_brown", "深棕色", "dark brown hair"),
        ("brown", "棕色", "brown hair"),
        ("blonde", "金色", "blonde hair"),
        ("platinum", "白金色", "platinum blonde hair"),
        ("silver", "銀色", "silver hair"),
        ("white", "純白色", "white hair"),
        ("red", "紅色", "red hair"),
        ("auburn", "赤褐色", "auburn hair"),
        ("pink", "粉紅色", "pink hair"),
        ("blue", "藍色", "blue hair"),
        ("teal", "藍綠色", "teal hair"),
        ("green", "綠色", "green hair"),
        ("purple", "紫色", "purple hair"),
        help_text="可選一至三種髮色；多色會自動合成自然英文片語。",
        default=("black",),
        random_min=1,
        random_max=3,
        selection_max=3,
    ),
    _single(
        "hair_texture",
        "髮質效果",
        "髮型與髮色",
        ("silky", "絲滑", "silky hair texture"),
        ("fluffy", "蓬鬆", "fluffy hair texture"),
        ("tousled", "微亂", "tousled hair texture"),
        ("coarse", "粗獷", "coarse hair texture"),
        ("wet", "濕髮", "wet hair"),
        ("glowing", "魔法微光", "faintly glowing hair"),
        default="silky",
    ),
    _single(
        "expression",
        "面部表情／情緒",
        "個性與奇幻特徵",
        ("gentle", "溫柔", "gentle expression"),
        ("cheerful", "開朗", "cheerful expression"),
        ("shy", "害羞", "shy expression"),
        ("lonely", "孤單", "lonely expression"),
        ("melancholic", "憂鬱", "melancholic expression"),
        ("stoic", "冷靜克制", "stoic expression"),
        ("fierce", "凌厲", "fierce expression"),
        ("confident", "自信", "confident expression"),
        ("mischievous", "淘氣", "mischievous expression"),
        ("weary", "疲憊", "weary expression"),
        ("surprised", "驚訝", "surprised expression"),
        default="gentle",
    ),
    _multi(
        "fantasy_traits",
        "奇幻身體特徵",
        "個性與奇幻特徵",
        ("pointed_ears", "尖耳", "pointed ears"),
        ("fangs", "尖牙", "visible fangs"),
        ("horns", "角", "ornate horns"),
        ("halo", "光環", "luminous halo"),
        ("feathered_wings", "羽翼", "feathered wings"),
        ("bat_wings", "蝠翼", "batlike wings"),
        ("fairy_wings", "妖精薄翼", "translucent insectlike wings"),
        ("dragon_wings", "龍翼", "dragon wings"),
        ("tail", "尾巴", "expressive tail"),
        ("scales", "鱗片", "iridescent scales"),
        ("fur", "局部獸毛", "soft patches of fur"),
        ("crystal_growths", "晶體生長", "crystalline growths"),
        ("glowing_runes", "發光符文", "glowing runes on skin"),
        ("mechanical_limbs", "機械義肢", "intricate mechanical limbs"),
        ("ethereal_aura", "靈氣", "ethereal aura"),
        random_max=3,
    ),
    _multi(
        "distinctive_marks",
        "辨識特徵",
        "個性與奇幻特徵",
        ("freckles", "雀斑", "soft freckles"),
        ("beauty_mark", "美人痣", "beauty mark"),
        ("facial_scar", "臉部疤痕", "facial scar"),
        ("body_scar", "身體疤痕", "visible old scars"),
        ("tattoo", "刺青", "ornate tattoo"),
        ("war_paint", "戰紋", "ritual war paint"),
        ("golden_cracks", "金繼裂紋", "golden kintsugi-like cracks"),
        ("glasses", "眼鏡", "elegant glasses"),
        ("eyepatch", "眼罩", "decorative eyepatch"),
        random_max=2,
    ),
    _single(
        "outfit_archetype",
        "服裝主題",
        "服裝與配件",
        ("casual", "日常休閒", "layered casual outfit"),
        ("streetwear", "潮流街頭", "modern streetwear"),
        ("formal", "正式禮服", "elegant formal attire"),
        ("business", "俐落商務", "tailored business attire"),
        ("traveler", "奇幻旅人", "weathered fantasy traveler outfit"),
        ("mage", "法師", "ornate fantasy mage robes"),
        ("knight", "騎士", "fitted fantasy knight armor"),
        ("rogue", "盜賊／遊俠", "practical fantasy rogue outfit"),
        ("ranger", "荒野遊俠", "layered fantasy ranger outfit"),
        ("priest", "祭司", "ceremonial priestly robes"),
        ("royal", "王族", "regal court attire"),
        ("alchemist", "鍊金術師", "detailed alchemist outfit"),
        ("steampunk", "蒸汽龐克", "steampunk adventurer outfit"),
        ("cyberpunk", "賽博龐克", "cyberpunk tactical fashion"),
        ("space_suit", "太空裝", "sleek science-fiction spacesuit"),
        ("kimono", "和風服飾", "layered traditional Japanese attire"),
        ("hanfu", "漢服風", "flowing historical Chinese attire"),
        default="traveler",
    ),
    _multi(
        "outfit_materials",
        "服裝材質",
        "服裝與配件",
        ("linen", "亞麻", "textured linen fabric"),
        ("silk", "絲綢", "lustrous silk fabric"),
        ("velvet", "天鵝絨", "rich velvet fabric"),
        ("leather", "皮革", "weathered leather details"),
        ("metal", "金屬", "polished metal accents"),
        ("lace", "蕾絲", "delicate lace details"),
        ("fur_trim", "毛皮滾邊", "soft fur trim"),
        ("translucent", "半透明薄紗", "layered translucent fabric"),
        ("holographic", "全息材質", "holographic fabric accents"),
        random_min=1,
        random_max=2,
    ),
    _single(
        "outfit_palette",
        "服裝配色",
        "服裝與配件",
        ("monochrome", "黑白單色", "monochrome clothing palette"),
        ("pastel", "柔和粉彩", "soft pastel clothing palette"),
        ("earth", "大地色", "earth-tone clothing palette"),
        ("jewel", "寶石色", "rich jewel-tone clothing palette"),
        ("warm", "暖色", "warm clothing palette"),
        ("cool", "冷色", "cool clothing palette"),
        ("black_red", "黑紅", "black and crimson clothing palette"),
        ("white_gold", "白金", "white and gold clothing palette"),
        ("blue_silver", "藍銀", "blue and silver clothing palette"),
        default="earth",
    ),
    _multi(
        "accessories",
        "配件／持有物",
        "服裝與配件",
        ("earrings", "耳環", "distinctive earrings"),
        ("necklace", "項鍊", "ornate necklace"),
        ("choker", "頸飾", "decorative choker"),
        ("gloves", "手套", "fitted gloves"),
        ("cape", "披風", "flowing cape"),
        ("hood", "兜帽", "layered hood"),
        ("crown", "王冠", "delicate crown"),
        ("wide_hat", "寬帽", "wide-brimmed hat"),
        ("sword", "長劍", "ornate sword"),
        ("staff", "法杖", "arcane staff"),
        ("book", "魔導書", "ancient spellbook"),
        ("lantern", "提燈", "glowing lantern"),
        ("satchel", "側背包", "weathered satchel"),
        random_max=3,
    ),
    _single(
        "pose",
        "姿勢／動作",
        "畫面與風格",
        ("standing", "自然站姿", "natural standing pose"),
        ("walking", "行走中", "walking pose"),
        ("running", "奔跑中", "dynamic running pose"),
        ("seated", "坐姿", "relaxed seated pose"),
        ("looking_back", "回眸", "looking back over shoulder"),
        ("combat", "戰鬥架勢", "dynamic combat stance"),
        ("casting", "施法", "casting magic"),
        ("weapon_ready", "持武戒備", "weapon-ready stance"),
        ("floating", "漂浮", "weightless floating pose"),
        default="standing",
    ),
    _single(
        "framing",
        "取景範圍",
        "畫面與風格",
        ("face", "臉部特寫", "facial close-up"),
        ("bust", "胸像", "bust portrait"),
        ("half_body", "半身", "half-body portrait"),
        ("three_quarter", "四分之三身", "three-quarter body portrait"),
        ("full_body", "全身", "full-body character portrait"),
        ("character_sheet", "角色設定圖", "character design sheet"),
        default="full_body",
    ),
    _single(
        "viewpoint",
        "鏡頭視角",
        "畫面與風格",
        ("eye_level", "平視", "eye-level view"),
        ("low_angle", "低角度仰視", "low-angle view"),
        ("high_angle", "高角度俯視", "high-angle view"),
        ("profile", "側面", "profile view"),
        ("three_quarter", "四分之三側面", "three-quarter view"),
        ("back_view", "背面", "back view"),
        default="three_quarter",
    ),
    _single(
        "character_lighting",
        "角色打光",
        "畫面與風格",
        ("soft_daylight", "柔和日光", "soft natural daylight"),
        ("golden_hour", "金色夕照", "golden-hour lighting"),
        ("moonlight", "月光", "cool moonlight"),
        ("rim_light", "輪廓光", "dramatic rim lighting"),
        ("candlelight", "燭光", "warm candlelight"),
        ("neon", "霓虹光", "colorful neon lighting"),
        ("volumetric", "體積光", "volumetric lighting"),
        ("studio", "棚拍光", "clean studio lighting"),
        default="soft_daylight",
    ),
    _single(
        "character_style",
        "美術風格",
        "畫面與風格",
        ("anime", "動漫插畫", "polished anime illustration"),
        ("semi_realistic", "半寫實", "semi-realistic character illustration"),
        ("realistic", "寫實概念圖", "realistic character concept art"),
        ("painterly", "奇幻厚塗", "painterly fantasy illustration"),
        ("cel_shaded", "賽璐璐上色", "clean cel-shaded illustration"),
        ("watercolor", "水彩", "delicate watercolor illustration"),
        ("comic", "漫畫風", "graphic comic-book illustration"),
        ("cinematic", "電影感", "cinematic character key art"),
        ("pixel_art", "像素藝術", "detailed pixel art"),
        default="anime",
    ),
    _single(
        "character_backdrop",
        "角色圖背景",
        "畫面與風格",
        (
            "transparent",
            "透明／去背感",
            "isolated character on a transparent background",
        ),
        ("simple", "簡潔背景", "simple unobtrusive backdrop"),
        ("gradient", "漸層背景", "soft gradient backdrop"),
        ("environmental", "情境背景", "story-rich environmental backdrop"),
        ("ornamental", "裝飾框景", "ornamental fantasy backdrop"),
        default="simple",
    ),
    _single(
        "character_detail",
        "細節密度",
        "畫面與風格",
        ("clean", "乾淨簡潔", "clean readable design"),
        ("detailed", "高細節", "highly detailed character design"),
        ("intricate", "極繁複", "intricate character design"),
        default="detailed",
    ),
)


_BASE_BACKGROUND_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    _single(
        "world_genre",
        "世界類型",
        "世界與地點",
        ("high_fantasy", "高奇幻", "high-fantasy world"),
        ("dark_fantasy", "黑暗奇幻", "dark-fantasy world"),
        ("urban_fantasy", "都市奇幻", "urban-fantasy world"),
        ("fairy_tale", "童話奇境", "storybook fantasy world"),
        ("mythic", "神話世界", "mythic world"),
        ("historical", "歷史世界", "historical world"),
        ("gothic", "哥德世界", "gothic world"),
        ("steampunk", "蒸汽龐克", "steampunk world"),
        ("dieselpunk", "柴油龐克", "dieselpunk world"),
        ("cyberpunk", "賽博龐克", "cyberpunk world"),
        ("solarpunk", "太陽能龐克", "solarpunk world"),
        ("space_opera", "太空歌劇", "space-opera world"),
        ("post_apocalyptic", "末日後", "post-apocalyptic world"),
        ("surreal", "超現實", "surreal dream world"),
        default="high_fantasy",
    ),
    _single(
        "location",
        "主要地點",
        "世界與地點",
        ("castle", "城堡外觀", "ancient castle exterior"),
        ("throne_room", "王座大廳", "grand throne room"),
        ("tavern", "旅店酒館", "cozy fantasy tavern"),
        ("library", "古老圖書館", "vast ancient library"),
        ("temple", "神殿", "sacred temple"),
        ("market", "奇幻市集", "bustling fantasy market"),
        ("village", "村落", "lived-in rural village"),
        ("city_street", "城市街道", "detailed city street"),
        ("alley", "狹窄巷弄", "narrow atmospheric alley"),
        ("forest", "古老森林", "ancient enchanted forest"),
        ("ruins", "失落遺跡", "overgrown lost ruins"),
        ("cave", "洞窟", "vast crystal cave"),
        ("coast", "海岸", "windswept rocky coast"),
        ("desert", "沙漠", "vast desert landscape"),
        ("mountain", "高山", "towering mountain pass"),
        ("swamp", "沼澤", "misty ancient swamp"),
        ("floating_island", "浮空島", "floating island sanctuary"),
        ("underwater_city", "海底城市", "submerged underwater city"),
        ("train_station", "車站", "grand old train station"),
        ("laboratory", "實驗室", "advanced research laboratory"),
        ("spaceship", "太空船", "vast spacecraft interior"),
        ("alien_planet", "異星地表", "alien planetary landscape"),
        default="forest",
    ),
    _single(
        "spatial_scale",
        "空間尺度",
        "世界與地點",
        ("intimate", "私密小空間", "intimate small-scale space"),
        ("room", "室內空間", "room-scale environment"),
        ("street", "街區尺度", "street-scale environment"),
        ("district", "城區尺度", "district-scale environment"),
        ("city", "城市全景", "city-scale panorama"),
        ("epic", "史詩級廣景", "epic monumental scale"),
        default="epic",
    ),
    _single(
        "architecture",
        "建築風格",
        "建築與材質",
        ("medieval", "中世紀", "medieval architecture"),
        ("gothic", "哥德式", "Gothic architecture"),
        ("baroque", "巴洛克", "Baroque architecture"),
        ("art_nouveau", "新藝術", "Art Nouveau architecture"),
        ("east_asian", "東亞傳統", "traditional East Asian architecture"),
        ("islamic", "伊斯蘭式", "ornate Islamic architecture"),
        ("classical", "古典希臘羅馬", "classical Greco-Roman architecture"),
        ("industrial", "工業風", "industrial architecture"),
        ("brutalist", "粗獷主義", "Brutalist architecture"),
        ("futuristic", "未來主義", "futuristic architecture"),
        ("organic", "有機建築", "organic biomorphic architecture"),
        ("alien", "異星建築", "alien nonhuman architecture"),
        ("none", "無建築／自然景觀", "natural landscape without architecture"),
        default="medieval",
    ),
    _multi(
        "environment_materials",
        "主要材質",
        "建築與材質",
        ("stone", "石材", "weathered stone surfaces"),
        ("wood", "木材", "aged timber construction"),
        ("brick", "磚牆", "textured brickwork"),
        ("concrete", "混凝土", "textured concrete surfaces"),
        ("marble", "大理石", "polished marble surfaces"),
        ("glass", "玻璃", "expansive glass structures"),
        ("metal", "金屬", "layered metal structures"),
        ("crystal", "水晶", "luminous crystal structures"),
        ("bone", "骨質", "monumental bone structures"),
        ("living_plants", "活體植物", "living botanical architecture"),
        ("overgrown", "藤蔓覆蓋", "dense overgrown vegetation"),
        random_min=1,
        random_max=3,
    ),
    _single(
        "terrain",
        "地形",
        "自然與天候",
        ("flat", "平原", "open plains"),
        ("rolling_hills", "丘陵", "rolling hills"),
        ("jagged_mountains", "峻峭山脈", "jagged mountains"),
        ("cliffs", "懸崖", "towering cliffs"),
        ("river", "河谷", "winding river valley"),
        ("lake", "湖畔", "still lakeshore"),
        ("wetlands", "濕地", "layered wetlands"),
        ("dunes", "沙丘", "sweeping sand dunes"),
        ("volcanic", "火山地帶", "volcanic terrain"),
        ("glacial", "冰川地帶", "glacial terrain"),
        ("floating", "漂浮地形", "levitating landmasses"),
        ("interior", "室內／不顯示地形", "enclosed interior environment"),
        ("seafloor", "海床", "submerged seafloor terrain"),
        default="rolling_hills",
    ),
    _single(
        "season",
        "季節",
        "自然與天候",
        ("spring", "春季", "spring season"),
        ("summer", "夏季", "summer season"),
        ("autumn", "秋季", "autumn season"),
        ("winter", "冬季", "winter season"),
        ("timeless", "超越季節", "timeless otherworldly season"),
        default="autumn",
    ),
    _single(
        "time_of_day",
        "時間",
        "自然與天候",
        ("dawn", "黎明", "early dawn"),
        ("morning", "清晨", "morning light"),
        ("noon", "正午", "bright midday"),
        ("afternoon", "午後", "late afternoon"),
        ("golden_hour", "黃金時刻", "golden hour"),
        ("dusk", "黃昏", "deep dusk"),
        ("night", "夜晚", "deep night"),
        ("midnight", "午夜", "midnight setting"),
        default="golden_hour",
    ),
    _multi(
        "weather",
        "天候",
        "自然與天候",
        ("clear", "晴朗", "clear weather"),
        ("cloudy", "多雲", "layered clouds"),
        ("fog", "濃霧", "rolling fog"),
        ("drizzle", "細雨", "fine drizzle"),
        ("rain", "大雨", "heavy rainfall"),
        ("storm", "暴風雨", "violent storm"),
        ("snow", "飄雪", "drifting snow"),
        ("blizzard", "暴風雪", "blizzard conditions"),
        ("wind", "強風", "strong wind"),
        ("dust", "沙塵", "airborne dust"),
        ("aurora", "極光", "vivid aurora overhead"),
        ("magical_rain", "魔力光雨", "falling magical light"),
        ("indoor", "室內環境", "sheltered indoor conditions"),
        ("underwater", "水下環境", "submerged underwater conditions"),
        random_min=1,
        random_max=2,
    ),
    _single(
        "atmosphere",
        "氛圍",
        "氣氛與光色",
        ("peaceful", "寧靜", "peaceful atmosphere"),
        ("cozy", "溫馨", "cozy atmosphere"),
        ("romantic", "浪漫", "romantic atmosphere"),
        ("lonely", "孤寂", "quiet lonely atmosphere"),
        ("melancholic", "憂鬱", "melancholic atmosphere"),
        ("mysterious", "神祕", "mysterious atmosphere"),
        ("ominous", "不祥", "ominous atmosphere"),
        ("sacred", "神聖", "sacred atmosphere"),
        ("dreamlike", "夢幻", "dreamlike atmosphere"),
        ("chaotic", "混亂", "chaotic atmosphere"),
        ("desolate", "荒涼", "desolate atmosphere"),
        default="mysterious",
    ),
    _single(
        "background_lighting",
        "環境光線",
        "氣氛與光色",
        ("soft_diffuse", "柔和漫射光", "soft diffuse lighting"),
        ("sunbeams", "穿透光束", "sunbeams through the atmosphere"),
        ("backlit", "逆光", "dramatic backlighting"),
        ("moonlit", "月光", "cool moonlit illumination"),
        ("candlelit", "燭火", "warm candlelit illumination"),
        ("firelit", "火光", "flickering firelight"),
        ("neon", "霓虹", "layered neon illumination"),
        ("bioluminescent", "生物發光", "bioluminescent illumination"),
        ("volumetric", "體積光", "dramatic volumetric lighting"),
        ("high_contrast", "高反差", "high-contrast cinematic lighting"),
        default="volumetric",
    ),
    _single(
        "color_palette",
        "色調／色盤",
        "氣氛與光色",
        ("warm", "暖色調", "warm color palette"),
        ("cool", "冷色調", "cool color palette"),
        ("pastel", "粉彩色調", "soft pastel color palette"),
        ("earth", "大地色調", "earth-tone color palette"),
        ("jewel", "寶石色調", "rich jewel-tone palette"),
        ("monochrome", "單色調", "restrained monochrome palette"),
        ("teal_orange", "青橙電影色", "teal-and-orange cinematic palette"),
        ("blue_gold", "藍金色調", "blue-and-gold color palette"),
        ("violet_cyan", "紫青霓虹色", "violet-and-cyan neon palette"),
        ("desaturated", "低飽和", "desaturated color palette"),
        default="jewel",
    ),
    _single(
        "composition",
        "畫面構圖",
        "構圖與鏡頭",
        ("symmetrical", "對稱構圖", "symmetrical composition"),
        ("rule_of_thirds", "三分構圖", "rule-of-thirds composition"),
        ("leading_lines", "引導線構圖", "strong leading lines"),
        ("central_vista", "中央景深", "central vanishing-point composition"),
        ("foreground_frame", "前景框景", "foreground framing elements"),
        ("layered_depth", "多層景深", "layered deep-space composition"),
        ("diagonal", "對角動勢", "dynamic diagonal composition"),
        ("panoramic", "全景構圖", "sweeping panoramic composition"),
        default="layered_depth",
    ),
    _single(
        "camera",
        "鏡頭語言",
        "構圖與鏡頭",
        ("wide_establishing", "廣角建立鏡頭", "wide establishing shot"),
        ("ultra_wide", "超廣角", "ultra-wide-angle view"),
        ("eye_level", "平視", "eye-level camera"),
        ("low_angle", "低角度", "low-angle camera"),
        ("high_angle", "高角度", "high-angle camera"),
        ("aerial", "空拍俯視", "aerial view"),
        ("isometric", "等角視圖", "isometric view"),
        ("telephoto", "望遠壓縮", "telephoto compression"),
        ("fisheye", "魚眼", "subtle fisheye perspective"),
        default="wide_establishing",
    ),
    _multi(
        "story_details",
        "敘事細節",
        "場景故事",
        ("personal_objects", "遺留私人物品", "abandoned personal belongings"),
        ("fresh_tracks", "新鮮足跡", "fresh tracks across the ground"),
        ("open_book", "翻開的書", "an open book left behind"),
        ("broken_weapon", "斷裂武器", "a broken weapon in the foreground"),
        ("festival", "節慶裝飾", "weathered festival decorations"),
        ("market_goods", "攤販貨物", "carefully arranged market goods"),
        ("laundry", "生活晾曬", "laundry moving in the wind"),
        ("warning_signs", "警示標記", "cryptic warning symbols"),
        ("ritual", "儀式痕跡", "remnants of an old ritual"),
        ("maps", "散落地圖", "scattered maps and notes"),
        random_min=1,
        random_max=3,
    ),
    _multi(
        "history_and_decay",
        "歷史痕跡",
        "場景故事",
        ("pristine", "保存完好", "pristine well-maintained surfaces"),
        ("weathered", "風化", "weathered surfaces"),
        ("cracked", "龜裂", "cracked masonry"),
        ("overgrown", "植被侵蝕", "vegetation reclaiming the structures"),
        ("flooded", "積水淹沒", "partially flooded ground"),
        ("burned", "火災痕跡", "old fire damage"),
        ("battle_damage", "戰損", "visible battle damage"),
        ("repaired", "反覆修補", "layers of improvised repairs"),
        random_min=1,
        random_max=2,
    ),
    _multi(
        "supernatural_details",
        "超自然現象",
        "場景故事",
        ("floating_debris", "漂浮碎片", "slowly floating debris"),
        ("glowing_runes", "發光符文", "glowing runes across the environment"),
        ("spirit_lights", "靈火", "wandering spirit lights"),
        ("portal", "傳送門", "unstable magical portal"),
        ("time_distortion", "時間扭曲", "visible time distortion"),
        ("giant_roots", "巨型神木根", "colossal ancient roots"),
        ("crystal_bloom", "晶簇綻放", "blooming crystal formations"),
        ("levitating_water", "漂浮水流", "levitating streams of water"),
        random_max=2,
    ),
    _single(
        "population",
        "人物密度",
        "場景故事",
        ("empty", "無人空景", "no people"),
        ("one_silhouette", "遠方單一剪影", "one distant human silhouette"),
        ("few_silhouettes", "少量遠景剪影", "a few distant silhouettes"),
        ("lived_in", "有人生活但不入鏡", "signs of inhabitants without visible people"),
        ("busy", "熱鬧人群", "busy distant crowd"),
        default="empty",
    ),
    _single(
        "background_style",
        "美術風格",
        "風格與完成度",
        ("anime", "動漫背景美術", "polished anime background art"),
        ("semi_realistic", "半寫實", "semi-realistic environmental illustration"),
        ("realistic", "寫實概念圖", "realistic environment concept art"),
        ("painterly", "奇幻厚塗", "painterly fantasy environment art"),
        ("watercolor", "水彩", "atmospheric watercolor landscape"),
        ("ink", "水墨", "expressive ink-wash landscape"),
        ("comic", "漫畫背景", "graphic comic-book environment art"),
        ("pixel_art", "像素藝術", "detailed pixel-art environment"),
        ("cinematic", "電影場景", "cinematic production environment art"),
        default="cinematic",
    ),
    _single(
        "rendering",
        "材質呈現",
        "風格與完成度",
        ("matte_painting", "數位場景繪景", "digital matte painting"),
        ("concept_art", "概念設計", "professional environment concept design"),
        ("illustration", "精緻插畫", "polished environmental illustration"),
        ("photoreal", "擬真渲染", "photorealistic rendering"),
        ("stylized_3d", "風格化 3D", "stylized three-dimensional rendering"),
        ("line_and_color", "線稿上色", "clean line-and-color rendering"),
        ("pixel_rendering", "像素渲染", "crisp pixel rendering"),
        default="concept_art",
    ),
    _single(
        "background_detail",
        "細節密度",
        "風格與完成度",
        ("clean", "乾淨易讀", "clean readable environment design"),
        ("detailed", "高細節", "highly detailed environment"),
        ("intricate", "極繁複", "intricate environmental detail"),
        default="detailed",
    ),
)


_BASE_NEGATIVE_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    _multi(
        "negative_quality",
        "畫質問題",
        "負面提示詞",
        ("low_quality", "低畫質", "low quality"),
        ("low_resolution", "低解析度", "low resolution"),
        ("blurry", "模糊", "blurry"),
        ("noise", "雜訊", "excessive noise"),
        ("compression", "壓縮破圖", "compression artifacts"),
        ("oversharpened", "過度銳化", "oversharpened"),
        default=("low_quality", "low_resolution", "blurry"),
        random_min=2,
        random_max=4,
    ),
    _multi(
        "negative_anatomy",
        "人物結構問題",
        "負面提示詞",
        ("bad_anatomy", "錯誤人體", "bad anatomy"),
        ("bad_hands", "錯誤手部", "bad hands"),
        ("extra_digits", "多餘手指", "extra digits"),
        ("missing_digits", "缺少手指", "missing digits"),
        ("extra_limbs", "多餘肢體", "extra limbs"),
        ("missing_limbs", "缺少肢體", "missing limbs"),
        ("deformed_face", "臉部變形", "deformed face"),
        ("cross_eyed", "鬥雞眼", "cross-eyed"),
        default=("bad_anatomy", "bad_hands", "extra_digits", "extra_limbs"),
        random_min=3,
        random_max=5,
    ),
    _multi(
        "negative_composition",
        "構圖問題",
        "負面提示詞",
        ("cropped", "意外裁切", "awkward cropping"),
        ("cut_off", "肢體截斷", "cut-off limbs"),
        ("duplicate", "重複主體", "duplicated subjects"),
        ("bad_perspective", "錯誤透視", "incorrect perspective"),
        ("tilted_horizon", "歪斜地平線", "unintentionally tilted horizon"),
        ("clutter", "視覺雜亂", "visual clutter"),
        default=("cropped",),
        random_min=1,
        random_max=3,
    ),
    _multi(
        "negative_artifacts",
        "文字與生成瑕疵",
        "負面提示詞",
        ("text", "文字", "text"),
        ("watermark", "浮水印", "watermark"),
        ("logo", "標誌", "logo"),
        ("signature", "簽名", "signature"),
        ("frame", "多餘邊框", "decorative frame"),
        ("ui", "介面元素", "user-interface elements"),
        ("generation_artifacts", "生成殘影", "generation artifacts"),
        default=("text", "watermark", "logo", "signature"),
        random_min=3,
        random_max=5,
    ),
    _multi(
        "negative_environment",
        "場景結構問題",
        "負面提示詞",
        ("warped_architecture", "建築扭曲", "warped architecture"),
        ("floating_objects", "不合理漂浮物", "unintended floating objects"),
        ("repeating_patterns", "重複紋理", "repeating texture patterns"),
        ("flat_depth", "景深扁平", "flat spatial depth"),
        ("empty_detail", "細節空洞", "empty untextured surfaces"),
        ("people", "排除人物", "people"),
        default=("warped_architecture", "repeating_patterns"),
        random_min=1,
        random_max=3,
    ),
)


def _extra_options(*values: tuple[str, str, str]) -> tuple[TagOption, ...]:
    return tuple(_option(*value) for value in values)


_CHARACTER_OPTION_EXTENSIONS: Final[dict[str, tuple[TagOption, ...]]] = {
    "age_impression": _extra_options(
        ("prime_adult", "盛年成年人", "adult in their prime"),
        ("seasoned_adult", "歷練成年人", "seasoned adult appearance"),
        ("elderly", "高齡成年人", "elderly adult appearance"),
    ),
    "fantasy_race": _extra_options(
        ("aasimar", "天界裔", "aasimar"),
        ("oni", "鬼族", "oni"),
        ("dryad", "樹精", "dryad"),
        ("djinn", "燈神族", "djinn"),
        ("shadowfolk", "影族", "shadow folk"),
        ("mothfolk", "蛾族獸人", "mothfolk"),
        ("avianfolk", "鳥族獸人", "avian folk"),
        ("lizardfolk", "蜥蜴族獸人", "lizardfolk"),
        ("slime_humanoid", "史萊姆人形", "humanoid slime being"),
        ("living_doll", "活人偶", "living doll person"),
        ("golem", "魔像族", "sentient golem"),
        ("cyborg", "改造人", "cyborg"),
        ("clone", "複製人", "engineered clone adult"),
        ("witchblood", "巫血族", "witch-blooded human"),
        ("fae_noble", "妖精貴族", "noble fae"),
        ("moonfolk", "月民", "moon folk"),
        ("sunfolk", "日民", "sun folk"),
        ("voidborn", "虛空裔", "voidborn humanoid"),
        ("giantkin", "巨人裔", "giantkin"),
        ("halfling", "半身人", "adult halfling"),
    ),
    "height": _extra_options(
        ("diminutive", "極度矮小", "diminutive adult stature"),
        ("above_average", "略高", "above-average height"),
        ("towering", "巨人般高大", "towering stature"),
    ),
    "build": _extra_options(
        ("willowy", "柳條纖長", "willowy build"),
        ("lithe", "輕盈柔韌", "lithe build"),
        ("soft", "柔軟肉感", "soft adult build"),
        ("broad", "寬厚", "broad powerful build"),
        ("bodybuilder", "健美型", "bodybuilder physique"),
        ("plus_size", "豐腴大尺碼", "plus-size adult build"),
    ),
    "body_proportions": _extra_options(
        ("short_torso", "短軀幹", "short-torso proportions"),
        ("long_torso", "長軀幹", "long-torso proportions"),
        ("top_heavy", "上身量感突出", "top-heavy proportions"),
        ("bottom_heavy", "下身量感突出", "bottom-heavy proportions"),
        ("heroic", "英雄式比例", "heroic body proportions"),
    ),
    "female_bust": _extra_options(
        ("flat", "近乎平胸", "nearly flat adult chest"),
        ("full_medium", "中等偏豐滿", "full medium breasts"),
    ),
    "male_chest": _extra_options(
        ("soft", "柔和胸膛", "soft adult male chest"),
        ("hairy", "有胸毛", "hairy adult chest"),
        ("barrel", "桶狀胸膛", "barrel chest"),
        ("sculpted", "雕塑感胸肌", "sculpted pectorals"),
    ),
    "waist": _extra_options(
        ("high_waist", "高腰線", "high waistline"),
        ("low_waist", "低腰線", "low waistline"),
        ("corseted", "束腰輪廓", "corseted waist silhouette"),
        ("athletic", "運動型腰腹", "athletic waist and core"),
    ),
    "hips": _extra_options(
        ("angular", "稜角臀胯", "angular hips"),
        ("rounded", "圓潤臀胯", "rounded hips"),
        ("high_set", "高臀線", "high-set hips"),
        ("strong", "強健臀胯", "strong athletic hips"),
    ),
    "shoulders": _extra_options(
        ("square", "平直方肩", "square shoulders"),
        ("rounded", "圓肩", "rounded shoulders"),
        ("athletic", "運動型肩線", "athletic shoulders"),
        ("delicate", "纖柔肩線", "delicate shoulders"),
    ),
    "legs": _extra_options(
        ("compact", "短而勻稱", "compact proportional legs"),
        ("curvy", "曲線腿型", "curvy adult legs"),
        ("muscular", "肌肉腿型", "muscular legs"),
        ("runner", "跑者腿型", "runner's legs"),
        ("dancer", "舞者腿型", "dancer's legs"),
    ),
    "skin_tone": _extra_options(
        ("cool_fair", "冷調白皙", "cool fair skin"),
        ("peach", "蜜桃膚色", "peach-toned skin"),
        ("golden", "金棕膚色", "golden brown skin"),
        ("copper", "赤銅膚色", "copper skin"),
        ("gray", "灰色奇幻膚色", "fantasy gray skin"),
        ("obsidian", "黑曜石膚色", "obsidian-black skin"),
        ("rose", "玫瑰粉膚色", "rose-pink skin"),
        ("translucent", "半透明膚質", "translucent ethereal skin"),
    ),
    "face_shape": _extra_options(
        ("triangle", "三角臉", "triangular face"),
        ("inverted_triangle", "倒三角臉", "inverted-triangle face"),
        ("soft_square", "柔和方臉", "soft square face"),
        ("chiseled", "雕刻感臉型", "chiseled face"),
        ("full_cheeks", "豐潤臉頰", "full-cheeked face"),
        ("gaunt", "消瘦臉型", "gaunt face"),
        ("high_cheekbones", "高顴骨", "high-cheekboned face"),
        ("delicate", "精緻小臉", "delicate small face"),
    ),
    "eye_shape": _extra_options(
        ("deep_set", "深邃眼", "deep-set eyes"),
        ("wide_set", "寬眼距", "wide-set eyes"),
        ("close_set", "窄眼距", "close-set eyes"),
        ("monolid", "單眼皮", "monolid eyes"),
        ("double_lid", "明顯雙眼皮", "defined double-lid eyes"),
        ("catlike", "貓眼", "catlike eyes"),
        ("doe", "小鹿眼", "doe eyes"),
        ("sanpaku", "三白眼", "sanpaku eyes"),
    ),
    "eye_color": _extra_options(
        ("black", "黑色", "black eyes"),
        ("cyan", "青藍色", "cyan eyes"),
        ("turquoise", "綠松石色", "turquoise eyes"),
        ("pink", "粉紅色", "pink eyes"),
        ("orange", "橙色", "orange eyes"),
        ("white", "純白色", "pure white eyes"),
    ),
    "hair_length": _extra_options(
        ("ear", "及耳", "ear-length hair"),
        ("neck", "及頸", "neck-length hair"),
        ("chest", "及胸", "chest-length hair"),
        ("hip", "及臀", "hip-length hair"),
        ("ankle", "及踝", "ankle-length hair"),
    ),
    "hair_style": _extra_options(
        ("wolf_cut", "狼尾剪", "wolf cut hairstyle"),
        ("shag", "層次碎剪", "shag haircut"),
        ("lob", "長鮑伯", "long bob haircut"),
        ("french_braid", "法式辮", "French braid"),
        ("fishtail_braid", "魚骨辮", "fishtail braid"),
        ("waterfall_braid", "瀑布辮", "waterfall braid"),
        ("rope_braid", "繩結辮", "rope braid"),
        ("braided_ponytail", "編髮馬尾", "braided ponytail"),
        ("low_ponytail", "低馬尾", "low ponytail"),
        ("half_up", "公主頭", "half-up hairstyle"),
        ("topknot", "高髮髻", "topknot"),
        ("side_bun", "側髮髻", "side hair bun"),
        ("dreadlocks", "長髒辮", "dreadlocks"),
        ("afro", "爆炸捲髮", "afro hairstyle"),
        ("slicked_back", "油頭後梳", "slicked-back hair"),
    ),
    "bangs": _extra_options(
        ("micro", "眉上短瀏海", "micro bangs"),
        ("arched", "弧形瀏海", "arched bangs"),
        ("choppy", "碎剪瀏海", "choppy bangs"),
        ("long_side", "長側瀏海", "long side bangs"),
        ("split", "中分瀏海", "center-parted bangs"),
        ("braided", "編髮瀏海", "braided bangs"),
    ),
    "hair_color": _extra_options(
        ("rose_gold", "玫瑰金", "rose-gold hair"),
        ("lavender", "薰衣草紫", "lavender hair"),
        ("navy", "深藍", "navy-blue hair"),
        ("cyan", "青色", "cyan hair"),
        ("orange", "橙色", "orange hair"),
        ("peach", "蜜桃色", "peach-colored hair"),
        ("mint", "薄荷綠", "mint-green hair"),
        ("burgundy", "酒紅", "burgundy hair"),
    ),
    "hair_texture": _extra_options(
        ("fine", "細軟", "fine hair texture"),
        ("thick", "濃密", "thick hair texture"),
        ("kinky", "緊密捲曲", "tightly coiled hair"),
        ("feathered", "羽毛層次", "feathered hair texture"),
        ("windswept", "風吹動感", "windswept hair"),
        ("metallic", "金屬光澤", "metallic hair sheen"),
    ),
    "expression": (
        *_extra_options(
            ("serene", "安詳", "serene expression"),
            ("hopeful", "充滿希望", "hopeful expression"),
            ("determined", "堅定", "determined expression"),
            ("defiant", "不屈", "defiant expression"),
            ("suspicious", "狐疑", "suspicious expression"),
            ("annoyed", "不耐", "annoyed expression"),
            ("angry", "憤怒", "angry expression"),
            ("terrified", "驚恐", "terrified expression"),
            ("tearful", "含淚", "tearful expression"),
            ("crying", "哭泣", "crying expression"),
            ("embarrassed", "尷尬", "embarrassed expression"),
            ("blushing", "臉紅", "blushing expression"),
            ("smug", "得意", "smug expression"),
            ("bored", "無聊", "bored expression"),
            ("sleepy", "睏倦", "sleepy expression"),
            ("focused", "專注", "focused expression"),
            ("curious", "好奇", "curious expression"),
        ),
        _adult_option("seductive", "誘惑神情（18+）", "consensual adult seductive expression"),
        _adult_option("aroused", "情慾神情（18+）", "consensual adult aroused expression"),
        _adult_option("ecstatic", "歡愉神情（18+）", "consensual adult ecstatic expression"),
    ),
    "fantasy_traits": _extra_options(
        ("antlers", "鹿角", "branching antlers"),
        ("fin_ears", "魚鰭耳", "finlike ears"),
        ("gills", "鰓", "visible gills"),
        ("tentacle_hair", "觸手髮", "living tentacle hair"),
        ("extra_arms", "多臂", "multiple symmetrical arms"),
        ("third_eye", "第三眼", "mystical third eye"),
        ("stone_skin", "石質肌膚", "stone-textured skin"),
        ("wooden_skin", "木質肌膚", "wood-grain skin"),
        ("slime_body", "凝膠身體", "translucent slime body"),
        ("flame_hair", "火焰髮", "living flame hair"),
        ("shadow_body", "影子身體", "shadowlike body"),
        ("constellation_skin", "星座肌膚", "constellations across the skin"),
    ),
    "distinctive_marks": _extra_options(
        ("nose_scar", "鼻樑疤痕", "scar across the nose"),
        ("lip_scar", "唇邊疤痕", "small scar near the lip"),
        ("burn_scar", "燒傷疤痕", "old burn scar"),
        ("birthmark", "胎記", "distinctive birthmark"),
        ("vitiligo", "白斑膚色", "vitiligo skin pattern"),
        ("dimples", "酒窩", "visible dimples"),
        ("pierced_ears", "多耳洞", "multiple ear piercings"),
        ("nose_piercing", "鼻環", "nose piercing"),
        ("lip_piercing", "唇環", "lip piercing"),
        ("facial_runes", "臉部符文", "facial rune markings"),
        ("cyber_lines", "義體接縫", "subtle cybernetic seam lines"),
        ("glowing_freckles", "發光雀斑", "glowing freckles"),
        ("metallic_makeup", "金屬妝", "metallic eye makeup"),
        ("smoky_makeup", "暈染煙燻妝", "softly smudged smoky makeup accent"),
    ),
    "outfit_archetype": (
        *_extra_options(
            ("workwear", "機能工作服", "practical workwear"),
            ("sportswear", "運動服", "modern adult sportswear"),
            ("swimwear", "泳裝", "fitted adult swimwear"),
            ("ball_gown", "舞會禮服", "ornate ball gown"),
            ("cocktail_dress", "雞尾酒禮服", "elegant cocktail dress"),
            ("tailcoat", "燕尾服", "formal tailcoat ensemble"),
            ("trench_coat", "風衣", "layered trench-coat outfit"),
            ("pilot", "飛行員裝", "detailed pilot outfit"),
            ("sailor", "航海家裝", "weathered sailor outfit"),
            ("mechanic", "機械師裝", "grease-stained mechanic outfit"),
            ("healer", "治療師裝", "fantasy healer robes"),
            ("scholar", "學者裝", "layered adult scholar attire"),
            ("witch", "女巫裝", "elegant fantasy witch outfit"),
            ("samurai", "武士甲冑", "layered samurai armor"),
            ("ninja", "忍者裝", "practical shinobi outfit"),
            ("desert_nomad", "沙漠遊牧裝", "layered desert nomad attire"),
            ("tribal_fantasy", "奇幻部族裝", "respectful fantasy tribal attire"),
            ("post_apocalyptic", "末日倖存者裝", "patched post-apocalyptic outfit"),
            ("gothic_lolita_adult", "成年哥德洋裝", "adult gothic frilled fashion"),
            ("techwear", "科技機能服", "futuristic techwear"),
        ),
        _adult_option("lingerie", "情趣內衣（18+）", "consensual adult lingerie"),
        _adult_option("sheer_lingerie", "透視情趣內衣（18+）", "consensual adult sheer lingerie"),
        _adult_option("topless", "上身裸體（18+）", "consensual topless adult"),
        _adult_option("nude", "裸體（18+）", "fully nude consenting adult"),
        _adult_option("body_paint", "人體藝術彩繪（18+）", "consensual nude adult body paint"),
        _adult_option(
            "bondage_fashion", "合意束縛風服裝（18+）", "consensual adult bondage fashion"
        ),
    ),
    "outfit_materials": (
        *_extra_options(
            ("cotton", "棉布", "soft cotton fabric"),
            ("denim", "丹寧", "textured denim fabric"),
            ("wool", "羊毛", "woven wool fabric"),
            ("chainmail", "鎖子甲", "interlocking chainmail"),
            ("scale_armor", "鱗甲", "layered scale armor"),
            ("ceramic", "陶瓷甲片", "glazed ceramic armor plates"),
            ("crystal", "水晶材質", "faceted crystal clothing accents"),
            ("latex", "乳膠質感", "glossy latex material"),
            ("mesh", "網布", "layered mesh fabric"),
        ),
        _adult_option("bare_skin", "無衣料／裸身（18+）", "consensual bare adult skin"),
    ),
    "outfit_palette": (
        *_extra_options(
            ("red_gold", "紅金", "crimson-and-gold clothing palette"),
            ("green_bronze", "綠銅", "green-and-bronze clothing palette"),
            ("purple_black", "紫黑", "purple-and-black clothing palette"),
            ("pink_white", "粉白", "pink-and-white clothing palette"),
            ("rainbow", "彩虹", "rainbow clothing palette"),
            ("iridescent", "虹彩", "iridescent clothing palette"),
            ("desaturated", "低飽和", "desaturated clothing palette"),
            ("custom_emblem", "家徽配色", "heraldic clothing palette"),
        ),
        _adult_option(
            "natural_skin",
            "自然裸膚色（18+）",
            "consensual adult natural skin-tone palette",
        ),
    ),
    "accessories": (
        *_extra_options(
            ("bracelets", "手鐲", "stacked bracelets"),
            ("rings", "戒指", "ornate rings"),
            ("brooch", "胸針", "decorative brooch"),
            ("hairpin", "髮簪", "ornate hairpin"),
            ("tiara", "小皇冠", "delicate tiara"),
            ("mask", "面具", "ornate face mask"),
            ("scarf", "圍巾", "flowing scarf"),
            ("belt_pouches", "腰包", "utility belt pouches"),
            ("quiver", "箭袋", "detailed arrow quiver"),
            ("shield", "盾牌", "decorated shield"),
            ("dagger", "匕首", "ornate dagger"),
            ("bow", "長弓", "elegant longbow"),
            ("hammer", "戰鎚", "fantasy war hammer"),
            ("potion_belt", "藥水帶", "belt of glowing potions"),
            ("mechanical_drone", "機械無人機", "small companion drone"),
            ("pocket_watch", "懷錶", "antique pocket watch"),
            ("umbrella", "傘", "decorative umbrella"),
        ),
        _adult_option("body_chain", "身體鍊飾（18+）", "consensual adult body-chain jewelry"),
        _adult_option(
            "intimate_harness", "情趣皮革帶（18+）", "consensual adult intimate leather harness"
        ),
        _adult_option("nipple_jewelry", "乳頭飾品（18+）", "consensual adult nipple jewelry"),
    ),
    "pose": (
        *_extra_options(
            ("arms_crossed", "雙臂交叉", "arms-crossed pose"),
            ("hands_on_hips", "雙手叉腰", "hands-on-hips pose"),
            ("one_hand_raised", "單手舉起", "one hand raised"),
            ("reaching_out", "伸手向前", "reaching toward the viewer"),
            ("kneeling", "跪姿", "kneeling pose"),
            ("crouching", "蹲姿", "crouching pose"),
            ("lying_down", "躺姿", "lying-down pose"),
            ("leaning_wall", "倚牆", "leaning against a wall"),
            ("turning", "轉身動作", "turning in motion"),
            ("jumping", "跳躍", "dynamic jumping pose"),
            ("dancing", "舞蹈", "expressive dancing pose"),
            ("bowing", "鞠躬", "formal bowing pose"),
            ("meditating", "冥想", "cross-legged meditation pose"),
            ("reading", "閱讀", "reading a book"),
            ("writing", "書寫", "writing in a journal"),
            ("drinking", "飲用飲料", "drinking from a cup"),
            ("playing_instrument", "演奏樂器", "playing a musical instrument"),
            ("aiming_bow", "拉弓瞄準", "aiming a drawn bow"),
            ("sword_swing", "揮劍", "dynamic sword-swinging action"),
            ("shield_guard", "舉盾防禦", "defensive shield stance"),
        ),
        _adult_option(
            "artistic_nude", "人體藝術裸姿（18+）", "consensual adult artistic nude pose"
        ),
        _adult_option("topless_pose", "上身裸露姿勢（18+）", "consensual adult topless pose"),
        _adult_option("nude_recline", "裸體斜躺（18+）", "consensual adult nude reclining pose"),
        _adult_option("erotic_kneel", "情慾跪姿（18+）", "consensual adult erotic kneeling pose"),
        _adult_option("open_leg_pose", "張腿姿勢（18+）", "consensual adult open-leg erotic pose"),
        _adult_option("breast_touch", "撫胸姿勢（18+）", "consensual adult breast-touching pose"),
        _adult_option(
            "nude_back_arch", "裸體拱背（18+）", "consensual adult nude back-arching pose"
        ),
        _adult_option(
            "self_pleasure", "自慰姿勢（18+）", "consensual adult solo self-pleasure pose"
        ),
        _adult_option(
            "erotic_stretch", "情慾伸展（18+）", "consensual adult erotic stretching pose"
        ),
        _adult_option(
            "intimate_bondage_pose", "合意束縛姿勢（18+）", "consensual adult intimate bondage pose"
        ),
    ),
    "framing": (
        *_extra_options(
            ("extreme_closeup", "極特寫", "extreme facial close-up"),
            ("cowboy_shot", "牛仔景", "cowboy-shot framing"),
            ("wide_full_body", "廣景全身", "wide full-body framing"),
            ("turnaround_sheet", "多視角設定圖", "multi-view character turnaround sheet"),
        ),
        _adult_option(
            "intimate_full_body",
            "成人親密全身構圖（18+）",
            "consensual adult intimate full-body framing",
        ),
    ),
    "viewpoint": _extra_options(
        ("front", "正面", "front view"),
        ("rear_three_quarter", "後方四分之三", "rear three-quarter view"),
        ("dutch_angle", "荷蘭角", "Dutch-angle view"),
        ("overhead", "正上方俯視", "overhead view"),
        ("worm_eye", "蟲視角", "worm's-eye view"),
    ),
    "character_lighting": _extra_options(
        ("rembrandt", "林布蘭光", "Rembrandt lighting"),
        ("butterfly", "蝴蝶光", "butterfly portrait lighting"),
        ("split", "分割光", "dramatic split lighting"),
        ("underlight", "下方打光", "theatrical underlighting"),
        ("colored_gel", "彩色濾片光", "colored-gel studio lighting"),
        ("magic_glow", "魔法自發光", "magical self-illumination"),
        (
            "firelight",
            "營火光",
            "warm flickering campfire illumination on the character",
        ),
        ("overcast", "陰天柔光", "soft overcast lighting"),
    ),
    "character_style": _extra_options(
        ("manga_ink", "漫畫墨線", "detailed manga ink illustration"),
        ("western_animation", "歐美動畫", "polished Western animation design"),
        ("storybook", "故事書插畫", "storybook character illustration"),
        ("gouache", "廣告顏料", "gouache character painting"),
        ("oil_painting", "油畫", "classical oil-painted character portrait"),
        ("charcoal", "炭筆", "expressive charcoal character drawing"),
        ("low_poly", "低多邊形 3D", "stylized low-poly character render"),
        ("clay", "黏土動畫", "handcrafted clay character render"),
        ("retro_game", "復古遊戲立繪", "retro game character portrait"),
        ("visual_novel", "視覺小說立繪", "polished visual-novel character art"),
    ),
    "character_backdrop": _extra_options(
        ("spotlight", "聚光舞台", "dark stage spotlight backdrop"),
        ("graphic_shapes", "幾何圖形", "bold graphic-shape backdrop"),
        ("floral", "花卉背景", "ornamental floral backdrop"),
        ("magic_circle", "魔法陣", "glowing magic-circle backdrop"),
        ("smoke", "煙霧背景", "layered atmospheric smoke backdrop"),
    ),
    "character_detail": _extra_options(
        ("minimal", "極簡", "minimal character detail"),
        ("production_ready", "製作用設定細節", "production-ready character design detail"),
        ("micro_detail", "微觀材質細節", "fine micro-texture detail"),
        ("ornate", "裝飾性高細節", "ornate decorative detail"),
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V2: Final[dict[str, tuple[TagOption, ...]]] = {
    "age_impression": _extra_options(
        ("fresh_adult", "初入成年期", "fresh adult appearance"),
        ("established_adult", "穩重成年人", "established adult appearance"),
        ("late_middle_age", "壯年後期", "late-middle-aged adult appearance"),
        ("venerable", "德高望重", "venerable adult appearance"),
    ),
    "fantasy_race": _extra_options(
        ("centaur", "半人馬族", "centaur humanoid"),
        ("minotaur", "牛頭人族", "minotaur humanoid"),
        ("satyr", "薩堤爾族", "satyr humanoid"),
        ("harpy", "鷹身人族", "harpy humanoid"),
        ("naga", "娜迦族", "naga humanoid"),
        ("kitsune", "妖狐族", "kitsune humanoid"),
        ("tengu", "天狗族", "tengu humanoid"),
        ("phoenixkin", "鳳凰裔", "phoenixkin humanoid"),
        ("insectfolk", "昆蟲族", "sapient insectfolk humanoid"),
        ("sharkfolk", "鯊族", "sapient sharkfolk humanoid"),
        ("plantfolk", "植物族", "sapient plantfolk humanoid"),
        ("mushroomfolk", "蕈族", "sapient mushroomfolk humanoid"),
        ("crystalborn", "水晶裔", "crystalborn humanoid"),
        ("celestial", "星界族", "celestial humanoid"),
        ("eldritch", "異界裔", "eldritch humanoid"),
        ("ursafolk", "熊族獸人", "ursafolk humanoid"),
        ("cervidfolk", "鹿族獸人", "cervidfolk humanoid"),
        ("bovinefolk", "牛族獸人", "bovinefolk humanoid"),
        ("spiderfolk", "蛛族", "sapient spiderfolk humanoid"),
        ("deepfolk", "深海眷族", "sapient deep-sea humanoid"),
    ),
    "height": _extra_options(
        ("compact_tall", "修長但緊湊", "compact tall stature"),
        ("statuesque", "雕像般高挑", "statuesque adult height"),
        ("colossal", "超常高大", "colossal fantasy stature"),
    ),
    "build": _extra_options(
        ("rangy", "瘦長", "rangy adult build"),
        ("swimmer", "游泳選手型", "swimmer's athletic build"),
        ("powerlifter", "力量型", "powerlifter physique"),
        ("dancer", "舞者型", "dancer's flexible build"),
        ("robust", "健壯", "robust adult build"),
        ("rounded", "圓潤", "rounded adult build"),
    ),
    "body_proportions": _extra_options(
        ("long_arms", "手臂修長", "long-armed proportions"),
        ("broad_torso", "寬闊軀幹", "broad-torso proportions"),
        ("narrow_frame", "窄骨架", "narrow-frame proportions"),
        ("statuesque", "雕像式比例", "statuesque body proportions"),
        ("stylized_heroic", "誇張英雄式", "stylized heroic proportions"),
    ),
    "male_chest": _extra_options(
        ("slender", "纖薄胸膛", "slender adult male chest"),
        ("square", "方正胸膛", "square adult male chest"),
        ("massive", "巨量胸肌", "massive adult pectorals"),
        ("scarred", "帶疤胸膛", "scarred adult male chest"),
    ),
    "waist": _extra_options(
        ("tapered", "收束腰線", "tapered waistline"),
        ("boxy", "方正腰身", "boxy waistline"),
        ("soft_round", "柔和圓腰", "soft rounded waistline"),
        ("muscular_core", "清晰核心肌群", "defined muscular core"),
    ),
    "hips": _extra_options(
        ("tapered", "收束臀胯", "tapered hips"),
        ("heart_shaped", "心形臀胯", "heart-shaped adult hips"),
        ("low_set", "低臀線", "low-set hips"),
        ("sculpted", "雕塑感臀胯", "sculpted athletic hips"),
    ),
    "shoulders": _extra_options(
        ("tapered", "收束肩線", "tapered shoulders"),
        ("high_set", "高肩線", "high-set shoulders"),
        ("powerful", "強壯肩線", "powerful shoulders"),
        ("asymmetrical", "自然不對稱肩線", "naturally asymmetrical shoulders"),
    ),
    "legs": _extra_options(
        ("long_slender", "修長纖腿", "long slender legs"),
        ("soft_full", "柔和豐潤腿型", "soft full adult legs"),
        ("sprinter", "短跑選手腿型", "sprinter's powerful legs"),
        ("knock_kneed", "內收膝線", "subtly inward-kneed stance"),
        ("digitigrade", "趾行腿型", "digitigrade fantasy legs"),
    ),
    "skin_tone": _extra_options(
        ("ivory", "象牙色", "ivory skin"),
        ("neutral_beige", "中性米色", "neutral beige skin"),
        ("caramel", "焦糖色", "caramel-brown skin"),
        ("mahogany", "桃花心木色", "mahogany-brown skin"),
        ("ruby", "紅寶石奇幻膚色", "ruby-red fantasy skin"),
        ("gold_metallic", "金屬金膚色", "metallic gold skin"),
        ("pearl", "珍珠光膚色", "pearl-lustrous skin"),
        ("galaxy", "星河膚色", "galaxy-patterned skin"),
    ),
    "face_shape": _extra_options(
        ("oblong", "長橢圓臉", "oblong face"),
        ("trapezoid", "梯形臉", "trapezoidal face"),
        ("soft_rounded_adult", "柔和圓潤成年臉", "soft rounded adult facial features"),
        ("sculptural", "雕塑感臉型", "sculptural facial structure"),
        ("broad_jaw", "寬下顎臉型", "broad-jawed face"),
    ),
    "eye_shape": _extra_options(
        ("protruding", "微凸眼", "slightly prominent eyes"),
        ("recessed", "深陷眼", "recessed eyes"),
        ("siren", "魅惑上挑眼", "elongated siren eyes"),
        ("puppy", "無辜下垂眼", "soft puppy-like eyes"),
        ("crescent", "月牙笑眼", "crescent-shaped smiling eyes"),
        ("asymmetrical", "自然不對稱眼型", "naturally asymmetrical eye shapes"),
    ),
    "eye_color": _extra_options(
        ("cobalt", "鈷藍色", "cobalt blue eyes"),
        ("indigo", "靛藍色", "indigo eyes"),
        ("emerald", "翡翠色", "emerald green eyes"),
        ("lime", "萊姆綠", "lime green eyes"),
        ("copper", "赤銅色", "copper eyes"),
        ("bronze", "青銅色", "bronze eyes"),
        ("rose_gold", "玫瑰金", "rose-gold eyes"),
        ("lavender", "薰衣草紫", "lavender eyes"),
        ("magenta", "洋紅色", "magenta eyes"),
        ("ice_blue", "冰藍色", "ice-blue eyes"),
    ),
    "hair_length": _extra_options(
        ("cheek", "及頰", "cheek-length hair"),
        ("lower_back", "及下背", "lower-back-length hair"),
        ("calf", "及小腿", "calf-length hair"),
    ),
    "hair_style": _extra_options(
        ("pageboy", "童花頭", "adult pageboy haircut"),
        ("bixie", "短鮑伯精靈剪", "bixie haircut"),
        ("mullet", "鯔魚頭", "modern mullet hairstyle"),
        ("cornrows", "貼頭辮", "neat cornrow braids"),
        ("box_braids", "箱型辮", "box-braid hairstyle"),
        ("halo_braid", "光環辮", "halo braid hairstyle"),
        ("bubble_ponytail", "泡泡馬尾", "bubble ponytail"),
        ("space_buns", "太空包頭", "space-bun hairstyle"),
        ("victory_rolls", "勝利卷", "vintage victory rolls"),
        ("pompadour", "蓬巴杜髮型", "pompadour hairstyle"),
        ("quiff", "飛機頭", "textured quiff hairstyle"),
        ("locs_updo", "髒辮盤髮", "locs updo hairstyle"),
    ),
    "bangs": _extra_options(
        ("bottleneck", "瓶頸瀏海", "bottleneck bangs"),
        ("side_piece", "側邊重點瀏海", "face-framing side bangs"),
        ("feathered", "羽毛瀏海", "feathered bangs"),
        ("curly", "捲曲瀏海", "curly bangs"),
        ("asymmetrical", "不對稱瀏海", "asymmetrical bangs"),
        ("veil", "面紗式長瀏海", "veil-like long bangs"),
    ),
    "hair_color": _extra_options(
        ("charcoal", "炭黑色", "charcoal-black hair"),
        ("chestnut", "栗棕色", "chestnut-brown hair"),
        ("honey_blonde", "蜂蜜金", "honey-blonde hair"),
        ("strawberry_blonde", "草莓金", "strawberry-blonde hair"),
        ("copper", "赤銅色", "copper-red hair"),
        ("scarlet", "鮮紅色", "scarlet hair"),
        ("cobalt", "鈷藍色", "cobalt-blue hair"),
        ("emerald", "翡翠綠", "emerald-green hair"),
        ("magenta", "洋紅色", "magenta hair"),
        ("lilac", "淡紫丁香色", "lilac hair"),
    ),
    "hair_texture": _extra_options(
        ("glasslike", "玻璃光澤", "glasslike hair sheen"),
        ("velvety", "天鵝絨質感", "velvety hair texture"),
        ("frizzy", "自然毛躁", "naturally frizzy hair"),
        ("crimped", "玉米鬚壓紋", "crimped hair texture"),
        ("ringlets", "螺旋小捲", "defined ringlet texture"),
        ("cloudlike", "雲朵蓬鬆", "cloudlike fluffy hair"),
    ),
    "expression": (
        *_extra_options(
            ("relieved", "如釋重負", "relieved expression"),
            ("awed", "敬畏驚嘆", "awe-struck expression"),
            ("guilty", "愧疚", "guilty expression"),
            ("resigned", "認命", "resigned expression"),
            ("protective", "守護意志", "protective expression"),
            ("calculating", "精於算計", "calculating expression"),
            ("playful", "玩心大起", "playful expression"),
            ("nostalgic", "懷念", "nostalgic expression"),
            ("haunted", "心有陰影", "haunted expression"),
            ("triumphant", "勝利喜悅", "triumphant expression"),
            ("devoted", "深情專注", "devoted expression"),
            ("entranced", "著迷", "entranced expression"),
        ),
        _adult_option(
            "bedroom_eyes",
            "迷離媚眼（18+）",
            "consensual adult bedroom-eyes expression",
        ),
        _adult_option(
            "lustful_gaze",
            "情慾凝視（18+）",
            "consensual adult lustful gaze",
        ),
        _adult_option(
            "breathless_desire",
            "喘息渴望（18+）",
            "consensual adult breathless desire expression",
        ),
        _adult_option(
            "inviting_smile",
            "親密邀請微笑（18+）",
            "consensual adult inviting intimate smile",
        ),
        _adult_option(
            "flushed_arousal",
            "情動潮紅（18+）",
            "consensual adult flushed arousal expression",
        ),
        _adult_option(
            "post_climax_bliss",
            "高潮後愉悅（18+）",
            "consensual adult post-climax bliss expression",
        ),
    ),
    "fantasy_traits": _extra_options(
        ("hooves", "蹄足", "fantasy hooves"),
        ("equine_lower_body", "馬身下半身", "equine lower body"),
        ("serpentine_lower_body", "蛇身下半身", "serpentine lower body"),
        ("spider_lower_body", "蜘蛛下半身", "spiderlike lower body"),
        ("avian_talons", "鳥爪足", "avian talons"),
        ("bird_feathers", "鳥羽覆體", "decorative body feathers"),
        ("phoenix_wings", "鳳凰火翼", "fiery phoenix wings"),
        ("multiple_tails", "多條尾巴", "multiple expressive tails"),
        ("fox_ears", "狐耳", "foxlike ears"),
        ("bovine_horns", "牛角", "sweeping bovine horns"),
        ("goat_horns", "山羊角", "curled goat horns"),
        ("insect_antennae", "昆蟲觸角", "delicate insect antennae"),
        ("chitin", "幾丁質甲殼", "chitinous body plates"),
        ("compound_eyes", "複眼", "faceted compound eyes"),
        ("shark_tail", "鯊魚尾", "powerful shark tail"),
        ("web_spinnerets", "蛛絲器官", "fantasy web spinnerets"),
        ("living_vines", "活藤蔓", "living vines around the body"),
        ("body_flowers", "身體花朵", "flowers growing from the body"),
        ("mushroom_cap", "蕈傘冠", "mushroom-cap crown"),
        ("spore_cloud", "孢子微光", "faint luminous spore cloud"),
        ("crystal_body", "水晶身體", "translucent crystalline body"),
        ("liquid_body", "液態身體", "flowing liquid body"),
        ("smoke_body", "煙霧身體", "partially smoke-formed body"),
        ("spectral_limbs", "幽體肢體", "translucent spectral limbs"),
        ("eye_cluster", "多眼群", "cluster of mystical eyes"),
        ("bioluminescent_patterns", "生物光紋", "bioluminescent body patterns"),
        ("beak", "鳥喙", "elegant avian beak"),
    ),
    "distinctive_marks": _extra_options(
        ("constellation_tattoo", "星座刺青", "constellation tattoo"),
        ("ritual_brand", "儀式烙印", "old ritual brand"),
        ("surgical_scars", "手術疤痕", "subtle surgical scars"),
        ("glowing_veins", "發光脈絡", "subtle glowing veins"),
        ("ink_stained_hands", "墨跡手指", "ink-stained hands"),
    ),
    "accessories": _extra_options(
        ("ear_cuffs", "耳骨夾", "ornate ear cuffs"),
        ("shoulder_cape", "單肩披風", "single-shoulder cape"),
        ("spellbook", "魔法書", "ornate spellbook"),
        ("greatsword", "巨劍", "massive fantasy greatsword"),
        ("fan", "摺扇", "ornate folding fan"),
        ("parasol", "陽傘", "decorative parasol"),
        ("prosthetic_arm", "義肢手臂", "detailed prosthetic arm"),
    ),
    "pose": (
        *_extra_options(
            ("sprinting", "全速奔跑", "full sprinting action"),
            ("climbing", "攀爬", "dynamic climbing pose"),
            ("praying", "祈禱", "reverent praying pose"),
            ("embracing_self", "環抱自己", "self-embracing pose"),
            ("hand_to_heart", "手按胸口", "hand-over-heart pose"),
            ("drawing_weapon", "拔出武器", "weapon-drawing action"),
            ("casting_two_hands", "雙手施法", "two-handed spellcasting pose"),
        ),
        _adult_option(
            "sensual_recline",
            "情慾側躺（18+）",
            "consensual adult sensual side-reclining pose",
        ),
        _adult_option(
            "intimate_arch",
            "親密拱身（18+）",
            "consensual adult intimate body-arching pose",
        ),
        _adult_option(
            "erotic_squat",
            "情慾蹲姿（18+）",
            "consensual adult erotic squatting pose",
        ),
        _adult_option(
            "inviting_open_pose",
            "親密開放姿勢（18+）",
            "consensual adult inviting open-body pose",
        ),
    ),
    "character_lighting": _extra_options(
        ("moonlit", "冷色月輪光", "cool moonlit character lighting"),
        ("candlelit", "暖燭群光", "warm candlelit character lighting"),
        ("bioluminescent", "生物光", "bioluminescent character lighting"),
        ("neon_rim", "霓虹輪廓光", "neon rim lighting"),
        ("volumetric_backlight", "體積逆光", "volumetric backlighting"),
        ("eclipse", "日蝕光", "eclipse-edge character lighting"),
    ),
    "character_style": _extra_options(
        ("dark_academia", "黑暗學院風", "dark-academia character illustration"),
        ("art_nouveau", "新藝術風", "Art Nouveau character illustration"),
        ("papercut", "剪紙風", "layered papercut character art"),
        ("linocut", "油氈版畫", "linocut character illustration"),
        ("ink_wash", "水墨風", "ink-wash character painting"),
        ("synthwave", "合成波風", "synthwave character art"),
        ("fashion_editorial", "時尚編輯風", "high-fashion editorial character art"),
    ),
    "character_backdrop": _extra_options(
        ("stained_glass", "彩繪玻璃", "stained-glass backdrop"),
        ("moon_disc", "巨大月輪", "large moon-disc backdrop"),
        ("paper_texture", "紙張肌理", "textured paper backdrop"),
        ("floating_runes", "漂浮符文", "floating-rune backdrop"),
    ),
    "character_detail": _extra_options(
        ("clean_shapes", "清晰造型", "clean readable character shapes"),
        ("material_focus", "材質重點", "material-focused character detail"),
        ("couture_detail", "高訂服飾細節", "couture-level costume detail"),
        ("cinematic_detail", "電影級細節", "cinematic character detail"),
    ),
}


_BACKGROUND_OPTION_EXTENSIONS: Final[dict[str, tuple[TagOption, ...]]] = {
    "world_genre": _extra_options(
        ("wuxia", "武俠", "wuxia world"),
        ("xianxia", "仙俠", "xianxia cultivation world"),
        ("biopunk", "生物龐克", "biopunk world"),
        ("clockpunk", "發條龐克", "clockpunk world"),
        ("atompunk", "原子龐克", "atompunk world"),
        ("arcanepunk", "魔導龐克", "arcanepunk world"),
        ("gaslamp", "煤氣燈奇幻", "gaslamp fantasy world"),
        ("nautical_fantasy", "航海奇幻", "nautical fantasy world"),
        ("cosmic_horror", "宇宙恐怖", "cosmic-horror world"),
        ("cozy_fantasy", "溫馨奇幻", "cozy fantasy world"),
    ),
    "location": _extra_options(
        ("palace_garden", "宮殿花園", "ornate palace garden"),
        ("cathedral", "大教堂", "monumental cathedral interior"),
        ("observatory", "天文台", "ancient astronomical observatory"),
        ("workshop", "工匠工坊", "cluttered artisan workshop"),
        ("greenhouse", "溫室", "vast botanical greenhouse"),
        ("archive", "祕密檔案庫", "secure underground archive"),
        ("harbor", "港口", "busy weathered harbor"),
        ("waterfall", "瀑布谷地", "towering waterfall valley"),
        ("canyon", "峽谷", "vast layered canyon"),
        ("tundra", "苔原", "windswept tundra"),
        ("bamboo_grove", "竹林", "misty bamboo grove"),
        ("hot_spring", "溫泉", "secluded natural hot spring"),
        ("battlefield", "古戰場", "abandoned ancient battlefield"),
        ("airship", "飛空艇", "grand airship interior"),
        ("megacity_rooftop", "巨城屋頂", "towering megacity rooftop"),
    ),
    "spatial_scale": _extra_options(
        ("tabletop", "桌面微景", "tabletop miniature scale"),
        ("courtyard", "庭院尺度", "courtyard-scale environment"),
        ("landscape", "地景尺度", "landscape-scale environment"),
        ("planetary", "行星尺度", "planetary-scale vista"),
    ),
    "architecture": _extra_options(
        ("renaissance", "文藝復興", "Renaissance architecture"),
        ("rococo", "洛可可", "Rococo architecture"),
        ("victorian", "維多利亞式", "Victorian architecture"),
        ("byzantine", "拜占庭式", "Byzantine architecture"),
        ("art_deco", "裝飾藝術", "Art Deco architecture"),
        ("vernacular", "地方傳統民居", "regional vernacular architecture"),
        ("crystalline", "水晶建築", "crystalline fantasy architecture"),
        ("biomechanical", "生物機械建築", "biomechanical architecture"),
    ),
    "environment_materials": _extra_options(
        ("plaster", "灰泥", "aged plaster surfaces"),
        ("terracotta", "陶瓦", "warm terracotta surfaces"),
        ("copper", "銅材", "oxidized copper surfaces"),
        ("brass", "黃銅", "polished brass details"),
        ("ice", "冰晶", "translucent ice structures"),
        ("fabric", "織物棚幕", "layered architectural fabric"),
        ("paper", "紙質構造", "layered paper architecture"),
        ("biomass", "生物組織", "living biomechanical tissue"),
    ),
    "terrain": _extra_options(
        ("plateau", "高原", "high plateau terrain"),
        ("badlands", "惡地", "eroded badlands"),
        ("rice_terraces", "梯田", "layered rice terraces"),
        ("coral_reef", "珊瑚礁", "submerged coral-reef terrain"),
        ("urban", "城市地表", "dense urban terrain"),
        ("cloud_sea", "雲海", "vast sea of clouds"),
        ("giant_mushrooms", "巨菇地形", "giant mushroom landscape"),
        ("salt_flat", "鹽湖平原", "reflective salt-flat terrain"),
    ),
    "season": _extra_options(
        ("early_spring", "初春", "early spring season"),
        ("rainy_season", "雨季", "monsoon rainy season"),
        ("dry_season", "旱季", "dry season"),
        ("eternal_winter", "永冬", "eternal winter season"),
    ),
    "time_of_day": _extra_options(
        ("blue_hour", "藍調時刻", "blue hour"),
        ("twilight", "暮光", "atmospheric twilight"),
        ("pre_dawn", "破曉前", "dark pre-dawn"),
        ("eclipse", "日蝕時刻", "solar eclipse"),
        ("timeless_light", "無時間光境", "timeless ambient light"),
    ),
    "weather": _extra_options(
        ("hail", "冰雹", "falling hail"),
        ("sleet", "雨夾雪", "windblown sleet"),
        ("heat_haze", "熱浪", "shimmering heat haze"),
        ("lightning", "閃電", "frequent lightning"),
        ("meteor_shower", "流星雨", "vivid meteor shower"),
        ("ashfall", "火山灰", "falling volcanic ash"),
        ("pollen", "花粉飛舞", "glowing airborne pollen"),
        ("rainbow", "雨後彩虹", "rainbow after rain"),
    ),
    "atmosphere": _extra_options(
        ("nostalgic", "懷舊", "nostalgic atmosphere"),
        ("triumphant", "凱旋", "triumphant atmosphere"),
        ("tense", "緊張", "tense atmosphere"),
        ("uncanny", "詭異", "uncanny atmosphere"),
        ("whimsical", "奇趣", "whimsical atmosphere"),
        ("luxurious", "奢華", "luxurious atmosphere"),
        ("scholarly", "學術氣息", "scholarly atmosphere"),
        ("festive", "節慶", "festive atmosphere"),
        ("oppressive", "壓迫", "oppressive atmosphere"),
        ("adventurous", "冒險感", "adventurous atmosphere"),
    ),
    "background_lighting": _extra_options(
        ("starlight", "星光", "clear starlight illumination"),
        ("lanterns", "燈籠光", "layered lantern illumination"),
        ("window_light", "窗光", "strong window light"),
        ("god_rays", "神聖光束", "dramatic god rays"),
        ("eclipse_rim", "日蝕環光", "eclipse rim lighting"),
        ("underwater_caustics", "水下焦散光", "underwater caustic lighting"),
        ("electrical_arcs", "電弧光", "electrical arc lighting"),
        ("magic_crystals", "魔晶光", "luminous crystal lighting"),
    ),
    "color_palette": _extra_options(
        ("sepia", "褐色懷舊", "sepia color palette"),
        ("black_gold", "黑金", "black-and-gold color palette"),
        ("red_black", "紅黑", "crimson-and-black color palette"),
        ("green_gold", "綠金", "green-and-gold color palette"),
        ("pink_blue", "粉藍", "pink-and-blue color palette"),
        ("rainbow", "彩虹", "rainbow color palette"),
        ("iridescent", "虹彩", "iridescent color palette"),
        ("duotone", "雙色調", "bold duotone palette"),
    ),
    "composition": _extra_options(
        ("golden_ratio", "黃金比例", "golden-ratio composition"),
        ("radial", "放射構圖", "radial composition"),
        ("frame_within_frame", "框中框", "frame-within-a-frame composition"),
        ("negative_space", "留白構圖", "strong negative-space composition"),
        ("s_curve", "S 型動線", "S-curve composition"),
        ("triangular", "三角構圖", "triangular composition"),
        ("split_scene", "分割場景", "split-scene composition"),
    ),
    "camera": _extra_options(
        ("macro", "微距", "macro environmental view"),
        ("ground_level", "貼地視角", "ground-level camera"),
        ("bird_eye", "鳥瞰", "bird's-eye view"),
        ("drone", "無人機跟拍", "drone-camera view"),
        ("orthographic", "正交視圖", "orthographic environment view"),
        ("tilt_shift", "移軸鏡頭", "tilt-shift lens view"),
        ("long_exposure", "長曝光", "long-exposure camera effect"),
        ("handheld", "手持紀實", "handheld documentary framing"),
    ),
    "story_details": _extra_options(
        ("half_eaten_meal", "吃到一半的餐點", "a half-eaten meal left behind"),
        ("fresh_flowers", "新鮮花束", "freshly arranged flowers"),
        ("old_photographs", "老照片", "scattered old photographs"),
        ("sealed_letter", "封蠟信件", "an unopened sealed letter"),
        ("childhood_toy", "陳舊玩具", "an old abandoned toy"),
        ("clock_stopped", "停擺時鐘", "a clock stopped at a meaningful hour"),
        ("memorial", "紀念碑", "a weathered memorial"),
        ("wanted_posters", "懸賞海報", "torn wanted posters"),
        ("caravan", "商隊物資", "unloaded caravan supplies"),
        ("medical_supplies", "醫療物資", "hastily used medical supplies"),
        ("construction", "施工痕跡", "active construction materials"),
        ("secret_door", "暗門", "a barely visible secret door"),
        ("coded_message", "密碼訊息", "a hidden coded message"),
        ("animal_tracks", "動物足跡", "animal tracks crossing the scene"),
        ("forgotten_instrument", "遺落樂器", "a forgotten musical instrument"),
    ),
    "history_and_decay": _extra_options(
        ("newly_built", "新建", "newly built pristine construction"),
        ("dusty", "積滿灰塵", "thick layers of dust"),
        ("rusted", "鏽蝕", "heavily rusted metal"),
        ("collapsed", "局部倒塌", "partially collapsed structures"),
        ("frozen_over", "冰封", "surfaces frozen over"),
        ("sand_buried", "沙埋", "partially buried by sand"),
        ("mossy", "苔蘚覆蓋", "moss-covered surfaces"),
        ("restored", "修復翻新", "carefully restored architecture"),
    ),
    "supernatural_details": _extra_options(
        ("ghosts", "幽靈", "faint wandering ghosts"),
        ("giant_moon", "巨大月亮", "impossibly large moon"),
        ("double_suns", "雙日", "two suns in the sky"),
        ("inverted_waterfall", "逆流瀑布", "waterfall flowing upward"),
        ("endless_stairs", "無盡階梯", "impossible endless stairways"),
        ("mirror_dimension", "鏡像世界裂縫", "fractures into a mirror dimension"),
        ("frozen_time", "時間靜止", "objects suspended in frozen time"),
        ("living_shadows", "活影", "independent living shadows"),
        ("singing_stones", "鳴響石", "resonating rune stones"),
        ("celestial_whale", "天空巨鯨", "celestial whale in the distance"),
        ("memory_echoes", "記憶殘影", "translucent echoes of past events"),
        ("gravity_well", "重力異常", "visible gravitational distortion"),
    ),
    "population": _extra_options(
        ("paired_silhouettes", "兩個遠方剪影", "two distant silhouettes"),
        ("small_group", "小群人", "small distant group of adults"),
        ("procession", "遊行隊伍", "distant ceremonial procession"),
        ("evacuating", "疏散人潮", "distant evacuating crowd"),
        ("nonhuman_crowd", "奇幻種族人群", "diverse fantasy humanoid crowd"),
    ),
    "background_style": _extra_options(
        ("storybook", "故事書背景", "storybook environment illustration"),
        ("gouache", "廣告顏料", "gouache environment painting"),
        ("oil_painting", "古典油畫", "classical oil-painted landscape"),
        ("woodblock", "木刻版畫", "traditional woodblock-print landscape"),
        ("retro_anime", "復古動畫背景", "retro anime background art"),
        ("low_poly", "低多邊形", "stylized low-poly environment"),
        ("miniature", "微縮模型", "handcrafted miniature environment"),
        ("architectural_sketch", "建築速寫", "detailed architectural sketch"),
    ),
    "rendering": _extra_options(
        ("ink_rendering", "墨線渲染", "expressive ink rendering"),
        ("watercolor_rendering", "水彩渲染", "layered watercolor rendering"),
        ("voxel", "體素渲染", "crisp voxel rendering"),
        ("miniature_render", "模型攝影感", "miniature model rendering"),
        ("blueprint", "藍圖渲染", "technical blueprint rendering"),
        ("cutaway", "剖面渲染", "architectural cutaway rendering"),
    ),
    "background_detail": _extra_options(
        ("minimal", "極簡", "minimal environmental detail"),
        ("production_ready", "製作用高完成度", "production-ready environment detail"),
        ("micro_texture", "微觀材質", "fine environmental micro-textures"),
        ("encyclopedic", "百科圖鑑級", "encyclopedic environmental detail"),
    ),
}


_BACKGROUND_OPTION_EXTENSIONS_V2: Final[dict[str, tuple[TagOption, ...]]] = {
    "environment_materials": _extra_options(
        ("jade", "玉石", "carved jade surfaces"),
        ("obsidian", "黑曜石", "polished obsidian structures"),
        ("mother_of_pearl", "珍珠母", "mother-of-pearl inlays"),
        ("sandstone", "砂岩", "layered sandstone surfaces"),
        ("volcanic_glass", "火山玻璃", "dark volcanic-glass surfaces"),
        ("glowing_resin", "發光樹脂", "translucent glowing resin"),
        ("porcelain", "瓷器", "glazed porcelain architecture"),
        ("woven_reeds", "編織蘆葦", "woven reed structures"),
    ),
    "atmosphere": _extra_options(
        ("liminal", "閾限感", "liminal atmosphere"),
        ("somber", "沉鬱", "somber atmosphere"),
        ("buoyant", "輕快", "buoyant atmosphere"),
        ("reverent", "崇敬", "reverent atmosphere"),
        ("intimate", "親密", "intimate atmosphere"),
        ("majestic", "壯麗", "majestic atmosphere"),
        ("hushed", "萬籟俱寂", "hushed atmosphere"),
        ("feverish", "躁動迷離", "feverish atmosphere"),
    ),
    "background_lighting": _extra_options(
        ("lava_glow", "熔岩光", "lava-glow illumination"),
        ("lightning_flash", "閃電瞬光", "lightning-flash illumination"),
        ("phosphorescent", "磷光", "phosphorescent illumination"),
        ("water_reflection", "水面反射光", "rippling water-reflection light"),
        ("projector", "投影機光", "projector-beam lighting"),
        ("stained_glass", "彩繪玻璃光", "colored stained-glass light"),
        ("searchlights", "探照燈", "sweeping searchlight beams"),
        ("dawn_rim", "破曉輪廓光", "dawn rim illumination"),
    ),
    "color_palette": _extra_options(
        ("coral_teal", "珊瑚橘與藍綠", "coral-and-teal color palette"),
        ("jade_gold", "玉綠與金", "jade-and-gold color palette"),
        ("violet_silver", "紫羅蘭與銀", "violet-and-silver color palette"),
        ("ochre_cyan", "赭黃與青", "ochre-and-cyan color palette"),
        ("burgundy_cream", "酒紅與奶油", "burgundy-and-cream color palette"),
        ("acid_magenta", "酸綠與洋紅", "acid-green-and-magenta color palette"),
        ("moonlit_blue", "月光藍", "moonlit-blue color palette"),
        ("ember_black", "餘燼橙與黑", "ember-orange-and-black color palette"),
    ),
    "composition": _extra_options(
        ("spiral", "螺旋構圖", "spiral composition"),
        ("tunnel_frame", "隧道框景", "tunnel-framed composition"),
        ("asymmetric_balance", "不對稱平衡", "asymmetrically balanced composition"),
        ("horizon_split", "地平線分割", "horizon-split composition"),
        ("deep_focus_layers", "深焦層次", "deep-focus layered composition"),
        ("silhouette_frame", "剪影框景", "silhouette-framed composition"),
    ),
    "camera": _extra_options(
        ("dolly_zoom", "滑動變焦", "dolly-zoom environmental view"),
        ("worm_eye", "蟲視角", "worm's-eye environmental view"),
        ("shoulder_view", "越肩環境視角", "over-the-shoulder environmental view"),
        ("wide_24mm", "24mm 廣角", "24mm wide-angle lens view"),
        ("portrait_85mm", "85mm 壓縮景深", "85mm compressed-perspective view"),
        ("anamorphic", "變形寬銀幕", "anamorphic widescreen lens view"),
        ("infrared", "紅外線鏡頭", "infrared environmental camera view"),
        ("surveillance", "監視器視角", "fixed surveillance-camera view"),
    ),
    "story_details": _extra_options(
        ("broken_crown", "破損王冠", "a broken crown left behind"),
        ("cold_campfire", "熄滅營火", "a recently extinguished campfire"),
        ("spilled_ink", "潑灑墨水", "freshly spilled ink"),
        ("wet_footprints", "潮濕腳印", "fresh wet footprints"),
        ("open_portal_trace", "傳送門殘痕", "fading traces of an opened portal"),
        ("abandoned_luggage", "遺棄行李", "abandoned travel luggage"),
        ("unfinished_portrait", "未完成肖像", "an unfinished portrait"),
        ("burning_letter", "燃燒信件", "a letter burning at the edges"),
        ("shattered_hourglass", "破碎沙漏", "a shattered hourglass"),
        ("freshly_dug_soil", "新翻土壤", "freshly disturbed soil"),
        ("empty_cage", "空鳥籠", "an open empty cage"),
        ("ceremonial_mask", "遺落儀式面具", "a discarded ceremonial mask"),
    ),
    "history_and_decay": _extra_options(
        ("salt_eroded", "鹽蝕", "salt-eroded surfaces"),
        ("soot_stained", "煙燻染黑", "soot-stained structures"),
        ("petrified", "石化", "petrified organic structures"),
        ("vine_reclaimed", "藤蔓重新占據", "structures reclaimed by thick vines"),
        ("excavated", "考古發掘中", "partially excavated ruins"),
        ("storm_damaged", "風暴損毀", "storm-damaged structures"),
        ("unfinished", "半途停工", "abandoned unfinished construction"),
        ("ritual_stained", "儀式染痕", "old ritual stains across surfaces"),
    ),
    "supernatural_details": _extra_options(
        ("floating_lanterns", "漂浮燈籠", "self-guided floating lanterns"),
        ("sky_eye", "天空巨眼", "a colossal eye opening in the sky"),
        ("spectral_train", "幽靈列車", "a distant spectral train"),
        ("whispering_mist", "低語霧氣", "mist forming whispered words"),
        ("time_loop", "時間迴圈", "visible repetitions from a time loop"),
        ("door_to_stars", "星海之門", "an open doorway into deep space"),
        ("walking_statues", "行走雕像", "distant walking statues"),
        ("moon_fragments", "月亮碎片", "floating fragments of a broken moon"),
        ("sentient_rain", "有意識的雨", "rain moving with deliberate intent"),
        ("impossible_shadow", "不可能的影子", "a shadow cast by no object"),
    ),
    "population": _extra_options(
        ("solo_traveler", "單一成年旅人", "one distant adult traveler"),
        ("merchant_caravan", "成年商隊", "distant caravan of adult merchants"),
        ("city_guards", "城鎮守衛", "small patrol of adult guards"),
        ("pilgrims", "朝聖者", "distant group of adult pilgrims"),
        ("masked_revelers", "蒙面慶典人群", "distant masked adult revelers"),
        ("scholars", "研究者群體", "small group of adult scholars"),
    ),
    "background_style": _extra_options(
        ("pastel_chalk", "粉彩畫", "pastel-chalk environment illustration"),
        ("dark_academia", "黑暗學院風", "dark-academia environment art"),
        ("synthwave", "合成波風", "synthwave environment art"),
        ("papercut", "剪紙風", "layered papercut environment art"),
        ("linocut", "油氈版畫", "linocut environment illustration"),
        ("ukiyo_e", "浮世繪風", "ukiyo-e environment print"),
        ("surreal_collage", "超現實拼貼", "surreal collage environment art"),
        ("scientific_plate", "科學圖版", "scientific-plate environment illustration"),
    ),
    "rendering": _extra_options(
        ("pastel_rendering", "粉彩渲染", "layered pastel rendering"),
        ("cel_rendering", "賽璐璐渲染", "clean cel-shaded environment rendering"),
        ("paper_cut_rendering", "剪紙渲染", "layered paper-cut rendering"),
        ("linocut_rendering", "版畫渲染", "high-contrast linocut rendering"),
        ("collage_rendering", "拼貼渲染", "mixed-media collage rendering"),
        ("scientific_rendering", "科學圖版渲染", "precise scientific-plate rendering"),
        ("ray_traced", "光線追蹤", "ray-traced environment rendering"),
        ("hand_painted_3d", "手繪 3D 材質", "hand-painted 3D environment rendering"),
    ),
    "background_detail": _extra_options(
        ("atmospheric", "氛圍優先", "atmosphere-focused environmental detail"),
        ("readable_shapes", "清晰造型", "clean readable environmental shapes"),
        ("dense_narrative", "密集敘事細節", "dense narrative environmental detail"),
        ("material_study", "材質研究級", "material-study environmental detail"),
        ("architectural_precision", "精密建築細節", "architecturally precise detail"),
        ("cinematic_density", "電影級密度", "cinematic environmental detail density"),
    ),
}


_NEGATIVE_OPTION_EXTENSIONS: Final[dict[str, tuple[TagOption, ...]]] = {
    "negative_quality": _extra_options(
        ("overexposed", "過曝", "overexposed"),
        ("underexposed", "曝光不足", "underexposed"),
        ("banding", "色階斷層", "color banding"),
        ("posterization", "色彩海報化", "posterization"),
        ("chromatic_aberration", "色差", "unwanted chromatic aberration"),
        ("jpeg_artifacts", "JPEG 瑕疵", "JPEG artifacts"),
    ),
    "negative_anatomy": _extra_options(
        ("fused_fingers", "手指黏連", "fused fingers"),
        ("twisted_limbs", "肢體扭曲", "twisted limbs"),
        ("dislocated_joints", "關節脫位", "dislocated joints"),
        ("asymmetrical_eyes", "眼睛不對稱", "unintentionally asymmetrical eyes"),
        ("duplicate_face", "重複臉孔", "duplicated face"),
        ("floating_limbs", "肢體分離", "detached floating limbs"),
        ("bad_teeth", "牙齒錯誤", "malformed teeth"),
        ("broken_neck", "頸部結構錯誤", "anatomically impossible neck"),
    ),
    "negative_composition": _extra_options(
        ("tangent_lines", "錯誤相切線", "awkward tangent lines"),
        ("centered_accidentally", "意外正中央", "accidentally centered composition"),
        ("subject_too_small", "主體過小", "subject too small"),
        ("subject_too_large", "主體過大", "subject too large"),
        ("dead_space", "無意義空白", "unintentional dead space"),
        ("conflicting_focal_points", "焦點衝突", "conflicting focal points"),
    ),
    "negative_artifacts": _extra_options(
        ("date_stamp", "日期戳記", "date stamp"),
        ("speech_bubble", "對話框", "speech bubbles"),
        ("prompt_text", "提示詞文字", "visible prompt text"),
        ("censor_bar", "遮蔽條", "censor bars"),
        ("underage", "排除未成年外觀", "underage appearance"),
        ("nonconsensual", "排除非合意情境", "non-consensual context"),
        ("incest", "排除亂倫", "incest"),
        ("bestiality", "排除人獸性行為", "bestiality"),
    ),
    "negative_environment": _extra_options(
        ("impossible_stairs", "錯誤階梯", "impossible stair geometry"),
        ("broken_scale", "比例失衡", "inconsistent environmental scale"),
        ("bad_reflections", "錯誤反射", "incorrect reflections"),
        ("bad_shadows", "錯誤陰影", "inconsistent shadows"),
        ("texture_seams", "材質接縫", "visible texture seams"),
        ("popping_objects", "突兀物件", "contextless out-of-place props"),
        ("melted_buildings", "建築融化", "melted building geometry"),
        ("inconsistent_weather", "天候矛盾", "contradictory weather effects"),
    ),
}


_NEGATIVE_OPTION_EXTENSIONS_V2: Final[dict[str, tuple[TagOption, ...]]] = {
    "negative_quality": _extra_options(
        ("muddy_colors", "色彩混濁", "muddy colors"),
        ("aliasing", "鋸齒邊緣", "aliased edges"),
        ("color_noise", "彩色雜訊", "color noise"),
        ("washed_out", "色彩褪白", "washed-out colors"),
        ("crushed_blacks", "暗部死黑", "crushed black levels"),
        ("halo_sharpening", "銳化光暈", "sharpening halos"),
    ),
    "negative_anatomy": _extra_options(
        ("uneven_pupils", "瞳孔大小不一", "unintentionally uneven pupils"),
        ("misplaced_ears", "耳朵位置錯誤", "misplaced ears"),
        ("duplicated_torso", "重複軀幹", "duplicated torso"),
        ("broken_spine", "脊椎扭曲", "impossible spinal curvature"),
        ("merged_limbs", "肢體融合", "merged limbs"),
        ("inconsistent_hands", "雙手結構不一致", "inconsistent hand anatomy"),
        ("malformed_feet", "腳部畸形", "malformed feet"),
        ("floating_facial_parts", "五官漂移", "misplaced facial features"),
    ),
    "negative_composition": _extra_options(
        ("head_cutoff", "頭頂被切斷", "accidentally cut-off head"),
        ("limb_cutoff", "肢體被切斷", "accidentally cut-off limbs"),
        ("slanted_horizon", "地平線歪斜", "unintentionally slanted horizon"),
        ("foreground_block", "前景遮擋主體", "foreground blocking the subject"),
        ("no_focal_point", "缺乏視覺焦點", "missing visual focal point"),
        ("repetitive_layout", "構圖重複", "repetitive composition layout"),
    ),
    "negative_artifacts": _extra_options(
        ("qr_code", "QR Code／二維碼", "QR code"),
        ("mosaic_censor", "馬賽克遮蔽", "mosaic censoring"),
        ("editing_handles", "編輯控制點", "visible editing handles"),
        ("color_profile_error", "色彩設定錯誤", "color-profile artifacts"),
        ("browser_chrome", "瀏覽器介面", "browser interface elements"),
        ("thumbnail_border", "縮圖邊框", "thumbnail border"),
    ),
    "negative_environment": _extra_options(
        ("floating_doors", "門窗漂浮", "unsupported floating doors"),
        ("broken_vanishing_points", "消失點錯誤", "conflicting vanishing points"),
        ("repeated_buildings", "建築重複", "duplicated building patterns"),
        ("weather_indoors", "室內錯誤天候", "outdoor weather inside sealed rooms"),
        ("disconnected_roads", "道路斷裂", "roads connecting to nowhere"),
        ("impossible_waterline", "水面線錯誤", "physically impossible waterline"),
        ("scale_drift", "遠近比例漂移", "scale drift across the environment"),
        ("inconsistent_light_sources", "光源矛盾", "contradictory environmental light sources"),
    ),
}


_BEAST_HUMANOID_OPTIONS: Final[tuple[TagOption, ...]] = (
    _beast_humanoid_option("dog", "犬族半獸人", "canine ears and a canine tail"),
    _beast_humanoid_option("shiba_inu", "柴犬半獸人", "Shiba Inu ears and a curled canine tail"),
    _beast_humanoid_option("husky", "哈士奇半獸人", "husky ears and a thick curled canine tail"),
    _beast_humanoid_option(
        "german_shepherd", "德國牧羊犬半獸人", "German shepherd ears and a long canine tail"
    ),
    _beast_humanoid_option("doberman", "杜賓犬半獸人", "Doberman ears and a slender canine tail"),
    _beast_humanoid_option("corgi", "柯基犬半獸人", "corgi ears and a short canine tail"),
    _beast_humanoid_option("domestic_cat", "貓咪", "cat ears and a flexible feline tail"),
    _beast_humanoid_option("lion", "獅族半獸人", "rounded lion ears and a tufted lion tail"),
    _beast_humanoid_option("tiger", "虎族半獸人", "tiger ears and a striped feline tail"),
    _beast_humanoid_option("leopard", "豹族半獸人", "leopard ears and a spotted feline tail"),
    _beast_humanoid_option(
        "snow_leopard", "雪豹半獸人", "snow-leopard ears and a thick feline tail"
    ),
    _beast_humanoid_option("lynx", "猞猁半獸人", "tufted lynx ears and a short feline tail"),
    _beast_humanoid_option("black_panther", "黑豹半獸人", "panther ears and a sleek feline tail"),
    _beast_humanoid_option("serval", "藪貓半獸人", "serval ears and a long spotted feline tail"),
    _beast_humanoid_option("red_fox", "赤狐半獸人", "red-fox ears and a full fox tail"),
    _beast_humanoid_option("arctic_fox", "北極狐半獸人", "arctic-fox ears and a plush fox tail"),
    _beast_humanoid_option(
        "fennec_fox", "耳廓狐半獸人", "large fennec-fox ears and a slim fox tail"
    ),
    _beast_humanoid_option("gray_wolf", "灰狼半獸人", "gray-wolf ears and a full lupine tail"),
    _beast_humanoid_option(
        "arctic_wolf", "北極狼半獸人", "arctic-wolf ears and a thick lupine tail"
    ),
    _beast_humanoid_option("rabbit", "兔族半獸人", "long rabbit ears and a round rabbit tail"),
    _beast_humanoid_option("hare", "野兔半獸人", "tall hare ears and a short hare tail"),
    _beast_humanoid_option(
        "arctic_hare",
        "北極兔半獸人",
        "arctic-hare ears and a compact white hare tail",
    ),
    _beast_humanoid_option("brown_bear", "棕熊半獸人", "rounded bear ears and a small bear tail"),
    _beast_humanoid_option(
        "polar_bear", "北極熊半獸人", "rounded polar-bear ears and a small bear tail"
    ),
    _beast_humanoid_option("panda", "熊貓半獸人", "black panda ears and a small panda tail"),
    _beast_humanoid_option("red_deer", "赤鹿半獸人", "deer ears and a short deer tail"),
    _beast_humanoid_option("reindeer", "馴鹿半獸人", "reindeer ears and branching antlers"),
    _beast_humanoid_option("moose", "駝鹿半獸人", "broad moose ears and palmate antlers"),
    _beast_humanoid_option("goat", "山羊半獸人", "goat ears and curled goat horns"),
    _beast_humanoid_option("sheep", "綿羊半獸人", "sheep ears and soft wool accents"),
    _beast_humanoid_option("ram", "公羊半獸人", "ram ears and sweeping spiral horns"),
    _beast_humanoid_option("cow", "乳牛半獸人", "bovine ears and a slender bovine tail"),
    _beast_humanoid_option("bull", "公牛半獸人", "bovine ears and broad bull horns"),
    _beast_humanoid_option("buffalo", "水牛半獸人", "buffalo ears and crescent buffalo horns"),
    _beast_humanoid_option("horse", "馬族半獸人", "horse ears and a flowing horse tail"),
    _beast_humanoid_option("zebra", "斑馬半獸人", "zebra ears and a striped equine tail"),
    _beast_humanoid_option("mouse", "小鼠半獸人", "round mouse ears and a slender mouse tail"),
    _beast_humanoid_option("rat", "大鼠半獸人", "rat ears and a long rat tail"),
    _beast_humanoid_option("hamster", "倉鼠半獸人", "small hamster ears and soft cheek accents"),
    _beast_humanoid_option(
        "squirrel", "松鼠半獸人", "squirrel ears and a large fluffy squirrel tail"
    ),
    _beast_humanoid_option("eagle", "鷹族半獸人", "eagle feather ears and decorative wing accents"),
    _beast_humanoid_option(
        "golden_eagle",
        "金雕半獸人",
        "golden-eagle feather tufts and decorative wing accents",
    ),
    _beast_humanoid_option(
        "owl", "貓頭鷹半獸人", "owl feather ear tufts and decorative wing accents"
    ),
    _beast_humanoid_option("raven", "渡鴉半獸人", "raven feather ear tufts and black wing accents"),
    _beast_humanoid_option(
        "parrot", "鸚鵡半獸人", "colorful feather ear tufts and decorative wing accents"
    ),
    _beast_humanoid_option(
        "swan", "天鵝半獸人", "white feather ear tufts and elegant wing accents"
    ),
    _beast_humanoid_option(
        "lizard", "蜥蜴半獸人", "subtle lizard scales and a tapered reptilian tail"
    ),
    _beast_humanoid_option("gecko", "守宮半獸人", "soft gecko scales and a thick gecko tail"),
    _beast_humanoid_option("snake", "蛇族半獸人", "subtle snake scales and a slender snake tail"),
    _beast_humanoid_option(
        "crocodile", "鱷魚半獸人", "crocodilian scale accents and a heavy reptilian tail"
    ),
    _beast_humanoid_option(
        "turtle", "龜族半獸人", "turtle-shell markings and a small reptilian tail"
    ),
    _beast_humanoid_option(
        "shark", "鯊族半獸人", "shark fin head accents and a powerful shark tail"
    ),
    _beast_humanoid_option(
        "dolphin", "海豚半獸人", "dolphin fin head accents and a streamlined aquatic tail"
    ),
    _beast_humanoid_option(
        "orca", "虎鯨半獸人", "orca fin head accents and a black-and-white aquatic tail"
    ),
    _beast_humanoid_option("koi", "錦鯉半獸人", "koi fin head accents and a flowing koi tail"),
    _beast_humanoid_option(
        "axolotl", "六角恐龍半獸人", "axolotl head frills and a soft aquatic tail"
    ),
    _beast_humanoid_option(
        "butterfly", "蝴蝶半獸人", "butterfly antennae and decorative butterfly wings"
    ),
    _beast_humanoid_option("moth", "飛蛾半獸人", "moth antennae and decorative moth wings"),
    _beast_humanoid_option("bee", "蜜蜂半獸人", "bee antennae and decorative translucent wings"),
    _beast_humanoid_option(
        "beetle", "甲蟲半獸人", "beetle antennae and decorative shell-wing accents"
    ),
    _beast_humanoid_option(
        "dragonfly", "蜻蜓半獸人", "dragonfly antennae and decorative transparent wings"
    ),
    _beast_humanoid_option(
        "mantis", "螳螂半獸人", "mantis antennae and subtle mantis forearm accents"
    ),
    _beast_humanoid_option("jackal", "豺", ""),
    _beast_humanoid_option("hyena", "鬣狗", ""),
    _beast_humanoid_option("cheetah", "獵豹", ""),
    _beast_humanoid_option("cougar", "美洲獅", ""),
    _beast_humanoid_option("red_panda", "小熊貓", ""),
    _beast_humanoid_option("elk", "麋鹿", ""),
    _beast_humanoid_option("donkey", "驢", ""),
    _beast_humanoid_option("hedgehog", "刺蝟", ""),
    _beast_humanoid_option("kangaroo", "袋鼠", ""),
    _beast_humanoid_option("sloth", "樹懶", ""),
    _beast_humanoid_option("gorilla", "大猩猩", ""),
    _beast_humanoid_option("monkey", "猴族", ""),
    _beast_humanoid_option("capybara", "水豚", ""),
    _beast_humanoid_option("beaver", "河狸", ""),
    _beast_humanoid_option("badger", "獾", ""),
    _beast_humanoid_option("ferret", "雪貂", ""),
    _beast_humanoid_option("meerkat", "狐獴", ""),
    _beast_humanoid_option("wolverine", "狼獾", ""),
    _beast_humanoid_option("koala", "無尾熊", ""),
    _beast_humanoid_option("wombat", "袋熊", ""),
    _beast_humanoid_option("opossum", "負鼠", ""),
    _beast_humanoid_option("armadillo", "犰狳", ""),
    _beast_humanoid_option("wild_boar", "野豬", ""),
    _beast_humanoid_option("pig", "家豬", ""),
    _beast_humanoid_option("camel", "駱駝", ""),
    _beast_humanoid_option("alpaca", "羊駝", ""),
    _beast_humanoid_option("llama", "美洲駝", ""),
    _beast_humanoid_option("yak", "牦牛", ""),
    _beast_humanoid_option("bison", "野牛", ""),
    _beast_humanoid_option("antelope", "羚羊", ""),
    _beast_humanoid_option("gazelle", "瞪羚", ""),
    _beast_humanoid_option("ibex", "北山羊", ""),
    _beast_humanoid_option("chinchilla", "龍貓", ""),
    _beast_humanoid_option("guinea_pig", "天竺鼠", ""),
    _beast_humanoid_option("lemur", "狐猴", ""),
    _beast_humanoid_option("elephant", "大象", ""),
    _beast_humanoid_option("rhinoceros", "犀牛", ""),
    _beast_humanoid_option("hippopotamus", "河馬", ""),
    _beast_humanoid_option("seal", "海豹", ""),
    _beast_humanoid_option("sea_lion", "海獅", ""),
    _beast_humanoid_option("manatee", "海牛", ""),
    _option(
        "other",
        "其他／自訂",
        _beast_lineage_prompt("custom"),
    ),
)


_FURRY_SPECIES_OPTIONS: Final[tuple[TagOption, ...]] = (
    _furry_species_option("dog", "犬科福瑞", "canine"),
    _furry_species_option("shiba_inu", "柴犬福瑞", "Shiba Inu"),
    _furry_species_option("husky", "哈士奇福瑞", "husky"),
    _furry_species_option("german_shepherd", "德國牧羊犬福瑞", "German shepherd"),
    _furry_species_option("doberman", "杜賓犬福瑞", "Doberman"),
    _furry_species_option("corgi", "柯基犬福瑞", "corgi"),
    _furry_species_option("red_fox", "赤狐福瑞", "red fox"),
    _furry_species_option("arctic_fox", "北極狐福瑞", "arctic fox"),
    _furry_species_option("fennec_fox", "耳廓狐福瑞", "fennec fox"),
    _furry_species_option("gray_wolf", "灰狼福瑞", "gray wolf"),
    _furry_species_option("arctic_wolf", "北極狼福瑞", "arctic wolf"),
    _furry_species_option("coyote", "郊狼福瑞", "coyote"),
    _furry_species_option("domestic_cat", "貓咪", "cat"),
    _furry_species_option("lion", "獅族福瑞", "lion"),
    _furry_species_option("tiger", "虎族福瑞", "tiger"),
    _furry_species_option("leopard", "豹族福瑞", "leopard"),
    _furry_species_option("snow_leopard", "雪豹福瑞", "snow leopard"),
    _furry_species_option("lynx", "猞猁福瑞", "lynx"),
    _furry_species_option("rabbit", "兔族福瑞", "rabbit"),
    _furry_species_option("brown_bear", "棕熊福瑞", "brown bear"),
    _furry_species_option("polar_bear", "北極熊福瑞", "polar bear"),
    _furry_species_option("panda", "熊貓福瑞", "giant panda"),
    _furry_species_option("red_deer", "赤鹿福瑞", "red deer"),
    _furry_species_option("reindeer", "馴鹿福瑞", "reindeer"),
    _furry_species_option("goat", "山羊福瑞", "goat"),
    _furry_species_option("sheep", "綿羊福瑞", "sheep"),
    _furry_species_option("cow", "牛族福瑞", "bovine"),
    _furry_species_option("horse", "馬族福瑞", "horse"),
    _furry_species_option("zebra", "斑馬福瑞", "zebra"),
    _furry_species_option("mouse", "小鼠福瑞", "mouse"),
    _furry_species_option("rat", "大鼠福瑞", "rat"),
    _furry_species_option("squirrel", "松鼠福瑞", "squirrel"),
    _furry_species_option("otter", "水獺福瑞", "otter"),
    _furry_species_option("raccoon", "浣熊福瑞", "raccoon"),
    _furry_species_option("skunk", "臭鼬福瑞", "skunk"),
    _furry_species_option("bat", "蝙蝠福瑞", "bat"),
    _furry_species_option("eagle", "鷹族福瑞", "eagle"),
    _furry_species_option("owl", "貓頭鷹福瑞", "owl"),
    _furry_species_option("raven", "渡鴉福瑞", "raven"),
    _furry_species_option("parrot", "鸚鵡福瑞", "parrot"),
    _furry_species_option("lizard", "蜥蜴福瑞", "lizard"),
    _furry_species_option("gecko", "守宮福瑞", "gecko"),
    _furry_species_option("snake", "蛇族福瑞", "snake"),
    _furry_species_option("crocodile", "鱷魚福瑞", "crocodile"),
    _furry_species_option("shark", "鯊族福瑞", "shark"),
    _furry_species_option("dolphin", "海豚福瑞", "dolphin"),
    _furry_species_option("orca", "虎鯨福瑞", "orca"),
    _furry_species_option("axolotl", "六角恐龍福瑞", "axolotl"),
    _furry_species_option("butterfly", "蝴蝶福瑞", "butterfly"),
    _furry_species_option("moth", "飛蛾福瑞", "moth"),
    _furry_species_option("bee", "蜜蜂福瑞", "bee"),
    _furry_species_option("beetle", "甲蟲福瑞", "beetle"),
    _furry_species_option("mantis", "螳螂福瑞", "mantis"),
    _furry_species_option("dragon", "龍族福瑞", "fantasy dragon"),
    _furry_species_option("jackal", "豺", "jackal"),
    _furry_species_option("hyena", "鬣狗", "hyena"),
    _furry_species_option("cheetah", "獵豹", "cheetah"),
    _furry_species_option("cougar", "美洲獅", "cougar"),
    _furry_species_option("red_panda", "小熊貓", "red panda"),
    _furry_species_option("elk", "麋鹿", "elk"),
    _furry_species_option("donkey", "驢", "donkey"),
    _furry_species_option("hedgehog", "刺蝟", "hedgehog"),
    _furry_species_option("kangaroo", "袋鼠", "kangaroo"),
    _furry_species_option("sloth", "樹懶", "sloth"),
    _furry_species_option("gorilla", "大猩猩", "gorilla"),
    _furry_species_option("monkey", "猴族", "monkey"),
    _furry_species_option("capybara", "水豚", "capybara"),
    _furry_species_option("beaver", "河狸", "beaver"),
    _furry_species_option("badger", "獾", "badger"),
    _furry_species_option("ferret", "雪貂", "ferret"),
    _furry_species_option("meerkat", "狐獴", "meerkat"),
    _furry_species_option("wolverine", "狼獾", "wolverine"),
    _furry_species_option("koala", "無尾熊", "koala"),
    _furry_species_option("wombat", "袋熊", "wombat"),
    _furry_species_option("opossum", "負鼠", "opossum"),
    _furry_species_option("armadillo", "犰狳", "armadillo"),
    _furry_species_option("wild_boar", "野豬", "wild boar"),
    _furry_species_option("pig", "家豬", "pig"),
    _furry_species_option("camel", "駱駝", "camel"),
    _furry_species_option("alpaca", "羊駝", "alpaca"),
    _furry_species_option("llama", "美洲駝", "llama"),
    _furry_species_option("yak", "牦牛", "yak"),
    _furry_species_option("bison", "野牛", "bison"),
    _furry_species_option("antelope", "羚羊", "antelope"),
    _furry_species_option("gazelle", "瞪羚", "gazelle"),
    _furry_species_option("ibex", "北山羊", "ibex"),
    _furry_species_option("chinchilla", "龍貓", "chinchilla"),
    _furry_species_option("guinea_pig", "天竺鼠", "guinea pig"),
    _furry_species_option("lemur", "狐猴", "lemur"),
    _furry_species_option("elephant", "大象", "elephant"),
    _furry_species_option("rhinoceros", "犀牛", "rhinoceros"),
    _furry_species_option("hippopotamus", "河馬", "hippopotamus"),
    _furry_species_option("seal", "海豹", "seal"),
    _furry_species_option("sea_lion", "海獅", "sea lion"),
    _furry_species_option("manatee", "海牛", "manatee"),
    _option(
        "other",
        "其他／自訂",
        _furry_lineage_prompt("custom species"),
    ),
)


_ANIMAL_TAXONOMY_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    _optional_single_options(
        "beast_humanoid_species",
        "獸人／半獸人（人型獸徵）",
        "獸人／半獸人",
        _BEAST_HUMANOID_OPTIONS,
        help_text="固定成年、雙足與人型臉身比例；可在下方自由調整融合程度、臉鼻、體表、花紋與手腳。",
    ),
    _optional_single_options(
        "beast_morphology_balance",
        "半獸人融合比例",
        "獸人／半獸人細節",
        _extra_options(
            (
                "near_human",
                "近乎人類",
                "near-human bipedal anatomy with minimal species influence",
            ),
            (
                "human_dominant",
                "人型為主",
                "human-dominant bipedal proportions with restrained half-beast traits",
            ),
            (
                "subtle_integrated",
                "輕度自然融合",
                "subtly integrated species traits on an unmistakably human-shaped bipedal body",
            ),
            (
                "balanced",
                "均衡半獸融合",
                "well-balanced human and species traits on a human-shaped bipedal body",
            ),
            (
                "species_forward",
                "獸徵較明顯",
                "pronounced species traits while preserving a human-shaped bipedal body",
            ),
            (
                "strong_integrated",
                "高度自然融合",
                "strongly integrated species traits with coherent human proportions and silhouette",
            ),
            (
                "seamless_hybrid",
                "自然無縫融合",
                "seamlessly integrated species traits with coherent human body proportions",
            ),
        ),
        help_text="只調整人型與獸徵的比例；不會變成四足動物或完整福瑞。",
    ),
    _optional_single_options(
        "beast_face_blend",
        "半獸人臉部／鼻口影響",
        "獸人／半獸人細節",
        _extra_options(
            (
                "human_face",
                "完整人類臉型",
                "fully human facial proportions with only faint species influence",
            ),
            (
                "subtle_species_nose",
                "輕微種族鼻型",
                "predominantly human face with a small species-appropriate nose",
            ),
            (
                "animal_nose_human_mouth",
                "獸鼻搭配人類嘴型",
                "human facial proportions with a species nose and a human mouth",
            ),
            (
                "short_hybrid_muzzle",
                "短而克制的混合吻部",
                "recognizably human face with a short restrained species-influenced muzzle",
            ),
            (
                "balanced_hybrid",
                "均衡獸鼻與短吻部",
                "well-balanced human face with a short species-influenced nose bridge and muzzle",
            ),
            (
                "pronounced_hybrid",
                "明顯獸鼻但保留人臉",
                "clearly species-influenced nose and short muzzle while retaining a human face",
            ),
            (
                "avian_bridge",
                "輕微鳥系鼻樑",
                "human face with a subtle avian nose-bridge profile and no full beak",
            ),
            (
                "reptilian_bridge",
                "輕微爬蟲鼻吻",
                "human face with a subtle reptilian nose bridge and restrained snout influence",
            ),
            (
                "aquatic_bridge",
                "輕微水生鼻型",
                "human face with a smooth species-appropriate aquatic nose profile",
            ),
            (
                "insect_face_accents",
                "輕微昆蟲顏面特徵",
                "human face with restrained symmetrical insectoid facial accents",
            ),
        ),
        help_text="所有選項都保留可辨識的人類臉部比例；特殊鼻型只會配給相容物種。",
    ),
    _optional_single_options(
        "beast_skin_covering",
        "半獸人體表／局部毛皮",
        "獸人／半獸人細節",
        _extra_options(
            ("human_skin", "人類皮膚", "predominantly smooth human skin"),
            (
                "species_colored_skin",
                "種族色的人型皮膚",
                "smooth human-shaped skin with species-derived coloration",
            ),
            (
                "smooth_patterned_skin",
                "可呈現花紋的光滑皮膚",
                "smooth human-shaped skin prepared for coherent species markings",
            ),
            (
                "short_fur_accents",
                "局部短毛",
                "localized short fur accents over otherwise human-shaped skin",
            ),
            (
                "velvet_fur_layer",
                "極短絨毛皮膚",
                "very short velvety fur following human body contours",
            ),
            (
                "facial_fur_accents",
                "臉頰局部獸毛",
                "restrained facial fur accents preserving human facial structure",
            ),
            (
                "limb_fur_accents",
                "四肢局部獸毛",
                "localized fur along human-shaped forearms and lower legs",
            ),
            (
                "mixed_skin_fur",
                "皮膚與獸毛自然分區",
                "harmonious zones of smooth skin and short fur on human-shaped anatomy",
            ),
            (
                "feather_accents",
                "局部羽毛覆層",
                "localized feather accents over a human-shaped body",
            ),
            (
                "scale_accents",
                "局部鱗片覆層",
                "localized species scales over otherwise smooth human-shaped skin",
            ),
            (
                "aquatic_skin",
                "水生光滑皮膚",
                "smooth aquatic skin following human body contours",
            ),
            (
                "chitin_accents",
                "局部幾丁質甲片",
                "restrained chitin accents over a human-shaped body",
            ),
        ),
        help_text="可選完全人類皮膚、種族色皮膚或局部獸毛／羽毛／鱗片；不會自動變成全身福瑞。",
    ),
    _optional_single_options(
        "beast_marking_pattern",
        "半獸人種族花紋",
        "獸人／半獸人細節",
        _extra_options(
            ("solid", "無明顯花紋", "clean solid species coloration"),
            ("countershading", "背深腹淺", "subtle species countershading"),
            ("facial_mask", "臉部面罩紋", "balanced species facial-mask markings"),
            ("eye_stripes", "眼側條紋", "symmetrical species stripes beside the eyes"),
            ("cheek_stripes", "臉頰條紋", "balanced species stripes across the cheeks"),
            ("limb_bands", "四肢環帶紋", "coherent species band markings around the limbs"),
            ("dorsal_line", "背部中線紋", "single species-colored dorsal line"),
            ("shoulder_stripes", "肩部條紋", "balanced species stripes over the shoulders"),
            ("flank_stripes", "腰側條紋", "harmonious species stripes along the flanks"),
            ("tiger_stripes", "虎紋", "balanced tiger stripe markings on human-shaped skin"),
            ("zebra_stripes", "斑馬紋", "balanced zebra stripe markings on human-shaped skin"),
            ("tabby", "虎斑紋", "restrained tabby markings on human-shaped skin"),
            ("spots", "斑點", "clean species spot markings on human-shaped skin"),
            ("rosettes", "玫瑰斑", "balanced leopard rosettes on human-shaped skin"),
            ("piebald", "花斑", "harmonious piebald patches on human-shaped skin"),
            ("brindle", "虎斑混色", "restrained brindle markings on human-shaped skin"),
            ("point_coloration", "重點色", "species point coloration on the face and extremities"),
            ("saddle_mark", "鞍狀背斑", "balanced saddle-shaped species marking"),
            ("gradient", "自然漸層體色", "smooth species-color gradient across human-shaped skin"),
            (
                "reptile_mottling",
                "爬蟲斑駁紋",
                "restrained reptilian mottling on human-shaped skin",
            ),
            ("diamond_bands", "菱形鱗紋", "symmetrical diamond scale markings"),
            ("orca_patches", "虎鯨黑白斑", "balanced orca-inspired black-and-white skin patches"),
            ("koi_patches", "錦鯉色塊", "harmonious koi-inspired skin patches"),
            ("insect_bands", "昆蟲環帶紋", "symmetrical insect-inspired body bands"),
            ("bioluminescent", "生物光紋", "controlled bioluminescent species markings"),
        ),
        help_text="花紋會遵守物種相容性並保留人型臉身比例。",
    ),
    _optional_single_options(
        "beast_marking_coverage",
        "半獸人花紋覆蓋位置",
        "獸人／半獸人細節",
        _extra_options(
            (
                "face_only",
                "僅臉部",
                "species markings limited to the human-shaped face",
            ),
            (
                "limbs_only",
                "僅四肢",
                "species markings limited to human-shaped arms and legs",
            ),
            (
                "face_and_limbs",
                "臉部與四肢",
                "balanced species markings across the human-shaped face and limbs",
            ),
            (
                "shoulders_back",
                "肩膀與背部",
                "species markings concentrated over human-shaped shoulders and back",
            ),
            (
                "torso_accents",
                "軀幹局部",
                "restrained species markings accenting the human-shaped torso",
            ),
            (
                "extremity_gradient",
                "末端漸增",
                "species markings gradually increasing toward human-shaped extremities",
            ),
            (
                "balanced_full_body",
                "全身均衡分布",
                "well-proportioned species markings distributed across a human-shaped body",
            ),
            (
                "asymmetrical_accent",
                "單側不對稱重點",
                "intentional asymmetrical species markings on a human-shaped body",
            ),
        ),
        help_text="花紋種類與覆蓋位置分開選，方便做出參考圖般的臉部、肩背與四肢比例。",
    ),
    _optional_single_options(
        "beast_ear_style",
        "半獸人耳部型態",
        "獸人／半獸人細節",
        _extra_options(
            (
                "species_default",
                "物種預設耳型",
                "one anatomically consistent species ear configuration replacing human ears "
                "with no duplicate ears",
            ),
            (
                "small",
                "小型獸耳",
                "small animal ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "upright",
                "直立獸耳",
                "upright animal ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "drooping",
                "垂耳",
                "drooping animal ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "rounded",
                "圓耳",
                "rounded animal ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "tufted",
                "簇毛獸耳",
                "tufted animal ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "side_set",
                "側向獸耳",
                "side-set species ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "long",
                "修長獸耳",
                "long species ears replacing human ears with no human ears or duplicate ears",
            ),
            (
                "feather_tufts",
                "羽簇耳部",
                "feathered ear tufts replacing human ears with no human ears or duplicate ears",
            ),
            (
                "finlike",
                "鰭狀耳部",
                "finlike ear structures replacing human ears with no human ears or duplicate ears",
            ),
            (
                "no_external",
                "無外露耳廓",
                "species-appropriate ear openings with no visible human ears or duplicate ears",
            ),
        ),
        help_text="獸耳會取代人耳；爬蟲、昆蟲與部分水生物種改用無外露耳廓。",
    ),
    _optional_single_options(
        "beast_tail_style",
        "半獸人尾部型態",
        "獸人／半獸人細節",
        _extra_options(
            ("none", "無尾", "no visible tail on the human-shaped half-beast body"),
            (
                "species_default",
                "物種預設尾型",
                "one anatomically coherent species-appropriate tail on the half-beast body",
            ),
            ("short", "短尾", "one short species-appropriate half-beast tail"),
            ("curled", "捲尾", "one curled canine-style half-beast tail"),
            ("fluffy", "蓬鬆長尾", "one long fluffy half-beast tail"),
            ("fox_brush", "狐系刷尾", "one full fox-brush half-beast tail"),
            ("feline", "貓科長尾", "one flexible feline half-beast tail"),
            ("canine", "犬科尾", "one expressive canine half-beast tail"),
            ("lupine", "狼系粗尾", "one thick lupine half-beast tail"),
            ("lapine", "兔球尾", "one round lapine half-beast tail"),
            ("ursine", "熊系短尾", "one small ursine half-beast tail"),
            ("cervine", "鹿系短尾", "one short cervine half-beast tail"),
            ("bovine", "牛系長尾", "one slender bovine half-beast tail"),
            ("equine", "馬科流尾", "one flowing equine half-beast tail"),
            ("rodent", "齧齒類長尾", "one slender rodent half-beast tail"),
            ("squirrel", "松鼠蓬尾", "one large fluffy squirrel half-beast tail"),
            ("avian_plume", "鳥類尾羽", "one coherent fan of avian half-beast tail feathers"),
            ("reptilian", "爬蟲長尾", "one tapered reptilian half-beast tail"),
            ("aquatic", "水生尾", "one streamlined aquatic half-beast tail"),
            ("insect_abdomen", "昆蟲腹尾", "one streamlined insectoid half-beast abdomen tail"),
        ),
        help_text="物種只決定血統，尾巴在此獨立選擇；無尾永遠可用，其餘尾型依物種限制。",
    ),
    _optional_single_options(
        "beast_hand_style",
        "半獸人手部型態",
        "獸人／半獸人細節",
        _extra_options(
            ("human_hands", "完整人型手", "fully human five-fingered hands with human palms"),
            (
                "human_paw_pads",
                "人型手搭配肉球",
                "humanlike five-fingered hands with soft species-appropriate paw pads",
            ),
            (
                "articulated_paw_hands",
                "可持物的獸掌手",
                "articulated paw-shaped hands with five tool-using humanoid fingers and paw pads",
            ),
            (
                "short_claws",
                "人型手搭配短爪",
                "humanlike hands with short controlled species claws",
            ),
            (
                "retractable_claws",
                "人型手搭配伸縮爪",
                "humanlike hands with controlled retractable feline claws",
            ),
            (
                "hooflike_nails",
                "人型手搭配蹄質指甲",
                "humanlike hands with refined species-appropriate hooflike nails",
            ),
            (
                "feathered_hands",
                "人型手搭配羽毛",
                "humanlike hands with restrained feather accents across the hands and forearms",
            ),
            (
                "taloned_hands",
                "可持物的鳥爪手",
                "tool-using humanlike hands with controlled avian talons",
            ),
            (
                "scaled_hands",
                "人型鱗手",
                "humanlike five-fingered hands with localized species scales",
            ),
            (
                "scaled_claws",
                "人型鱗手搭配短爪",
                "humanlike scaled hands with short controlled claws",
            ),
            (
                "webbed_hands",
                "人型蹼手",
                "humanlike five-fingered hands with subtle webbing",
            ),
            (
                "chitin_hands",
                "人型甲殼手",
                "humanlike tool-using hands with restrained chitin plates",
            ),
            (
                "insect_claws",
                "可持物的昆蟲爪手",
                "articulated tool-using humanoid hands with delicate insect claws",
            ),
            (
                "species_hands",
                "物種原生手部",
                "species-accurate humanoid tool-using hands",
            ),
        ),
        help_text="完整人型手永遠可選；獸掌、肉球、爪、蹼或甲殼手只會出現在相容物種。",
    ),
    _optional_single_options(
        "beast_foot_style",
        "半獸人腳部型態",
        "獸人／半獸人細節",
        _extra_options(
            ("human_feet", "完整人型腳", "fully human plantigrade feet"),
            (
                "paw_pad_feet",
                "人型腳底搭配肉球",
                "human-shaped plantigrade feet with soft species-appropriate paw pads",
            ),
            (
                "plantigrade_paws",
                "蹠行獸掌腳",
                "bipedal plantigrade paw feet with coherent humanoid proportions",
            ),
            (
                "digitigrade_paws",
                "趾行獸掌腳",
                "bipedal digitigrade paw feet with balanced human body proportions",
            ),
            (
                "clawed_human_feet",
                "人型腳搭配短爪",
                "human-shaped feet with short controlled species claws",
            ),
            (
                "split_hooves",
                "雙趾蹄足",
                "bipedal split hooves with coherent human leg proportions",
            ),
            (
                "equine_hooves",
                "馬科單蹄足",
                "bipedal equine hooves with coherent human leg proportions",
            ),
            (
                "taloned_feet",
                "鳥爪足",
                "bipedal avian taloned feet with coherent human leg proportions",
            ),
            (
                "webbed_feet",
                "人型蹼足",
                "human-shaped bipedal feet with subtle aquatic webbing",
            ),
            (
                "scaled_clawed_feet",
                "鱗片爪足",
                "bipedal scaled feet with short controlled claws",
            ),
            (
                "gecko_toe_pads",
                "守宮吸附趾墊",
                "human-shaped bipedal feet with species-inspired adhesive toe pads",
            ),
            (
                "chitin_feet",
                "人型甲殼足",
                "human-shaped bipedal feet with restrained chitin plates",
            ),
            (
                "insect_tarsi",
                "昆蟲附節足",
                "bipedal insect-inspired tarsal feet with coherent human leg proportions",
            ),
            (
                "species_feet",
                "物種原生腳部",
                "species-accurate bipedal feet with coherent human body proportions",
            ),
        ),
        help_text="完整人型腳永遠可選；肉球、趾行、蹄、鳥爪、蹼與甲殼足會依物種限制。",
    ),
    _optional_single_options(
        "furry_species",
        "福瑞物種（完整擬人）",
        "福瑞",
        _FURRY_SPECIES_OPTIONS,
        help_text="固定為成年、有智慧、雙足站立且具人型比例的完整擬人角色。",
    ),
    _optional_single_options(
        "furry_body_covering",
        "福瑞體表／毛皮",
        "福瑞細節",
        _extra_options(
            (
                "species_default",
                "物種原生體表",
                "species-accurate coat texture and surface anatomy",
            ),
            ("short_fur", "短毛", "short well-groomed fur"),
            ("medium_fur", "中長毛", "medium-length layered fur"),
            ("long_fur", "長毛", "long flowing fur"),
            ("plush_fur", "蓬鬆絨毛", "dense plush fur"),
            ("curly_wool", "捲曲羊毛", "curly wool covering"),
            ("sleek_fur", "貼身亮毛", "sleek glossy fur"),
            ("feathers", "羽毛覆體", "layered feather covering"),
            ("smooth_scales", "光滑鱗片", "smooth overlapping scales"),
            ("keeled_scales", "稜脊鱗片", "textured keeled scales"),
            ("aquatic_skin", "水生光滑皮膚", "smooth aquatic skin"),
            ("chitin", "幾丁質甲殼", "segmented chitin covering"),
            ("mixed_covering", "混合體表", "harmonious mixed fur and scales"),
        ),
        help_text="需先選擇福瑞物種；隨機生成只會搭配合理體表。",
    ),
    _optional_single_options(
        "furry_marking_pattern",
        "福瑞毛色／體表花紋",
        "福瑞細節",
        _extra_options(
            ("solid", "純色", "solid body coloration"),
            ("countershading", "背深腹淺", "natural countershading"),
            ("tuxedo", "燕尾服花色", "tuxedo fur pattern"),
            ("tabby", "虎斑", "tabby fur pattern"),
            ("tiger_stripes", "虎紋", "bold tiger stripes"),
            ("zebra_stripes", "斑馬紋", "high-contrast zebra stripes"),
            ("spots", "斑點", "clean spotted pattern"),
            ("rosettes", "玫瑰斑", "leopard rosette pattern"),
            ("piebald", "花斑", "piebald body pattern"),
            ("brindle", "虎斑混色", "brindle fur pattern"),
            ("point_coloration", "重點色", "point coloration"),
            ("facial_mask", "面罩花紋", "contrasting facial mask pattern"),
            ("saddle_mark", "鞍狀斑", "contrasting saddle marking"),
            ("gradient", "漸層體色", "smooth gradient body coloration"),
            ("bioluminescent", "生物光紋", "controlled bioluminescent markings"),
        ),
        help_text="需先選擇福瑞物種；花紋會依體表類型限制。",
    ),
    _optional_single_options(
        "furry_muzzle_shape",
        "福瑞口鼻／頭部輪廓",
        "福瑞細節",
        _extra_options(
            (
                "species_default",
                "物種原生口鼻",
                "species-accurate muzzle and head profile",
            ),
            ("short_feline", "短貓科口鼻", "short feline muzzle"),
            ("long_canine", "長犬科口鼻", "long canine muzzle"),
            ("vulpine", "狐系口鼻", "slender vulpine muzzle"),
            ("lupine", "狼系口鼻", "strong lupine muzzle"),
            ("ursine", "熊系口鼻", "broad ursine muzzle"),
            ("lapine", "兔系鼻吻", "compact lapine muzzle"),
            ("cervine", "鹿系口鼻", "graceful cervine muzzle"),
            ("caprine", "羊系口鼻", "structured caprine muzzle"),
            ("bovine", "牛系口鼻", "broad bovine muzzle"),
            ("equine", "馬系口鼻", "elongated equine muzzle"),
            ("rodent", "齧齒系口鼻", "compact rodent muzzle"),
            ("mustelid", "鼬科口鼻", "rounded mustelid muzzle"),
            ("avian_beak", "鳥喙", "expressive avian beak"),
            ("reptilian", "爬蟲口鼻", "structured reptilian snout"),
            ("aquatic_rostrum", "水生吻部", "streamlined aquatic rostrum"),
            ("insectoid", "昆蟲顏面甲", "expressive insectoid facial plates"),
            ("draconic", "龍系口鼻", "refined draconic snout"),
        ),
        help_text="需先選擇福瑞物種；不會產生普通動物或四足姿態。",
    ),
    _optional_single_options(
        "furry_leg_style",
        "福瑞腿型",
        "福瑞細節",
        _extra_options(
            ("plantigrade", "人型蹠行腿", "bipedal plantigrade humanoid legs"),
            ("digitigrade", "趾行腿", "bipedal digitigrade humanoid legs"),
            ("unguligrade", "蹄行腿", "bipedal unguligrade humanoid legs"),
            ("avian", "鳥類腿型", "bipedal avian humanoid legs"),
            ("reptilian", "爬蟲趾行腿", "bipedal digitigrade reptilian humanoid legs"),
            ("webbed", "水生蹼足腿型", "bipedal aquatic humanoid legs with webbed feet"),
            ("insectoid", "昆蟲節肢腿", "bipedal insectoid humanoid legs"),
            ("draconic", "龍族趾行腿", "bipedal digitigrade draconic humanoid legs"),
        ),
        help_text="所有選項均固定雙足與人型比例。",
    ),
    _optional_single_options(
        "furry_extremities",
        "福瑞手足末端",
        "福瑞細節",
        _extra_options(
            (
                "species_default",
                "物種原生手足",
                "species-accurate dexterous hands and bipedal feet",
            ),
            ("padded_hands", "肉球人型手", "humanlike hands with paw pads"),
            ("paw_hands", "爪掌手", "articulated paw-hands with humanoid fingers"),
            ("clawed_hands", "利爪人型手", "humanoid hands with controlled claws"),
            ("hoof_hands", "蹄足與人型手", "humanoid hands with split-hoof feet"),
            ("talons", "鳥爪手足", "articulated avian hands and feet with talons"),
            ("webbed", "蹼狀手足", "humanoid webbed hands and feet"),
            ("scaled_claws", "鱗爪手足", "humanoid scaled hands and feet with claws"),
            ("insectoid", "節肢手足", "articulated insectoid humanoid hands and feet"),
            ("soft_paws", "柔軟獸掌", "soft expressive paw-hands"),
            ("armored_claws", "甲殼利爪", "armored humanoid clawed hands"),
        ),
        help_text="所有手足皆可維持工具使用與直立活動。",
    ),
    _optional_single_options(
        "furry_tail_style",
        "福瑞尾型",
        "福瑞細節",
        _extra_options(
            ("species_default", "物種原生尾型", "species-accurate tail anatomy"),
            ("none", "無尾", "no visible tail"),
            ("short", "短尾", "short expressive tail"),
            ("curled", "捲尾", "curled expressive tail"),
            ("fluffy", "蓬鬆長尾", "long fluffy tail"),
            ("fox_brush", "狐系刷尾", "full fox-brush tail"),
            ("feline", "貓科長尾", "flexible feline tail"),
            ("canine", "犬科尾", "expressive canine tail"),
            ("lupine", "狼系尾", "thick lupine tail"),
            ("lapine", "兔球尾", "round lapine tail"),
            ("ursine", "熊系短尾", "small ursine tail"),
            ("cervine", "鹿系短尾", "short cervine tail"),
            ("bovine", "牛系長尾", "slender bovine tail"),
            ("equine", "馬系流尾", "flowing equine tail"),
            ("rodent", "齧齒長尾", "slender rodent tail"),
            ("avian_plume", "鳥類尾羽", "layered avian tail plumage"),
            ("reptilian", "爬蟲長尾", "tapered reptilian tail"),
            ("aquatic", "水生尾", "streamlined aquatic tail"),
            ("insect_abdomen", "昆蟲腹尾", "streamlined insectoid abdomen tail"),
        ),
        help_text="需先選擇福瑞物種；尾型會依物種限制。",
    ),
)


_ADDITIONAL_CHARACTER_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    _single(
        "character_output_purpose",
        "角色圖輸出用途",
        "畫面與風格",
        (
            "character_illustration",
            "角色立繪",
            "single full-body character illustration with a clean unobtrusive backdrop",
        ),
        (
            "orthographic_turnaround",
            "標準三視圖",
            "same adult character in a consistent outfit and proportions shown as full-body "
            "front side and back orthographic views on a clean unlabeled turnaround sheet",
        ),
        (
            "illustration_plus_turnaround",
            "角色立繪＋三視圖設定板",
            "same adult character with consistent identity outfit and proportions shown in one "
            "polished full-body illustration beside full-body front side and back orthographic "
            "views on a clean unlabeled design board",
        ),
        (
            "complete_character_sheet",
            "完整角色設定表",
            "complete unlabeled character design sheet of the same adult character with "
            "consistent proportions and outfit plus full-body front side and back orthographic "
            "views and expression and detail callouts",
        ),
        (
            "expression_sheet",
            "表情設定表",
            "same adult character shown in a consistent head-and-shoulders expression lineup "
            "on a clean unlabeled design sheet",
        ),
        (
            "outfit_sheet",
            "服裝變化設定表",
            "same adult character shown in coordinated full-body outfit variants with "
            "consistent identity and proportions on a clean unlabeled design sheet",
        ),
        (
            "action_sheet",
            "動作設定表",
            "same adult character shown in multiple full-body action poses with consistent "
            "identity outfit and proportions on a clean unlabeled design sheet",
        ),
        (
            "silhouette_sheet",
            "輪廓探索設定表",
            "same adult character shown as clean full-body silhouette explorations with "
            "consistent proportions on an unlabeled design sheet",
        ),
        help_text="可輸出單張立繪、正側背三視圖或無文字的角色設定表。",
        default="character_illustration",
    ),
    _single(
        "breast_shape",
        "胸型",
        "身材細節",
        ("round", "圓潤型", "round adult breast shape"),
        ("teardrop", "水滴型", "teardrop adult breast shape"),
        ("shallow", "淺盤型", "shallow-profile adult breast shape"),
        ("projected", "前挺型", "projected adult breast shape"),
        ("conical", "圓錐型", "conical adult breast shape"),
        ("bell", "鐘型", "bell-shaped adult breasts"),
        ("east_west", "外擴型", "outward-facing adult breast shape"),
        ("close_set", "集中型", "close-set adult breasts"),
        ("wide_set", "寬間距型", "wide-set adult breasts"),
        ("pendulous", "自然垂墜型", "naturally pendulous adult breasts"),
        ("asymmetrical", "自然不對稱型", "naturally asymmetrical adult breasts"),
        ("athletic", "緊實運動型", "firm athletic adult breast shape"),
        help_text="胸部大小與形狀分開選擇，只適用於成年女性角色。",
        default="round",
        applicable_gender=CharacterGender.FEMALE,
    ),
    _multi(
        "hair_color_pattern",
        "髮色配置",
        "髮型與髮色",
        ("solid", "單一純色", "solid hair color placement"),
        ("highlights", "挑染", "contrasting hair highlights"),
        ("fine_streaks", "細束挑染", "fine contrasting hair streaks"),
        ("chunky_streaks", "粗束挑染", "bold chunky hair streaks"),
        ("two_tone", "雙色分區", "two-tone hair color placement"),
        ("split_dye", "左右半分染", "split-dye hair color placement"),
        ("gradient", "漸層染", "smooth gradient hair color placement"),
        ("ombre", "漸變染", "soft ombre hair color placement"),
        ("underlayer", "內層染", "contrasting underlayer hair color"),
        ("inner_color", "耳圈內層染", "hidden inner hair color"),
        ("dip_dye", "浸染髮尾", "dip-dyed hair ends"),
        ("colored_tips", "異色髮尾", "contrasting colored hair tips"),
        ("colored_roots", "異色髮根", "contrasting colored hair roots"),
        ("money_piece", "臉側重點染", "face-framing money-piece highlights"),
        ("balayage", "手刷染", "soft balayage hair coloring"),
        ("color_blocks", "色塊染", "graphic color-blocked hair"),
        ("rainbow_sections", "彩虹分區", "sectioned rainbow hair coloring"),
        ("iridescent_sheen", "虹彩光澤", "iridescent hair-color sheen"),
        help_text="可搭配一至三種髮色；需要多色的配置在隨機生成時會自動配對。",
        random_min=0,
        random_max=1,
        selection_max=1,
    ),
    _multi(
        "iris_color_pattern",
        "虹膜配色／異色瞳樣式",
        "臉部與眼睛",
        ("solid", "單一虹膜色", "solid iris coloring"),
        ("complete_heterochromia", "雙眼完全異色", "complete heterochromia"),
        ("sectoral_heterochromia", "扇形異色", "sectoral heterochromia"),
        ("central_heterochromia", "中央異色環", "central heterochromia"),
        ("gradient", "虹膜漸層", "gradient iris coloring"),
        ("limbal_ring", "深色虹膜外環", "defined dark limbal rings"),
        ("starburst", "放射星芒紋", "radial starburst iris pattern"),
        ("speckled", "虹膜斑點", "finely speckled irises"),
        ("concentric", "同心環紋", "concentric iris rings"),
        ("glowing", "發光虹膜", "glowing irises"),
        ("rainbow", "彩虹虹膜", "rainbow irises"),
        ("starry", "星空虹膜", "star-filled irises"),
        ("mechanical", "機械虹膜", "mechanical irises"),
        ("faceted", "寶石切面虹膜", "faceted gemstone irises"),
        ("rune_ring", "符文虹膜環", "rune-inscribed iris rings"),
        ("void_center", "虛空瞳孔", "void-dark pupil centers"),
        help_text="可配合一或兩種眼睛顏色；異色瞳配置在隨機生成時至少會搭配兩色。",
        random_min=0,
        random_max=1,
        selection_max=1,
    ),
    _adult_optional(
        "areola_size",
        "乳暈尺寸（18+）",
        "成人身體細節（18+）",
        ("petite", "極小乳暈（18+）", "consensual adult female with petite areolae"),
        ("small", "小乳暈（18+）", "consensual adult female with small areolae"),
        ("medium", "中等乳暈（18+）", "consensual adult female with medium areolae"),
        ("large", "大乳暈（18+）", "consensual adult female with large areolae"),
        ("broad", "寬大乳暈（18+）", "consensual adult female with broad areolae"),
        (
            "proportional",
            "比例自然乳暈（18+）",
            "consensual adult female with proportionate areolae",
        ),
        ("prominent", "顯眼乳暈（18+）", "consensual adult female with prominent areolae"),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "areola_shape",
        "乳暈形狀（18+）",
        "成人身體細節（18+）",
        ("round", "圓形乳暈（18+）", "consensual adult female with round areolae"),
        ("oval", "橢圓乳暈（18+）", "consensual adult female with oval areolae"),
        ("soft_edge", "柔和邊緣乳暈（18+）", "consensual adult female with soft-edged areolae"),
        (
            "defined_edge",
            "清晰邊緣乳暈（18+）",
            "consensual adult female with defined areola edges",
        ),
        ("puffy", "微凸乳暈（18+）", "consensual adult female with gently puffy areolae"),
        ("flat", "平坦乳暈（18+）", "consensual adult female with flat areolae"),
        (
            "natural_irregular",
            "自然不規則乳暈（18+）",
            "consensual adult female with naturally irregular areolae",
        ),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "areola_tone",
        "乳暈色澤（18+）",
        "成人身體細節（18+）",
        ("pale_pink", "淡粉色（18+）", "consensual adult female with pale-pink areolae"),
        ("rose", "玫瑰色（18+）", "consensual adult female with rose-toned areolae"),
        ("peach", "蜜桃色（18+）", "consensual adult female with peach-toned areolae"),
        ("warm_beige", "暖米色（18+）", "consensual adult female with warm-beige areolae"),
        ("mauve", "藕紫色（18+）", "consensual adult female with mauve areolae"),
        ("warm_brown", "暖棕色（18+）", "consensual adult female with warm-brown areolae"),
        ("deep_brown", "深棕色（18+）", "consensual adult female with deep-brown areolae"),
        ("copper", "赤銅色（18+）", "consensual adult female with copper-toned areolae"),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "nipple_size",
        "乳頭尺寸（18+）",
        "成人身體細節（18+）",
        ("petite", "極小乳頭（18+）", "consensual adult female with petite nipples"),
        ("small", "小乳頭（18+）", "consensual adult female with small nipples"),
        ("medium", "中等乳頭（18+）", "consensual adult female with medium nipples"),
        ("large", "大乳頭（18+）", "consensual adult female with large nipples"),
        ("broad", "寬乳頭（18+）", "consensual adult female with broad nipples"),
        (
            "proportional",
            "比例自然乳頭（18+）",
            "consensual adult female with proportionate nipples",
        ),
        ("prominent", "顯眼乳頭（18+）", "consensual adult female with prominent nipples"),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "nipple_shape",
        "乳頭形狀（18+）",
        "成人身體細節（18+）",
        ("round", "圓潤型（18+）", "consensual adult female with rounded nipples"),
        ("tapered", "尖錐型（18+）", "consensual adult female with tapered nipples"),
        ("cylindrical", "圓柱型（18+）", "consensual adult female with cylindrical nipples"),
        ("elongated", "修長型（18+）", "consensual adult female with elongated nipples"),
        ("flat_profile", "平緩型（18+）", "consensual adult female with low-profile nipples"),
        ("wide_base", "寬基部型（18+）", "consensual adult female with wide-base nipples"),
        (
            "natural_asymmetry",
            "自然不對稱型（18+）",
            "consensual adult female with naturally asymmetrical nipples",
        ),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "nipple_state",
        "乳頭狀態（18+）",
        "成人身體細節（18+）",
        ("relaxed", "自然放鬆（18+）", "consensual adult female with relaxed nipples"),
        (
            "slightly_raised",
            "微微挺立（18+）",
            "consensual adult female with slightly raised nipples",
        ),
        ("erect", "挺立（18+）", "consensual adult female with erect nipples"),
        ("inverted", "內陷型（18+）", "consensual adult female with inverted nipples"),
        (
            "partly_inverted",
            "部分內陷（18+）",
            "consensual adult female with partly inverted nipples",
        ),
        ("puffy", "柔軟微凸（18+）", "consensual adult female with softly puffy nipples"),
        ("pierced", "乳頭穿環（18+）", "consensual adult female with pierced nipples"),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "vulva_shape",
        "外陰整體外觀（18+）",
        "成人身體細節（18+）",
        ("compact", "緊緻型（18+）", "consensual adult female with compact external vulva anatomy"),
        ("rounded", "圓潤型（18+）", "consensual adult female with rounded external vulva anatomy"),
        ("full", "飽滿型（18+）", "consensual adult female with full external vulva anatomy"),
        ("narrow", "纖窄型（18+）", "consensual adult female with narrow external vulva anatomy"),
        (
            "soft_folded",
            "柔和摺疊型（18+）",
            "consensual adult female with softly folded external vulva anatomy",
        ),
        (
            "prominent_mound",
            "明顯陰阜型（18+）",
            "consensual adult female with a prominent adult pubic mound",
        ),
        (
            "natural_asymmetry",
            "自然不對稱型（18+）",
            "consensual adult female with naturally asymmetrical external vulva anatomy",
        ),
        help_text="只描述成年女性的外部解剖外觀，不含任何非合意情境。",
    ),
    _adult_optional(
        "labia_shape",
        "陰唇形態（18+）",
        "成人身體細節（18+）",
        (
            "concealed_inner",
            "內唇隱藏型（18+）",
            "consensual adult female with concealed inner labia",
        ),
        (
            "subtle_inner",
            "內唇微露型（18+）",
            "consensual adult female with subtly visible inner labia",
        ),
        ("visible_inner", "內唇可見型（18+）", "consensual adult female with visible inner labia"),
        ("full_outer", "外唇飽滿型（18+）", "consensual adult female with full outer labia"),
        ("slender_outer", "外唇纖細型（18+）", "consensual adult female with slender outer labia"),
        (
            "elongated_inner",
            "內唇修長型（18+）",
            "consensual adult female with elongated inner labia",
        ),
        (
            "natural_asymmetry",
            "自然不對稱型（18+）",
            "consensual adult female with naturally asymmetrical labia",
        ),
        help_text="只描述成年女性的外部解剖外觀，不含任何非合意情境。",
    ),
    _adult_optional(
        "pubic_hair_style",
        "陰毛造型（18+）",
        "成人身體細節（18+）",
        ("natural", "自然生長（18+）", "consensual adult female with natural pubic hair"),
        ("trimmed", "整齊修短（18+）", "consensual adult female with neatly trimmed pubic hair"),
        ("shaved", "完全剃除（18+）", "consensual adult female with a shaved pubic area"),
        (
            "landing_strip",
            "直條造型（18+）",
            "consensual adult female with a landing-strip pubic hairstyle",
        ),
        (
            "triangle",
            "三角造型（18+）",
            "consensual adult female with a triangular pubic hairstyle",
        ),
        (
            "bikini_line",
            "比基尼線修整（18+）",
            "consensual adult female with a groomed bikini-line pubic style",
        ),
        ("heart", "心形造型（18+）", "consensual adult female with a heart-shaped pubic hairstyle"),
        (
            "decorative",
            "幾何裝飾造型（18+）",
            "consensual adult female with a geometric pubic hairstyle",
        ),
        help_text="僅供明確成年且合意的角色使用。",
        applicable_gender=None,
    ),
    _adult_optional(
        "adult_female_breast_hand_action",
        "胸部手部動作（18+）",
        "成人動作與表情（18+）",
        (
            "support_left",
            "單手托住左乳（18+）",
            "consensual adult female supporting her left breast from below with one hand",
        ),
        (
            "support_right",
            "單手托住右乳（18+）",
            "consensual adult female supporting her right breast from below with one hand",
        ),
        (
            "support_both",
            "雙手托住雙乳（18+）",
            "consensual adult female supporting both breasts from below with both hands",
        ),
        (
            "cup_left",
            "單手捧住左乳（18+）",
            "consensual adult female cupping her left breast with one hand",
        ),
        (
            "cup_right",
            "單手捧住右乳（18+）",
            "consensual adult female cupping her right breast with one hand",
        ),
        (
            "cup_both",
            "雙手分別捧住雙乳（18+）",
            "consensual adult female cupping both breasts with one hand on each breast",
        ),
        (
            "squeeze_left",
            "單手擠壓左乳（18+）",
            "consensual adult female squeezing her left breast with one hand",
        ),
        (
            "squeeze_right",
            "單手擠壓右乳（18+）",
            "consensual adult female squeezing her right breast with one hand",
        ),
        (
            "squeeze_both",
            "雙手擠壓雙乳（18+）",
            "consensual adult female squeezing both breasts with both hands",
        ),
        (
            "lift_both",
            "雙手向上捧起雙乳（18+）",
            "consensual adult female lifting both breasts upward with both hands",
        ),
        (
            "press_together",
            "雙手將雙乳向中間聚攏（18+）",
            "consensual adult female pressing both breasts together with both hands",
        ),
        (
            "knead_left",
            "單手揉捏左乳（18+）",
            "consensual adult female kneading her left breast with one hand",
        ),
        (
            "knead_right",
            "單手揉捏右乳（18+）",
            "consensual adult female kneading her right breast with one hand",
        ),
        (
            "knead_both",
            "雙手揉捏雙乳（18+）",
            "consensual adult female kneading both breasts with both hands",
        ),
        (
            "pinch_left_nipple",
            "單手捏住左乳頭（18+）",
            "consensual adult female gently pinching her left nipple with one hand",
        ),
        (
            "pinch_right_nipple",
            "單手捏住右乳頭（18+）",
            "consensual adult female gently pinching her right nipple with one hand",
        ),
        (
            "pinch_both_nipples",
            "雙手捏住雙側乳頭（18+）",
            "consensual adult female gently pinching both nipples with both hands",
        ),
        help_text="僅顯示於成年女性 18+ 模式；左右方向以角色自身為準。",
    ),
    _adult_optional(
        "adult_female_breast_suckling_action",
        "乳房／乳頭親密吸吮（18+）",
        "成人動作與表情（18+）",
        (
            "suckle_left_breast",
            "左乳被吸吮（18+）",
            "consensual adult female having her left breast suckled by one consenting adult "
            "human partner",
        ),
        (
            "suckle_right_breast",
            "右乳被吸吮（18+）",
            "consensual adult female having her right breast suckled by one consenting adult "
            "human partner",
        ),
        (
            "suckle_both_breasts",
            "雙乳同時被吸吮（18+）",
            "consensual adult female having both breasts suckled by two consenting adult human "
            "partners simultaneously",
        ),
        (
            "suckle_left_nipple",
            "左乳頭被吸吮（18+）",
            "consensual adult female having her left nipple suckled by one consenting adult "
            "human partner",
        ),
        (
            "suckle_right_nipple",
            "右乳頭被吸吮（18+）",
            "consensual adult female having her right nipple suckled by one consenting adult "
            "human partner",
        ),
        (
            "suckle_both_nipples",
            "雙側乳頭同時被吸吮（18+）",
            "consensual adult female having both nipples suckled by two consenting adult human "
            "partners simultaneously",
        ),
        (
            "lick_left_nipple",
            "左乳頭被舔舐（18+）",
            "consensual adult female having her left nipple licked by one consenting adult human "
            "partner",
        ),
        (
            "lick_right_nipple",
            "右乳頭被舔舐（18+）",
            "consensual adult female having her right nipple licked by one consenting adult "
            "human partner",
        ),
        (
            "lick_both_nipples",
            "雙側乳頭同時被舔舐（18+）",
            "consensual adult female having both nipples licked by two consenting adult human "
            "partners simultaneously",
        ),
        (
            "kiss_left_breast",
            "左乳被親吻（18+）",
            "consensual adult female having her left breast kissed by one consenting adult human "
            "partner",
        ),
        (
            "kiss_right_breast",
            "右乳被親吻（18+）",
            "consensual adult female having her right breast kissed by one consenting adult human "
            "partner",
        ),
        (
            "kiss_both_breasts",
            "雙乳同時被親吻（18+）",
            "consensual adult female having both breasts kissed by two consenting adult human "
            "partners simultaneously",
        ),
        help_text="只描述明確成年且合意的參與者；左右方向以角色自身為準。",
    ),
    _adult_optional(
        "adult_female_lactation_action",
        "泌乳／擠乳動作（18+）",
        "成人動作與表情（18+）",
        (
            "milk_splash",
            "乳汁噴濺（18+）",
            "consensual adult female with breast milk splashing across her chest",
        ),
        (
            "milk_spray_left",
            "左乳乳汁噴射（18+）",
            "consensual adult female with breast milk spraying from her left nipple",
        ),
        (
            "milk_spray_right",
            "右乳乳汁噴射（18+）",
            "consensual adult female with breast milk spraying from her right nipple",
        ),
        (
            "milk_spray_both",
            "雙乳乳汁同時噴射（18+）",
            "consensual adult female with breast milk spraying simultaneously from both nipples",
        ),
        (
            "hand_express_left",
            "手擠左乳（18+）",
            "consensual adult female hand-expressing milk from her left breast",
        ),
        (
            "hand_express_right",
            "手擠右乳（18+）",
            "consensual adult female hand-expressing milk from her right breast",
        ),
        (
            "hand_express_both",
            "雙手同時擠乳（18+）",
            "consensual adult female using both hands to express milk from both breasts "
            "simultaneously",
        ),
        (
            "milk_drip_left",
            "左乳乳汁滴落（18+）",
            "consensual adult female with breast milk dripping from her left nipple",
        ),
        (
            "milk_drip_right",
            "右乳乳汁滴落（18+）",
            "consensual adult female with breast milk dripping from her right nipple",
        ),
        (
            "milk_drip_both",
            "雙乳乳汁滴落（18+）",
            "consensual adult female with breast milk dripping from both nipples",
        ),
        (
            "single_breast_pump_left",
            "左乳單邊吸乳器（18+）",
            "consensual adult female using a breast pump on her left breast",
        ),
        (
            "single_breast_pump_right",
            "右乳單邊吸乳器（18+）",
            "consensual adult female using a breast pump on her right breast",
        ),
        (
            "double_breast_pump",
            "雙邊吸乳器（18+）",
            "consensual adult female using a double breast pump on both breasts",
        ),
        help_text="僅供明確成年、合意的女性泌乳情境；可與上方手部動作分開搭配。",
    ),
    _adult_optional(
        "adult_female_expression",
        "成人歡愉表情（18+）",
        "成人動作與表情（18+）",
        (
            "orgasm_face",
            "高潮臉（18+）",
            "consensual adult female orgasmic facial expression",
        ),
        (
            "ahegao",
            "啊嘿顏／アヘ顔（18+）",
            "consensual adult female ahegao expression",
        ),
        (
            "eyes_rolled_orgasm",
            "高潮翻白眼（18+）",
            "consensual adult female orgasmic expression with eyes rolled upward",
        ),
        (
            "crossed_eyes_ecstasy",
            "歡愉鬥雞眼（18+）",
            "consensual adult female ecstatic crossed-eye expression",
        ),
        (
            "tongue_out_ecstasy",
            "吐舌歡愉臉（18+）",
            "consensual adult female ecstatic expression with tongue extended",
        ),
        (
            "panting_open_mouth",
            "張口喘息臉（18+）",
            "consensual adult female open-mouthed panting expression",
        ),
        (
            "drooling_ecstasy",
            "流涎恍惚臉（18+）",
            "consensual adult female dazed ecstatic expression with subtle drooling",
        ),
        (
            "tearful_pleasure",
            "含淚歡愉臉（18+）",
            "consensual adult female tearful expression of intense pleasure",
        ),
        (
            "bitten_lip_pleasure",
            "咬唇歡愉臉（18+）",
            "consensual adult female pleasure expression with a bitten lower lip",
        ),
        (
            "climax_flush",
            "高潮潮紅臉（18+）",
            "consensual adult female deeply flushed climax expression",
        ),
        (
            "half_lidded_ecstasy",
            "半闔眼歡愉臉（18+）",
            "consensual adult female half-lidded ecstatic expression",
        ),
        (
            "overwhelmed_pleasure",
            "難以承受的歡愉臉（18+）",
            "consensual adult female overwhelmed pleasure expression",
        ),
        (
            "dazed_afterglow",
            "高潮後恍惚臉（18+）",
            "consensual adult female dazed afterglow expression",
        ),
        (
            "blissful_climax",
            "幸福高潮臉（18+）",
            "consensual adult female blissful climax expression",
        ),
        help_text="只在成年角色與 18+ 模式顯示；所有選項皆為合意情境。",
        applicable_gender=None,
    ),
    _multi(
        "eyewear",
        "眼鏡／眼部配件",
        "臉部與眼睛",
        ("none", "不戴眼鏡", "no eyewear"),
        ("round", "圓框眼鏡", "round-frame glasses"),
        ("square", "方框眼鏡", "square-frame glasses"),
        ("rectangular", "長方框眼鏡", "rectangular-frame glasses"),
        ("oval", "橢圓框眼鏡", "oval-frame glasses"),
        ("cat_eye", "貓眼眼鏡", "cat-eye glasses"),
        ("aviator", "飛行員眼鏡", "aviator glasses"),
        ("rimless", "無框眼鏡", "rimless glasses"),
        ("half_rim", "半框眼鏡", "half-rim glasses"),
        ("wire_frame", "細金屬框", "thin wire-frame glasses"),
        ("thick_frame", "粗框眼鏡", "thick-frame glasses"),
        ("reading", "閱讀眼鏡", "reading glasses"),
        ("sunglasses", "太陽眼鏡", "stylish sunglasses"),
        ("tinted", "有色鏡片", "tinted glasses"),
        ("goggles", "護目鏡", "protective goggles"),
        ("steampunk_goggles", "蒸汽龐克護目鏡", "steampunk goggles"),
        ("monocle", "單片眼鏡", "ornate monocle"),
        ("eyepatch", "眼罩", "decorative eyepatch"),
        ("visor", "科技面罩", "futuristic eye visor"),
        ("blindfold", "蒙眼布", "decorative blindfold"),
        random_min=0,
        random_max=1,
        selection_max=1,
    ),
    _single(
        "eyebrows",
        "眉型",
        "臉部與眼睛",
        ("soft", "柔和眉", "soft natural eyebrows"),
        ("straight", "平直眉", "straight eyebrows"),
        ("arched", "高挑眉", "highly arched eyebrows"),
        ("angled", "稜角眉", "sharply angled eyebrows"),
        ("rounded", "圓弧眉", "rounded eyebrows"),
        ("thick", "濃眉", "thick eyebrows"),
        ("thin", "細眉", "thin eyebrows"),
        ("feathered", "羽毛野生眉", "feathered eyebrows"),
        ("short", "短眉", "short eyebrows"),
        ("long", "長眉尾", "long tapered eyebrows"),
        ("split", "斷眉", "single eyebrow slit"),
        ("none", "無眉", "no visible eyebrows"),
        default="soft",
    ),
    _single(
        "nose_shape",
        "鼻型",
        "臉部與眼睛",
        ("small", "小巧鼻", "small delicate nose"),
        ("straight", "直鼻", "straight nose"),
        ("button", "圓鈕鼻", "button nose"),
        ("upturned", "微翹鼻", "slightly upturned nose"),
        ("aquiline", "鷹鉤鼻", "aquiline nose"),
        ("roman", "羅馬鼻", "Roman nose"),
        ("broad", "寬鼻", "broad nose"),
        ("flat_bridge", "低鼻樑", "low nose bridge"),
        ("high_bridge", "高鼻樑", "high nose bridge"),
        ("crooked", "微歪鼻", "slightly crooked nose"),
        default="straight",
    ),
    _single(
        "lips",
        "嘴唇／嘴型",
        "臉部與眼睛",
        ("thin", "薄唇", "thin lips"),
        ("balanced", "均衡唇形", "balanced lips"),
        ("full", "豐唇", "full adult lips"),
        ("heart", "心形唇峰", "heart-shaped cupid's bow"),
        ("wide", "寬嘴型", "wide mouth"),
        ("small", "小嘴型", "small mouth"),
        ("downturned", "嘴角下垂", "downturned lips"),
        ("upturned", "嘴角上揚", "upturned lips"),
        ("asymmetrical", "不對稱唇形", "slightly asymmetrical lips"),
        ("glossy", "水光唇", "glossy lips"),
        ("matte", "霧面唇", "matte lips"),
        ("fang_smile", "露牙微笑", "toothy smile"),
        default="balanced",
    ),
    _multi(
        "makeup",
        "妝容細節",
        "臉部與眼睛",
        ("natural", "自然裸妝", "natural makeup"),
        ("no_makeup", "無妝感", "no visible makeup"),
        ("winged_eyeliner", "飛揚眼線", "winged eyeliner"),
        ("smoky_eyes", "煙燻眼妝", "smoky eye makeup"),
        ("cut_crease", "截斷式眼妝", "cut-crease eye makeup"),
        ("glitter", "亮片眼妝", "glitter eye makeup"),
        ("graphic_liner", "圖形眼線", "graphic eyeliner"),
        ("blush", "明顯腮紅", "visible blush"),
        ("freckle_makeup", "雀斑妝", "decorative freckle makeup"),
        ("red_lip", "紅唇", "crimson lipstick"),
        ("dark_lip", "深色唇彩", "dark lipstick"),
        ("gradient_lip", "漸層唇", "gradient lip color"),
        ("metallic_lip", "金屬唇彩", "metallic lipstick"),
        ("face_gems", "臉部水鑽", "decorative face gems"),
        ("festival_paint", "節慶彩繪", "festival face paint"),
        ("gothic", "哥德妝", "gothic makeup"),
        ("stage", "舞台妝", "dramatic stage makeup"),
        ("cyber", "賽博妝", "cybernetic makeup accents"),
        ("fantasy_runes", "符文妝", "painted facial runes"),
        ("tear_streaks", "淚痕妝", "deliberate tear-streak makeup"),
        random_min=0,
        random_max=3,
    ),
)


_LEGACY_EYEWEAR_MARK_KEYS: Final[frozenset[str]] = frozenset(
    {
        "glasses",
        "round_glasses",
        "square_glasses",
        "rimless_glasses",
        "half_rim_glasses",
        "cat_eye_glasses",
        "monocle",
        "goggles",
        "sunglasses",
        "tinted_glasses",
        "visor",
        "reading_glasses",
        "eyepatch",
    }
)

_ANIMAL_LINEAGE_RACE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "catfolk",
        "foxfolk",
        "wolffolk",
        "rabbitfolk",
        "mothfolk",
        "avianfolk",
        "lizardfolk",
        "insectfolk",
        "sharkfolk",
        "ursafolk",
        "cervidfolk",
        "bovinefolk",
    }
)


_CHARACTER_OPTION_EXTENSIONS_V3: Final[dict[str, tuple[TagOption, ...]]] = {
    "age_impression": _extra_options(
        ("early_thirties", "三十歲初段成年人", "adult appearance in the early thirties"),
        ("late_thirties", "三十歲後段成年人", "adult appearance in the late thirties"),
        ("early_forties", "四十歲初段成年人", "adult appearance in the early forties"),
        ("late_fifties", "五十歲後段成年人", "adult appearance in the late fifties"),
    ),
    "fantasy_race": _extra_options(
        ("half_orc", "半歐克族", "half-orc"),
        ("stoneborn", "岩石裔", "stoneborn humanoid"),
        ("starforged", "星鑄裔", "star-forged humanoid"),
        ("dreamborn", "夢境裔", "dreamborn humanoid"),
        ("homunculus", "人工生命族", "sapient homunculus adult"),
        ("stormborn", "風暴裔", "stormborn humanoid"),
        ("sandfolk", "沙海民", "sand folk humanoid"),
        ("inkborn", "墨靈裔", "inkborn humanoid"),
        ("mirrorfolk", "鏡界民", "mirror-realm humanoid"),
        ("timewalker", "時行者血統", "time-walker humanoid lineage"),
    ),
    "expression": _extra_options(
        ("wry", "苦笑", "wry adult expression"),
        ("skeptical", "懷疑挑眉", "skeptical raised-brow expression"),
        ("reverent", "敬畏虔誠", "reverent expression"),
        ("homesick", "思鄉神情", "homesick adult expression"),
        ("guarded", "戒備克制", "guarded restrained expression"),
        ("compassionate", "憐憫溫柔", "compassionate expression"),
        ("grim_resolve", "冷峻決意", "grimly resolved expression"),
        ("quiet_pride", "沉靜自豪", "quietly proud expression"),
        ("dry_amusement", "淡淡揶揄", "dryly amused expression"),
        ("conflicted", "內心掙扎", "emotionally conflicted expression"),
    ),
    "character_lighting": _extra_options(
        ("lantern_side", "提燈側光", "warm lantern side lighting"),
        ("storm_flash", "暴風閃光", "dramatic storm-flash lighting"),
        ("prismatic", "稜鏡折射光", "prismatic refracted lighting"),
        ("window_pattern", "窗格投影光", "patterned window-cast lighting"),
        ("deep_sea_glow", "深海幽光", "deep-sea luminous lighting"),
        ("embers", "餘燼光", "soft ember-lit illumination"),
    ),
    "character_style": _extra_options(
        ("etched_engraving", "蝕刻版畫", "etched engraving character art"),
        ("tapestry", "織毯插畫", "woven tapestry character art"),
        ("stained_glass", "彩繪玻璃", "stained-glass character art"),
        ("porcelain_figure", "瓷偶質感", "painted porcelain figure style"),
        ("luminous_manuscript", "泥金手抄本", "illuminated manuscript character art"),
        ("graphic_novel", "圖像小說", "graphic-novel character illustration"),
    ),
    "character_backdrop": _extra_options(
        ("archive_shelves", "古老檔案架", "soft-focus ancient archive shelves"),
        ("observatory_dome", "觀星穹頂", "celestial observatory dome backdrop"),
        ("rain_window", "雨夜窗景", "rain-streaked window backdrop"),
        ("ruined_arch", "殘破拱門", "weathered ruined arch backdrop"),
        ("floating_isles", "漂浮群島", "distant floating-island backdrop"),
        ("underwater_light", "水下光紋", "underwater caustic-light backdrop"),
    ),
    "character_detail": _extra_options(
        ("textile_microdetail", "織物微細節", "precise textile microdetail"),
        ("facial_microdetail", "臉部微細節", "refined adult facial microdetail"),
        ("prop_storytelling", "道具敘事細節", "story-rich prop detailing"),
        ("silhouette_clarity", "輪廓清晰度", "exceptionally clear silhouette design"),
        ("surface_patina", "表面歲月質感", "carefully rendered surface patina"),
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V4: Final[dict[str, tuple[TagOption, ...]]] = {
    "pose": _extra_options(
        ("greeting_wave", "揮手招呼", "friendly waving gesture"),
        ("point_forward", "向前指示", "assertive pointing gesture toward the viewer"),
        ("hands_behind_back", "雙手背後", "relaxed pose with both hands behind the back"),
        ("one_hand_pocket", "單手插袋", "casual pose with one hand in a pocket"),
        ("both_hands_pockets", "雙手插袋", "casual pose with both hands in pockets"),
        ("adjust_glasses", "扶正眼鏡", "adjusting glasses with one hand"),
        ("brush_hair_back", "撥髮向後", "brushing hair back with one hand"),
        ("tie_hair", "綁起頭髮", "tying hair into place with both hands"),
        ("overhead_stretch", "雙臂上舉伸展", "stretching both arms overhead"),
        ("lean_forward", "身體前傾", "attentive forward-leaning pose"),
        ("seated_crossed_legs", "交叉腿坐姿", "seated pose with legs crossed"),
        ("seated_sideways", "側身坐姿", "sideways seated pose"),
        ("sitting_on_edge", "坐在邊緣", "sitting carefully on the edge of a surface"),
        ("lying_on_side", "側躺", "relaxed side-lying pose"),
        ("stepping_forward", "向前跨步", "decisive forward-stepping pose"),
        ("forward_lunge", "向前弓步", "dynamic forward lunge"),
        ("dodging", "閃避", "agile dodging action"),
        ("heroic_landing", "英雄式落地", "dynamic three-point landing pose"),
        ("spinning", "旋身", "fluid spinning motion"),
        ("high_kick", "高踢", "controlled high-kick action"),
        ("punching", "出拳", "powerful forward punching action"),
        ("one_leg_balance", "單腳平衡", "balanced pose standing on one leg"),
        ("carry_object", "搬抱物品", "carrying a substantial object with both arms"),
        ("inspect_object", "仔細查看物品", "carefully inspecting a handheld object"),
        ("hold_umbrella", "撐傘", "holding an open umbrella overhead"),
        ("salute", "敬禮", "formal saluting pose"),
        ("shrug", "聳肩", "expressive shrugging gesture"),
        ("listening", "側耳傾聽", "attentive listening pose"),
    ),
    "character_style": _extra_options(
        ("anime_cel_shaded", "電視動畫賽璐璐", "crisp television-anime cel shading"),
        ("anime_key_visual", "動畫主視覺", "polished anime promotional key visual"),
        ("anime_model_art", "動畫角色設定稿風格", "professional anime character model art"),
        ("anime_feature_film", "動畫電影質感", "cinematic anime feature-film rendering"),
        ("retro_70s_anime", "1970 年代復古動畫", "1970s retro anime illustration"),
        ("retro_80s_anime", "1980 年代復古動畫", "1980s retro anime illustration"),
        ("retro_90s_anime", "1990 年代復古動畫", "1990s retro anime illustration"),
        ("modern_tv_anime", "現代電視動畫", "modern television-anime character rendering"),
        ("prestige_anime", "高規格動畫作畫", "prestige anime production-quality illustration"),
        ("shoujo_anime", "少女漫畫動畫風", "ornate shoujo-anime character illustration"),
        ("josei_anime", "女性向成熟動畫風", "refined josei-anime character illustration"),
        ("seinen_anime", "青年漫畫動畫風", "detailed seinen-anime character illustration"),
        ("mecha_anime", "機甲動畫風", "technical mecha-anime character rendering"),
        ("fantasy_anime", "奇幻動畫風", "lush fantasy-anime character illustration"),
        ("isekai_anime", "異世界動畫風", "colorful isekai-anime character illustration"),
        ("anime_game_splash", "日系遊戲主視覺", "dynamic anime game splash illustration"),
        ("anime_gacha_art", "日系抽卡角色圖", "premium anime gacha character artwork"),
        ("anime_lineart", "精緻動畫線稿", "precise anime production line art"),
        ("painterly_anime", "動畫厚塗融合", "painterly anime character rendering"),
        ("watercolor_anime", "動畫水彩融合", "watercolor anime character rendering"),
        ("donghua_animation", "國風動畫", "polished Chinese donghua character rendering"),
        ("western_tv_animation", "現代歐美電視動畫", "modern Western television-animation design"),
        ("full_animation", "全動畫作畫", "fluid full-animation character design"),
        ("limited_animation", "有限動畫作畫", "graphic limited-animation character design"),
        ("cutout_animation", "剪紙動畫", "layered cutout-animation character design"),
        ("stop_motion", "定格動畫", "handcrafted stop-motion character design"),
        ("puppet_animation", "偶動畫", "articulated puppet-animation character design"),
        ("rotoscope_animation", "轉描動畫", "expressive rotoscoped-animation character rendering"),
        ("webtoon_rendering", "條漫上色風", "polished webtoon character rendering"),
        ("manhwa_rendering", "韓式漫畫風", "refined manhwa character illustration"),
    ),
    "adult_female_breast_hand_action": tuple(
        _adult_option(*value)
        for value in (
            (
                "cover_left",
                "單手遮住左乳（18+）",
                "consensual adult female covering her left breast with one hand",
            ),
            (
                "cover_right",
                "單手遮住右乳（18+）",
                "consensual adult female covering her right breast with one hand",
            ),
            (
                "cover_both",
                "雙手遮住雙乳（18+）",
                "consensual adult female covering both breasts with both hands",
            ),
            (
                "pull_left_outward",
                "單手將左乳向外拉（18+）",
                "consensual adult female gently pulling her left breast outward with one hand",
            ),
            (
                "pull_right_outward",
                "單手將右乳向外拉（18+）",
                "consensual adult female gently pulling her right breast outward with one hand",
            ),
            (
                "spread_both_outward",
                "雙手將雙乳向外分開（18+）",
                "consensual adult female gently spreading both breasts outward with both hands",
            ),
            (
                "press_left_upward",
                "單手上托左乳（18+）",
                "consensual adult female pressing her left breast upward with one hand",
            ),
            (
                "press_right_upward",
                "單手上托右乳（18+）",
                "consensual adult female pressing her right breast upward with one hand",
            ),
        )
    ),
    "adult_female_breast_suckling_action": tuple(
        _adult_option(*value)
        for value in (
            (
                "deep_suckle_left_nipple",
                "深吸左乳頭（18+）",
                "consensual adult female having her left nipple deeply suckled by one consenting "
                "adult human partner",
            ),
            (
                "deep_suckle_right_nipple",
                "深吸右乳頭（18+）",
                "consensual adult female having her right nipple deeply suckled by one consenting "
                "adult human partner",
            ),
            (
                "gentle_suckle_left_breast",
                "輕柔吸吮左乳（18+）",
                "consensual adult female having her left breast gently suckled by one consenting "
                "adult human partner",
            ),
            (
                "gentle_suckle_right_breast",
                "輕柔吸吮右乳（18+）",
                "consensual adult female having her right breast gently suckled by one consenting "
                "adult human partner",
            ),
            (
                "kiss_left_nipple",
                "親吻左乳頭（18+）",
                "consensual adult female having her left nipple kissed by one consenting adult "
                "human partner",
            ),
            (
                "kiss_right_nipple",
                "親吻右乳頭（18+）",
                "consensual adult female having her right nipple kissed by one consenting adult "
                "human partner",
            ),
        )
    ),
    "adult_female_lactation_action": tuple(
        _adult_option(*value)
        for value in (
            (
                "milk_arc_left",
                "左乳乳汁弧線（18+）",
                "consensual adult female producing an arcing stream of milk from her left nipple",
            ),
            (
                "milk_arc_right",
                "右乳乳汁弧線（18+）",
                "consensual adult female producing an arcing stream of milk from her right nipple",
            ),
            (
                "milk_arc_both",
                "雙乳乳汁弧線（18+）",
                "consensual adult female producing twin arcing streams of milk from both nipples",
            ),
            (
                "milk_pooling",
                "乳汁在胸前匯聚（18+）",
                "consensual adult female with expressed breast milk pooling across her chest",
            ),
            (
                "milk_stream_left",
                "手擠左乳形成乳汁流（18+）",
                "consensual adult female hand-expressing a steady milk stream from her left breast",
            ),
            (
                "milk_stream_right",
                "手擠右乳形成乳汁流（18+）",
                "consensual adult female hand-expressing a steady milk stream from her right "
                "breast",
            ),
            (
                "milk_stream_both",
                "雙手擠乳形成雙股乳汁流（18+）",
                "consensual adult female using both hands to express steady milk streams from "
                "both breasts",
            ),
            (
                "milk_beads_left",
                "左乳頭凝聚乳珠（18+）",
                "consensual adult female with beads of breast milk gathering on her left nipple",
            ),
            (
                "milk_beads_right",
                "右乳頭凝聚乳珠（18+）",
                "consensual adult female with beads of breast milk gathering on her right nipple",
            ),
        )
    ),
    "adult_female_expression": tuple(
        _adult_option(*value)
        for value in (
            (
                "eyes_squeezed_climax",
                "緊閉雙眼高潮臉（18+）",
                "consensual adult female climax expression with eyes tightly closed",
            ),
            (
                "arched_brow_pleasure",
                "揚眉歡愉臉（18+）",
                "consensual adult female pleasure expression with arched eyebrows",
            ),
            (
                "trembling_lips",
                "顫唇歡愉臉（18+）",
                "consensual adult female intense pleasure expression with trembling lips",
            ),
            (
                "clenched_teeth_pleasure",
                "咬緊牙關歡愉臉（18+）",
                "consensual adult female pleasure expression with gently clenched teeth",
            ),
            (
                "upward_gaze_ecstasy",
                "仰視恍惚歡愉臉（18+）",
                "consensual adult female ecstatic expression with an unfocused upward gaze",
            ),
            (
                "saliva_string_ecstasy",
                "唇間涎絲歡愉臉（18+）",
                "consensual adult female ecstatic expression with a subtle saliva strand",
            ),
            (
                "flushed_ears_pleasure",
                "耳根潮紅歡愉臉（18+）",
                "consensual adult female pleasure expression with deeply flushed ears",
            ),
            (
                "spent_smile",
                "高潮後疲憊微笑（18+）",
                "consensual adult female exhausted afterglow smile",
            ),
            (
                "pleasure_shock",
                "驟然歡愉驚愕臉（18+）",
                "consensual adult female startled expression of sudden intense pleasure",
            ),
            (
                "breathless_afterglow",
                "無力喘息餘韻臉（18+）",
                "consensual adult female breathless afterglow expression",
            ),
        )
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V5: Final[dict[str, tuple[TagOption, ...]]] = {
    "framing": _extra_options(
        ("head_shoulders", "頭肩構圖", "head-and-shoulders portrait framing"),
        ("upper_body", "上半身構圖", "upper-body character framing"),
        ("knee_up", "膝上構圖", "knee-up character portrait"),
        (
            "full_body_negative_space",
            "全身留白構圖",
            "full-body character framing with generous negative space",
        ),
        (
            "tight_full_body",
            "緊湊全身構圖",
            "tightly cropped full-body character framing",
        ),
        (
            "environmental_portrait",
            "環境式角色肖像",
            "wide environmental character portrait framing",
        ),
    ),
    "viewpoint": _extra_options(
        ("over_shoulder", "肩後視角", "over-the-shoulder character view"),
        ("first_person", "主觀視角", "first-person point-of-view shot"),
        ("oblique_birdseye", "斜向鳥瞰", "oblique bird's-eye view"),
        ("ground_level", "低貼地面視角", "ground-level character view"),
        ("mirror_reflection", "鏡面反射視角", "mirror-reflection character view"),
        ("profile_low_angle", "側面低角度", "side-profile low-angle view"),
    ),
    "character_output_purpose": _extra_options(
        ("avatar_portrait", "頭像圖示", "square avatar portrait of the same adult character"),
        (
            "facial_detail_sheet",
            "臉部細節設定表",
            "unlabeled facial-detail reference sheet for the same adult character",
        ),
        (
            "hairstyle_variant_sheet",
            "髮型變化設定表",
            "unlabeled hairstyle-variant sheet for the same adult character",
        ),
        (
            "accessory_breakdown_sheet",
            "配件拆解設定表",
            "unlabeled accessory breakdown sheet for the same adult character",
        ),
        (
            "prop_weapon_sheet",
            "道具與武器設定表",
            "unlabeled prop and weapon reference sheet for the same adult character",
        ),
        (
            "color_palette_sheet",
            "色彩配置設定表",
            "unlabeled color-palette reference sheet for the same adult character",
        ),
        (
            "outfit_front_back_sheet",
            "服裝正反面設定表",
            "unlabeled front-and-back outfit sheet for the same adult character",
        ),
        (
            "animation_model_sheet",
            "動畫模型設定表",
            "animation-ready model sheet for the same adult character",
        ),
    ),
    "character_backdrop": _extra_options(
        ("solid_color_studio", "純色背景", "solid-color studio backdrop"),
        ("soft_bokeh", "柔焦光斑", "soft bokeh-light backdrop"),
        ("radiant_sunburst", "放射光芒", "radiant sunburst backdrop"),
        ("abstract_ink_backdrop", "抽象墨暈", "abstract ink-wash backdrop"),
        ("geometric_grid", "幾何網格", "clean geometric-grid backdrop"),
        ("drifting_stardust", "星塵背景", "drifting stardust backdrop"),
        ("stage_curtain_backdrop", "舞台布幕", "theatrical curtain backdrop"),
        ("fractured_mirror", "破碎鏡面", "fractured-mirror backdrop"),
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V6: Final[dict[str, tuple[TagOption, ...]]] = {
    "pose": _extra_options(
        (
            "hand_on_chin",
            "手托下巴思考",
            "thoughtful pose with one hand resting on the chin",
        ),
        (
            "hands_clasped_front",
            "雙手身前交握",
            "composed pose with both hands clasped in front",
        ),
        ("arms_open_welcome", "張臂迎接", "welcoming pose with both arms open"),
        (
            "covering_mouth",
            "單手掩嘴",
            "expressive pose with one hand covering the mouth",
        ),
        ("palms_together", "雙掌合十", "calm pose with both palms pressed together"),
        (
            "shade_eyes",
            "手搭額前遠眺",
            "scouting pose shading the eyes with one hand",
        ),
        (
            "head_resting_on_hand",
            "頭靠手掌",
            "seated pose resting the head against one hand",
        ),
    ),
    "viewpoint": _extra_options(
        (
            "three_quarter_low_angle",
            "四分之三低角度",
            "three-quarter low-angle character view",
        ),
        (
            "three_quarter_high_angle",
            "四分之三高角度",
            "three-quarter high-angle character view",
        ),
        ("rear_low_angle", "背面低角度", "rear low-angle character view"),
        ("rear_high_angle", "背面高角度", "rear high-angle character view"),
        ("side_high_angle", "側面高角度", "side-profile high-angle view"),
        (
            "through_foreground",
            "前景框景視角",
            "character viewed through a soft foreground frame",
        ),
        (
            "shallow_water_reflection",
            "淺水倒影視角",
            "character viewed through a shallow-water reflection",
        ),
    ),
    "character_lighting": _extra_options(
        (
            "dappled_canopy",
            "林間斑駁光",
            "dappled sunlight filtered through a leafy canopy",
        ),
        (
            "water_caustics",
            "水波焦散光",
            "rippling water-caustic light across the character",
        ),
        ("sunrise_backlight", "日出逆光", "warm sunrise backlighting"),
        ("projected_pattern", "投影圖紋光", "graphic projected-pattern lighting"),
        ("dual_color_rim", "雙色輪廓光", "contrasting dual-color rim lighting"),
        ("large_softbox", "大型柔光箱", "large-softbox portrait lighting"),
        ("snow_reflection", "雪地反射光", "cool reflected-snow illumination"),
    ),
    "character_location": _extra_options(
        (
            "museum_gallery",
            "藝術博物館展廳",
            "inside a spacious art-museum gallery",
        ),
        (
            "glass_greenhouse",
            "玻璃植物溫室",
            "inside a glass-roofed botanical greenhouse",
        ),
        ("subway_platform", "地鐵月台", "on a modern subway platform"),
    ),
}


_NEW_CHARACTER_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    _multi(
        "facial_hair",
        "鬍鬚造型",
        "臉部與眼睛",
        ("clean_shaven", "光滑無鬍", "clean-shaven adult male face"),
        ("light_stubble", "淡鬍渣", "light facial stubble"),
        ("short_boxed_beard", "方框短鬍", "neatly trimmed short boxed beard"),
        ("full_beard", "濃密絡腮鬍", "full well-groomed beard"),
        ("goatee", "山羊鬍", "neatly shaped goatee"),
        ("chevron_mustache", "八字鬍", "neatly groomed chevron mustache"),
        ("long_beard", "長鬍", "long flowing beard"),
        help_text="成年男性可選填一種鬍鬚造型；不選時不會加入鬍鬚提示詞。",
        random_min=0,
        random_max=1,
        applicable_gender=CharacterGender.MALE,
        selection_max=1,
    ),
    _multi(
        "character_location",
        "所在地點",
        "畫面與風格",
        ("on_bed", "床上", "on a neatly made bed"),
        ("bedroom", "臥室", "inside a cozy bedroom"),
        ("private_room", "房間", "inside a private furnished room"),
        ("kitchen", "廚房", "inside a lived-in kitchen"),
        ("living_room", "客廳", "inside a comfortable living room"),
        ("bathroom", "浴室", "inside a tiled bathroom"),
        ("balcony", "陽台", "on a spacious balcony"),
        ("corridor", "走廊", "in a long interior corridor"),
        ("classroom", "教室", "inside an adult education classroom"),
        ("office", "辦公室", "inside a modern office"),
        ("bar", "酒吧", "inside an atmospheric bar"),
        ("cafe", "咖啡館", "inside a cozy cafe"),
        ("library", "圖書館", "inside a quiet library"),
        ("street", "街道", "on a detailed city street"),
        ("park", "公園", "in a landscaped public park"),
        ("beach", "海灘", "on a sunlit beach"),
        ("forest", "森林", "in a deep forest clearing"),
        ("arena", "競技場", "inside a monumental arena"),
        ("rooftop", "屋頂", "on a high rooftop"),
        ("garden", "花園", "in a lush garden"),
        ("workshop", "工坊", "inside a cluttered workshop"),
        ("laboratory", "實驗室", "inside a research laboratory"),
        ("train_carriage", "火車車廂", "inside a passenger train carriage"),
        ("shrine", "山中神殿", "at a quiet mountain shrine"),
        ("tavern", "酒館", "inside a rustic tavern"),
        ("cave", "洞窟", "inside a vast cave"),
        ("ruins", "遺跡", "among ancient ruins"),
        ("spacecraft", "太空船內", "inside a spacecraft"),
        ("photo_studio", "攝影棚", "inside a professional photo studio"),
        ("hot_spring", "溫泉", "beside a secluded hot spring"),
        help_text="可手動複選以表達相連場景；一鍵隨機時最多挑選一個，避免地點互相衝突。",
        random_min=0,
        random_max=1,
    ),
    _multi(
        "character_state",
        "狀態",
        "狀態",
        ("sweating", "流汗", "visible beads of sweat"),
        ("clothes_soaked", "衣服濕透", "clothes soaked through with water"),
        ("pale_face", "臉色蒼白", "visibly pale complexion"),
        ("pouting", "嘟嘴", "pouting expression"),
        ("contemptuous_face", "鄙視臉", "contemptuous facial expression"),
        ("exhausted", "疲憊不堪", "visibly exhausted posture"),
        ("trembling", "顫抖", "body trembling subtly"),
        ("rain_soaked", "雨淋", "rain-soaked hair and clothing"),
        ("muddy", "沾泥", "mud-streaked skin and clothing"),
        ("disheveled", "凌亂", "disheveled appearance"),
        ("alert", "警戒", "alert guarded body language"),
        ("dazed", "恍神", "dazed unfocused demeanor"),
        ("dusty", "沾滿灰塵", "dust-covered clothing and skin"),
        ("windblown", "風吹凌亂", "windblown hair and clothing"),
        ("snow_dusted", "覆著薄雪", "lightly snow-dusted hair and shoulders"),
        ("ash_streaked", "沾著灰燼", "ash-streaked face and clothing"),
        ("wet_hair", "頭髮濕潤", "wet hair clinging in loose strands"),
        ("flushed_skin", "臉頰潮紅", "visibly flushed cheeks"),
        ("tear_stained", "淚痕", "tear-stained cheeks"),
        ("sleepy", "睏倦", "sleepy half-focused demeanor"),
        ("breathless", "氣喘吁吁", "visibly breathless posture"),
        ("battle_worn", "歷戰狀態", "battle-worn appearance"),
        ("lightly_injured", "輕傷", "small visible scrapes and bruises"),
        ("bandaged", "包紮中", "practical visible bandages"),
        ("cold_shivering", "冷得發抖", "shivering visibly from the cold"),
        ("overheated", "熱得發昏", "visibly overheated and flushed"),
        ("magically_charged", "魔力充盈", "magically charged aura"),
        ("curse_marked", "詛咒痕跡", "visible supernatural curse marks"),
        ("glowing_skin", "肌膚發光", "subtle luminous skin"),
        ("smoke_stained", "煙燻痕跡", "smoke-stained face and clothing"),
        ("travel_worn", "旅途風塵", "travel-worn appearance"),
        ("tense", "緊繃", "tense restrained body language"),
        ("well_rested", "精神飽滿", "fresh well-rested appearance"),
        ("sleep_deprived", "睡眠不足", "visible signs of sleep deprivation"),
        ("feverish", "發燒", "feverish flushed physical state"),
        ("dizzy", "暈眩", "visibly dizzy and unsteady"),
        ("nauseated", "反胃想吐", "visibly nauseated sickly state"),
        ("coughing", "咳嗽", "caught in a brief coughing fit"),
        ("sneezing", "打噴嚏", "caught mid-sneeze"),
        ("yawning", "打哈欠", "caught mid-yawn"),
        ("waking_up", "剛睡醒", "freshly awakened groggy state"),
        ("asleep", "熟睡", "peacefully asleep"),
        ("unconscious", "失去意識", "unconscious unresponsive state"),
        ("poisoned", "中毒", "visibly poisoned sickly condition"),
        ("convalescing", "康復期", "visibly convalescing after illness or injury"),
        ("bruised", "瘀傷", "visible non-graphic bruising across exposed skin"),
        ("scratched", "抓傷", "fresh superficial scratches across exposed skin"),
        ("slight_bleeding", "輕微流血", "small non-graphic traces of fresh blood"),
        ("dehydrated", "脫水", "visibly dehydrated physical condition"),
        ("sunburned", "曬傷", "fresh sunburn across exposed skin"),
        ("sore_muscles", "肌肉痠痛", "visible muscle soreness and stiffness"),
        ("limping", "跛行", "visibly favoring one leg with a slight limp"),
        (
            "frost_crusted",
            "身上結霜",
            "thin frost crystals clinging to hair and exposed skin",
        ),
        ("salt_sprayed", "海鹽沾身", "fine sea-salt residue across hair and skin"),
        ("sand_coated", "沙塵沾身", "fine sand clinging across skin and gear"),
        ("petal_strewn", "花瓣沾身", "flower petals caught across hair and shoulders"),
        ("leaf_strewn", "落葉沾身", "dry leaves caught across hair and shoulders"),
        ("oil_stained", "油污", "dark oil smudges across hands and clothing"),
        (
            "blood_spattered",
            "血跡斑駁",
            "non-graphic blood spatters across skin and clothing",
        ),
        ("clothes_torn", "衣物破損", "visibly torn and weathered clothing"),
        ("pollen_dusted", "花粉沾身", "fine glowing pollen dust across hair and shoulders"),
        ("cobweb_tangled", "蛛網纏身", "wispy cobweb strands caught across hair and gear"),
        (
            "electrically_charged",
            "電流纏身",
            "crackling electrical energy coursing over the body",
        ),
        ("shadow_wreathed", "暗影纏身", "unstable shadow energy clinging to the body"),
        (
            "partially_petrified",
            "局部石化",
            "partial stone transformation spreading across the body",
        ),
        ("spectral_phase", "靈體化", "partially translucent spectral bodily state"),
        ("healing_aura", "治療光暈", "visible restorative magic flowing around the body"),
        ("time_frozen", "時間凝結", "body held in a visibly time-frozen state"),
        (
            "gravity_distorted",
            "重力扭曲",
            "hair and body subtly pulled by distorted gravity",
        ),
        (
            "magically_exhausted",
            "魔力耗竭",
            "visibly drained after intense magical exertion",
        ),
        (
            "flickering_invisibility",
            "隱形閃爍",
            "body flickering between visible and invisible states",
        ),
        (
            "luminous_cracks",
            "發光裂紋",
            "temporary luminous cracks tracing across the skin",
        ),
        help_text="可複選彼此相容的即時狀態；隨機最多挑選兩個並避開明顯衝突。",
        random_min=0,
        random_max=2,
    ),
    _adult_optional(
        "adult_partner_intimacy",
        "其他（18+）",
        "成人動作與表情（18+）",
        (
            "manual_partner_genitals",
            "手部刺激伴侶性器官（18+）",
            "manually stimulating the genitals of one consenting adult human partner",
        ),
        (
            "receive_manual_genital",
            "接受伴侶手部性刺激（18+）",
            "receiving manual genital stimulation from one consenting adult human partner",
        ),
        (
            "give_oral_sex",
            "為伴侶口交（18+）",
            "performing oral sex on one consenting adult human partner",
        ),
        (
            "receive_oral_sex",
            "接受伴侶口交（18+）",
            "receiving oral sex from one consenting adult human partner",
        ),
        (
            "mutual_oral_sex",
            "相互口交／69式（18+）",
            "engaging in mutual oral sex with one consenting adult human partner",
        ),
        (
            "breast_intercourse",
            "乳交（18+）",
            "participating in breast intercourse with one consenting adult human partner",
        ),
        (
            "missionary_vaginal",
            "傳教士式陰道性交（18+）",
            "participating in vaginal intercourse in the missionary position with one "
            "consenting adult human partner",
        ),
        (
            "rear_entry_vaginal",
            "後入式陰道性交（18+）",
            "participating in rear-entry vaginal intercourse with one consenting adult human "
            "partner",
        ),
        (
            "side_lying_vaginal",
            "側躺式陰道性交（18+）",
            "participating in side-lying vaginal intercourse with one consenting adult human "
            "partner",
        ),
        (
            "seated_face_to_face_vaginal",
            "面對面坐式陰道性交（18+）",
            "participating in seated face-to-face vaginal intercourse with one consenting "
            "adult human partner",
        ),
        (
            "partner_on_top_vaginal",
            "伴侶上位式陰道性交（18+）",
            "participating in vaginal intercourse with one consenting adult human partner "
            "positioned on top",
        ),
        (
            "standing_vaginal",
            "站立式陰道性交（18+）",
            "participating in standing vaginal intercourse with one consenting adult human "
            "partner",
        ),
        help_text=(
            "僅供明確成年、合意的雙人情境；請先選擇裸體或人體藝術彩繪。"
            "本類只接受手動選擇，不會由隨機功能自動加入。"
        ),
        applicable_gender=None,
        random_max=0,
    ),
    _adult_optional(
        "adult_female_masturbation_pose",
        "自慰姿勢（18+）",
        "成人動作與表情（18+）",
        (
            "reclining_open_legs",
            "仰躺張腿（18+）",
            "reclining solo-masturbation pose with her legs spread",
        ),
        (
            "side_reclining_knee_raised",
            "側躺抬膝（18+）",
            "side-reclining solo-masturbation pose with one knee raised",
        ),
        (
            "seated_legs_apart",
            "坐姿張腿（18+）",
            "seated solo-masturbation pose with her legs spread",
        ),
        (
            "seated_on_edge",
            "邊緣張腿坐姿（18+）",
            "solo-masturbation pose seated on the edge of a surface with her knees apart",
        ),
        (
            "kneeling_thighs_apart",
            "跪姿張腿（18+）",
            "kneeling solo-masturbation pose with her thighs spread",
        ),
        ("open_squat", "張腿蹲姿（18+）", "squatting solo-masturbation pose with her knees spread"),
        (
            "standing_leg_raised",
            "站姿抬腿（18+）",
            "standing solo-masturbation pose with one leg raised and supported",
        ),
        (
            "standing_against_wall",
            "靠牆站姿（18+）",
            "standing solo-masturbation pose leaning against a wall",
        ),
        (
            "all_fours_reaching_under",
            "四足跪姿（18+）",
            "solo-masturbation pose on all fours with one hand reaching beneath her body",
        ),
        (
            "lying_hips_raised",
            "仰躺抬臀（18+）",
            "solo-masturbation pose lying on her back with her hips raised",
        ),
        (
            "prone_hips_raised",
            "俯臥抬臀（18+）",
            "prone solo-masturbation pose with her hips raised",
        ),
        (
            "cross_legged_open",
            "開放盤腿坐姿（18+）",
            "cross-legged solo-masturbation pose with an open relaxed posture",
        ),
        (
            "one_knee_up_seated",
            "單膝抬起坐姿（18+）",
            "seated solo-masturbation pose with one knee raised",
        ),
        (
            "deep_kneeling_recline",
            "深跪後仰（18+）",
            "deep-kneeling solo-masturbation pose with her torso reclining backward",
        ),
        help_text="僅適用於明確成年女性；可與下方一個自慰動作組合。",
    ),
    _adult_optional(
        "adult_female_masturbation_action",
        "自慰動作（18+）",
        "成人動作與表情（18+）",
        ("touching_vulva_one_hand", "單手撫摸外陰（18+）", "touching her own vulva with one hand"),
        (
            "two_finger_clitoral_rub",
            "雙指揉弄陰蒂（18+）",
            "rubbing her own clitoris with two fingers",
        ),
        (
            "circular_clitoral_rub",
            "環狀揉弄陰蒂（18+）",
            "making slow circular motions over her own clitoris",
        ),
        (
            "one_finger_vaginal",
            "單指插入陰道（18+）",
            "stimulating her own vagina with one inserted finger",
        ),
        (
            "two_finger_vaginal",
            "雙指插入陰道（18+）",
            "stimulating her own vagina with two inserted fingers",
        ),
        (
            "three_finger_vaginal",
            "三指插入陰道（18+）",
            "stimulating her own vagina with three inserted fingers",
        ),
        (
            "dual_clitoral_vaginal",
            "陰蒂與陰道同步刺激（18+）",
            "stimulating her own clitoris and vagina simultaneously",
        ),
        (
            "breast_and_vulva",
            "一手撫胸一手撫陰（18+）",
            "touching one breast with one hand while stimulating her own vulva with the other",
        ),
        (
            "vibrator_on_clitoris",
            "震動器刺激陰蒂（18+）",
            "pressing a vibrator against her own clitoris",
        ),
        ("inserted_vibrator", "插入式震動器（18+）", "using an inserted vibrator on herself"),
        ("vaginal_dildo", "假陽具插入陰道（18+）", "using a dildo vaginally on herself"),
        ("shower_stream", "水柱刺激陰蒂（18+）", "directing a shower stream onto her own clitoris"),
        ("pillow_grinding", "磨蹭枕頭（18+）", "grinding her own vulva against a pillow"),
        ("thigh_rubbing", "夾腿磨蹭（18+）", "rubbing her thighs together for self-stimulation"),
        help_text="僅適用於明確成年女性；提示詞只保留動作語意，成年與合意界線由生成器集中加入。",
    ),
    _adult_optional(
        "adult_female_state",
        "成人生理狀態（18+）",
        "狀態",
        ("climax_tremors", "高潮全身顫動（18+）", "full-body tremors during climax"),
        ("climax_body_flush", "高潮全身潮紅（18+）", "deep full-body flush during climax"),
        (
            "climax_goosebumps",
            "高潮起雞皮疙瘩（18+）",
            "pronounced goosebumps during climax",
        ),
        (
            "climax_muscle_tension",
            "高潮肌肉緊繃（18+）",
            "visible whole-body muscle tension at climax",
        ),
        (
            "climax_breath_catch",
            "高潮屏息（18+）",
            "breath visibly caught at the peak of climax",
        ),
        (
            "squirting_climax",
            "性高潮噴水（18+）",
            "visible squirting and female ejaculation during climax",
        ),
        (
            "repeated_squirting",
            "連續噴液（18+）",
            "repeated visible squirting with female ejaculation during sustained climax",
        ),
        (
            "arousal_fluid_dripping",
            "愛液滴落（18+）",
            "visible arousal fluid dripping along her inner thighs",
        ),
        (
            "inner_thigh_wetness",
            "大腿內側濕潤（18+）",
            "fresh arousal wetness across her inner thighs",
        ),
        (
            "overstimulated_trembling",
            "過度刺激顫抖（18+）",
            "overstimulated full-body trembling",
        ),
        (
            "rhythmic_pelvic_contractions",
            "高潮骨盆律動（18+）",
            "visible rhythmic pelvic contractions during climax",
        ),
        (
            "climax_abdominal_contractions",
            "高潮腹部收縮（18+）",
            "visible abdominal contractions during climax",
        ),
        (
            "climax_leg_tremors",
            "高潮雙腿顫動（18+）",
            "pronounced leg tremors during climax",
        ),
        (
            "climax_toe_curl",
            "高潮腳趾緊繃（18+）",
            "toes visibly curling at the peak of climax",
        ),
        (
            "climax_visible_pulse",
            "高潮脈搏明顯（18+）",
            "visible quickened pulse during climax",
        ),
        (
            "climax_skin_sheen",
            "高潮肌膚水光（18+）",
            "warm luminous sheen across her skin during climax",
        ),
        (
            "arousal_skin_sheen",
            "情慾肌膚光澤（18+）",
            "subtle arousal sheen across her exposed skin",
        ),
        (
            "inner_thigh_fluid_trails",
            "大腿內側愛液痕跡（18+）",
            "thin arousal fluid trails along her inner thighs",
        ),
        (
            "post_orgasm_afterglow",
            "高潮後全身餘韻（18+）",
            "relaxed full-body post-orgasm afterglow",
        ),
        (
            "post_orgasm_trembling",
            "高潮後餘顫（18+）",
            "lingering full-body tremors after orgasm",
        ),
        (
            "post_orgasm_spent",
            "高潮後虛脫（18+）",
            "physically spent posture after orgasm",
        ),
        (
            "multiple_orgasm_exhaustion",
            "多重高潮後疲憊（18+）",
            "visible exhaustion after multiple orgasms",
        ),
        (
            "post_orgasm_weak_knees",
            "高潮後腿軟（18+）",
            "unsteady weak-kneed state after orgasm",
        ),
        (
            "post_orgasm_goosebumps",
            "高潮後雞皮疙瘩（18+）",
            "lingering goosebumps after orgasm",
        ),
        (
            "post_orgasm_sweat",
            "高潮後汗濕（18+）",
            "post-orgasm perspiration across her skin",
        ),
        (
            "post_orgasm_hypersensitivity",
            "高潮後過度敏感（18+）",
            "visibly heightened bodily sensitivity after orgasm",
        ),
        (
            "post_squirt_wetness",
            "噴液後濕潤（18+）",
            "visible residual wetness across her inner thighs after female ejaculation",
        ),
        (
            "aftercare_relaxation",
            "事後照顧放鬆（18+）",
            "calm physically relaxed aftercare state",
        ),
        (
            "post_orgasm_breathlessness",
            "高潮後呼吸未平（18+）",
            "shallow unsteady breathing after orgasm",
        ),
        (
            "post_orgasm_limp_relaxation",
            "高潮後全身鬆軟（18+）",
            "limp full-body relaxation after orgasm",
        ),
        (
            "post_orgasm_shivers",
            "高潮後輕顫（18+）",
            "gentle involuntary shivers after orgasm",
        ),
        (
            "post_orgasm_drowsiness",
            "高潮後嗜睡（18+）",
            "drowsy post-orgasm bodily state",
        ),
        (
            "lingering_body_flush",
            "高潮後軀幹潮紅（18+）",
            "lingering warmth and redness across her torso after orgasm",
        ),
        (
            "relaxed_hands_after_orgasm",
            "高潮後雙手放鬆（18+）",
            "hands and fingers loosely relaxed after orgasm",
        ),
        (
            "settling_pulse_after_orgasm",
            "高潮後脈搏漸緩（18+）",
            "visible pulse gradually settling after orgasm",
        ),
        (
            "heavy_limbs_after_orgasm",
            "高潮後四肢沉重（18+）",
            "visibly heavy relaxed limbs after orgasm",
        ),
        (
            "softened_posture_after_orgasm",
            "高潮後姿態鬆懈（18+）",
            "softened unguarded posture after orgasm",
        ),
        (
            "post_squirt_droplets",
            "噴水後水珠殘留（18+）",
            "residual droplets across her inner thighs after female ejaculation",
        ),
        (
            "post_squirt_puddle",
            "噴水後液體積聚（18+）",
            "small residual fluid pool beneath her after female ejaculation",
        ),
        (
            "cooling_skin_after_orgasm",
            "高潮後肌膚降溫（18+）",
            "post-orgasm skin gradually cooling from a lingering flush",
        ),
        help_text="僅適用於明確成年女性；高潮中與高潮後狀態互斥，後選狀態會取代同分類中衝突的先選狀態。",
        random_max=2,
        selection_max=2,
    ),
)


_BACKGROUND_OPTION_EXTENSIONS_V3: Final[dict[str, tuple[TagOption, ...]]] = {
    "color_palette": _extra_options(
        ("obsidian_amber", "黑曜石與琥珀", "obsidian and amber color palette"),
        ("lavender_mint", "薰衣草與薄荷", "lavender and mint color palette"),
        ("rust_teal", "鐵鏽與藍綠", "rust and teal color palette"),
        ("parchment_crimson", "羊皮紙與緋紅", "parchment and crimson color palette"),
        ("coral_navy", "珊瑚與深藍", "coral and navy color palette"),
        ("pearl_indigo", "珍珠與靛藍", "pearl and indigo color palette"),
    ),
    "composition": _extra_options(
        ("nested_arches", "層疊拱門引導", "nested arches guiding the composition"),
        ("layered_thresholds", "多重門檻景深", "layered thresholds creating spatial depth"),
        ("central_void", "中央留白核心", "central void anchoring the composition"),
        ("overlapping_planes", "交疊景片", "overlapping scenic planes"),
        ("reflected_axis", "倒影軸線", "reflection-balanced compositional axis"),
        ("foreground_reveal", "前景揭幕", "foreground reveal composition"),
    ),
    "camera": _extra_options(
        ("telephoto_compression", "長焦壓縮", "telephoto spatial compression"),
        ("crane_view", "吊臂俯視", "high crane-view camera angle"),
        ("threshold_view", "門檻視角", "camera positioned at a threshold"),
        ("reflection_view", "倒影取景", "scene framed through a reflection"),
        ("subsurface_view", "水面下視角", "camera viewpoint just below the water surface"),
        ("ceiling_down", "穹頂俯拍", "ceiling-down architectural viewpoint"),
    ),
    "story_details": _extra_options(
        ("unclaimed_throne", "無人王座", "an unclaimed throne"),
        ("fresh_wax_seal", "新封蠟印", "a freshly pressed wax seal"),
        ("misplaced_key", "錯置鑰匙", "a key left in the wrong place"),
        ("evacuation_marks", "撤離記號", "hurried evacuation markings"),
        ("returned_package", "退回包裹", "an unopened returned package"),
        ("silent_bell", "沉默之鐘", "a bell that has fallen silent"),
        ("two_place_settings", "雙人餐具", "two untouched place settings"),
        ("repaired_map", "拼補地圖", "a carefully repaired map"),
    ),
    "supernatural_details": _extra_options(
        ("memory_rain", "記憶之雨", "rain carrying visible memories"),
        ("upward_snow", "逆向飄雪", "snow drifting upward"),
        ("constellation_bridge", "星座橋", "a bridge formed from constellations"),
        ("echoing_doorways", "迴聲門廊", "doorways echoing into alternate spaces"),
        ("glass_tide", "玻璃潮汐", "a tide made of translucent glass"),
        ("sleeping_colossus", "沉睡巨像", "a dormant stone colossus in the landscape"),
        ("borrowed_sunlight", "借來的日光", "bottled sunlight illuminating the scene"),
        ("folded_horizon", "摺疊地平線", "a horizon folded into impossible layers"),
    ),
    "background_detail": _extra_options(
        ("ecological_detail", "生態微細節", "coherent ecological microdetail"),
        ("historical_layers", "歷史層理", "readable layers of historical detail"),
        ("wayfinding_clarity", "動線清晰度", "clear environmental wayfinding"),
        ("weathering_logic", "風化邏輯", "physically coherent weathering detail"),
        ("distance_detail", "遠景細節", "controlled long-distance environmental detail"),
    ),
}


_NEGATIVE_OPTION_EXTENSIONS_V3: Final[dict[str, tuple[TagOption, ...]]] = {
    "negative_quality": _extra_options(
        ("plastic_surfaces", "塑膠感表面", "unintended plastic-looking surfaces"),
        ("waxy_skin", "蠟像皮膚", "waxy artificial skin"),
        ("overprocessed", "過度後製", "overprocessed image"),
        ("uneven_detail", "細節密度不均", "inconsistent detail density"),
    ),
    "negative_anatomy": _extra_options(
        ("quadruped_body", "排除四足獸體", "quadruped body plan"),
        ("feral_posture", "排除野獸姿態", "feral animal posture"),
        ("ordinary_animal_head", "排除普通動物頭像", "ordinary nonsapient animal head"),
        ("nonhumanoid_proportions", "排除非人型比例", "non-humanoid body proportions"),
        ("unusable_hands", "無法持物的手", "hands unable to hold tools"),
        ("duplicated_ears", "排除多餘耳朵", "duplicated ears"),
        (
            "mixed_human_animal_ears",
            "排除人耳與獸耳同時出現",
            "simultaneous human and animal ears",
        ),
        ("mismatched_animal_ears", "排除異種耳朵", "mismatched animal ear anatomy"),
    ),
    "negative_composition": _extra_options(
        ("accidental_symmetry", "意外僵硬對稱", "unintended rigid symmetry"),
        ("ambiguous_depth", "景深關係不明", "ambiguous spatial depth"),
        ("competing_horizons", "多重衝突地平線", "competing horizon lines"),
        ("blocked_eyeline", "視線受阻", "unintentionally blocked eyeline"),
    ),
    "negative_artifacts": _extra_options(
        ("model_sheet_labels", "設定稿標示文字", "model-sheet labels"),
        ("reference_grid", "參考網格", "reference grid overlay"),
        ("selection_outline", "選取外框", "selection outline artifact"),
        ("broken_transparency", "透明背景破損", "broken transparency artifacts"),
    ),
    "negative_environment": _extra_options(
        ("feral_animals", "排除普通野獸主體", "ordinary feral animal subjects"),
        ("quadruped_crowd", "排除四足獸群", "quadruped animal crowd"),
        ("incoherent_ecology", "生態不連貫", "incoherent environmental ecology"),
        ("contradictory_era", "時代元素衝突", "contradictory historical eras"),
        ("inaccessible_paths", "無法通行的道路", "inaccessible environmental pathways"),
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V7: Final[dict[str, tuple[TagOption, ...]]] = {
    "age_impression": _extra_options(
        ("age_early_fifties", "五十歲初段成年人", "adult appearance in the early fifties"),
    ),
    "fantasy_race": _extra_options(
        ("race_changeling", "變形族", "changeling humanoid"),
        ("race_sylph", "希爾芙族", "sylph humanoid"),
        ("race_frostborn", "霜裔", "frostborn humanoid"),
    ),
    "height": _extra_options(
        ("height_below_average", "略矮", "below-average adult height"),
    ),
    "build": _extra_options(
        ("build_trim", "精實修整", "trim athletic adult build"),
        ("build_burly", "魁梧厚重", "burly adult build"),
    ),
    "body_proportions": _extra_options(
        ("proportions_broad_pelvis", "寬骨盆比例", "broad-pelvis body proportions"),
        ("proportions_narrow_hips", "窄胯比例", "narrow-hipped body proportions"),
        (
            "proportions_compact_limbs",
            "短肢比例",
            "compact-limbed adult body proportions",
        ),
    ),
    "male_chest": _extra_options(
        ("male_chest_narrow", "窄胸廓", "narrow adult male chest"),
        ("male_chest_deep", "深厚胸廓", "deep adult male chest"),
        ("male_chest_lightly_defined", "微線條胸肌", "lightly defined pectorals"),
    ),
    "waist": _extra_options(
        ("waist_broad_soft", "寬闊柔和腰身", "broad softly contoured waist"),
        ("waist_defined_obliques", "明顯腹斜肌", "clearly defined oblique muscles"),
    ),
    "legs": _extra_options(
        ("legs_tapered", "漸細腿型", "smoothly tapered legs"),
    ),
    "eye_color": _extra_options(
        ("eye_teal", "青綠色", "teal eyes"),
        ("eye_honey_gold", "蜂蜜金色", "honey-gold eyes"),
    ),
    "hair_length": _extra_options(
        ("hair_length_collarbone", "鎖骨長度", "collarbone-length hair"),
    ),
    "bangs": _extra_options(
        ("bangs_v_shaped", "V 字瀏海", "V-shaped bangs"),
        ("bangs_deep_side_part", "深側分瀏海", "deep side-parted bangs"),
    ),
    "hair_texture": _extra_options(
        ("hair_texture_matte", "柔霧髮質", "soft matte hair texture"),
        ("hair_texture_soft_wave", "柔和波紋髮質", "softly waved hair texture"),
    ),
    "expression": (
        *_extra_options(
            ("expression_puzzled", "困惑", "puzzled expression"),
            ("expression_delighted", "欣喜", "delighted expression"),
        ),
        _adult_option(
            "adult_expression_willing_desire",
            "自願渴望（18+）",
            "consensual adult openly willing intimate desire expression",
        ),
        _adult_option(
            "adult_expression_pleasured_gasp",
            "愉悅輕喘（18+）",
            "consensual adult pleasured gasping expression",
        ),
        _adult_option(
            "adult_expression_trusting_surrender",
            "信任沉醉（18+）",
            "consensual adult trusting intimate surrender expression",
        ),
    ),
    "fantasy_traits": _extra_options(
        ("fantasy_floating_crown", "懸浮魔冠", "floating arcane crown above the head"),
        (
            "fantasy_glowing_core",
            "發光魔力核心",
            "visible glowing magical core within the torso",
        ),
    ),
    "distinctive_marks": _extra_options(
        ("mark_prosthetic_eye", "精密義眼", "detailed visible prosthetic eye"),
        ("mark_calloused_hands", "粗繭雙手", "visibly weathered calloused hands"),
    ),
    "outfit_archetype": (
        *_extra_options(
            ("outfit_chef", "主廚服", "professional chef attire"),
            ("outfit_archaeologist", "考古學者裝", "rugged archaeologist field attire"),
            ("outfit_stage_musician", "舞臺樂手裝", "stylish stage-musician attire"),
        ),
        _adult_option(
            "adult_outfit_open_robe",
            "敞開親密睡袍（18+）",
            "consensual adult wearing an open intimate robe exposing bare skin",
        ),
        _adult_option(
            "adult_outfit_transparent_bodystocking",
            "透明連身網衣（18+）",
            "consensual adult wearing a transparent intimate bodystocking over bare skin",
        ),
    ),
    "outfit_materials": (
        *_extra_options(
            ("material_brocade", "織錦", "ornate brocade fabric"),
            ("material_suede", "麂皮", "soft suede details"),
        ),
        _adult_option(
            "adult_material_transparent_lace",
            "透明蕾絲（18+）",
            "consensual adult wearing transparent lace fabric revealing bare skin",
        ),
        _adult_option(
            "adult_material_transparent_mesh",
            "透明網紗（18+）",
            "consensual adult wearing transparent mesh fabric revealing bare skin",
        ),
        _adult_option(
            "adult_material_sheer_silk",
            "透膚絲綢（18+）",
            "consensual adult wearing sheer silk fabric revealing bare skin",
        ),
    ),
    "outfit_palette": (
        *_extra_options(
            ("palette_navy_cream", "海軍藍米白", "navy-and-cream clothing palette"),
            ("palette_teal_copper", "青綠銅色", "teal-and-copper clothing palette"),
            ("palette_burgundy_gray", "酒紅灰色", "burgundy-and-gray clothing palette"),
        ),
        _adult_option(
            "adult_palette_warm_flushed_skin",
            "暖紅裸膚色（18+）",
            "consensual adult warm flushed skin-tone palette",
        ),
        _adult_option(
            "adult_palette_cool_natural_skin",
            "冷調裸膚色（18+）",
            "consensual adult cool natural skin-tone palette",
        ),
        _adult_option(
            "adult_palette_metallic_body_paint",
            "金屬人體藝術色（18+）",
            "consensual adult metallic body-paint palette over bare skin",
        ),
    ),
    "accessories": (
        *_extra_options(
            ("accessory_gauntlets", "護手甲", "ornate protective gauntlets"),
            ("accessory_talisman", "護身符", "inscribed protective talisman"),
            ("accessory_spyglass", "單筒望遠鏡", "antique handheld spyglass"),
        ),
        _adult_option(
            "adult_accessory_wrist_cuffs",
            "合意情趣腕銬（18+）",
            "consensual adult intimate wrist cuffs",
        ),
    ),
    "pose": (
        *_extra_options(
            ("pose_tiptoe_reach", "踮腳伸手", "reaching upward while standing on tiptoe"),
        ),
        _adult_option(
            "adult_pose_seated_recline",
            "情慾後仰坐姿（18+）",
            "consensual adult sensual seated reclining pose",
        ),
        _adult_option(
            "adult_pose_nude_standing_profile",
            "人體藝術側身站姿（18+）",
            "consensual adult artistic nude standing profile pose",
        ),
    ),
    "framing": (
        _adult_option(
            "adult_framing_body_detail",
            "成人身體細節構圖（18+）",
            "consensual adult intimate body-detail framing",
        ),
        _adult_option(
            "adult_framing_nude_profile",
            "人體藝術側面全身構圖（18+）",
            "consensual adult artistic nude full-body profile framing",
        ),
        _adult_option(
            "adult_framing_over_shoulder",
            "成人親密回眸構圖（18+）",
            "consensual adult intimate over-the-shoulder body framing",
        ),
    ),
    "character_lighting": _extra_options(
        (
            "lighting_aurora_glow",
            "極光映照",
            "multicolored aurora glow illuminating the character",
        ),
    ),
    "character_style": _extra_options(
        ("style_colored_pencil", "彩色鉛筆", "layered colored-pencil character illustration"),
        (
            "style_woodblock_print",
            "角色木刻版畫",
            "traditional woodblock-print character illustration",
        ),
    ),
    "hair_color_pattern": _extra_options(
        ("hair_pattern_halo_dye", "光環染", "halo-dye hair color placement"),
        ("hair_pattern_marbled", "大理石紋染", "marbled multitone hair coloring"),
    ),
    "areola_size": (
        _adult_option(
            "adult_areola_very_broad",
            "極寬乳暈（18+）",
            "consensual adult female with very broad areolae",
        ),
    ),
    "areola_shape": (
        _adult_option(
            "adult_areola_elongated_oval",
            "細長橢圓乳暈（18+）",
            "consensual adult female with elongated oval areolae",
        ),
    ),
    "nipple_size": (
        _adult_option(
            "adult_nipple_very_large",
            "特大乳頭（18+）",
            "consensual adult female with very large nipples",
        ),
    ),
    "nipple_shape": (
        _adult_option(
            "adult_nipple_button_shaped",
            "珠狀乳頭（18+）",
            "consensual adult female with button-shaped nipples",
        ),
    ),
    "nipple_state": (
        _adult_option(
            "adult_nipple_partly_erect",
            "半挺立乳頭（18+）",
            "consensual adult female with partly erect nipples",
        ),
    ),
    "vulva_shape": (
        _adult_option(
            "adult_vulva_smooth_contour",
            "平滑輪廓型（18+）",
            "consensual adult female with smooth-contoured external vulva anatomy",
        ),
    ),
    "labia_shape": (
        _adult_option(
            "adult_labia_ruffled_inner",
            "波浪內唇型（18+）",
            "consensual adult female with gently ruffled inner labia",
        ),
    ),
    "adult_female_breast_hand_action": (
        _adult_option(
            "adult_breast_stroke_left",
            "單手撫過左乳（18+）",
            "consensual adult female stroking her left breast with one hand",
        ),
        _adult_option(
            "adult_breast_stroke_right",
            "單手撫過右乳（18+）",
            "consensual adult female stroking her right breast with one hand",
        ),
        _adult_option(
            "adult_breast_stroke_both",
            "雙手撫過雙乳（18+）",
            "consensual adult female stroking both breasts with both hands",
        ),
    ),
    "adult_female_breast_suckling_action": (
        _adult_option(
            "adult_nipple_nibble_left",
            "左乳頭被輕咬（18+）",
            "consensual adult female having her left nipple gently nibbled by one "
            "consenting adult human partner",
        ),
        _adult_option(
            "adult_nipple_nibble_right",
            "右乳頭被輕咬（18+）",
            "consensual adult female having her right nipple gently nibbled by one "
            "consenting adult human partner",
        ),
    ),
    "adult_female_lactation_action": (
        _adult_option(
            "adult_milk_beads_both",
            "雙乳頭凝聚乳珠（18+）",
            "consensual adult female with beads of breast milk gathering on both nipples",
        ),
        _adult_option(
            "adult_milk_trickle_chest",
            "乳汁沿胸前流淌（18+）",
            "consensual adult female with breast milk trickling down across her chest",
        ),
    ),
    "nose_shape": _extra_options(
        ("nose_narrow", "細窄鼻", "narrow nose"),
        ("nose_prominent", "高挺立體鼻", "prominent sculpted nose"),
    ),
    "facial_hair": _extra_options(
        ("facial_hair_handlebar", "翹鬍", "groomed handlebar mustache"),
    ),
    "character_location": _extra_options(
        ("location_observatory", "天文臺", "inside a domed astronomical observatory"),
        ("location_theater_stage", "劇院舞臺", "on an illuminated theater stage"),
        ("location_airship_deck", "飛空艇甲板", "on the open deck of a fantasy airship"),
    ),
    "adult_female_masturbation_pose": (
        _adult_option(
            "adult_masturbation_pose_bathtub_recline",
            "浴缸斜躺（18+）",
            "consensual adult female reclining in a solo-masturbation pose inside a bathtub "
            "with her knees apart",
        ),
        _adult_option(
            "adult_masturbation_pose_half_kneel",
            "半跪抬膝（18+）",
            "consensual adult female in a half-kneeling solo-masturbation pose with one knee "
            "raised",
        ),
    ),
    "adult_female_masturbation_action": (
        _adult_option(
            "adult_masturbation_action_palm_rub",
            "掌心按揉外陰（18+）",
            "consensual adult female pressing and rubbing her palm against her own vulva",
        ),
        _adult_option(
            "adult_masturbation_action_finger_vibrator",
            "手指與震動器同步刺激（18+）",
            "consensual adult female using vaginal finger stimulation while holding a "
            "vibrator against her own clitoris",
        ),
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V8: Final[dict[str, tuple[TagOption, ...]]] = {
    "beast_morphology_balance": _extra_options(
        (
            "beast_human_frame_species_details",
            "人型骨架配明確獸徵",
            "clearly readable species details arranged on an entirely "
            "human-proportioned bipedal frame",
        ),
    ),
    "beast_face_blend": _extra_options(
        (
            "beast_restrained_species_face_contours",
            "克制種族臉部輪廓",
            "predominantly human face with restrained species-specific contour accents "
            "and no full animal head",
        ),
        (
            "beast_human_expression_species_features",
            "人類表情配自然獸徵",
            "fully readable human expression with naturally integrated species-specific "
            "facial details and no full animal head",
        ),
    ),
    "beast_marking_pattern": _extra_options(
        (
            "beast_freckled_speckles",
            "半獸細點紋",
            "fine species-colored speckles across selected half-beast features",
        ),
        (
            "beast_ringed_accents",
            "半獸環圈紋",
            "clean ring-shaped markings on selected half-beast features",
        ),
        (
            "beast_marbled_pattern",
            "半獸雲石紋",
            "organic marbled markings across selected half-beast features",
        ),
    ),
    "beast_ear_style": _extra_options(
        (
            "beast_streamlined_hearing_profile",
            "貼合頭型種族耳部",
            "streamlined species-appropriate hearing structures following the head "
            "silhouette and replacing human ears with no human ears or duplicate ears",
        ),
    ),
    "beast_hand_style": _extra_options(
        (
            "beast_refined_species_nails",
            "種族質感人型指甲",
            "humanlike five-fingered hands with refined species-colored nails and full "
            "tool-using dexterity",
        ),
        (
            "beast_textured_human_palms",
            "種族紋理人型手掌",
            "humanlike five-fingered hands with subtle species-appropriate palm texture "
            "and full tool-using dexterity",
        ),
    ),
    "beast_foot_style": _extra_options(
        (
            "beast_refined_species_toes",
            "種族質感人型腳趾",
            "human-shaped bipedal feet with subtle species-appropriate toe details and "
            "stable human leg proportions",
        ),
        (
            "beast_textured_human_soles",
            "種族紋理人型腳底",
            "human-shaped bipedal feet with subtle species-appropriate sole texture and "
            "stable human leg proportions",
        ),
    ),
    "furry_body_covering": _extra_options(
        (
            "furry_downy_feathers",
            "絨羽覆體",
            "dense downy feather covering layered across the sapient anthropomorphic body",
        ),
        (
            "furry_short_flexible_quills",
            "柔性短棘覆體",
            "short flexible quills integrated into the sapient anthropomorphic body covering",
        ),
        (
            "furry_shell_plates",
            "層疊甲片覆體",
            "overlapping protective shell plates integrated across the sapient "
            "anthropomorphic body",
        ),
    ),
    "furry_marking_pattern": _extra_options(
        (
            "furry_ringed_distal_pattern",
            "肢尾環紋",
            "coordinated ring markings across the tail and distal limbs",
        ),
    ),
    "furry_muzzle_shape": _extra_options(
        (
            "furry_rounded_anthro_profile",
            "圓潤擬人頭部輪廓",
            "rounded species-appropriate anthropomorphic facial profile with a clearly "
            "sapient expression",
        ),
        (
            "furry_angular_anthro_profile",
            "俐落擬人頭部輪廓",
            "angular species-appropriate anthropomorphic facial profile with a clearly "
            "sapient expression",
        ),
    ),
    "furry_extremities": _extra_options(
        (
            "furry_adhesive_pads",
            "吸附趾墊手足",
            "dexterous humanoid hands and bipedal feet with species-appropriate adhesive pads",
        ),
    ),
    "furry_tail_style": _extra_options(
        (
            "furry_prehensile_tail",
            "可捲握長尾",
            "dexterous prehensile tail with a natural expressive curve",
        ),
    ),
}


_CHARACTER_OPTION_EXTENSIONS_V9: Final[dict[str, tuple[TagOption, ...]]] = {
    "legs": _extra_options(
        ("bowed_leg_line", "自然外彎腿線", "naturally bowed leg silhouette"),
        ("defined_calves", "清晰小腿線條", "clearly defined calf muscles"),
        ("tapered_ankles", "收細腳踝線條", "slender tapered ankle lines"),
        ("long_femur_proportions", "長股骨比例", "long femur-dominant leg proportions"),
    ),
    "makeup": _extra_options(
        ("pearl_accent_makeup", "珍珠點綴妝", "pearl-accented editorial makeup"),
        ("color_block_makeup", "色塊藝術妝", "bold color-block editorial makeup"),
        ("sun_kissed_makeup", "日曬暖調妝", "sun-kissed warm-toned makeup"),
        ("porcelain_doll_makeup", "瓷偶細緻妝", "delicate porcelain-doll makeup"),
    ),
    "facial_hair": _extra_options(
        ("facial_hair_circle_beard", "環形鬍", "groomed circle beard"),
        ("facial_hair_mutton_chops", "絡腮鬢角", "prominent mutton-chop sideburns"),
        ("facial_hair_soul_patch", "下唇小鬍", "neat soul-patch beard"),
        ("facial_hair_braided", "編辮鬍", "carefully braided beard"),
    ),
    "hair_color": _extra_options(
        ("ash_brown", "灰棕色", "ash-brown hair"),
        ("smoky_gray", "煙霧灰", "smoky-gray hair"),
        ("midnight_purple", "午夜紫", "midnight-purple hair"),
        ("seafoam_green", "海沫綠", "seafoam-green hair"),
    ),
    "fantasy_traits": _extra_options(
        ("crystal_antlers", "水晶鹿角", "translucent crystal antlers"),
        ("orbiting_runestones", "環繞符文石", "small rune stones orbiting the body"),
        ("porcelain_joints", "瓷質關節", "visible articulated porcelain joints"),
        (
            "constellation_hair",
            "星座流光髮",
            "living constellation lights flowing through the hair",
        ),
    ),
    "distinctive_marks": _extra_options(
        (
            "mark_stretch_lines",
            "自然伸展紋",
            "subtle natural stretch-mark lines across the skin",
        ),
        ("mark_constellation_freckles", "星座雀斑", "constellation-shaped freckle pattern"),
        ("mark_ritual_scarification", "儀式性刻痕", "intentional ceremonial scarification pattern"),
        (
            "mark_lightning_scar",
            "閃電狀疤痕",
            "branching lightning-shaped scar pattern",
        ),
    ),
    "beast_marking_pattern": _extra_options(
        (
            "beast_chevron_bands",
            "半獸折線帶紋",
            "clean chevron bands across selected half-beast features",
        ),
        (
            "beast_constellation_spots",
            "半獸星座點紋",
            "constellation-like spots across selected half-beast features",
        ),
        (
            "beast_mosaic_patches",
            "半獸鑲嵌斑塊",
            "mosaic-like color patches across selected half-beast features",
        ),
        (
            "beast_faded_edge_markings",
            "半獸邊緣羽化紋",
            "soft markings fading along the edges of selected half-beast features",
        ),
    ),
    "furry_marking_pattern": _extra_options(
        (
            "furry_dipped_extremities",
            "末端浸染紋",
            "contrasting dipped-color markings on distal limbs and tail",
        ),
        (
            "furry_dorsal_gradient",
            "背腹漸層紋",
            "smooth dorsal-to-ventral color gradient across the anthropomorphic body",
        ),
        (
            "furry_constellation_speckles",
            "星座細點紋",
            "constellation-like speckles across the anthropomorphic body covering",
        ),
        (
            "furry_iridescent_edges",
            "虹彩邊緣紋",
            "subtle iridescent edge markings along the anthropomorphic body covering",
        ),
    ),
}


_CHARACTER_CATEGORIES_V9: Final[tuple[TagCategory, ...]] = (
    _adult_optional(
        "adult_body_adornment",
        "成人身體裝飾",
        "成人身體細節（18+）",
        (
            "adult_adornment_nipple_barbells",
            "乳頭槓鈴飾品（18+）",
            "paired nipple barbell jewelry",
        ),
        (
            "adult_adornment_nipple_shields",
            "乳頭罩飾（18+）",
            "ornamental nipple-shield jewelry",
        ),
        (
            "adult_adornment_nipple_chain",
            "乳頭鍊飾（18+）",
            "delicate decorative chain linking nipple jewelry",
        ),
        (
            "adult_adornment_chest_chain",
            "胸前身體鍊（18+）",
            "ornamental body chain draped across the bare chest",
        ),
        (
            "adult_adornment_waist_chain",
            "裸腰鍊飾（18+）",
            "delicate waist chain resting against bare skin",
        ),
        (
            "adult_adornment_thigh_garters",
            "成對大腿環（18+）",
            "paired decorative garters around the bare thighs",
        ),
        (
            "adult_adornment_pelvic_tattoo",
            "骨盆線刺青（18+）",
            "ornamental tattoo following the pelvic line",
        ),
        (
            "adult_adornment_lower_abdomen_tattoo",
            "下腹刺青（18+）",
            "decorative lower-abdomen tattoo above the pubic area",
        ),
        (
            "adult_adornment_hip_tattoos",
            "成對髖側刺青（18+）",
            "symmetrical tattoos along both hips",
        ),
        (
            "adult_adornment_body_glitter",
            "裸膚亮粉（18+）",
            "fine cosmetic body glitter across exposed skin",
        ),
        (
            "adult_adornment_gold_leaf",
            "裸膚金箔貼飾（18+）",
            "delicate gold-leaf accents applied across exposed skin",
        ),
        (
            "adult_adornment_lace_applique",
            "蕾絲身體貼飾（18+）",
            "decorative lace appliques placed directly on bare skin",
        ),
        (
            "adult_adornment_body_jewels",
            "人體藝術水鑽（18+）",
            "adhesive body jewels arranged across exposed skin",
        ),
        (
            "adult_adornment_satin_ribbon_wrap",
            "緞帶纏身裝飾（18+）",
            "satin ribbons decoratively wrapped around the exposed body",
        ),
        (
            "adult_adornment_sheer_body_veil",
            "透膚身體薄紗（18+）",
            "sheer body veil draped loosely over exposed anatomy",
        ),
        (
            "adult_adornment_flower_petals",
            "花瓣人體藝術裝飾（18+）",
            "artistic flower-petal arrangement across exposed skin",
        ),
        applicable_gender=None,
        random_max=1,
        selection_max=2,
    ),
    _adult_optional(
        "adult_aftercare_action",
        "成人事後照顧",
        "成人動作與表情（18+）",
        (
            "adult_aftercare_cuddling",
            "親密相擁休息（18+）",
            "resting in a close affectionate cuddle with one consenting adult human partner "
            "after intimacy",
        ),
        (
            "adult_aftercare_blanket",
            "為伴侶蓋上毯子（18+）",
            "gently covering one consenting adult human partner with a soft blanket after intimacy",
        ),
        (
            "adult_aftercare_water",
            "遞水照顧伴侶（18+）",
            "offering a glass of water to one consenting adult human partner after intimacy",
        ),
        (
            "adult_aftercare_forehead_kiss",
            "事後額頭親吻（18+）",
            "giving one consenting adult human partner a reassuring forehead kiss after intimacy",
        ),
        (
            "adult_aftercare_hair_stroking",
            "事後輕撫髮絲（18+）",
            "gently stroking one consenting adult human partner's hair after intimacy",
        ),
        (
            "adult_aftercare_hand_holding",
            "事後十指相扣（18+）",
            "holding one consenting adult human partner's hand with fingers interlaced after "
            "intimacy",
        ),
        (
            "adult_aftercare_shoulder_neck_massage",
            "事後肩頸按摩（18+）",
            "giving one consenting adult human partner a gentle shoulder-and-neck massage after "
            "intimacy",
        ),
        (
            "adult_aftercare_wiping_sweat",
            "替伴侶擦拭汗水（18+）",
            "tenderly wiping perspiration from one consenting adult human partner after intimacy",
        ),
        (
            "adult_aftercare_shared_bath",
            "事後共浴（18+）",
            "sharing a calm cleansing bath with one consenting adult human partner after intimacy",
        ),
        (
            "adult_aftercare_warm_towel",
            "遞上溫熱毛巾（18+）",
            "offering a warm towel to one consenting adult human partner after intimacy",
        ),
        (
            "adult_aftercare_head_on_chest",
            "依偎胸前休息（18+）",
            "resting one's head against one consenting adult human partner's chest after intimacy",
        ),
        (
            "adult_aftercare_spooning",
            "事後側躺相擁（18+）",
            "relaxing in an affectionate spooning embrace with one consenting adult human partner "
            "after intimacy",
        ),
        (
            "adult_aftercare_check_in",
            "溫柔確認伴侶狀態（18+）",
            "making caring eye contact while checking on one consenting adult human partner "
            "after intimacy",
        ),
        (
            "adult_aftercare_slow_breathing",
            "相擁調整呼吸（18+）",
            "breathing slowly in a close embrace with one consenting adult human partner after "
            "intimacy",
        ),
        (
            "adult_aftercare_lap_rest",
            "枕在伴侶腿上休息（18+）",
            "resting comfortably with one's head in one consenting adult human partner's lap "
            "after intimacy",
        ),
        (
            "adult_aftercare_gentle_smile",
            "事後相視微笑（18+）",
            "exchanging gentle smiles and eye contact with one consenting adult human partner "
            "after intimacy",
        ),
        applicable_gender=None,
        random_max=1,
        selection_max=2,
    ),
)


_CHARACTER_OPTION_EXTENSIONS_V10: Final[dict[str, tuple[TagOption, ...]]] = {
    "accessories": tuple(
        _adult_option(*value)
        for value in (
            (
                "adult_accessory_ankle_cuffs",
                "合意情趣腳銬（18+）",
                "intimate ankle cuffs",
            ),
            (
                "adult_accessory_collar_leash",
                "合意項圈牽繩（18+）",
                "intimate collar-and-leash accessory",
            ),
            (
                "adult_accessory_restraint_ribbons",
                "合意束縛緞帶（18+）",
                "decorative satin restraint ribbons",
            ),
            (
                "adult_accessory_nipple_clamps",
                "乳夾飾品（18+）",
                "ornamental nipple-clamp jewelry",
            ),
        )
    ),
    "beast_ear_style": _extra_options(
        (
            "folded_tip",
            "折尖獸耳",
            "folded-tip species ears replacing human ears with no human ears or duplicate ears",
        ),
        (
            "forward_cupped",
            "前傾聚音獸耳",
            "forward-cupped species ears replacing human ears with no human ears or duplicate "
            "ears",
        ),
        (
            "broad_fan",
            "扇形寬耳",
            "broad fan-shaped species ears replacing human ears with no human ears or duplicate "
            "ears",
        ),
        (
            "notched_edge",
            "缺口獸耳",
            "naturally notched species ears replacing human ears with no human ears or duplicate "
            "ears",
        ),
    ),
    "beast_tail_style": _extra_options(
        ("prehensile", "半獸可捲握尾", "one dexterous prehensile half-beast tail"),
        ("paddle", "半獸槳狀寬尾", "one broad paddle-shaped half-beast tail"),
        ("segmented_armor", "半獸節甲尾", "one segmented armor-plated half-beast tail"),
        ("balancing", "半獸平衡長尾", "one long counterbalancing half-beast tail"),
    ),
    "body_proportions": _extra_options(
        ("short_arms", "短臂比例", "short-armed body proportions"),
        ("long_neck", "修長頸部比例", "long-necked body proportions"),
        ("large_head_ratio", "大頭身比例", "large head-to-body proportion"),
        ("small_head_ratio", "小頭身比例", "small head-to-body proportion"),
    ),
    "breast_shape": _extra_options(
        ("upper_fullness", "上緣飽滿型", "upper-full adult breast shape"),
        ("lower_fullness", "下緣飽滿型", "lower-full adult breast shape"),
        ("tubular", "管狀型", "tubular adult breast shape"),
        ("narrow_root", "窄底座型", "narrow-root adult breast shape"),
    ),
    "character_backdrop": _extra_options(
        ("technical_blueprint", "技術藍圖背景", "technical blueprint backdrop"),
        ("parchment_collage", "羊皮紙拼貼", "layered torn-parchment collage backdrop"),
        ("neon_city_blur", "霓虹城市柔焦", "soft-focus neon cityscape backdrop"),
        ("sunlit_leaf_shadows", "日照葉影背景", "sunlit leaf-shadow backdrop"),
    ),
    "expression": (
        *_extra_options(
            ("contemptuous", "輕蔑", "contemptuous expression"),
            ("anxious", "焦慮不安", "anxious expression"),
            ("jealous", "嫉妒", "jealous expression"),
            ("solemn", "莊重肅穆", "solemn expression"),
        ),
        _adult_option(
            "adult_expression_sultry_side_glance",
            "親密側眸（18+）",
            "consensual adult sultry sidelong glance",
        ),
        _adult_option(
            "adult_expression_breathless_smirk",
            "喘息微笑（18+）",
            "consensual adult breathless intimate smirk",
        ),
        _adult_option(
            "adult_expression_heated_focus",
            "熾熱專注（18+）",
            "consensual adult intensely focused intimate expression",
        ),
        _adult_option(
            "adult_expression_playful_invitation",
            "俏皮邀請神情（18+）",
            "consensual adult playfully inviting intimate expression",
        ),
    ),
    "eye_color": _extra_options(
        ("aquamarine", "海水藍綠", "aquamarine eyes"),
        ("smoky_quartz", "煙晶棕灰", "smoky-quartz eyes"),
        ("moonstone_blue", "月光石白藍", "moonstone-blue eyes"),
        ("garnet_red", "深石榴紅", "garnet-red eyes"),
    ),
    "eyewear": _extra_options(
        ("pince_nez", "夾鼻眼鏡", "ornate pince-nez glasses"),
        ("hexagonal_frame", "六角框眼鏡", "hexagonal-frame glasses"),
        ("wraparound_sport", "運動環繞式眼鏡", "wraparound sport glasses"),
        ("slit_snow_goggles", "狹縫雪地護目鏡", "traditional slit snow goggles"),
    ),
    "fantasy_traits": _extra_options(
        ("fantasy_halo_shards", "懸浮光環碎片", "floating luminous halo fragments"),
        ("fantasy_mirror_skin", "鏡面肌膚", "mirrorlike reflective skin"),
        (
            "fantasy_living_ink",
            "活體墨紋",
            "living ink markings moving across the skin",
        ),
        (
            "fantasy_orbiting_planets",
            "微型行星環",
            "miniature planets orbiting the body",
        ),
    ),
    "framing": tuple(
        _adult_option(*value)
        for value in (
            (
                "adult_framing_lower_body_detail",
                "成人下身細節構圖（18+）",
                "intimate lower-body detail framing",
            ),
            (
                "adult_framing_reclining_full_body",
                "成人斜躺全身構圖（18+）",
                "intimate reclining full-body framing",
            ),
            (
                "adult_framing_mirror_body",
                "成人鏡面人體藝術構圖（18+）",
                "intimate mirror-reflected body framing",
            ),
            (
                "adult_framing_partner_two_shot",
                "成人伴侶雙人構圖（18+）",
                "intimate two-shot framing with one consenting adult human partner",
            ),
        )
    ),
    "furry_extremities": _extra_options(
        (
            "opposable_paw_hands",
            "可對握肉球手",
            "dexterous paw-hands with opposable thumbs and soft pads",
        ),
        (
            "feathered_talons",
            "羽覆利爪手足",
            "feathered humanoid hands and feet with controlled talons",
        ),
        (
            "fin_webbed_extremities",
            "鰭緣蹼手足",
            "webbed humanoid hands and feet with streamlined fin edges",
        ),
        (
            "chitin_pincer_hands",
            "幾丁質螯手",
            "articulated humanoid hands ending in controlled chitin pincers",
        ),
    ),
    "furry_tail_style": _extra_options(
        ("paddle_tail", "福瑞槳狀寬尾", "broad paddle-shaped anthropomorphic tail"),
        (
            "segmented_armor_tail",
            "福瑞節甲長尾",
            "segmented armor-plated anthropomorphic tail",
        ),
        (
            "aquatic_fluke_tail",
            "水平水生尾鰭",
            "broad horizontal aquatic tail fluke",
        ),
        ("balancing_tail", "福瑞平衡長尾", "long counterbalancing anthropomorphic tail"),
    ),
    "hair_color": _extra_options(
        ("slate_blue", "石板藍", "slate-blue hair"),
        ("champagne_blonde", "香檳金", "champagne-blonde hair"),
        ("mauve", "灰紫色", "mauve hair"),
        ("forest_green", "森林深綠", "forest-green hair"),
    ),
    "hair_color_pattern": _extra_options(
        ("horizontal_bands", "橫向環帶染", "horizontal banded hair-color placement"),
        (
            "checkerboard_panels",
            "棋盤格分區染",
            "checkerboard-panel hair coloring",
        ),
        (
            "constellation_speckles",
            "星點潑染",
            "constellation-speckled hair coloring",
        ),
        ("flame_sections", "火焰分區染", "flame-shaped multicolor hair placement"),
    ),
    "hair_style": _extra_options(
        ("textured_pixie", "層次精靈短剪", "textured pixie-cut hairstyle"),
        ("structured_hime_cut", "姬髮式", "structured hime-cut hairstyle"),
        ("braided_mohawk", "編辮莫霍克", "braided mohawk hairstyle"),
        ("low_chignon", "法式低髻", "classic low chignon hairstyle"),
    ),
    "facial_hair": _extra_options(
        ("anchor_beard", "錨形鬍", "groomed anchor beard"),
        ("ducktail_beard", "鴨尾鬍", "tapered ducktail beard"),
        ("pencil_mustache", "鉛筆小鬍", "fine pencil mustache"),
        ("horseshoe_mustache", "馬蹄鬍", "bold horseshoe mustache"),
    ),
    "height": _extra_options(
        ("palm_sized_fantasy", "掌上微型成人身高", "palm-sized adult fantasy stature"),
        ("knee_high_fantasy", "膝高型成人身高", "knee-high adult fantasy stature"),
        (
            "double_human_fantasy",
            "雙倍人高巨人體型",
            "adult fantasy stature roughly twice human height",
        ),
        (
            "building_scale_giant",
            "建築級巨人體型",
            "building-scale adult giant stature",
        ),
    ),
    "legs": _extra_options(
        ("full_calves", "飽滿小腿", "full rounded calf contours"),
        ("columnar", "筆直柱狀腿", "straight columnar leg silhouette"),
        ("high_set_knees", "高膝位比例", "high-set knee proportions"),
        ("low_set_knees", "低膝位比例", "low-set knee proportions"),
    ),
    "lips": _extra_options(
        ("full_lower_lip", "下唇較豐", "full lower lip with a slimmer upper lip"),
        ("full_upper_lip", "上唇較豐", "full upper lip with a slimmer lower lip"),
        ("slightly_parted", "微啟唇", "slightly parted lips"),
        ("firmly_pressed", "緊抿唇", "firmly pressed lips"),
    ),
    "skin_tone": _extra_options(
        ("warm_sienna", "暖赭棕膚色", "warm sienna-brown skin"),
        ("cool_taupe", "冷灰褐膚色", "cool taupe-brown skin"),
        ("deep_plum", "深梅紫奇幻膚色", "deep plum-purple fantasy skin"),
        ("celadon", "青瓷綠奇幻膚色", "pale celadon-green fantasy skin"),
    ),
    "adult_female_breast_hand_action": tuple(
        _adult_option(*value)
        for value in (
            (
                "adult_breast_trace_left_areola",
                "指尖繞畫左乳暈（18+）",
                "tracing slow circles around the areola of her left breast with one fingertip",
            ),
            (
                "adult_breast_trace_right_areola",
                "指尖繞畫右乳暈（18+）",
                "tracing slow circles around the areola of her right breast with one fingertip",
            ),
            (
                "adult_breast_trace_both_areolae",
                "雙手繞畫雙側乳暈（18+）",
                "tracing slow circles around both breast areolae with one fingertip on each side",
            ),
            (
                "adult_breast_trace_cleavage",
                "指尖沿乳溝滑過（18+）",
                "tracing one fingertip slowly along the cleavage between her breasts",
            ),
        )
    ),
    "adult_female_breast_suckling_action": tuple(
        _adult_option(*value)
        for value in (
            (
                "adult_areola_tongue_circle_left",
                "左乳暈被舌尖繞圈（18+）",
                "having her left areola traced in slow circles by one consenting adult human "
                "partner using their tongue",
            ),
            (
                "adult_areola_tongue_circle_right",
                "右乳暈被舌尖繞圈（18+）",
                "having her right areola traced in slow circles by one consenting adult human "
                "partner using their tongue",
            ),
            (
                "adult_cleavage_kiss",
                "乳溝被輕吻（18+）",
                "having her cleavage gently kissed by one consenting adult human partner",
            ),
            (
                "adult_alternating_breast_suckle",
                "雙乳被交替吸吮（18+）",
                "having both breasts alternately suckled by one consenting adult human partner",
            ),
        )
    ),
    "adult_partner_intimacy": tuple(
        _adult_option(*value)
        for value in (
            (
                "mutual_manual_genital",
                "相互手部刺激性器官（18+）",
                "engaging in mutual manual genital stimulation with one consenting adult human "
                "partner",
            ),
            (
                "mutual_genital_rubbing",
                "相互性器官磨蹭（18+）",
                "engaging in mutual genital rubbing without penetration with one consenting "
                "adult human partner",
            ),
            (
                "receptive_anal_intercourse",
                "作為被插入方進行肛門性交（18+）",
                "participating in receptive anal intercourse with one consenting adult human "
                "partner",
            ),
            (
                "insertive_anal_intercourse",
                "作為插入方進行肛門性交（18+）",
                "participating in insertive anal intercourse with one consenting adult human "
                "partner",
            ),
        )
    ),
}


_BACKGROUND_OPTION_EXTENSIONS_V4: Final[dict[str, tuple[TagOption, ...]]] = {
    "location": _extra_options(
        ("opera_house", "歷史歌劇院", "ornate historic opera house"),
        ("lighthouse_headland", "燈塔岬角", "remote lighthouse on a rocky headland"),
        ("catacombs", "地下墓穴", "labyrinthine underground catacombs"),
    ),
    "spatial_scale": _extra_options(
        ("building_complex", "建築群尺度", "building-complex-scale environment"),
        ("continental", "大陸尺度", "continental-scale vista"),
    ),
    "architecture": _extra_options(
        ("mughal", "蒙兀兒式", "Mughal architecture"),
        ("nordic_stave", "北歐木構教堂式", "Nordic stave-church architecture"),
        ("mesoamerican", "中部美洲古文明式", "Mesoamerican monumental architecture"),
    ),
    "environment_materials": _extra_options(
        ("rammed_earth", "夯土", "rammed-earth construction"),
    ),
    "terrain": _extra_options(
        ("archipelago", "島嶼群", "island-archipelago terrain"),
        ("karst_towers", "喀斯特峰林", "karst tower landscape"),
        ("lava_field", "熔岩原", "hardened lava-field terrain"),
    ),
    "season": _extra_options(
        ("midsummer", "盛夏", "midsummer season"),
        ("late_autumn", "深秋", "late autumn season"),
        ("snowmelt_season", "融雪季", "snowmelt season"),
    ),
    "time_of_day": _extra_options(
        ("sunrise", "日出時刻", "sunrise breaking over the horizon"),
        ("sunset", "日落時刻", "sunset at the horizon"),
        ("moonrise", "月升時刻", "moonrise over the landscape"),
    ),
    "weather": _extra_options(
        ("thunderstorm", "雷雨", "thunderstorm with driving rain"),
        ("freezing_fog", "凍霧", "freezing fog conditions"),
    ),
    "atmosphere": _extra_options(
        ("suspended_expectation", "屏息期待", "breathless anticipatory atmosphere"),
        ("eerie_stillness", "幽森靜謐", "eerie still atmosphere"),
        ("contemplative", "沉靜思索", "contemplative atmosphere"),
    ),
    "background_lighting": _extra_options(
        ("overcast_skylight", "陰天頂光", "soft overcast skylight"),
        ("gaslight", "煤氣燈光", "warm gaslight illumination"),
    ),
    "composition": _extra_options(
        ("layered_silhouettes", "剪影層疊構圖", "layered-silhouette composition"),
    ),
    "camera": _extra_options(
        (
            "first_person_environment",
            "第一人稱環境視角",
            "first-person environmental viewpoint",
        ),
    ),
    "story_details": _extra_options(
        ("chalk_countdown", "粉筆倒數記號", "a chalk countdown written on a wall"),
        ("wet_umbrella", "滴水雨傘", "one wet umbrella left dripping"),
        ("steaming_tea", "尚有熱氣的茶", "a cup of tea still steaming"),
    ),
    "supernatural_details": _extra_options(
        (
            "inverted_sky_city",
            "倒懸天空城",
            "an upside-down city suspended in the sky",
        ),
        (
            "ground_starlight_river",
            "星河流過地表",
            "a river of starlight flowing across the ground",
        ),
    ),
    "background_style": _extra_options(
        ("charcoal_environment", "炭筆景觀", "charcoal-drawn environment art"),
        ("risograph_environment", "孔版套色印刷風", "risograph environment illustration"),
        ("mosaic_environment", "馬賽克鑲嵌畫", "mosaic-tile environment artwork"),
    ),
    "rendering": _extra_options(
        ("vector_environment", "向量圖渲染", "clean vector environment rendering"),
        ("clay_environment", "黏土模型渲染", "clay-model environment rendering"),
        (
            "engraved_line_environment",
            "雕版線刻渲染",
            "fine engraved-line environment rendering",
        ),
    ),
    "background_detail": _extra_options(
        (
            "structural_clarity",
            "結構清晰度",
            "structurally coherent environmental detail",
        ),
        (
            "storytelling_focus",
            "敘事重點細節",
            "selective story-focused environmental detail",
        ),
    ),
}


_BACKGROUND_OPTION_EXTENSIONS_V5: Final[dict[str, tuple[TagOption, ...]]] = {
    "spatial_scale": _extra_options(
        ("room_corner_micro", "房間角落尺度", "small room-corner-scale environment"),
        ("village", "村落尺度", "village-scale environment"),
        ("regional_valley", "區域山谷尺度", "regional valley-scale vista"),
        (
            "interior_megastructure",
            "巨構內部尺度",
            "vast interior-megastructure-scale environment",
        ),
    ),
    "season": _extra_options(
        ("late_winter", "冬末", "late-winter season"),
        ("early_summer", "初夏", "early-summer season"),
        ("harvest_season", "收穫季", "harvest season"),
        ("storm_season", "風暴季", "season of recurring storms"),
    ),
    "population": _extra_options(
        ("night_market_crowd", "夜市人潮", "dense night-market crowd"),
        ("festival_dancers", "節慶舞者群", "group of festival dancers"),
        ("dock_workers", "碼頭工人群", "adult dock workers loading cargo"),
        ("distant_rescue_team", "遠方救援隊", "distant adult rescue team"),
    ),
    "supernatural_details": _extra_options(
        (
            "walking_reflections",
            "脫離本體的倒影",
            "reflections walking independently of their owners",
        ),
        ("floating_doorways", "漂浮門扉群", "freestanding doorways floating in midair"),
        ("aurora_roots", "極光樹根", "luminous aurora-like roots spreading through the sky"),
        ("whispering_statues", "低語雕像群", "ancient statues visibly whispering to one another"),
    ),
    "background_detail": _extra_options(
        ("foreground_readability", "前景層次清晰", "clearly readable foreground layering"),
        ("midground_story_density", "中景敘事密度", "story-rich midground detail density"),
        (
            "distance_atmospheric_detail",
            "遠距空氣透視層次",
            "graduated atmospheric perspective separating distant depth layers",
        ),
        (
            "functional_prop_logic",
            "道具功能邏輯",
            "functionally coherent environmental prop detail",
        ),
    ),
}


_BACKGROUND_OPTION_EXTENSIONS_V6: Final[dict[str, tuple[TagOption, ...]]] = {
    "environment_materials": _extra_options(
        ("cork_panels", "軟木拼板", "layered cork-panel surfaces"),
        ("terrazzo", "水磨石", "speckled terrazzo surfaces"),
        (
            "polymer_panels",
            "聚合物板材",
            "translucent polymer panel structures",
        ),
        ("carbon_fiber", "碳纖維", "woven carbon-fiber structures"),
    ),
    "background_lighting": _extra_options(
        (
            "sodium_vapor",
            "鈉氣燈光",
            "amber sodium-vapor street lighting",
        ),
        (
            "fluorescent_panels",
            "冷白螢光燈",
            "cold fluorescent panel lighting",
        ),
        (
            "string_lights",
            "串燈光影",
            "warm overhead string-light illumination",
        ),
        (
            "high_floodlights",
            "高架泛光燈",
            "broad high-mounted floodlight illumination",
        ),
    ),
    "composition": _extra_options(
        ("l_shape", "L 型構圖", "strong L-shaped compositional balance"),
        ("cross_axis", "十字軸線構圖", "cross-axis composition"),
        (
            "repeating_grid",
            "重複網格構圖",
            "rhythmic repeating-grid composition",
        ),
        (
            "low_horizon",
            "低地平線構圖",
            "low-horizon composition emphasizing the sky",
        ),
    ),
    "population": _extra_options(
        (
            "morning_commuters",
            "晨間通勤人潮",
            "stream of distant adult morning commuters",
        ),
        (
            "terrace_farmers",
            "梯田農作者",
            "scattered adult farmers working across terraces",
        ),
        (
            "shoreline_fishers",
            "岸邊漁人群",
            "small groups of adult fishers along the shoreline",
        ),
        (
            "restoration_crew",
            "古蹟修復團隊",
            "adult restoration crew repairing an old structure",
        ),
    ),
    "supernatural_details": _extra_options(
        (
            "unseen_colossus_tracks",
            "無形巨像足跡",
            "colossal footprints forming beneath an unseen traveler",
        ),
        (
            "breathing_mountains",
            "呼吸山脈",
            "mountain ranges visibly breathing in slow rhythms",
        ),
        (
            "living_murals",
            "壁畫生物出走",
            "painted creatures stepping out of ancient wall murals",
        ),
        (
            "seasonal_patchwork",
            "四季拼接地貌",
            "adjacent landscape patches displaying four different seasons",
        ),
    ),
    "background_style": _extra_options(
        (
            "fresco_environment",
            "濕壁畫景觀",
            "fresco-mural environment painting",
        ),
        (
            "embroidered_tapestry",
            "刺繡掛毯景觀",
            "embroidered tapestry environment artwork",
        ),
        (
            "clay_animation",
            "黏土動畫場景",
            "handcrafted clay-animation environment",
        ),
        (
            "pointillist_environment",
            "點描派景觀",
            "pointillist environment painting",
        ),
    ),
}


_NEGATIVE_OPTION_EXTENSIONS_V4: Final[dict[str, tuple[TagOption, ...]]] = {
    "negative_quality": _extra_options(
        ("moire_interference", "摩爾干涉紋", "unwanted moire interference patterns"),
        ("dirty_lens_smears", "鏡頭污痕", "unwanted dirty-lens smears"),
    ),
    "negative_composition": _extra_options(
        ("unbalanced_visual_weight", "視覺重心失衡", "unbalanced visual weight"),
        (
            "merged_subject_silhouettes",
            "主體輪廓黏連",
            "unintentionally merged subject silhouettes",
        ),
    ),
    "negative_artifacts": _extra_options(
        ("subtitle_artifact", "錯誤字幕殘留", "unwanted subtitle text"),
        ("crop_mark_artifact", "錯誤裁切記號", "unwanted crop marks"),
        ("color_swatch_artifact", "錯誤色票覆蓋", "unwanted color-swatch overlays"),
    ),
    "negative_environment": _extra_options(
        (
            "intersecting_environment_geometry",
            "場景物件穿模",
            "intersecting environmental geometry",
        ),
    ),
}


_NEGATIVE_OPTION_EXTENSIONS_V5: Final[dict[str, tuple[TagOption, ...]]] = {
    "negative_quality": _extra_options(
        ("muddy_microcontrast", "微對比混濁", "muddy local microcontrast"),
        (
            "shadow_hue_contamination",
            "陰影色偏污染",
            "unwanted hue contamination in shadow regions",
        ),
        ("uneven_texture_sharpness", "材質銳度不一致", "inconsistent texture sharpness"),
        (
            "edge_ghosting",
            "邊緣雙影",
            "faint duplicated ghost contours around object edges",
        ),
    ),
    "negative_composition": _extra_options(
        (
            "accidental_edge_tension",
            "主體緊貼畫面邊緣",
            "accidental subject tension against the frame edge",
        ),
        (
            "accidental_subject_occlusion",
            "次要主體意外遮擋",
            "unintentional occlusion of the primary subject by another subject",
        ),
        (
            "empty_leading_space",
            "視線方向留白錯置",
            "empty leading space placed opposite the subject gaze",
        ),
        (
            "missing_scale_reference",
            "缺少尺度參照",
            "missing reliable scale reference between subjects and environment",
        ),
    ),
}


_NEGATIVE_OPTION_EXTENSIONS_V6: Final[dict[str, tuple[TagOption, ...]]] = {
    "negative_quality": _extra_options(
        (
            "specular_bloom_bleed",
            "高光泛白溢出",
            "uncontrolled specular bloom bleeding into nearby details",
        ),
        (
            "microdetail_ringing",
            "微細節振鈴邊",
            "ringing contours around fine image details",
        ),
        (
            "local_contrast_clipping",
            "局部對比截斷",
            "clipped local contrast with lost highlight and shadow detail",
        ),
        (
            "denoise_texture_smearing",
            "降噪材質抹除",
            "over-smoothed texture smearing from excessive denoising",
        ),
    ),
    "negative_composition": _extra_options(
        (
            "cramped_subject_spacing",
            "主體間距過度擁擠",
            "cramped spacing between intended focal subjects",
        ),
        (
            "contradictory_eyelines",
            "角色視線關係矛盾",
            "contradictory eyeline directions between interacting subjects",
        ),
        (
            "trapped_negative_space",
            "封閉式尷尬留白",
            "awkward enclosed pockets of negative space",
        ),
        (
            "depth_layer_collision",
            "景深層次相互衝突",
            "overlapping depth layers with unclear visual ordering",
        ),
    ),
    "negative_artifacts": _extra_options(
        ("cursor_artifact", "滑鼠游標殘留", "visible mouse cursor artifact"),
        (
            "loading_spinner_artifact",
            "載入圖示殘留",
            "unwanted loading-spinner overlay",
        ),
        (
            "bounding_box_artifact",
            "辨識框線殘留",
            "unwanted object-detection bounding boxes",
        ),
        (
            "mask_edge_artifact",
            "遮罩邊界殘留",
            "visible editing-mask boundary artifacts",
        ),
    ),
    "negative_environment": _extra_options(
        (
            "misaligned_surface_tiles",
            "牆地磚縫錯位",
            "misaligned floor or wall tile boundaries",
        ),
        (
            "unsupported_balconies",
            "懸空無支撐陽台",
            "physically unsupported architectural balconies",
        ),
        (
            "broken_doorway_scale",
            "門框尺度異常",
            "inconsistent doorway scale within the same environment",
        ),
        (
            "reflection_world_mismatch",
            "反射場景世界不一致",
            "reflections showing a contradictory surrounding environment",
        ),
    ),
}


def _extend_categories(
    categories: Sequence[TagCategory],
    extensions: Mapping[str, Sequence[TagOption]],
) -> tuple[TagCategory, ...]:
    known = {category.key for category in categories}
    unknown = sorted(set(extensions) - known)
    if unknown:
        raise ValueError(f"擴充選項引用不存在的分類：{', '.join(unknown)}")
    extended: list[TagCategory] = []
    for category in categories:
        options = (*category.options, *extensions.get(category.key, ()))
        if category.key == "distinctive_marks":
            options = tuple(
                option for option in options if option.key not in _LEGACY_EYEWEAR_MARK_KEYS
            )
        if category.key == "fantasy_race":
            options = tuple(
                option for option in options if option.key not in _ANIMAL_LINEAGE_RACE_KEYS
            )
        extended.append(
            TagCategory(
                key=category.key,
                label_zh=category.label_zh,
                group=category.group,
                options=options,
                selection_mode=category.selection_mode,
                help_text=category.help_text,
                default_keys=category.default_keys,
                random_min=category.random_min,
                random_max=category.random_max,
                applicable_gender=category.applicable_gender,
                selection_max=category.selection_max,
            )
        )
    return tuple(extended)


_FEMALE_BUST_ORDER: Final[tuple[str, ...]] = (
    "extremely_large",
    "very_large",
    "large",
    "full_medium",
    "medium",
    "modest",
    "small",
    "flat",
)


def _order_character_categories(
    categories: Sequence[TagCategory],
) -> tuple[TagCategory, ...]:
    """Apply author-facing semantic ordering after option extensions merge."""

    result: list[TagCategory] = []
    for category in categories:
        if category.key != "female_bust":
            result.append(category)
            continue
        by_key = {option.key: option for option in category.options}
        if set(by_key) != set(_FEMALE_BUST_ORDER):
            raise ValueError("胸部尺寸排序表必須完整涵蓋 female_bust 選項")
        result.append(
            TagCategory(
                key=category.key,
                label_zh=category.label_zh,
                group=category.group,
                options=tuple(by_key[key] for key in _FEMALE_BUST_ORDER),
                selection_mode=category.selection_mode,
                help_text=category.help_text,
                default_keys=category.default_keys,
                random_min=category.random_min,
                random_max=category.random_max,
                applicable_gender=category.applicable_gender,
                selection_max=category.selection_max,
            )
        )
    return tuple(result)


def filter_adult_options(
    categories: Sequence[TagCategory],
    include_adult: bool = False,
) -> tuple[TagCategory, ...]:
    """Return a catalog with adult-only options hidden unless explicitly enabled."""

    if include_adult:
        return tuple(categories)
    filtered: list[TagCategory] = []
    for category in categories:
        options = tuple(option for option in category.options if not option.adult_only)
        if not options:
            continue
        defaults = tuple(key for key in category.default_keys if key in {o.key for o in options})
        random_max = min(category.random_max, len(options))
        random_min = min(category.random_min, random_max)
        filtered.append(
            TagCategory(
                key=category.key,
                label_zh=category.label_zh,
                group=category.group,
                options=options,
                selection_mode=category.selection_mode,
                help_text=category.help_text,
                default_keys=defaults,
                random_min=random_min,
                random_max=random_max,
                applicable_gender=category.applicable_gender,
                selection_max=(
                    min(category.selection_max, len(options))
                    if category.selection_max is not None
                    else None
                ),
            )
        )
    return tuple(filtered)


CHARACTER_CATEGORIES: Final[tuple[TagCategory, ...]] = _extend_categories(
    _extend_categories(
        _extend_categories(
            _extend_categories(
                _extend_categories(
                    _extend_categories(
                        _extend_categories(
                            (
                                *_order_character_categories(
                                    _extend_categories(
                                        _extend_categories(
                                            _extend_categories(
                                                _BASE_CHARACTER_CATEGORIES,
                                                _CHARACTER_OPTION_EXTENSIONS,
                                            ),
                                            _CHARACTER_OPTION_EXTENSIONS_V2,
                                        ),
                                        _CHARACTER_OPTION_EXTENSIONS_V3,
                                    ),
                                ),
                                *_ANIMAL_TAXONOMY_CATEGORIES,
                                *_ADDITIONAL_CHARACTER_CATEGORIES,
                                *_NEW_CHARACTER_CATEGORIES,
                                *_CHARACTER_CATEGORIES_V9,
                            ),
                            _CHARACTER_OPTION_EXTENSIONS_V4,
                        ),
                        _CHARACTER_OPTION_EXTENSIONS_V5,
                    ),
                    _CHARACTER_OPTION_EXTENSIONS_V6,
                ),
                _CHARACTER_OPTION_EXTENSIONS_V7,
            ),
            _CHARACTER_OPTION_EXTENSIONS_V8,
        ),
        _CHARACTER_OPTION_EXTENSIONS_V9,
    ),
    _CHARACTER_OPTION_EXTENSIONS_V10,
)
BACKGROUND_CATEGORIES: Final[tuple[TagCategory, ...]] = _extend_categories(
    _extend_categories(
        _extend_categories(
            _extend_categories(
                _extend_categories(
                    _extend_categories(
                        _BASE_BACKGROUND_CATEGORIES,
                        _BACKGROUND_OPTION_EXTENSIONS,
                    ),
                    _BACKGROUND_OPTION_EXTENSIONS_V2,
                ),
                _BACKGROUND_OPTION_EXTENSIONS_V3,
            ),
            _BACKGROUND_OPTION_EXTENSIONS_V4,
        ),
        _BACKGROUND_OPTION_EXTENSIONS_V5,
    ),
    _BACKGROUND_OPTION_EXTENSIONS_V6,
)
NEGATIVE_CATEGORIES: Final[tuple[TagCategory, ...]] = _extend_categories(
    _extend_categories(
        _extend_categories(
            _extend_categories(
                _extend_categories(
                    _extend_categories(
                        _BASE_NEGATIVE_CATEGORIES,
                        _NEGATIVE_OPTION_EXTENSIONS,
                    ),
                    _NEGATIVE_OPTION_EXTENSIONS_V2,
                ),
                _NEGATIVE_OPTION_EXTENSIONS_V3,
            ),
            _NEGATIVE_OPTION_EXTENSIONS_V4,
        ),
        _NEGATIVE_OPTION_EXTENSIONS_V5,
    ),
    _NEGATIVE_OPTION_EXTENSIONS_V6,
)


_ADULT_FEMALE_ACTIVE_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "climax_tremors",
        "climax_body_flush",
        "climax_goosebumps",
        "climax_muscle_tension",
        "climax_breath_catch",
        "squirting_climax",
        "repeated_squirting",
        "arousal_fluid_dripping",
        "inner_thigh_wetness",
        "overstimulated_trembling",
        "rhythmic_pelvic_contractions",
        "climax_abdominal_contractions",
        "climax_leg_tremors",
        "climax_toe_curl",
        "climax_visible_pulse",
        "climax_skin_sheen",
        "arousal_skin_sheen",
        "inner_thigh_fluid_trails",
    }
)
ADULT_FEMALE_ACTIVE_STATE_KEYS: Final[frozenset[str]] = _ADULT_FEMALE_ACTIVE_STATE_KEYS
_ADULT_FEMALE_AFTERCARE_EXPRESSION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "dazed_afterglow",
        "spent_smile",
        "breathless_afterglow",
    }
)
_ADULT_FEMALE_EXPRESSION_KEYS: Final[frozenset[str]] = frozenset(
    option.key
    for category in CHARACTER_CATEGORIES
    if category.key == "adult_female_expression"
    for option in category.options
)
ADULT_FEMALE_EXPRESSION_KEYS: Final[frozenset[str]] = _ADULT_FEMALE_EXPRESSION_KEYS
if not _ADULT_FEMALE_AFTERCARE_EXPRESSION_KEYS <= _ADULT_FEMALE_EXPRESSION_KEYS:
    raise ValueError("成人事後表情索引引用不存在的選項")
_ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS: Final[frozenset[str]] = (
    _ADULT_FEMALE_EXPRESSION_KEYS - _ADULT_FEMALE_AFTERCARE_EXPRESSION_KEYS
)
ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS: Final[frozenset[str]] = (
    _ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS
)
_ADULT_BASE_EXPRESSION_KEYS: Final[frozenset[str]] = frozenset(
    option.key
    for category in CHARACTER_CATEGORIES
    if category.key == "expression"
    for option in category.options
    if option.adult_only
)
ADULT_BASE_EXPRESSION_KEYS: Final[frozenset[str]] = _ADULT_BASE_EXPRESSION_KEYS
_ADULT_BASE_AFTERCARE_EXPRESSION_KEYS: Final[frozenset[str]] = frozenset(
    {"post_climax_bliss"}
)
if not _ADULT_BASE_AFTERCARE_EXPRESSION_KEYS <= _ADULT_BASE_EXPRESSION_KEYS:
    raise ValueError("成人事後基礎表情索引引用不存在的選項")
_ADULT_BASE_ACTIVE_EXPRESSION_KEYS: Final[frozenset[str]] = (
    _ADULT_BASE_EXPRESSION_KEYS - _ADULT_BASE_AFTERCARE_EXPRESSION_KEYS
)
ADULT_BASE_ACTIVE_EXPRESSION_KEYS: Final[frozenset[str]] = (
    _ADULT_BASE_ACTIVE_EXPRESSION_KEYS
)
_ADULT_FEMALE_POST_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "post_orgasm_afterglow",
        "post_orgasm_trembling",
        "post_orgasm_spent",
        "multiple_orgasm_exhaustion",
        "post_orgasm_weak_knees",
        "post_orgasm_goosebumps",
        "post_orgasm_sweat",
        "post_orgasm_hypersensitivity",
        "post_squirt_wetness",
        "aftercare_relaxation",
        "post_orgasm_breathlessness",
        "post_orgasm_limp_relaxation",
        "post_orgasm_shivers",
        "post_orgasm_drowsiness",
        "lingering_body_flush",
        "relaxed_hands_after_orgasm",
        "settling_pulse_after_orgasm",
        "heavy_limbs_after_orgasm",
        "softened_posture_after_orgasm",
        "post_squirt_droplets",
        "post_squirt_puddle",
        "cooling_skin_after_orgasm",
    }
)


@dataclass(frozen=True, slots=True)
class _CompatibilityRule:
    """Allowed dependent values for each explicitly constrained anchor value."""

    anchor_category: str
    dependent_category: str
    allowed_dependent_by_anchor: tuple[tuple[str, frozenset[str]], ...]

    def allowed_for(self, anchor_value: str) -> frozenset[str] | None:
        for key, allowed in self.allowed_dependent_by_anchor:
            if key == anchor_value:
                return allowed
        return None


@dataclass(frozen=True, slots=True)
class _MutualExclusionRule:
    """Pairs that a random multi-select must never choose together."""

    category: str
    incompatible_pairs: tuple[tuple[str, str], ...]


def _compatibility(
    anchor_category: str,
    dependent_category: str,
    allowed: Mapping[str, Sequence[str]],
) -> _CompatibilityRule:
    if anchor_category == "fantasy_race":
        allowed = {
            anchor: dependents
            for anchor, dependents in allowed.items()
            if anchor not in _ANIMAL_LINEAGE_RACE_KEYS
        }
    return _CompatibilityRule(
        anchor_category=anchor_category,
        dependent_category=dependent_category,
        allowed_dependent_by_anchor=tuple(
            (anchor, frozenset(dependents)) for anchor, dependents in allowed.items()
        ),
    )


_OUTDOOR_WEATHER: Final[tuple[str, ...]] = (
    "clear",
    "cloudy",
    "fog",
    "drizzle",
    "rain",
    "storm",
    "snow",
    "blizzard",
    "wind",
    "dust",
    "aurora",
    "magical_rain",
    "hail",
    "sleet",
    "heat_haze",
    "lightning",
    "meteor_shower",
    "ashfall",
    "pollen",
    "rainbow",
)

_FURRY_SPECIES_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "domestic_canine": (
        "dog",
        "shiba_inu",
        "husky",
        "german_shepherd",
        "doberman",
        "corgi",
    ),
    "fox": ("red_fox", "arctic_fox", "fennec_fox"),
    "wolf": ("gray_wolf", "arctic_wolf", "coyote", "jackal", "hyena"),
    "feline": (
        "domestic_cat",
        "lion",
        "tiger",
        "leopard",
        "snow_leopard",
        "lynx",
        "cheetah",
        "cougar",
    ),
    "lapine": ("rabbit",),
    "ursine": ("brown_bear", "polar_bear", "panda", "red_panda", "koala"),
    "cervine": ("red_deer", "reindeer", "elk", "antelope", "gazelle"),
    "caprine": ("goat", "sheep", "camel", "alpaca", "llama", "ibex"),
    "bovine": (
        "cow",
        "yak",
        "bison",
        "wild_boar",
        "pig",
        "elephant",
        "rhinoceros",
        "hippopotamus",
    ),
    "equine": ("horse", "zebra", "donkey"),
    "rodent": ("mouse", "rat", "squirrel", "capybara", "beaver", "chinchilla", "guinea_pig"),
    "small_mammal": (
        "otter",
        "raccoon",
        "skunk",
        "hedgehog",
        "kangaroo",
        "sloth",
        "gorilla",
        "monkey",
        "badger",
        "ferret",
        "meerkat",
        "wolverine",
        "wombat",
        "opossum",
        "armadillo",
        "lemur",
    ),
    "bat": ("bat",),
    "avian": ("eagle", "owl", "raven", "parrot"),
    "reptile": ("lizard", "gecko", "snake", "crocodile"),
    "aquatic": ("shark", "dolphin", "orca", "axolotl", "seal", "sea_lion", "manatee"),
    "insect": ("butterfly", "moth", "bee", "beetle", "mantis"),
    "dragon": ("dragon",),
}


_BEAST_SPECIES_GROUPS: Final[dict[str, tuple[str, ...]]] = {
    "domestic_canine": (
        "dog",
        "shiba_inu",
        "husky",
        "german_shepherd",
        "doberman",
        "corgi",
    ),
    "fox": ("red_fox", "arctic_fox", "fennec_fox"),
    "wolf": ("gray_wolf", "arctic_wolf", "jackal", "hyena"),
    "feline": (
        "domestic_cat",
        "lion",
        "tiger",
        "leopard",
        "snow_leopard",
        "lynx",
        "black_panther",
        "cheetah",
        "cougar",
        "serval",
    ),
    "lapine": ("rabbit", "hare", "arctic_hare"),
    "ursine": ("brown_bear", "polar_bear", "panda", "red_panda", "koala"),
    "cervine": ("red_deer", "reindeer", "moose", "elk", "antelope", "gazelle"),
    "caprine": ("goat", "sheep", "ram", "camel", "alpaca", "llama", "ibex"),
    "bovine": (
        "cow",
        "bull",
        "buffalo",
        "yak",
        "bison",
        "wild_boar",
        "pig",
        "elephant",
        "rhinoceros",
        "hippopotamus",
    ),
    "equine": ("horse", "zebra", "donkey"),
    "rodent": (
        "mouse",
        "rat",
        "hamster",
        "squirrel",
        "capybara",
        "beaver",
        "chinchilla",
        "guinea_pig",
        "hedgehog",
        "kangaroo",
        "sloth",
        "gorilla",
        "monkey",
        "badger",
        "ferret",
        "meerkat",
        "wolverine",
        "wombat",
        "opossum",
        "armadillo",
        "lemur",
    ),
    "raptor_bird": ("eagle", "golden_eagle", "owl", "raven"),
    "parrot": ("parrot",),
    "waterbird": ("swan",),
    "scaled_reptile": ("lizard", "gecko", "crocodile"),
    "serpentine": ("snake",),
    "chelonian": ("turtle",),
    "shark": ("shark",),
    "cetacean": ("dolphin", "orca", "seal", "sea_lion", "manatee"),
    "fish": ("koi",),
    "amphibian": ("axolotl",),
    "insect": ("butterfly", "moth", "bee", "beetle", "dragonfly", "mantis"),
}


# These species were added to broaden the catalog but do not share the concrete
# muzzle, extremity, gait, or tail anatomy of the legacy bucket that provides
# their general mammal/aquatic defaults.  Per-species overrides below keep the
# selectable details taxonomically neutral and species-accurate instead of
# leaking labels such as rodent, bovine, mustelid, or cetacean into the Prompt.
_SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES: Final[frozenset[str]] = frozenset(
    {
        "hyena",
        "red_panda",
        "koala",
        "antelope",
        "gazelle",
        "camel",
        "alpaca",
        "llama",
        "hedgehog",
        "kangaroo",
        "sloth",
        "gorilla",
        "monkey",
        "badger",
        "ferret",
        "meerkat",
        "wolverine",
        "wombat",
        "opossum",
        "armadillo",
        "wild_boar",
        "pig",
        "lemur",
        "elephant",
        "rhinoceros",
        "hippopotamus",
        "seal",
        "sea_lion",
        "manatee",
    }
)
_FURRY_DEDICATED_LEG_OVERRIDES: Final[dict[str, tuple[str, ...]]] = cast(
    dict[str, tuple[str, ...]],
    {species: ("plantigrade",) for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES}
    | {
        "hyena": ("digitigrade",),
        "antelope": ("unguligrade",),
        "gazelle": ("unguligrade",),
        "camel": ("unguligrade",),
        "alpaca": ("unguligrade",),
        "llama": ("unguligrade",),
        "kangaroo": ("digitigrade",),
        "wild_boar": ("unguligrade",),
        "pig": ("unguligrade",),
        "rhinoceros": ("unguligrade",),
        "seal": ("webbed",),
        "sea_lion": ("webbed",),
        "manatee": ("webbed",),
    },
)


def _expand_beast_group_rules(
    allowed_by_group: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Expand half-beast group rules and prove every species is covered once."""

    if set(allowed_by_group) != set(_BEAST_SPECIES_GROUPS):
        raise ValueError("半獸人相容規則必須完整涵蓋所有物種群")
    result = {
        species: tuple(allowed_by_group[group])
        for group, species_keys in _BEAST_SPECIES_GROUPS.items()
        for species in species_keys
    }
    known_species = {option.key for option in _BEAST_HUMANOID_OPTIONS}
    if set(result) != known_species - {"other"}:
        raise ValueError("半獸人物種群與 catalog 選項不一致")
    result["other"] = tuple(
        dict.fromkeys(
            detail for group_details in allowed_by_group.values() for detail in group_details
        )
    )
    return result


def _append_species_detail_options(
    rules: Mapping[str, Sequence[str]],
    additions: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Append compatible details and keep the custom-species fallback complete."""

    unknown = set(additions) - (set(rules) - {"other"})
    if unknown:
        raise ValueError(f"細節擴充引用不存在的物種：{', '.join(sorted(unknown))}")
    result = {
        species: tuple(dict.fromkeys((*options, *additions.get(species, ()))))
        for species, options in rules.items()
    }
    added_options = tuple(
        dict.fromkeys(option for options in additions.values() for option in options)
    )
    if "other" in result:
        result["other"] = tuple(dict.fromkeys((*result["other"], *added_options)))
    return result


_BEAST_MORPHOLOGY_OPTIONS: Final[tuple[str, ...]] = (
    "near_human",
    "human_dominant",
    "subtle_integrated",
    "balanced",
    "species_forward",
    "strong_integrated",
    "seamless_hybrid",
    "beast_human_frame_species_details",
)
_BEAST_MORPHOLOGY_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = _expand_beast_group_rules(
    {group: _BEAST_MORPHOLOGY_OPTIONS for group in _BEAST_SPECIES_GROUPS}
)

_MAMMAL_BEAST_GROUPS: Final[frozenset[str]] = frozenset(
    {
        "domestic_canine",
        "fox",
        "wolf",
        "feline",
        "lapine",
        "ursine",
        "cervine",
        "caprine",
        "bovine",
        "equine",
        "rodent",
    }
)
_MAMMAL_FACE_OPTIONS: Final[tuple[str, ...]] = (
    "human_face",
    "subtle_species_nose",
    "animal_nose_human_mouth",
    "short_hybrid_muzzle",
    "balanced_hybrid",
    "pronounced_hybrid",
)
_BEAST_FACE_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = {
    species: (
        *options,
        "beast_restrained_species_face_contours",
        "beast_human_expression_species_features",
    )
    for species, options in _expand_beast_group_rules(
        {
        group: (
            _MAMMAL_FACE_OPTIONS
            if group in _MAMMAL_BEAST_GROUPS
            else {
                "raptor_bird": ("human_face", "avian_bridge"),
                "parrot": ("human_face", "avian_bridge"),
                "waterbird": ("human_face", "avian_bridge"),
                "scaled_reptile": (
                    "human_face",
                    "subtle_species_nose",
                    "short_hybrid_muzzle",
                    "balanced_hybrid",
                    "reptilian_bridge",
                ),
                "serpentine": ("human_face", "subtle_species_nose", "reptilian_bridge"),
                "chelonian": ("human_face", "subtle_species_nose", "reptilian_bridge"),
                "shark": ("human_face", "subtle_species_nose", "aquatic_bridge"),
                "cetacean": ("human_face", "subtle_species_nose", "aquatic_bridge"),
                "fish": ("human_face", "subtle_species_nose", "aquatic_bridge"),
                "amphibian": ("human_face", "subtle_species_nose", "aquatic_bridge"),
                "insect": ("human_face", "insect_face_accents"),
            }[group]
        )
        for group in _BEAST_SPECIES_GROUPS
        }
    ).items()
}

_HUMANLIKE_SKIN_OPTIONS: Final[tuple[str, ...]] = (
    "human_skin",
    "species_colored_skin",
    "smooth_patterned_skin",
)
_MAMMAL_SKIN_OPTIONS: Final[tuple[str, ...]] = (
    *_HUMANLIKE_SKIN_OPTIONS,
    "short_fur_accents",
    "velvet_fur_layer",
    "facial_fur_accents",
    "limb_fur_accents",
    "mixed_skin_fur",
)
_BEAST_SKIN_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = _expand_beast_group_rules(
    {
        group: (
            _MAMMAL_SKIN_OPTIONS
            if group in _MAMMAL_BEAST_GROUPS
            else (
                (*_HUMANLIKE_SKIN_OPTIONS, "feather_accents")
                if group in {"raptor_bird", "parrot", "waterbird"}
                else (
                    (*_HUMANLIKE_SKIN_OPTIONS, "scale_accents")
                    if group in {"scaled_reptile", "serpentine", "chelonian"}
                    else (
                        (*_HUMANLIKE_SKIN_OPTIONS, "aquatic_skin")
                        if group in {"shark", "cetacean", "fish", "amphibian"}
                        else (*_HUMANLIKE_SKIN_OPTIONS, "chitin_accents")
                    )
                )
            )
        )
        for group in _BEAST_SPECIES_GROUPS
    }
)

_GENERIC_BEAST_MARKINGS: Final[tuple[str, ...]] = (
    "solid",
    "countershading",
    "facial_mask",
    "eye_stripes",
    "cheek_stripes",
    "limb_bands",
    "dorsal_line",
    "shoulder_stripes",
    "flank_stripes",
    "gradient",
    "bioluminescent",
    "beast_freckled_speckles",
    "beast_ringed_accents",
    "beast_marbled_pattern",
    "beast_chevron_bands",
    "beast_constellation_spots",
    "beast_mosaic_patches",
    "beast_faded_edge_markings",
)
_BEAST_MARKING_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = (
    _expand_beast_group_rules(
        {
            "domestic_canine": (
                *_GENERIC_BEAST_MARKINGS,
                "spots",
                "piebald",
                "brindle",
                "point_coloration",
                "saddle_mark",
            ),
            "fox": (*_GENERIC_BEAST_MARKINGS, "piebald", "point_coloration"),
            "wolf": (*_GENERIC_BEAST_MARKINGS, "saddle_mark"),
            "feline": (
                *_GENERIC_BEAST_MARKINGS,
                "tiger_stripes",
                "tabby",
                "spots",
                "rosettes",
                "piebald",
                "point_coloration",
            ),
            "lapine": (*_GENERIC_BEAST_MARKINGS, "spots", "piebald", "point_coloration"),
            "ursine": (*_GENERIC_BEAST_MARKINGS, "piebald"),
            "cervine": (*_GENERIC_BEAST_MARKINGS, "spots", "piebald"),
            "caprine": (*_GENERIC_BEAST_MARKINGS, "piebald", "brindle"),
            "bovine": (*_GENERIC_BEAST_MARKINGS, "piebald", "brindle"),
            "equine": (
                *_GENERIC_BEAST_MARKINGS,
                "zebra_stripes",
                "piebald",
                "point_coloration",
                "saddle_mark",
            ),
            "rodent": (*_GENERIC_BEAST_MARKINGS, "spots", "piebald", "point_coloration"),
            "raptor_bird": (*_GENERIC_BEAST_MARKINGS, "spots", "piebald"),
            "parrot": (*_GENERIC_BEAST_MARKINGS, "piebald"),
            "waterbird": (*_GENERIC_BEAST_MARKINGS, "piebald"),
            "scaled_reptile": (
                *_GENERIC_BEAST_MARKINGS,
                "spots",
                "reptile_mottling",
                "diamond_bands",
            ),
            "serpentine": (*_GENERIC_BEAST_MARKINGS, "reptile_mottling", "diamond_bands"),
            "chelonian": (*_GENERIC_BEAST_MARKINGS, "reptile_mottling", "diamond_bands"),
            "shark": (*_GENERIC_BEAST_MARKINGS, "spots"),
            "cetacean": _GENERIC_BEAST_MARKINGS,
            "fish": (*_GENERIC_BEAST_MARKINGS, "koi_patches"),
            "amphibian": (*_GENERIC_BEAST_MARKINGS, "spots"),
            "insect": (*_GENERIC_BEAST_MARKINGS, "insect_bands", "spots"),
        }
    )
    | {species: _GENERIC_BEAST_MARKINGS for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES}
    | {"orca": (*_GENERIC_BEAST_MARKINGS, "orca_patches")}
)

_BEAST_MARKING_COVERAGE_OPTIONS: Final[tuple[str, ...]] = (
    "face_only",
    "limbs_only",
    "face_and_limbs",
    "shoulders_back",
    "torso_accents",
    "extremity_gradient",
    "balanced_full_body",
    "asymmetrical_accent",
)
_BEAST_MARKING_COVERAGE_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = _expand_beast_group_rules(
    {group: _BEAST_MARKING_COVERAGE_OPTIONS for group in _BEAST_SPECIES_GROUPS}
)

_BEAST_EXTERNAL_EAR_SPECIES: Final[frozenset[str]] = frozenset(
    species
    for group in _MAMMAL_BEAST_GROUPS
    for species in _BEAST_SPECIES_GROUPS[group]
)
_BEAST_EAR_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = (
    _append_species_detail_options(
        {
            species: (*options, "beast_streamlined_hearing_profile")
            for species, options in (
                _expand_beast_group_rules(
                    {
        "domestic_canine": ("species_default", "small", "upright", "drooping"),
        "fox": ("species_default", "upright", "tufted"),
        "wolf": ("species_default", "upright", "tufted"),
        "feline": ("species_default", "small", "upright", "rounded", "tufted"),
        "lapine": ("species_default", "upright", "drooping", "long"),
        "ursine": ("species_default", "small", "rounded"),
        "cervine": ("species_default", "side_set"),
        "caprine": ("species_default", "side_set", "drooping"),
        "bovine": ("species_default", "side_set", "drooping"),
        "equine": ("species_default", "upright", "side_set"),
        "rodent": ("species_default", "small", "rounded", "upright"),
        "raptor_bird": ("feather_tufts", "no_external"),
        "parrot": ("feather_tufts", "no_external"),
        "waterbird": ("feather_tufts", "no_external"),
        "scaled_reptile": ("no_external",),
        "serpentine": ("no_external",),
        "chelonian": ("no_external",),
        "shark": ("finlike", "no_external"),
        "cetacean": ("finlike", "no_external"),
        "fish": ("finlike", "no_external"),
        "amphibian": ("no_external",),
        "insect": ("no_external",),
                    }
                )
                | {
                    species: ("species_default",)
                    for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
                }
            ).items()
        },
        {
            species: (
                "folded_tip",
                "forward_cupped",
                "notched_edge",
                *(("broad_fan",) if species == "elephant" else ()),
            )
            for species in _BEAST_EXTERNAL_EAR_SPECIES
        },
    )
)

_BEAST_TAIL_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = _append_species_detail_options(
    _expand_beast_group_rules(
        {
        "domestic_canine": ("none", "species_default", "short", "curled", "fluffy", "canine"),
        "fox": ("none", "species_default", "fluffy", "fox_brush"),
        "wolf": ("none", "species_default", "fluffy", "canine", "lupine"),
        "feline": ("none", "species_default", "short", "feline"),
        "lapine": ("none", "species_default", "lapine"),
        "ursine": ("none", "species_default", "short", "ursine"),
        "cervine": ("none", "species_default", "short", "cervine"),
        "caprine": ("none", "species_default", "short"),
        "bovine": ("none", "species_default", "bovine"),
        "equine": ("none", "species_default", "equine"),
        "rodent": ("none", "species_default", "short", "rodent", "squirrel"),
        "raptor_bird": ("none", "species_default", "avian_plume"),
        "parrot": ("none", "species_default", "avian_plume"),
        "waterbird": ("none", "species_default", "avian_plume"),
        "scaled_reptile": ("none", "species_default", "reptilian"),
        "serpentine": ("none", "species_default", "reptilian"),
        "chelonian": ("none", "species_default", "short", "reptilian"),
        "shark": ("none", "species_default", "aquatic"),
        "cetacean": ("none", "species_default", "aquatic"),
        "fish": ("none", "species_default", "aquatic"),
        "amphibian": ("none", "species_default", "aquatic"),
        "insect": ("none", "species_default", "insect_abdomen"),
        }
    )
    | {
        species: ("none", "species_default")
        for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
    },
    {
        **{
            species: ("balancing",)
            for species in (
                *_BEAST_SPECIES_GROUPS["feline"],
                "mouse",
                "rat",
                "squirrel",
                "kangaroo",
                "lizard",
            )
        },
        "monkey": ("prehensile",),
        "opossum": ("prehensile",),
        "lemur": ("prehensile",),
        "beaver": ("paddle",),
        "manatee": ("paddle",),
        "armadillo": ("segmented_armor",),
        "crocodile": ("segmented_armor", "balancing"),
        "turtle": ("segmented_armor",),
    },
)

_BEAST_HAND_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = {
    species: (*options, "beast_refined_species_nails", "beast_textured_human_palms")
    for species, options in (
        _expand_beast_group_rules(
            {
        "domestic_canine": (
            "human_hands",
            "human_paw_pads",
            "articulated_paw_hands",
            "short_claws",
        ),
        "fox": ("human_hands", "human_paw_pads", "articulated_paw_hands", "short_claws"),
        "wolf": ("human_hands", "human_paw_pads", "articulated_paw_hands", "short_claws"),
        "feline": (
            "human_hands",
            "human_paw_pads",
            "articulated_paw_hands",
            "short_claws",
            "retractable_claws",
        ),
        "lapine": ("human_hands", "human_paw_pads", "articulated_paw_hands", "short_claws"),
        "ursine": ("human_hands", "human_paw_pads", "articulated_paw_hands", "short_claws"),
        "cervine": ("human_hands", "hooflike_nails"),
        "caprine": ("human_hands", "hooflike_nails"),
        "bovine": ("human_hands", "hooflike_nails"),
        "equine": ("human_hands", "hooflike_nails"),
        "rodent": ("human_hands", "human_paw_pads", "articulated_paw_hands", "short_claws"),
        "raptor_bird": ("human_hands", "feathered_hands", "taloned_hands"),
        "parrot": ("human_hands", "feathered_hands", "taloned_hands"),
        "waterbird": ("human_hands", "feathered_hands", "webbed_hands"),
        "scaled_reptile": ("human_hands", "scaled_hands", "scaled_claws"),
        "serpentine": ("human_hands", "scaled_hands", "scaled_claws"),
        "chelonian": ("human_hands", "scaled_hands", "scaled_claws"),
        "shark": ("human_hands", "webbed_hands"),
        "cetacean": ("human_hands", "webbed_hands"),
        "fish": ("human_hands", "webbed_hands"),
        "amphibian": ("human_hands", "webbed_hands"),
        "insect": ("human_hands", "chitin_hands", "insect_claws"),
            }
        )
        | {
            species: ("human_hands", "species_hands")
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        }
    ).items()
}

_BEAST_FOOT_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = {
    species: (*options, "beast_refined_species_toes", "beast_textured_human_soles")
    for species, options in (
        _expand_beast_group_rules(
            {
            "domestic_canine": (
                "human_feet",
                "paw_pad_feet",
                "plantigrade_paws",
                "digitigrade_paws",
                "clawed_human_feet",
            ),
            "fox": (
                "human_feet",
                "paw_pad_feet",
                "plantigrade_paws",
                "digitigrade_paws",
                "clawed_human_feet",
            ),
            "wolf": (
                "human_feet",
                "paw_pad_feet",
                "plantigrade_paws",
                "digitigrade_paws",
                "clawed_human_feet",
            ),
            "feline": (
                "human_feet",
                "paw_pad_feet",
                "plantigrade_paws",
                "digitigrade_paws",
                "clawed_human_feet",
            ),
            "lapine": (
                "human_feet",
                "paw_pad_feet",
                "plantigrade_paws",
                "digitigrade_paws",
                "clawed_human_feet",
            ),
            "ursine": ("human_feet", "paw_pad_feet", "plantigrade_paws", "clawed_human_feet"),
            "cervine": ("human_feet", "split_hooves"),
            "caprine": ("human_feet", "split_hooves"),
            "bovine": ("human_feet", "split_hooves"),
            "equine": ("human_feet", "equine_hooves"),
            "rodent": (
                "human_feet",
                "paw_pad_feet",
                "plantigrade_paws",
                "digitigrade_paws",
                "clawed_human_feet",
            ),
            "raptor_bird": ("human_feet", "taloned_feet"),
            "parrot": ("human_feet", "taloned_feet"),
            "waterbird": ("human_feet", "webbed_feet"),
            "scaled_reptile": ("human_feet", "scaled_clawed_feet"),
            "serpentine": ("human_feet", "scaled_clawed_feet"),
            "chelonian": ("human_feet", "scaled_clawed_feet"),
            "shark": ("human_feet", "webbed_feet"),
            "cetacean": ("human_feet", "webbed_feet"),
            "fish": ("human_feet", "webbed_feet"),
            "amphibian": ("human_feet", "webbed_feet"),
            "insect": ("human_feet", "chitin_feet", "insect_tarsi"),
            }
        )
        | {
            species: ("human_feet", "species_feet")
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        }
        | {"gecko": ("human_feet", "scaled_clawed_feet", "gecko_toe_pads")}
    ).items()
}

_BEAST_DETAIL_RULES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "beast_morphology_balance": _BEAST_MORPHOLOGY_BY_SPECIES,
    "beast_face_blend": _BEAST_FACE_BY_SPECIES,
    "beast_skin_covering": _BEAST_SKIN_BY_SPECIES,
    "beast_marking_pattern": _BEAST_MARKING_BY_SPECIES,
    "beast_marking_coverage": _BEAST_MARKING_COVERAGE_BY_SPECIES,
    "beast_ear_style": _BEAST_EAR_BY_SPECIES,
    "beast_tail_style": _BEAST_TAIL_BY_SPECIES,
    "beast_hand_style": _BEAST_HAND_BY_SPECIES,
    "beast_foot_style": _BEAST_FOOT_BY_SPECIES,
}


def _expand_furry_group_rules(
    allowed_by_group: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Expand group-level furry compatibility into every concrete species key."""

    if set(allowed_by_group) != set(_FURRY_SPECIES_GROUPS):
        raise ValueError("福瑞相容規則必須完整涵蓋所有物種群")
    result = {
        species: tuple(allowed_by_group[group])
        for group, species_keys in _FURRY_SPECIES_GROUPS.items()
        for species in species_keys
    }
    known_species = {option.key for option in _FURRY_SPECIES_OPTIONS}
    if set(result) != known_species - {"other"}:
        raise ValueError("福瑞物種群與 catalog 選項不一致")
    result["other"] = tuple(
        dict.fromkeys(
            detail for group_details in allowed_by_group.values() for detail in group_details
        )
    )
    return result


_FURRY_BODY_COVERING_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = (
    _append_species_detail_options(
        _expand_furry_group_rules(
            {
        "domestic_canine": ("short_fur", "medium_fur", "long_fur", "plush_fur", "sleek_fur"),
        "fox": ("medium_fur", "long_fur", "plush_fur", "sleek_fur"),
        "wolf": ("short_fur", "medium_fur", "long_fur", "plush_fur"),
        "feline": ("short_fur", "medium_fur", "long_fur", "plush_fur", "sleek_fur"),
        "lapine": ("short_fur", "medium_fur", "plush_fur"),
        "ursine": ("short_fur", "medium_fur", "plush_fur"),
        "cervine": ("short_fur", "medium_fur", "sleek_fur"),
        "caprine": ("short_fur", "medium_fur", "long_fur", "curly_wool"),
        "bovine": ("short_fur", "sleek_fur"),
        "equine": ("short_fur", "sleek_fur"),
        "rodent": ("short_fur", "medium_fur", "plush_fur"),
        "small_mammal": ("short_fur", "medium_fur", "plush_fur", "sleek_fur"),
        "bat": ("short_fur", "medium_fur", "sleek_fur"),
        "avian": ("feathers",),
        "reptile": ("smooth_scales", "keeled_scales"),
        "aquatic": ("aquatic_skin",),
        "insect": ("chitin",),
        "dragon": ("smooth_scales", "keeled_scales", "mixed_covering"),
            }
        )
        | {
            species: ("species_default",)
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        },
        {
            "eagle": ("furry_downy_feathers",),
            "owl": ("furry_downy_feathers",),
            "raven": ("furry_downy_feathers",),
            "parrot": ("furry_downy_feathers",),
            "hedgehog": ("furry_short_flexible_quills",),
            "armadillo": ("furry_shell_plates",),
            "beetle": ("furry_shell_plates",),
            "crocodile": ("furry_shell_plates",),
            "dragon": ("furry_shell_plates",),
        },
    )
)

_FURRY_GLOBAL_MARKING_OPTIONS: Final[tuple[str, ...]] = (
    "furry_dipped_extremities",
    "furry_dorsal_gradient",
    "furry_constellation_speckles",
    "furry_iridescent_edges",
)
_FURRY_MARKING_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = {
    species: tuple(dict.fromkeys((*options, *_FURRY_GLOBAL_MARKING_OPTIONS)))
    for species, options in _append_species_detail_options(
        _expand_furry_group_rules(
            {
        "domestic_canine": (
            "solid",
            "countershading",
            "tuxedo",
            "piebald",
            "brindle",
            "point_coloration",
            "facial_mask",
            "saddle_mark",
            "gradient",
            "bioluminescent",
        ),
        "fox": ("solid", "countershading", "piebald", "facial_mask", "gradient", "bioluminescent"),
        "wolf": (
            "solid",
            "countershading",
            "facial_mask",
            "saddle_mark",
            "gradient",
            "bioluminescent",
        ),
        "feline": (
            "solid",
            "countershading",
            "tuxedo",
            "tabby",
            "tiger_stripes",
            "spots",
            "rosettes",
            "piebald",
            "point_coloration",
            "gradient",
            "bioluminescent",
        ),
        "lapine": ("solid", "countershading", "tuxedo", "piebald", "point_coloration", "gradient"),
        "ursine": ("solid", "countershading", "piebald", "facial_mask", "gradient"),
        "cervine": ("solid", "countershading", "spots", "piebald", "gradient"),
        "caprine": ("solid", "countershading", "piebald", "brindle", "facial_mask", "gradient"),
        "bovine": ("solid", "countershading", "piebald", "brindle", "facial_mask", "gradient"),
        "equine": (
            "solid",
            "countershading",
            "zebra_stripes",
            "piebald",
            "point_coloration",
            "gradient",
        ),
        "rodent": ("solid", "countershading", "tuxedo", "piebald", "point_coloration", "gradient"),
        "small_mammal": ("solid", "countershading", "facial_mask", "piebald", "gradient"),
        "bat": ("solid", "countershading", "facial_mask", "gradient", "bioluminescent"),
        "avian": ("solid", "countershading", "spots", "piebald", "gradient", "bioluminescent"),
        "reptile": (
            "solid",
            "countershading",
            "tiger_stripes",
            "spots",
            "rosettes",
            "gradient",
            "bioluminescent",
        ),
        "aquatic": (
            "solid",
            "countershading",
            "tiger_stripes",
            "spots",
            "piebald",
            "gradient",
            "bioluminescent",
        ),
        "insect": ("solid", "tiger_stripes", "spots", "gradient", "bioluminescent"),
        "dragon": (
            "solid",
            "countershading",
            "tiger_stripes",
            "spots",
            "rosettes",
            "gradient",
            "bioluminescent",
        ),
            }
        )
        | {
            species: ("solid", "countershading", "gradient", "bioluminescent")
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        },
        {
            "raccoon": ("furry_ringed_distal_pattern",),
            "red_panda": ("furry_ringed_distal_pattern",),
            "lemur": ("furry_ringed_distal_pattern",),
        },
    ).items()
}

_FURRY_MUZZLE_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = {
    species: (*options, "furry_rounded_anthro_profile", "furry_angular_anthro_profile")
    for species, options in (
        _expand_furry_group_rules(
            {
        "domestic_canine": ("long_canine",),
        "fox": ("vulpine",),
        "wolf": ("lupine", "long_canine"),
        "feline": ("short_feline",),
        "lapine": ("lapine",),
        "ursine": ("ursine",),
        "cervine": ("cervine",),
        "caprine": ("caprine",),
        "bovine": ("bovine",),
        "equine": ("equine",),
        "rodent": ("rodent",),
        "small_mammal": ("mustelid",),
        "bat": ("short_feline",),
        "avian": ("avian_beak",),
        "reptile": ("reptilian",),
        "aquatic": ("aquatic_rostrum",),
        "insect": ("insectoid",),
        "dragon": ("draconic",),
            }
        )
        | {
            species: ("species_default",)
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        }
    ).items()
}

_FURRY_LEGS_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = (
    _expand_furry_group_rules(
        {
            "domestic_canine": ("plantigrade", "digitigrade"),
            "fox": ("plantigrade", "digitigrade"),
            "wolf": ("plantigrade", "digitigrade"),
            "feline": ("plantigrade", "digitigrade"),
            "lapine": ("plantigrade", "digitigrade"),
            "ursine": ("plantigrade",),
            "cervine": ("unguligrade",),
            "caprine": ("unguligrade",),
            "bovine": ("unguligrade",),
            "equine": ("unguligrade",),
            "rodent": ("plantigrade", "digitigrade"),
            "small_mammal": ("plantigrade", "digitigrade"),
            "bat": ("plantigrade", "digitigrade"),
            "avian": ("avian",),
            "reptile": ("reptilian",),
            "aquatic": ("plantigrade", "webbed"),
            "insect": ("insectoid",),
            "dragon": ("draconic",),
        }
    )
    | _FURRY_DEDICATED_LEG_OVERRIDES
)

_FURRY_EXTREMITIES_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = (
    _append_species_detail_options(
        _expand_furry_group_rules(
            {
        "domestic_canine": ("padded_hands", "paw_hands", "soft_paws"),
        "fox": ("padded_hands", "paw_hands", "soft_paws"),
        "wolf": ("padded_hands", "paw_hands", "clawed_hands"),
        "feline": ("padded_hands", "paw_hands", "clawed_hands", "soft_paws"),
        "lapine": ("padded_hands", "soft_paws"),
        "ursine": ("paw_hands", "clawed_hands", "soft_paws"),
        "cervine": ("hoof_hands",),
        "caprine": ("hoof_hands",),
        "bovine": ("hoof_hands",),
        "equine": ("hoof_hands",),
        "rodent": ("padded_hands", "soft_paws"),
        "small_mammal": ("padded_hands", "paw_hands", "soft_paws"),
        "bat": ("clawed_hands",),
        "avian": ("talons",),
        "reptile": ("scaled_claws",),
        "aquatic": ("webbed",),
        "insect": ("insectoid", "armored_claws"),
        "dragon": ("scaled_claws", "armored_claws"),
            }
        )
        | {
            species: ("species_default",)
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        },
        {
            **{
                species: ("opposable_paw_hands",)
                for group in (
                    "domestic_canine",
                    "fox",
                    "wolf",
                    "feline",
                    "lapine",
                    "ursine",
                    "rodent",
                    "small_mammal",
                )
                for species in _FURRY_SPECIES_GROUPS[group]
            },
            **{
                species: ("feathered_talons",)
                for species in _FURRY_SPECIES_GROUPS["avian"]
            },
            **{
                species: ("fin_webbed_extremities",)
                for species in _FURRY_SPECIES_GROUPS["aquatic"]
            },
            "beetle": ("chitin_pincer_hands",),
            "mantis": ("chitin_pincer_hands",),
            "gecko": ("furry_adhesive_pads",),
        },
    )
)

_FURRY_TAIL_BY_SPECIES: Final[dict[str, tuple[str, ...]]] = (
    _append_species_detail_options(
        _expand_furry_group_rules(
            {
        "domestic_canine": ("short", "curled", "fluffy", "canine"),
        "fox": ("fluffy", "fox_brush"),
        "wolf": ("fluffy", "canine", "lupine"),
        "feline": ("short", "feline"),
        "lapine": ("lapine",),
        "ursine": ("none", "short", "ursine"),
        "cervine": ("short", "cervine"),
        "caprine": ("short",),
        "bovine": ("bovine",),
        "equine": ("equine",),
        "rodent": ("short", "fluffy", "rodent"),
        "small_mammal": ("short", "fluffy", "rodent"),
        "bat": ("none", "short"),
        "avian": ("none", "avian_plume"),
        "reptile": ("reptilian",),
        "aquatic": ("none", "aquatic"),
        "insect": ("none", "insect_abdomen"),
        "dragon": ("reptilian",),
            }
        )
        | {
            species: ("none", "species_default")
            for species in _SPECIES_WITH_DEDICATED_MORPHOLOGY_OVERRIDES
        },
        {
            **{
                species: ("balancing_tail",)
                for species in _FURRY_SPECIES_GROUPS["feline"]
            },
            "monkey": ("furry_prehensile_tail",),
            "opossum": ("furry_prehensile_tail",),
            "lemur": ("furry_prehensile_tail",),
            "beaver": ("paddle_tail",),
            "otter": ("paddle_tail",),
            "manatee": ("paddle_tail", "aquatic_fluke_tail"),
            "armadillo": ("segmented_armor_tail",),
            "crocodile": ("segmented_armor_tail", "balancing_tail"),
            "dragon": ("segmented_armor_tail", "balancing_tail"),
            "dolphin": ("aquatic_fluke_tail",),
            "orca": ("aquatic_fluke_tail",),
            "kangaroo": ("balancing_tail",),
            "mouse": ("balancing_tail",),
            "rat": ("balancing_tail",),
            "squirrel": ("balancing_tail",),
            "lizard": ("balancing_tail",),
        },
    )
)

_FURRY_DETAIL_RULES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "furry_body_covering": _FURRY_BODY_COVERING_BY_SPECIES,
    "furry_marking_pattern": _FURRY_MARKING_BY_SPECIES,
    "furry_muzzle_shape": _FURRY_MUZZLE_BY_SPECIES,
    "furry_leg_style": _FURRY_LEGS_BY_SPECIES,
    "furry_extremities": _FURRY_EXTREMITIES_BY_SPECIES,
    "furry_tail_style": _FURRY_TAIL_BY_SPECIES,
}

_OPTIONAL_TAXONOMY_SPECIES_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"beast_humanoid_species", "furry_species"}
)
_BEAST_DETAIL_CATEGORIES: Final[frozenset[str]] = frozenset(_BEAST_DETAIL_RULES)
_FURRY_DETAIL_CATEGORIES: Final[frozenset[str]] = frozenset(_FURRY_DETAIL_RULES)
_ANIMAL_DETAIL_CATEGORIES: Final[frozenset[str]] = (
    _BEAST_DETAIL_CATEGORIES | _FURRY_DETAIL_CATEGORIES
)
CHARACTER_IDENTITY_CATEGORY_KEYS: Final[dict[CharacterIdentityMode, frozenset[str]]] = {
    "standard": frozenset({"fantasy_race"}),
    "beast_humanoid": frozenset({"beast_humanoid_species", *_BEAST_DETAIL_CATEGORIES}),
    "furry": frozenset({"furry_species", *_FURRY_DETAIL_CATEGORIES}),
}
CHARACTER_IDENTITY_MODE_LABELS_ZH: Final[dict[CharacterIdentityMode, str]] = {
    "standard": "一般／奇幻",
    "beast_humanoid": "獸人／半獸人",
    "furry": "福瑞",
}
NON_ANIMAL_ORC_LINEAGE_KEYS: Final[frozenset[str]] = frozenset({"orc", "half_orc"})
_ANIMAL_TAXONOMY_INCOMPATIBLE_FANTASY_RACES: Final[frozenset[str]] = frozenset(
    {"merfolk", "centaur", "naga", "spiderfolk"}
)


def filter_character_categories_by_identity(
    categories: Sequence[TagCategory],
    identity_mode: CharacterIdentityMode | str,
) -> tuple[TagCategory, ...]:
    """Expose exactly one animal-identity branch while retaining common categories."""

    if identity_mode not in CHARACTER_IDENTITY_MODES:
        raise ValueError("角色身分模式只能是 standard、beast_humanoid 或 furry")
    mode = cast(CharacterIdentityMode, identity_mode)
    allowed = CHARACTER_IDENTITY_CATEGORY_KEYS[mode]
    identity_categories = frozenset(
        {
            "fantasy_race",
            "beast_humanoid_species",
            "furry_species",
            *_ANIMAL_DETAIL_CATEGORIES,
        }
    )
    return tuple(
        category
        for category in categories
        if category.key not in identity_categories or category.key in allowed
    )


_EAR_TRAIT_KEYS: Final[frozenset[str]] = frozenset({"pointed_ears", "fin_ears", "fox_ears"})
_ANIMAL_TAXONOMY_OWNED_TRAIT_KEYS: Final[frozenset[str]] = frozenset(
    {
        *_EAR_TRAIT_KEYS,
        "tail",
        "multiple_tails",
        "shark_tail",
        "hooves",
        "avian_talons",
        "fur",
        "scales",
        "bird_feathers",
        "chitin",
        "equine_lower_body",
        "serpentine_lower_body",
        "spider_lower_body",
        "beak",
    }
)


_COMPATIBILITY_RULES: Final[tuple[_CompatibilityRule, ...]] = (
    *(
        _compatibility("beast_humanoid_species", detail_category, allowed_by_species)
        for detail_category, allowed_by_species in _BEAST_DETAIL_RULES.items()
    ),
    *(
        _compatibility("furry_species", detail_category, allowed_by_species)
        for detail_category, allowed_by_species in _FURRY_DETAIL_RULES.items()
    ),
    # Hair is sampled as length -> style -> bangs.  Manual combinations remain
    # untouched; these rules apply only to random completion and rerolls.
    _compatibility(
        "hair_length",
        "hair_style",
        {
            "shaved": ("buzz_cut",),
            "pixie": ("loose_straight", "loose_wavy", "curly", "undercut", "messy"),
            "chin": ("loose_straight", "loose_wavy", "curly", "bob", "messy"),
            "shoulder": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "bob",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "side_ponytail",
                "single_braid",
                "double_braids",
                "crown_braid",
                "bun",
                "double_buns",
                "messy",
            ),
            "mid_back": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "side_ponytail",
                "single_braid",
                "double_braids",
                "crown_braid",
                "bun",
                "double_buns",
                "messy",
            ),
            "waist": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "side_ponytail",
                "single_braid",
                "double_braids",
                "crown_braid",
                "bun",
                "double_buns",
                "messy",
            ),
            "floor": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "single_braid",
                "double_braids",
                "crown_braid",
                "bun",
                "messy",
            ),
        },
    ),
    _compatibility(
        "hair_length",
        "bangs",
        {
            "shaved": ("none",),
            "pixie": ("none", "straight", "side_swept", "wispy"),
            "chin": ("none", "straight", "side_swept", "curtain", "wispy", "hime"),
            "shoulder": ("none", "straight", "side_swept", "curtain", "wispy", "hime"),
            "mid_back": ("none", "straight", "side_swept", "curtain", "wispy", "hime"),
            "waist": ("none", "straight", "side_swept", "curtain", "wispy", "hime"),
            "floor": ("none", "straight", "side_swept", "curtain", "wispy", "hime"),
        },
    ),
    _compatibility(
        "hair_style",
        "bangs",
        {
            "buzz_cut": ("none",),
            "undercut": ("none", "side_swept"),
            "mohawk": ("none",),
        },
    ),
    _compatibility(
        "fantasy_race",
        "fantasy_traits",
        {
            "human": ("glowing_runes", "mechanical_limbs", "ethereal_aura"),
            "elf": ("pointed_ears", "glowing_runes", "ethereal_aura"),
            "dark_elf": ("pointed_ears", "fangs", "glowing_runes", "ethereal_aura"),
            "half_elf": ("pointed_ears", "glowing_runes", "ethereal_aura"),
            "dwarf": ("glowing_runes", "mechanical_limbs"),
            "orc": ("fangs", "pointed_ears", "glowing_runes"),
            "goblin": ("fangs", "pointed_ears", "glowing_runes"),
            "tiefling": ("fangs", "horns", "bat_wings", "tail", "glowing_runes"),
            "demon": (
                "fangs",
                "horns",
                "bat_wings",
                "dragon_wings",
                "tail",
                "scales",
                "glowing_runes",
                "ethereal_aura",
            ),
            "angel": ("halo", "feathered_wings", "glowing_runes", "ethereal_aura"),
            "vampire": ("fangs", "pointed_ears", "bat_wings", "ethereal_aura"),
            "werewolf": ("fangs", "pointed_ears", "tail", "fur", "glowing_runes"),
            "merfolk": ("pointed_ears", "tail", "scales", "ethereal_aura"),
            "fairy": ("pointed_ears", "fairy_wings", "glowing_runes", "ethereal_aura"),
            "dragonkin": (
                "fangs",
                "horns",
                "dragon_wings",
                "tail",
                "scales",
                "crystal_growths",
                "glowing_runes",
            ),
            "catfolk": ("fangs", "pointed_ears", "tail", "fur"),
            "foxfolk": ("fangs", "pointed_ears", "tail", "fur", "ethereal_aura"),
            "wolffolk": ("fangs", "pointed_ears", "tail", "fur"),
            "rabbitfolk": ("pointed_ears", "tail", "fur"),
            "automaton": ("crystal_growths", "glowing_runes", "mechanical_limbs"),
            "android": ("glowing_runes", "mechanical_limbs"),
            "undead": ("fangs", "glowing_runes", "ethereal_aura"),
            "elemental": ("horns", "crystal_growths", "glowing_runes", "ethereal_aura"),
        },
    ),
    _compatibility(
        "outfit_archetype",
        "outfit_materials",
        {
            "casual": ("linen", "silk", "leather", "lace"),
            "streetwear": ("linen", "leather", "metal", "holographic"),
            "formal": ("silk", "velvet", "lace", "translucent"),
            "business": ("linen", "silk", "velvet", "leather"),
            "traveler": ("linen", "leather", "fur_trim"),
            "mage": ("linen", "silk", "velvet", "leather", "lace", "translucent"),
            "knight": ("linen", "leather", "metal", "fur_trim"),
            "rogue": ("linen", "leather"),
            "ranger": ("linen", "leather", "fur_trim"),
            "priest": ("linen", "silk", "velvet", "lace"),
            "royal": ("silk", "velvet", "metal", "lace", "fur_trim", "translucent"),
            "alchemist": ("linen", "leather", "metal"),
            "steampunk": ("linen", "velvet", "leather", "metal"),
            "cyberpunk": ("leather", "metal", "holographic"),
            "space_suit": ("leather", "metal", "holographic"),
            "kimono": ("linen", "silk"),
            "hanfu": ("linen", "silk", "translucent"),
        },
    ),
    _compatibility(
        "outfit_archetype",
        "accessories",
        {"space_suit": ("gloves", "satchel")},
    ),
    _compatibility(
        "character_backdrop",
        "character_location",
        {"transparent": ()},
    ),
    _compatibility(
        "framing",
        "character_backdrop",
        {"character_sheet": ("transparent", "simple", "gradient")},
    ),
    _compatibility(
        "framing",
        "pose",
        {
            "face": ("standing", "seated", "looking_back"),
            "bust": ("standing", "seated", "looking_back", "casting"),
            "half_body": (
                "standing",
                "seated",
                "looking_back",
                "casting",
                "weapon_ready",
            ),
            "character_sheet": ("standing", "weapon_ready"),
        },
    ),
    _compatibility(
        "framing",
        "viewpoint",
        {
            "face": ("eye_level", "low_angle", "high_angle", "profile", "three_quarter"),
            "bust": ("eye_level", "low_angle", "high_angle", "profile", "three_quarter"),
            "half_body": (
                "eye_level",
                "low_angle",
                "high_angle",
                "profile",
                "three_quarter",
            ),
            "character_sheet": ("eye_level", "three_quarter"),
        },
    ),
    # World genre anchors the location before architectural and natural details.
    _compatibility(
        "world_genre",
        "location",
        {
            "high_fantasy": (
                "castle",
                "throne_room",
                "tavern",
                "library",
                "temple",
                "market",
                "village",
                "city_street",
                "alley",
                "forest",
                "ruins",
                "cave",
                "coast",
                "desert",
                "mountain",
                "swamp",
                "floating_island",
                "underwater_city",
            ),
            "dark_fantasy": (
                "castle",
                "throne_room",
                "tavern",
                "library",
                "temple",
                "market",
                "village",
                "city_street",
                "alley",
                "forest",
                "ruins",
                "cave",
                "coast",
                "desert",
                "mountain",
                "swamp",
                "train_station",
                "laboratory",
            ),
            "urban_fantasy": (
                "library",
                "temple",
                "market",
                "city_street",
                "alley",
                "forest",
                "ruins",
                "train_station",
                "laboratory",
            ),
            "fairy_tale": (
                "castle",
                "throne_room",
                "tavern",
                "library",
                "temple",
                "village",
                "forest",
                "ruins",
                "cave",
                "coast",
                "mountain",
                "swamp",
                "floating_island",
                "underwater_city",
            ),
            "mythic": (
                "castle",
                "throne_room",
                "library",
                "temple",
                "market",
                "village",
                "forest",
                "ruins",
                "cave",
                "coast",
                "desert",
                "mountain",
                "swamp",
                "floating_island",
                "underwater_city",
                "alien_planet",
            ),
            "historical": (
                "castle",
                "throne_room",
                "tavern",
                "library",
                "temple",
                "market",
                "village",
                "city_street",
                "alley",
                "forest",
                "ruins",
                "coast",
                "desert",
                "mountain",
                "train_station",
            ),
            "gothic": (
                "castle",
                "throne_room",
                "tavern",
                "library",
                "temple",
                "village",
                "city_street",
                "alley",
                "forest",
                "ruins",
                "cave",
                "coast",
                "train_station",
            ),
            "steampunk": (
                "castle",
                "throne_room",
                "tavern",
                "library",
                "market",
                "village",
                "city_street",
                "alley",
                "ruins",
                "train_station",
                "laboratory",
            ),
            "dieselpunk": (
                "market",
                "city_street",
                "alley",
                "ruins",
                "train_station",
                "laboratory",
            ),
            "cyberpunk": (
                "market",
                "city_street",
                "alley",
                "ruins",
                "train_station",
                "laboratory",
                "spaceship",
            ),
            "solarpunk": (
                "market",
                "village",
                "city_street",
                "forest",
                "coast",
                "mountain",
                "floating_island",
                "laboratory",
                "spaceship",
            ),
            "space_opera": (
                "throne_room",
                "market",
                "city_street",
                "ruins",
                "desert",
                "floating_island",
                "laboratory",
                "spaceship",
                "alien_planet",
            ),
            "post_apocalyptic": (
                "village",
                "city_street",
                "alley",
                "forest",
                "ruins",
                "desert",
                "swamp",
                "train_station",
                "laboratory",
            ),
            "surreal": tuple(
                option.key
                for option in next(
                    category for category in BACKGROUND_CATEGORIES if category.key == "location"
                ).options
            ),
        },
    ),
    _compatibility(
        "world_genre",
        "architecture",
        {
            "high_fantasy": (
                "medieval",
                "gothic",
                "baroque",
                "east_asian",
                "islamic",
                "classical",
                "organic",
                "alien",
                "none",
            ),
            "dark_fantasy": (
                "medieval",
                "gothic",
                "baroque",
                "industrial",
                "brutalist",
                "organic",
                "alien",
                "none",
            ),
            "urban_fantasy": (
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "industrial",
                "brutalist",
                "futuristic",
                "organic",
                "none",
            ),
            "fairy_tale": (
                "medieval",
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "organic",
                "none",
            ),
            "mythic": (
                "medieval",
                "gothic",
                "baroque",
                "east_asian",
                "islamic",
                "classical",
                "organic",
                "alien",
                "none",
            ),
            "historical": (
                "medieval",
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "islamic",
                "classical",
                "industrial",
                "none",
            ),
            "gothic": ("medieval", "gothic", "baroque", "industrial", "none"),
            "steampunk": ("gothic", "baroque", "art_nouveau", "industrial"),
            "dieselpunk": ("art_nouveau", "industrial", "brutalist"),
            "cyberpunk": ("east_asian", "industrial", "brutalist", "futuristic"),
            "solarpunk": ("art_nouveau", "futuristic", "organic", "none"),
            "space_opera": ("classical", "brutalist", "futuristic", "organic", "alien", "none"),
            "post_apocalyptic": ("industrial", "brutalist", "futuristic", "organic", "none"),
        },
    ),
    _compatibility(
        "location",
        "architecture",
        {
            "castle": ("medieval", "gothic", "baroque", "east_asian", "islamic", "classical"),
            "throne_room": (
                "medieval",
                "gothic",
                "baroque",
                "east_asian",
                "islamic",
                "classical",
                "futuristic",
                "alien",
            ),
            "tavern": ("medieval", "gothic", "east_asian", "industrial"),
            "library": (
                "medieval",
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "industrial",
                "futuristic",
            ),
            "temple": (
                "medieval",
                "gothic",
                "baroque",
                "east_asian",
                "islamic",
                "classical",
                "futuristic",
                "organic",
                "alien",
            ),
            "market": (
                "medieval",
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "islamic",
                "industrial",
                "futuristic",
                "organic",
            ),
            "village": ("medieval", "east_asian", "islamic", "organic"),
            "city_street": (
                "medieval",
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "islamic",
                "classical",
                "industrial",
                "brutalist",
                "futuristic",
                "organic",
                "alien",
            ),
            "alley": (
                "medieval",
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "industrial",
                "brutalist",
                "futuristic",
            ),
            "forest": ("organic", "alien", "none"),
            "ruins": (
                "medieval",
                "gothic",
                "baroque",
                "east_asian",
                "islamic",
                "classical",
                "industrial",
                "brutalist",
                "futuristic",
                "organic",
                "alien",
            ),
            "cave": ("organic", "alien", "none"),
            "coast": ("organic", "alien", "none"),
            "desert": ("organic", "alien", "none"),
            "mountain": ("organic", "alien", "none"),
            "swamp": ("organic", "alien", "none"),
            "floating_island": (
                "medieval",
                "gothic",
                "east_asian",
                "classical",
                "futuristic",
                "organic",
                "alien",
                "none",
            ),
            "underwater_city": (
                "baroque",
                "art_nouveau",
                "classical",
                "futuristic",
                "organic",
                "alien",
            ),
            "train_station": (
                "gothic",
                "baroque",
                "art_nouveau",
                "east_asian",
                "industrial",
                "brutalist",
                "futuristic",
            ),
            "laboratory": ("industrial", "brutalist", "futuristic", "organic", "alien"),
            "spaceship": ("industrial", "brutalist", "futuristic", "organic", "alien"),
            "alien_planet": ("futuristic", "organic", "alien", "none"),
        },
    ),
    _compatibility(
        "location",
        "terrain",
        {
            "castle": ("flat", "rolling_hills", "jagged_mountains", "cliffs", "river", "lake"),
            "throne_room": ("interior",),
            "tavern": ("interior",),
            "library": ("interior",),
            "temple": ("interior", "flat", "rolling_hills", "jagged_mountains", "cliffs"),
            "market": ("interior", "flat", "rolling_hills"),
            "village": ("flat", "rolling_hills", "jagged_mountains", "river", "lake", "wetlands"),
            "city_street": ("flat", "rolling_hills"),
            "alley": ("flat", "rolling_hills"),
            "forest": (
                "flat",
                "rolling_hills",
                "jagged_mountains",
                "cliffs",
                "river",
                "lake",
                "wetlands",
                "volcanic",
                "glacial",
                "floating",
            ),
            "ruins": (
                "flat",
                "rolling_hills",
                "jagged_mountains",
                "cliffs",
                "river",
                "lake",
                "wetlands",
                "dunes",
                "volcanic",
                "glacial",
                "floating",
            ),
            "cave": ("interior", "volcanic", "glacial"),
            "coast": ("flat", "cliffs", "dunes"),
            "desert": ("flat", "rolling_hills", "cliffs", "dunes"),
            "mountain": ("jagged_mountains", "cliffs", "volcanic", "glacial"),
            "swamp": ("river", "lake", "wetlands"),
            "floating_island": ("jagged_mountains", "cliffs", "floating"),
            "underwater_city": ("seafloor",),
            "train_station": ("interior", "flat"),
            "laboratory": ("interior",),
            "spaceship": ("interior",),
            "alien_planet": (
                "flat",
                "rolling_hills",
                "jagged_mountains",
                "cliffs",
                "river",
                "lake",
                "wetlands",
                "dunes",
                "volcanic",
                "glacial",
                "floating",
            ),
        },
    ),
    _compatibility(
        "location",
        "spatial_scale",
        {
            "throne_room": ("room", "epic"),
            "tavern": ("intimate", "room"),
            "library": ("room", "epic"),
            "laboratory": ("room", "epic"),
            "spaceship": ("room", "district", "epic"),
            "alley": ("intimate", "street"),
            "train_station": ("room", "street", "epic"),
            "city_street": ("street", "district", "city"),
            "village": ("street", "district"),
        },
    ),
    _compatibility(
        "architecture",
        "environment_materials",
        {
            "medieval": ("stone", "wood", "brick", "marble", "metal", "overgrown"),
            "gothic": ("stone", "brick", "marble", "glass", "metal", "overgrown"),
            "baroque": ("stone", "wood", "marble", "glass", "metal"),
            "art_nouveau": ("wood", "brick", "glass", "metal"),
            "east_asian": ("stone", "wood", "brick"),
            "islamic": ("stone", "brick", "marble", "glass"),
            "classical": ("stone", "marble"),
            "industrial": ("brick", "concrete", "glass", "metal"),
            "brutalist": ("stone", "concrete", "glass", "metal"),
            "futuristic": ("concrete", "glass", "metal", "crystal"),
            "organic": ("wood", "crystal", "bone", "living_plants", "overgrown"),
            "alien": ("glass", "metal", "crystal", "bone", "living_plants"),
            "none": ("stone", "wood", "crystal", "bone", "living_plants", "overgrown"),
        },
    ),
    _compatibility(
        "location",
        "weather",
        {
            **dict.fromkeys(
                (
                    "castle",
                    "village",
                    "city_street",
                    "alley",
                    "forest",
                    "ruins",
                    "coast",
                    "desert",
                    "mountain",
                    "swamp",
                    "floating_island",
                    "alien_planet",
                ),
                _OUTDOOR_WEATHER,
            ),
            "throne_room": ("indoor",),
            "tavern": ("indoor",),
            "library": ("indoor",),
            "temple": (*_OUTDOOR_WEATHER, "indoor"),
            "market": (*_OUTDOOR_WEATHER, "indoor"),
            "cave": ("indoor",),
            "laboratory": ("indoor",),
            "spaceship": ("indoor",),
            "underwater_city": ("underwater",),
            "train_station": ("indoor", "clear", "cloudy", "fog", "drizzle", "rain", "snow"),
        },
    ),
    _compatibility(
        "terrain",
        "weather",
        {
            **dict.fromkeys(
                (
                    "flat",
                    "rolling_hills",
                    "jagged_mountains",
                    "cliffs",
                    "river",
                    "lake",
                    "wetlands",
                    "floating",
                ),
                _OUTDOOR_WEATHER,
            ),
            "interior": ("indoor",),
            "seafloor": ("underwater",),
            "dunes": ("clear", "cloudy", "wind", "dust", "magical_rain"),
            "glacial": (
                "clear",
                "cloudy",
                "fog",
                "snow",
                "blizzard",
                "wind",
                "aurora",
                "magical_rain",
            ),
            "volcanic": ("clear", "cloudy", "fog", "rain", "storm", "wind", "dust", "magical_rain"),
        },
    ),
    _compatibility(
        "season",
        "weather",
        {
            "spring": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "wind",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "summer": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "autumn": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "winter": (
                "clear",
                "cloudy",
                "fog",
                "snow",
                "blizzard",
                "wind",
                "aurora",
                "magical_rain",
                "indoor",
                "underwater",
            ),
        },
    ),
    _compatibility(
        "time_of_day",
        "weather",
        {
            "dawn": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "morning": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "noon": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "afternoon": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "golden_hour": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "dusk": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "aurora",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "night": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "aurora",
                "magical_rain",
                "indoor",
                "underwater",
            ),
            "midnight": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "snow",
                "blizzard",
                "wind",
                "dust",
                "aurora",
                "magical_rain",
                "indoor",
                "underwater",
            ),
        },
    ),
    _compatibility(
        "time_of_day",
        "background_lighting",
        {
            "dawn": (
                "soft_diffuse",
                "sunbeams",
                "backlit",
                "candlelit",
                "firelit",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
            "morning": (
                "soft_diffuse",
                "sunbeams",
                "backlit",
                "candlelit",
                "firelit",
                "neon",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
            "noon": (
                "soft_diffuse",
                "sunbeams",
                "backlit",
                "firelit",
                "neon",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
            "afternoon": (
                "soft_diffuse",
                "sunbeams",
                "backlit",
                "candlelit",
                "firelit",
                "neon",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
            "golden_hour": (
                "soft_diffuse",
                "sunbeams",
                "backlit",
                "candlelit",
                "firelit",
                "neon",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
            "night": (
                "soft_diffuse",
                "backlit",
                "moonlit",
                "candlelit",
                "firelit",
                "neon",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
            "midnight": (
                "soft_diffuse",
                "backlit",
                "moonlit",
                "candlelit",
                "firelit",
                "neon",
                "bioluminescent",
                "volumetric",
                "high_contrast",
            ),
        },
    ),
    _compatibility(
        "weather",
        "atmosphere",
        {
            "clear": ("peaceful", "cozy", "romantic", "lonely", "sacred", "dreamlike"),
            "fog": (
                "peaceful",
                "lonely",
                "melancholic",
                "mysterious",
                "ominous",
                "sacred",
                "dreamlike",
                "desolate",
            ),
            "drizzle": ("cozy", "romantic", "lonely", "melancholic", "mysterious", "dreamlike"),
            "rain": (
                "cozy",
                "romantic",
                "lonely",
                "melancholic",
                "mysterious",
                "ominous",
                "chaotic",
            ),
            "storm": ("mysterious", "ominous", "chaotic", "desolate"),
            "snow": ("peaceful", "cozy", "lonely", "mysterious", "sacred", "dreamlike", "desolate"),
            "blizzard": ("lonely", "ominous", "chaotic", "desolate"),
            "dust": ("mysterious", "ominous", "chaotic", "desolate"),
            "aurora": ("peaceful", "romantic", "mysterious", "sacred", "dreamlike"),
            "magical_rain": ("romantic", "mysterious", "sacred", "dreamlike"),
            "indoor": (
                "peaceful",
                "cozy",
                "romantic",
                "lonely",
                "mysterious",
                "ominous",
                "sacred",
                "dreamlike",
            ),
            "underwater": ("peaceful", "lonely", "mysterious", "ominous", "sacred", "dreamlike"),
        },
    ),
    _compatibility(
        "background_style",
        "rendering",
        {
            "anime": ("concept_art", "illustration", "stylized_3d", "line_and_color"),
            "semi_realistic": (
                "matte_painting",
                "concept_art",
                "illustration",
                "photoreal",
                "stylized_3d",
            ),
            "realistic": ("matte_painting", "concept_art", "photoreal"),
            "painterly": ("matte_painting", "concept_art", "illustration"),
            "watercolor": ("illustration", "line_and_color"),
            "ink": ("illustration", "line_and_color"),
            "comic": ("illustration", "line_and_color"),
            "pixel_art": ("pixel_rendering",),
            "cinematic": (
                "matte_painting",
                "concept_art",
                "photoreal",
                "stylized_3d",
            ),
        },
    ),
    # Expanded character catalog compatibility.
    _compatibility(
        "hair_length",
        "hair_style",
        {
            "pixie": ("wolf_cut", "shag", "slicked_back"),
            "chin": ("wolf_cut", "shag", "lob", "afro", "slicked_back"),
            "shoulder": (
                "wolf_cut",
                "shag",
                "lob",
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
                "side_bun",
                "dreadlocks",
                "afro",
                "slicked_back",
            ),
            "mid_back": (
                "wolf_cut",
                "shag",
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
                "side_bun",
                "dreadlocks",
                "slicked_back",
            ),
            "waist": (
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
                "side_bun",
                "dreadlocks",
                "slicked_back",
            ),
            "floor": (
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
                "side_bun",
                "dreadlocks",
                "slicked_back",
            ),
            "ear": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "bob",
                "wolf_cut",
                "shag",
                "messy",
                "undercut",
                "slicked_back",
            ),
            "neck": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "bob",
                "wolf_cut",
                "shag",
                "lob",
                "messy",
                "slicked_back",
            ),
            "chest": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "side_ponytail",
                "single_braid",
                "double_braids",
                "crown_braid",
                "bun",
                "double_buns",
                "messy",
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
                "side_bun",
                "dreadlocks",
            ),
            "hip": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "single_braid",
                "double_braids",
                "crown_braid",
                "bun",
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
                "side_bun",
                "dreadlocks",
            ),
            "ankle": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "twin_tails",
                "single_braid",
                "double_braids",
                "crown_braid",
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "braided_ponytail",
                "low_ponytail",
                "half_up",
                "topknot",
            ),
        },
    ),
    _compatibility(
        "hair_length",
        "bangs",
        {
            "pixie": ("micro", "choppy", "long_side"),
            "chin": ("micro", "arched", "choppy", "long_side", "split", "braided"),
            "shoulder": ("micro", "arched", "choppy", "long_side", "split", "braided"),
            "mid_back": ("micro", "arched", "choppy", "long_side", "split", "braided"),
            "waist": ("micro", "arched", "choppy", "long_side", "split", "braided"),
            "floor": ("micro", "arched", "choppy", "long_side", "split", "braided"),
            "ear": ("none", "micro", "choppy", "long_side", "side_swept", "wispy"),
            "neck": ("none", "straight", "side_swept", "micro", "choppy", "long_side"),
            "chest": (
                "none",
                "straight",
                "side_swept",
                "curtain",
                "wispy",
                "hime",
                "micro",
                "arched",
                "choppy",
                "long_side",
                "split",
                "braided",
            ),
            "hip": (
                "none",
                "straight",
                "side_swept",
                "curtain",
                "wispy",
                "hime",
                "micro",
                "arched",
                "choppy",
                "long_side",
                "split",
                "braided",
            ),
            "ankle": (
                "none",
                "straight",
                "side_swept",
                "curtain",
                "wispy",
                "hime",
                "micro",
                "arched",
                "choppy",
                "long_side",
                "split",
                "braided",
            ),
        },
    ),
    _compatibility("hair_style", "bangs", {"slicked_back": ("none", "long_side")}),
    _compatibility(
        "fantasy_race",
        "fantasy_traits",
        {
            "human": ("third_eye", "constellation_skin"),
            "elf": ("antlers", "third_eye", "constellation_skin"),
            "dark_elf": ("third_eye", "shadow_body", "constellation_skin"),
            "half_elf": ("third_eye", "constellation_skin"),
            "orc": ("stone_skin", "third_eye"),
            "goblin": ("third_eye",),
            "angel": ("constellation_skin",),
            "merfolk": ("fin_ears", "gills", "tentacle_hair"),
            "fairy": ("antlers", "constellation_skin"),
            "dragonkin": ("fin_ears", "gills", "stone_skin"),
            "automaton": ("third_eye", "stone_skin"),
            "android": ("third_eye", "constellation_skin"),
            "undead": ("third_eye", "shadow_body"),
            "elemental": (
                "antlers",
                "third_eye",
                "stone_skin",
                "wooden_skin",
                "flame_hair",
                "shadow_body",
                "constellation_skin",
            ),
            "aasimar": (
                "halo",
                "feathered_wings",
                "glowing_runes",
                "ethereal_aura",
                "constellation_skin",
            ),
            "oni": ("fangs", "horns", "glowing_runes", "stone_skin", "third_eye"),
            "dryad": ("pointed_ears", "antlers", "wooden_skin", "glowing_runes"),
            "djinn": ("ethereal_aura", "flame_hair", "shadow_body", "constellation_skin"),
            "shadowfolk": ("shadow_body", "third_eye", "glowing_runes", "ethereal_aura"),
            "mothfolk": ("fairy_wings", "pointed_ears", "fur", "extra_arms"),
            "avianfolk": ("feathered_wings", "pointed_ears", "extra_arms"),
            "lizardfolk": ("fangs", "tail", "scales", "fin_ears", "gills"),
            "slime_humanoid": ("slime_body", "ethereal_aura", "glowing_runes"),
            "living_doll": ("mechanical_limbs", "crystal_growths", "glowing_runes"),
            "golem": ("stone_skin", "wooden_skin", "crystal_growths", "glowing_runes"),
            "cyborg": ("mechanical_limbs", "third_eye", "glowing_runes"),
            "clone": ("mechanical_limbs", "constellation_skin"),
            "witchblood": ("third_eye", "glowing_runes", "ethereal_aura"),
            "fae_noble": ("pointed_ears", "antlers", "fairy_wings", "ethereal_aura"),
            "moonfolk": ("third_eye", "constellation_skin", "ethereal_aura"),
            "sunfolk": ("halo", "flame_hair", "constellation_skin", "ethereal_aura"),
            "voidborn": ("tentacle_hair", "third_eye", "shadow_body", "constellation_skin"),
            "giantkin": ("stone_skin", "glowing_runes"),
            "halfling": ("pointed_ears", "glowing_runes"),
        },
    ),
    _compatibility(
        "hair_color",
        "hair_color_pattern",
        {
            color.key: tuple(
                pattern.key
                for pattern in next(
                    category
                    for category in CHARACTER_CATEGORIES
                    if category.key == "hair_color_pattern"
                ).options
            )
            for color in next(
                category for category in CHARACTER_CATEGORIES if category.key == "hair_color"
            ).options
        },
    ),
    _compatibility(
        "eye_color",
        "iris_color_pattern",
        {
            color.key: tuple(
                pattern.key
                for pattern in next(
                    category
                    for category in CHARACTER_CATEGORIES
                    if category.key == "iris_color_pattern"
                ).options
            )
            for color in next(
                category for category in CHARACTER_CATEGORIES if category.key == "eye_color"
            ).options
        },
    ),
    _compatibility(
        "nipple_shape",
        "nipple_state",
        {
            "flat_profile": ("relaxed", "inverted", "partly_inverted", "puffy"),
        },
    ),
    _compatibility(
        "outfit_archetype",
        "outfit_materials",
        {
            "swimwear": ("silk", "translucent", "latex", "mesh"),
            "lingerie": ("silk", "lace", "translucent", "latex", "mesh"),
            "sheer_lingerie": ("silk", "lace", "translucent", "mesh"),
            "topless": ("bare_skin",),
            "nude": ("bare_skin",),
            "body_paint": ("bare_skin",),
            "bondage_fashion": ("leather", "metal", "latex", "mesh"),
        },
    ),
    _compatibility(
        "outfit_archetype",
        "outfit_palette",
        {
            "nude": ("natural_skin",),
            "topless": ("natural_skin",),
            "body_paint": ("natural_skin", "rainbow", "iridescent"),
        },
    ),
    _compatibility(
        "outfit_archetype",
        "accessories",
        {"nude": ("body_chain", "nipple_jewelry"), "topless": ("body_chain", "nipple_jewelry")},
    ),
    _compatibility(
        "pose",
        "outfit_archetype",
        {
            "artistic_nude": ("nude", "body_paint"),
            "topless_pose": ("topless", "nude", "body_paint"),
            "nude_recline": ("nude", "lingerie", "sheer_lingerie", "body_paint"),
            "erotic_kneel": ("lingerie", "sheer_lingerie", "nude", "bondage_fashion"),
            "open_leg_pose": ("lingerie", "sheer_lingerie", "nude", "bondage_fashion"),
            "breast_touch": ("topless", "nude", "lingerie", "sheer_lingerie"),
            "nude_back_arch": ("nude", "body_paint"),
            "self_pleasure": ("nude", "lingerie", "sheer_lingerie"),
            "erotic_stretch": ("lingerie", "sheer_lingerie", "nude"),
            "intimate_bondage_pose": ("bondage_fashion", "lingerie", "nude"),
        },
    ),
    _compatibility(
        "framing",
        "pose",
        {
            "extreme_closeup": ("standing", "seated", "looking_back"),
            "turnaround_sheet": ("standing", "weapon_ready"),
        },
    ),
    _compatibility(
        "eyewear",
        "distinctive_marks",
        {
            option.key: tuple(
                mark.key
                for mark in next(
                    category
                    for category in CHARACTER_CATEGORIES
                    if category.key == "distinctive_marks"
                ).options
                if mark.key
                not in {
                    "glasses",
                    "round_glasses",
                    "square_glasses",
                    "rimless_glasses",
                    "half_rim_glasses",
                    "cat_eye_glasses",
                    "monocle",
                    "goggles",
                    "sunglasses",
                    "tinted_glasses",
                    "visor",
                    "reading_glasses",
                    "eyepatch",
                }
            )
            for option in next(
                category for category in CHARACTER_CATEGORIES if category.key == "eyewear"
            ).options
            if option.key != "none"
        },
    ),
    # Expanded background catalog compatibility.
    _compatibility(
        "world_genre",
        "location",
        {
            "wuxia": (
                "palace_garden",
                "temple",
                "market",
                "village",
                "city_street",
                "forest",
                "bamboo_grove",
                "mountain",
                "tavern",
            ),
            "xianxia": (
                "palace_garden",
                "temple",
                "forest",
                "bamboo_grove",
                "mountain",
                "floating_island",
                "waterfall",
                "cave",
            ),
            "biopunk": (
                "laboratory",
                "greenhouse",
                "archive",
                "city_street",
                "alien_planet",
                "megacity_rooftop",
            ),
            "clockpunk": (
                "workshop",
                "observatory",
                "train_station",
                "airship",
                "city_street",
                "library",
            ),
            "atompunk": (
                "laboratory",
                "archive",
                "train_station",
                "spaceship",
                "city_street",
                "observatory",
            ),
            "arcanepunk": (
                "workshop",
                "laboratory",
                "city_street",
                "market",
                "airship",
                "observatory",
                "megacity_rooftop",
            ),
            "gaslamp": (
                "cathedral",
                "library",
                "archive",
                "city_street",
                "alley",
                "train_station",
                "observatory",
            ),
            "nautical_fantasy": (
                "harbor",
                "coast",
                "tavern",
                "underwater_city",
                "waterfall",
                "airship",
            ),
            "cosmic_horror": (
                "observatory",
                "ruins",
                "cave",
                "laboratory",
                "alien_planet",
                "archive",
            ),
            "cozy_fantasy": (
                "tavern",
                "village",
                "market",
                "forest",
                "greenhouse",
                "hot_spring",
                "workshop",
            ),
        },
    ),
    _compatibility(
        "world_genre",
        "architecture",
        {
            "wuxia": ("east_asian", "vernacular", "none"),
            "xianxia": ("east_asian", "organic", "crystalline", "none"),
            "biopunk": ("futuristic", "organic", "biomechanical", "alien"),
            "clockpunk": ("victorian", "art_nouveau", "industrial"),
            "atompunk": ("art_deco", "industrial", "brutalist", "futuristic"),
            "arcanepunk": ("gothic", "art_nouveau", "industrial", "futuristic", "crystalline"),
            "gaslamp": ("gothic", "victorian", "art_nouveau", "industrial"),
            "nautical_fantasy": ("medieval", "vernacular", "industrial", "organic", "none"),
            "cosmic_horror": ("gothic", "brutalist", "alien", "biomechanical", "none"),
            "cozy_fantasy": ("medieval", "vernacular", "east_asian", "organic", "none"),
        },
    ),
    _compatibility(
        "location",
        "architecture",
        {
            "palace_garden": (
                "baroque",
                "rococo",
                "east_asian",
                "islamic",
                "classical",
                "renaissance",
            ),
            "cathedral": ("gothic", "baroque", "byzantine", "renaissance"),
            "observatory": (
                "gothic",
                "art_nouveau",
                "classical",
                "industrial",
                "futuristic",
                "crystalline",
            ),
            "workshop": ("medieval", "vernacular", "victorian", "industrial", "futuristic"),
            "greenhouse": ("art_nouveau", "victorian", "futuristic", "organic", "biomechanical"),
            "archive": ("gothic", "brutalist", "industrial", "futuristic"),
            "harbor": ("medieval", "vernacular", "victorian", "industrial", "futuristic"),
            "waterfall": ("organic", "crystalline", "none"),
            "canyon": ("organic", "alien", "none"),
            "tundra": ("organic", "alien", "none"),
            "bamboo_grove": ("east_asian", "organic", "none"),
            "hot_spring": ("east_asian", "vernacular", "organic", "none"),
            "battlefield": ("medieval", "industrial", "futuristic", "none"),
            "airship": ("victorian", "industrial", "futuristic"),
            "megacity_rooftop": ("art_deco", "industrial", "brutalist", "futuristic"),
        },
    ),
    _compatibility(
        "location",
        "terrain",
        {
            "palace_garden": ("flat", "rolling_hills", "river", "lake"),
            "cathedral": ("interior",),
            "observatory": ("interior", "plateau", "jagged_mountains", "urban"),
            "workshop": ("interior", "urban"),
            "greenhouse": ("interior",),
            "archive": ("interior",),
            "harbor": ("flat", "urban"),
            "waterfall": ("cliffs", "river", "rolling_hills"),
            "canyon": ("cliffs", "badlands", "river"),
            "tundra": ("flat", "glacial", "plateau"),
            "bamboo_grove": ("rolling_hills", "river", "giant_mushrooms"),
            "hot_spring": ("rolling_hills", "jagged_mountains", "volcanic"),
            "battlefield": ("flat", "rolling_hills", "badlands", "urban"),
            "airship": ("interior", "cloud_sea"),
            "megacity_rooftop": ("urban",),
        },
    ),
    _compatibility(
        "location",
        "spatial_scale",
        {
            "palace_garden": ("courtyard", "district", "landscape"),
            "cathedral": ("room", "epic"),
            "observatory": ("room", "epic"),
            "workshop": ("intimate", "room"),
            "greenhouse": ("room", "district"),
            "archive": ("room", "district"),
            "harbor": ("district", "city", "landscape"),
            "waterfall": ("landscape", "epic"),
            "canyon": ("landscape", "epic"),
            "tundra": ("landscape", "epic"),
            "airship": ("room", "district", "epic"),
            "megacity_rooftop": ("street", "city", "epic"),
        },
    ),
    _compatibility(
        "location",
        "weather",
        {
            "cathedral": ("indoor",),
            "workshop": ("indoor",),
            "greenhouse": ("indoor",),
            "archive": ("indoor",),
            "airship": (*_OUTDOOR_WEATHER, "indoor"),
            "palace_garden": _OUTDOOR_WEATHER,
            "observatory": (*_OUTDOOR_WEATHER, "indoor"),
            "harbor": _OUTDOOR_WEATHER,
            "waterfall": _OUTDOOR_WEATHER,
            "canyon": _OUTDOOR_WEATHER,
            "tundra": _OUTDOOR_WEATHER,
            "bamboo_grove": _OUTDOOR_WEATHER,
            "hot_spring": _OUTDOOR_WEATHER,
            "battlefield": _OUTDOOR_WEATHER,
            "megacity_rooftop": _OUTDOOR_WEATHER,
        },
    ),
    _compatibility(
        "architecture",
        "environment_materials",
        {
            "renaissance": ("stone", "brick", "plaster", "marble", "wood"),
            "rococo": ("plaster", "marble", "wood", "glass", "fabric"),
            "victorian": ("brick", "wood", "glass", "copper", "brass"),
            "byzantine": ("stone", "brick", "marble", "glass"),
            "art_deco": ("concrete", "glass", "metal", "brass", "marble"),
            "vernacular": ("stone", "wood", "brick", "plaster", "terracotta"),
            "crystalline": ("crystal", "glass", "ice"),
            "biomechanical": ("metal", "bone", "biomass", "living_plants"),
        },
    ),
    _compatibility(
        "terrain",
        "weather",
        {
            "plateau": _OUTDOOR_WEATHER,
            "badlands": ("clear", "cloudy", "wind", "dust", "heat_haze", "ashfall"),
            "rice_terraces": ("clear", "cloudy", "fog", "drizzle", "rain", "wind", "rainbow"),
            "coral_reef": ("underwater",),
            "urban": _OUTDOOR_WEATHER,
            "cloud_sea": ("clear", "cloudy", "fog", "wind", "aurora", "meteor_shower"),
            "giant_mushrooms": ("clear", "cloudy", "fog", "drizzle", "rain", "pollen"),
            "salt_flat": ("clear", "cloudy", "wind", "dust", "heat_haze", "meteor_shower"),
        },
    ),
    _compatibility(
        "season",
        "weather",
        {
            "spring": ("hail", "pollen", "rainbow"),
            "summer": ("heat_haze", "lightning", "meteor_shower", "pollen", "rainbow"),
            "autumn": ("hail", "lightning", "ashfall", "meteor_shower", "rainbow"),
            "winter": ("hail", "sleet", "lightning", "meteor_shower"),
            "early_spring": (
                "clear",
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "sleet",
                "hail",
                "pollen",
                "rainbow",
                "indoor",
                "underwater",
            ),
            "rainy_season": (
                "cloudy",
                "fog",
                "drizzle",
                "rain",
                "storm",
                "lightning",
                "rainbow",
                "indoor",
                "underwater",
            ),
            "dry_season": (
                "clear",
                "cloudy",
                "wind",
                "dust",
                "heat_haze",
                "ashfall",
                "indoor",
                "underwater",
            ),
            "eternal_winter": (
                "clear",
                "cloudy",
                "fog",
                "snow",
                "blizzard",
                "sleet",
                "hail",
                "aurora",
                "indoor",
                "underwater",
            ),
        },
    ),
    _compatibility(
        "time_of_day",
        "weather",
        {
            "dawn": ("hail", "sleet", "heat_haze", "lightning", "ashfall", "pollen", "rainbow"),
            "morning": ("hail", "sleet", "heat_haze", "lightning", "ashfall", "pollen", "rainbow"),
            "noon": ("hail", "sleet", "heat_haze", "lightning", "ashfall", "pollen", "rainbow"),
            "afternoon": (
                "hail",
                "sleet",
                "heat_haze",
                "lightning",
                "ashfall",
                "pollen",
                "rainbow",
            ),
            "golden_hour": (
                "hail",
                "sleet",
                "heat_haze",
                "lightning",
                "ashfall",
                "pollen",
                "rainbow",
            ),
            "dusk": (
                "hail",
                "sleet",
                "heat_haze",
                "lightning",
                "meteor_shower",
                "ashfall",
                "pollen",
                "rainbow",
            ),
            "night": (
                "hail",
                "sleet",
                "heat_haze",
                "lightning",
                "meteor_shower",
                "ashfall",
                "pollen",
                "rainbow",
            ),
            "midnight": (
                "hail",
                "sleet",
                "heat_haze",
                "lightning",
                "meteor_shower",
                "ashfall",
                "pollen",
                "rainbow",
            ),
            "blue_hour": _OUTDOOR_WEATHER,
            "twilight": _OUTDOOR_WEATHER,
            "pre_dawn": _OUTDOOR_WEATHER,
            "eclipse": _OUTDOOR_WEATHER,
            "timeless_light": (*_OUTDOOR_WEATHER, "indoor", "underwater"),
        },
    ),
    _compatibility(
        "time_of_day",
        "background_lighting",
        {
            "blue_hour": (
                "soft_diffuse",
                "backlit",
                "moonlit",
                "starlight",
                "lanterns",
                "window_light",
                "god_rays",
                "underwater_caustics",
                "magic_crystals",
            ),
            "twilight": (
                "soft_diffuse",
                "backlit",
                "moonlit",
                "starlight",
                "lanterns",
                "god_rays",
                "eclipse_rim",
                "magic_crystals",
            ),
            "pre_dawn": (
                "soft_diffuse",
                "moonlit",
                "starlight",
                "lanterns",
                "window_light",
                "bioluminescent",
                "magic_crystals",
            ),
            "eclipse": (
                "backlit",
                "volumetric",
                "high_contrast",
                "eclipse_rim",
                "electrical_arcs",
                "magic_crystals",
            ),
            "timeless_light": tuple(
                option.key
                for option in next(
                    category
                    for category in BACKGROUND_CATEGORIES
                    if category.key == "background_lighting"
                ).options
            ),
        },
    ),
    _compatibility(
        "weather",
        "atmosphere",
        {
            "hail": ("tense", "ominous", "chaotic", "adventurous"),
            "sleet": ("lonely", "melancholic", "tense", "desolate"),
            "heat_haze": ("oppressive", "tense", "desolate", "adventurous"),
            "lightning": ("tense", "ominous", "chaotic", "triumphant"),
            "meteor_shower": ("romantic", "mysterious", "sacred", "dreamlike", "triumphant"),
            "ashfall": ("ominous", "oppressive", "desolate", "uncanny"),
            "pollen": ("peaceful", "romantic", "dreamlike", "whimsical"),
            "rainbow": ("peaceful", "romantic", "triumphant", "festive", "whimsical"),
        },
    ),
    _compatibility(
        "background_style",
        "rendering",
        {
            "watercolor": ("watercolor_rendering",),
            "ink": ("ink_rendering",),
            "pixel_art": ("voxel",),
            "storybook": ("illustration", "watercolor_rendering", "line_and_color"),
            "gouache": ("matte_painting", "illustration"),
            "oil_painting": ("matte_painting", "illustration"),
            "woodblock": ("ink_rendering", "line_and_color"),
            "retro_anime": ("illustration", "line_and_color"),
            "low_poly": ("stylized_3d", "voxel"),
            "miniature": ("miniature_render", "stylized_3d"),
            "architectural_sketch": ("blueprint", "cutaway", "line_and_color"),
        },
    ),
    _compatibility(
        "fantasy_race",
        "fantasy_traits",
        {
            "centaur": ("equine_lower_body", "hooves", "tail", "pointed_ears"),
            "minotaur": ("bovine_horns", "hooves", "fur", "tail"),
            "satyr": ("goat_horns", "hooves", "fur", "pointed_ears", "tail"),
            "harpy": ("feathered_wings", "bird_feathers", "avian_talons", "beak"),
            "naga": ("serpentine_lower_body", "scales", "fangs", "fin_ears"),
            "kitsune": (
                "fox_ears",
                "multiple_tails",
                "fur",
                "fangs",
                "ethereal_aura",
            ),
            "tengu": ("bird_feathers", "feathered_wings", "avian_talons", "beak"),
            "phoenixkin": (
                "phoenix_wings",
                "bird_feathers",
                "avian_talons",
                "flame_hair",
                "ethereal_aura",
            ),
            "insectfolk": (
                "insect_antennae",
                "chitin",
                "compound_eyes",
                "extra_arms",
                "fairy_wings",
            ),
            "sharkfolk": ("shark_tail", "gills", "fin_ears", "fangs", "scales"),
            "plantfolk": ("living_vines", "body_flowers", "wooden_skin"),
            "mushroomfolk": ("mushroom_cap", "spore_cloud", "wooden_skin"),
            "crystalborn": ("crystal_body", "crystal_growths", "glowing_runes"),
            "celestial": (
                "halo",
                "feathered_wings",
                "constellation_skin",
                "ethereal_aura",
            ),
            "eldritch": (
                "tentacle_hair",
                "extra_arms",
                "third_eye",
                "eye_cluster",
                "shadow_body",
            ),
            "ursafolk": ("fur", "fangs", "tail"),
            "cervidfolk": ("antlers", "hooves", "fur", "tail"),
            "bovinefolk": ("bovine_horns", "hooves", "fur", "tail"),
            "spiderfolk": (
                "spider_lower_body",
                "extra_arms",
                "chitin",
                "eye_cluster",
                "web_spinnerets",
            ),
            "deepfolk": (
                "gills",
                "fin_ears",
                "scales",
                "tentacle_hair",
                "bioluminescent_patterns",
            ),
        },
    ),
    _compatibility(
        "hair_length",
        "hair_style",
        {
            "pixie": ("bixie", "mullet", "pompadour", "quiff"),
            "chin": ("pageboy", "bixie", "mullet", "victory_rolls", "pompadour", "quiff"),
            "shoulder": (
                "pageboy",
                "mullet",
                "cornrows",
                "box_braids",
                "halo_braid",
                "bubble_ponytail",
                "space_buns",
                "victory_rolls",
                "pompadour",
                "quiff",
                "locs_updo",
            ),
            "mid_back": (
                "cornrows",
                "box_braids",
                "halo_braid",
                "bubble_ponytail",
                "space_buns",
                "victory_rolls",
                "locs_updo",
            ),
            "waist": (
                "cornrows",
                "box_braids",
                "halo_braid",
                "bubble_ponytail",
                "space_buns",
                "locs_updo",
            ),
            "floor": ("box_braids", "halo_braid", "bubble_ponytail", "locs_updo"),
            "cheek": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "bob",
                "wolf_cut",
                "shag",
                "pageboy",
                "bixie",
                "mullet",
                "pompadour",
                "quiff",
            ),
            "lower_back": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "high_ponytail",
                "twin_tails",
                "single_braid",
                "double_braids",
                "french_braid",
                "fishtail_braid",
                "waterfall_braid",
                "rope_braid",
                "box_braids",
                "bubble_ponytail",
                "locs_updo",
            ),
            "calf": (
                "loose_straight",
                "loose_wavy",
                "curly",
                "ponytail",
                "twin_tails",
                "single_braid",
                "double_braids",
                "french_braid",
                "fishtail_braid",
                "rope_braid",
                "box_braids",
                "bubble_ponytail",
            ),
        },
    ),
    _compatibility(
        "hair_length",
        "bangs",
        {
            "pixie": ("bottleneck", "side_piece", "feathered", "curly", "asymmetrical"),
            "chin": (
                "bottleneck",
                "side_piece",
                "feathered",
                "curly",
                "asymmetrical",
                "veil",
            ),
            "shoulder": (
                "bottleneck",
                "side_piece",
                "feathered",
                "curly",
                "asymmetrical",
                "veil",
            ),
            "mid_back": (
                "bottleneck",
                "side_piece",
                "feathered",
                "curly",
                "asymmetrical",
                "veil",
            ),
            "waist": ("side_piece", "feathered", "curly", "asymmetrical", "veil"),
            "floor": ("side_piece", "feathered", "curly", "asymmetrical", "veil"),
            "cheek": (
                "none",
                "straight",
                "side_swept",
                "wispy",
                "micro",
                "choppy",
                "bottleneck",
                "side_piece",
                "feathered",
                "curly",
                "asymmetrical",
            ),
            "lower_back": tuple(
                option.key
                for option in next(
                    category for category in CHARACTER_CATEGORIES if category.key == "bangs"
                ).options
            ),
            "calf": tuple(
                option.key
                for option in next(
                    category for category in CHARACTER_CATEGORIES if category.key == "bangs"
                ).options
            ),
        },
    ),
    _compatibility(
        "hair_style",
        "bangs",
        {
            "pompadour": ("none", "side_piece"),
            "quiff": ("none", "side_swept", "side_piece"),
            "cornrows": ("none",),
            "halo_braid": ("none", "side_piece", "wispy"),
        },
    ),
    _compatibility(
        "pose",
        "outfit_archetype",
        {
            "sensual_recline": ("lingerie", "sheer_lingerie", "nude", "body_paint"),
            "intimate_arch": ("lingerie", "sheer_lingerie", "nude", "body_paint"),
            "erotic_squat": ("lingerie", "sheer_lingerie", "nude", "bondage_fashion"),
            "inviting_open_pose": (
                "lingerie",
                "sheer_lingerie",
                "nude",
                "bondage_fashion",
            ),
        },
    ),
    _compatibility(
        "background_style",
        "rendering",
        {
            "pastel_chalk": ("pastel_rendering", "illustration"),
            "dark_academia": ("matte_painting", "concept_art", "illustration"),
            "synthwave": ("cel_rendering", "stylized_3d", "illustration"),
            "papercut": ("paper_cut_rendering",),
            "linocut": ("linocut_rendering", "ink_rendering"),
            "ukiyo_e": ("linocut_rendering", "ink_rendering", "line_and_color"),
            "surreal_collage": ("collage_rendering",),
            "scientific_plate": ("scientific_rendering", "line_and_color"),
        },
    ),
)


_MUTUAL_EXCLUSION_RULES: Final[tuple[_MutualExclusionRule, ...]] = (
    _MutualExclusionRule(
        category="makeup",
        incompatible_pairs=tuple(
            ("no_makeup", option.key)
            for option in next(
                category for category in CHARACTER_CATEGORIES if category.key == "makeup"
            ).options
            if option.key != "no_makeup"
        ),
    ),
    _MutualExclusionRule(
        category="fantasy_traits",
        incompatible_pairs=(
            ("halo", "horns"),
            ("halo", "bat_wings"),
            ("halo", "dragon_wings"),
            ("feathered_wings", "bat_wings"),
            ("feathered_wings", "fairy_wings"),
            ("feathered_wings", "dragon_wings"),
            ("bat_wings", "fairy_wings"),
            ("bat_wings", "dragon_wings"),
            ("fairy_wings", "dragon_wings"),
            ("fur", "scales"),
            ("feathered_wings", "phoenix_wings"),
            ("bat_wings", "phoenix_wings"),
            ("fairy_wings", "phoenix_wings"),
            ("dragon_wings", "phoenix_wings"),
            ("equine_lower_body", "serpentine_lower_body"),
            ("equine_lower_body", "spider_lower_body"),
            ("serpentine_lower_body", "spider_lower_body"),
            ("halo", "bovine_horns"),
            ("halo", "goat_horns"),
            ("fur", "chitin"),
            ("fur", "crystal_body"),
            ("crystal_body", "liquid_body"),
            ("crystal_body", "smoke_body"),
            ("liquid_body", "smoke_body"),
        ),
    ),
    _MutualExclusionRule(
        category="history_and_decay",
        incompatible_pairs=(
            ("pristine", "weathered"),
            ("pristine", "cracked"),
            ("pristine", "overgrown"),
            ("pristine", "flooded"),
            ("pristine", "burned"),
            ("pristine", "battle_damage"),
            ("pristine", "repaired"),
            ("pristine", "dusty"),
            ("pristine", "rusted"),
            ("pristine", "collapsed"),
            ("pristine", "frozen_over"),
            ("pristine", "sand_buried"),
            ("pristine", "mossy"),
            ("newly_built", "weathered"),
            ("newly_built", "cracked"),
            ("newly_built", "overgrown"),
            ("newly_built", "flooded"),
            ("newly_built", "burned"),
            ("newly_built", "battle_damage"),
            ("newly_built", "repaired"),
            ("newly_built", "dusty"),
            ("newly_built", "rusted"),
            ("newly_built", "collapsed"),
            ("newly_built", "frozen_over"),
            ("newly_built", "sand_buried"),
            ("newly_built", "mossy"),
            ("restored", "collapsed"),
            ("restored", "burned"),
            ("restored", "battle_damage"),
            *(
                ("pristine", key)
                for key in (
                    "salt_eroded",
                    "soot_stained",
                    "petrified",
                    "vine_reclaimed",
                    "excavated",
                    "storm_damaged",
                    "unfinished",
                    "ritual_stained",
                )
            ),
            *(
                ("newly_built", key)
                for key in (
                    "salt_eroded",
                    "soot_stained",
                    "petrified",
                    "vine_reclaimed",
                    "excavated",
                    "storm_damaged",
                    "unfinished",
                    "ritual_stained",
                )
            ),
            ("restored", "storm_damaged"),
            ("restored", "unfinished"),
            ("restored", "soot_stained"),
        ),
    ),
    _MutualExclusionRule(
        category="weather",
        incompatible_pairs=(
            ("clear", "cloudy"),
            ("clear", "fog"),
            ("clear", "drizzle"),
            ("clear", "rain"),
            ("clear", "storm"),
            ("clear", "snow"),
            ("clear", "blizzard"),
            ("clear", "dust"),
            ("cloudy", "dust"),
            ("fog", "dust"),
            ("drizzle", "rain"),
            ("drizzle", "storm"),
            ("drizzle", "snow"),
            ("drizzle", "blizzard"),
            ("drizzle", "dust"),
            ("rain", "storm"),
            ("rain", "snow"),
            ("rain", "blizzard"),
            ("rain", "dust"),
            ("storm", "snow"),
            ("storm", "blizzard"),
            ("storm", "dust"),
            ("storm", "aurora"),
            ("snow", "blizzard"),
            ("snow", "dust"),
            ("blizzard", "dust"),
            ("blizzard", "aurora"),
            ("magical_rain", "clear"),
            ("magical_rain", "snow"),
            ("magical_rain", "blizzard"),
            ("magical_rain", "dust"),
            ("indoor", "clear"),
            ("indoor", "cloudy"),
            ("indoor", "fog"),
            ("indoor", "drizzle"),
            ("indoor", "rain"),
            ("indoor", "storm"),
            ("indoor", "snow"),
            ("indoor", "blizzard"),
            ("indoor", "wind"),
            ("indoor", "dust"),
            ("indoor", "aurora"),
            ("indoor", "magical_rain"),
            ("underwater", "indoor"),
            ("underwater", "clear"),
            ("underwater", "cloudy"),
            ("underwater", "fog"),
            ("underwater", "drizzle"),
            ("underwater", "rain"),
            ("underwater", "storm"),
            ("underwater", "snow"),
            ("underwater", "blizzard"),
            ("underwater", "wind"),
            ("underwater", "dust"),
            ("underwater", "aurora"),
            ("underwater", "magical_rain"),
            ("clear", "hail"),
            ("clear", "lightning"),
            ("clear", "ashfall"),
            ("hail", "heat_haze"),
            ("hail", "dust"),
            ("hail", "pollen"),
            ("sleet", "heat_haze"),
            ("sleet", "dust"),
            ("sleet", "ashfall"),
            ("sleet", "pollen"),
            ("heat_haze", "snow"),
            ("heat_haze", "blizzard"),
            ("lightning", "aurora"),
            ("lightning", "meteor_shower"),
            ("lightning", "rainbow"),
            ("meteor_shower", "cloudy"),
            ("meteor_shower", "fog"),
            ("meteor_shower", "rain"),
            ("meteor_shower", "storm"),
            ("meteor_shower", "blizzard"),
            ("meteor_shower", "ashfall"),
            ("ashfall", "rain"),
            ("ashfall", "snow"),
            ("ashfall", "blizzard"),
            ("ashfall", "pollen"),
            ("ashfall", "rainbow"),
            ("pollen", "rain"),
            ("pollen", "storm"),
            ("pollen", "snow"),
            ("pollen", "blizzard"),
            ("rainbow", "storm"),
            ("rainbow", "blizzard"),
            ("rainbow", "dust"),
            *(
                ("indoor", key)
                for key in (
                    "hail",
                    "sleet",
                    "heat_haze",
                    "lightning",
                    "meteor_shower",
                    "ashfall",
                    "pollen",
                    "rainbow",
                )
            ),
            *(
                ("underwater", key)
                for key in (
                    "hail",
                    "sleet",
                    "heat_haze",
                    "lightning",
                    "meteor_shower",
                    "ashfall",
                    "pollen",
                    "rainbow",
                )
            ),
        ),
    ),
    _MutualExclusionRule(
        category="character_state",
        incompatible_pairs=(
            ("pouting", "contemptuous_face"),
            ("alert", "dazed"),
            ("alert", "sleepy"),
            ("cold_shivering", "overheated"),
            ("well_rested", "sleep_deprived"),
            ("well_rested", "exhausted"),
            ("well_rested", "sleepy"),
            ("well_rested", "magically_exhausted"),
            ("well_rested", "poisoned"),
            ("alert", "asleep"),
            ("alert", "unconscious"),
            ("alert", "magically_exhausted"),
            ("asleep", "unconscious"),
            ("asleep", "waking_up"),
            ("asleep", "coughing"),
            ("asleep", "sneezing"),
            ("asleep", "yawning"),
            ("unconscious", "waking_up"),
            ("unconscious", "coughing"),
            ("unconscious", "sneezing"),
            ("unconscious", "yawning"),
            ("magically_charged", "magically_exhausted"),
            ("partially_petrified", "spectral_phase"),
            ("partially_petrified", "flickering_invisibility"),
            ("spectral_phase", "flickering_invisibility"),
        ),
    ),
    _MutualExclusionRule(
        category="adult_female_state",
        incompatible_pairs=tuple(
            (active, post)
            for active in sorted(_ADULT_FEMALE_ACTIVE_STATE_KEYS)
            for post in sorted(_ADULT_FEMALE_POST_STATE_KEYS)
        ),
    ),
)


_YOUTH_WORDS: Final[frozenset[str]] = frozenset(
    {
        "adolescent",
        "baby",
        "boy",
        "child",
        "children",
        "girl",
        "kid",
        "kids",
        "loli",
        "minor",
        "juvenile",
        "preteen",
        "schoolboy",
        "schoolgirl",
        "shota",
        "teen",
        "teenage",
        "teenager",
        "toddler",
        "tween",
        "underage",
        "youth",
    }
)
_GENDER_WORDS: Final[frozenset[str]] = frozenset(
    {"female", "females", "male", "males", "man", "men", "woman", "women"}
)
_MINOR_NUMBER_WORDS: Final[tuple[str, ...]] = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
)
_MINOR_AGE_VALUE: Final = rf"(?:[0-9]|1[0-7]|{'|'.join(_MINOR_NUMBER_WORDS)})"
_MINOR_YEAR_OLD_PATTERN: Final = re.compile(
    rf"\b{_MINOR_AGE_VALUE}[\s_.-]*(?:years?|yrs?)[\s_.-]*old\b",
    re.IGNORECASE,
)
_MINOR_YO_PATTERN: Final = re.compile(
    rf"\b{_MINOR_AGE_VALUE}[\s_.-]*y[\s_./-]*o(?:\b|\.)",
    re.IGNORECASE,
)
_MINOR_AGE_LABEL_PATTERN: Final = re.compile(
    rf"\bage(?:d)?[\s_:=-]*{_MINOR_AGE_VALUE}\b",
    re.IGNORECASE,
)
_MINOR_OF_AGE_PATTERN: Final = re.compile(
    rf"(?:\b{_MINOR_AGE_VALUE}[\s_.-]*(?:years?|yrs?)[\s_.-]*of[\s_.-]*age\b|"
    rf"\bage(?:d)?[\s_.-]*of[\s_.-]*{_MINOR_AGE_VALUE}\b)",
    re.IGNORECASE,
)
_UNDER_EIGHTEEN_PATTERN: Final = re.compile(
    r"\b(?:under|below|less[-_.\s]*than|younger[-_.\s]*than|almost|"
    r"not[-_.\s]*yet)"
    r"[-_.\s]*(?:18|eighteen)\b",
    re.IGNORECASE,
)


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", value.casefold()))


def _has_minor_age_marker(value: str) -> bool:
    return any(
        pattern.search(value)
        for pattern in (
            _MINOR_YEAR_OLD_PATTERN,
            _MINOR_YO_PATTERN,
            _MINOR_AGE_LABEL_PATTERN,
            _MINOR_OF_AGE_PATTERN,
            _UNDER_EIGHTEEN_PATTERN,
        )
    )


def _prompt_tokens(value: str) -> list[str]:
    normalized = normalize_english_image_prompt(value)
    return [token.strip() for token in normalized.split(",") if token.strip()]


def _dedupe(tokens: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        key = token.casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(token)
    return result


_COLOR_CATEGORY_SUFFIXES: Final[dict[str, str]] = {
    "hair_color": "hair",
    "eye_color": "eyes",
}


def _color_descriptor(prompt_en: str, suffix: str) -> str:
    ending = f" {suffix}"
    if not prompt_en.casefold().endswith(ending):
        raise ValueError(f"多色分類的英文提示詞必須以 {suffix!r} 結尾")
    descriptor = prompt_en[: -len(ending)].strip()
    if descriptor.endswith("-colored"):
        descriptor = descriptor[: -len("-colored")]
    return descriptor


def _join_color_descriptors(descriptors: Sequence[str]) -> str:
    if len(descriptors) == 2:
        return " and ".join(descriptors)
    return f"{descriptors[0]} with {' and '.join(descriptors[1:])}"


def _compose_hair_colors(options: Sequence[TagOption], pattern: str | None) -> str:
    if len(options) == 1 and pattern is None:
        return options[0].prompt_en
    colors = [_color_descriptor(option.prompt_en, "hair") for option in options]
    if pattern in {None, "solid"}:
        if len(colors) == 1:
            return options[0].prompt_en
        if len(colors) == 2:
            return f"{_join_color_descriptors(colors)} multicolored hair"
        return f"multicolored hair combining {_join_color_descriptors(colors)}"
    if pattern in {"two_tone", "split_dye", "gradient", "ombre", "color_blocks"}:
        joined = "-to-".join(colors) if pattern in {"gradient", "ombre"} else " and ".join(colors)
        noun = {
            "two_tone": "two-tone hair",
            "split_dye": "split-dye hair",
            "gradient": "gradient hair",
            "ombre": "ombre hair",
            "color_blocks": "color-blocked hair",
        }[pattern]
        return f"{joined} {noun}"

    base = colors[0]
    accents = colors[1:] or colors
    accent = _join_color_descriptors(accents) if len(accents) > 1 else accents[0]
    templates = {
        "highlights": "{base} hair with {accent} highlights",
        "fine_streaks": "{base} hair with fine {accent} streaks",
        "chunky_streaks": "{base} hair with bold {accent} streaks",
        "underlayer": "{base} hair with a {accent} underlayer",
        "inner_color": "{base} hair with hidden {accent} inner coloring",
        "dip_dye": "{base} hair with {accent} dip-dyed ends",
        "colored_tips": "{base} hair with {accent} tips",
        "colored_roots": "{base} hair with {accent} roots",
        "money_piece": "{base} hair with {accent} face-framing highlights",
        "balayage": "{base} hair with {accent} balayage",
        "rainbow_sections": "{base} hair with sectioned rainbow coloring",
        "iridescent_sheen": "{base} hair with an iridescent color sheen",
        "hair_pattern_halo_dye": (
            "{base} hair with a {accent} halo-dye band around the crown"
        ),
        "hair_pattern_marbled": (
            "{base} hair marbled with {accent} multitone veining"
        ),
        "horizontal_bands": "{base} hair with horizontal {accent} color bands",
        "checkerboard_panels": "{base} hair with checkerboard {accent} panel coloring",
        "constellation_speckles": (
            "{base} hair with constellation-like {accent} speckles"
        ),
        "flame_sections": "{base} hair with flame-shaped {accent} color sections",
    }
    if pattern is None or pattern not in templates:
        raise ValueError("未知髮色配置")
    return templates[pattern].format(base=base, accent=accent)


def _compose_eye_colors(options: Sequence[TagOption], pattern: str | None) -> str:
    if len(options) == 1 and pattern is None:
        return options[0].prompt_en
    colors = [_color_descriptor(option.prompt_en, "eyes") for option in options]
    if pattern in {None, "solid"}:
        if len(colors) == 1:
            return options[0].prompt_en
        return f"{_join_color_descriptors(colors)} multicolored eyes"
    if pattern == "complete_heterochromia":
        return f"{colors[0]} left eye and {colors[1]} right eye with complete heterochromia"
    if pattern == "central_heterochromia":
        return f"{colors[0]} irises with {colors[1]} central rings and central heterochromia"
    if pattern == "sectoral_heterochromia":
        return f"{colors[0]} irises with {colors[1]} sectors and sectoral heterochromia"
    if pattern == "gradient":
        return f"{'-to-'.join(colors)} gradient irises"

    base = colors[0] if len(colors) == 1 else _join_color_descriptors(colors)
    templates = {
        "limbal_ring": "{base} irises with defined dark limbal rings",
        "starburst": "{base} irises with a radial starburst pattern",
        "speckled": "finely speckled {base} irises",
        "concentric": "{base} irises with concentric rings",
        "glowing": "glowing {base} irises",
        "rainbow": "{base}-dominant rainbow irises",
        "starry": "star-filled {base} irises",
        "mechanical": "{base} mechanical irises",
        "faceted": "faceted {base} gemstone irises",
        "rune_ring": "{base} irises with rune-inscribed rings",
        "void_center": "{base} irises with void-dark pupil centers",
    }
    if pattern is None or pattern not in templates:
        raise ValueError("未知虹膜配色")
    return templates[pattern].format(base=base)


def _compose_color_options(
    category: TagCategory,
    options: Sequence[TagOption],
    pattern: str | None,
) -> str:
    """Keep one-color legacy output and render selected color placement naturally."""

    if category.key == "hair_color":
        return _compose_hair_colors(options, pattern)
    return _compose_eye_colors(options, pattern)


def _validate_selection_coherence(
    selections: Mapping[str, tuple[str, ...]],
) -> None:
    for rule in _MUTUAL_EXCLUSION_RULES:
        selected = set(selections.get(rule.category, ()))
        for first, second in rule.incompatible_pairs:
            if first in selected and second in selected:
                raise ValueError(f"分類 {rule.category} 含互斥選項：{first} 與 {second}")

    beast_species = selections.get("beast_humanoid_species", ())
    furry_species = selections.get("furry_species", ())
    if beast_species and furry_species:
        raise ValueError("人型獸徵與完整擬人福瑞不可同時選擇")
    fantasy_races = set(selections.get("fantasy_race", ()))
    if (beast_species or furry_species) and (
        fantasy_races & _ANIMAL_TAXONOMY_INCOMPATIBLE_FANTASY_RACES
    ):
        raise ValueError("所選奇幻血統體型與人型獸徵或雙足福瑞不相容")
    owned_traits = set(selections.get("fantasy_traits", ())) & (_ANIMAL_TAXONOMY_OWNED_TRAIT_KEYS)
    if (beast_species or furry_species) and owned_traits:
        raise ValueError("獸人或福瑞物種已管理耳尾與體表槽位，不能疊加衝突的奇幻身體特徵")
    if not beast_species and any(selections.get(key) for key in _BEAST_DETAIL_CATEGORIES):
        raise ValueError("選擇半獸人細節前，必須先選擇獸人／半獸人物種")
    if beast_species:
        species = beast_species[0]
        for detail_category, allowed_by_species in _BEAST_DETAIL_RULES.items():
            allowed = frozenset(allowed_by_species[species])
            if any(detail not in allowed for detail in selections.get(detail_category, ())):
                raise ValueError("所選半獸人細節與獸人／半獸人物種不相容")
    if not furry_species and any(selections.get(key) for key in _FURRY_DETAIL_CATEGORIES):
        raise ValueError("選擇福瑞細節前，必須先選擇福瑞物種")
    if furry_species:
        species = furry_species[0]
        for detail_category, allowed_by_species in _FURRY_DETAIL_RULES.items():
            allowed = frozenset(allowed_by_species[species])
            if any(detail not in allowed for detail in selections.get(detail_category, ())):
                raise ValueError("所選福瑞細節與福瑞物種不相容")

    for pattern_category, (
        color_category,
        needs_multiple,
        needs_single,
    ) in _COLOR_PATTERN_RULES.items():
        patterns = selections.get(pattern_category, ())
        if not patterns:
            continue
        color_count = len(selections.get(color_category, ()))
        if color_count == 0:
            raise ValueError("選擇髮色或虹膜配置前，必須先選擇至少一種顏色")
        if needs_multiple & set(patterns) and color_count < 2:
            raise ValueError("此多色配置至少需要選擇 2 種顏色")
        if needs_single & set(patterns) and color_count != 1:
            raise ValueError("單一純色配置只能搭配 1 種顏色")

    outfits = set(selections.get("outfit_archetype", ()))
    has_upper_visibility_option = any(
        option_key in selections.get(option_category, ())
        for option_category, option_key in _UPPER_VISIBILITY_OPTION_KEYS
    )
    has_upper = any(selections.get(key) for key in _UPPER_ADULT_ANATOMY_CATEGORIES)
    has_lower = any(selections.get(key) for key in _LOWER_ADULT_ANATOMY_CATEGORIES)
    has_breast_action = any(selections.get(key) for key in _BREAST_ADULT_ACTION_CATEGORIES)
    has_lower_explicit_action = any(
        selections.get(key) for key in _LOWER_ADULT_EXPLICIT_ACTION_CATEGORIES
    )
    if outfits and has_upper and not (outfits & _UPPER_ANATOMY_OUTFITS):
        raise ValueError("所選服裝無法呈現胸部成人細節，請改用可見的成人服裝")
    if outfits and has_upper_visibility_option and not (outfits & _UPPER_ANATOMY_OUTFITS):
        raise ValueError("所選服裝無法呈現乳頭飾品，請改用胸部可見的成人服裝")
    if has_lower and not (outfits & _LOWER_ANATOMY_OUTFITS):
        raise ValueError("所選服裝無法呈現外陰成人細節，請改用可見的成人服裝")
    if outfits and has_breast_action and not (outfits & _EXPOSED_BREAST_ACTION_OUTFITS):
        raise ValueError("所選服裝無法呈現胸部成人動作，請改用胸部裸露的成人服裝")
    if has_lower_explicit_action and not (outfits & _LOWER_EXPLICIT_ACTION_OUTFITS):
        raise ValueError("所選服裝無法呈現外陰或插入動作，請改用裸體或人體藝術彩繪")


def _normalize_selection(category: TagCategory, value: SelectionValue) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    selected: tuple[str, ...]
    if isinstance(value, str):
        selected = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        selected = tuple(value)
    else:
        raise ValueError(f"分類 {category.label_zh} 的選項格式無效")
    if any(not isinstance(key, str) for key in selected):
        raise ValueError(f"分類 {category.label_zh} 的選項必須是文字 key")
    selected = tuple(key for key in selected if key)
    if category.selection_mode == "single" and len(selected) > 1:
        raise ValueError(f"分類 {category.label_zh} 只能單選")
    known = {option.key for option in category.options}
    unknown = sorted(set(selected) - known)
    if unknown:
        raise ValueError(f"分類 {category.label_zh} 含未知選項：{', '.join(unknown)}")
    normalized = tuple(dict.fromkeys(selected))
    if category.selection_max is not None and len(normalized) > category.selection_max:
        raise ValueError(f"分類 {category.label_zh} 最多只能選擇 {category.selection_max} 個選項")
    return normalized


_CHARACTER_SHEET_OUTPUT_PURPOSES: Final[frozenset[str]] = frozenset(
    {
        "orthographic_turnaround",
        "illustration_plus_turnaround",
        "complete_character_sheet",
        "expression_sheet",
        "outfit_sheet",
        "action_sheet",
        "silhouette_sheet",
    }
)
_CHARACTER_SHEET_SUPPRESSED_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "pose",
        "framing",
        "viewpoint",
        "character_backdrop",
        "character_location",
        "adult_partner_intimacy",
        "adult_female_masturbation_pose",
        "adult_female_masturbation_action",
        "adult_female_state",
    }
)
_CHARACTER_STATE_EXPRESSION_OVERRIDES: Final[frozenset[str]] = frozenset(
    {"pouting", "contemptuous_face", "dazed", "sleepy", "asleep", "unconscious"}
)
CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS: Final[frozenset[str]] = (
    _CHARACTER_STATE_EXPRESSION_OVERRIDES
)
_CLOTHING_DEPENDENT_CHARACTER_STATES: Final[frozenset[str]] = frozenset(
    {
        "clothes_soaked",
        "rain_soaked",
        "muddy",
        "dusty",
        "windblown",
        "ash_streaked",
        "smoke_stained",
        "oil_stained",
        "blood_spattered",
        "clothes_torn",
    }
)
_CLOTHINGLESS_OUTFITS: Final[frozenset[str]] = frozenset({"nude", "body_paint"})
_INCAPACITATED_CHARACTER_STATES: Final[frozenset[str]] = frozenset({"asleep", "unconscious"})
INCAPACITATED_CHARACTER_STATE_KEYS: Final[frozenset[str]] = _INCAPACITATED_CHARACTER_STATES
_ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATES: Final[frozenset[str]] = frozenset(
    {*_INCAPACITATED_CHARACTER_STATES, "time_frozen"}
)
ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATE_KEYS: Final[frozenset[str]] = (
    _ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATES
)
_INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY: Final[
    dict[str, frozenset[str]]
] = {
    category_key: frozenset(
        option.key
        for category in CHARACTER_CATEGORIES
        if category.key == category_key
        for option in category.options
        if option.adult_only
    )
    for category_key in ("accessories", "pose", "framing")
}
if any(not keys for keys in _INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY.values()):
    raise ValueError("失去意識安全索引必須涵蓋成人配件、姿勢與構圖")
INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY: Final[
    Mapping[str, frozenset[str]]
] = _INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY
_INCAPACITATED_COMPATIBLE_POSES: Final[frozenset[str]] = frozenset(
    {
        "seated",
        "lying_down",
        "leaning_wall",
        "nude_recline",
        "sensual_recline",
        "seated_crossed_legs",
        "seated_sideways",
        "sitting_on_edge",
        "lying_on_side",
    }
)
_ADULT_AFTERCARE_CATEGORY_KEY: Final = "adult_aftercare_action"
_ACTIVE_ADULT_PHASE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "adult_partner_intimacy",
        "adult_female_breast_hand_action",
        "adult_female_breast_suckling_action",
        "adult_female_lactation_action",
        "adult_female_masturbation_pose",
        "adult_female_masturbation_action",
    }
)
_ACTIVE_ADULT_ACTIVITY_CATEGORIES: Final[frozenset[str]] = (
    _ACTIVE_ADULT_PHASE_CATEGORIES
    | {_ADULT_AFTERCARE_CATEGORY_KEY, "adult_female_expression"}
)


def _has_active_adult_activity(selections: Mapping[str, tuple[str, ...]]) -> bool:
    return (
        any(selections.get(key) for key in _ACTIVE_ADULT_ACTIVITY_CATEGORIES)
        or bool(set(selections.get("expression", ())) & _ADULT_BASE_EXPRESSION_KEYS)
        or bool(selections.get("adult_female_state"))
        or any(
            set(selections.get(category_key, ())) & conflict_keys
            for category_key, conflict_keys in (
                _INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY.items()
            )
        )
    )


def _has_active_adult_phase(selections: Mapping[str, tuple[str, ...]]) -> bool:
    """Return whether the image is still depicting an active adult phase."""

    return (
        any(selections.get(key) for key in _ACTIVE_ADULT_PHASE_CATEGORIES)
        or bool(
            set(selections.get("adult_female_expression", ()))
            & _ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS
        )
        or bool(
            set(selections.get("expression", ()))
            & _ADULT_BASE_ACTIVE_EXPRESSION_KEYS
        )
        or bool(
            set(selections.get("adult_female_state", ()))
            & _ADULT_FEMALE_ACTIVE_STATE_KEYS
        )
    )


def _adult_phase_candidate_is_compatible(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
) -> bool:
    """Keep aftercare separate from actions that depict an earlier active phase."""

    has_aftercare = bool(selections.get(_ADULT_AFTERCARE_CATEGORY_KEY))
    if category_key == _ADULT_AFTERCARE_CATEGORY_KEY:
        return not _has_active_adult_phase(selections)
    if not has_aftercare:
        return True
    if category_key in _ACTIVE_ADULT_PHASE_CATEGORIES:
        return False
    if (
        category_key == "adult_female_expression"
        and candidate in _ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS
    ):
        return False
    if category_key == "expression" and candidate in _ADULT_BASE_ACTIVE_EXPRESSION_KEYS:
        return False
    return not (
        category_key == "adult_female_state"
        and candidate in _ADULT_FEMALE_ACTIVE_STATE_KEYS
    )


def _is_character_sheet_selection(selections: Mapping[str, tuple[str, ...]]) -> bool:
    return bool(
        set(selections.get("character_output_purpose", ())) & _CHARACTER_SHEET_OUTPUT_PURPOSES
    )


def _effective_character_states(
    selections: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Resolve state precedence once so later Prompt layers use the same result."""

    selected = selections.get("character_state", ())
    if _has_active_adult_activity(selections):
        selected = tuple(
            key
            for key in selected
            if key not in _ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATES
        )
    if set(selections.get("outfit_archetype", ())) & _CLOTHINGLESS_OUTFITS:
        selected = tuple(
            key for key in selected if key not in _CLOTHING_DEPENDENT_CHARACTER_STATES
        )
    return selected


def _effective_selection(
    category_key: str,
    selections: Mapping[str, tuple[str, ...]],
    *,
    is_character_sheet: bool,
) -> tuple[str, ...]:
    """Apply deterministic Prompt precedence without mutating saved UI state."""

    selected = selections.get(category_key, ())
    effective_character_states = _effective_character_states(selections)
    if category_key == "character_state":
        selected = effective_character_states
    if is_character_sheet and category_key in _CHARACTER_SHEET_SUPPRESSED_CATEGORIES:
        return ()
    if category_key == "character_location" and "transparent" in selections.get(
        "character_backdrop", ()
    ):
        return ()
    has_aftercare = bool(selections.get(_ADULT_AFTERCARE_CATEGORY_KEY))
    effective_adult_expressions = selections.get("adult_female_expression", ())
    if has_aftercare:
        effective_adult_expressions = tuple(
            key
            for key in effective_adult_expressions
            if key not in _ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS
        )
    has_adult_expression = bool(effective_adult_expressions)
    state_overrides_expression = bool(
        set(effective_character_states) & _CHARACTER_STATE_EXPRESSION_OVERRIDES
    )
    if category_key == "expression":
        if has_aftercare:
            selected = tuple(
                key for key in selected if key not in _ADULT_BASE_ACTIVE_EXPRESSION_KEYS
            )
        if has_adult_expression:
            return ()
        if state_overrides_expression and not (
            set(selected) & _ADULT_BASE_EXPRESSION_KEYS
        ):
            return ()
    has_partner_intimacy = bool(selections.get("adult_partner_intimacy"))
    if category_key == "pose" and (
        has_partner_intimacy
        or has_aftercare
        or selections.get("adult_female_masturbation_pose")
    ):
        return ()
    if has_partner_intimacy and category_key in {
        "adult_female_masturbation_pose",
        "adult_female_masturbation_action",
    }:
        return ()
    if has_aftercare and category_key in _ACTIVE_ADULT_PHASE_CATEGORIES:
        return ()
    if has_aftercare and category_key == "adult_female_expression":
        selected = effective_adult_expressions
    if has_aftercare and category_key == "adult_female_state":
        selected = tuple(
            key for key in selected if key not in _ADULT_FEMALE_ACTIVE_STATE_KEYS
        )
    if category_key == "pose" and (
        set(effective_character_states) & _INCAPACITATED_CHARACTER_STATES
    ):
        return tuple(key for key in selected if key in _INCAPACITATED_COMPATIBLE_POSES)
    if category_key == "character_state" and has_adult_expression:
        selected = tuple(
            key for key in selected if key not in _CHARACTER_STATE_EXPRESSION_OVERRIDES
        )
    if category_key == "character_state" and (
        set(selections.get("expression", ())) & _ADULT_BASE_EXPRESSION_KEYS
    ):
        selected = tuple(
            key for key in selected if key not in _CHARACTER_STATE_EXPRESSION_OVERRIDES
        )
    return selected


def _resolve_prompt_tokens(
    categories: Sequence[TagCategory],
    selections: Mapping[str, SelectionValue],
    *,
    gender: CharacterGender | None = None,
    include_adult: bool = True,
) -> list[str]:
    known_categories = {category.key for category in categories}
    unknown_categories = sorted(set(selections) - known_categories)
    if unknown_categories:
        raise ValueError(f"含未知標籤分類：{', '.join(unknown_categories)}")

    active_categories: list[TagCategory] = []
    normalized_selections: dict[str, tuple[str, ...]] = {}
    for category in categories:
        if category.applicable_gender is not None and category.applicable_gender is not gender:
            continue
        selected = _normalize_selection(category, selections.get(category.key))
        lookup = {option.key: option for option in category.options}
        blocked = [key for key in selected if lookup[key].adult_only and not include_adult]
        if blocked:
            raise ValueError("成人限定標籤預設停用，請先明確開啟 18+ 成人模式")
        active_categories.append(category)
        normalized_selections[category.key] = selected

    is_character_sheet = _is_character_sheet_selection(normalized_selections)
    coherence_selections = {
        key: (() if is_character_sheet and key in _CHARACTER_SHEET_SUPPRESSED_CATEGORIES else value)
        for key, value in normalized_selections.items()
    }
    if coherence_selections.get(_ADULT_AFTERCARE_CATEGORY_KEY):
        coherence_selections = {
            key: (
                ()
                if key in _ACTIVE_ADULT_PHASE_CATEGORIES
                else tuple(
                    value_key
                    for value_key in value
                    if value_key not in _ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS
                )
                if key == "adult_female_expression"
                else tuple(
                    value_key
                    for value_key in value
                    if value_key not in _ADULT_BASE_ACTIVE_EXPRESSION_KEYS
                )
                if key == "expression"
                else tuple(
                    value_key
                    for value_key in value
                    if value_key not in _ADULT_FEMALE_ACTIVE_STATE_KEYS
                )
                if key == "adult_female_state"
                else value
            )
            for key, value in coherence_selections.items()
        }
    _validate_selection_coherence(coherence_selections)

    result: list[str] = []
    for category in active_categories:
        selected = _effective_selection(
            category.key,
            normalized_selections,
            is_character_sheet=is_character_sheet,
        )
        lookup = {option.key: option for option in category.options}
        selected_options = tuple(lookup[key] for key in selected)
        if category.key == "fantasy_race" and normalized_selections.get("furry_species"):
            selected_options = tuple(option for option in selected_options if option.key != "human")
        if category.key in _COLOR_CATEGORY_SUFFIXES and selected_options:
            pattern_category = (
                "hair_color_pattern" if category.key == "hair_color" else "iris_color_pattern"
            )
            patterns = normalized_selections.get(pattern_category, ())
            result.append(
                _compose_color_options(
                    category,
                    selected_options,
                    patterns[0] if patterns else None,
                )
            )
        elif category.key in _COLOR_PATTERN_RULES and normalized_selections.get(
            _COLOR_PATTERN_RULES[category.key][0]
        ):
            continue
        else:
            result.extend(option.prompt_en for option in selected_options)
    return result


def _has_selected_adult_option(
    categories: Sequence[TagCategory],
    selections: Mapping[str, SelectionValue],
    *,
    gender: CharacterGender,
) -> bool:
    """Return whether an applicable, explicitly selected option is adult-only."""

    active_categories: list[TagCategory] = []
    normalized_selections: dict[str, tuple[str, ...]] = {}
    for category in categories:
        if category.applicable_gender is not None and category.applicable_gender is not gender:
            continue
        active_categories.append(category)
        normalized_selections[category.key] = _normalize_selection(
            category, selections.get(category.key)
        )

    is_character_sheet = _is_character_sheet_selection(normalized_selections)
    for category in active_categories:
        selected = _effective_selection(
            category.key,
            normalized_selections,
            is_character_sheet=is_character_sheet,
        )
        lookup = {option.key: option for option in category.options}
        if any(lookup[key].adult_only for key in selected):
            return True
    return False


def _custom_tokens(value: str, *, character: bool) -> list[str]:
    tokens = _prompt_tokens(value)
    youth_coded = character and (
        _has_minor_age_marker(", ".join(tokens))
        or any(
            (_words(token) & _YOUTH_WORDS)
            or ("young" in _words(token) and "adult" not in _words(token))
            for token in tokens
        )
    )
    if youth_coded:
        raise ValueError("角色提示詞只能描述成年人，請移除兒童或青少年相關英文詞")
    if character:
        tokens = [token for token in tokens if not (_words(token) & _GENDER_WORDS)]
    return tokens


def _render(tokens: Sequence[str]) -> str:
    return normalize_english_image_prompt(", ".join(_dedupe(tokens)))


_CUSTOM_SPECIES_EN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z][A-Za-z0-9]*(?:[ '\-][A-Za-z0-9]+)*"
)


def validate_custom_species_english(value: str) -> str:
    """Normalize a UI-supplied English species fragment or fail closed."""

    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("自訂物種英文不可留白")
    if not normalized.isascii():
        raise ValueError("自訂物種英文只能使用 ASCII；中文請改用翻譯 hook")
    if len(normalized) > 80 or _CUSTOM_SPECIES_EN_PATTERN.fullmatch(normalized) is None:
        raise ValueError("自訂物種英文只能包含英文字母、數字、空格、連字號或撇號")
    if _has_minor_age_marker(normalized) or _words(normalized) & _YOUTH_WORDS:
        raise ValueError("自訂物種不得含兒童或青少年語意")
    return normalized


def _resolve_custom_species_english(
    selections: Mapping[str, SelectionValue],
    *,
    custom_species_en: str,
    custom_species_zh: str,
    species_translation_hook: SpeciesTranslationHook | None,
) -> tuple[CharacterIdentityMode | None, str | None]:
    categories = {category.key: category for category in CHARACTER_CATEGORIES}
    beast = _normalize_selection(
        categories["beast_humanoid_species"],
        selections.get("beast_humanoid_species"),
    )
    furry = _normalize_selection(
        categories["furry_species"],
        selections.get("furry_species"),
    )
    mode: CharacterIdentityMode | None = None
    if "other" in beast:
        mode = "beast_humanoid"
    if "other" in furry:
        if mode is not None:
            raise ValueError("人型獸徵與完整擬人福瑞不可同時選擇")
        mode = "furry"

    direct_english = custom_species_en.strip()
    source_chinese = custom_species_zh.strip()
    if direct_english and source_chinese:
        raise ValueError("自訂物種英文與中文來源只能擇一提供")
    if mode is None:
        if direct_english or source_chinese:
            raise ValueError("提供自訂物種前，必須先將物種選為「其他／自訂」")
        return None, None
    if not direct_english and not source_chinese:
        raise ValueError("物種選為「其他／自訂」時必須提供自訂物種英文")

    candidate = direct_english
    if source_chinese:
        if species_translation_hook is None:
            raise ValueError("自訂中文物種需要翻譯 hook 才能轉成英文 Prompt")
        try:
            translated = species_translation_hook(source_chinese)
        except Exception as exc:
            raise ValueError("自訂中文物種翻譯失敗") from exc
        if not isinstance(translated, str):
            raise ValueError("自訂物種翻譯 hook 必須回傳英文文字")
        candidate = translated
    return mode, validate_custom_species_english(candidate)


def _validate_identity_mode_selections(
    selections: Mapping[str, SelectionValue],
    identity_mode: CharacterIdentityMode | str | None,
) -> None:
    if identity_mode is None:
        return
    if identity_mode not in CHARACTER_IDENTITY_MODES:
        raise ValueError("角色身分模式只能是 standard、beast_humanoid 或 furry")
    mode = cast(CharacterIdentityMode, identity_mode)
    allowed = CHARACTER_IDENTITY_CATEGORY_KEYS[mode]
    selected_hidden = {
        key
        for key in {
            "fantasy_race",
            "beast_humanoid_species",
            "furry_species",
            *_ANIMAL_DETAIL_CATEGORIES,
        }
        if selections.get(key) and key not in allowed
    }
    if selected_hidden:
        raise ValueError("目前角色身分模式含有其他模式的物種或細節")


def build_character_prompt(
    gender: CharacterGender | str,
    selections: Mapping[str, SelectionValue],
    custom_keywords: str = "",
    *,
    include_adult: bool = False,
    identity_mode: CharacterIdentityMode | str | None = None,
    custom_species_en: str = "",
    custom_species_zh: str = "",
    species_translation_hook: SpeciesTranslationHook | None = None,
) -> str:
    """Build a canonical adult character prompt from Chinese-facing tag keys."""

    try:
        selected_gender = CharacterGender(gender)
    except ValueError as exc:
        raise ValueError("角色性別只能是 male 或 female") from exc
    _validate_identity_mode_selections(selections, identity_mode)
    catalog_tokens = _resolve_prompt_tokens(
        CHARACTER_CATEGORIES,
        selections,
        gender=selected_gender,
        include_adult=include_adult,
    )
    custom_mode, custom_species = _resolve_custom_species_english(
        selections,
        custom_species_en=custom_species_en,
        custom_species_zh=custom_species_zh,
        species_translation_hook=species_translation_hook,
    )
    if custom_mode is not None and custom_species is not None:
        species_category = (
            "beast_humanoid_species" if custom_mode == "beast_humanoid" else "furry_species"
        )
        placeholder = next(
            option.prompt_en
            for option in next(
                category for category in CHARACTER_CATEGORIES if category.key == species_category
            ).options
            if option.key == "other"
        )
        replacement = (
            _custom_beast_lineage_prompt(custom_species)
            if custom_mode == "beast_humanoid"
            else _furry_lineage_prompt(custom_species)
        )
        catalog_tokens = [
            replacement if token == placeholder else token for token in catalog_tokens
        ]
    uses_adult_boundary = (
        include_adult
        and _has_selected_adult_option(
            CHARACTER_CATEGORIES,
            selections,
            gender=selected_gender,
        )
    )
    adult_identity_tokens = {
        CharacterGender.FEMALE: "consensual adult woman",
        CharacterGender.MALE: "consensual adult man",
    }
    identity_token = adult_identity_tokens[selected_gender] if uses_adult_boundary else (
        selected_gender.prompt_token
    )
    return _render(
        (
            identity_token,
            *catalog_tokens,
            *_custom_tokens(custom_keywords, character=True),
        )
    )


def build_background_prompt(
    selections: Mapping[str, SelectionValue],
    custom_keywords: str = "",
) -> str:
    """Build a canonical English environment prompt from background tags."""

    return _render(
        (
            *_resolve_prompt_tokens(BACKGROUND_CATEGORIES, selections),
            *_custom_tokens(custom_keywords, character=False),
        )
    )


_COMBINED_ISOLATED_CHARACTER_BACKDROPS: Final[frozenset[str]] = frozenset(
    {"transparent", "simple", "gradient"}
)


def _selected_catalog_keys(
    categories: Sequence[TagCategory],
    category_key: str,
    selections: Mapping[str, SelectionValue],
) -> tuple[str, ...]:
    category = next(category for category in categories if category.key == category_key)
    return _normalize_selection(category, selections.get(category_key))


def _combined_output_purpose_token(purpose_key: str) -> str:
    category = next(
        category for category in CHARACTER_CATEGORIES if category.key == "character_output_purpose"
    )
    original = next(option.prompt_en for option in category.options if option.key == purpose_key)
    if purpose_key == "character_illustration":
        return "single full-body character illustration integrated naturally into the environment"
    if purpose_key in _CHARACTER_SHEET_OUTPUT_PURPOSES:
        return f"{original} accompanied by a separate environment reference panel"
    return original


def build_combined_scene_prompt(
    gender: CharacterGender | str,
    character_selections: Mapping[str, SelectionValue],
    background_selections: Mapping[str, SelectionValue],
    character_custom_keywords: str = "",
    background_custom_keywords: str = "",
    *,
    include_adult: bool = False,
    identity_mode: CharacterIdentityMode | str | None = None,
    custom_species_en: str = "",
    custom_species_zh: str = "",
    species_translation_hook: SpeciesTranslationHook | None = None,
) -> str:
    """Build one coordinated character-in-environment positive prompt.

    Character framing, viewpoint, art style, and an explicitly selected nearby
    location own the final scene.  Background time and lighting own the shared
    illumination.  The remaining background tags contribute environment,
    weather, atmosphere, and scene detail without reintroducing an isolated
    backdrop, empty population, or competing wide camera/style directive.
    """

    coordinated_character = dict(character_selections)
    coordinated_background = dict(background_selections)

    purpose_keys = _selected_catalog_keys(
        CHARACTER_CATEGORIES,
        "character_output_purpose",
        character_selections,
    )
    backdrop_keys = _selected_catalog_keys(
        CHARACTER_CATEGORIES,
        "character_backdrop",
        character_selections,
    )
    if set(backdrop_keys) & _COMBINED_ISOLATED_CHARACTER_BACKDROPS:
        coordinated_character.pop("character_backdrop", None)

    has_character_location = bool(
        _selected_catalog_keys(
            CHARACTER_CATEGORIES,
            "character_location",
            character_selections,
        )
    )
    if has_character_location:
        _selected_catalog_keys(
            BACKGROUND_CATEGORIES,
            "location",
            background_selections,
        )
        coordinated_background.pop("location", None)

    has_background_time_or_lighting = any(
        _selected_catalog_keys(BACKGROUND_CATEGORIES, key, background_selections)
        for key in ("time_of_day", "background_lighting")
    )
    if has_background_time_or_lighting:
        _selected_catalog_keys(
            CHARACTER_CATEGORIES,
            "character_lighting",
            character_selections,
        )
        coordinated_character.pop("character_lighting", None)

    population_keys = _selected_catalog_keys(
        BACKGROUND_CATEGORIES,
        "population",
        background_selections,
    )
    if "empty" in population_keys:
        coordinated_background.pop("population", None)

    has_character_camera_priority = any(
        _selected_catalog_keys(CHARACTER_CATEGORIES, key, character_selections)
        for key in ("framing", "viewpoint")
    )
    camera_keys = _selected_catalog_keys(
        BACKGROUND_CATEGORIES,
        "camera",
        background_selections,
    )
    if has_character_camera_priority or "wide_establishing" in camera_keys:
        coordinated_background.pop("camera", None)

    has_character_style_priority = bool(
        _selected_catalog_keys(
            CHARACTER_CATEGORIES,
            "character_style",
            character_selections,
        )
    )
    if has_character_style_priority:
        _selected_catalog_keys(
            BACKGROUND_CATEGORIES,
            "background_style",
            background_selections,
        )
        _selected_catalog_keys(
            BACKGROUND_CATEGORIES,
            "rendering",
            background_selections,
        )
        coordinated_background.pop("background_style", None)
        coordinated_background.pop("rendering", None)

    character_prompt = build_character_prompt(
        gender,
        coordinated_character,
        character_custom_keywords,
        include_adult=include_adult,
        identity_mode=identity_mode,
        custom_species_en=custom_species_en,
        custom_species_zh=custom_species_zh,
        species_translation_hook=species_translation_hook,
    )
    character_tokens = _prompt_tokens(character_prompt)
    if purpose_keys:
        purpose_category = next(
            category
            for category in CHARACTER_CATEGORIES
            if category.key == "character_output_purpose"
        )
        purpose_lookup = {option.key: option.prompt_en for option in purpose_category.options}
        purpose_key = purpose_keys[0]
        original_purpose = purpose_lookup[purpose_key]
        replacement_purpose = _combined_output_purpose_token(purpose_key)
        character_tokens = [
            replacement_purpose if token == original_purpose else token
            for token in character_tokens
        ]

    background_prompt = build_background_prompt(
        coordinated_background,
        background_custom_keywords,
    )
    return _render((*character_tokens, *_prompt_tokens(background_prompt)))


def build_negative_prompt(
    selections: Mapping[str, SelectionValue],
    custom_keywords: str = "",
) -> str:
    """Build a separate negative prompt; never mix it into positive prompts."""

    return _render(
        (
            *_resolve_prompt_tokens(NEGATIVE_CATEGORIES, selections),
            *_custom_tokens(custom_keywords, character=False),
        )
    )


def _ordered_random_categories(categories: Sequence[TagCategory]) -> tuple[TagCategory, ...]:
    """Topologically place compatibility anchors before their dependents."""

    category_by_key = {category.key: category for category in categories}
    if len(category_by_key) != len(categories):
        raise ValueError("隨機標籤分類不可含重複 key")
    original_index = {category.key: index for index, category in enumerate(categories)}
    outgoing: dict[str, set[str]] = {key: set() for key in category_by_key}
    indegree: dict[str, int] = dict.fromkeys(category_by_key, 0)
    for rule in _COMPATIBILITY_RULES:
        anchor = rule.anchor_category
        dependent = rule.dependent_category
        if (
            anchor in category_by_key
            and dependent in category_by_key
            and dependent not in outgoing[anchor]
        ):
            outgoing[anchor].add(dependent)
            indegree[dependent] += 1

    available = sorted(
        (key for key, count in indegree.items() if count == 0),
        key=original_index.__getitem__,
    )
    ordered_keys: list[str] = []
    while available:
        key = available.pop(0)
        ordered_keys.append(key)
        for dependent in sorted(outgoing[key], key=original_index.__getitem__):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                available.append(dependent)
                available.sort(key=original_index.__getitem__)
    if len(ordered_keys) != len(category_by_key):
        # No cycle is expected in the curated rules.  Keeping catalog order is
        # a deterministic fail-soft path if a future rule is authored badly.
        return tuple(categories)
    return tuple(category_by_key[key] for key in ordered_keys)


def _mutually_excluded(category: str, left: str, right: str) -> bool:
    for rule in _MUTUAL_EXCLUSION_RULES:
        if rule.category != category:
            continue
        if any(
            (left == first and right == second) or (left == second and right == first)
            for first, second in rule.incompatible_pairs
        ):
            return True
    return False


def resolve_mutually_exclusive_selection(
    category_key: str,
    selected_keys: Sequence[str],
    *,
    newly_selected_key: str,
) -> tuple[str, ...]:
    """Resolve one UI selection event with the newest clicked value winning.

    The helper accepts catalog keys rather than labels so a UI can use the
    same contract for character, background, and negative categories.  It
    folds older values in their current order, moves the newly clicked value
    to the end, and removes every older value that conflicts with a later one.
    """

    if isinstance(selected_keys, (str, bytes, bytearray)):
        raise ValueError("互斥選項必須使用選項 key 序列")
    matching_categories = tuple(
        category
        for category in (*CHARACTER_CATEGORIES, *BACKGROUND_CATEGORIES, *NEGATIVE_CATEGORIES)
        if category.key == category_key
    )
    if not matching_categories:
        raise ValueError(f"含未知標籤分類：{category_key}")
    if len(matching_categories) != 1:
        raise ValueError(f"標籤分類 key 不唯一：{category_key}")

    known_keys = {option.key for option in matching_categories[0].options}
    raw_selected = tuple(selected_keys)
    if any(not isinstance(key, str) for key in raw_selected):
        raise ValueError("互斥選項必須是文字 key")
    selected = tuple(key for key in raw_selected if key)
    unknown_keys = sorted(set(selected) - known_keys)
    if unknown_keys:
        raise ValueError(f"分類 {category_key} 含未知選項：{', '.join(unknown_keys)}")
    if newly_selected_key not in selected:
        raise ValueError("新點選的選項必須存在於 selected_keys")

    ordered = [key for key in dict.fromkeys(selected) if key != newly_selected_key]
    ordered.append(newly_selected_key)
    resolved: list[str] = []
    for candidate in ordered:
        resolved = [
            existing
            for existing in resolved
            if not _mutually_excluded(category_key, existing, candidate)
        ]
        resolved.append(candidate)
    return tuple(resolved)


_COLOR_PATTERN_RULES: Final[dict[str, tuple[str, frozenset[str], frozenset[str]]]] = {
    "hair_color_pattern": (
        "hair_color",
        frozenset(
            {
                "two_tone",
                "split_dye",
                "gradient",
                "ombre",
                "color_blocks",
                "hair_pattern_halo_dye",
                "hair_pattern_marbled",
                "horizontal_bands",
                "checkerboard_panels",
                "constellation_speckles",
                "flame_sections",
            }
        ),
        frozenset({"solid"}),
    ),
    "iris_color_pattern": (
        "eye_color",
        frozenset(
            {
                "complete_heterochromia",
                "sectoral_heterochromia",
                "central_heterochromia",
                "gradient",
            }
        ),
        frozenset({"solid"}),
    ),
}

_UPPER_ADULT_ANATOMY_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "areola_size",
        "areola_shape",
        "areola_tone",
        "nipple_size",
        "nipple_shape",
        "nipple_state",
    }
)
_LOWER_ADULT_ANATOMY_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"vulva_shape", "labia_shape", "pubic_hair_style", "adult_body_adornment"}
)
_LOWER_ADULT_POSE_CATEGORIES: Final[frozenset[str]] = frozenset({"adult_female_masturbation_pose"})
_LOWER_ADULT_EXPLICIT_ACTION_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"adult_partner_intimacy", "adult_female_masturbation_action"}
)
_LOWER_ADULT_ACTION_CATEGORIES: Final[frozenset[str]] = (
    _LOWER_ADULT_POSE_CATEGORIES | _LOWER_ADULT_EXPLICIT_ACTION_CATEGORIES
)
_UPPER_ANATOMY_OUTFITS: Final[frozenset[str]] = frozenset(
    {"nude", "topless", "body_paint", "sheer_lingerie"}
)
_UPPER_VISIBILITY_OPTION_KEYS: Final[frozenset[tuple[str, str]]] = frozenset(
    {("accessories", "adult_accessory_nipple_clamps")}
)
_LOWER_ANATOMY_OUTFITS: Final[frozenset[str]] = frozenset({"nude", "body_paint", "sheer_lingerie"})
_LOWER_EXPLICIT_ACTION_OUTFITS: Final[frozenset[str]] = frozenset({"nude", "body_paint"})
_OPTIONAL_ADULT_ANATOMY_CATEGORIES: Final[frozenset[str]] = (
    _UPPER_ADULT_ANATOMY_CATEGORIES | _LOWER_ADULT_ANATOMY_CATEGORIES
)
_BREAST_ADULT_ACTION_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "adult_female_breast_hand_action",
        "adult_female_breast_suckling_action",
        "adult_female_lactation_action",
    }
)
_EXPOSED_BREAST_ACTION_OUTFITS: Final[frozenset[str]] = frozenset({"nude", "topless", "body_paint"})
_OPTIONAL_ADULT_ACTION_CATEGORIES: Final[frozenset[str]] = _BREAST_ADULT_ACTION_CATEGORIES | {
    "adult_female_expression",
    "adult_aftercare_action",
    *_LOWER_ADULT_ACTION_CATEGORIES,
}
_OPTIONAL_ADULT_STATE_CATEGORIES: Final[frozenset[str]] = frozenset({"adult_female_state"})
_OPTIONAL_ADULT_CATEGORIES: Final[frozenset[str]] = (
    _OPTIONAL_ADULT_ANATOMY_CATEGORIES
    | _OPTIONAL_ADULT_ACTION_CATEGORIES
    | _OPTIONAL_ADULT_STATE_CATEGORIES
)


def _color_candidate_is_compatible(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
) -> bool:
    for pattern_category, (
        color_category,
        needs_multiple,
        needs_single,
    ) in _COLOR_PATTERN_RULES.items():
        if category_key == pattern_category:
            color_count = len(selections.get(color_category, ()))
            if candidate in needs_multiple and color_count < 2:
                return False
            if candidate in needs_single and color_count > 1:
                return False
        elif category_key == color_category:
            chosen = selections.get(color_category, ())
            count_after = len(chosen) + int(candidate not in chosen)
            patterns = selections.get(pattern_category, ())
            if needs_single & set(patterns) and count_after > 1:
                return False
    return True


def _adult_anatomy_candidate_is_compatible(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
) -> bool:
    selected_outfits = set(selections.get("outfit_archetype", ()))
    if (category_key, candidate) in _UPPER_VISIBILITY_OPTION_KEYS and selected_outfits:
        return bool(selected_outfits & _UPPER_ANATOMY_OUTFITS)
    if category_key in _UPPER_ADULT_ANATOMY_CATEGORIES and selected_outfits:
        return bool(selected_outfits & _UPPER_ANATOMY_OUTFITS)
    if category_key in _LOWER_ADULT_ANATOMY_CATEGORIES:
        return bool(selected_outfits & _LOWER_ANATOMY_OUTFITS)
    if category_key in _BREAST_ADULT_ACTION_CATEGORIES and selected_outfits:
        return bool(selected_outfits & _EXPOSED_BREAST_ACTION_OUTFITS)
    if category_key in _LOWER_ADULT_POSE_CATEGORIES:
        return True
    if category_key in _LOWER_ADULT_EXPLICIT_ACTION_CATEGORIES:
        return bool(selected_outfits & _LOWER_EXPLICIT_ACTION_OUTFITS)
    if category_key != "outfit_archetype":
        return True
    has_upper_visibility_option = any(
        option_key in selections.get(option_category, ())
        for option_category, option_key in _UPPER_VISIBILITY_OPTION_KEYS
    )
    if has_upper_visibility_option and candidate not in _UPPER_ANATOMY_OUTFITS:
        return False
    has_upper = any(selections.get(key) for key in _UPPER_ADULT_ANATOMY_CATEGORIES)
    has_lower = any(selections.get(key) for key in _LOWER_ADULT_ANATOMY_CATEGORIES)
    has_breast_action = any(selections.get(key) for key in _BREAST_ADULT_ACTION_CATEGORIES)
    has_lower_explicit_action = any(
        selections.get(key) for key in _LOWER_ADULT_EXPLICIT_ACTION_CATEGORIES
    )
    if has_upper and candidate not in _UPPER_ANATOMY_OUTFITS:
        return False
    if has_breast_action and candidate not in _EXPOSED_BREAST_ACTION_OUTFITS:
        return False
    if has_lower_explicit_action and candidate not in _LOWER_EXPLICIT_ACTION_OUTFITS:
        return False
    return not has_lower or candidate in _LOWER_ANATOMY_OUTFITS


def _animal_taxonomy_candidate_is_compatible(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
) -> bool:
    """Keep generated animal taxonomies coherent without rewriting manual choices."""

    if category_key in _OPTIONAL_TAXONOMY_SPECIES_CATEGORIES and candidate == "other":
        return False
    owned_traits = set(selections.get("fantasy_traits", ())) & (_ANIMAL_TAXONOMY_OWNED_TRAIT_KEYS)
    if category_key == "beast_humanoid_species":
        return (
            not owned_traits
            and not selections.get("furry_species")
            and not (
                set(selections.get("fantasy_race", ()))
                & _ANIMAL_TAXONOMY_INCOMPATIBLE_FANTASY_RACES
            )
        )
    if category_key == "furry_species":
        return (
            not owned_traits
            and not selections.get("beast_humanoid_species")
            and not (
                set(selections.get("fantasy_race", ()))
                & _ANIMAL_TAXONOMY_INCOMPATIBLE_FANTASY_RACES
            )
        )
    if category_key == "fantasy_race" and (
        selections.get("beast_humanoid_species") or selections.get("furry_species")
    ):
        return candidate not in _ANIMAL_TAXONOMY_INCOMPATIBLE_FANTASY_RACES
    if category_key == "fantasy_traits" and candidate in _ANIMAL_TAXONOMY_OWNED_TRAIT_KEYS:
        return not (selections.get("beast_humanoid_species") or selections.get("furry_species"))
    if category_key in _BEAST_DETAIL_CATEGORIES:
        return bool(selections.get("beast_humanoid_species"))
    if category_key in _FURRY_DETAIL_CATEGORIES:
        return bool(selections.get("furry_species"))
    return True


def _state_candidate_is_compatible(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
) -> bool:
    selected_states = set(selections.get("character_state", ()))
    selected_outfits = set(selections.get("outfit_archetype", ()))
    selected_poses = set(selections.get("pose", ()))
    if category_key == "expression" and selections.get("adult_female_expression"):
        return False
    if category_key == "adult_female_expression" and selections.get("expression"):
        return False
    if category_key == "character_state":
        if (
            candidate in _CLOTHING_DEPENDENT_CHARACTER_STATES
            and selected_outfits & _CLOTHINGLESS_OUTFITS
        ):
            return False
        if candidate in _INCAPACITATED_CHARACTER_STATES and any(
            pose not in _INCAPACITATED_COMPATIBLE_POSES for pose in selected_poses
        ):
            return False
        if candidate in _CHARACTER_STATE_EXPRESSION_OVERRIDES and (
            selections.get("expression") or selections.get("adult_female_expression")
        ):
            return False
    elif category_key == "outfit_archetype":
        if candidate in _CLOTHINGLESS_OUTFITS and (
            selected_states & _CLOTHING_DEPENDENT_CHARACTER_STATES
        ):
            return False
    elif category_key == "pose" and (selected_states & _INCAPACITATED_CHARACTER_STATES):
        return (
            candidate
            not in _INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY["pose"]
            and candidate in _INCAPACITATED_COMPATIBLE_POSES
        )
    elif category_key == "expression" and candidate in _ADULT_BASE_EXPRESSION_KEYS:
        return not selected_states & (
            _CHARACTER_STATE_EXPRESSION_OVERRIDES
            | _ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATES
        )
    elif category_key in {"expression", "adult_female_expression"} and (
        selected_states & _CHARACTER_STATE_EXPRESSION_OVERRIDES
    ):
        return False
    elif (
        category_key in _ACTIVE_ADULT_ACTIVITY_CATEGORIES
        or category_key == "adult_female_state"
        or candidate
        in _INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY.get(category_key, frozenset())
    ):
        return not selected_states & _ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATES
    if (
        category_key == "character_state"
        and candidate in _ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATES
    ):
        return not _has_active_adult_activity(selections)
    return True


def _combined_allowed_for(pair: tuple[str, str], anchor_value: str) -> frozenset[str] | None:
    parts = [
        allowed
        for rule in _COMPATIBILITY_RULES
        if (rule.anchor_category, rule.dependent_category) == pair
        and (allowed := rule.allowed_for(anchor_value)) is not None
    ]
    return frozenset().union(*parts) if parts else None


def _candidate_is_compatible(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
) -> bool:
    if not _animal_taxonomy_candidate_is_compatible(category_key, candidate, selections):
        return False
    if not _color_candidate_is_compatible(category_key, candidate, selections):
        return False
    if not _adult_anatomy_candidate_is_compatible(category_key, candidate, selections):
        return False
    if not _adult_phase_candidate_is_compatible(category_key, candidate, selections):
        return False
    if not _state_candidate_is_compatible(category_key, candidate, selections):
        return False
    if any(
        _mutually_excluded(category_key, candidate, selected)
        for selected in selections.get(category_key, ())
    ):
        return False
    checked_pairs: set[tuple[str, str]] = set()
    for rule in _COMPATIBILITY_RULES:
        pair = (rule.anchor_category, rule.dependent_category)
        if pair in checked_pairs:
            continue
        checked_pairs.add(pair)

        if category_key == pair[1]:
            for anchor in selections.get(pair[0], ()):
                allowed = _combined_allowed_for(pair, anchor)
                if allowed is not None and candidate not in allowed:
                    return False
        elif category_key == pair[0]:
            allowed = _combined_allowed_for(pair, candidate)
            if allowed is not None and any(
                dependent not in allowed for dependent in selections.get(pair[1], ())
            ):
                return False
    return True


def _locked_random_selections(
    categories: Sequence[TagCategory],
    current: Mapping[str, SelectionValue],
    *,
    fill_blanks_only: bool,
) -> dict[str, tuple[str, ...]]:
    if not fill_blanks_only:
        return {}
    locked: dict[str, tuple[str, ...]] = {}
    for category in categories:
        selected = _normalize_selection(category, current.get(category.key))
        if selected:
            locked[category.key] = selected
    return locked


def _required_random_category_keys(
    categories: Sequence[TagCategory],
    locked: Mapping[str, tuple[str, ...]],
    required_groups: Sequence[str],
    *,
    picker: random.Random,
) -> frozenset[str]:
    """Choose one randomizable category for every still-empty required group."""

    locked_groups = {
        category.group
        for category in categories
        if locked.get(category.key)
    }
    required_keys: set[str] = set()
    for group in required_groups:
        if group in locked_groups:
            continue
        candidates = tuple(
            category.key
            for category in categories
            if category.group == group
            and category.random_max > 0
            and category.key not in locked
        )
        if candidates:
            required_keys.add(picker.choice(candidates))
    return frozenset(required_keys)


def _candidate_preserves_required_adult_coverage(
    category_key: str,
    candidate: str,
    selections: Mapping[str, tuple[str, ...]],
    required_category_keys: frozenset[str],
    category_by_key: Mapping[str, TagCategory],
) -> bool:
    """Keep an outfit compatible with adult categories required by this attempt."""

    if category_key != "outfit_archetype":
        return True
    adult_required = required_category_keys & (
        _OPTIONAL_ADULT_ANATOMY_CATEGORIES | _OPTIONAL_ADULT_ACTION_CATEGORIES
    )
    if not adult_required:
        return True
    trial_state = dict(selections)
    trial_state[category_key] = (candidate,)
    return all(
        any(
            _candidate_is_compatible(required_key, option.key, trial_state)
            for option in category_by_key[required_key].options
        )
        for required_key in adult_required
    )


def _covered_random_groups(
    categories: Sequence[TagCategory],
    result: Mapping[str, SelectionValue],
    required_groups: Sequence[str],
) -> frozenset[str]:
    required = frozenset(required_groups)
    return frozenset(
        category.group
        for category in categories
        if category.group in required and bool(result.get(category.key))
    )


def _randomize_attempt(
    categories: Sequence[TagCategory],
    locked: Mapping[str, tuple[str, ...]],
    *,
    fill_blanks_only: bool,
    required_groups: Sequence[str],
    picker: random.Random,
) -> tuple[SelectionResult, bool]:
    selection_state = {key: tuple(value) for key, value in locked.items()}
    result: SelectionResult = {}
    category_by_key = {category.key: category for category in categories}
    required_category_keys = _required_random_category_keys(
        categories,
        locked,
        required_groups,
        picker=picker,
    )
    expression_slots = tuple(
        key
        for key in ("expression", "adult_female_expression")
        if key in category_by_key
    )
    has_state_override_slot = "character_state" in category_by_key
    locked_authorities = {
        key for key in expression_slots if locked.get(key)
    }
    if set(locked.get("character_state", ())) & _CHARACTER_STATE_EXPRESSION_OVERRIDES:
        locked_authorities.add("character_state")
    expression_authority: str | None = None
    if len(locked_authorities) == 1:
        expression_authority = next(iter(locked_authorities))
    elif not locked_authorities:
        authority_candidates = [*expression_slots]
        if (
            expression_slots
            and has_state_override_slot
            and "character_state" not in locked
        ):
            authority_candidates.append("character_state")
        required_authorities = [
            key for key in authority_candidates if key in required_category_keys
        ]
        if len(authority_candidates) >= 2:
            expression_authority = picker.choice(
                required_authorities or authority_candidates
            )
    for key, locked_selected in locked.items():
        category = category_by_key[key]
        result[key] = locked_selected[0] if category.selection_mode == "single" else locked_selected

    complete = True
    for category in _ordered_random_categories(categories):
        if category.key in locked:
            continue
        if (
            expression_authority is not None
            and category.key in expression_slots
            and category.key != expression_authority
        ):
            selection_state[category.key] = ()
            result[category.key] = ()
            continue
        candidates = [
            option.key
            for option in category.options
            if _candidate_is_compatible(category.key, option.key, selection_state)
            and _candidate_preserves_required_adult_coverage(
                category.key,
                option.key,
                selection_state,
                required_category_keys,
                category_by_key,
            )
        ]
        if category.key == "character_state":
            if expression_authority == "character_state":
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate in _CHARACTER_STATE_EXPRESSION_OVERRIDES
                ]
            elif expression_authority in expression_slots:
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate not in _CHARACTER_STATE_EXPRESSION_OVERRIDES
                ]
        if category.selection_mode == "single":
            if not candidates:
                complete = False
                continue
            single_choice = picker.choice(tuple(candidates))
            selection_state[category.key] = (single_choice,)
            result[category.key] = single_choice
            continue

        if fill_blanks_only and category.key not in (
            _OPTIONAL_ADULT_CATEGORIES | _OPTIONAL_TAXONOMY_SPECIES_CATEGORIES
        ):
            missing_animal_anchor = (
                category.key in _BEAST_DETAIL_CATEGORIES
                and not selection_state.get("beast_humanoid_species")
            ) or (
                category.key in _FURRY_DETAIL_CATEGORIES
                and not selection_state.get("furry_species")
            )
            minimum = 0 if missing_animal_anchor else max(1, category.random_min)
        else:
            minimum = category.random_min
        if category.key in required_category_keys:
            minimum = max(1, minimum)
        if category.key == expression_authority and category.key in expression_slots:
            minimum = max(1, minimum)
        if category.key == "character_state" and expression_authority == "character_state":
            minimum = max(1, minimum)
        if category.key == "beast_humanoid_species" and any(
            selection_state.get(key) for key in _BEAST_DETAIL_CATEGORIES
        ):
            minimum = max(1, minimum)
        if category.key == "furry_species" and any(
            selection_state.get(key) for key in _FURRY_DETAIL_CATEGORIES
        ):
            minimum = max(1, minimum)
        for pattern_category, (
            color_category,
            needs_multiple,
            _needs_single,
        ) in _COLOR_PATTERN_RULES.items():
            if category.key == color_category and needs_multiple & set(
                selection_state.get(pattern_category, ())
            ):
                minimum = max(minimum, 2)
        target = picker.randint(minimum, category.random_max)
        if category.key == "character_state" and expression_authority == "character_state":
            target = 1
        chosen: list[str] = []
        while len(chosen) < target:
            trial_state = dict(selection_state)
            trial_state[category.key] = tuple(chosen)
            eligible = [
                candidate
                for candidate in candidates
                if candidate not in chosen
                and _candidate_is_compatible(category.key, candidate, trial_state)
            ]
            if not eligible:
                break
            chosen.append(picker.choice(eligible))
        if len(chosen) < minimum:
            complete = False
            continue
        selected_multi = tuple(chosen)
        selection_state[category.key] = selected_multi
        result[category.key] = selected_multi
    if _covered_random_groups(categories, result, required_groups) != frozenset(
        required_groups
    ):
        complete = False
    return result, complete


def _ordered_random_result(
    categories: Sequence[TagCategory], result: SelectionResult
) -> SelectionResult:
    return {category.key: result[category.key] for category in categories if category.key in result}


def _validate_coherence_rules() -> None:
    all_categories = (*CHARACTER_CATEGORIES, *BACKGROUND_CATEGORIES, *NEGATIVE_CATEGORIES)
    options_by_category = {
        category.key: {option.key for option in category.options} for category in all_categories
    }
    for compatibility_rule in _COMPATIBILITY_RULES:
        if (
            compatibility_rule.anchor_category not in options_by_category
            or compatibility_rule.dependent_category not in options_by_category
        ):
            raise ValueError("相容性規則引用不存在的標籤分類")
        for anchor, dependents in compatibility_rule.allowed_dependent_by_anchor:
            if anchor not in options_by_category[compatibility_rule.anchor_category]:
                raise ValueError(f"相容性規則引用不存在的 anchor：{anchor}")
            unknown = dependents - options_by_category[compatibility_rule.dependent_category]
            if unknown:
                raise ValueError(f"相容性規則引用不存在的 dependent：{sorted(unknown)}")
    for mutual_rule in _MUTUAL_EXCLUSION_RULES:
        if mutual_rule.category not in options_by_category:
            raise ValueError("互斥規則引用不存在的標籤分類")
        known = options_by_category[mutual_rule.category]
        if any(
            first not in known or second not in known
            for first, second in mutual_rule.incompatible_pairs
        ):
            raise ValueError(f"互斥規則引用不存在的選項：{mutual_rule.category}")


_validate_coherence_rules()


def randomize_selections(
    categories: Sequence[TagCategory],
    current: Mapping[str, SelectionValue] | None = None,
    fill_blanks_only: bool = True,
    seed: int | str | bytes | bytearray | None = None,
    required_groups: Sequence[str] = (),
) -> SelectionResult:
    """Return coherent random keys while preserving deliberate manual choices.

    With ``fill_blanks_only=True``, every non-empty current value is locked and
    retained in the same order, even when the author intentionally selected a
    conflicting combination.  Only newly generated values are constrained to
    fit those anchors.  A full reroll applies coherence rules to every value.
    ``required_groups`` opts selected callers into at least one non-empty
    randomizable category per named group whenever the locked anchors permit it.
    """

    current = current or {}
    if isinstance(required_groups, (str, bytes, bytearray)):
        raise ValueError("必要隨機群組必須使用群組名稱序列")
    required_groups = tuple(dict.fromkeys(required_groups))
    known_categories = {category.key for category in categories}
    unknown_categories = sorted(set(current) - known_categories)
    if unknown_categories:
        raise ValueError(f"含未知標籤分類：{', '.join(unknown_categories)}")

    locked = _locked_random_selections(
        categories,
        current,
        fill_blanks_only=fill_blanks_only,
    )
    known_groups = {category.group for category in categories}
    unknown_groups = sorted(set(required_groups) - known_groups)
    if unknown_groups:
        raise ValueError(f"含未知必要隨機群組：{', '.join(unknown_groups)}")
    unavailable_groups = sorted(
        group
        for group in required_groups
        if not any(
            category.group == group
            and (category.random_max > 0 or bool(locked.get(category.key)))
            for category in categories
        )
    )
    if unavailable_groups:
        raise ValueError(f"必要隨機群組沒有可用選項：{', '.join(unavailable_groups)}")
    picker = random.Random(seed)
    best: SelectionResult = {}
    best_score = -1
    for _attempt in range(128):
        result, complete = _randomize_attempt(
            categories,
            locked,
            fill_blanks_only=fill_blanks_only,
            required_groups=required_groups,
            picker=picker,
        )
        covered_groups = _covered_random_groups(categories, result, required_groups)
        coverage_weight = (len(categories) * 2) + 1
        score = (
            len(covered_groups) * coverage_weight
            + len(result)
            + sum(bool(value) for value in result.values())
        )
        if score > best_score:
            best = result
            best_score = score
        if complete:
            return _ordered_random_result(categories, result)
    return _ordered_random_result(categories, best)


__all__ = [
    "ADULT_BASE_ACTIVE_EXPRESSION_KEYS",
    "ADULT_BASE_EXPRESSION_KEYS",
    "ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATE_KEYS",
    "ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS",
    "ADULT_FEMALE_ACTIVE_STATE_KEYS",
    "ADULT_FEMALE_EXPRESSION_KEYS",
    "BACKGROUND_CATEGORIES",
    "CHARACTER_CATEGORIES",
    "CHARACTER_IDENTITY_CATEGORY_KEYS",
    "CHARACTER_IDENTITY_MODES",
    "CHARACTER_IDENTITY_MODE_LABELS_ZH",
    "CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS",
    "INCAPACITATED_CHARACTER_STATE_KEYS",
    "INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY",
    "NEGATIVE_CATEGORIES",
    "NON_ANIMAL_ORC_LINEAGE_KEYS",
    "CharacterIdentityMode",
    "SelectionMode",
    "SelectionResult",
    "SelectionValue",
    "SpeciesTranslationHook",
    "TagCategory",
    "TagOption",
    "build_background_prompt",
    "build_character_prompt",
    "build_combined_scene_prompt",
    "build_negative_prompt",
    "filter_adult_options",
    "filter_character_categories_by_identity",
    "randomize_selections",
    "resolve_mutually_exclusive_selection",
    "validate_custom_species_english",
]
