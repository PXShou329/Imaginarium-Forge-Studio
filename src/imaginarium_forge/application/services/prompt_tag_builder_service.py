"""Traditional Chinese tags for general character and environment prompts.

The curated catalog supports character design, wardrobe composition, fantasy
species, scenery, and illustration styles without a content-mode switch.
This module has no Streamlit or persistence dependency.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

from imaginarium_forge.application.services.clothing_tag_catalog import (
    ACCESSORIES_BAGS_SAFE,
    ACCESSORIES_FACE_NECK_SAFE,
    ACCESSORIES_HAND_ARM_SAFE,
    ACCESSORIES_HEAD_HAIR_SAFE,
    ACCESSORIES_WAIST_BODY_SAFE,
    FOOTWEAR_SAFE,
    HOSIERY_LENGTH_SAFE,
    HOSIERY_STYLE_SAFE,
    OUTFIT_LOWER_SAFE,
    OUTFIT_LOWER_STATE_SAFE,
    OUTFIT_ONE_PIECE_SAFE,
    OUTFIT_OUTERWEAR_SAFE,
    OUTFIT_SLEEPWEAR_SAFE,
    OUTFIT_SWIMWEAR_SAFE,
    OUTFIT_UNIFORM_SPORT_SAFE,
    OUTFIT_UPPER_SAFE,
    OUTFIT_UPPER_STATE_SAFE,
)
from imaginarium_forge.domain.character.biography_draft import normalize_english_image_prompt
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
) -> TagOption:
    return TagOption(
        key=key,
        label_zh=label_zh,
        prompt_en=prompt_en,
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


def _furry_lineage_prompt(species: str) -> str:
    return (
        f"adult anthropomorphic sapient bipedal {species} furry with humanoid proportions "
        "and a fully anthropomorphic head plus species-appropriate full-body covering"
    )


CHARACTER_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    TagCategory(
        key="age_impression",
        label_zh="成年年齡感",
        group="身分與輪廓",
        options=(
            _option("young_adult", "年輕成年人", "young adult appearance"),
            _option("adult", "成熟成年人", "mature adult appearance"),
            _option("middle_aged", "中年", "middle-aged adult appearance"),
            _option("older_adult", "年長成年人", "older adult appearance"),
            _option("prime_adult", "盛年成年人", "adult in their prime"),
            _option("seasoned_adult", "歷練成年人", "seasoned adult appearance"),
            _option("elderly", "高齡成年人", "elderly adult appearance"),
            _option("fresh_adult", "初入成年期", "fresh adult appearance"),
            _option("established_adult", "穩重成年人", "established adult appearance"),
            _option("late_middle_age", "壯年後期", "late-middle-aged adult appearance"),
            _option("venerable", "德高望重", "venerable adult appearance"),
            _option("early_thirties", "三十歲初段成年人", "adult appearance in the early thirties"),
            _option("late_thirties", "三十歲後段成年人", "adult appearance in the late thirties"),
            _option("early_forties", "四十歲初段成年人", "adult appearance in the early forties"),
            _option("late_fifties", "五十歲後段成年人", "adult appearance in the late fifties"),
            _option("age_early_fifties", "五十歲初段成年人", "adult appearance in the early fifties"),
        ),
        selection_mode="single",
        help_text="只提供成年外觀，不含兒童或青少年選項。",
        default_keys=("young_adult",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="fantasy_race",
        label_zh="種族／血統",
        group="身分與輪廓",
        options=(
            _option("human", "人類", "human"),
            _option("elf", "精靈", "elf"),
            _option("dark_elf", "黑暗精靈", "dark elf"),
            _option("half_elf", "半精靈", "half-elf"),
            _option("dwarf", "矮人", "dwarf"),
            _option("orc", "歐克族", "orc"),
            _option("goblin", "哥布林", "goblin"),
            _option("tiefling", "魔裔", "tiefling"),
            _option("demon", "惡魔族", "demon"),
            _option("angel", "天使族", "angelic being"),
            _option("vampire", "吸血鬼", "vampire"),
            _option("werewolf", "狼人", "werewolf"),
            _option("merfolk", "人魚族", "merfolk"),
            _option("fairy", "妖精", "fairy folk"),
            _option("dragonkin", "龍裔", "dragonkin"),
            _option("automaton", "魔導人偶", "arcane automaton"),
            _option("android", "仿生人", "android"),
            _option("undead", "不死族", "sentient undead"),
            _option("elemental", "元素精靈", "elemental humanoid"),
            _option("aasimar", "天界裔", "aasimar"),
            _option("oni", "鬼族", "oni"),
            _option("dryad", "樹精", "dryad"),
            _option("djinn", "燈神族", "djinn"),
            _option("shadowfolk", "影族", "shadow folk"),
            _option("slime_humanoid", "史萊姆人形", "humanoid slime being"),
            _option("living_doll", "活人偶", "living doll person"),
            _option("golem", "魔像族", "sentient golem"),
            _option("cyborg", "改造人", "cyborg"),
            _option("clone", "複製人", "engineered clone adult"),
            _option("witchblood", "巫血族", "witch-blooded human"),
            _option("fae_noble", "妖精貴族", "noble fae"),
            _option("moonfolk", "月民", "moon folk"),
            _option("sunfolk", "日民", "sun folk"),
            _option("voidborn", "虛空裔", "voidborn humanoid"),
            _option("giantkin", "巨人裔", "giantkin"),
            _option("halfling", "半身人", "adult halfling"),
            _option("centaur", "半人馬族", "centaur humanoid"),
            _option("minotaur", "牛頭人族", "minotaur humanoid"),
            _option("satyr", "薩堤爾族", "satyr humanoid"),
            _option("harpy", "鷹身人族", "harpy humanoid"),
            _option("naga", "娜迦族", "naga humanoid"),
            _option("kitsune", "妖狐族", "kitsune humanoid"),
            _option("tengu", "天狗族", "tengu humanoid"),
            _option("phoenixkin", "鳳凰裔", "phoenixkin humanoid"),
            _option("plantfolk", "植物族", "sapient plantfolk humanoid"),
            _option("mushroomfolk", "蕈族", "sapient mushroomfolk humanoid"),
            _option("crystalborn", "水晶裔", "crystalborn humanoid"),
            _option("celestial", "星界族", "celestial humanoid"),
            _option("eldritch", "異界裔", "eldritch humanoid"),
            _option("spiderfolk", "蛛族", "sapient spiderfolk humanoid"),
            _option("deepfolk", "深海眷族", "sapient deep-sea humanoid"),
            _option("half_orc", "半歐克族", "half-orc"),
            _option("stoneborn", "岩石裔", "stoneborn humanoid"),
            _option("starforged", "星鑄裔", "star-forged humanoid"),
            _option("dreamborn", "夢境裔", "dreamborn humanoid"),
            _option("homunculus", "人工生命族", "sapient homunculus adult"),
            _option("stormborn", "風暴裔", "stormborn humanoid"),
            _option("sandfolk", "沙海民", "sand folk humanoid"),
            _option("inkborn", "墨靈裔", "inkborn humanoid"),
            _option("mirrorfolk", "鏡界民", "mirror-realm humanoid"),
            _option("timewalker", "時行者血統", "time-walker humanoid lineage"),
            _option("race_changeling", "變形族", "changeling humanoid"),
            _option("race_sylph", "希爾芙族", "sylph humanoid"),
            _option("race_frostborn", "霜裔", "frostborn humanoid"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("human",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="height",
        label_zh="身高",
        group="身分與輪廓",
        options=(
            _option("very_short", "非常嬌小", "very short stature"),
            _option("short", "嬌小", "short stature"),
            _option("average", "平均身高", "average height"),
            _option("tall", "高挑", "tall stature"),
            _option("very_tall", "非常高挑", "very tall stature"),
            _option("diminutive", "極度矮小", "diminutive adult stature"),
            _option("above_average", "略高", "above-average height"),
            _option("towering", "巨人般高大", "towering stature"),
            _option("compact_tall", "修長但緊湊", "compact tall stature"),
            _option("statuesque", "雕像般高挑", "statuesque adult height"),
            _option("colossal", "超常高大", "colossal fantasy stature"),
            _option("height_below_average", "略矮", "below-average adult height"),
            _option("palm_sized_fantasy", "掌上微型成人身高", "palm-sized adult fantasy stature"),
            _option("knee_high_fantasy", "膝高型成人身高", "knee-high adult fantasy stature"),
            _option(
                "double_human_fantasy",
                "雙倍人高巨人體型",
                "adult fantasy stature roughly twice human height",
            ),
            _option("building_scale_giant", "建築級巨人體型", "building-scale adult giant stature"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("average",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="build",
        label_zh="整體身材",
        group="身分與輪廓",
        options=(
            _option("petite", "纖小", "petite adult build"),
            _option("slender", "纖細", "slender build"),
            _option("lean", "精瘦", "lean build"),
            _option("toned", "勻稱結實", "toned build"),
            _option("athletic", "運動型", "athletic build"),
            _option("curvy", "曲線型", "curvy adult figure"),
            _option("stocky", "厚實型", "stocky build"),
            _option("muscular", "肌肉型", "muscular build"),
            _option("heavyset", "大尺碼", "heavyset adult build"),
            _option("willowy", "柳條纖長", "willowy build"),
            _option("lithe", "輕盈柔韌", "lithe build"),
            _option("soft", "柔和體型", "soft adult build"),
            _option("broad", "寬厚", "broad powerful build"),
            _option("bodybuilder", "健美型", "bodybuilder physique"),
            _option("plus_size", "豐腴大尺碼", "plus-size adult build"),
            _option("rangy", "瘦長", "rangy adult build"),
            _option("swimmer", "游泳選手型", "swimmer's athletic build"),
            _option("powerlifter", "力量型", "powerlifter physique"),
            _option("dancer", "舞者型", "dancer's flexible build"),
            _option("robust", "健壯", "robust adult build"),
            _option("rounded", "圓潤", "rounded adult build"),
            _option("build_trim", "精實修整", "trim athletic adult build"),
            _option("build_burly", "魁梧厚重", "burly adult build"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("slender",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="body_proportions",
        label_zh="身體比例",
        group="身分與輪廓",
        options=(
            _option("balanced", "均衡比例", "balanced body proportions"),
            _option("long_legged", "修長腿型", "long-legged proportions"),
            _option("compact", "緊湊比例", "compact body proportions"),
            _option("hourglass", "沙漏型", "hourglass proportions"),
            _option("pear", "梨形", "pear-shaped proportions"),
            _option("inverted_triangle", "倒三角型", "inverted-triangle proportions"),
            _option("rectangular", "直筒型", "rectangular proportions"),
            _option("short_torso", "短軀幹", "short-torso proportions"),
            _option("long_torso", "長軀幹", "long-torso proportions"),
            _option("top_heavy", "上身量感突出", "top-heavy proportions"),
            _option("bottom_heavy", "下身量感突出", "bottom-heavy proportions"),
            _option("heroic", "英雄式比例", "heroic body proportions"),
            _option("long_arms", "手臂修長", "long-armed proportions"),
            _option("broad_torso", "寬闊軀幹", "broad-torso proportions"),
            _option("narrow_frame", "窄骨架", "narrow-frame proportions"),
            _option("statuesque", "雕像式比例", "statuesque body proportions"),
            _option("stylized_heroic", "誇張英雄式", "stylized heroic proportions"),
            _option("proportions_broad_pelvis", "寬骨盆比例", "broad-pelvis body proportions"),
            _option("proportions_narrow_hips", "窄胯比例", "narrow-hipped body proportions"),
            _option("proportions_compact_limbs", "短肢比例", "compact-limbed adult body proportions"),
            _option("short_arms", "短臂比例", "short-armed body proportions"),
            _option("long_neck", "修長頸部比例", "long-necked body proportions"),
            _option("large_head_ratio", "大頭身比例", "large head-to-body proportion"),
            _option("small_head_ratio", "小頭身比例", "small head-to-body proportion"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("balanced",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="male_chest",
        label_zh="胸膛／胸肌",
        group="身材細節",
        options=(
            _option("flat", "平坦胸膛", "flat chest"),
            _option("lean", "精瘦胸膛", "lean chest"),
            _option("defined", "結實胸肌", "defined pectorals"),
            _option("broad", "寬闊胸膛", "broad chest"),
            _option("muscular", "厚實胸肌", "broad muscular chest"),
            _option("soft", "柔和胸膛", "soft adult male chest"),
            _option("hairy", "有胸毛", "hairy adult chest"),
            _option("barrel", "桶狀胸膛", "barrel chest"),
            _option("sculpted", "雕塑感胸肌", "sculpted pectorals"),
            _option("slender", "纖薄胸膛", "slender adult male chest"),
            _option("square", "方正胸膛", "square adult male chest"),
            _option("massive", "巨量胸肌", "massive adult pectorals"),
            _option("scarred", "帶疤胸膛", "scarred adult male chest"),
            _option("male_chest_narrow", "窄胸廓", "narrow adult male chest"),
            _option("male_chest_deep", "深厚胸廓", "deep adult male chest"),
            _option("male_chest_lightly_defined", "微線條胸肌", "lightly defined pectorals"),
        ),
        selection_mode="single",
        help_text="只在性別選擇「男」時出現。",
        default_keys=("defined",),
        random_min=1,
        random_max=1,
        applicable_gender=CharacterGender.MALE,
        selection_max=None,
    ),
    TagCategory(
        key="waist",
        label_zh="腰圍線條",
        group="身材細節",
        options=(
            _option("straight", "直腰", "straight waistline"),
            _option("soft", "柔和腰線", "soft waistline"),
            _option("defined", "明顯腰線", "defined waistline"),
            _option("narrow", "纖腰", "narrow waist"),
            _option("very_narrow", "極纖腰", "very narrow waist"),
            _option("thick", "厚實腰身", "thick waist"),
            _option("high_waist", "高腰線", "high waistline"),
            _option("low_waist", "低腰線", "low waistline"),
            _option("athletic", "運動型腰腹", "athletic waist and core"),
            _option("tapered", "收束腰線", "tapered waistline"),
            _option("boxy", "方正腰身", "boxy waistline"),
            _option("soft_round", "柔和圓腰", "soft rounded waistline"),
            _option("muscular_core", "清晰核心肌群", "defined muscular core"),
            _option("waist_broad_soft", "寬闊柔和腰身", "broad softly contoured waist"),
            _option("waist_defined_obliques", "明顯腹斜肌", "clearly defined oblique muscles"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("defined",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="hips",
        label_zh="臀胯比例",
        group="身材細節",
        options=(
            _option("narrow", "窄臀", "narrow hips"),
            _option("balanced", "均衡臀胯", "balanced hips"),
            _option("wide", "寬臀", "wide hips"),
            _option("full", "豐滿臀型", "full hips"),
            _option("angular", "稜角臀胯", "angular hips"),
            _option("rounded", "圓潤臀胯", "rounded hips"),
            _option("high_set", "高臀線", "high-set hips"),
            _option("strong", "強健臀胯", "strong athletic hips"),
            _option("tapered", "收束臀胯", "tapered hips"),
            _option("heart_shaped", "心形臀胯", "heart-shaped adult hips"),
            _option("low_set", "低臀線", "low-set hips"),
            _option("sculpted", "雕塑感臀胯", "sculpted athletic hips"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("balanced",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="shoulders",
        label_zh="肩膀",
        group="身材細節",
        options=(
            _option("narrow", "窄肩", "narrow shoulders"),
            _option("sloping", "柔和斜肩", "gently sloping shoulders"),
            _option("balanced", "均衡肩寬", "balanced shoulders"),
            _option("broad", "寬肩", "broad shoulders"),
            _option("square", "平直方肩", "square shoulders"),
            _option("rounded", "圓肩", "rounded shoulders"),
            _option("athletic", "運動型肩線", "athletic shoulders"),
            _option("delicate", "纖柔肩線", "delicate shoulders"),
            _option("tapered", "收束肩線", "tapered shoulders"),
            _option("high_set", "高肩線", "high-set shoulders"),
            _option("powerful", "強壯肩線", "powerful shoulders"),
            _option("asymmetrical", "自然不對稱肩線", "naturally asymmetrical shoulders"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("balanced",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="legs",
        label_zh="腿部線條",
        group="身材細節",
        options=(
            _option("slender", "纖細雙腿", "slender legs"),
            _option("long", "修長雙腿", "long legs"),
            _option("toned", "勻稱腿型", "toned legs"),
            _option("thick", "厚實腿型", "thick legs"),
            _option("powerful", "強健腿型", "powerful legs"),
            _option("compact", "短而勻稱", "compact proportional legs"),
            _option("curvy", "曲線腿型", "curvy adult legs"),
            _option("muscular", "肌肉腿型", "muscular legs"),
            _option("runner", "跑者腿型", "runner's legs"),
            _option("dancer", "舞者腿型", "dancer's legs"),
            _option("long_slender", "修長纖腿", "long slender legs"),
            _option("soft_full", "柔和豐潤腿型", "soft full adult legs"),
            _option("sprinter", "短跑選手腿型", "sprinter's powerful legs"),
            _option("knock_kneed", "內收膝線", "subtly inward-kneed stance"),
            _option("digitigrade", "趾行腿型", "digitigrade fantasy legs"),
            _option("legs_tapered", "漸細腿型", "smoothly tapered legs"),
            _option("bowed_leg_line", "自然外彎腿線", "naturally bowed leg silhouette"),
            _option("defined_calves", "清晰小腿線條", "clearly defined calf muscles"),
            _option("tapered_ankles", "收細腳踝線條", "slender tapered ankle lines"),
            _option("long_femur_proportions", "長股骨比例", "long femur-dominant leg proportions"),
            _option("full_calves", "飽滿小腿", "full rounded calf contours"),
            _option("columnar", "筆直柱狀腿", "straight columnar leg silhouette"),
            _option("high_set_knees", "高膝位比例", "high-set knee proportions"),
            _option("low_set_knees", "低膝位比例", "low-set knee proportions"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("toned",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="skin_tone",
        label_zh="膚色",
        group="身材細節",
        options=(
            _option("porcelain", "瓷白", "porcelain skin"),
            _option("fair", "白皙", "fair skin"),
            _option("light", "淺膚色", "light skin"),
            _option("warm_beige", "暖米色", "warm beige skin"),
            _option("tan", "小麥色", "tan skin"),
            _option("olive", "橄欖色", "olive skin"),
            _option("brown", "棕色", "brown skin"),
            _option("deep_brown", "深棕色", "deep brown skin"),
            _option("ebony", "烏木色", "ebony skin"),
            _option("pale_blue", "淡藍奇幻膚色", "pale blue skin"),
            _option("lavender", "薰衣草紫膚色", "lavender skin"),
            _option("emerald", "翡翠綠膚色", "emerald green skin"),
            _option("cool_fair", "冷調白皙", "cool fair skin"),
            _option("peach", "蜜桃膚色", "peach-toned skin"),
            _option("golden", "金棕膚色", "golden brown skin"),
            _option("copper", "赤銅膚色", "copper skin"),
            _option("gray", "灰色奇幻膚色", "fantasy gray skin"),
            _option("obsidian", "黑曜石膚色", "obsidian-black skin"),
            _option("rose", "玫瑰粉膚色", "rose-pink skin"),
            _option("translucent", "半透明膚質", "translucent ethereal skin"),
            _option("ivory", "象牙色", "ivory skin"),
            _option("neutral_beige", "中性米色", "neutral beige skin"),
            _option("caramel", "焦糖色", "caramel-brown skin"),
            _option("mahogany", "桃花心木色", "mahogany-brown skin"),
            _option("ruby", "紅寶石奇幻膚色", "ruby-red fantasy skin"),
            _option("gold_metallic", "金屬金膚色", "metallic gold skin"),
            _option("pearl", "珍珠光膚色", "pearl-lustrous skin"),
            _option("galaxy", "星河膚色", "galaxy-patterned skin"),
            _option("warm_sienna", "暖赭棕膚色", "warm sienna-brown skin"),
            _option("cool_taupe", "冷灰褐膚色", "cool taupe-brown skin"),
            _option("deep_plum", "深梅紫奇幻膚色", "deep plum-purple fantasy skin"),
            _option("celadon", "青瓷綠奇幻膚色", "pale celadon-green fantasy skin"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("fair",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="face_shape",
        label_zh="臉型",
        group="臉部與眼睛",
        options=(
            _option("oval", "鵝蛋臉", "oval face"),
            _option("round", "圓臉", "round face"),
            _option("heart", "心形臉", "heart-shaped face"),
            _option("square", "方臉", "square face"),
            _option("angular", "稜角臉", "angular face"),
            _option("diamond", "菱形臉", "diamond-shaped face"),
            _option("long", "長臉", "long face"),
            _option("triangle", "三角臉", "triangular face"),
            _option("inverted_triangle", "倒三角臉", "inverted-triangle face"),
            _option("soft_square", "柔和方臉", "soft square face"),
            _option("chiseled", "雕刻感臉型", "chiseled face"),
            _option("full_cheeks", "豐潤臉頰", "full-cheeked face"),
            _option("gaunt", "消瘦臉型", "gaunt face"),
            _option("high_cheekbones", "高顴骨", "high-cheekboned face"),
            _option("delicate", "精緻小臉", "delicate small face"),
            _option("oblong", "長橢圓臉", "oblong face"),
            _option("trapezoid", "梯形臉", "trapezoidal face"),
            _option("soft_rounded_adult", "柔和圓潤成年臉", "soft rounded adult facial features"),
            _option("sculptural", "雕塑感臉型", "sculptural facial structure"),
            _option("broad_jaw", "寬下顎臉型", "broad-jawed face"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("oval",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="eye_shape",
        label_zh="眼型",
        group="臉部與眼睛",
        options=(
            _option("round", "圓眼", "round eyes"),
            _option("almond", "杏眼", "almond-shaped eyes"),
            _option("upturned", "上挑眼", "upturned eyes"),
            _option("downturned", "下垂眼", "downturned eyes"),
            _option("hooded", "內雙眼", "hooded eyes"),
            _option("narrow", "細長眼", "narrow eyes"),
            _option("deep_set", "深邃眼", "deep-set eyes"),
            _option("wide_set", "寬眼距", "wide-set eyes"),
            _option("close_set", "窄眼距", "close-set eyes"),
            _option("monolid", "單眼皮", "monolid eyes"),
            _option("double_lid", "明顯雙眼皮", "defined double-lid eyes"),
            _option("catlike", "貓眼", "catlike eyes"),
            _option("doe", "小鹿眼", "doe eyes"),
            _option("sanpaku", "三白眼", "sanpaku eyes"),
            _option("protruding", "微凸眼", "slightly prominent eyes"),
            _option("recessed", "深陷眼", "recessed eyes"),
            _option("siren", "修長上挑眼", "elongated upturned eyes"),
            _option("puppy", "無辜下垂眼", "soft puppy-like eyes"),
            _option("crescent", "月牙笑眼", "crescent-shaped smiling eyes"),
            _option("asymmetrical", "自然不對稱眼型", "naturally asymmetrical eye shapes"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("almond",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="eye_color",
        label_zh="眼睛顏色",
        group="臉部與眼睛",
        options=(
            _option("brown", "棕色", "brown eyes"),
            _option("amber", "琥珀色", "amber eyes"),
            _option("hazel", "榛果色", "hazel eyes"),
            _option("green", "綠色", "green eyes"),
            _option("blue", "藍色", "blue eyes"),
            _option("gray", "灰色", "gray eyes"),
            _option("violet", "紫羅蘭色", "violet eyes"),
            _option("red", "緋紅色", "crimson eyes"),
            _option("gold", "金色", "golden eyes"),
            _option("silver", "銀色", "silver eyes"),
            _option("black", "黑色", "black eyes"),
            _option("cyan", "青藍色", "cyan eyes"),
            _option("turquoise", "綠松石色", "turquoise eyes"),
            _option("pink", "粉紅色", "pink eyes"),
            _option("orange", "橙色", "orange eyes"),
            _option("white", "純白色", "pure white eyes"),
            _option("cobalt", "鈷藍色", "cobalt blue eyes"),
            _option("indigo", "靛藍色", "indigo eyes"),
            _option("emerald", "翡翠色", "emerald green eyes"),
            _option("lime", "萊姆綠", "lime green eyes"),
            _option("copper", "赤銅色", "copper eyes"),
            _option("bronze", "青銅色", "bronze eyes"),
            _option("rose_gold", "玫瑰金", "rose-gold eyes"),
            _option("lavender", "薰衣草紫", "lavender eyes"),
            _option("magenta", "洋紅色", "magenta eyes"),
            _option("ice_blue", "冰藍色", "ice-blue eyes"),
            _option("eye_teal", "青綠色", "teal eyes"),
            _option("eye_honey_gold", "蜂蜜金色", "honey-gold eyes"),
            _option("aquamarine", "海水藍綠", "aquamarine eyes"),
            _option("smoky_quartz", "煙晶棕灰", "smoky-quartz eyes"),
            _option("moonstone_blue", "月光石白藍", "moonstone-blue eyes"),
            _option("garnet_red", "深石榴紅", "garnet-red eyes"),
        ),
        selection_mode="multi",
        help_text="可選一至兩種虹膜顏色；多色會自動合成自然英文片語。",
        default_keys=("brown",),
        random_min=1,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="hair_length",
        label_zh="頭髮長度",
        group="髮型與髮色",
        options=(
            _option("shaved", "剃髮", "shaved hair"),
            _option("pixie", "精靈短髮", "pixie-cut hair"),
            _option("chin", "下巴長度", "chin-length hair"),
            _option("shoulder", "及肩", "shoulder-length hair"),
            _option("mid_back", "背中長度", "mid-back-length hair"),
            _option("waist", "及腰", "waist-length hair"),
            _option("floor", "曳地超長髮", "floor-length hair"),
            _option("ear", "及耳", "ear-length hair"),
            _option("neck", "及頸", "neck-length hair"),
            _option("chest", "及胸", "chest-length hair"),
            _option("hip", "及臀", "hip-length hair"),
            _option("ankle", "及踝", "ankle-length hair"),
            _option("cheek", "及頰", "cheek-length hair"),
            _option("lower_back", "及下背", "lower-back-length hair"),
            _option("calf", "及小腿", "calf-length hair"),
            _option("hair_length_collarbone", "鎖骨長度", "collarbone-length hair"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("shoulder",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="hair_style",
        label_zh="髮型",
        group="髮型與髮色",
        options=(
            _option("loose_straight", "自然直髮", "loose straight hair"),
            _option("loose_wavy", "自然波浪髮", "loose wavy hair"),
            _option("curly", "捲髮", "curly hair"),
            _option("bob", "鮑伯頭", "bob haircut"),
            _option("ponytail", "馬尾", "ponytail"),
            _option("high_ponytail", "高馬尾", "high ponytail"),
            _option("twin_tails", "雙馬尾", "twin tails"),
            _option("side_ponytail", "側馬尾", "side ponytail"),
            _option("single_braid", "單辮", "single braid"),
            _option("double_braids", "雙辮", "double braids"),
            _option("crown_braid", "冠狀編髮", "crown braid"),
            _option("bun", "髮髻", "hair bun"),
            _option("double_buns", "雙丸子頭", "double hair buns"),
            _option("buzz_cut", "平頭／極短髮", "buzz cut"),
            _option("undercut", "側削髮", "undercut hairstyle"),
            _option("mohawk", "莫霍克頭", "mohawk hairstyle"),
            _option("messy", "凌亂髮", "messy hair"),
            _option("wolf_cut", "狼尾剪", "wolf cut hairstyle"),
            _option("shag", "層次碎剪", "shag haircut"),
            _option("lob", "長鮑伯", "long bob haircut"),
            _option("french_braid", "法式辮", "French braid"),
            _option("fishtail_braid", "魚骨辮", "fishtail braid"),
            _option("waterfall_braid", "瀑布辮", "waterfall braid"),
            _option("rope_braid", "繩結辮", "rope braid"),
            _option("braided_ponytail", "編髮馬尾", "braided ponytail"),
            _option("low_ponytail", "低馬尾", "low ponytail"),
            _option("half_up", "公主頭", "half-up hairstyle"),
            _option("topknot", "高髮髻", "topknot"),
            _option("side_bun", "側髮髻", "side hair bun"),
            _option("dreadlocks", "長髒辮", "dreadlocks"),
            _option("afro", "爆炸捲髮", "afro hairstyle"),
            _option("slicked_back", "油頭後梳", "slicked-back hair"),
            _option("pageboy", "童花頭", "adult pageboy haircut"),
            _option("bixie", "短鮑伯精靈剪", "bixie haircut"),
            _option("mullet", "鯔魚頭", "modern mullet hairstyle"),
            _option("cornrows", "貼頭辮", "neat cornrow braids"),
            _option("box_braids", "箱型辮", "box-braid hairstyle"),
            _option("halo_braid", "光環辮", "halo braid hairstyle"),
            _option("bubble_ponytail", "泡泡馬尾", "bubble ponytail"),
            _option("space_buns", "太空包頭", "space-bun hairstyle"),
            _option("victory_rolls", "勝利卷", "vintage victory rolls"),
            _option("pompadour", "蓬巴杜髮型", "pompadour hairstyle"),
            _option("quiff", "飛機頭", "textured quiff hairstyle"),
            _option("locs_updo", "髒辮盤髮", "locs updo hairstyle"),
            _option("textured_pixie", "層次精靈短剪", "textured pixie-cut hairstyle"),
            _option("structured_hime_cut", "姬髮式", "structured hime-cut hairstyle"),
            _option("braided_mohawk", "編辮莫霍克", "braided mohawk hairstyle"),
            _option("low_chignon", "法式低髻", "classic low chignon hairstyle"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("loose_straight",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="bangs",
        label_zh="瀏海",
        group="髮型與髮色",
        options=(
            _option("none", "無瀏海", "exposed forehead"),
            _option("straight", "齊瀏海", "straight bangs"),
            _option("side_swept", "側分瀏海", "side-swept bangs"),
            _option("curtain", "窗簾瀏海", "curtain bangs"),
            _option("wispy", "空氣瀏海", "wispy bangs"),
            _option("hime", "姬髮式側鬢", "hime-cut sidelocks"),
            _option("micro", "眉上短瀏海", "micro bangs"),
            _option("arched", "弧形瀏海", "arched bangs"),
            _option("choppy", "碎剪瀏海", "choppy bangs"),
            _option("long_side", "長側瀏海", "long side bangs"),
            _option("split", "中分瀏海", "center-parted bangs"),
            _option("braided", "編髮瀏海", "braided bangs"),
            _option("bottleneck", "瓶頸瀏海", "bottleneck bangs"),
            _option("side_piece", "側邊重點瀏海", "face-framing side bangs"),
            _option("feathered", "羽毛瀏海", "feathered bangs"),
            _option("curly", "捲曲瀏海", "curly bangs"),
            _option("asymmetrical", "不對稱瀏海", "asymmetrical bangs"),
            _option("veil", "面紗式長瀏海", "veil-like long bangs"),
            _option("bangs_v_shaped", "V 字瀏海", "V-shaped bangs"),
            _option("bangs_deep_side_part", "深側分瀏海", "deep side-parted bangs"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("side_swept",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="hair_color",
        label_zh="髮色",
        group="髮型與髮色",
        options=(
            _option("black", "黑色", "black hair"),
            _option("dark_brown", "深棕色", "dark brown hair"),
            _option("brown", "棕色", "brown hair"),
            _option("blonde", "金色", "blonde hair"),
            _option("platinum", "白金色", "platinum blonde hair"),
            _option("silver", "銀色", "silver hair"),
            _option("white", "純白色", "white hair"),
            _option("red", "紅色", "red hair"),
            _option("auburn", "赤褐色", "auburn hair"),
            _option("pink", "粉紅色", "pink hair"),
            _option("blue", "藍色", "blue hair"),
            _option("teal", "藍綠色", "teal hair"),
            _option("green", "綠色", "green hair"),
            _option("purple", "紫色", "purple hair"),
            _option("rose_gold", "玫瑰金", "rose-gold hair"),
            _option("lavender", "薰衣草紫", "lavender hair"),
            _option("navy", "深藍", "navy-blue hair"),
            _option("cyan", "青色", "cyan hair"),
            _option("orange", "橙色", "orange hair"),
            _option("peach", "蜜桃色", "peach-colored hair"),
            _option("mint", "薄荷綠", "mint-green hair"),
            _option("burgundy", "酒紅", "burgundy hair"),
            _option("charcoal", "炭黑色", "charcoal-black hair"),
            _option("chestnut", "栗棕色", "chestnut-brown hair"),
            _option("honey_blonde", "蜂蜜金", "honey-blonde hair"),
            _option("strawberry_blonde", "草莓金", "strawberry-blonde hair"),
            _option("copper", "赤銅色", "copper-red hair"),
            _option("scarlet", "鮮紅色", "scarlet hair"),
            _option("cobalt", "鈷藍色", "cobalt-blue hair"),
            _option("emerald", "翡翠綠", "emerald-green hair"),
            _option("magenta", "洋紅色", "magenta hair"),
            _option("lilac", "淡紫丁香色", "lilac hair"),
            _option("ash_brown", "灰棕色", "ash-brown hair"),
            _option("smoky_gray", "煙霧灰", "smoky-gray hair"),
            _option("midnight_purple", "午夜紫", "midnight-purple hair"),
            _option("seafoam_green", "海沫綠", "seafoam-green hair"),
            _option("slate_blue", "石板藍", "slate-blue hair"),
            _option("champagne_blonde", "香檳金", "champagne-blonde hair"),
            _option("mauve", "灰紫色", "mauve hair"),
            _option("forest_green", "森林深綠", "forest-green hair"),
        ),
        selection_mode="multi",
        help_text="可選一至三種髮色；多色會自動合成自然英文片語。",
        default_keys=("black",),
        random_min=1,
        random_max=3,
        applicable_gender=None,
        selection_max=3,
    ),
    TagCategory(
        key="hair_texture",
        label_zh="髮質效果",
        group="髮型與髮色",
        options=(
            _option("silky", "絲滑", "silky hair texture"),
            _option("fluffy", "蓬鬆", "fluffy hair texture"),
            _option("tousled", "微亂", "tousled hair texture"),
            _option("coarse", "粗獷", "coarse hair texture"),
            _option("wet", "濕髮", "wet hair"),
            _option("glowing", "魔法微光", "faintly glowing hair"),
            _option("fine", "細軟", "fine hair texture"),
            _option("thick", "濃密", "thick hair texture"),
            _option("kinky", "緊密捲曲", "tightly coiled hair"),
            _option("feathered", "羽毛層次", "feathered hair texture"),
            _option("windswept", "風吹動感", "windswept hair"),
            _option("metallic", "金屬光澤", "metallic hair sheen"),
            _option("glasslike", "玻璃光澤", "glasslike hair sheen"),
            _option("velvety", "天鵝絨質感", "velvety hair texture"),
            _option("frizzy", "自然毛躁", "naturally frizzy hair"),
            _option("crimped", "玉米鬚壓紋", "crimped hair texture"),
            _option("ringlets", "螺旋小捲", "defined ringlet texture"),
            _option("cloudlike", "雲朵蓬鬆", "cloudlike fluffy hair"),
            _option("hair_texture_matte", "柔霧髮質", "soft matte hair texture"),
            _option("hair_texture_soft_wave", "柔和波紋髮質", "softly waved hair texture"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("silky",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="expression",
        label_zh="面部表情／情緒",
        group="個性與奇幻特徵",
        options=(
            _option(
                "tsundere",
                "傲嬌",
                "tsundere personality with a prickly aloof exterior masking shy affection",
            ),
            _option(
                "yandere",
                "病嬌",
                "yandere personality with an outwardly sweet demeanor and obsessively "
                "possessive devotion",
            ),
            _option(
                "kuudere",
                "冷嬌／冰山美人",
                "kuudere personality with a cool emotionally reserved exterior and hidden "
                "tenderness",
            ),
            _option(
                "dandere",
                "害羞內向／無口",
                "dandere personality with a quiet shy introverted demeanor that opens up around"
                " trusted companions",
            ),
            _option("gentle", "溫柔", "gentle expression"),
            _option("cheerful", "開朗", "cheerful expression"),
            _option("shy", "害羞", "shy expression"),
            _option("lonely", "孤單", "lonely expression"),
            _option("melancholic", "憂鬱", "melancholic expression"),
            _option("stoic", "冷靜克制", "stoic expression"),
            _option("fierce", "凌厲", "fierce expression"),
            _option("confident", "自信", "confident expression"),
            _option("mischievous", "淘氣", "mischievous expression"),
            _option("weary", "疲憊", "weary expression"),
            _option("surprised", "驚訝", "surprised expression"),
            _option("serene", "安詳", "serene expression"),
            _option("hopeful", "充滿希望", "hopeful expression"),
            _option("determined", "堅定", "determined expression"),
            _option("defiant", "不屈", "defiant expression"),
            _option("suspicious", "狐疑", "suspicious expression"),
            _option("annoyed", "不耐", "annoyed expression"),
            _option("angry", "憤怒", "angry expression"),
            _option("terrified", "驚恐", "terrified expression"),
            _option("tearful", "含淚", "tearful expression"),
            _option("crying", "哭泣", "crying expression"),
            _option("embarrassed", "尷尬", "embarrassed expression"),
            _option("blushing", "臉紅", "blushing expression"),
            _option("smug", "得意", "smug expression"),
            _option("bored", "無聊", "bored expression"),
            _option("sleepy", "睏倦", "sleepy expression"),
            _option("focused", "專注", "focused expression"),
            _option("curious", "好奇", "curious expression"),
            _option("relieved", "如釋重負", "relieved expression"),
            _option("awed", "敬畏驚嘆", "awe-struck expression"),
            _option("guilty", "愧疚", "guilty expression"),
            _option("resigned", "認命", "resigned expression"),
            _option("protective", "守護意志", "protective expression"),
            _option("calculating", "精於算計", "calculating expression"),
            _option("playful", "玩心大起", "playful expression"),
            _option("nostalgic", "懷念", "nostalgic expression"),
            _option("haunted", "心有陰影", "haunted expression"),
            _option("triumphant", "勝利喜悅", "triumphant expression"),
            _option("devoted", "深情專注", "devoted expression"),
            _option("entranced", "著迷", "entranced expression"),
            _option("wry", "苦笑", "wry adult expression"),
            _option("skeptical", "懷疑挑眉", "skeptical raised-brow expression"),
            _option("reverent", "敬畏虔誠", "reverent expression"),
            _option("homesick", "思鄉神情", "homesick adult expression"),
            _option("guarded", "戒備克制", "guarded restrained expression"),
            _option("compassionate", "憐憫溫柔", "compassionate expression"),
            _option("grim_resolve", "冷峻決意", "grimly resolved expression"),
            _option("quiet_pride", "沉靜自豪", "quietly proud expression"),
            _option("dry_amusement", "淡淡揶揄", "dryly amused expression"),
            _option("conflicted", "內心掙扎", "emotionally conflicted expression"),
            _option("expression_puzzled", "困惑", "puzzled expression"),
            _option("expression_delighted", "欣喜", "delighted expression"),
            _option("contemptuous", "輕蔑", "contemptuous expression"),
            _option("anxious", "焦慮不安", "anxious expression"),
            _option("jealous", "嫉妒", "jealous expression"),
            _option("solemn", "莊重肅穆", "solemn expression"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("gentle",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="fantasy_traits",
        label_zh="奇幻身體特徵",
        group="個性與奇幻特徵",
        options=(
            _option("pointed_ears", "尖耳", "pointed ears"),
            _option("fangs", "尖牙", "visible fangs"),
            _option("horns", "角", "ornate horns"),
            _option("halo", "光環", "luminous halo"),
            _option("feathered_wings", "羽翼", "feathered wings"),
            _option("bat_wings", "蝠翼", "batlike wings"),
            _option("fairy_wings", "妖精薄翼", "translucent insectlike wings"),
            _option("dragon_wings", "龍翼", "dragon wings"),
            _option("tail", "尾巴", "expressive tail"),
            _option("scales", "鱗片", "iridescent scales"),
            _option("fur", "局部獸毛", "soft patches of fur"),
            _option("crystal_growths", "晶體生長", "crystalline growths"),
            _option("glowing_runes", "發光符文", "glowing runes on skin"),
            _option("mechanical_limbs", "機械義肢", "intricate mechanical limbs"),
            _option("ethereal_aura", "靈氣", "ethereal aura"),
            _option("antlers", "鹿角", "branching antlers"),
            _option("fin_ears", "魚鰭耳", "finlike ears"),
            _option("gills", "鰓", "visible gills"),
            _option("tentacle_hair", "觸手髮", "living tentacle hair"),
            _option("extra_arms", "多臂", "multiple symmetrical arms"),
            _option("third_eye", "第三眼", "mystical third eye"),
            _option("stone_skin", "石質肌膚", "stone-textured skin"),
            _option("wooden_skin", "木質肌膚", "wood-grain skin"),
            _option("slime_body", "凝膠身體", "translucent slime body"),
            _option("flame_hair", "火焰髮", "living flame hair"),
            _option("shadow_body", "影子身體", "shadowlike body"),
            _option("constellation_skin", "星座肌膚", "constellations across the skin"),
            _option("hooves", "蹄足", "fantasy hooves"),
            _option("equine_lower_body", "馬身下半身", "equine lower body"),
            _option("serpentine_lower_body", "蛇身下半身", "serpentine lower body"),
            _option("spider_lower_body", "蜘蛛下半身", "spiderlike lower body"),
            _option("avian_talons", "鳥爪足", "avian talons"),
            _option("bird_feathers", "鳥羽覆體", "decorative body feathers"),
            _option("phoenix_wings", "鳳凰火翼", "fiery phoenix wings"),
            _option("multiple_tails", "多條尾巴", "multiple expressive tails"),
            _option("fox_ears", "狐耳", "foxlike ears"),
            _option("bovine_horns", "牛角", "sweeping bovine horns"),
            _option("goat_horns", "山羊角", "curled goat horns"),
            _option("insect_antennae", "昆蟲觸角", "delicate insect antennae"),
            _option("chitin", "幾丁質甲殼", "chitinous body plates"),
            _option("compound_eyes", "複眼", "faceted compound eyes"),
            _option("shark_tail", "鯊魚尾", "powerful shark tail"),
            _option("web_spinnerets", "蛛絲器官", "fantasy web spinnerets"),
            _option("living_vines", "活藤蔓", "living vines around the body"),
            _option("body_flowers", "身體花朵", "flowers growing from the body"),
            _option("mushroom_cap", "蕈傘冠", "mushroom-cap crown"),
            _option("spore_cloud", "孢子微光", "faint luminous spore cloud"),
            _option("crystal_body", "水晶身體", "translucent crystalline body"),
            _option("liquid_body", "液態身體", "flowing liquid body"),
            _option("smoke_body", "煙霧身體", "partially smoke-formed body"),
            _option("spectral_limbs", "幽體肢體", "translucent spectral limbs"),
            _option("eye_cluster", "多眼群", "cluster of mystical eyes"),
            _option("bioluminescent_patterns", "生物光紋", "bioluminescent body patterns"),
            _option("beak", "鳥喙", "elegant avian beak"),
            _option("fantasy_floating_crown", "懸浮魔冠", "floating arcane crown above the head"),
            _option(
                "fantasy_glowing_core",
                "發光魔力核心",
                "visible glowing magical core within the torso",
            ),
            _option("crystal_antlers", "水晶鹿角", "translucent crystal antlers"),
            _option("orbiting_runestones", "環繞符文石", "small rune stones orbiting the body"),
            _option("porcelain_joints", "瓷質關節", "visible articulated porcelain joints"),
            _option(
                "constellation_hair",
                "星座流光髮",
                "living constellation lights flowing through the hair",
            ),
            _option("fantasy_halo_shards", "懸浮光環碎片", "floating luminous halo fragments"),
            _option("fantasy_mirror_skin", "鏡面肌膚", "mirrorlike reflective skin"),
            _option("fantasy_living_ink", "活體墨紋", "living ink markings moving across the skin"),
            _option("fantasy_orbiting_planets", "微型行星環", "miniature planets orbiting the body"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=0,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="distinctive_marks",
        label_zh="辨識特徵",
        group="個性與奇幻特徵",
        options=(
            _option("freckles", "雀斑", "soft freckles"),
            _option("beauty_mark", "美人痣", "beauty mark"),
            _option("facial_scar", "臉部疤痕", "facial scar"),
            _option("body_scar", "身體疤痕", "visible old scars"),
            _option("tattoo", "刺青", "ornate tattoo"),
            _option("war_paint", "戰紋", "ritual war paint"),
            _option("golden_cracks", "金繼裂紋", "golden kintsugi-like cracks"),
            _option("nose_scar", "鼻樑疤痕", "scar across the nose"),
            _option("lip_scar", "唇邊疤痕", "small scar near the lip"),
            _option("burn_scar", "燒傷疤痕", "old burn scar"),
            _option("birthmark", "胎記", "distinctive birthmark"),
            _option("vitiligo", "白斑膚色", "vitiligo skin pattern"),
            _option("dimples", "酒窩", "visible dimples"),
            _option("pierced_ears", "多耳洞", "multiple ear piercings"),
            _option("nose_piercing", "鼻環", "nose piercing"),
            _option("lip_piercing", "唇環", "lip piercing"),
            _option("facial_runes", "臉部符文", "facial rune markings"),
            _option("cyber_lines", "義體接縫", "subtle cybernetic seam lines"),
            _option("glowing_freckles", "發光雀斑", "glowing freckles"),
            _option("metallic_makeup", "金屬妝", "metallic eye makeup"),
            _option("smoky_makeup", "暈染煙燻妝", "softly smudged smoky makeup accent"),
            _option("constellation_tattoo", "星座刺青", "constellation tattoo"),
            _option("ritual_brand", "儀式烙印", "old ritual brand"),
            _option("surgical_scars", "手術疤痕", "subtle surgical scars"),
            _option("glowing_veins", "發光脈絡", "subtle glowing veins"),
            _option("ink_stained_hands", "墨跡手指", "ink-stained hands"),
            _option("mark_prosthetic_eye", "精密義眼", "detailed visible prosthetic eye"),
            _option("mark_calloused_hands", "粗繭雙手", "visibly weathered calloused hands"),
            _option(
                "mark_stretch_lines",
                "自然伸展紋",
                "subtle natural stretch-mark lines across the skin",
            ),
            _option("mark_constellation_freckles", "星座雀斑", "constellation-shaped freckle pattern"),
            _option(
                "mark_ritual_scarification",
                "儀式性刻痕",
                "intentional ceremonial scarification pattern",
            ),
            _option("mark_lightning_scar", "閃電狀疤痕", "branching lightning-shaped scar pattern"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="outfit_archetype",
        label_zh="穿搭主題／基底",
        group="服裝與配件",
        options=(
            _option("casual", "日常休閒", "layered casual outfit"),
            _option("streetwear", "潮流街頭", "modern streetwear"),
            _option("formal", "正式禮服", "elegant formal attire"),
            _option("business", "俐落商務", "tailored business attire"),
            _option("traveler", "奇幻旅人", "weathered fantasy traveler outfit"),
            _option("mage", "法師", "ornate fantasy mage robes"),
            _option("knight", "騎士", "fitted fantasy knight armor"),
            _option("rogue", "盜賊／遊俠", "practical fantasy rogue outfit"),
            _option("ranger", "荒野遊俠", "layered fantasy ranger outfit"),
            _option("priest", "祭司", "ceremonial priestly robes"),
            _option("royal", "王族", "regal court attire"),
            _option("alchemist", "鍊金術師", "detailed alchemist outfit"),
            _option("steampunk", "蒸汽龐克", "steampunk adventurer outfit"),
            _option("cyberpunk", "賽博龐克", "cyberpunk tactical fashion"),
            _option("space_suit", "太空裝", "sleek science-fiction spacesuit"),
            _option("kimono", "和風服飾", "layered traditional Japanese attire"),
            _option("hanfu", "漢服風", "flowing historical Chinese attire"),
            _option("workwear", "機能工作服", "practical workwear"),
            _option("sportswear", "運動服", "modern adult sportswear"),
            _option("swimwear", "泳裝", "fitted adult swimwear"),
            _option("ball_gown", "舞會禮服", "ornate ball gown"),
            _option("cocktail_dress", "雞尾酒禮服", "elegant cocktail dress"),
            _option("tailcoat", "燕尾服", "formal tailcoat ensemble"),
            _option("trench_coat", "風衣", "layered trench-coat outfit"),
            _option("pilot", "飛行員裝", "detailed pilot outfit"),
            _option("sailor", "航海家裝", "weathered sailor outfit"),
            _option("mechanic", "機械師裝", "grease-stained mechanic outfit"),
            _option("healer", "治療師裝", "fantasy healer robes"),
            _option("scholar", "學者裝", "layered adult scholar attire"),
            _option("witch", "女巫裝", "elegant fantasy witch outfit"),
            _option("samurai", "武士甲冑", "layered samurai armor"),
            _option("ninja", "忍者裝", "practical shinobi outfit"),
            _option("desert_nomad", "沙漠遊牧裝", "layered desert nomad attire"),
            _option("tribal_fantasy", "奇幻部族裝", "respectful fantasy tribal attire"),
            _option("post_apocalyptic", "末日倖存者裝", "patched post-apocalyptic outfit"),
            _option("gothic_frilled", "成年哥德洋裝", "adult gothic frilled fashion"),
            _option("techwear", "科技機能服", "futuristic techwear"),
            _option("outfit_chef", "主廚服", "professional chef attire"),
            _option("outfit_archaeologist", "考古學者裝", "rugged archaeologist field attire"),
            _option("outfit_stage_musician", "舞臺樂手裝", "stylish stage-musician attire"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("traveler",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="outfit_materials",
        label_zh="服裝材質",
        group="服裝與配件",
        options=(
            _option("linen", "亞麻", "textured linen fabric"),
            _option("silk", "絲綢", "lustrous silk fabric"),
            _option("velvet", "天鵝絨", "rich velvet fabric"),
            _option("leather", "皮革", "weathered leather details"),
            _option("metal", "金屬", "polished metal accents"),
            _option("lace", "蕾絲", "delicate lace details"),
            _option("fur_trim", "毛皮滾邊", "soft fur trim"),
            _option("holographic", "全息材質", "holographic fabric accents"),
            _option("cotton", "棉布", "soft cotton fabric"),
            _option("denim", "丹寧", "textured denim fabric"),
            _option("wool", "羊毛", "woven wool fabric"),
            _option("chainmail", "鎖子甲", "interlocking chainmail"),
            _option("scale_armor", "鱗甲", "layered scale armor"),
            _option("ceramic", "陶瓷甲片", "glazed ceramic armor plates"),
            _option("crystal", "水晶材質", "faceted crystal clothing accents"),
            _option("mesh", "網布", "layered mesh fabric"),
            _option("material_brocade", "織錦", "ornate brocade fabric"),
            _option("material_suede", "麂皮", "soft suede details"),
            _option("faux_leather", "人造皮革", "faux-leather fabric"),
            _option("knit", "針織", "knitted fabric"),
            _option("nylon", "尼龍", "nylon fabric"),
            _option("chiffon", "雪紡", "chiffon fabric"),
            _option("patent_leather", "漆皮", "patent leather"),
            _option("satin", "緞面", "satin fabric"),
            _option("organza", "歐根紗", "organza fabric"),
            _option("tulle", "薄紗網", "tulle fabric"),
            _option("jersey_knit", "針織汗布", "jersey-knit fabric"),
            _option("ribbed_knit", "羅紋針織", "ribbed knit fabric"),
            _option("canvas", "帆布", "canvas fabric"),
            _option("tweed", "花呢", "tweed fabric"),
            _option("corduroy", "燈芯絨", "corduroy fabric"),
            _option("fleece", "刷毛", "fleece fabric"),
            _option("faux_fur", "人造毛皮", "faux-fur fabric"),
            _option("sequined", "亮片布", "sequined fabric"),
            _option("metallic_textile", "金屬光澤布", "metallic textile"),
            _option("neoprene", "氯丁橡膠", "neoprene fabric"),
            _option("bamboo_fiber", "竹纖維", "bamboo-fiber fabric"),
            _option("ramie", "苧麻", "ramie fabric"),
            _option("cashmere", "喀什米爾", "cashmere fabric"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=1,
        random_max=2,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="outfit_palette",
        label_zh="服裝配色",
        group="服裝與配件",
        options=(
            _option("monochrome", "黑白單色", "monochrome clothing palette"),
            _option("pastel", "柔和粉彩", "soft pastel clothing palette"),
            _option("earth", "大地色", "earth-tone clothing palette"),
            _option("jewel", "寶石色", "rich jewel-tone clothing palette"),
            _option("warm", "暖色", "warm clothing palette"),
            _option("cool", "冷色", "cool clothing palette"),
            _option("black_red", "黑紅", "black and crimson clothing palette"),
            _option("white_gold", "白金", "white and gold clothing palette"),
            _option("blue_silver", "藍銀", "blue and silver clothing palette"),
            _option("red_gold", "紅金", "crimson-and-gold clothing palette"),
            _option("green_bronze", "綠銅", "green-and-bronze clothing palette"),
            _option("purple_black", "紫黑", "purple-and-black clothing palette"),
            _option("pink_white", "粉白", "pink-and-white clothing palette"),
            _option("rainbow", "彩虹", "rainbow clothing palette"),
            _option("iridescent", "虹彩", "iridescent clothing palette"),
            _option("desaturated", "低飽和", "desaturated clothing palette"),
            _option("custom_emblem", "家徽配色", "heraldic clothing palette"),
            _option("palette_navy_cream", "海軍藍米白", "navy-and-cream clothing palette"),
            _option("palette_teal_copper", "青綠銅色", "teal-and-copper clothing palette"),
            _option("palette_burgundy_gray", "酒紅灰色", "burgundy-and-gray clothing palette"),
            _option("palette_black", "黑色", "black clothing palette"),
            _option("palette_white", "白色", "white clothing palette"),
            _option("palette_gray", "灰色", "gray clothing palette"),
            _option("palette_silver", "銀色", "silver clothing palette"),
            _option("palette_red", "紅色", "red clothing palette"),
            _option("palette_burgundy", "酒紅色", "burgundy clothing palette"),
            _option("palette_pink", "粉紅色", "pink clothing palette"),
            _option("palette_orange", "橙色", "orange clothing palette"),
            _option("palette_yellow", "黃色", "yellow clothing palette"),
            _option("palette_gold", "金色", "gold clothing palette"),
            _option("palette_green", "綠色", "green clothing palette"),
            _option("palette_olive", "橄欖綠", "olive clothing palette"),
            _option("palette_teal", "青綠色", "teal clothing palette"),
            _option("palette_cyan", "青色", "cyan clothing palette"),
            _option("palette_blue", "藍色", "blue clothing palette"),
            _option("palette_navy", "海軍藍", "navy clothing palette"),
            _option("palette_purple", "紫色", "purple clothing palette"),
            _option("palette_brown", "棕色", "brown clothing palette"),
            _option("palette_beige", "米色", "beige clothing palette"),
            _option("palette_cream", "奶油白", "cream clothing palette"),
            _option("palette_multicolored", "多色", "multicolored clothing palette"),
            _option("palette_copper", "銅色", "copper clothing palette"),
            _option("palette_bronze", "青銅色", "bronze clothing palette"),
            _option("palette_rose_gold", "玫瑰金", "rose-gold clothing palette"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("earth",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="accessories",
        label_zh="經典配件／裝備",
        group="服裝與配件",
        options=(
            _option("earrings", "耳環", "distinctive earrings"),
            _option("necklace", "項鍊", "ornate necklace"),
            _option("choker", "頸飾", "decorative choker"),
            _option("gloves", "手套", "fitted gloves"),
            _option("cape", "披風", "flowing cape"),
            _option("hood", "兜帽", "layered hood"),
            _option("crown", "王冠", "delicate crown"),
            _option("wide_hat", "寬帽", "wide-brimmed hat"),
            _option("sword", "長劍", "ornate sword"),
            _option("staff", "法杖", "arcane staff"),
            _option("book", "魔導書", "ancient spellbook"),
            _option("lantern", "提燈", "glowing lantern"),
            _option("satchel", "側背包", "weathered satchel"),
            _option("bracelets", "手鐲", "stacked bracelets"),
            _option("rings", "戒指", "ornate rings"),
            _option("brooch", "胸針", "decorative brooch"),
            _option("hairpin", "髮簪", "ornate hairpin"),
            _option("tiara", "小皇冠", "delicate tiara"),
            _option("mask", "面具", "ornate face mask"),
            _option("scarf", "圍巾", "flowing scarf"),
            _option("belt_pouches", "腰包", "utility belt pouches"),
            _option("quiver", "箭袋", "detailed arrow quiver"),
            _option("shield", "盾牌", "decorated shield"),
            _option("dagger", "匕首", "ornate dagger"),
            _option("bow", "長弓", "elegant longbow"),
            _option("hammer", "戰鎚", "fantasy war hammer"),
            _option("potion_belt", "藥水帶", "belt of glowing potions"),
            _option("mechanical_drone", "機械無人機", "small companion drone"),
            _option("pocket_watch", "懷錶", "antique pocket watch"),
            _option("umbrella", "傘", "decorative umbrella"),
            _option("ear_cuffs", "耳骨夾", "ornate ear cuffs"),
            _option("shoulder_cape", "單肩披風", "single-shoulder cape"),
            _option("spellbook", "魔法書", "ornate spellbook"),
            _option("greatsword", "巨劍", "massive fantasy greatsword"),
            _option("fan", "摺扇", "ornate folding fan"),
            _option("parasol", "陽傘", "decorative parasol"),
            _option("prosthetic_arm", "義肢手臂", "detailed prosthetic arm"),
            _option("accessory_gauntlets", "護手甲", "ornate protective gauntlets"),
            _option("accessory_talisman", "護身符", "inscribed protective talisman"),
            _option("accessory_spyglass", "單筒望遠鏡", "antique handheld spyglass"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=0,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="pose",
        label_zh="姿勢／動作",
        group="畫面與風格",
        options=(
            _option("standing", "自然站姿", "natural standing pose"),
            _option("walking", "行走中", "walking pose"),
            _option("running", "奔跑中", "dynamic running pose"),
            _option("seated", "坐姿", "relaxed seated pose"),
            _option("looking_back", "回眸", "looking back over shoulder"),
            _option("combat", "戰鬥架勢", "dynamic combat stance"),
            _option("casting", "施法", "casting magic"),
            _option("weapon_ready", "持武戒備", "weapon-ready stance"),
            _option("floating", "漂浮", "weightless floating pose"),
            _option("arms_crossed", "雙臂交叉", "arms-crossed pose"),
            _option("hands_on_hips", "雙手叉腰", "hands-on-hips pose"),
            _option("one_hand_raised", "單手舉起", "one hand raised"),
            _option("reaching_out", "伸手向前", "reaching toward the viewer"),
            _option("kneeling", "跪姿", "kneeling pose"),
            _option("crouching", "蹲姿", "crouching pose"),
            _option("lying_down", "躺姿", "lying-down pose"),
            _option("leaning_wall", "倚牆", "leaning against a wall"),
            _option("turning", "轉身動作", "turning in motion"),
            _option("jumping", "跳躍", "dynamic jumping pose"),
            _option("dancing", "舞蹈", "expressive dancing pose"),
            _option("bowing", "鞠躬", "formal bowing pose"),
            _option("meditating", "冥想", "cross-legged meditation pose"),
            _option("reading", "閱讀", "reading a book"),
            _option("writing", "書寫", "writing in a journal"),
            _option("drinking", "飲用飲料", "drinking from a cup"),
            _option("playing_instrument", "演奏樂器", "playing a musical instrument"),
            _option("aiming_bow", "拉弓瞄準", "aiming a drawn bow"),
            _option("sword_swing", "揮劍", "dynamic sword-swinging action"),
            _option("shield_guard", "舉盾防禦", "defensive shield stance"),
            _option("sprinting", "全速奔跑", "full sprinting action"),
            _option("climbing", "攀爬", "dynamic climbing pose"),
            _option("praying", "祈禱", "reverent praying pose"),
            _option("embracing_self", "環抱自己", "self-embracing pose"),
            _option("hand_to_heart", "手按胸口", "hand-over-heart pose"),
            _option("drawing_weapon", "拔出武器", "weapon-drawing action"),
            _option("casting_two_hands", "雙手施法", "two-handed spellcasting pose"),
            _option("greeting_wave", "揮手招呼", "friendly waving gesture"),
            _option("point_forward", "向前指示", "assertive pointing gesture toward the viewer"),
            _option("hands_behind_back", "雙手背後", "relaxed pose with both hands behind the back"),
            _option("one_hand_pocket", "單手插袋", "casual pose with one hand in a pocket"),
            _option("both_hands_pockets", "雙手插袋", "casual pose with both hands in pockets"),
            _option("adjust_glasses", "扶正眼鏡", "adjusting glasses with one hand"),
            _option("brush_hair_back", "撥髮向後", "brushing hair back with one hand"),
            _option("tie_hair", "綁起頭髮", "tying hair into place with both hands"),
            _option("overhead_stretch", "雙臂上舉伸展", "stretching both arms overhead"),
            _option("lean_forward", "身體前傾", "attentive forward-leaning pose"),
            _option("seated_crossed_legs", "交叉腿坐姿", "seated pose with legs crossed"),
            _option("seated_sideways", "側身坐姿", "sideways seated pose"),
            _option("sitting_on_edge", "坐在邊緣", "sitting carefully on the edge of a surface"),
            _option("lying_on_side", "側躺", "relaxed side-lying pose"),
            _option("stepping_forward", "向前跨步", "decisive forward-stepping pose"),
            _option("forward_lunge", "向前弓步", "dynamic forward lunge"),
            _option("dodging", "閃避", "agile dodging action"),
            _option("heroic_landing", "英雄式落地", "dynamic three-point landing pose"),
            _option("spinning", "旋身", "fluid spinning motion"),
            _option("high_kick", "高踢", "controlled high-kick action"),
            _option("punching", "出拳", "powerful forward punching action"),
            _option("one_leg_balance", "單腳平衡", "balanced pose standing on one leg"),
            _option("carry_object", "搬抱物品", "carrying a substantial object with both arms"),
            _option("inspect_object", "仔細查看物品", "carefully inspecting a handheld object"),
            _option("hold_umbrella", "撐傘", "holding an open umbrella overhead"),
            _option("salute", "敬禮", "formal saluting pose"),
            _option("shrug", "聳肩", "expressive shrugging gesture"),
            _option("listening", "側耳傾聽", "attentive listening pose"),
            _option("hand_on_chin", "手托下巴思考", "thoughtful pose with one hand resting on the chin"),
            _option(
                "hands_clasped_front",
                "雙手身前交握",
                "composed pose with both hands clasped in front",
            ),
            _option("arms_open_welcome", "張臂迎接", "welcoming pose with both arms open"),
            _option("covering_mouth", "單手掩嘴", "expressive pose with one hand covering the mouth"),
            _option("palms_together", "雙掌合十", "calm pose with both palms pressed together"),
            _option("shade_eyes", "手搭額前遠眺", "scouting pose shading the eyes with one hand"),
            _option(
                "head_resting_on_hand",
                "頭靠手掌",
                "seated pose resting the head against one hand",
            ),
            _option("pose_tiptoe_reach", "踮腳伸手", "reaching upward while standing on tiptoe"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("standing",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="framing",
        label_zh="取景範圍",
        group="畫面與風格",
        options=(
            _option("face", "臉部特寫", "facial close-up"),
            _option("bust", "胸像", "bust portrait"),
            _option("half_body", "半身", "half-body portrait"),
            _option("three_quarter", "四分之三身", "three-quarter body portrait"),
            _option("full_body", "全身", "full-body character portrait"),
            _option("character_sheet", "角色設定圖", "character design sheet"),
            _option("extreme_closeup", "極特寫", "extreme facial close-up"),
            _option("cowboy_shot", "牛仔景", "cowboy-shot framing"),
            _option("wide_full_body", "廣景全身", "wide full-body framing"),
            _option("turnaround_sheet", "多視角設定圖", "multi-view character turnaround sheet"),
            _option("head_shoulders", "頭肩構圖", "head-and-shoulders portrait framing"),
            _option("upper_body", "上半身構圖", "upper-body character framing"),
            _option("knee_up", "膝上構圖", "knee-up character portrait"),
            _option(
                "full_body_negative_space",
                "全身留白構圖",
                "full-body character framing with generous negative space",
            ),
            _option("tight_full_body", "緊湊全身構圖", "tightly cropped full-body character framing"),
            _option(
                "environmental_portrait",
                "環境式角色肖像",
                "wide environmental character portrait framing",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("full_body",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="viewpoint",
        label_zh="鏡頭視角",
        group="畫面與風格",
        options=(
            _option("eye_level", "平視", "eye-level view"),
            _option("low_angle", "低角度仰視", "low-angle view"),
            _option("high_angle", "高角度俯視", "high-angle view"),
            _option("profile", "側面", "profile view"),
            _option("three_quarter", "四分之三側面", "three-quarter view"),
            _option("back_view", "背面", "back view"),
            _option("front", "正面", "front view"),
            _option("rear_three_quarter", "後方四分之三", "rear three-quarter view"),
            _option("dutch_angle", "荷蘭角", "Dutch-angle view"),
            _option("overhead", "正上方俯視", "overhead view"),
            _option("worm_eye", "蟲視角", "worm's-eye view"),
            _option("over_shoulder", "肩後視角", "over-the-shoulder character view"),
            _option("first_person", "主觀視角", "first-person point-of-view shot"),
            _option("oblique_birdseye", "斜向鳥瞰", "oblique bird's-eye view"),
            _option("ground_level", "低貼地面視角", "ground-level character view"),
            _option("mirror_reflection", "鏡面反射視角", "mirror-reflection character view"),
            _option("profile_low_angle", "側面低角度", "side-profile low-angle view"),
            _option("three_quarter_low_angle", "四分之三低角度", "three-quarter low-angle character view"),
            _option(
                "three_quarter_high_angle",
                "四分之三高角度",
                "three-quarter high-angle character view",
            ),
            _option("rear_low_angle", "背面低角度", "rear low-angle character view"),
            _option("rear_high_angle", "背面高角度", "rear high-angle character view"),
            _option("side_high_angle", "側面高角度", "side-profile high-angle view"),
            _option(
                "through_foreground",
                "前景框景視角",
                "character viewed through a soft foreground frame",
            ),
            _option(
                "shallow_water_reflection",
                "淺水倒影視角",
                "character viewed through a shallow-water reflection",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("three_quarter",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="character_lighting",
        label_zh="角色打光",
        group="畫面與風格",
        options=(
            _option("soft_daylight", "柔和日光", "soft natural daylight"),
            _option("golden_hour", "金色夕照", "golden-hour lighting"),
            _option("moonlight", "月光", "cool moonlight"),
            _option("rim_light", "輪廓光", "dramatic rim lighting"),
            _option("candlelight", "燭光", "warm candlelight"),
            _option("neon", "霓虹光", "colorful neon lighting"),
            _option("volumetric", "體積光", "volumetric lighting"),
            _option("studio", "棚拍光", "clean studio lighting"),
            _option("rembrandt", "林布蘭光", "Rembrandt lighting"),
            _option("butterfly", "蝴蝶光", "butterfly portrait lighting"),
            _option("split", "分割光", "dramatic split lighting"),
            _option("underlight", "下方打光", "theatrical underlighting"),
            _option("colored_gel", "彩色濾片光", "colored-gel studio lighting"),
            _option("magic_glow", "魔法自發光", "magical self-illumination"),
            _option("firelight", "營火光", "warm flickering campfire illumination on the character"),
            _option("overcast", "陰天柔光", "soft overcast lighting"),
            _option("moonlit", "冷色月輪光", "cool moonlit character lighting"),
            _option("candlelit", "暖燭群光", "warm candlelit character lighting"),
            _option("bioluminescent", "生物光", "bioluminescent character lighting"),
            _option("neon_rim", "霓虹輪廓光", "neon rim lighting"),
            _option("volumetric_backlight", "體積逆光", "volumetric backlighting"),
            _option("eclipse", "日蝕光", "eclipse-edge character lighting"),
            _option("lantern_side", "提燈側光", "warm lantern side lighting"),
            _option("storm_flash", "暴風閃光", "dramatic storm-flash lighting"),
            _option("prismatic", "稜鏡折射光", "prismatic refracted lighting"),
            _option("window_pattern", "窗格投影光", "patterned window-cast lighting"),
            _option("deep_sea_glow", "深海幽光", "deep-sea luminous lighting"),
            _option("embers", "餘燼光", "soft ember-lit illumination"),
            _option("dappled_canopy", "林間斑駁光", "dappled sunlight filtered through a leafy canopy"),
            _option("water_caustics", "水波焦散光", "rippling water-caustic light across the character"),
            _option("sunrise_backlight", "日出逆光", "warm sunrise backlighting"),
            _option("projected_pattern", "投影圖紋光", "graphic projected-pattern lighting"),
            _option("dual_color_rim", "雙色輪廓光", "contrasting dual-color rim lighting"),
            _option("large_softbox", "大型柔光箱", "large-softbox portrait lighting"),
            _option("snow_reflection", "雪地反射光", "cool reflected-snow illumination"),
            _option(
                "lighting_aurora_glow",
                "極光映照",
                "multicolored aurora glow illuminating the character",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("soft_daylight",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="character_style",
        label_zh="美術風格",
        group="畫面與風格",
        options=(
            _option("anime", "動漫插畫", "polished anime illustration"),
            _option("semi_realistic", "半寫實", "semi-realistic character illustration"),
            _option("realistic", "寫實概念圖", "realistic character concept art"),
            _option("painterly", "奇幻厚塗", "painterly fantasy illustration"),
            _option("cel_shaded", "賽璐璐上色", "clean cel-shaded illustration"),
            _option("watercolor", "水彩", "delicate watercolor illustration"),
            _option("comic", "漫畫風", "graphic comic-book illustration"),
            _option("cinematic", "電影感", "cinematic character key art"),
            _option("pixel_art", "像素藝術", "detailed pixel art"),
            _option("manga_ink", "漫畫墨線", "detailed manga ink illustration"),
            _option("western_animation", "歐美動畫", "polished Western animation design"),
            _option("storybook", "故事書插畫", "storybook character illustration"),
            _option("gouache", "廣告顏料", "gouache character painting"),
            _option("oil_painting", "油畫", "classical oil-painted character portrait"),
            _option("charcoal", "炭筆", "expressive charcoal character drawing"),
            _option("low_poly", "低多邊形 3D", "stylized low-poly character render"),
            _option("clay", "黏土動畫", "handcrafted clay character render"),
            _option("retro_game", "復古遊戲立繪", "retro game character portrait"),
            _option("visual_novel", "視覺小說立繪", "polished visual-novel character art"),
            _option("dark_academia", "黑暗學院風", "dark-academia character illustration"),
            _option("art_nouveau", "新藝術風", "Art Nouveau character illustration"),
            _option("papercut", "剪紙風", "layered papercut character art"),
            _option("linocut", "油氈版畫", "linocut character illustration"),
            _option("ink_wash", "水墨風", "ink-wash character painting"),
            _option("synthwave", "合成波風", "synthwave character art"),
            _option("fashion_editorial", "時尚編輯風", "high-fashion editorial character art"),
            _option("etched_engraving", "蝕刻版畫", "etched engraving character art"),
            _option("tapestry", "織毯插畫", "woven tapestry character art"),
            _option("stained_glass", "彩繪玻璃", "stained-glass character art"),
            _option("porcelain_figure", "瓷偶質感", "painted porcelain figure style"),
            _option("luminous_manuscript", "泥金手抄本", "illuminated manuscript character art"),
            _option("graphic_novel", "圖像小說", "graphic-novel character illustration"),
            _option("anime_cel_shaded", "電視動畫賽璐璐", "crisp television-anime cel shading"),
            _option("anime_key_visual", "動畫主視覺", "polished anime promotional key visual"),
            _option("anime_model_art", "動畫角色設定稿風格", "professional anime character model art"),
            _option("anime_feature_film", "動畫電影質感", "cinematic anime feature-film rendering"),
            _option("retro_70s_anime", "1970 年代復古動畫", "1970s retro anime illustration"),
            _option("retro_80s_anime", "1980 年代復古動畫", "1980s retro anime illustration"),
            _option("retro_90s_anime", "1990 年代復古動畫", "1990s retro anime illustration"),
            _option("modern_tv_anime", "現代電視動畫", "modern television-anime character rendering"),
            _option("prestige_anime", "高規格動畫作畫", "prestige anime production-quality illustration"),
            _option("shoujo_anime", "少女漫畫動畫風", "ornate shoujo-anime character illustration"),
            _option("josei_anime", "女性向成熟動畫風", "refined josei-anime character illustration"),
            _option("seinen_anime", "青年漫畫動畫風", "detailed seinen-anime character illustration"),
            _option("mecha_anime", "機甲動畫風", "technical mecha-anime character rendering"),
            _option("fantasy_anime", "奇幻動畫風", "lush fantasy-anime character illustration"),
            _option("isekai_anime", "異世界動畫風", "colorful isekai-anime character illustration"),
            _option("anime_game_splash", "日系遊戲主視覺", "dynamic anime game splash illustration"),
            _option("anime_gacha_art", "日系抽卡角色圖", "premium anime gacha character artwork"),
            _option("anime_lineart", "精緻動畫線稿", "precise anime production line art"),
            _option("painterly_anime", "動畫厚塗融合", "painterly anime character rendering"),
            _option("watercolor_anime", "動畫水彩融合", "watercolor anime character rendering"),
            _option("donghua_animation", "國風動畫", "polished Chinese donghua character rendering"),
            _option(
                "western_tv_animation",
                "現代歐美電視動畫",
                "modern Western television-animation design",
            ),
            _option("full_animation", "全動畫作畫", "fluid full-animation character design"),
            _option("limited_animation", "有限動畫作畫", "graphic limited-animation character design"),
            _option("cutout_animation", "剪紙動畫", "layered cutout-animation character design"),
            _option("stop_motion", "定格動畫", "handcrafted stop-motion character design"),
            _option("puppet_animation", "偶動畫", "articulated puppet-animation character design"),
            _option(
                "rotoscope_animation",
                "轉描動畫",
                "expressive rotoscoped-animation character rendering",
            ),
            _option("webtoon_rendering", "條漫上色風", "polished webtoon character rendering"),
            _option("manhwa_rendering", "韓式漫畫風", "refined manhwa character illustration"),
            _option(
                "style_colored_pencil",
                "彩色鉛筆",
                "layered colored-pencil character illustration",
            ),
            _option(
                "style_woodblock_print",
                "角色木刻版畫",
                "traditional woodblock-print character illustration",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("anime",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="character_backdrop",
        label_zh="角色圖背景",
        group="畫面與風格",
        options=(
            _option("transparent", "透明／去背感", "isolated character on a transparent background"),
            _option("simple", "簡潔背景", "simple unobtrusive backdrop"),
            _option("gradient", "漸層背景", "soft gradient backdrop"),
            _option("environmental", "情境背景", "story-rich environmental backdrop"),
            _option("ornamental", "裝飾框景", "ornamental fantasy backdrop"),
            _option("spotlight", "聚光舞台", "dark stage spotlight backdrop"),
            _option("graphic_shapes", "幾何圖形", "bold graphic-shape backdrop"),
            _option("floral", "花卉背景", "ornamental floral backdrop"),
            _option("magic_circle", "魔法陣", "glowing magic-circle backdrop"),
            _option("smoke", "煙霧背景", "layered atmospheric smoke backdrop"),
            _option("stained_glass", "彩繪玻璃", "stained-glass backdrop"),
            _option("moon_disc", "巨大月輪", "large moon-disc backdrop"),
            _option("paper_texture", "紙張肌理", "textured paper backdrop"),
            _option("floating_runes", "漂浮符文", "floating-rune backdrop"),
            _option("archive_shelves", "古老檔案架", "soft-focus ancient archive shelves"),
            _option("observatory_dome", "觀星穹頂", "celestial observatory dome backdrop"),
            _option("rain_window", "雨夜窗景", "rain-streaked window backdrop"),
            _option("ruined_arch", "殘破拱門", "weathered ruined arch backdrop"),
            _option("floating_isles", "漂浮群島", "distant floating-island backdrop"),
            _option("underwater_light", "水下光紋", "underwater caustic-light backdrop"),
            _option("solid_color_studio", "純色背景", "solid-color studio backdrop"),
            _option("soft_bokeh", "柔焦光斑", "soft bokeh-light backdrop"),
            _option("radiant_sunburst", "放射光芒", "radiant sunburst backdrop"),
            _option("abstract_ink_backdrop", "抽象墨暈", "abstract ink-wash backdrop"),
            _option("geometric_grid", "幾何網格", "clean geometric-grid backdrop"),
            _option("drifting_stardust", "星塵背景", "drifting stardust backdrop"),
            _option("stage_curtain_backdrop", "舞台布幕", "theatrical curtain backdrop"),
            _option("fractured_mirror", "破碎鏡面", "fractured-mirror backdrop"),
            _option("technical_blueprint", "技術藍圖背景", "technical blueprint backdrop"),
            _option("parchment_collage", "羊皮紙拼貼", "layered torn-parchment collage backdrop"),
            _option("neon_city_blur", "霓虹城市柔焦", "soft-focus neon cityscape backdrop"),
            _option("sunlit_leaf_shadows", "日照葉影背景", "sunlit leaf-shadow backdrop"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("simple",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="character_detail",
        label_zh="細節密度",
        group="畫面與風格",
        options=(
            _option("clean", "乾淨簡潔", "clean readable design"),
            _option("detailed", "高細節", "highly detailed character design"),
            _option("intricate", "極繁複", "intricate character design"),
            _option("minimal", "極簡", "minimal character detail"),
            _option("production_ready", "製作用設定細節", "production-ready character design detail"),
            _option("micro_detail", "微觀材質細節", "fine micro-texture detail"),
            _option("ornate", "裝飾性高細節", "ornate decorative detail"),
            _option("clean_shapes", "清晰造型", "clean readable character shapes"),
            _option("material_focus", "材質重點", "material-focused character detail"),
            _option("couture_detail", "高訂服飾細節", "couture-level costume detail"),
            _option("cinematic_detail", "電影級細節", "cinematic character detail"),
            _option("textile_microdetail", "織物微細節", "precise textile microdetail"),
            _option("facial_microdetail", "臉部微細節", "refined adult facial microdetail"),
            _option("prop_storytelling", "道具敘事細節", "story-rich prop detailing"),
            _option("silhouette_clarity", "輪廓清晰度", "exceptionally clear silhouette design"),
            _option("surface_patina", "表面歲月質感", "carefully rendered surface patina"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("detailed",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="beast_humanoid_species",
        label_zh="獸人／半獸人（人型獸徵）",
        group="獸人／半獸人",
        options=(
            _option("dog", "犬族", _beast_lineage_prompt("dog")),
            _option("shiba_inu", "柴犬", _beast_lineage_prompt("shiba inu")),
            _option("husky", "哈士奇", _beast_lineage_prompt("husky")),
            _option("german_shepherd", "德國牧羊犬", _beast_lineage_prompt("german shepherd")),
            _option("doberman", "杜賓犬", _beast_lineage_prompt("doberman")),
            _option("corgi", "柯基犬", _beast_lineage_prompt("corgi")),
            _option("domestic_cat", "貓咪", _beast_lineage_prompt("cat")),
            _option("lion", "獅族", _beast_lineage_prompt("lion")),
            _option("tiger", "虎族", _beast_lineage_prompt("tiger")),
            _option("leopard", "豹族", _beast_lineage_prompt("leopard")),
            _option("snow_leopard", "雪豹", _beast_lineage_prompt("snow leopard")),
            _option("lynx", "猞猁", _beast_lineage_prompt("lynx")),
            _option("black_panther", "黑豹", _beast_lineage_prompt("black panther")),
            _option("serval", "藪貓", _beast_lineage_prompt("serval")),
            _option("red_fox", "赤狐", _beast_lineage_prompt("red fox")),
            _option("arctic_fox", "北極狐", _beast_lineage_prompt("arctic fox")),
            _option("fennec_fox", "耳廓狐", _beast_lineage_prompt("fennec fox")),
            _option("gray_wolf", "灰狼", _beast_lineage_prompt("gray wolf")),
            _option("arctic_wolf", "北極狼", _beast_lineage_prompt("arctic wolf")),
            _option("rabbit", "兔族", _beast_lineage_prompt("rabbit")),
            _option("hare", "野兔", _beast_lineage_prompt("hare")),
            _option("arctic_hare", "北極兔", _beast_lineage_prompt("arctic hare")),
            _option("brown_bear", "棕熊", _beast_lineage_prompt("brown bear")),
            _option("polar_bear", "北極熊", _beast_lineage_prompt("polar bear")),
            _option("panda", "熊貓", _beast_lineage_prompt("panda")),
            _option("red_deer", "赤鹿", _beast_lineage_prompt("red deer")),
            _option("reindeer", "馴鹿", _beast_lineage_prompt("reindeer")),
            _option("moose", "駝鹿", _beast_lineage_prompt("moose")),
            _option("goat", "山羊", _beast_lineage_prompt("goat")),
            _option("sheep", "綿羊", _beast_lineage_prompt("sheep")),
            _option("ram", "公羊", _beast_lineage_prompt("ram")),
            _option("cow", "乳牛", _beast_lineage_prompt("cow")),
            _option("bull", "公牛", _beast_lineage_prompt("bull")),
            _option("buffalo", "水牛", _beast_lineage_prompt("buffalo")),
            _option("horse", "馬族", _beast_lineage_prompt("horse")),
            _option("zebra", "斑馬", _beast_lineage_prompt("zebra")),
            _option("mouse", "小鼠", _beast_lineage_prompt("mouse")),
            _option("rat", "大鼠", _beast_lineage_prompt("rat")),
            _option("hamster", "倉鼠", _beast_lineage_prompt("hamster")),
            _option("squirrel", "松鼠", _beast_lineage_prompt("squirrel")),
            _option("eagle", "鷹族", _beast_lineage_prompt("eagle")),
            _option("golden_eagle", "金雕", _beast_lineage_prompt("golden eagle")),
            _option("owl", "貓頭鷹", _beast_lineage_prompt("owl")),
            _option("raven", "渡鴉", _beast_lineage_prompt("raven")),
            _option("parrot", "鸚鵡", _beast_lineage_prompt("parrot")),
            _option("swan", "天鵝", _beast_lineage_prompt("swan")),
            _option("lizard", "蜥蜴", _beast_lineage_prompt("lizard")),
            _option("gecko", "守宮", _beast_lineage_prompt("gecko")),
            _option("snake", "蛇族", _beast_lineage_prompt("snake")),
            _option("crocodile", "鱷魚", _beast_lineage_prompt("crocodile")),
            _option("turtle", "龜族", _beast_lineage_prompt("turtle")),
            _option("shark", "鯊族", _beast_lineage_prompt("shark")),
            _option("dolphin", "海豚", _beast_lineage_prompt("dolphin")),
            _option("orca", "虎鯨", _beast_lineage_prompt("orca")),
            _option("koi", "錦鯉", _beast_lineage_prompt("koi")),
            _option("axolotl", "六角恐龍", _beast_lineage_prompt("axolotl")),
            _option("butterfly", "蝴蝶", _beast_lineage_prompt("butterfly")),
            _option("moth", "飛蛾", _beast_lineage_prompt("moth")),
            _option("bee", "蜜蜂", _beast_lineage_prompt("bee")),
            _option("beetle", "甲蟲", _beast_lineage_prompt("beetle")),
            _option("dragonfly", "蜻蜓", _beast_lineage_prompt("dragonfly")),
            _option("mantis", "螳螂", _beast_lineage_prompt("mantis")),
            _option("jackal", "豺", _beast_lineage_prompt("jackal")),
            _option("hyena", "鬣狗", _beast_lineage_prompt("hyena")),
            _option("cheetah", "獵豹", _beast_lineage_prompt("cheetah")),
            _option("cougar", "美洲獅", _beast_lineage_prompt("cougar")),
            _option("red_panda", "小熊貓", _beast_lineage_prompt("red panda")),
            _option("elk", "麋鹿", _beast_lineage_prompt("elk")),
            _option("donkey", "驢", _beast_lineage_prompt("donkey")),
            _option("hedgehog", "刺蝟", _beast_lineage_prompt("hedgehog")),
            _option("kangaroo", "袋鼠", _beast_lineage_prompt("kangaroo")),
            _option("sloth", "樹懶", _beast_lineage_prompt("sloth")),
            _option("gorilla", "大猩猩", _beast_lineage_prompt("gorilla")),
            _option("monkey", "猴族", _beast_lineage_prompt("monkey")),
            _option("capybara", "水豚", _beast_lineage_prompt("capybara")),
            _option("beaver", "河狸", _beast_lineage_prompt("beaver")),
            _option("badger", "獾", _beast_lineage_prompt("badger")),
            _option("ferret", "雪貂", _beast_lineage_prompt("ferret")),
            _option("meerkat", "狐獴", _beast_lineage_prompt("meerkat")),
            _option("wolverine", "狼獾", _beast_lineage_prompt("wolverine")),
            _option("koala", "無尾熊", _beast_lineage_prompt("koala")),
            _option("wombat", "袋熊", _beast_lineage_prompt("wombat")),
            _option("opossum", "負鼠", _beast_lineage_prompt("opossum")),
            _option("armadillo", "犰狳", _beast_lineage_prompt("armadillo")),
            _option("wild_boar", "野豬", _beast_lineage_prompt("wild boar")),
            _option("pig", "家豬", _beast_lineage_prompt("pig")),
            _option("camel", "駱駝", _beast_lineage_prompt("camel")),
            _option("alpaca", "羊駝", _beast_lineage_prompt("alpaca")),
            _option("llama", "美洲駝", _beast_lineage_prompt("llama")),
            _option("yak", "牦牛", _beast_lineage_prompt("yak")),
            _option("bison", "野牛", _beast_lineage_prompt("bison")),
            _option("antelope", "羚羊", _beast_lineage_prompt("antelope")),
            _option("gazelle", "瞪羚", _beast_lineage_prompt("gazelle")),
            _option("ibex", "北山羊", _beast_lineage_prompt("ibex")),
            _option("chinchilla", "龍貓", _beast_lineage_prompt("chinchilla")),
            _option("guinea_pig", "天竺鼠", _beast_lineage_prompt("guinea pig")),
            _option("lemur", "狐猴", _beast_lineage_prompt("lemur")),
            _option("elephant", "大象", _beast_lineage_prompt("elephant")),
            _option("rhinoceros", "犀牛", _beast_lineage_prompt("rhinoceros")),
            _option("hippopotamus", "河馬", _beast_lineage_prompt("hippopotamus")),
            _option("seal", "海豹", _beast_lineage_prompt("seal")),
            _option("sea_lion", "海獅", _beast_lineage_prompt("sea lion")),
            _option("manatee", "海牛", _beast_lineage_prompt("manatee")),
            _option("other", "其他／自訂", _beast_lineage_prompt("custom")),
        ),
        selection_mode="multi",
        help_text="固定成年、雙足與人型臉身比例；可在下方自由調整融合程度、臉鼻、體表、花紋與手腳。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_morphology_balance",
        label_zh="半獸人融合比例",
        group="獸人／半獸人細節",
        options=(
            _option(
                "near_human",
                "近乎人類",
                "near-human bipedal anatomy with minimal species influence",
            ),
            _option(
                "human_dominant",
                "人型為主",
                "human-dominant bipedal proportions with restrained half-beast traits",
            ),
            _option(
                "subtle_integrated",
                "輕度自然融合",
                "subtly integrated species traits on an unmistakably human-shaped bipedal body",
            ),
            _option(
                "balanced",
                "均衡半獸融合",
                "well-balanced human and species traits on a human-shaped bipedal body",
            ),
            _option(
                "species_forward",
                "獸徵較明顯",
                "pronounced species traits while preserving a human-shaped bipedal body",
            ),
            _option(
                "strong_integrated",
                "高度自然融合",
                "strongly integrated species traits with coherent human proportions and silhouette",
            ),
            _option(
                "seamless_hybrid",
                "自然無縫融合",
                "seamlessly integrated species traits with coherent human body proportions",
            ),
            _option(
                "beast_human_frame_species_details",
                "人型骨架配明確獸徵",
                "clearly readable species details arranged on an entirely human-proportioned "
                "bipedal frame",
            ),
        ),
        selection_mode="multi",
        help_text="只調整人型與獸徵的比例；不會變成四足動物或完整福瑞。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_face_blend",
        label_zh="半獸人臉部／鼻口影響",
        group="獸人／半獸人細節",
        options=(
            _option(
                "human_face",
                "完整人類臉型",
                "fully human facial proportions with only faint species influence",
            ),
            _option(
                "subtle_species_nose",
                "輕微種族鼻型",
                "predominantly human face with a small species-appropriate nose",
            ),
            _option(
                "animal_nose_human_mouth",
                "獸鼻搭配人類嘴型",
                "human facial proportions with a species nose and a human mouth",
            ),
            _option(
                "short_hybrid_muzzle",
                "短而克制的混合吻部",
                "recognizably human face with a short restrained species-influenced muzzle",
            ),
            _option(
                "balanced_hybrid",
                "均衡獸鼻與短吻部",
                "well-balanced human face with a short species-influenced nose bridge and muzzle",
            ),
            _option(
                "pronounced_hybrid",
                "明顯獸鼻但保留人臉",
                "clearly species-influenced nose and short muzzle while retaining a human face",
            ),
            _option(
                "avian_bridge",
                "輕微鳥系鼻樑",
                "human face with a subtle avian nose-bridge profile and no full beak",
            ),
            _option(
                "reptilian_bridge",
                "輕微爬蟲鼻吻",
                "human face with a subtle reptilian nose bridge and restrained snout influence",
            ),
            _option(
                "aquatic_bridge",
                "輕微水生鼻型",
                "human face with a smooth species-appropriate aquatic nose profile",
            ),
            _option(
                "insect_face_accents",
                "輕微昆蟲顏面特徵",
                "human face with restrained symmetrical insectoid facial accents",
            ),
            _option(
                "beast_restrained_species_face_contours",
                "克制種族臉部輪廓",
                "predominantly human face with restrained species-specific contour accents and "
                "no full animal head",
            ),
            _option(
                "beast_human_expression_species_features",
                "人類表情配自然獸徵",
                "fully readable human expression with naturally integrated species-specific "
                "facial details and no full animal head",
            ),
        ),
        selection_mode="multi",
        help_text="所有選項都保留可辨識的人類臉部比例；特殊鼻型只會配給相容物種。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_skin_covering",
        label_zh="半獸人體表／局部毛皮",
        group="獸人／半獸人細節",
        options=(
            _option("human_skin", "人類皮膚", "predominantly smooth human skin"),
            _option(
                "species_colored_skin",
                "種族色的人型皮膚",
                "smooth human-shaped skin with species-derived coloration",
            ),
            _option(
                "smooth_patterned_skin",
                "可呈現花紋的光滑皮膚",
                "smooth human-shaped skin prepared for coherent species markings",
            ),
            _option(
                "short_fur_accents",
                "局部短毛",
                "localized short fur accents over otherwise human-shaped skin",
            ),
            _option(
                "velvet_fur_layer",
                "極短絨毛皮膚",
                "very short velvety fur following human body contours",
            ),
            _option(
                "facial_fur_accents",
                "臉頰局部獸毛",
                "restrained facial fur accents preserving human facial structure",
            ),
            _option(
                "limb_fur_accents",
                "四肢局部獸毛",
                "localized fur along human-shaped forearms and lower legs",
            ),
            _option(
                "mixed_skin_fur",
                "皮膚與獸毛自然分區",
                "harmonious zones of smooth skin and short fur on human-shaped anatomy",
            ),
            _option(
                "feather_accents",
                "局部羽毛覆層",
                "localized feather accents over a human-shaped body",
            ),
            _option(
                "scale_accents",
                "局部鱗片覆層",
                "localized species scales over otherwise smooth human-shaped skin",
            ),
            _option("aquatic_skin", "水生光滑皮膚", "smooth aquatic skin following human body contours"),
            _option(
                "chitin_accents",
                "局部幾丁質甲片",
                "restrained chitin accents over a human-shaped body",
            ),
        ),
        selection_mode="multi",
        help_text="可選完全人類皮膚、種族色皮膚或局部獸毛／羽毛／鱗片；不會自動變成全身福瑞。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_marking_pattern",
        label_zh="半獸人種族花紋",
        group="獸人／半獸人細節",
        options=(
            _option("solid", "無明顯花紋", "clean solid species coloration"),
            _option("countershading", "背深腹淺", "subtle species countershading"),
            _option("facial_mask", "臉部面罩紋", "balanced species facial-mask markings"),
            _option("eye_stripes", "眼側條紋", "symmetrical species stripes beside the eyes"),
            _option("cheek_stripes", "臉頰條紋", "balanced species stripes across the cheeks"),
            _option("limb_bands", "四肢環帶紋", "coherent species band markings around the limbs"),
            _option("dorsal_line", "背部中線紋", "single species-colored dorsal line"),
            _option("shoulder_stripes", "肩部條紋", "balanced species stripes over the shoulders"),
            _option("flank_stripes", "腰側條紋", "harmonious species stripes along the flanks"),
            _option("tiger_stripes", "虎紋", "balanced tiger stripe markings on human-shaped skin"),
            _option("zebra_stripes", "斑馬紋", "balanced zebra stripe markings on human-shaped skin"),
            _option("tabby", "虎斑紋", "restrained tabby markings on human-shaped skin"),
            _option("spots", "斑點", "clean species spot markings on human-shaped skin"),
            _option("rosettes", "玫瑰斑", "balanced leopard rosettes on human-shaped skin"),
            _option("piebald", "花斑", "harmonious piebald patches on human-shaped skin"),
            _option("brindle", "虎斑混色", "restrained brindle markings on human-shaped skin"),
            _option(
                "point_coloration",
                "重點色",
                "species point coloration on the face and extremities",
            ),
            _option("saddle_mark", "鞍狀背斑", "balanced saddle-shaped species marking"),
            _option("gradient", "自然漸層體色", "smooth species-color gradient across human-shaped skin"),
            _option(
                "reptile_mottling",
                "爬蟲斑駁紋",
                "restrained reptilian mottling on human-shaped skin",
            ),
            _option("diamond_bands", "菱形鱗紋", "symmetrical diamond scale markings"),
            _option("orca_patches", "虎鯨黑白斑", "balanced orca-inspired black-and-white skin patches"),
            _option("koi_patches", "錦鯉色塊", "harmonious koi-inspired skin patches"),
            _option("insect_bands", "昆蟲環帶紋", "symmetrical insect-inspired body bands"),
            _option("bioluminescent", "生物光紋", "controlled bioluminescent species markings"),
            _option(
                "beast_freckled_speckles",
                "半獸細點紋",
                "fine species-colored speckles across selected half-beast features",
            ),
            _option(
                "beast_ringed_accents",
                "半獸環圈紋",
                "clean ring-shaped markings on selected half-beast features",
            ),
            _option(
                "beast_marbled_pattern",
                "半獸雲石紋",
                "organic marbled markings across selected half-beast features",
            ),
            _option(
                "beast_chevron_bands",
                "半獸折線帶紋",
                "clean chevron bands across selected half-beast features",
            ),
            _option(
                "beast_constellation_spots",
                "半獸星座點紋",
                "constellation-like spots across selected half-beast features",
            ),
            _option(
                "beast_mosaic_patches",
                "半獸鑲嵌斑塊",
                "mosaic-like color patches across selected half-beast features",
            ),
            _option(
                "beast_faded_edge_markings",
                "半獸邊緣羽化紋",
                "soft markings fading along the edges of selected half-beast features",
            ),
        ),
        selection_mode="multi",
        help_text="花紋會遵守物種相容性並保留人型臉身比例。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_marking_coverage",
        label_zh="半獸人花紋覆蓋位置",
        group="獸人／半獸人細節",
        options=(
            _option("face_only", "僅臉部", "species markings limited to the human-shaped face"),
            _option("limbs_only", "僅四肢", "species markings limited to human-shaped arms and legs"),
            _option(
                "face_and_limbs",
                "臉部與四肢",
                "balanced species markings across the human-shaped face and limbs",
            ),
            _option(
                "shoulders_back",
                "肩膀與背部",
                "species markings concentrated over human-shaped shoulders and back",
            ),
            _option(
                "torso_accents",
                "軀幹局部",
                "restrained species markings accenting the human-shaped torso",
            ),
            _option(
                "extremity_gradient",
                "末端漸增",
                "species markings gradually increasing toward human-shaped extremities",
            ),
            _option(
                "balanced_full_body",
                "全身均衡分布",
                "well-proportioned species markings distributed across a human-shaped body",
            ),
            _option(
                "asymmetrical_accent",
                "單側不對稱重點",
                "intentional asymmetrical species markings on a human-shaped body",
            ),
        ),
        selection_mode="multi",
        help_text="花紋種類與覆蓋位置分開選，方便做出參考圖般的臉部、肩背與四肢比例。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_ear_style",
        label_zh="半獸人耳部型態",
        group="獸人／半獸人細節",
        options=(
            _option(
                "species_default",
                "物種預設耳型",
                "one anatomically consistent species ear configuration replacing human ears "
                "with no duplicate ears",
            ),
            _option(
                "small",
                "小型獸耳",
                "small animal ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "upright",
                "直立獸耳",
                "upright animal ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "drooping",
                "垂耳",
                "drooping animal ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "rounded",
                "圓耳",
                "rounded animal ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "tufted",
                "簇毛獸耳",
                "tufted animal ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "side_set",
                "側向獸耳",
                "side-set species ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "long",
                "修長獸耳",
                "long species ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "feather_tufts",
                "羽簇耳部",
                "feathered ear tufts replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "finlike",
                "鰭狀耳部",
                "finlike ear structures replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "no_external",
                "無外露耳廓",
                "species-appropriate ear openings with no visible human ears or duplicate ears",
            ),
            _option(
                "beast_streamlined_hearing_profile",
                "貼合頭型種族耳部",
                "streamlined species-appropriate hearing structures following the head "
                "silhouette and replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "folded_tip",
                "折尖獸耳",
                "folded-tip species ears replacing human ears with no human ears or duplicate ears",
            ),
            _option(
                "forward_cupped",
                "前傾聚音獸耳",
                "forward-cupped species ears replacing human ears with no human ears or "
                "duplicate ears",
            ),
            _option(
                "broad_fan",
                "扇形寬耳",
                "broad fan-shaped species ears replacing human ears with no human ears or "
                "duplicate ears",
            ),
            _option(
                "notched_edge",
                "缺口獸耳",
                "naturally notched species ears replacing human ears with no human ears or "
                "duplicate ears",
            ),
        ),
        selection_mode="multi",
        help_text="獸耳會取代人耳；爬蟲、昆蟲與部分水生物種改用無外露耳廓。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_tail_style",
        label_zh="半獸人尾部型態",
        group="獸人／半獸人細節",
        options=(
            _option("none", "無尾", "no visible tail on the human-shaped half-beast body"),
            _option(
                "species_default",
                "物種預設尾型",
                "one anatomically coherent species-appropriate tail on the half-beast body",
            ),
            _option("short", "短尾", "one short species-appropriate half-beast tail"),
            _option("curled", "捲尾", "one curled canine-style half-beast tail"),
            _option("fluffy", "蓬鬆長尾", "one long fluffy half-beast tail"),
            _option("fox_brush", "狐系刷尾", "one full fox-brush half-beast tail"),
            _option("feline", "貓科長尾", "one flexible feline half-beast tail"),
            _option("canine", "犬科尾", "one expressive canine half-beast tail"),
            _option("lupine", "狼系粗尾", "one thick lupine half-beast tail"),
            _option("lapine", "兔球尾", "one round lapine half-beast tail"),
            _option("ursine", "熊系短尾", "one small ursine half-beast tail"),
            _option("cervine", "鹿系短尾", "one short cervine half-beast tail"),
            _option("bovine", "牛系長尾", "one slender bovine half-beast tail"),
            _option("equine", "馬科流尾", "one flowing equine half-beast tail"),
            _option("rodent", "齧齒類長尾", "one slender rodent half-beast tail"),
            _option("squirrel", "松鼠蓬尾", "one large fluffy squirrel half-beast tail"),
            _option("avian_plume", "鳥類尾羽", "one coherent fan of avian half-beast tail feathers"),
            _option("reptilian", "爬蟲長尾", "one tapered reptilian half-beast tail"),
            _option("aquatic", "水生尾", "one streamlined aquatic half-beast tail"),
            _option("insect_abdomen", "昆蟲腹尾", "one streamlined insectoid half-beast abdomen tail"),
            _option("prehensile", "半獸可捲握尾", "one dexterous prehensile half-beast tail"),
            _option("paddle", "半獸槳狀寬尾", "one broad paddle-shaped half-beast tail"),
            _option("segmented_armor", "半獸節甲尾", "one segmented armor-plated half-beast tail"),
            _option("balancing", "半獸平衡長尾", "one long counterbalancing half-beast tail"),
        ),
        selection_mode="multi",
        help_text="物種只決定血統，尾巴在此獨立選擇；無尾永遠可用，其餘尾型依物種限制。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_hand_style",
        label_zh="半獸人手部型態",
        group="獸人／半獸人細節",
        options=(
            _option("human_hands", "完整人型手", "fully human five-fingered hands with human palms"),
            _option(
                "human_paw_pads",
                "人型手搭配肉球",
                "humanlike five-fingered hands with soft species-appropriate paw pads",
            ),
            _option(
                "articulated_paw_hands",
                "可持物的獸掌手",
                "articulated paw-shaped hands with five tool-using humanoid fingers and paw pads",
            ),
            _option(
                "short_claws",
                "人型手搭配短爪",
                "humanlike hands with short controlled species claws",
            ),
            _option(
                "retractable_claws",
                "人型手搭配伸縮爪",
                "humanlike hands with controlled retractable feline claws",
            ),
            _option(
                "hooflike_nails",
                "人型手搭配蹄質指甲",
                "humanlike hands with refined species-appropriate hooflike nails",
            ),
            _option(
                "feathered_hands",
                "人型手搭配羽毛",
                "humanlike hands with restrained feather accents across the hands and forearms",
            ),
            _option(
                "taloned_hands",
                "可持物的鳥爪手",
                "tool-using humanlike hands with controlled avian talons",
            ),
            _option(
                "scaled_hands",
                "人型鱗手",
                "humanlike five-fingered hands with localized species scales",
            ),
            _option(
                "scaled_claws",
                "人型鱗手搭配短爪",
                "humanlike scaled hands with short controlled claws",
            ),
            _option("webbed_hands", "人型蹼手", "humanlike five-fingered hands with subtle webbing"),
            _option(
                "chitin_hands",
                "人型甲殼手",
                "humanlike tool-using hands with restrained chitin plates",
            ),
            _option(
                "insect_claws",
                "可持物的昆蟲爪手",
                "articulated tool-using humanoid hands with delicate insect claws",
            ),
            _option("species_hands", "物種原生手部", "species-accurate humanoid tool-using hands"),
            _option(
                "beast_refined_species_nails",
                "種族質感人型指甲",
                "humanlike five-fingered hands with refined species-colored nails and full "
                "tool-using dexterity",
            ),
            _option(
                "beast_textured_human_palms",
                "種族紋理人型手掌",
                "humanlike five-fingered hands with subtle species-appropriate palm texture and"
                " full tool-using dexterity",
            ),
        ),
        selection_mode="multi",
        help_text="完整人型手永遠可選；獸掌、肉球、爪、蹼或甲殼手只會出現在相容物種。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="beast_foot_style",
        label_zh="半獸人腳部型態",
        group="獸人／半獸人細節",
        options=(
            _option("human_feet", "完整人型腳", "fully human plantigrade feet"),
            _option(
                "paw_pad_feet",
                "人型腳底搭配肉球",
                "human-shaped plantigrade feet with soft species-appropriate paw pads",
            ),
            _option(
                "plantigrade_paws",
                "蹠行獸掌腳",
                "bipedal plantigrade paw feet with coherent humanoid proportions",
            ),
            _option(
                "digitigrade_paws",
                "趾行獸掌腳",
                "bipedal digitigrade paw feet with balanced human body proportions",
            ),
            _option(
                "clawed_human_feet",
                "人型腳搭配短爪",
                "human-shaped feet with short controlled species claws",
            ),
            _option(
                "split_hooves",
                "雙趾蹄足",
                "bipedal split hooves with coherent human leg proportions",
            ),
            _option(
                "equine_hooves",
                "馬科單蹄足",
                "bipedal equine hooves with coherent human leg proportions",
            ),
            _option(
                "taloned_feet",
                "鳥爪足",
                "bipedal avian taloned feet with coherent human leg proportions",
            ),
            _option("webbed_feet", "人型蹼足", "human-shaped bipedal feet with subtle aquatic webbing"),
            _option(
                "scaled_clawed_feet",
                "鱗片爪足",
                "bipedal scaled feet with short controlled claws",
            ),
            _option(
                "gecko_toe_pads",
                "守宮吸附趾墊",
                "human-shaped bipedal feet with species-inspired adhesive toe pads",
            ),
            _option(
                "chitin_feet",
                "人型甲殼足",
                "human-shaped bipedal feet with restrained chitin plates",
            ),
            _option(
                "insect_tarsi",
                "昆蟲附節足",
                "bipedal insect-inspired tarsal feet with coherent human leg proportions",
            ),
            _option(
                "species_feet",
                "物種原生腳部",
                "species-accurate bipedal feet with coherent human body proportions",
            ),
            _option(
                "beast_refined_species_toes",
                "種族質感人型腳趾",
                "human-shaped bipedal feet with subtle species-appropriate toe details and "
                "stable human leg proportions",
            ),
            _option(
                "beast_textured_human_soles",
                "種族紋理人型腳底",
                "human-shaped bipedal feet with subtle species-appropriate sole texture and "
                "stable human leg proportions",
            ),
        ),
        selection_mode="multi",
        help_text="完整人型腳永遠可選；肉球、趾行、蹄、鳥爪、蹼與甲殼足會依物種限制。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_species",
        label_zh="福瑞物種（完整擬人）",
        group="福瑞",
        options=(
            _option("dog", "犬族", _furry_lineage_prompt("canine")),
            _option("shiba_inu", "柴犬", _furry_lineage_prompt("Shiba Inu")),
            _option("husky", "哈士奇", _furry_lineage_prompt("husky")),
            _option("german_shepherd", "德國牧羊犬", _furry_lineage_prompt("German shepherd")),
            _option("doberman", "杜賓犬", _furry_lineage_prompt("Doberman")),
            _option("corgi", "柯基犬", _furry_lineage_prompt("corgi")),
            _option("red_fox", "赤狐", _furry_lineage_prompt("red fox")),
            _option("arctic_fox", "北極狐", _furry_lineage_prompt("arctic fox")),
            _option("fennec_fox", "耳廓狐", _furry_lineage_prompt("fennec fox")),
            _option("gray_wolf", "灰狼", _furry_lineage_prompt("gray wolf")),
            _option("arctic_wolf", "北極狼", _furry_lineage_prompt("arctic wolf")),
            _option("coyote", "郊狼", _furry_lineage_prompt("coyote")),
            _option("domestic_cat", "貓咪", _furry_lineage_prompt("cat")),
            _option("lion", "獅族", _furry_lineage_prompt("lion")),
            _option("tiger", "虎族", _furry_lineage_prompt("tiger")),
            _option("leopard", "豹族", _furry_lineage_prompt("leopard")),
            _option("snow_leopard", "雪豹", _furry_lineage_prompt("snow leopard")),
            _option("lynx", "猞猁", _furry_lineage_prompt("lynx")),
            _option("rabbit", "兔族", _furry_lineage_prompt("rabbit")),
            _option("brown_bear", "棕熊", _furry_lineage_prompt("brown bear")),
            _option("polar_bear", "北極熊", _furry_lineage_prompt("polar bear")),
            _option("panda", "熊貓", _furry_lineage_prompt("giant panda")),
            _option("red_deer", "赤鹿", _furry_lineage_prompt("red deer")),
            _option("reindeer", "馴鹿", _furry_lineage_prompt("reindeer")),
            _option("goat", "山羊", _furry_lineage_prompt("goat")),
            _option("sheep", "綿羊", _furry_lineage_prompt("sheep")),
            _option("cow", "牛族", _furry_lineage_prompt("bovine")),
            _option("horse", "馬族", _furry_lineage_prompt("horse")),
            _option("zebra", "斑馬", _furry_lineage_prompt("zebra")),
            _option("mouse", "小鼠", _furry_lineage_prompt("mouse")),
            _option("rat", "大鼠", _furry_lineage_prompt("rat")),
            _option("squirrel", "松鼠", _furry_lineage_prompt("squirrel")),
            _option("otter", "水獺", _furry_lineage_prompt("otter")),
            _option("raccoon", "浣熊", _furry_lineage_prompt("raccoon")),
            _option("skunk", "臭鼬", _furry_lineage_prompt("skunk")),
            _option("bat", "蝙蝠", _furry_lineage_prompt("bat")),
            _option("eagle", "鷹族", _furry_lineage_prompt("eagle")),
            _option("owl", "貓頭鷹", _furry_lineage_prompt("owl")),
            _option("raven", "渡鴉", _furry_lineage_prompt("raven")),
            _option("parrot", "鸚鵡", _furry_lineage_prompt("parrot")),
            _option("lizard", "蜥蜴", _furry_lineage_prompt("lizard")),
            _option("gecko", "守宮", _furry_lineage_prompt("gecko")),
            _option("snake", "蛇族", _furry_lineage_prompt("snake")),
            _option("crocodile", "鱷魚", _furry_lineage_prompt("crocodile")),
            _option("shark", "鯊族", _furry_lineage_prompt("shark")),
            _option("dolphin", "海豚", _furry_lineage_prompt("dolphin")),
            _option("orca", "虎鯨", _furry_lineage_prompt("orca")),
            _option("axolotl", "六角恐龍", _furry_lineage_prompt("axolotl")),
            _option("butterfly", "蝴蝶", _furry_lineage_prompt("butterfly")),
            _option("moth", "飛蛾", _furry_lineage_prompt("moth")),
            _option("bee", "蜜蜂", _furry_lineage_prompt("bee")),
            _option("beetle", "甲蟲", _furry_lineage_prompt("beetle")),
            _option("mantis", "螳螂", _furry_lineage_prompt("mantis")),
            _option("dragon", "龍族", _furry_lineage_prompt("fantasy dragon")),
            _option("jackal", "豺", _furry_lineage_prompt("jackal")),
            _option("hyena", "鬣狗", _furry_lineage_prompt("hyena")),
            _option("cheetah", "獵豹", _furry_lineage_prompt("cheetah")),
            _option("cougar", "美洲獅", _furry_lineage_prompt("cougar")),
            _option("red_panda", "小熊貓", _furry_lineage_prompt("red panda")),
            _option("elk", "麋鹿", _furry_lineage_prompt("elk")),
            _option("donkey", "驢", _furry_lineage_prompt("donkey")),
            _option("hedgehog", "刺蝟", _furry_lineage_prompt("hedgehog")),
            _option("kangaroo", "袋鼠", _furry_lineage_prompt("kangaroo")),
            _option("sloth", "樹懶", _furry_lineage_prompt("sloth")),
            _option("gorilla", "大猩猩", _furry_lineage_prompt("gorilla")),
            _option("monkey", "猴族", _furry_lineage_prompt("monkey")),
            _option("capybara", "水豚", _furry_lineage_prompt("capybara")),
            _option("beaver", "河狸", _furry_lineage_prompt("beaver")),
            _option("badger", "獾", _furry_lineage_prompt("badger")),
            _option("ferret", "雪貂", _furry_lineage_prompt("ferret")),
            _option("meerkat", "狐獴", _furry_lineage_prompt("meerkat")),
            _option("wolverine", "狼獾", _furry_lineage_prompt("wolverine")),
            _option("koala", "無尾熊", _furry_lineage_prompt("koala")),
            _option("wombat", "袋熊", _furry_lineage_prompt("wombat")),
            _option("opossum", "負鼠", _furry_lineage_prompt("opossum")),
            _option("armadillo", "犰狳", _furry_lineage_prompt("armadillo")),
            _option("wild_boar", "野豬", _furry_lineage_prompt("wild boar")),
            _option("pig", "家豬", _furry_lineage_prompt("pig")),
            _option("camel", "駱駝", _furry_lineage_prompt("camel")),
            _option("alpaca", "羊駝", _furry_lineage_prompt("alpaca")),
            _option("llama", "美洲駝", _furry_lineage_prompt("llama")),
            _option("yak", "牦牛", _furry_lineage_prompt("yak")),
            _option("bison", "野牛", _furry_lineage_prompt("bison")),
            _option("antelope", "羚羊", _furry_lineage_prompt("antelope")),
            _option("gazelle", "瞪羚", _furry_lineage_prompt("gazelle")),
            _option("ibex", "北山羊", _furry_lineage_prompt("ibex")),
            _option("chinchilla", "龍貓", _furry_lineage_prompt("chinchilla")),
            _option("guinea_pig", "天竺鼠", _furry_lineage_prompt("guinea pig")),
            _option("lemur", "狐猴", _furry_lineage_prompt("lemur")),
            _option("elephant", "大象", _furry_lineage_prompt("elephant")),
            _option("rhinoceros", "犀牛", _furry_lineage_prompt("rhinoceros")),
            _option("hippopotamus", "河馬", _furry_lineage_prompt("hippopotamus")),
            _option("seal", "海豹", _furry_lineage_prompt("seal")),
            _option("sea_lion", "海獅", _furry_lineage_prompt("sea lion")),
            _option("manatee", "海牛", _furry_lineage_prompt("manatee")),
            _option("other", "其他／自訂", _furry_lineage_prompt("custom species")),
        ),
        selection_mode="multi",
        help_text="固定為成年、有智慧、雙足站立且具人型比例的完整擬人角色。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_body_covering",
        label_zh="福瑞體表／毛皮",
        group="福瑞細節",
        options=(
            _option(
                "species_default",
                "物種原生體表",
                "species-accurate coat texture and surface anatomy",
            ),
            _option("short_fur", "短毛", "short well-groomed fur"),
            _option("medium_fur", "中長毛", "medium-length layered fur"),
            _option("long_fur", "長毛", "long flowing fur"),
            _option("plush_fur", "蓬鬆絨毛", "dense plush fur"),
            _option("curly_wool", "捲曲羊毛", "curly wool covering"),
            _option("sleek_fur", "貼身亮毛", "sleek glossy fur"),
            _option("feathers", "羽毛覆體", "layered feather covering"),
            _option("smooth_scales", "光滑鱗片", "smooth overlapping scales"),
            _option("keeled_scales", "稜脊鱗片", "textured keeled scales"),
            _option("aquatic_skin", "水生光滑皮膚", "smooth aquatic skin"),
            _option("chitin", "幾丁質甲殼", "segmented chitin covering"),
            _option("mixed_covering", "混合體表", "harmonious mixed fur and scales"),
            _option(
                "furry_downy_feathers",
                "絨羽覆體",
                "dense downy feather covering layered across the sapient anthropomorphic body",
            ),
            _option(
                "furry_short_flexible_quills",
                "柔性短棘覆體",
                "short flexible quills integrated into the sapient anthropomorphic body covering",
            ),
            _option(
                "furry_shell_plates",
                "層疊甲片覆體",
                "overlapping protective shell plates integrated across the sapient "
                "anthropomorphic body",
            ),
        ),
        selection_mode="multi",
        help_text="需先選擇福瑞物種；隨機生成只會搭配合理體表。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_marking_pattern",
        label_zh="福瑞毛色／體表花紋",
        group="福瑞細節",
        options=(
            _option("solid", "純色", "solid body coloration"),
            _option("countershading", "背深腹淺", "natural countershading"),
            _option("tuxedo", "燕尾服花色", "tuxedo fur pattern"),
            _option("tabby", "虎斑", "tabby fur pattern"),
            _option("tiger_stripes", "虎紋", "bold tiger stripes"),
            _option("zebra_stripes", "斑馬紋", "high-contrast zebra stripes"),
            _option("spots", "斑點", "clean spotted pattern"),
            _option("rosettes", "玫瑰斑", "leopard rosette pattern"),
            _option("piebald", "花斑", "piebald body pattern"),
            _option("brindle", "虎斑混色", "brindle fur pattern"),
            _option("point_coloration", "重點色", "point coloration"),
            _option("facial_mask", "面罩花紋", "contrasting facial mask pattern"),
            _option("saddle_mark", "鞍狀斑", "contrasting saddle marking"),
            _option("gradient", "漸層體色", "smooth gradient body coloration"),
            _option("bioluminescent", "生物光紋", "controlled bioluminescent markings"),
            _option(
                "furry_ringed_distal_pattern",
                "肢尾環紋",
                "coordinated ring markings across the tail and distal limbs",
            ),
            _option(
                "furry_dipped_extremities",
                "末端浸染紋",
                "contrasting dipped-color markings on distal limbs and tail",
            ),
            _option(
                "furry_dorsal_gradient",
                "背腹漸層紋",
                "smooth dorsal-to-ventral color gradient across the anthropomorphic body",
            ),
            _option(
                "furry_constellation_speckles",
                "星座細點紋",
                "constellation-like speckles across the anthropomorphic body covering",
            ),
            _option(
                "furry_iridescent_edges",
                "虹彩邊緣紋",
                "subtle iridescent edge markings along the anthropomorphic body covering",
            ),
        ),
        selection_mode="multi",
        help_text="需先選擇福瑞物種；花紋會依體表類型限制。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_muzzle_shape",
        label_zh="福瑞口鼻／頭部輪廓",
        group="福瑞細節",
        options=(
            _option("species_default", "物種原生口鼻", "species-accurate muzzle and head profile"),
            _option("short_feline", "短貓科口鼻", "short feline muzzle"),
            _option("long_canine", "長犬科口鼻", "long canine muzzle"),
            _option("vulpine", "狐系口鼻", "slender vulpine muzzle"),
            _option("lupine", "狼系口鼻", "strong lupine muzzle"),
            _option("ursine", "熊系口鼻", "broad ursine muzzle"),
            _option("lapine", "兔系鼻吻", "compact lapine muzzle"),
            _option("cervine", "鹿系口鼻", "graceful cervine muzzle"),
            _option("caprine", "羊系口鼻", "structured caprine muzzle"),
            _option("bovine", "牛系口鼻", "broad bovine muzzle"),
            _option("equine", "馬系口鼻", "elongated equine muzzle"),
            _option("rodent", "齧齒系口鼻", "compact rodent muzzle"),
            _option("mustelid", "鼬科口鼻", "rounded mustelid muzzle"),
            _option("avian_beak", "鳥喙", "expressive avian beak"),
            _option("reptilian", "爬蟲口鼻", "structured reptilian snout"),
            _option("aquatic_rostrum", "水生吻部", "streamlined aquatic rostrum"),
            _option("insectoid", "昆蟲顏面甲", "expressive insectoid facial plates"),
            _option("draconic", "龍系口鼻", "refined draconic snout"),
            _option(
                "furry_rounded_anthro_profile",
                "圓潤擬人頭部輪廓",
                "rounded species-appropriate anthropomorphic facial profile with a clearly "
                "sapient expression",
            ),
            _option(
                "furry_angular_anthro_profile",
                "俐落擬人頭部輪廓",
                "angular species-appropriate anthropomorphic facial profile with a clearly "
                "sapient expression",
            ),
        ),
        selection_mode="multi",
        help_text="需先選擇福瑞物種；不會產生普通動物或四足姿態。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_leg_style",
        label_zh="福瑞腿型",
        group="福瑞細節",
        options=(
            _option("plantigrade", "人型蹠行腿", "bipedal plantigrade humanoid legs"),
            _option("digitigrade", "趾行腿", "bipedal digitigrade humanoid legs"),
            _option("unguligrade", "蹄行腿", "bipedal unguligrade humanoid legs"),
            _option("avian", "鳥類腿型", "bipedal avian humanoid legs"),
            _option("reptilian", "爬蟲趾行腿", "bipedal digitigrade reptilian humanoid legs"),
            _option("webbed", "水生蹼足腿型", "bipedal aquatic humanoid legs with webbed feet"),
            _option("insectoid", "昆蟲節肢腿", "bipedal insectoid humanoid legs"),
            _option("draconic", "龍族趾行腿", "bipedal digitigrade draconic humanoid legs"),
        ),
        selection_mode="multi",
        help_text="所有選項均固定雙足與人型比例。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_extremities",
        label_zh="福瑞手足末端",
        group="福瑞細節",
        options=(
            _option(
                "species_default",
                "物種原生手足",
                "species-accurate dexterous hands and bipedal feet",
            ),
            _option("padded_hands", "肉球人型手", "humanlike hands with paw pads"),
            _option("paw_hands", "爪掌手", "articulated paw-hands with humanoid fingers"),
            _option("clawed_hands", "利爪人型手", "humanoid hands with controlled claws"),
            _option("hoof_hands", "蹄足與人型手", "humanoid hands with split-hoof feet"),
            _option("talons", "鳥爪手足", "articulated avian hands and feet with talons"),
            _option("webbed", "蹼狀手足", "humanoid webbed hands and feet"),
            _option("scaled_claws", "鱗爪手足", "humanoid scaled hands and feet with claws"),
            _option("insectoid", "節肢手足", "articulated insectoid humanoid hands and feet"),
            _option("soft_paws", "柔軟獸掌", "soft expressive paw-hands"),
            _option("armored_claws", "甲殼利爪", "armored humanoid clawed hands"),
            _option(
                "furry_adhesive_pads",
                "吸附趾墊手足",
                "dexterous humanoid hands and bipedal feet with species-appropriate adhesive pads",
            ),
            _option(
                "opposable_paw_hands",
                "可對握肉球手",
                "dexterous paw-hands with opposable thumbs and soft pads",
            ),
            _option(
                "feathered_talons",
                "羽覆利爪手足",
                "feathered humanoid hands and feet with controlled talons",
            ),
            _option(
                "fin_webbed_extremities",
                "鰭緣蹼手足",
                "webbed humanoid hands and feet with streamlined fin edges",
            ),
            _option(
                "chitin_pincer_hands",
                "幾丁質螯手",
                "articulated humanoid hands ending in controlled chitin pincers",
            ),
        ),
        selection_mode="multi",
        help_text="所有手足皆可維持工具使用與直立活動。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="furry_tail_style",
        label_zh="福瑞尾型",
        group="福瑞細節",
        options=(
            _option("species_default", "物種原生尾型", "species-accurate tail anatomy"),
            _option("none", "無尾", "no visible tail"),
            _option("short", "短尾", "short expressive tail"),
            _option("curled", "捲尾", "curled expressive tail"),
            _option("fluffy", "蓬鬆長尾", "long fluffy tail"),
            _option("fox_brush", "狐系刷尾", "full fox-brush tail"),
            _option("feline", "貓科長尾", "flexible feline tail"),
            _option("canine", "犬科尾", "expressive canine tail"),
            _option("lupine", "狼系尾", "thick lupine tail"),
            _option("lapine", "兔球尾", "round lapine tail"),
            _option("ursine", "熊系短尾", "small ursine tail"),
            _option("cervine", "鹿系短尾", "short cervine tail"),
            _option("bovine", "牛系長尾", "slender bovine tail"),
            _option("equine", "馬系流尾", "flowing equine tail"),
            _option("rodent", "齧齒長尾", "slender rodent tail"),
            _option("avian_plume", "鳥類尾羽", "layered avian tail plumage"),
            _option("reptilian", "爬蟲長尾", "tapered reptilian tail"),
            _option("aquatic", "水生尾", "streamlined aquatic tail"),
            _option("insect_abdomen", "昆蟲腹尾", "streamlined insectoid abdomen tail"),
            _option(
                "furry_prehensile_tail",
                "可捲握長尾",
                "dexterous prehensile tail with a natural expressive curve",
            ),
            _option("paddle_tail", "福瑞槳狀寬尾", "broad paddle-shaped anthropomorphic tail"),
            _option(
                "segmented_armor_tail",
                "福瑞節甲長尾",
                "segmented armor-plated anthropomorphic tail",
            ),
            _option("aquatic_fluke_tail", "水平水生尾鰭", "broad horizontal aquatic tail fluke"),
            _option("balancing_tail", "福瑞平衡長尾", "long counterbalancing anthropomorphic tail"),
        ),
        selection_mode="multi",
        help_text="需先選擇福瑞物種；尾型會依物種限制。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="character_output_purpose",
        label_zh="角色圖輸出用途",
        group="畫面與風格",
        options=(
            _option(
                "character_illustration",
                "角色立繪",
                "single full-body character illustration with a clean unobtrusive backdrop",
            ),
            _option(
                "orthographic_turnaround",
                "標準三視圖",
                "same adult character in a consistent outfit and proportions shown as full-body"
                " front side and back orthographic views on a clean unlabeled turnaround sheet",
            ),
            _option(
                "illustration_plus_turnaround",
                "角色立繪＋三視圖設定板",
                "same adult character with consistent identity outfit and proportions shown in "
                "one polished full-body illustration beside full-body front side and back "
                "orthographic views on a clean unlabeled design board",
            ),
            _option(
                "complete_character_sheet",
                "完整角色設定表",
                "complete unlabeled character design sheet of the same adult character with "
                "consistent proportions and outfit plus full-body front side and back "
                "orthographic views and expression and detail callouts",
            ),
            _option(
                "expression_sheet",
                "表情設定表",
                "same adult character shown in a consistent head-and-shoulders expression "
                "lineup on a clean unlabeled design sheet",
            ),
            _option(
                "outfit_sheet",
                "服裝變化設定表",
                "same adult character shown in coordinated full-body outfit variants with "
                "consistent identity and proportions on a clean unlabeled design sheet",
            ),
            _option(
                "action_sheet",
                "動作設定表",
                "same adult character shown in multiple full-body action poses with consistent "
                "identity outfit and proportions on a clean unlabeled design sheet",
            ),
            _option(
                "silhouette_sheet",
                "輪廓探索設定表",
                "same adult character shown as clean full-body silhouette explorations with "
                "consistent proportions on an unlabeled design sheet",
            ),
            _option(
                "avatar_portrait",
                "頭像圖示",
                "square avatar portrait of the same adult character",
            ),
            _option(
                "facial_detail_sheet",
                "臉部細節設定表",
                "unlabeled facial-detail reference sheet for the same adult character",
            ),
            _option(
                "hairstyle_variant_sheet",
                "髮型變化設定表",
                "unlabeled hairstyle-variant sheet for the same adult character",
            ),
            _option(
                "accessory_breakdown_sheet",
                "配件拆解設定表",
                "unlabeled accessory breakdown sheet for the same adult character",
            ),
            _option(
                "prop_weapon_sheet",
                "道具與武器設定表",
                "unlabeled prop and weapon reference sheet for the same adult character",
            ),
            _option(
                "color_palette_sheet",
                "色彩配置設定表",
                "unlabeled color-palette reference sheet for the same adult character",
            ),
            _option(
                "outfit_front_back_sheet",
                "服裝正反面設定表",
                "unlabeled front-and-back outfit sheet for the same adult character",
            ),
            _option(
                "animation_model_sheet",
                "動畫模型設定表",
                "animation-ready model sheet for the same adult character",
            ),
        ),
        selection_mode="single",
        help_text="可輸出單張立繪、正側背三視圖或無文字的角色設定表。",
        default_keys=("character_illustration",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="hair_color_pattern",
        label_zh="髮色配置",
        group="髮型與髮色",
        options=(
            _option("solid", "單一純色", "solid hair color placement"),
            _option("highlights", "挑染", "contrasting hair highlights"),
            _option("fine_streaks", "細束挑染", "fine contrasting hair streaks"),
            _option("chunky_streaks", "粗束挑染", "bold chunky hair streaks"),
            _option("two_tone", "雙色分區", "two-tone hair color placement"),
            _option("split_dye", "左右半分染", "split-dye hair color placement"),
            _option("gradient", "漸層染", "smooth gradient hair color placement"),
            _option("ombre", "漸變染", "soft ombre hair color placement"),
            _option("underlayer", "內層染", "contrasting underlayer hair color"),
            _option("inner_color", "耳圈內層染", "hidden inner hair color"),
            _option("dip_dye", "浸染髮尾", "dip-dyed hair ends"),
            _option("colored_tips", "異色髮尾", "contrasting colored hair tips"),
            _option("colored_roots", "異色髮根", "contrasting colored hair roots"),
            _option("money_piece", "臉側重點染", "face-framing money-piece highlights"),
            _option("balayage", "手刷染", "soft balayage hair coloring"),
            _option("color_blocks", "色塊染", "graphic color-blocked hair"),
            _option("rainbow_sections", "彩虹分區", "sectioned rainbow hair coloring"),
            _option("iridescent_sheen", "虹彩光澤", "iridescent hair-color sheen"),
            _option("hair_pattern_halo_dye", "光環染", "halo-dye hair color placement"),
            _option("hair_pattern_marbled", "大理石紋染", "marbled multitone hair coloring"),
            _option("horizontal_bands", "橫向環帶染", "horizontal banded hair-color placement"),
            _option("checkerboard_panels", "棋盤格分區染", "checkerboard-panel hair coloring"),
            _option("constellation_speckles", "星點潑染", "constellation-speckled hair coloring"),
            _option("flame_sections", "火焰分區染", "flame-shaped multicolor hair placement"),
        ),
        selection_mode="multi",
        help_text="可搭配一至三種髮色；需要多色的配置在隨機生成時會自動配對。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="iris_color_pattern",
        label_zh="虹膜配色／異色瞳樣式",
        group="臉部與眼睛",
        options=(
            _option("solid", "單一虹膜色", "solid iris coloring"),
            _option("complete_heterochromia", "雙眼完全異色", "complete heterochromia"),
            _option("sectoral_heterochromia", "扇形異色", "sectoral heterochromia"),
            _option("central_heterochromia", "中央異色環", "central heterochromia"),
            _option("gradient", "虹膜漸層", "gradient iris coloring"),
            _option("limbal_ring", "深色虹膜外環", "defined dark limbal rings"),
            _option("starburst", "放射星芒紋", "radial starburst iris pattern"),
            _option("speckled", "虹膜斑點", "finely speckled irises"),
            _option("concentric", "同心環紋", "concentric iris rings"),
            _option("glowing", "發光虹膜", "glowing irises"),
            _option("rainbow", "彩虹虹膜", "rainbow irises"),
            _option("starry", "星空虹膜", "star-filled irises"),
            _option("mechanical", "機械虹膜", "mechanical irises"),
            _option("faceted", "寶石切面虹膜", "faceted gemstone irises"),
            _option("rune_ring", "符文虹膜環", "rune-inscribed iris rings"),
            _option("void_center", "虛空瞳孔", "void-dark pupil centers"),
        ),
        selection_mode="multi",
        help_text="可配合一或兩種眼睛顏色；異色瞳配置在隨機生成時至少會搭配兩色。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="eyewear",
        label_zh="眼鏡／眼部配件",
        group="臉部與眼睛",
        options=(
            _option("none", "不戴眼鏡", "no eyewear"),
            _option("round", "圓框眼鏡", "round-frame glasses"),
            _option("square", "方框眼鏡", "square-frame glasses"),
            _option("rectangular", "長方框眼鏡", "rectangular-frame glasses"),
            _option("oval", "橢圓框眼鏡", "oval-frame glasses"),
            _option("cat_eye", "貓眼眼鏡", "cat-eye glasses"),
            _option("aviator", "飛行員眼鏡", "aviator glasses"),
            _option("rimless", "無框眼鏡", "rimless glasses"),
            _option("half_rim", "半框眼鏡", "half-rim glasses"),
            _option("wire_frame", "細金屬框", "thin wire-frame glasses"),
            _option("thick_frame", "粗框眼鏡", "thick-frame glasses"),
            _option("reading", "閱讀眼鏡", "reading glasses"),
            _option("sunglasses", "太陽眼鏡", "stylish sunglasses"),
            _option("tinted", "有色鏡片", "tinted glasses"),
            _option("goggles", "護目鏡", "protective goggles"),
            _option("steampunk_goggles", "蒸汽龐克護目鏡", "steampunk goggles"),
            _option("monocle", "單片眼鏡", "ornate monocle"),
            _option("eyepatch", "眼罩", "decorative eyepatch"),
            _option("visor", "科技面罩", "futuristic eye visor"),
            _option("blindfold", "蒙眼布", "decorative blindfold"),
            _option("pince_nez", "夾鼻眼鏡", "ornate pince-nez glasses"),
            _option("hexagonal_frame", "六角框眼鏡", "hexagonal-frame glasses"),
            _option("wraparound_sport", "運動環繞式眼鏡", "wraparound sport glasses"),
            _option("slit_snow_goggles", "狹縫雪地護目鏡", "traditional slit snow goggles"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="eyebrows",
        label_zh="眉型",
        group="臉部與眼睛",
        options=(
            _option("soft", "柔和眉", "soft natural eyebrows"),
            _option("straight", "平直眉", "straight eyebrows"),
            _option("arched", "高挑眉", "highly arched eyebrows"),
            _option("angled", "稜角眉", "sharply angled eyebrows"),
            _option("rounded", "圓弧眉", "rounded eyebrows"),
            _option("thick", "濃眉", "thick eyebrows"),
            _option("thin", "細眉", "thin eyebrows"),
            _option("feathered", "羽毛野生眉", "feathered eyebrows"),
            _option("short", "短眉", "short eyebrows"),
            _option("long", "長眉尾", "long tapered eyebrows"),
            _option("split", "斷眉", "single eyebrow slit"),
            _option("none", "無眉", "no visible eyebrows"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("soft",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="nose_shape",
        label_zh="鼻型",
        group="臉部與眼睛",
        options=(
            _option("small", "小巧鼻", "small delicate nose"),
            _option("straight", "直鼻", "straight nose"),
            _option("button", "圓鈕鼻", "button nose"),
            _option("upturned", "微翹鼻", "slightly upturned nose"),
            _option("aquiline", "鷹鉤鼻", "aquiline nose"),
            _option("roman", "羅馬鼻", "Roman nose"),
            _option("broad", "寬鼻", "broad nose"),
            _option("flat_bridge", "低鼻樑", "low nose bridge"),
            _option("high_bridge", "高鼻樑", "high nose bridge"),
            _option("crooked", "微歪鼻", "slightly crooked nose"),
            _option("nose_narrow", "細窄鼻", "narrow nose"),
            _option("nose_prominent", "高挺立體鼻", "prominent sculpted nose"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("straight",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="lips",
        label_zh="嘴唇／嘴型",
        group="臉部與眼睛",
        options=(
            _option("thin", "薄唇", "thin lips"),
            _option("balanced", "均衡唇形", "balanced lips"),
            _option("full", "豐唇", "full adult lips"),
            _option("heart", "心形唇峰", "heart-shaped cupid's bow"),
            _option("wide", "寬嘴型", "wide mouth"),
            _option("small", "小嘴型", "small mouth"),
            _option("downturned", "嘴角下垂", "downturned lips"),
            _option("upturned", "嘴角上揚", "upturned lips"),
            _option("asymmetrical", "不對稱唇形", "slightly asymmetrical lips"),
            _option("glossy", "水光唇", "glossy lips"),
            _option("matte", "霧面唇", "matte lips"),
            _option("fang_smile", "露牙微笑", "toothy smile"),
            _option("full_lower_lip", "下唇較豐", "full lower lip with a slimmer upper lip"),
            _option("full_upper_lip", "上唇較豐", "full upper lip with a slimmer lower lip"),
            _option("slightly_parted", "微啟唇", "slightly parted lips"),
            _option("firmly_pressed", "緊抿唇", "firmly pressed lips"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("balanced",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="makeup",
        label_zh="妝容細節",
        group="臉部與眼睛",
        options=(
            _option("natural", "自然妝容", "natural makeup"),
            _option("no_makeup", "無妝感", "no visible makeup"),
            _option("winged_eyeliner", "飛揚眼線", "winged eyeliner"),
            _option("smoky_eyes", "煙燻眼妝", "smoky eye makeup"),
            _option("cut_crease", "截斷式眼妝", "cut-crease eye makeup"),
            _option("glitter", "亮片眼妝", "glitter eye makeup"),
            _option("graphic_liner", "圖形眼線", "graphic eyeliner"),
            _option("blush", "明顯腮紅", "visible blush"),
            _option("freckle_makeup", "雀斑妝", "decorative freckle makeup"),
            _option("red_lip", "紅唇", "crimson lipstick"),
            _option("dark_lip", "深色唇彩", "dark lipstick"),
            _option("gradient_lip", "漸層唇", "gradient lip color"),
            _option("metallic_lip", "金屬唇彩", "metallic lipstick"),
            _option("face_gems", "臉部水鑽", "decorative face gems"),
            _option("festival_paint", "節慶彩繪", "festival face paint"),
            _option("gothic", "哥德妝", "gothic makeup"),
            _option("stage", "舞台妝", "dramatic stage makeup"),
            _option("cyber", "賽博妝", "cybernetic makeup accents"),
            _option("fantasy_runes", "符文妝", "painted facial runes"),
            _option("tear_streaks", "淚痕妝", "deliberate tear-streak makeup"),
            _option("pearl_accent_makeup", "珍珠點綴妝", "pearl-accented editorial makeup"),
            _option("color_block_makeup", "色塊藝術妝", "bold color-block editorial makeup"),
            _option("sun_kissed_makeup", "日曬暖調妝", "sun-kissed warm-toned makeup"),
            _option("porcelain_doll_makeup", "瓷偶細緻妝", "delicate porcelain-doll makeup"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=0,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="facial_hair",
        label_zh="鬍鬚造型",
        group="臉部與眼睛",
        options=(
            _option("clean_shaven", "光滑無鬍", "clean-shaven adult male face"),
            _option("light_stubble", "淡鬍渣", "light facial stubble"),
            _option("short_boxed_beard", "方框短鬍", "neatly trimmed short boxed beard"),
            _option("full_beard", "濃密絡腮鬍", "full well-groomed beard"),
            _option("goatee", "山羊鬍", "neatly shaped goatee"),
            _option("chevron_mustache", "八字鬍", "neatly groomed chevron mustache"),
            _option("long_beard", "長鬍", "long flowing beard"),
            _option("facial_hair_handlebar", "翹鬍", "groomed handlebar mustache"),
            _option("facial_hair_circle_beard", "環形鬍", "groomed circle beard"),
            _option("facial_hair_mutton_chops", "絡腮鬢角", "prominent mutton-chop sideburns"),
            _option("facial_hair_soul_patch", "下唇小鬍", "neat soul-patch beard"),
            _option("facial_hair_braided", "編辮鬍", "carefully braided beard"),
            _option("anchor_beard", "錨形鬍", "groomed anchor beard"),
            _option("ducktail_beard", "鴨尾鬍", "tapered ducktail beard"),
            _option("pencil_mustache", "鉛筆小鬍", "fine pencil mustache"),
            _option("horseshoe_mustache", "馬蹄鬍", "bold horseshoe mustache"),
        ),
        selection_mode="multi",
        help_text="成年男性可選填一種鬍鬚造型；不選時不會加入鬍鬚提示詞。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=CharacterGender.MALE,
        selection_max=1,
    ),
    TagCategory(
        key="character_location",
        label_zh="所在地點",
        group="畫面與風格",
        options=(
            _option("on_bed", "床上", "on a neatly made bed"),
            _option("bedroom", "臥室", "inside a cozy bedroom"),
            _option("private_room", "房間", "inside a private furnished room"),
            _option("kitchen", "廚房", "inside a lived-in kitchen"),
            _option("living_room", "客廳", "inside a comfortable living room"),
            _option("bathroom", "浴室", "inside a tiled bathroom"),
            _option("balcony", "陽台", "on a spacious balcony"),
            _option("corridor", "走廊", "in a long interior corridor"),
            _option("classroom", "教室", "inside an adult education classroom"),
            _option("office", "辦公室", "inside a modern office"),
            _option("bar", "酒吧", "inside an atmospheric bar"),
            _option("cafe", "咖啡館", "inside a cozy cafe"),
            _option("library", "圖書館", "inside a quiet library"),
            _option("street", "街道", "on a detailed city street"),
            _option("park", "公園", "in a landscaped public park"),
            _option("beach", "海灘", "on a sunlit beach"),
            _option("forest", "森林", "in a deep forest clearing"),
            _option("arena", "競技場", "inside a monumental arena"),
            _option("rooftop", "屋頂", "on a high rooftop"),
            _option("garden", "花園", "in a lush garden"),
            _option("workshop", "工坊", "inside a cluttered workshop"),
            _option("laboratory", "實驗室", "inside a research laboratory"),
            _option("train_carriage", "火車車廂", "inside a passenger train carriage"),
            _option("shrine", "山中神殿", "at a quiet mountain shrine"),
            _option("tavern", "酒館", "inside a rustic tavern"),
            _option("cave", "洞窟", "inside a vast cave"),
            _option("ruins", "遺跡", "among ancient ruins"),
            _option("spacecraft", "太空船內", "inside a spacecraft"),
            _option("photo_studio", "攝影棚", "inside a professional photo studio"),
            _option("hot_spring", "溫泉", "beside a secluded hot spring"),
            _option("museum_gallery", "藝術博物館展廳", "inside a spacious art-museum gallery"),
            _option("glass_greenhouse", "玻璃植物溫室", "inside a glass-roofed botanical greenhouse"),
            _option("subway_platform", "地鐵月台", "on a modern subway platform"),
            _option("location_observatory", "天文臺", "inside a domed astronomical observatory"),
            _option("location_theater_stage", "劇院舞臺", "on an illuminated theater stage"),
            _option("location_airship_deck", "飛空艇甲板", "on the open deck of a fantasy airship"),
        ),
        selection_mode="multi",
        help_text="可手動複選以表達相連場景；一鍵隨機時最多挑選一個，避免地點互相衝突。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="character_state",
        label_zh="狀態",
        group="狀態",
        options=(
            _option("sweating", "流汗", "visible beads of sweat"),
            _option("clothes_soaked", "衣服濕透", "clothes soaked through with water"),
            _option("pale_face", "臉色蒼白", "visibly pale complexion"),
            _option("pouting", "嘟嘴", "pouting expression"),
            _option("contemptuous_face", "鄙視臉", "contemptuous facial expression"),
            _option("exhausted", "疲憊不堪", "visibly exhausted posture"),
            _option("trembling", "顫抖", "body trembling subtly"),
            _option("rain_soaked", "雨淋", "rain-soaked hair and clothing"),
            _option("muddy", "沾泥", "mud-streaked skin and clothing"),
            _option("disheveled", "凌亂", "disheveled appearance"),
            _option("alert", "警戒", "alert guarded body language"),
            _option("dazed", "恍神", "dazed unfocused demeanor"),
            _option("dusty", "沾滿灰塵", "dust-covered clothing and skin"),
            _option("windblown", "風吹凌亂", "windblown hair and clothing"),
            _option("snow_dusted", "覆著薄雪", "lightly snow-dusted hair and shoulders"),
            _option("ash_streaked", "沾著灰燼", "ash-streaked face and clothing"),
            _option("wet_hair", "頭髮濕潤", "wet hair clinging in loose strands"),
            _option("flushed_skin", "臉頰潮紅", "visibly flushed cheeks"),
            _option("tear_stained", "淚痕", "tear-stained cheeks"),
            _option("sleepy", "睏倦", "sleepy half-focused demeanor"),
            _option("breathless", "氣喘吁吁", "visibly breathless posture"),
            _option("battle_worn", "歷戰狀態", "battle-worn appearance"),
            _option("lightly_injured", "輕傷", "small visible scrapes and bruises"),
            _option("bandaged", "包紮中", "practical visible bandages"),
            _option("cold_shivering", "冷得發抖", "shivering visibly from the cold"),
            _option("overheated", "熱得發昏", "visibly overheated and flushed"),
            _option("magically_charged", "魔力充盈", "magically charged aura"),
            _option("curse_marked", "詛咒痕跡", "visible supernatural curse marks"),
            _option("glowing_skin", "肌膚發光", "subtle luminous skin"),
            _option("smoke_stained", "煙燻痕跡", "smoke-stained face and clothing"),
            _option("travel_worn", "旅途風塵", "travel-worn appearance"),
            _option("tense", "緊繃", "tense restrained body language"),
            _option("well_rested", "精神飽滿", "fresh well-rested appearance"),
            _option("sleep_deprived", "睡眠不足", "visible signs of sleep deprivation"),
            _option("feverish", "發燒", "feverish flushed physical state"),
            _option("dizzy", "暈眩", "visibly dizzy and unsteady"),
            _option("nauseated", "反胃想吐", "visibly nauseated sickly state"),
            _option("coughing", "咳嗽", "caught in a brief coughing fit"),
            _option("sneezing", "打噴嚏", "caught mid-sneeze"),
            _option("yawning", "打哈欠", "caught mid-yawn"),
            _option("waking_up", "剛睡醒", "freshly awakened groggy state"),
            _option("asleep", "熟睡", "peacefully asleep"),
            _option("unconscious", "失去意識", "unconscious unresponsive state"),
            _option("poisoned", "中毒", "visibly poisoned sickly condition"),
            _option("convalescing", "康復期", "visibly convalescing after illness or injury"),
            _option("bruised", "瘀傷", "visible non-graphic bruising across exposed skin"),
            _option("scratched", "抓傷", "fresh superficial scratches across exposed skin"),
            _option("slight_bleeding", "輕微流血", "small non-graphic traces of fresh blood"),
            _option("dehydrated", "脫水", "visibly dehydrated physical condition"),
            _option("sunburned", "曬傷", "fresh sunburn across exposed skin"),
            _option("sore_muscles", "肌肉痠痛", "visible muscle soreness and stiffness"),
            _option("limping", "跛行", "visibly favoring one leg with a slight limp"),
            _option(
                "frost_crusted",
                "身上結霜",
                "thin frost crystals clinging to hair and exposed skin",
            ),
            _option("salt_sprayed", "海鹽沾身", "fine sea-salt residue across hair and skin"),
            _option("sand_coated", "沙塵沾身", "fine sand clinging across skin and gear"),
            _option("petal_strewn", "花瓣沾身", "flower petals caught across hair and shoulders"),
            _option("leaf_strewn", "落葉沾身", "dry leaves caught across hair and shoulders"),
            _option("oil_stained", "油污", "dark oil smudges across hands and clothing"),
            _option(
                "blood_spattered",
                "血跡斑駁",
                "non-graphic blood spatters across skin and clothing",
            ),
            _option("clothes_torn", "衣物破損", "visibly torn and weathered clothing"),
            _option("pollen_dusted", "花粉沾身", "fine glowing pollen dust across hair and shoulders"),
            _option("cobweb_tangled", "蛛網纏身", "wispy cobweb strands caught across hair and gear"),
            _option(
                "electrically_charged",
                "電流纏身",
                "crackling electrical energy coursing over the body",
            ),
            _option("shadow_wreathed", "暗影纏身", "unstable shadow energy clinging to the body"),
            _option(
                "partially_petrified",
                "局部石化",
                "partial stone transformation spreading across the body",
            ),
            _option("spectral_phase", "靈體化", "partially translucent spectral bodily state"),
            _option("healing_aura", "治療光暈", "visible restorative magic flowing around the body"),
            _option("time_frozen", "時間凝結", "body held in a visibly time-frozen state"),
            _option(
                "gravity_distorted",
                "重力扭曲",
                "hair and body subtly pulled by distorted gravity",
            ),
            _option(
                "magically_exhausted",
                "魔力耗竭",
                "visibly drained after intense magical exertion",
            ),
            _option(
                "flickering_invisibility",
                "隱形閃爍",
                "body flickering between visible and invisible states",
            ),
            _option("luminous_cracks", "發光裂紋", "temporary luminous cracks tracing across the skin"),
        ),
        selection_mode="multi",
        help_text="可複選彼此相容的即時狀態；隨機最多挑選兩個並避開明顯衝突。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="outfit_upper",
        label_zh="上身服裝",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_UPPER_SAFE),
        selection_mode="multi",
        help_text="可疊加最多兩件上身單品；完整隨機會與下身或其他主服裝配成一套。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_lower",
        label_zh="下身服裝",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_LOWER_SAFE),
        selection_mode="multi",
        help_text="褲裝與裙裝，可與上身服裝搭配成一套。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_one_piece",
        label_zh="連身服裝",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_ONE_PIECE_SAFE),
        selection_mode="multi",
        help_text="洋裝、連身褲與其他完整單件服裝，可與外套或配件疊穿。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_outerwear",
        label_zh="外套與披掛",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_OUTERWEAR_SAFE),
        selection_mode="multi",
        help_text="外套、披風與披肩，可與上身／下身或連身服裝搭配。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_sleepwear",
        label_zh="睡衣與居家服",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_SLEEPWEAR_SAFE),
        selection_mode="multi",
        help_text="睡衣、睡袍與居家穿搭可作為完整主服裝。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_uniform_sport",
        label_zh="正裝、制服與運動服",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_UNIFORM_SPORT_SAFE),
        selection_mode="multi",
        help_text="正式套裝、工作服與運動服單品，可自由混合搭配。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_swimwear",
        label_zh="運動泳裝",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_SWIMWEAR_SAFE),
        selection_mode="multi",
        help_text="競泳、衝浪與防曬泳裝，可作為完整運動穿搭。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="accessory_head_hair",
        label_zh="頭部與髮飾",
        group="服裝與配件",
        options=tuple(_option(*item) for item in ACCESSORIES_HEAD_HAIR_SAFE),
        selection_mode="multi",
        help_text="帽子、頭巾與髮飾。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="accessory_face_neck",
        label_zh="臉、頸與正裝配件",
        group="服裝與配件",
        options=tuple(_option(*item) for item in ACCESSORIES_FACE_NECK_SAFE),
        selection_mode="multi",
        help_text="領帶、領巾、面紗與其他臉頸配件。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="accessory_hand_arm",
        label_zh="手部與手臂配件",
        group="服裝與配件",
        options=tuple(_option(*item) for item in ACCESSORIES_HAND_ARM_SAFE),
        selection_mode="multi",
        help_text="手套、手錶、手環與上臂飾品。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="accessory_waist_body",
        label_zh="腰部與身體配件",
        group="服裝與配件",
        options=tuple(_option(*item) for item in ACCESSORIES_WAIST_BODY_SAFE),
        selection_mode="multi",
        help_text="腰帶、吊帶、斜掛帶與身體配件。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="accessory_bags",
        label_zh="包袋與隨身收納",
        group="服裝與配件",
        options=tuple(_option(*item) for item in ACCESSORIES_BAGS_SAFE),
        selection_mode="multi",
        help_text="背包、側背包、手提包與腰包。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="hosiery_style",
        label_zh="襪子／絲襪類型",
        group="服裝與配件",
        options=tuple(_option(*item) for item in HOSIERY_STYLE_SAFE),
        selection_mode="multi",
        help_text="先選襪子或絲襪類型，再用襪長補充覆蓋高度。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="hosiery_length",
        label_zh="襪子長度",
        group="服裝與配件",
        options=tuple(_option(*item) for item in HOSIERY_LENGTH_SAFE),
        selection_mode="multi",
        help_text="可留白；搭配襪子或絲襪時選擇一種長度。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="footwear",
        label_zh="鞋類",
        group="服裝與配件",
        options=tuple(_option(*item) for item in FOOTWEAR_SAFE),
        selection_mode="multi",
        help_text="鞋、靴與涼鞋，可複選以描述成套或替換方案。",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=2,
    ),
    TagCategory(
        key="outfit_upper_state",
        label_zh="上衣狀態",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_UPPER_STATE_SAFE),
        selection_mode="multi",
        help_text="可留白；狀態會獨立同步到 Prompt，不會改動已選上衣。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
    TagCategory(
        key="outfit_lower_state",
        label_zh="下身服裝狀態",
        group="服裝與配件",
        options=tuple(_option(*item) for item in OUTFIT_LOWER_STATE_SAFE),
        selection_mode="multi",
        help_text="可留白；一次只保留一種下身服裝狀態。",
        default_keys=(),
        random_min=0,
        random_max=1,
        applicable_gender=None,
        selection_max=1,
    ),
)


BACKGROUND_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    TagCategory(
        key="world_genre",
        label_zh="世界類型",
        group="世界與地點",
        options=(
            _option("high_fantasy", "高奇幻", "high-fantasy world"),
            _option("dark_fantasy", "黑暗奇幻", "dark-fantasy world"),
            _option("urban_fantasy", "都市奇幻", "urban-fantasy world"),
            _option("fairy_tale", "童話奇境", "storybook fantasy world"),
            _option("mythic", "神話世界", "mythic world"),
            _option("historical", "歷史世界", "historical world"),
            _option("gothic", "哥德世界", "gothic world"),
            _option("steampunk", "蒸汽龐克", "steampunk world"),
            _option("dieselpunk", "柴油龐克", "dieselpunk world"),
            _option("cyberpunk", "賽博龐克", "cyberpunk world"),
            _option("solarpunk", "太陽能龐克", "solarpunk world"),
            _option("space_opera", "太空歌劇", "space-opera world"),
            _option("post_apocalyptic", "末日後", "post-apocalyptic world"),
            _option("surreal", "超現實", "surreal dream world"),
            _option("wuxia", "武俠", "wuxia world"),
            _option("xianxia", "仙俠", "xianxia cultivation world"),
            _option("biopunk", "生物龐克", "biopunk world"),
            _option("clockpunk", "發條龐克", "clockpunk world"),
            _option("atompunk", "原子龐克", "atompunk world"),
            _option("arcanepunk", "魔導龐克", "arcanepunk world"),
            _option("gaslamp", "煤氣燈奇幻", "gaslamp fantasy world"),
            _option("nautical_fantasy", "航海奇幻", "nautical fantasy world"),
            _option("cosmic_horror", "宇宙恐怖", "cosmic-horror world"),
            _option("cozy_fantasy", "溫馨奇幻", "cozy fantasy world"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("high_fantasy",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="location",
        label_zh="主要地點",
        group="世界與地點",
        options=(
            _option("castle", "城堡外觀", "ancient castle exterior"),
            _option("throne_room", "王座大廳", "grand throne room"),
            _option("tavern", "旅店酒館", "cozy fantasy tavern"),
            _option("library", "古老圖書館", "vast ancient library"),
            _option("temple", "神殿", "sacred temple"),
            _option("market", "奇幻市集", "bustling fantasy market"),
            _option("village", "村落", "lived-in rural village"),
            _option("city_street", "城市街道", "detailed city street"),
            _option("alley", "狹窄巷弄", "narrow atmospheric alley"),
            _option("forest", "古老森林", "ancient enchanted forest"),
            _option("ruins", "失落遺跡", "overgrown lost ruins"),
            _option("cave", "洞窟", "vast crystal cave"),
            _option("coast", "海岸", "windswept rocky coast"),
            _option("desert", "沙漠", "vast desert landscape"),
            _option("mountain", "高山", "towering mountain pass"),
            _option("swamp", "沼澤", "misty ancient swamp"),
            _option("floating_island", "浮空島", "floating island sanctuary"),
            _option("underwater_city", "海底城市", "submerged underwater city"),
            _option("train_station", "車站", "grand old train station"),
            _option("laboratory", "實驗室", "advanced research laboratory"),
            _option("spaceship", "太空船", "vast spacecraft interior"),
            _option("alien_planet", "異星地表", "alien planetary landscape"),
            _option("palace_garden", "宮殿花園", "ornate palace garden"),
            _option("cathedral", "大教堂", "monumental cathedral interior"),
            _option("observatory", "天文台", "ancient astronomical observatory"),
            _option("workshop", "工匠工坊", "cluttered artisan workshop"),
            _option("greenhouse", "溫室", "vast botanical greenhouse"),
            _option("archive", "祕密檔案庫", "secure underground archive"),
            _option("harbor", "港口", "busy weathered harbor"),
            _option("waterfall", "瀑布谷地", "towering waterfall valley"),
            _option("canyon", "峽谷", "vast layered canyon"),
            _option("tundra", "苔原", "windswept tundra"),
            _option("bamboo_grove", "竹林", "misty bamboo grove"),
            _option("hot_spring", "溫泉", "secluded natural hot spring"),
            _option("battlefield", "古戰場", "abandoned ancient battlefield"),
            _option("airship", "飛空艇", "grand airship interior"),
            _option("megacity_rooftop", "巨城屋頂", "towering megacity rooftop"),
            _option("opera_house", "歷史歌劇院", "ornate historic opera house"),
            _option("lighthouse_headland", "燈塔岬角", "remote lighthouse on a rocky headland"),
            _option("catacombs", "地下墓穴", "labyrinthine underground catacombs"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("forest",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="spatial_scale",
        label_zh="空間尺度",
        group="世界與地點",
        options=(
            _option("small_space", "緊湊小空間", "compact small-scale space"),
            _option("room", "室內空間", "room-scale environment"),
            _option("street", "街區尺度", "street-scale environment"),
            _option("district", "城區尺度", "district-scale environment"),
            _option("city", "城市全景", "city-scale panorama"),
            _option("epic", "史詩級廣景", "epic monumental scale"),
            _option("tabletop", "桌面微景", "tabletop miniature scale"),
            _option("courtyard", "庭院尺度", "courtyard-scale environment"),
            _option("landscape", "地景尺度", "landscape-scale environment"),
            _option("planetary", "行星尺度", "planetary-scale vista"),
            _option("building_complex", "建築群尺度", "building-complex-scale environment"),
            _option("continental", "大陸尺度", "continental-scale vista"),
            _option("room_corner_micro", "房間角落尺度", "small room-corner-scale environment"),
            _option("village", "村落尺度", "village-scale environment"),
            _option("regional_valley", "區域山谷尺度", "regional valley-scale vista"),
            _option(
                "interior_megastructure",
                "巨構內部尺度",
                "vast interior-megastructure-scale environment",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("epic",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="architecture",
        label_zh="建築風格",
        group="建築與材質",
        options=(
            _option("medieval", "中世紀", "medieval architecture"),
            _option("gothic", "哥德式", "Gothic architecture"),
            _option("baroque", "巴洛克", "Baroque architecture"),
            _option("art_nouveau", "新藝術", "Art Nouveau architecture"),
            _option("east_asian", "東亞傳統", "traditional East Asian architecture"),
            _option("islamic", "伊斯蘭式", "ornate Islamic architecture"),
            _option("classical", "古典希臘羅馬", "classical Greco-Roman architecture"),
            _option("industrial", "工業風", "industrial architecture"),
            _option("brutalist", "粗獷主義", "Brutalist architecture"),
            _option("futuristic", "未來主義", "futuristic architecture"),
            _option("organic", "有機建築", "organic biomorphic architecture"),
            _option("alien", "異星建築", "alien nonhuman architecture"),
            _option("none", "無建築／自然景觀", "natural landscape without architecture"),
            _option("renaissance", "文藝復興", "Renaissance architecture"),
            _option("rococo", "洛可可", "Rococo architecture"),
            _option("victorian", "維多利亞式", "Victorian architecture"),
            _option("byzantine", "拜占庭式", "Byzantine architecture"),
            _option("art_deco", "裝飾藝術", "Art Deco architecture"),
            _option("vernacular", "地方傳統民居", "regional vernacular architecture"),
            _option("crystalline", "水晶建築", "crystalline fantasy architecture"),
            _option("biomechanical", "生物機械建築", "biomechanical architecture"),
            _option("mughal", "蒙兀兒式", "Mughal architecture"),
            _option("nordic_stave", "北歐木構教堂式", "Nordic stave-church architecture"),
            _option("mesoamerican", "中部美洲古文明式", "Mesoamerican monumental architecture"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("medieval",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="environment_materials",
        label_zh="主要材質",
        group="建築與材質",
        options=(
            _option("stone", "石材", "weathered stone surfaces"),
            _option("wood", "木材", "aged timber construction"),
            _option("brick", "磚牆", "textured brickwork"),
            _option("concrete", "混凝土", "textured concrete surfaces"),
            _option("marble", "大理石", "polished marble surfaces"),
            _option("glass", "玻璃", "expansive glass structures"),
            _option("metal", "金屬", "layered metal structures"),
            _option("crystal", "水晶", "luminous crystal structures"),
            _option("bone", "骨質", "monumental bone structures"),
            _option("living_plants", "活體植物", "living botanical architecture"),
            _option("overgrown", "藤蔓覆蓋", "dense overgrown vegetation"),
            _option("plaster", "灰泥", "aged plaster surfaces"),
            _option("terracotta", "陶瓦", "warm terracotta surfaces"),
            _option("copper", "銅材", "oxidized copper surfaces"),
            _option("brass", "黃銅", "polished brass details"),
            _option("ice", "冰晶", "translucent ice structures"),
            _option("fabric", "織物棚幕", "layered architectural fabric"),
            _option("paper", "紙質構造", "layered paper architecture"),
            _option("biomass", "生物組織", "living biomechanical tissue"),
            _option("jade", "玉石", "carved jade surfaces"),
            _option("obsidian", "黑曜石", "polished obsidian structures"),
            _option("mother_of_pearl", "珍珠母", "mother-of-pearl inlays"),
            _option("sandstone", "砂岩", "layered sandstone surfaces"),
            _option("volcanic_glass", "火山玻璃", "dark volcanic-glass surfaces"),
            _option("glowing_resin", "發光樹脂", "translucent glowing resin"),
            _option("porcelain", "瓷器", "glazed porcelain architecture"),
            _option("woven_reeds", "編織蘆葦", "woven reed structures"),
            _option("rammed_earth", "夯土", "rammed-earth construction"),
            _option("cork_panels", "軟木拼板", "layered cork-panel surfaces"),
            _option("terrazzo", "水磨石", "speckled terrazzo surfaces"),
            _option("polymer_panels", "聚合物板材", "translucent polymer panel structures"),
            _option("carbon_fiber", "碳纖維", "woven carbon-fiber structures"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=1,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="terrain",
        label_zh="地形",
        group="自然與天候",
        options=(
            _option("flat", "平原", "open plains"),
            _option("rolling_hills", "丘陵", "rolling hills"),
            _option("jagged_mountains", "峻峭山脈", "jagged mountains"),
            _option("cliffs", "懸崖", "towering cliffs"),
            _option("river", "河谷", "winding river valley"),
            _option("lake", "湖畔", "still lakeshore"),
            _option("wetlands", "濕地", "layered wetlands"),
            _option("dunes", "沙丘", "sweeping sand dunes"),
            _option("volcanic", "火山地帶", "volcanic terrain"),
            _option("glacial", "冰川地帶", "glacial terrain"),
            _option("floating", "漂浮地形", "levitating landmasses"),
            _option("interior", "室內／不顯示地形", "enclosed interior environment"),
            _option("seafloor", "海床", "submerged seafloor terrain"),
            _option("plateau", "高原", "high plateau terrain"),
            _option("badlands", "惡地", "eroded badlands"),
            _option("rice_terraces", "梯田", "layered rice terraces"),
            _option("coral_reef", "珊瑚礁", "submerged coral-reef terrain"),
            _option("urban", "城市地表", "dense urban terrain"),
            _option("cloud_sea", "雲海", "vast sea of clouds"),
            _option("giant_mushrooms", "巨菇地形", "giant mushroom landscape"),
            _option("salt_flat", "鹽湖平原", "reflective salt-flat terrain"),
            _option("archipelago", "島嶼群", "island-archipelago terrain"),
            _option("karst_towers", "喀斯特峰林", "karst tower landscape"),
            _option("lava_field", "熔岩原", "hardened lava-field terrain"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("rolling_hills",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="season",
        label_zh="季節",
        group="自然與天候",
        options=(
            _option("spring", "春季", "spring season"),
            _option("summer", "夏季", "summer season"),
            _option("autumn", "秋季", "autumn season"),
            _option("winter", "冬季", "winter season"),
            _option("timeless", "超越季節", "timeless otherworldly season"),
            _option("early_spring", "初春", "early spring season"),
            _option("rainy_season", "雨季", "monsoon rainy season"),
            _option("dry_season", "旱季", "dry season"),
            _option("eternal_winter", "永冬", "eternal winter season"),
            _option("midsummer", "盛夏", "midsummer season"),
            _option("late_autumn", "深秋", "late autumn season"),
            _option("snowmelt_season", "融雪季", "snowmelt season"),
            _option("late_winter", "冬末", "late-winter season"),
            _option("early_summer", "初夏", "early-summer season"),
            _option("harvest_season", "收穫季", "harvest season"),
            _option("storm_season", "風暴季", "season of recurring storms"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("autumn",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="time_of_day",
        label_zh="時間",
        group="自然與天候",
        options=(
            _option("dawn", "黎明", "early dawn"),
            _option("morning", "清晨", "morning light"),
            _option("noon", "正午", "bright midday"),
            _option("afternoon", "午後", "late afternoon"),
            _option("golden_hour", "黃金時刻", "golden hour"),
            _option("dusk", "黃昏", "deep dusk"),
            _option("night", "夜晚", "deep night"),
            _option("midnight", "午夜", "midnight setting"),
            _option("blue_hour", "藍調時刻", "blue hour"),
            _option("twilight", "暮光", "atmospheric twilight"),
            _option("pre_dawn", "破曉前", "dark pre-dawn"),
            _option("eclipse", "日蝕時刻", "solar eclipse"),
            _option("timeless_light", "無時間光境", "timeless ambient light"),
            _option("sunrise", "日出時刻", "sunrise breaking over the horizon"),
            _option("sunset", "日落時刻", "sunset at the horizon"),
            _option("moonrise", "月升時刻", "moonrise over the landscape"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("golden_hour",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="weather",
        label_zh="天候",
        group="自然與天候",
        options=(
            _option("clear", "晴朗", "clear weather"),
            _option("cloudy", "多雲", "layered clouds"),
            _option("fog", "濃霧", "rolling fog"),
            _option("drizzle", "細雨", "fine drizzle"),
            _option("rain", "大雨", "heavy rainfall"),
            _option("storm", "暴風雨", "violent storm"),
            _option("snow", "飄雪", "drifting snow"),
            _option("blizzard", "暴風雪", "blizzard conditions"),
            _option("wind", "強風", "strong wind"),
            _option("dust", "沙塵", "airborne dust"),
            _option("aurora", "極光", "vivid aurora overhead"),
            _option("magical_rain", "魔力光雨", "falling magical light"),
            _option("indoor", "室內環境", "sheltered indoor conditions"),
            _option("underwater", "水下環境", "submerged underwater conditions"),
            _option("hail", "冰雹", "falling hail"),
            _option("sleet", "雨夾雪", "windblown sleet"),
            _option("heat_haze", "熱浪", "shimmering heat haze"),
            _option("lightning", "閃電", "frequent lightning"),
            _option("meteor_shower", "流星雨", "vivid meteor shower"),
            _option("ashfall", "火山灰", "falling volcanic ash"),
            _option("pollen", "花粉飛舞", "glowing airborne pollen"),
            _option("rainbow", "雨後彩虹", "rainbow after rain"),
            _option("thunderstorm", "雷雨", "thunderstorm with driving rain"),
            _option("freezing_fog", "凍霧", "freezing fog conditions"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=1,
        random_max=2,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="atmosphere",
        label_zh="氛圍",
        group="氣氛與光色",
        options=(
            _option("peaceful", "寧靜", "peaceful atmosphere"),
            _option("cozy", "溫馨", "cozy atmosphere"),
            _option("romantic", "浪漫", "romantic atmosphere"),
            _option("lonely", "孤寂", "quiet lonely atmosphere"),
            _option("melancholic", "憂鬱", "melancholic atmosphere"),
            _option("mysterious", "神祕", "mysterious atmosphere"),
            _option("ominous", "不祥", "ominous atmosphere"),
            _option("sacred", "神聖", "sacred atmosphere"),
            _option("dreamlike", "夢幻", "dreamlike atmosphere"),
            _option("chaotic", "混亂", "chaotic atmosphere"),
            _option("desolate", "荒涼", "desolate atmosphere"),
            _option("nostalgic", "懷舊", "nostalgic atmosphere"),
            _option("triumphant", "凱旋", "triumphant atmosphere"),
            _option("tense", "緊張", "tense atmosphere"),
            _option("uncanny", "詭異", "uncanny atmosphere"),
            _option("whimsical", "奇趣", "whimsical atmosphere"),
            _option("luxurious", "奢華", "luxurious atmosphere"),
            _option("scholarly", "學術氣息", "scholarly atmosphere"),
            _option("festive", "節慶", "festive atmosphere"),
            _option("oppressive", "壓迫", "oppressive atmosphere"),
            _option("adventurous", "冒險感", "adventurous atmosphere"),
            _option("liminal", "閾限感", "liminal atmosphere"),
            _option("somber", "沉鬱", "somber atmosphere"),
            _option("buoyant", "輕快", "buoyant atmosphere"),
            _option("reverent", "崇敬", "reverent atmosphere"),
            _option("welcoming", "親切", "welcoming atmosphere"),
            _option("majestic", "壯麗", "majestic atmosphere"),
            _option("hushed", "萬籟俱寂", "hushed atmosphere"),
            _option("feverish", "躁動迷離", "feverish atmosphere"),
            _option("suspended_expectation", "屏息期待", "breathless anticipatory atmosphere"),
            _option("eerie_stillness", "幽森靜謐", "eerie still atmosphere"),
            _option("contemplative", "沉靜思索", "contemplative atmosphere"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("mysterious",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="background_lighting",
        label_zh="環境光線",
        group="氣氛與光色",
        options=(
            _option("soft_diffuse", "柔和漫射光", "soft diffuse lighting"),
            _option("sunbeams", "穿透光束", "sunbeams through the atmosphere"),
            _option("backlit", "逆光", "dramatic backlighting"),
            _option("moonlit", "月光", "cool moonlit illumination"),
            _option("candlelit", "燭火", "warm candlelit illumination"),
            _option("firelit", "火光", "flickering firelight"),
            _option("neon", "霓虹", "layered neon illumination"),
            _option("bioluminescent", "生物發光", "bioluminescent illumination"),
            _option("volumetric", "體積光", "dramatic volumetric lighting"),
            _option("high_contrast", "高反差", "high-contrast cinematic lighting"),
            _option("starlight", "星光", "clear starlight illumination"),
            _option("lanterns", "燈籠光", "layered lantern illumination"),
            _option("window_light", "窗光", "strong window light"),
            _option("god_rays", "神聖光束", "dramatic god rays"),
            _option("eclipse_rim", "日蝕環光", "eclipse rim lighting"),
            _option("underwater_caustics", "水下焦散光", "underwater caustic lighting"),
            _option("electrical_arcs", "電弧光", "electrical arc lighting"),
            _option("magic_crystals", "魔晶光", "luminous crystal lighting"),
            _option("lava_glow", "熔岩光", "lava-glow illumination"),
            _option("lightning_flash", "閃電瞬光", "lightning-flash illumination"),
            _option("phosphorescent", "磷光", "phosphorescent illumination"),
            _option("water_reflection", "水面反射光", "rippling water-reflection light"),
            _option("projector", "投影機光", "projector-beam lighting"),
            _option("stained_glass", "彩繪玻璃光", "colored stained-glass light"),
            _option("searchlights", "探照燈", "sweeping searchlight beams"),
            _option("dawn_rim", "破曉輪廓光", "dawn rim illumination"),
            _option("overcast_skylight", "陰天頂光", "soft overcast skylight"),
            _option("gaslight", "煤氣燈光", "warm gaslight illumination"),
            _option("sodium_vapor", "鈉氣燈光", "amber sodium-vapor street lighting"),
            _option("fluorescent_panels", "冷白螢光燈", "cold fluorescent panel lighting"),
            _option("string_lights", "串燈光影", "warm overhead string-light illumination"),
            _option("high_floodlights", "高架泛光燈", "broad high-mounted floodlight illumination"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("volumetric",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="color_palette",
        label_zh="色調／色盤",
        group="氣氛與光色",
        options=(
            _option("warm", "暖色調", "warm color palette"),
            _option("cool", "冷色調", "cool color palette"),
            _option("pastel", "粉彩色調", "soft pastel color palette"),
            _option("earth", "大地色調", "earth-tone color palette"),
            _option("jewel", "寶石色調", "rich jewel-tone palette"),
            _option("monochrome", "單色調", "restrained monochrome palette"),
            _option("teal_orange", "青橙電影色", "teal-and-orange cinematic palette"),
            _option("blue_gold", "藍金色調", "blue-and-gold color palette"),
            _option("violet_cyan", "紫青霓虹色", "violet-and-cyan neon palette"),
            _option("desaturated", "低飽和", "desaturated color palette"),
            _option("sepia", "褐色懷舊", "sepia color palette"),
            _option("black_gold", "黑金", "black-and-gold color palette"),
            _option("red_black", "紅黑", "crimson-and-black color palette"),
            _option("green_gold", "綠金", "green-and-gold color palette"),
            _option("pink_blue", "粉藍", "pink-and-blue color palette"),
            _option("rainbow", "彩虹", "rainbow color palette"),
            _option("iridescent", "虹彩", "iridescent color palette"),
            _option("duotone", "雙色調", "bold duotone palette"),
            _option("coral_teal", "珊瑚橘與藍綠", "coral-and-teal color palette"),
            _option("jade_gold", "玉綠與金", "jade-and-gold color palette"),
            _option("violet_silver", "紫羅蘭與銀", "violet-and-silver color palette"),
            _option("ochre_cyan", "赭黃與青", "ochre-and-cyan color palette"),
            _option("burgundy_cream", "酒紅與奶油", "burgundy-and-cream color palette"),
            _option("acid_magenta", "酸綠與洋紅", "acid-green-and-magenta color palette"),
            _option("moonlit_blue", "月光藍", "moonlit-blue color palette"),
            _option("ember_black", "餘燼橙與黑", "ember-orange-and-black color palette"),
            _option("obsidian_amber", "黑曜石與琥珀", "obsidian and amber color palette"),
            _option("lavender_mint", "薰衣草與薄荷", "lavender and mint color palette"),
            _option("rust_teal", "鐵鏽與藍綠", "rust and teal color palette"),
            _option("parchment_crimson", "羊皮紙與緋紅", "parchment and crimson color palette"),
            _option("coral_navy", "珊瑚與深藍", "coral and navy color palette"),
            _option("pearl_indigo", "珍珠與靛藍", "pearl and indigo color palette"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("jewel",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="composition",
        label_zh="畫面構圖",
        group="構圖與鏡頭",
        options=(
            _option("symmetrical", "對稱構圖", "symmetrical composition"),
            _option("rule_of_thirds", "三分構圖", "rule-of-thirds composition"),
            _option("leading_lines", "引導線構圖", "strong leading lines"),
            _option("central_vista", "中央景深", "central vanishing-point composition"),
            _option("foreground_frame", "前景框景", "foreground framing elements"),
            _option("layered_depth", "多層景深", "layered deep-space composition"),
            _option("diagonal", "對角動勢", "dynamic diagonal composition"),
            _option("panoramic", "全景構圖", "sweeping panoramic composition"),
            _option("golden_ratio", "黃金比例", "golden-ratio composition"),
            _option("radial", "放射構圖", "radial composition"),
            _option("frame_within_frame", "框中框", "frame-within-a-frame composition"),
            _option("negative_space", "留白構圖", "strong negative-space composition"),
            _option("s_curve", "S 型動線", "S-curve composition"),
            _option("triangular", "三角構圖", "triangular composition"),
            _option("split_scene", "分割場景", "split-scene composition"),
            _option("spiral", "螺旋構圖", "spiral composition"),
            _option("tunnel_frame", "隧道框景", "tunnel-framed composition"),
            _option("asymmetric_balance", "不對稱平衡", "asymmetrically balanced composition"),
            _option("horizon_split", "地平線分割", "horizon-split composition"),
            _option("deep_focus_layers", "深焦層次", "deep-focus layered composition"),
            _option("silhouette_frame", "剪影框景", "silhouette-framed composition"),
            _option("nested_arches", "層疊拱門引導", "nested arches guiding the composition"),
            _option("layered_thresholds", "多重門檻景深", "layered thresholds creating spatial depth"),
            _option("central_void", "中央留白核心", "central void anchoring the composition"),
            _option("overlapping_planes", "交疊景片", "overlapping scenic planes"),
            _option("reflected_axis", "倒影軸線", "reflection-balanced compositional axis"),
            _option("foreground_reveal", "前景揭幕", "foreground reveal composition"),
            _option("layered_silhouettes", "剪影層疊構圖", "layered-silhouette composition"),
            _option("l_shape", "L 型構圖", "strong L-shaped compositional balance"),
            _option("cross_axis", "十字軸線構圖", "cross-axis composition"),
            _option("repeating_grid", "重複網格構圖", "rhythmic repeating-grid composition"),
            _option("low_horizon", "低地平線構圖", "low-horizon composition emphasizing the sky"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("layered_depth",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="camera",
        label_zh="鏡頭語言",
        group="構圖與鏡頭",
        options=(
            _option("wide_establishing", "廣角建立鏡頭", "wide establishing shot"),
            _option("ultra_wide", "超廣角", "ultra-wide-angle view"),
            _option("eye_level", "平視", "eye-level camera"),
            _option("low_angle", "低角度", "low-angle camera"),
            _option("high_angle", "高角度", "high-angle camera"),
            _option("aerial", "空拍俯視", "aerial view"),
            _option("isometric", "等角視圖", "isometric view"),
            _option("telephoto", "望遠壓縮", "telephoto compression"),
            _option("fisheye", "魚眼", "subtle fisheye perspective"),
            _option("macro", "微距", "macro environmental view"),
            _option("ground_level", "貼地視角", "ground-level camera"),
            _option("bird_eye", "鳥瞰", "bird's-eye view"),
            _option("drone", "無人機跟拍", "drone-camera view"),
            _option("orthographic", "正交視圖", "orthographic environment view"),
            _option("tilt_shift", "移軸鏡頭", "tilt-shift lens view"),
            _option("long_exposure", "長曝光", "long-exposure camera effect"),
            _option("handheld", "手持紀實", "handheld documentary framing"),
            _option("dolly_zoom", "滑動變焦", "dolly-zoom environmental view"),
            _option("worm_eye", "蟲視角", "worm's-eye environmental view"),
            _option("shoulder_view", "越肩環境視角", "over-the-shoulder environmental view"),
            _option("wide_24mm", "24mm 廣角", "24mm wide-angle lens view"),
            _option("portrait_85mm", "85mm 壓縮景深", "85mm compressed-perspective view"),
            _option("anamorphic", "變形寬銀幕", "anamorphic widescreen lens view"),
            _option("infrared", "紅外線鏡頭", "infrared environmental camera view"),
            _option("surveillance", "監視器視角", "fixed surveillance-camera view"),
            _option("telephoto_compression", "長焦壓縮", "telephoto spatial compression"),
            _option("crane_view", "吊臂俯視", "high crane-view camera angle"),
            _option("threshold_view", "門檻視角", "camera positioned at a threshold"),
            _option("reflection_view", "倒影取景", "scene framed through a reflection"),
            _option("subsurface_view", "水面下視角", "camera viewpoint just below the water surface"),
            _option("ceiling_down", "穹頂俯拍", "ceiling-down architectural viewpoint"),
            _option("first_person_environment", "第一人稱環境視角", "first-person environmental viewpoint"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("wide_establishing",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="story_details",
        label_zh="敘事細節",
        group="場景故事",
        options=(
            _option("personal_objects", "遺留私人物品", "abandoned personal belongings"),
            _option("fresh_tracks", "新鮮足跡", "fresh tracks across the ground"),
            _option("open_book", "翻開的書", "an open book left behind"),
            _option("broken_weapon", "斷裂武器", "a broken weapon in the foreground"),
            _option("festival", "節慶裝飾", "weathered festival decorations"),
            _option("market_goods", "攤販貨物", "carefully arranged market goods"),
            _option("laundry", "生活晾曬", "laundry moving in the wind"),
            _option("warning_signs", "警示標記", "cryptic warning symbols"),
            _option("ritual", "儀式痕跡", "remnants of an old ritual"),
            _option("maps", "散落地圖", "scattered maps and notes"),
            _option("half_eaten_meal", "吃到一半的餐點", "a half-eaten meal left behind"),
            _option("fresh_flowers", "新鮮花束", "freshly arranged flowers"),
            _option("old_photographs", "老照片", "scattered old photographs"),
            _option("sealed_letter", "封蠟信件", "an unopened sealed letter"),
            _option("childhood_toy", "陳舊玩具", "an old abandoned toy"),
            _option("clock_stopped", "停擺時鐘", "a clock stopped at a meaningful hour"),
            _option("memorial", "紀念碑", "a weathered memorial"),
            _option("wanted_posters", "懸賞海報", "torn wanted posters"),
            _option("caravan", "商隊物資", "unloaded caravan supplies"),
            _option("medical_supplies", "醫療物資", "hastily used medical supplies"),
            _option("construction", "施工痕跡", "active construction materials"),
            _option("secret_door", "暗門", "a barely visible secret door"),
            _option("coded_message", "密碼訊息", "a hidden coded message"),
            _option("animal_tracks", "動物足跡", "animal tracks crossing the scene"),
            _option("forgotten_instrument", "遺落樂器", "a forgotten musical instrument"),
            _option("broken_crown", "破損王冠", "a broken crown left behind"),
            _option("cold_campfire", "熄滅營火", "a recently extinguished campfire"),
            _option("spilled_ink", "潑灑墨水", "freshly spilled ink"),
            _option("wet_footprints", "潮濕腳印", "fresh wet footprints"),
            _option("open_portal_trace", "傳送門殘痕", "fading traces of an opened portal"),
            _option("abandoned_luggage", "遺棄行李", "abandoned travel luggage"),
            _option("unfinished_portrait", "未完成肖像", "an unfinished portrait"),
            _option("burning_letter", "燃燒信件", "a letter burning at the edges"),
            _option("shattered_hourglass", "破碎沙漏", "a shattered hourglass"),
            _option("freshly_dug_soil", "新翻土壤", "freshly disturbed soil"),
            _option("empty_cage", "空鳥籠", "an open empty cage"),
            _option("ceremonial_mask", "遺落儀式面具", "a discarded ceremonial mask"),
            _option("unclaimed_throne", "無人王座", "an unclaimed throne"),
            _option("fresh_wax_seal", "新封蠟印", "a freshly pressed wax seal"),
            _option("misplaced_key", "錯置鑰匙", "a key left in the wrong place"),
            _option("evacuation_marks", "撤離記號", "hurried evacuation markings"),
            _option("returned_package", "退回包裹", "an unopened returned package"),
            _option("silent_bell", "沉默之鐘", "a bell that has fallen silent"),
            _option("two_place_settings", "雙人餐具", "two untouched place settings"),
            _option("repaired_map", "拼補地圖", "a carefully repaired map"),
            _option("chalk_countdown", "粉筆倒數記號", "a chalk countdown written on a wall"),
            _option("wet_umbrella", "滴水雨傘", "one wet umbrella left dripping"),
            _option("steaming_tea", "尚有熱氣的茶", "a cup of tea still steaming"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=1,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="history_and_decay",
        label_zh="歷史痕跡",
        group="場景故事",
        options=(
            _option("pristine", "保存完好", "pristine well-maintained surfaces"),
            _option("weathered", "風化", "weathered surfaces"),
            _option("cracked", "龜裂", "cracked masonry"),
            _option("overgrown", "植被侵蝕", "vegetation reclaiming the structures"),
            _option("flooded", "積水淹沒", "partially flooded ground"),
            _option("burned", "火災痕跡", "old fire damage"),
            _option("battle_damage", "戰損", "visible battle damage"),
            _option("repaired", "反覆修補", "layers of improvised repairs"),
            _option("newly_built", "新建", "newly built pristine construction"),
            _option("dusty", "積滿灰塵", "thick layers of dust"),
            _option("rusted", "鏽蝕", "heavily rusted metal"),
            _option("collapsed", "局部倒塌", "partially collapsed structures"),
            _option("frozen_over", "冰封", "surfaces frozen over"),
            _option("sand_buried", "沙埋", "partially buried by sand"),
            _option("mossy", "苔蘚覆蓋", "moss-covered surfaces"),
            _option("restored", "修復翻新", "carefully restored architecture"),
            _option("salt_eroded", "鹽蝕", "salt-eroded surfaces"),
            _option("soot_stained", "煙燻染黑", "soot-stained structures"),
            _option("petrified", "石化", "petrified organic structures"),
            _option("vine_reclaimed", "藤蔓重新占據", "structures reclaimed by thick vines"),
            _option("excavated", "考古發掘中", "partially excavated ruins"),
            _option("storm_damaged", "風暴損毀", "storm-damaged structures"),
            _option("unfinished", "半途停工", "abandoned unfinished construction"),
            _option("ritual_stained", "儀式染痕", "old ritual stains across surfaces"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=1,
        random_max=2,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="supernatural_details",
        label_zh="超自然現象",
        group="場景故事",
        options=(
            _option("floating_debris", "漂浮碎片", "slowly floating debris"),
            _option("glowing_runes", "發光符文", "glowing runes across the environment"),
            _option("spirit_lights", "靈火", "wandering spirit lights"),
            _option("portal", "傳送門", "unstable magical portal"),
            _option("time_distortion", "時間扭曲", "visible time distortion"),
            _option("giant_roots", "巨型神木根", "colossal ancient roots"),
            _option("crystal_bloom", "晶簇綻放", "blooming crystal formations"),
            _option("levitating_water", "漂浮水流", "levitating streams of water"),
            _option("ghosts", "幽靈", "faint wandering ghosts"),
            _option("giant_moon", "巨大月亮", "impossibly large moon"),
            _option("double_suns", "雙日", "two suns in the sky"),
            _option("inverted_waterfall", "逆流瀑布", "waterfall flowing upward"),
            _option("endless_stairs", "無盡階梯", "impossible endless stairways"),
            _option("mirror_dimension", "鏡像世界裂縫", "fractures into a mirror dimension"),
            _option("frozen_time", "時間靜止", "objects suspended in frozen time"),
            _option("living_shadows", "活影", "independent living shadows"),
            _option("singing_stones", "鳴響石", "resonating rune stones"),
            _option("celestial_whale", "天空巨鯨", "celestial whale in the distance"),
            _option("memory_echoes", "記憶殘影", "translucent echoes of past events"),
            _option("gravity_well", "重力異常", "visible gravitational distortion"),
            _option("floating_lanterns", "漂浮燈籠", "self-guided floating lanterns"),
            _option("sky_eye", "天空巨眼", "a colossal eye opening in the sky"),
            _option("spectral_train", "幽靈列車", "a distant spectral train"),
            _option("whispering_mist", "低語霧氣", "mist forming whispered words"),
            _option("time_loop", "時間迴圈", "visible repetitions from a time loop"),
            _option("door_to_stars", "星海之門", "an open doorway into deep space"),
            _option("walking_statues", "行走雕像", "distant walking statues"),
            _option("moon_fragments", "月亮碎片", "floating fragments of a broken moon"),
            _option("sentient_rain", "有意識的雨", "rain moving with deliberate intent"),
            _option("impossible_shadow", "不可能的影子", "a shadow cast by no object"),
            _option("memory_rain", "記憶之雨", "rain carrying visible memories"),
            _option("upward_snow", "逆向飄雪", "snow drifting upward"),
            _option("constellation_bridge", "星座橋", "a bridge formed from constellations"),
            _option("echoing_doorways", "迴聲門廊", "doorways echoing into alternate spaces"),
            _option("glass_tide", "玻璃潮汐", "a tide made of translucent glass"),
            _option("sleeping_colossus", "沉睡巨像", "a dormant stone colossus in the landscape"),
            _option("borrowed_sunlight", "借來的日光", "bottled sunlight illuminating the scene"),
            _option("folded_horizon", "摺疊地平線", "a horizon folded into impossible layers"),
            _option("inverted_sky_city", "倒懸天空城", "an upside-down city suspended in the sky"),
            _option(
                "ground_starlight_river",
                "星河流過地表",
                "a river of starlight flowing across the ground",
            ),
            _option(
                "walking_reflections",
                "脫離本體的倒影",
                "reflections walking independently of their owners",
            ),
            _option("floating_doorways", "漂浮門扉群", "freestanding doorways floating in midair"),
            _option("aurora_roots", "極光樹根", "luminous aurora-like roots spreading through the sky"),
            _option(
                "whispering_statues",
                "低語雕像群",
                "ancient statues visibly whispering to one another",
            ),
            _option(
                "unseen_colossus_tracks",
                "無形巨像足跡",
                "colossal footprints forming beneath an unseen traveler",
            ),
            _option(
                "breathing_mountains",
                "呼吸山脈",
                "mountain ranges visibly breathing in slow rhythms",
            ),
            _option(
                "living_murals",
                "壁畫生物出走",
                "painted creatures stepping out of ancient wall murals",
            ),
            _option(
                "seasonal_patchwork",
                "四季拼接地貌",
                "adjacent landscape patches displaying four different seasons",
            ),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=(),
        random_min=0,
        random_max=2,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="population",
        label_zh="人物密度",
        group="場景故事",
        options=(
            _option("empty", "無人空景", "no people"),
            _option("one_silhouette", "遠方單一剪影", "one distant human silhouette"),
            _option("few_silhouettes", "少量遠景剪影", "a few distant silhouettes"),
            _option("lived_in", "有人生活但不入鏡", "signs of inhabitants without visible people"),
            _option("busy", "熱鬧人群", "busy distant crowd"),
            _option("paired_silhouettes", "兩個遠方剪影", "two distant silhouettes"),
            _option("small_group", "小群人", "small distant group of adults"),
            _option("procession", "遊行隊伍", "distant ceremonial procession"),
            _option("evacuating", "疏散人潮", "distant evacuating crowd"),
            _option("nonhuman_crowd", "奇幻種族人群", "diverse fantasy humanoid crowd"),
            _option("solo_traveler", "單一成年旅人", "one distant adult traveler"),
            _option("merchant_caravan", "成年商隊", "distant caravan of adult merchants"),
            _option("city_guards", "城鎮守衛", "small patrol of adult guards"),
            _option("pilgrims", "朝聖者", "distant group of adult pilgrims"),
            _option("masked_revelers", "蒙面慶典人群", "distant masked adult revelers"),
            _option("scholars", "研究者群體", "small group of adult scholars"),
            _option("night_market_crowd", "夜市人潮", "dense night-market crowd"),
            _option("festival_dancers", "節慶舞者群", "group of festival dancers"),
            _option("dock_workers", "碼頭工人群", "adult dock workers loading cargo"),
            _option("distant_rescue_team", "遠方救援隊", "distant adult rescue team"),
            _option("morning_commuters", "晨間通勤人潮", "stream of distant adult morning commuters"),
            _option("terrace_farmers", "梯田農作者", "scattered adult farmers working across terraces"),
            _option(
                "shoreline_fishers",
                "岸邊漁人群",
                "small groups of adult fishers along the shoreline",
            ),
            _option(
                "restoration_crew",
                "古蹟修復團隊",
                "adult restoration crew repairing an old structure",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("empty",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="background_style",
        label_zh="美術風格",
        group="風格與完成度",
        options=(
            _option("anime", "動漫背景美術", "polished anime background art"),
            _option("semi_realistic", "半寫實", "semi-realistic environmental illustration"),
            _option("realistic", "寫實概念圖", "realistic environment concept art"),
            _option("painterly", "奇幻厚塗", "painterly fantasy environment art"),
            _option("watercolor", "水彩", "atmospheric watercolor landscape"),
            _option("ink", "水墨", "expressive ink-wash landscape"),
            _option("comic", "漫畫背景", "graphic comic-book environment art"),
            _option("pixel_art", "像素藝術", "detailed pixel-art environment"),
            _option("cinematic", "電影場景", "cinematic production environment art"),
            _option("storybook", "故事書背景", "storybook environment illustration"),
            _option("gouache", "廣告顏料", "gouache environment painting"),
            _option("oil_painting", "古典油畫", "classical oil-painted landscape"),
            _option("woodblock", "木刻版畫", "traditional woodblock-print landscape"),
            _option("retro_anime", "復古動畫背景", "retro anime background art"),
            _option("low_poly", "低多邊形", "stylized low-poly environment"),
            _option("miniature", "微縮模型", "handcrafted miniature environment"),
            _option("architectural_sketch", "建築速寫", "detailed architectural sketch"),
            _option("pastel_chalk", "粉彩畫", "pastel-chalk environment illustration"),
            _option("dark_academia", "黑暗學院風", "dark-academia environment art"),
            _option("synthwave", "合成波風", "synthwave environment art"),
            _option("papercut", "剪紙風", "layered papercut environment art"),
            _option("linocut", "油氈版畫", "linocut environment illustration"),
            _option("ukiyo_e", "浮世繪風", "ukiyo-e environment print"),
            _option("surreal_collage", "超現實拼貼", "surreal collage environment art"),
            _option("scientific_plate", "科學圖版", "scientific-plate environment illustration"),
            _option("charcoal_environment", "炭筆景觀", "charcoal-drawn environment art"),
            _option("risograph_environment", "孔版套色印刷風", "risograph environment illustration"),
            _option("mosaic_environment", "馬賽克鑲嵌畫", "mosaic-tile environment artwork"),
            _option("fresco_environment", "濕壁畫景觀", "fresco-mural environment painting"),
            _option("embroidered_tapestry", "刺繡掛毯景觀", "embroidered tapestry environment artwork"),
            _option("clay_animation", "黏土動畫場景", "handcrafted clay-animation environment"),
            _option("pointillist_environment", "點描派景觀", "pointillist environment painting"),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("cinematic",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="rendering",
        label_zh="材質呈現",
        group="風格與完成度",
        options=(
            _option("matte_painting", "數位場景繪景", "digital matte painting"),
            _option("concept_art", "概念設計", "professional environment concept design"),
            _option("illustration", "精緻插畫", "polished environmental illustration"),
            _option("photoreal", "擬真渲染", "photorealistic rendering"),
            _option("stylized_3d", "風格化 3D", "stylized three-dimensional rendering"),
            _option("line_and_color", "線稿上色", "clean line-and-color rendering"),
            _option("pixel_rendering", "像素渲染", "crisp pixel rendering"),
            _option("ink_rendering", "墨線渲染", "expressive ink rendering"),
            _option("watercolor_rendering", "水彩渲染", "layered watercolor rendering"),
            _option("voxel", "體素渲染", "crisp voxel rendering"),
            _option("miniature_render", "模型攝影感", "miniature model rendering"),
            _option("blueprint", "藍圖渲染", "technical blueprint rendering"),
            _option("cutaway", "剖面渲染", "architectural cutaway rendering"),
            _option("pastel_rendering", "粉彩渲染", "layered pastel rendering"),
            _option("cel_rendering", "賽璐璐渲染", "clean cel-shaded environment rendering"),
            _option("paper_cut_rendering", "剪紙渲染", "layered paper-cut rendering"),
            _option("linocut_rendering", "版畫渲染", "high-contrast linocut rendering"),
            _option("collage_rendering", "拼貼渲染", "mixed-media collage rendering"),
            _option("scientific_rendering", "科學圖版渲染", "precise scientific-plate rendering"),
            _option("ray_traced", "光線追蹤", "ray-traced environment rendering"),
            _option("hand_painted_3d", "手繪 3D 材質", "hand-painted 3D environment rendering"),
            _option("vector_environment", "向量圖渲染", "clean vector environment rendering"),
            _option("clay_environment", "黏土模型渲染", "clay-model environment rendering"),
            _option(
                "engraved_line_environment",
                "雕版線刻渲染",
                "fine engraved-line environment rendering",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("concept_art",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="background_detail",
        label_zh="細節密度",
        group="風格與完成度",
        options=(
            _option("clean", "乾淨易讀", "clean readable environment design"),
            _option("detailed", "高細節", "highly detailed environment"),
            _option("intricate", "極繁複", "intricate environmental detail"),
            _option("minimal", "極簡", "minimal environmental detail"),
            _option("production_ready", "製作用高完成度", "production-ready environment detail"),
            _option("micro_texture", "微觀材質", "fine environmental micro-textures"),
            _option("encyclopedic", "百科圖鑑級", "encyclopedic environmental detail"),
            _option("atmospheric", "氛圍優先", "atmosphere-focused environmental detail"),
            _option("readable_shapes", "清晰造型", "clean readable environmental shapes"),
            _option("dense_narrative", "密集敘事細節", "dense narrative environmental detail"),
            _option("material_study", "材質研究級", "material-study environmental detail"),
            _option("architectural_precision", "精密建築細節", "architecturally precise detail"),
            _option("cinematic_density", "電影級密度", "cinematic environmental detail density"),
            _option("ecological_detail", "生態微細節", "coherent ecological microdetail"),
            _option("historical_layers", "歷史層理", "readable layers of historical detail"),
            _option("wayfinding_clarity", "動線清晰度", "clear environmental wayfinding"),
            _option("weathering_logic", "風化邏輯", "physically coherent weathering detail"),
            _option("distance_detail", "遠景細節", "controlled long-distance environmental detail"),
            _option("structural_clarity", "結構清晰度", "structurally coherent environmental detail"),
            _option("storytelling_focus", "敘事重點細節", "selective story-focused environmental detail"),
            _option("foreground_readability", "前景層次清晰", "clearly readable foreground layering"),
            _option("midground_story_density", "中景敘事密度", "story-rich midground detail density"),
            _option(
                "distance_atmospheric_detail",
                "遠距空氣透視層次",
                "graduated atmospheric perspective separating distant depth layers",
            ),
            _option(
                "functional_prop_logic",
                "道具功能邏輯",
                "functionally coherent environmental prop detail",
            ),
        ),
        selection_mode="single",
        help_text="",
        default_keys=("detailed",),
        random_min=1,
        random_max=1,
        applicable_gender=None,
        selection_max=None,
    ),
)


NEGATIVE_CATEGORIES: Final[tuple[TagCategory, ...]] = (
    TagCategory(
        key="negative_quality",
        label_zh="畫質問題",
        group="負面提示詞",
        options=(
            _option("low_quality", "低畫質", "low quality"),
            _option("low_resolution", "低解析度", "low resolution"),
            _option("blurry", "模糊", "blurry"),
            _option("noise", "雜訊", "excessive noise"),
            _option("compression", "壓縮破圖", "compression artifacts"),
            _option("oversharpened", "過度銳化", "oversharpened"),
            _option("overexposed", "過曝", "overexposed"),
            _option("underexposed", "曝光不足", "underexposed"),
            _option("banding", "色階斷層", "color banding"),
            _option("posterization", "色彩海報化", "posterization"),
            _option("chromatic_aberration", "色差", "unwanted chromatic aberration"),
            _option("jpeg_artifacts", "JPEG 瑕疵", "JPEG artifacts"),
            _option("muddy_colors", "色彩混濁", "muddy colors"),
            _option("aliasing", "鋸齒邊緣", "aliased edges"),
            _option("color_noise", "彩色雜訊", "color noise"),
            _option("washed_out", "色彩褪白", "washed-out colors"),
            _option("crushed_blacks", "暗部死黑", "crushed black levels"),
            _option("halo_sharpening", "銳化光暈", "sharpening halos"),
            _option("plastic_surfaces", "塑膠感表面", "unintended plastic-looking surfaces"),
            _option("waxy_skin", "蠟像皮膚", "waxy artificial skin"),
            _option("overprocessed", "過度後製", "overprocessed image"),
            _option("uneven_detail", "細節密度不均", "inconsistent detail density"),
            _option("moire_interference", "摩爾干涉紋", "unwanted moire interference patterns"),
            _option("dirty_lens_smears", "鏡頭污痕", "unwanted dirty-lens smears"),
            _option("muddy_microcontrast", "微對比混濁", "muddy local microcontrast"),
            _option(
                "shadow_hue_contamination",
                "陰影色偏污染",
                "unwanted hue contamination in shadow regions",
            ),
            _option("uneven_texture_sharpness", "材質銳度不一致", "inconsistent texture sharpness"),
            _option("edge_ghosting", "邊緣雙影", "faint duplicated ghost contours around object edges"),
            _option(
                "specular_bloom_bleed",
                "高光泛白溢出",
                "uncontrolled specular bloom bleeding into nearby details",
            ),
            _option("microdetail_ringing", "微細節振鈴邊", "ringing contours around fine image details"),
            _option(
                "local_contrast_clipping",
                "局部對比截斷",
                "clipped local contrast with lost highlight and shadow detail",
            ),
            _option(
                "denoise_texture_smearing",
                "降噪材質抹除",
                "over-smoothed texture smearing from excessive denoising",
            ),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=("low_quality", "low_resolution", "blurry"),
        random_min=2,
        random_max=4,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="negative_anatomy",
        label_zh="人物結構問題",
        group="負面提示詞",
        options=(
            _option("bad_anatomy", "錯誤人體", "bad anatomy"),
            _option("bad_hands", "錯誤手部", "bad hands"),
            _option("extra_digits", "多餘手指", "extra digits"),
            _option("missing_digits", "缺少手指", "missing digits"),
            _option("extra_limbs", "多餘肢體", "extra limbs"),
            _option("missing_limbs", "缺少肢體", "missing limbs"),
            _option("deformed_face", "臉部變形", "deformed face"),
            _option("cross_eyed", "鬥雞眼", "cross-eyed"),
            _option("fused_fingers", "手指黏連", "fused fingers"),
            _option("twisted_limbs", "肢體扭曲", "twisted limbs"),
            _option("dislocated_joints", "關節脫位", "dislocated joints"),
            _option("asymmetrical_eyes", "眼睛不對稱", "unintentionally asymmetrical eyes"),
            _option("duplicate_face", "重複臉孔", "duplicated face"),
            _option("floating_limbs", "肢體分離", "detached floating limbs"),
            _option("bad_teeth", "牙齒錯誤", "malformed teeth"),
            _option("broken_neck", "頸部結構錯誤", "anatomically impossible neck"),
            _option("uneven_pupils", "瞳孔大小不一", "unintentionally uneven pupils"),
            _option("misplaced_ears", "耳朵位置錯誤", "misplaced ears"),
            _option("duplicated_torso", "重複軀幹", "duplicated torso"),
            _option("broken_spine", "脊椎扭曲", "impossible spinal curvature"),
            _option("merged_limbs", "肢體融合", "merged limbs"),
            _option("inconsistent_hands", "雙手結構不一致", "inconsistent hand anatomy"),
            _option("malformed_feet", "腳部畸形", "malformed feet"),
            _option("floating_facial_parts", "五官漂移", "misplaced facial features"),
            _option("quadruped_body", "排除四足獸體", "quadruped body plan"),
            _option("feral_posture", "排除野獸姿態", "feral animal posture"),
            _option("ordinary_animal_head", "排除普通動物頭像", "ordinary nonsapient animal head"),
            _option("nonhumanoid_proportions", "排除非人型比例", "non-humanoid body proportions"),
            _option("unusable_hands", "無法持物的手", "hands unable to hold tools"),
            _option("duplicated_ears", "排除多餘耳朵", "duplicated ears"),
            _option("mixed_human_animal_ears", "排除人耳與獸耳同時出現", "simultaneous human and animal ears"),
            _option("mismatched_animal_ears", "排除異種耳朵", "mismatched animal ear anatomy"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=("bad_anatomy", "bad_hands", "extra_digits", "extra_limbs"),
        random_min=3,
        random_max=5,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="negative_composition",
        label_zh="構圖問題",
        group="負面提示詞",
        options=(
            _option("cropped", "意外裁切", "awkward cropping"),
            _option("cut_off", "肢體截斷", "cut-off limbs"),
            _option("duplicate", "重複主體", "duplicated subjects"),
            _option("bad_perspective", "錯誤透視", "incorrect perspective"),
            _option("tilted_horizon", "歪斜地平線", "unintentionally tilted horizon"),
            _option("clutter", "視覺雜亂", "visual clutter"),
            _option("tangent_lines", "錯誤相切線", "awkward tangent lines"),
            _option("centered_accidentally", "意外正中央", "accidentally centered composition"),
            _option("subject_too_small", "主體過小", "subject too small"),
            _option("subject_too_large", "主體過大", "subject too large"),
            _option("dead_space", "無意義空白", "unintentional dead space"),
            _option("conflicting_focal_points", "焦點衝突", "conflicting focal points"),
            _option("head_cutoff", "頭頂被切斷", "accidentally cut-off head"),
            _option("limb_cutoff", "肢體被切斷", "accidentally cut-off limbs"),
            _option("slanted_horizon", "地平線歪斜", "unintentionally slanted horizon"),
            _option("foreground_block", "前景遮擋主體", "foreground blocking the subject"),
            _option("no_focal_point", "缺乏視覺焦點", "missing visual focal point"),
            _option("repetitive_layout", "構圖重複", "repetitive composition layout"),
            _option("accidental_symmetry", "意外僵硬對稱", "unintended rigid symmetry"),
            _option("ambiguous_depth", "景深關係不明", "ambiguous spatial depth"),
            _option("competing_horizons", "多重衝突地平線", "competing horizon lines"),
            _option("blocked_eyeline", "視線受阻", "unintentionally blocked eyeline"),
            _option("unbalanced_visual_weight", "視覺重心失衡", "unbalanced visual weight"),
            _option(
                "merged_subject_silhouettes",
                "主體輪廓黏連",
                "unintentionally merged subject silhouettes",
            ),
            _option(
                "accidental_edge_tension",
                "主體緊貼畫面邊緣",
                "accidental subject tension against the frame edge",
            ),
            _option(
                "accidental_subject_occlusion",
                "次要主體意外遮擋",
                "unintentional occlusion of the primary subject by another subject",
            ),
            _option(
                "empty_leading_space",
                "視線方向留白錯置",
                "empty leading space placed opposite the subject gaze",
            ),
            _option(
                "missing_scale_reference",
                "缺少尺度參照",
                "missing reliable scale reference between subjects and environment",
            ),
            _option(
                "cramped_subject_spacing",
                "主體間距過度擁擠",
                "cramped spacing between intended focal subjects",
            ),
            _option(
                "contradictory_eyelines",
                "角色視線關係矛盾",
                "contradictory eyeline directions between interacting subjects",
            ),
            _option(
                "trapped_negative_space",
                "封閉式尷尬留白",
                "awkward enclosed pockets of negative space",
            ),
            _option(
                "depth_layer_collision",
                "景深層次相互衝突",
                "overlapping depth layers with unclear visual ordering",
            ),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=("cropped",),
        random_min=1,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="negative_artifacts",
        label_zh="文字與生成瑕疵",
        group="負面提示詞",
        options=(
            _option("text", "文字", "text"),
            _option("watermark", "浮水印", "watermark"),
            _option("logo", "標誌", "logo"),
            _option("signature", "簽名", "signature"),
            _option("frame", "多餘邊框", "decorative frame"),
            _option("ui", "介面元素", "user-interface elements"),
            _option("generation_artifacts", "生成殘影", "generation artifacts"),
            _option("date_stamp", "日期戳記", "date stamp"),
            _option("speech_bubble", "對話框", "speech bubbles"),
            _option("prompt_text", "提示詞文字", "visible prompt text"),
            _option("qr_code", "QR Code／二維碼", "QR code"),
            _option("editing_handles", "編輯控制點", "visible editing handles"),
            _option("color_profile_error", "色彩設定錯誤", "color-profile artifacts"),
            _option("browser_chrome", "瀏覽器介面", "browser interface elements"),
            _option("thumbnail_border", "縮圖邊框", "thumbnail border"),
            _option("model_sheet_labels", "設定稿標示文字", "model-sheet labels"),
            _option("reference_grid", "參考網格", "reference grid overlay"),
            _option("selection_outline", "選取外框", "selection outline artifact"),
            _option("broken_transparency", "透明背景破損", "broken transparency artifacts"),
            _option("subtitle_artifact", "錯誤字幕殘留", "unwanted subtitle text"),
            _option("crop_mark_artifact", "錯誤裁切記號", "unwanted crop marks"),
            _option("color_swatch_artifact", "錯誤色票覆蓋", "unwanted color-swatch overlays"),
            _option("cursor_artifact", "滑鼠游標殘留", "visible mouse cursor artifact"),
            _option("loading_spinner_artifact", "載入圖示殘留", "unwanted loading-spinner overlay"),
            _option("bounding_box_artifact", "辨識框線殘留", "unwanted object-detection bounding boxes"),
            _option("mask_edge_artifact", "遮罩邊界殘留", "visible editing-mask boundary artifacts"),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=("text", "watermark", "logo", "signature"),
        random_min=3,
        random_max=5,
        applicable_gender=None,
        selection_max=None,
    ),
    TagCategory(
        key="negative_environment",
        label_zh="場景結構問題",
        group="負面提示詞",
        options=(
            _option("warped_architecture", "建築扭曲", "warped architecture"),
            _option("floating_objects", "不合理漂浮物", "unintended floating objects"),
            _option("repeating_patterns", "重複紋理", "repeating texture patterns"),
            _option("flat_depth", "景深扁平", "flat spatial depth"),
            _option("empty_detail", "細節空洞", "empty untextured surfaces"),
            _option("people", "排除人物", "people"),
            _option("impossible_stairs", "錯誤階梯", "impossible stair geometry"),
            _option("broken_scale", "比例失衡", "inconsistent environmental scale"),
            _option("bad_reflections", "錯誤反射", "incorrect reflections"),
            _option("bad_shadows", "錯誤陰影", "inconsistent shadows"),
            _option("texture_seams", "材質接縫", "visible texture seams"),
            _option("popping_objects", "突兀物件", "contextless out-of-place props"),
            _option("melted_buildings", "建築融化", "melted building geometry"),
            _option("inconsistent_weather", "天候矛盾", "contradictory weather effects"),
            _option("floating_doors", "門窗漂浮", "unsupported floating doors"),
            _option("broken_vanishing_points", "消失點錯誤", "conflicting vanishing points"),
            _option("repeated_buildings", "建築重複", "duplicated building patterns"),
            _option("weather_indoors", "室內錯誤天候", "outdoor weather inside sealed rooms"),
            _option("disconnected_roads", "道路斷裂", "roads connecting to nowhere"),
            _option("impossible_waterline", "水面線錯誤", "physically impossible waterline"),
            _option("scale_drift", "遠近比例漂移", "scale drift across the environment"),
            _option(
                "inconsistent_light_sources",
                "光源矛盾",
                "contradictory environmental light sources",
            ),
            _option("feral_animals", "排除普通野獸主體", "ordinary feral animal subjects"),
            _option("quadruped_crowd", "排除四足獸群", "quadruped animal crowd"),
            _option("incoherent_ecology", "生態不連貫", "incoherent environmental ecology"),
            _option("contradictory_era", "時代元素衝突", "contradictory historical eras"),
            _option("inaccessible_paths", "無法通行的道路", "inaccessible environmental pathways"),
            _option(
                "intersecting_environment_geometry",
                "場景物件穿模",
                "intersecting environmental geometry",
            ),
            _option(
                "misaligned_surface_tiles",
                "牆地磚縫錯位",
                "misaligned floor or wall tile boundaries",
            ),
            _option(
                "unsupported_balconies",
                "懸空無支撐陽台",
                "physically unsupported architectural balconies",
            ),
            _option(
                "broken_doorway_scale",
                "門框尺度異常",
                "inconsistent doorway scale within the same environment",
            ),
            _option(
                "reflection_world_mismatch",
                "反射場景世界不一致",
                "reflections showing a contradictory surrounding environment",
            ),
        ),
        selection_mode="multi",
        help_text="",
        default_keys=("warped_architecture", "repeating_patterns"),
        random_min=1,
        random_max=3,
        applicable_gender=None,
        selection_max=None,
    ),
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


_BEAST_HUMANOID_OPTIONS: Final[tuple[TagOption, ...]] = next(
    category.options for category in CHARACTER_CATEGORIES
    if category.key == "beast_humanoid_species"
)
_FURRY_SPECIES_OPTIONS: Final[tuple[TagOption, ...]] = next(
    category.options for category in CHARACTER_CATEGORIES if category.key == "furry_species"
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
        raise ValueError(f"細節擴充引用不存在的物種：{", ".join(sorted(unknown))}")
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


HOSIERY_ALLOWED_LENGTHS: Final[Mapping[str, tuple[str, ...]]] = {"socks": ("hosiery_ankle_length", "hosiery_quarter_length", "hosiery_crew_length",
           "hosiery_mid_calf_length", "hosiery_knee_high_length", "hosiery_over_knee_length",
           "hosiery_thigh_high_length"),
 "stockings": ("hosiery_over_knee_length", "hosiery_thigh_high_length"),
 "pantyhose": ("hosiery_waist_high_length",),
 "tights": ("hosiery_waist_high_length",),
 "leg_warmers": ("hosiery_mid_calf_length", "hosiery_knee_high_length",
                 "hosiery_over_knee_length", "hosiery_thigh_high_length"),
 "cable_knit_socks": ("hosiery_ankle_length", "hosiery_quarter_length", "hosiery_crew_length",
                      "hosiery_mid_calf_length", "hosiery_knee_high_length",
                      "hosiery_over_knee_length", "hosiery_thigh_high_length"),
 "compression_socks": ("hosiery_crew_length", "hosiery_mid_calf_length",
                       "hosiery_knee_high_length", "hosiery_over_knee_length",
                       "hosiery_thigh_high_length")}


def _compatibility(
    anchor_category: str,
    dependent_category: str,
    allowed: Mapping[str, Sequence[str]],
) -> _CompatibilityRule:
    return _CompatibilityRule(
        anchor_category,
        dependent_category,
        tuple((anchor, frozenset(values)) for anchor, values in allowed.items()),
    )


_COMPATIBILITY_RULES: Final[tuple[_CompatibilityRule, ...]] = (
    *(
        _compatibility("beast_humanoid_species", detail_category, allowed)
        for detail_category, allowed in _BEAST_DETAIL_RULES.items()
    ),
    *(
        _compatibility("furry_species", detail_category, allowed)
        for detail_category, allowed in _FURRY_DETAIL_RULES.items()
    ),
    _compatibility(
        "hosiery_style", "hosiery_length",
        {"socks": ("hosiery_ankle_length", "hosiery_crew_length", "hosiery_knee_high_length",
                   "hosiery_mid_calf_length", "hosiery_over_knee_length",
                   "hosiery_quarter_length", "hosiery_thigh_high_length"),
         "stockings": ("hosiery_over_knee_length", "hosiery_thigh_high_length"),
         "pantyhose": ("hosiery_waist_high_length",),
         "tights": ("hosiery_waist_high_length",),
         "leg_warmers": ("hosiery_knee_high_length", "hosiery_mid_calf_length",
                         "hosiery_over_knee_length", "hosiery_thigh_high_length"),
         "cable_knit_socks": ("hosiery_ankle_length", "hosiery_crew_length",
                              "hosiery_knee_high_length", "hosiery_mid_calf_length",
                              "hosiery_over_knee_length", "hosiery_quarter_length",
                              "hosiery_thigh_high_length"),
         "compression_socks": ("hosiery_crew_length", "hosiery_knee_high_length",
                               "hosiery_mid_calf_length", "hosiery_over_knee_length",
                               "hosiery_thigh_high_length")},
    ),
    _compatibility(
        "hair_length", "hair_style",
        {"shaved": ("buzz_cut",),
         "pixie": ("curly", "loose_straight", "loose_wavy", "messy", "undercut"),
         "chin": ("bob", "curly", "loose_straight", "loose_wavy", "messy"),
         "shoulder": ("bob", "bun", "crown_braid", "curly", "double_braids", "double_buns",
                      "high_ponytail", "loose_straight", "loose_wavy", "messy", "ponytail",
                      "side_ponytail", "single_braid", "twin_tails"),
         "mid_back": ("bun", "crown_braid", "curly", "double_braids", "double_buns",
                      "high_ponytail", "loose_straight", "loose_wavy", "messy", "ponytail",
                      "side_ponytail", "single_braid", "twin_tails"),
         "waist": ("bun", "crown_braid", "curly", "double_braids", "double_buns",
                   "high_ponytail", "loose_straight", "loose_wavy", "messy", "ponytail",
                   "side_ponytail", "single_braid", "twin_tails"),
         "floor": ("bun", "crown_braid", "curly", "double_braids", "high_ponytail",
                   "loose_straight", "loose_wavy", "messy", "ponytail", "single_braid",
                   "twin_tails")},
    ),
    _compatibility(
        "hair_length", "bangs",
        {"shaved": ("none",),
         "pixie": ("none", "side_swept", "straight", "wispy"),
         "chin": ("curtain", "hime", "none", "side_swept", "straight", "wispy"),
         "shoulder": ("curtain", "hime", "none", "side_swept", "straight", "wispy"),
         "mid_back": ("curtain", "hime", "none", "side_swept", "straight", "wispy"),
         "waist": ("curtain", "hime", "none", "side_swept", "straight", "wispy"),
         "floor": ("curtain", "hime", "none", "side_swept", "straight", "wispy")},
    ),
    _compatibility(
        "hair_style", "bangs",
        {"buzz_cut": ("none",), "undercut": ("none", "side_swept"), "mohawk": ("none",)},
    ),
    _compatibility(
        "fantasy_race", "fantasy_traits",
        {"human": ("ethereal_aura", "glowing_runes", "mechanical_limbs"),
         "elf": ("ethereal_aura", "glowing_runes", "pointed_ears"),
         "dark_elf": ("ethereal_aura", "fangs", "glowing_runes", "pointed_ears"),
         "half_elf": ("ethereal_aura", "glowing_runes", "pointed_ears"),
         "dwarf": ("glowing_runes", "mechanical_limbs"),
         "orc": ("fangs", "glowing_runes", "pointed_ears"),
         "goblin": ("fangs", "glowing_runes", "pointed_ears"),
         "tiefling": ("bat_wings", "fangs", "glowing_runes", "horns", "tail"),
         "demon": ("bat_wings", "dragon_wings", "ethereal_aura", "fangs", "glowing_runes",
                   "horns", "scales", "tail"),
         "angel": ("ethereal_aura", "feathered_wings", "glowing_runes", "halo"),
         "vampire": ("bat_wings", "ethereal_aura", "fangs", "pointed_ears"),
         "werewolf": ("fangs", "fur", "glowing_runes", "pointed_ears", "tail"),
         "merfolk": ("ethereal_aura", "pointed_ears", "scales", "tail"),
         "fairy": ("ethereal_aura", "fairy_wings", "glowing_runes", "pointed_ears"),
         "dragonkin": ("crystal_growths", "dragon_wings", "fangs", "glowing_runes", "horns",
                       "scales", "tail"),
         "automaton": ("crystal_growths", "glowing_runes", "mechanical_limbs"),
         "android": ("glowing_runes", "mechanical_limbs"),
         "undead": ("ethereal_aura", "fangs", "glowing_runes"),
         "elemental": ("crystal_growths", "ethereal_aura", "glowing_runes", "horns")},
    ),
    _compatibility(
        "outfit_archetype", "outfit_materials",
        {"casual": ("lace", "leather", "linen", "silk"),
         "streetwear": ("holographic", "leather", "linen", "metal"),
         "formal": ("lace", "silk", "velvet"),
         "business": ("leather", "linen", "silk", "velvet"),
         "traveler": ("fur_trim", "leather", "linen"),
         "mage": ("lace", "leather", "linen", "silk", "velvet"),
         "knight": ("fur_trim", "leather", "linen", "metal"),
         "rogue": ("leather", "linen"),
         "ranger": ("fur_trim", "leather", "linen"),
         "priest": ("lace", "linen", "silk", "velvet"),
         "royal": ("fur_trim", "lace", "metal", "silk", "velvet"),
         "alchemist": ("leather", "linen", "metal"),
         "steampunk": ("leather", "linen", "metal", "velvet"),
         "cyberpunk": ("holographic", "leather", "metal"),
         "space_suit": ("holographic", "leather", "metal"),
         "kimono": ("linen", "silk"),
         "hanfu": ("linen", "silk")},
    ),
    _compatibility(
        "outfit_archetype", "accessories",
        {"space_suit": ("gloves", "satchel")},
    ),
    _compatibility(
        "character_backdrop", "character_location",
        {"transparent": ()},
    ),
    _compatibility(
        "framing", "character_backdrop",
        {"character_sheet": ("gradient", "simple", "transparent")},
    ),
    _compatibility(
        "framing", "pose",
        {"face": ("looking_back", "seated", "standing"),
         "bust": ("casting", "looking_back", "seated", "standing"),
         "half_body": ("casting", "looking_back", "seated", "standing", "weapon_ready"),
         "character_sheet": ("standing", "weapon_ready")},
    ),
    _compatibility(
        "framing", "viewpoint",
        {"face": ("eye_level", "high_angle", "low_angle", "profile", "three_quarter"),
         "bust": ("eye_level", "high_angle", "low_angle", "profile", "three_quarter"),
         "half_body": ("eye_level", "high_angle", "low_angle", "profile", "three_quarter"),
         "character_sheet": ("eye_level", "three_quarter")},
    ),
    _compatibility(
        "world_genre", "location",
        {"high_fantasy": ("alley", "castle", "cave", "city_street", "coast", "desert",
                          "floating_island", "forest", "library", "market", "mountain",
                          "ruins", "swamp", "tavern", "temple", "throne_room",
                          "underwater_city", "village"),
         "dark_fantasy": ("alley", "castle", "cave", "city_street", "coast", "desert",
                          "forest", "laboratory", "library", "market", "mountain", "ruins",
                          "swamp", "tavern", "temple", "throne_room", "train_station",
                          "village"),
         "urban_fantasy": ("alley", "city_street", "forest", "laboratory", "library",
                           "market", "ruins", "temple", "train_station"),
         "fairy_tale": ("castle", "cave", "coast", "floating_island", "forest", "library",
                        "mountain", "ruins", "swamp", "tavern", "temple", "throne_room",
                        "underwater_city", "village"),
         "mythic": ("alien_planet", "castle", "cave", "coast", "desert", "floating_island",
                    "forest", "library", "market", "mountain", "ruins", "swamp", "temple",
                    "throne_room", "underwater_city", "village"),
         "historical": ("alley", "castle", "city_street", "coast", "desert", "forest",
                        "library", "market", "mountain", "ruins", "tavern", "temple",
                        "throne_room", "train_station", "village"),
         "gothic": ("alley", "castle", "cave", "city_street", "coast", "forest", "library",
                    "ruins", "tavern", "temple", "throne_room", "train_station", "village"),
         "steampunk": ("alley", "castle", "city_street", "laboratory", "library", "market",
                       "ruins", "tavern", "throne_room", "train_station", "village"),
         "dieselpunk": ("alley", "city_street", "laboratory", "market", "ruins",
                        "train_station"),
         "cyberpunk": ("alley", "city_street", "laboratory", "market", "ruins", "spaceship",
                       "train_station"),
         "solarpunk": ("city_street", "coast", "floating_island", "forest", "laboratory",
                       "market", "mountain", "spaceship", "village"),
         "space_opera": ("alien_planet", "city_street", "desert", "floating_island",
                         "laboratory", "market", "ruins", "spaceship", "throne_room"),
         "post_apocalyptic": ("alley", "city_street", "desert", "forest", "laboratory",
                              "ruins", "swamp", "train_station", "village"),
         "surreal": ("airship", "alien_planet", "alley", "archive", "bamboo_grove",
                     "battlefield", "canyon", "castle", "catacombs", "cathedral", "cave",
                     "city_street", "coast", "desert", "floating_island", "forest",
                     "greenhouse", "harbor", "hot_spring", "laboratory", "library",
                     "lighthouse_headland", "market", "megacity_rooftop", "mountain",
                     "observatory", "opera_house", "palace_garden", "ruins", "spaceship",
                     "swamp", "tavern", "temple", "throne_room", "train_station", "tundra",
                     "underwater_city", "village", "waterfall", "workshop")},
    ),
    _compatibility(
        "world_genre", "architecture",
        {"high_fantasy": ("alien", "baroque", "classical", "east_asian", "gothic", "islamic",
                          "medieval", "none", "organic"),
         "dark_fantasy": ("alien", "baroque", "brutalist", "gothic", "industrial", "medieval",
                          "none", "organic"),
         "urban_fantasy": ("art_nouveau", "baroque", "brutalist", "east_asian", "futuristic",
                           "gothic", "industrial", "none", "organic"),
         "fairy_tale": ("art_nouveau", "baroque", "east_asian", "gothic", "medieval", "none",
                        "organic"),
         "mythic": ("alien", "baroque", "classical", "east_asian", "gothic", "islamic",
                    "medieval", "none", "organic"),
         "historical": ("art_nouveau", "baroque", "classical", "east_asian", "gothic",
                        "industrial", "islamic", "medieval", "none"),
         "gothic": ("baroque", "gothic", "industrial", "medieval", "none"),
         "steampunk": ("art_nouveau", "baroque", "gothic", "industrial"),
         "dieselpunk": ("art_nouveau", "brutalist", "industrial"),
         "cyberpunk": ("brutalist", "east_asian", "futuristic", "industrial"),
         "solarpunk": ("art_nouveau", "futuristic", "none", "organic"),
         "space_opera": ("alien", "brutalist", "classical", "futuristic", "none", "organic"),
         "post_apocalyptic": ("brutalist", "futuristic", "industrial", "none", "organic")},
    ),
    _compatibility(
        "location", "architecture",
        {"castle": ("baroque", "classical", "east_asian", "gothic", "islamic", "medieval"),
         "throne_room": ("alien", "baroque", "classical", "east_asian", "futuristic",
                         "gothic", "islamic", "medieval"),
         "tavern": ("east_asian", "gothic", "industrial", "medieval"),
         "library": ("art_nouveau", "baroque", "east_asian", "futuristic", "gothic",
                     "industrial", "medieval"),
         "temple": ("alien", "baroque", "classical", "east_asian", "futuristic", "gothic",
                    "islamic", "medieval", "organic"),
         "market": ("art_nouveau", "baroque", "east_asian", "futuristic", "gothic",
                    "industrial", "islamic", "medieval", "organic"),
         "village": ("east_asian", "islamic", "medieval", "organic"),
         "city_street": ("alien", "art_nouveau", "baroque", "brutalist", "classical",
                         "east_asian", "futuristic", "gothic", "industrial", "islamic",
                         "medieval", "organic"),
         "alley": ("art_nouveau", "baroque", "brutalist", "east_asian", "futuristic",
                   "gothic", "industrial", "medieval"),
         "forest": ("alien", "none", "organic"),
         "ruins": ("alien", "baroque", "brutalist", "classical", "east_asian", "futuristic",
                   "gothic", "industrial", "islamic", "medieval", "organic"),
         "cave": ("alien", "none", "organic"),
         "coast": ("alien", "none", "organic"),
         "desert": ("alien", "none", "organic"),
         "mountain": ("alien", "none", "organic"),
         "swamp": ("alien", "none", "organic"),
         "floating_island": ("alien", "classical", "east_asian", "futuristic", "gothic",
                             "medieval", "none", "organic"),
         "underwater_city": ("alien", "art_nouveau", "baroque", "classical", "futuristic",
                             "organic"),
         "train_station": ("art_nouveau", "baroque", "brutalist", "east_asian", "futuristic",
                           "gothic", "industrial"),
         "laboratory": ("alien", "brutalist", "futuristic", "industrial", "organic"),
         "spaceship": ("alien", "brutalist", "futuristic", "industrial", "organic"),
         "alien_planet": ("alien", "futuristic", "none", "organic")},
    ),
    _compatibility(
        "location", "terrain",
        {"castle": ("cliffs", "flat", "jagged_mountains", "lake", "river", "rolling_hills"),
         "throne_room": ("interior",),
         "tavern": ("interior",),
         "library": ("interior",),
         "temple": ("cliffs", "flat", "interior", "jagged_mountains", "rolling_hills"),
         "market": ("flat", "interior", "rolling_hills"),
         "village": ("flat", "jagged_mountains", "lake", "river", "rolling_hills",
                     "wetlands"),
         "city_street": ("flat", "rolling_hills"),
         "alley": ("flat", "rolling_hills"),
         "forest": ("cliffs", "flat", "floating", "glacial", "jagged_mountains", "lake",
                    "river", "rolling_hills", "volcanic", "wetlands"),
         "ruins": ("cliffs", "dunes", "flat", "floating", "glacial", "jagged_mountains",
                   "lake", "river", "rolling_hills", "volcanic", "wetlands"),
         "cave": ("glacial", "interior", "volcanic"),
         "coast": ("cliffs", "dunes", "flat"),
         "desert": ("cliffs", "dunes", "flat", "rolling_hills"),
         "mountain": ("cliffs", "glacial", "jagged_mountains", "volcanic"),
         "swamp": ("lake", "river", "wetlands"),
         "floating_island": ("cliffs", "floating", "jagged_mountains"),
         "underwater_city": ("seafloor",),
         "train_station": ("flat", "interior"),
         "laboratory": ("interior",),
         "spaceship": ("interior",),
         "alien_planet": ("cliffs", "dunes", "flat", "floating", "glacial",
                          "jagged_mountains", "lake", "river", "rolling_hills", "volcanic",
                          "wetlands")},
    ),
    _compatibility(
        "location", "spatial_scale",
        {"throne_room": ("epic", "room"),
         "tavern": ("room", "small_space"),
         "library": ("epic", "room"),
         "laboratory": ("epic", "room"),
         "spaceship": ("district", "epic", "room"),
         "alley": ("small_space", "street"),
         "train_station": ("epic", "room", "street"),
         "city_street": ("city", "district", "street"),
         "village": ("district", "street")},
    ),
    _compatibility(
        "architecture", "environment_materials",
        {"medieval": ("brick", "marble", "metal", "overgrown", "stone", "wood"),
         "gothic": ("brick", "glass", "marble", "metal", "overgrown", "stone"),
         "baroque": ("glass", "marble", "metal", "stone", "wood"),
         "art_nouveau": ("brick", "glass", "metal", "wood"),
         "east_asian": ("brick", "stone", "wood"),
         "islamic": ("brick", "glass", "marble", "stone"),
         "classical": ("marble", "stone"),
         "industrial": ("brick", "concrete", "glass", "metal"),
         "brutalist": ("concrete", "glass", "metal", "stone"),
         "futuristic": ("concrete", "crystal", "glass", "metal"),
         "organic": ("bone", "crystal", "living_plants", "overgrown", "wood"),
         "alien": ("bone", "crystal", "glass", "living_plants", "metal"),
         "none": ("bone", "crystal", "living_plants", "overgrown", "stone", "wood")},
    ),
    _compatibility(
        "location", "weather",
        {"castle": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "village": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                     "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                     "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "city_street": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                         "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                         "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                         "storm", "wind"),
         "alley": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                   "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                   "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "forest": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "ruins": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                   "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                   "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "coast": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                   "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                   "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "desert": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "mountain": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                      "fog", "hail", "heat_haze", "lightning", "magical_rain",
                      "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                      "wind"),
         "swamp": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                   "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                   "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "floating_island": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                             "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                             "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                             "storm", "wind"),
         "alien_planet": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                          "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                          "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                          "storm", "wind"),
         "throne_room": ("indoor",),
         "tavern": ("indoor",),
         "library": ("indoor",),
         "temple": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "indoor", "lightning", "magical_rain",
                    "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                    "wind"),
         "market": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "indoor", "lightning", "magical_rain",
                    "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                    "wind"),
         "cave": ("indoor",),
         "laboratory": ("indoor",),
         "spaceship": ("indoor",),
         "underwater_city": ("underwater",),
         "train_station": ("clear", "cloudy", "drizzle", "fog", "indoor", "rain", "snow")},
    ),
    _compatibility(
        "terrain", "weather",
        {"flat": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                  "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                  "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "rolling_hills": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                           "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                           "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                           "storm", "wind"),
         "jagged_mountains": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                              "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                              "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                              "storm", "wind"),
         "cliffs": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "river": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                   "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                   "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "lake": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                  "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                  "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "wetlands": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                      "fog", "hail", "heat_haze", "lightning", "magical_rain",
                      "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                      "wind"),
         "floating": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                      "fog", "hail", "heat_haze", "lightning", "magical_rain",
                      "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                      "wind"),
         "interior": ("indoor",),
         "seafloor": ("underwater",),
         "dunes": ("clear", "cloudy", "dust", "magical_rain", "wind"),
         "glacial": ("aurora", "blizzard", "clear", "cloudy", "fog", "magical_rain", "snow",
                     "wind"),
         "volcanic": ("clear", "cloudy", "dust", "fog", "magical_rain", "rain", "storm",
                      "wind")},
    ),
    _compatibility(
        "season", "weather",
        {"spring": ("clear", "cloudy", "drizzle", "fog", "indoor", "magical_rain", "rain",
                    "storm", "underwater", "wind"),
         "summer": ("clear", "cloudy", "drizzle", "dust", "fog", "indoor", "magical_rain",
                    "rain", "storm", "underwater", "wind"),
         "autumn": ("clear", "cloudy", "drizzle", "dust", "fog", "indoor", "magical_rain",
                    "rain", "storm", "underwater", "wind"),
         "winter": ("aurora", "blizzard", "clear", "cloudy", "fog", "indoor", "magical_rain",
                    "snow", "underwater", "wind")},
    ),
    _compatibility(
        "time_of_day", "weather",
        {"dawn": ("blizzard", "clear", "cloudy", "drizzle", "dust", "fog", "indoor",
                  "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "morning": ("blizzard", "clear", "cloudy", "drizzle", "dust", "fog", "indoor",
                     "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "noon": ("blizzard", "clear", "cloudy", "drizzle", "dust", "fog", "indoor",
                  "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "afternoon": ("blizzard", "clear", "cloudy", "drizzle", "dust", "fog", "indoor",
                       "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "golden_hour": ("blizzard", "clear", "cloudy", "drizzle", "dust", "fog", "indoor",
                         "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "dusk": ("aurora", "blizzard", "clear", "cloudy", "drizzle", "dust", "fog", "indoor",
                  "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "night": ("aurora", "blizzard", "clear", "cloudy", "drizzle", "dust", "fog",
                   "indoor", "magical_rain", "rain", "snow", "storm", "underwater", "wind"),
         "midnight": ("aurora", "blizzard", "clear", "cloudy", "drizzle", "dust", "fog",
                      "indoor", "magical_rain", "rain", "snow", "storm", "underwater",
                      "wind")},
    ),
    _compatibility(
        "time_of_day", "background_lighting",
        {"dawn": ("backlit", "bioluminescent", "candlelit", "firelit", "high_contrast",
                  "soft_diffuse", "sunbeams", "volumetric"),
         "morning": ("backlit", "bioluminescent", "candlelit", "firelit", "high_contrast",
                     "neon", "soft_diffuse", "sunbeams", "volumetric"),
         "noon": ("backlit", "bioluminescent", "firelit", "high_contrast", "neon",
                  "soft_diffuse", "sunbeams", "volumetric"),
         "afternoon": ("backlit", "bioluminescent", "candlelit", "firelit", "high_contrast",
                       "neon", "soft_diffuse", "sunbeams", "volumetric"),
         "golden_hour": ("backlit", "bioluminescent", "candlelit", "firelit", "high_contrast",
                         "neon", "soft_diffuse", "sunbeams", "volumetric"),
         "night": ("backlit", "bioluminescent", "candlelit", "firelit", "high_contrast",
                   "moonlit", "neon", "soft_diffuse", "volumetric"),
         "midnight": ("backlit", "bioluminescent", "candlelit", "firelit", "high_contrast",
                      "moonlit", "neon", "soft_diffuse", "volumetric")},
    ),
    _compatibility(
        "weather", "atmosphere",
        {"clear": ("cozy", "dreamlike", "lonely", "peaceful", "romantic", "sacred"),
         "fog": ("desolate", "dreamlike", "lonely", "melancholic", "mysterious", "ominous",
                 "peaceful", "sacred"),
         "drizzle": ("cozy", "dreamlike", "lonely", "melancholic", "mysterious", "romantic"),
         "rain": ("chaotic", "cozy", "lonely", "melancholic", "mysterious", "ominous",
                  "romantic"),
         "storm": ("chaotic", "desolate", "mysterious", "ominous"),
         "snow": ("cozy", "desolate", "dreamlike", "lonely", "mysterious", "peaceful",
                  "sacred"),
         "blizzard": ("chaotic", "desolate", "lonely", "ominous"),
         "dust": ("chaotic", "desolate", "mysterious", "ominous"),
         "aurora": ("dreamlike", "mysterious", "peaceful", "romantic", "sacred"),
         "magical_rain": ("dreamlike", "mysterious", "romantic", "sacred"),
         "indoor": ("cozy", "dreamlike", "lonely", "mysterious", "ominous", "peaceful",
                    "romantic", "sacred"),
         "underwater": ("dreamlike", "lonely", "mysterious", "ominous", "peaceful", "sacred")},
    ),
    _compatibility(
        "background_style", "rendering",
        {"anime": ("concept_art", "illustration", "line_and_color", "stylized_3d"),
         "semi_realistic": ("concept_art", "illustration", "matte_painting", "photoreal",
                            "stylized_3d"),
         "realistic": ("concept_art", "matte_painting", "photoreal"),
         "painterly": ("concept_art", "illustration", "matte_painting"),
         "watercolor": ("illustration", "line_and_color"),
         "ink": ("illustration", "line_and_color"),
         "comic": ("illustration", "line_and_color"),
         "pixel_art": ("pixel_rendering",),
         "cinematic": ("concept_art", "matte_painting", "photoreal", "stylized_3d")},
    ),
    _compatibility(
        "hair_length", "hair_style",
        {"pixie": ("shag", "slicked_back", "wolf_cut"),
         "chin": ("afro", "lob", "shag", "slicked_back", "wolf_cut"),
         "shoulder": ("afro", "braided_ponytail", "dreadlocks", "fishtail_braid",
                      "french_braid", "half_up", "lob", "low_ponytail", "rope_braid", "shag",
                      "side_bun", "slicked_back", "topknot", "waterfall_braid", "wolf_cut"),
         "mid_back": ("braided_ponytail", "dreadlocks", "fishtail_braid", "french_braid",
                      "half_up", "low_ponytail", "rope_braid", "shag", "side_bun",
                      "slicked_back", "topknot", "waterfall_braid", "wolf_cut"),
         "waist": ("braided_ponytail", "dreadlocks", "fishtail_braid", "french_braid",
                   "half_up", "low_ponytail", "rope_braid", "side_bun", "slicked_back",
                   "topknot", "waterfall_braid"),
         "floor": ("braided_ponytail", "dreadlocks", "fishtail_braid", "french_braid",
                   "half_up", "low_ponytail", "rope_braid", "side_bun", "slicked_back",
                   "topknot", "waterfall_braid"),
         "ear": ("bob", "curly", "loose_straight", "loose_wavy", "messy", "shag",
                 "slicked_back", "undercut", "wolf_cut"),
         "neck": ("bob", "curly", "lob", "loose_straight", "loose_wavy", "messy", "shag",
                  "slicked_back", "wolf_cut"),
         "chest": ("braided_ponytail", "bun", "crown_braid", "curly", "double_braids",
                   "double_buns", "dreadlocks", "fishtail_braid", "french_braid", "half_up",
                   "high_ponytail", "loose_straight", "loose_wavy", "low_ponytail", "messy",
                   "ponytail", "rope_braid", "side_bun", "side_ponytail", "single_braid",
                   "topknot", "twin_tails", "waterfall_braid"),
         "hip": ("braided_ponytail", "bun", "crown_braid", "curly", "double_braids",
                 "dreadlocks", "fishtail_braid", "french_braid", "half_up", "high_ponytail",
                 "loose_straight", "loose_wavy", "low_ponytail", "ponytail", "rope_braid",
                 "side_bun", "single_braid", "topknot", "twin_tails", "waterfall_braid"),
         "ankle": ("braided_ponytail", "crown_braid", "curly", "double_braids",
                   "fishtail_braid", "french_braid", "half_up", "loose_straight",
                   "loose_wavy", "low_ponytail", "ponytail", "rope_braid", "single_braid",
                   "topknot", "twin_tails", "waterfall_braid")},
    ),
    _compatibility(
        "hair_length", "bangs",
        {"pixie": ("choppy", "long_side", "micro"),
         "chin": ("arched", "braided", "choppy", "long_side", "micro", "split"),
         "shoulder": ("arched", "braided", "choppy", "long_side", "micro", "split"),
         "mid_back": ("arched", "braided", "choppy", "long_side", "micro", "split"),
         "waist": ("arched", "braided", "choppy", "long_side", "micro", "split"),
         "floor": ("arched", "braided", "choppy", "long_side", "micro", "split"),
         "ear": ("choppy", "long_side", "micro", "none", "side_swept", "wispy"),
         "neck": ("choppy", "long_side", "micro", "none", "side_swept", "straight"),
         "chest": ("arched", "braided", "choppy", "curtain", "hime", "long_side", "micro",
                   "none", "side_swept", "split", "straight", "wispy"),
         "hip": ("arched", "braided", "choppy", "curtain", "hime", "long_side", "micro",
                 "none", "side_swept", "split", "straight", "wispy"),
         "ankle": ("arched", "braided", "choppy", "curtain", "hime", "long_side", "micro",
                   "none", "side_swept", "split", "straight", "wispy")},
    ),
    _compatibility(
        "hair_style", "bangs",
        {"slicked_back": ("long_side", "none")},
    ),
    _compatibility(
        "fantasy_race", "fantasy_traits",
        {"human": ("constellation_skin", "third_eye"),
         "elf": ("antlers", "constellation_skin", "third_eye"),
         "dark_elf": ("constellation_skin", "shadow_body", "third_eye"),
         "half_elf": ("constellation_skin", "third_eye"),
         "orc": ("stone_skin", "third_eye"),
         "goblin": ("third_eye",),
         "angel": ("constellation_skin",),
         "merfolk": ("fin_ears", "gills", "tentacle_hair"),
         "fairy": ("antlers", "constellation_skin"),
         "dragonkin": ("fin_ears", "gills", "stone_skin"),
         "automaton": ("stone_skin", "third_eye"),
         "android": ("constellation_skin", "third_eye"),
         "undead": ("shadow_body", "third_eye"),
         "elemental": ("antlers", "constellation_skin", "flame_hair", "shadow_body",
                       "stone_skin", "third_eye", "wooden_skin"),
         "aasimar": ("constellation_skin", "ethereal_aura", "feathered_wings",
                     "glowing_runes", "halo"),
         "oni": ("fangs", "glowing_runes", "horns", "stone_skin", "third_eye"),
         "dryad": ("antlers", "glowing_runes", "pointed_ears", "wooden_skin"),
         "djinn": ("constellation_skin", "ethereal_aura", "flame_hair", "shadow_body"),
         "shadowfolk": ("ethereal_aura", "glowing_runes", "shadow_body", "third_eye"),
         "slime_humanoid": ("ethereal_aura", "glowing_runes", "slime_body"),
         "living_doll": ("crystal_growths", "glowing_runes", "mechanical_limbs"),
         "golem": ("crystal_growths", "glowing_runes", "stone_skin", "wooden_skin"),
         "cyborg": ("glowing_runes", "mechanical_limbs", "third_eye"),
         "clone": ("constellation_skin", "mechanical_limbs"),
         "witchblood": ("ethereal_aura", "glowing_runes", "third_eye"),
         "fae_noble": ("antlers", "ethereal_aura", "fairy_wings", "pointed_ears"),
         "moonfolk": ("constellation_skin", "ethereal_aura", "third_eye"),
         "sunfolk": ("constellation_skin", "ethereal_aura", "flame_hair", "halo"),
         "voidborn": ("constellation_skin", "shadow_body", "tentacle_hair", "third_eye"),
         "giantkin": ("glowing_runes", "stone_skin"),
         "halfling": ("glowing_runes", "pointed_ears")},
    ),
    _compatibility(
        "hair_color", "hair_color_pattern",
        {"black": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "dark_brown": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                        "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                        "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                        "hair_pattern_marbled", "highlights", "horizontal_bands",
                        "inner_color", "iridescent_sheen", "money_piece", "ombre",
                        "rainbow_sections", "solid", "split_dye", "two_tone", "underlayer"),
         "brown": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "blonde": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "platinum": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                      "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                      "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                      "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                      "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                      "split_dye", "two_tone", "underlayer"),
         "silver": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "white": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "red": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                 "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                 "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                 "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                 "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                 "split_dye", "two_tone", "underlayer"),
         "auburn": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "pink": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                  "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                  "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                  "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                  "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                  "split_dye", "two_tone", "underlayer"),
         "blue": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                  "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                  "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                  "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                  "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                  "split_dye", "two_tone", "underlayer"),
         "teal": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                  "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                  "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                  "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                  "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                  "split_dye", "two_tone", "underlayer"),
         "green": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "purple": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "rose_gold": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                       "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                       "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                       "hair_pattern_marbled", "highlights", "horizontal_bands",
                       "inner_color", "iridescent_sheen", "money_piece", "ombre",
                       "rainbow_sections", "solid", "split_dye", "two_tone", "underlayer"),
         "lavender": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                      "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                      "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                      "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                      "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                      "split_dye", "two_tone", "underlayer"),
         "navy": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                  "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                  "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                  "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                  "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                  "split_dye", "two_tone", "underlayer"),
         "cyan": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                  "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                  "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                  "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                  "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                  "split_dye", "two_tone", "underlayer"),
         "orange": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "peach": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "mint": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                  "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                  "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                  "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                  "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                  "split_dye", "two_tone", "underlayer"),
         "burgundy": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                      "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                      "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                      "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                      "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                      "split_dye", "two_tone", "underlayer"),
         "charcoal": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                      "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                      "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                      "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                      "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                      "split_dye", "two_tone", "underlayer"),
         "chestnut": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                      "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                      "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                      "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                      "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                      "split_dye", "two_tone", "underlayer"),
         "honey_blonde": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                          "colored_roots", "colored_tips", "constellation_speckles",
                          "dip_dye", "fine_streaks", "flame_sections", "gradient",
                          "hair_pattern_halo_dye", "hair_pattern_marbled", "highlights",
                          "horizontal_bands", "inner_color", "iridescent_sheen",
                          "money_piece", "ombre", "rainbow_sections", "solid", "split_dye",
                          "two_tone", "underlayer"),
         "strawberry_blonde": ("balayage", "checkerboard_panels", "chunky_streaks",
                               "color_blocks", "colored_roots", "colored_tips",
                               "constellation_speckles", "dip_dye", "fine_streaks",
                               "flame_sections", "gradient", "hair_pattern_halo_dye",
                               "hair_pattern_marbled", "highlights", "horizontal_bands",
                               "inner_color", "iridescent_sheen", "money_piece", "ombre",
                               "rainbow_sections", "solid", "split_dye", "two_tone",
                               "underlayer"),
         "copper": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "scarlet": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                     "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                     "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                     "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                     "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                     "split_dye", "two_tone", "underlayer"),
         "cobalt": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                    "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                    "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                    "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                    "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                    "split_dye", "two_tone", "underlayer"),
         "emerald": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                     "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                     "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                     "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                     "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                     "split_dye", "two_tone", "underlayer"),
         "magenta": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                     "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                     "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                     "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                     "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                     "split_dye", "two_tone", "underlayer"),
         "lilac": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "ash_brown": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                       "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                       "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                       "hair_pattern_marbled", "highlights", "horizontal_bands",
                       "inner_color", "iridescent_sheen", "money_piece", "ombre",
                       "rainbow_sections", "solid", "split_dye", "two_tone", "underlayer"),
         "smoky_gray": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                        "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                        "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                        "hair_pattern_marbled", "highlights", "horizontal_bands",
                        "inner_color", "iridescent_sheen", "money_piece", "ombre",
                        "rainbow_sections", "solid", "split_dye", "two_tone", "underlayer"),
         "midnight_purple": ("balayage", "checkerboard_panels", "chunky_streaks",
                             "color_blocks", "colored_roots", "colored_tips",
                             "constellation_speckles", "dip_dye", "fine_streaks",
                             "flame_sections", "gradient", "hair_pattern_halo_dye",
                             "hair_pattern_marbled", "highlights", "horizontal_bands",
                             "inner_color", "iridescent_sheen", "money_piece", "ombre",
                             "rainbow_sections", "solid", "split_dye", "two_tone",
                             "underlayer"),
         "seafoam_green": ("balayage", "checkerboard_panels", "chunky_streaks",
                           "color_blocks", "colored_roots", "colored_tips",
                           "constellation_speckles", "dip_dye", "fine_streaks",
                           "flame_sections", "gradient", "hair_pattern_halo_dye",
                           "hair_pattern_marbled", "highlights", "horizontal_bands",
                           "inner_color", "iridescent_sheen", "money_piece", "ombre",
                           "rainbow_sections", "solid", "split_dye", "two_tone",
                           "underlayer"),
         "slate_blue": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                        "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                        "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                        "hair_pattern_marbled", "highlights", "horizontal_bands",
                        "inner_color", "iridescent_sheen", "money_piece", "ombre",
                        "rainbow_sections", "solid", "split_dye", "two_tone", "underlayer"),
         "champagne_blonde": ("balayage", "checkerboard_panels", "chunky_streaks",
                              "color_blocks", "colored_roots", "colored_tips",
                              "constellation_speckles", "dip_dye", "fine_streaks",
                              "flame_sections", "gradient", "hair_pattern_halo_dye",
                              "hair_pattern_marbled", "highlights", "horizontal_bands",
                              "inner_color", "iridescent_sheen", "money_piece", "ombre",
                              "rainbow_sections", "solid", "split_dye", "two_tone",
                              "underlayer"),
         "mauve": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                   "colored_roots", "colored_tips", "constellation_speckles", "dip_dye",
                   "fine_streaks", "flame_sections", "gradient", "hair_pattern_halo_dye",
                   "hair_pattern_marbled", "highlights", "horizontal_bands", "inner_color",
                   "iridescent_sheen", "money_piece", "ombre", "rainbow_sections", "solid",
                   "split_dye", "two_tone", "underlayer"),
         "forest_green": ("balayage", "checkerboard_panels", "chunky_streaks", "color_blocks",
                          "colored_roots", "colored_tips", "constellation_speckles",
                          "dip_dye", "fine_streaks", "flame_sections", "gradient",
                          "hair_pattern_halo_dye", "hair_pattern_marbled", "highlights",
                          "horizontal_bands", "inner_color", "iridescent_sheen",
                          "money_piece", "ombre", "rainbow_sections", "solid", "split_dye",
                          "two_tone", "underlayer")},
    ),
    _compatibility(
        "eye_color", "iris_color_pattern",
        {"brown": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                   "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                   "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                   "void_center"),
         "amber": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                   "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                   "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                   "void_center"),
         "hazel": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                   "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                   "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                   "void_center"),
         "green": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                   "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                   "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                   "void_center"),
         "blue": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                  "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                  "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                  "void_center"),
         "gray": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                  "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                  "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                  "void_center"),
         "violet": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "red": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                 "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                 "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                 "void_center"),
         "gold": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                  "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                  "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                  "void_center"),
         "silver": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "black": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                   "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                   "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                   "void_center"),
         "cyan": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                  "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                  "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                  "void_center"),
         "turquoise": ("central_heterochromia", "complete_heterochromia", "concentric",
                       "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                       "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                       "starburst", "starry", "void_center"),
         "pink": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                  "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                  "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                  "void_center"),
         "orange": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "white": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                   "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                   "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                   "void_center"),
         "cobalt": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "indigo": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "emerald": ("central_heterochromia", "complete_heterochromia", "concentric",
                     "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                     "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                     "starry", "void_center"),
         "lime": ("central_heterochromia", "complete_heterochromia", "concentric", "faceted",
                  "glowing", "gradient", "limbal_ring", "mechanical", "rainbow", "rune_ring",
                  "sectoral_heterochromia", "solid", "speckled", "starburst", "starry",
                  "void_center"),
         "copper": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "bronze": ("central_heterochromia", "complete_heterochromia", "concentric",
                    "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                    "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                    "starry", "void_center"),
         "rose_gold": ("central_heterochromia", "complete_heterochromia", "concentric",
                       "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                       "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                       "starburst", "starry", "void_center"),
         "lavender": ("central_heterochromia", "complete_heterochromia", "concentric",
                      "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                      "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                      "starburst", "starry", "void_center"),
         "magenta": ("central_heterochromia", "complete_heterochromia", "concentric",
                     "faceted", "glowing", "gradient", "limbal_ring", "mechanical", "rainbow",
                     "rune_ring", "sectoral_heterochromia", "solid", "speckled", "starburst",
                     "starry", "void_center"),
         "ice_blue": ("central_heterochromia", "complete_heterochromia", "concentric",
                      "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                      "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                      "starburst", "starry", "void_center"),
         "eye_teal": ("central_heterochromia", "complete_heterochromia", "concentric",
                      "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                      "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                      "starburst", "starry", "void_center"),
         "eye_honey_gold": ("central_heterochromia", "complete_heterochromia", "concentric",
                            "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                            "rainbow", "rune_ring", "sectoral_heterochromia", "solid",
                            "speckled", "starburst", "starry", "void_center"),
         "aquamarine": ("central_heterochromia", "complete_heterochromia", "concentric",
                        "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                        "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                        "starburst", "starry", "void_center"),
         "smoky_quartz": ("central_heterochromia", "complete_heterochromia", "concentric",
                          "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                          "rainbow", "rune_ring", "sectoral_heterochromia", "solid",
                          "speckled", "starburst", "starry", "void_center"),
         "moonstone_blue": ("central_heterochromia", "complete_heterochromia", "concentric",
                            "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                            "rainbow", "rune_ring", "sectoral_heterochromia", "solid",
                            "speckled", "starburst", "starry", "void_center"),
         "garnet_red": ("central_heterochromia", "complete_heterochromia", "concentric",
                        "faceted", "glowing", "gradient", "limbal_ring", "mechanical",
                        "rainbow", "rune_ring", "sectoral_heterochromia", "solid", "speckled",
                        "starburst", "starry", "void_center")},
    ),
    _compatibility(
        "outfit_archetype", "outfit_materials",
        {"swimwear": ("mesh", "silk")},
    ),
    _compatibility(
        "framing", "pose",
        {"extreme_closeup": ("looking_back", "seated", "standing"),
         "turnaround_sheet": ("standing", "weapon_ready")},
    ),
    _compatibility(
        "eyewear", "distinctive_marks",
        {"round": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                   "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                   "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                   "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                   "mark_calloused_hands", "mark_constellation_freckles",
                   "mark_lightning_scar", "mark_prosthetic_eye", "mark_ritual_scarification",
                   "mark_stretch_lines", "metallic_makeup", "nose_piercing", "nose_scar",
                   "pierced_ears", "ritual_brand", "smoky_makeup", "surgical_scars", "tattoo",
                   "vitiligo", "war_paint"),
         "square": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                    "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                    "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                    "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                    "mark_calloused_hands", "mark_constellation_freckles",
                    "mark_lightning_scar", "mark_prosthetic_eye", "mark_ritual_scarification",
                    "mark_stretch_lines", "metallic_makeup", "nose_piercing", "nose_scar",
                    "pierced_ears", "ritual_brand", "smoky_makeup", "surgical_scars",
                    "tattoo", "vitiligo", "war_paint"),
         "rectangular": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                         "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                         "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                         "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                         "mark_calloused_hands", "mark_constellation_freckles",
                         "mark_lightning_scar", "mark_prosthetic_eye",
                         "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                         "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                         "smoky_makeup", "surgical_scars", "tattoo", "vitiligo",
                         "war_paint"),
         "oval": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                  "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                  "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                  "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                  "mark_calloused_hands", "mark_constellation_freckles",
                  "mark_lightning_scar", "mark_prosthetic_eye", "mark_ritual_scarification",
                  "mark_stretch_lines", "metallic_makeup", "nose_piercing", "nose_scar",
                  "pierced_ears", "ritual_brand", "smoky_makeup", "surgical_scars", "tattoo",
                  "vitiligo", "war_paint"),
         "cat_eye": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                     "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                     "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                     "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                     "mark_calloused_hands", "mark_constellation_freckles",
                     "mark_lightning_scar", "mark_prosthetic_eye",
                     "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                     "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                     "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "aviator": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                     "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                     "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                     "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                     "mark_calloused_hands", "mark_constellation_freckles",
                     "mark_lightning_scar", "mark_prosthetic_eye",
                     "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                     "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                     "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "rimless": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                     "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                     "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                     "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                     "mark_calloused_hands", "mark_constellation_freckles",
                     "mark_lightning_scar", "mark_prosthetic_eye",
                     "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                     "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                     "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "half_rim": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                      "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                      "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                      "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                      "mark_calloused_hands", "mark_constellation_freckles",
                      "mark_lightning_scar", "mark_prosthetic_eye",
                      "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                      "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                      "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "wire_frame": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                        "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                        "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                        "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                        "mark_calloused_hands", "mark_constellation_freckles",
                        "mark_lightning_scar", "mark_prosthetic_eye",
                        "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                        "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                        "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "thick_frame": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                         "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                         "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                         "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                         "mark_calloused_hands", "mark_constellation_freckles",
                         "mark_lightning_scar", "mark_prosthetic_eye",
                         "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                         "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                         "smoky_makeup", "surgical_scars", "tattoo", "vitiligo",
                         "war_paint"),
         "reading": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                     "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                     "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                     "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                     "mark_calloused_hands", "mark_constellation_freckles",
                     "mark_lightning_scar", "mark_prosthetic_eye",
                     "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                     "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                     "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "sunglasses": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                        "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                        "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                        "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                        "mark_calloused_hands", "mark_constellation_freckles",
                        "mark_lightning_scar", "mark_prosthetic_eye",
                        "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                        "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                        "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "tinted": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                    "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                    "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                    "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                    "mark_calloused_hands", "mark_constellation_freckles",
                    "mark_lightning_scar", "mark_prosthetic_eye", "mark_ritual_scarification",
                    "mark_stretch_lines", "metallic_makeup", "nose_piercing", "nose_scar",
                    "pierced_ears", "ritual_brand", "smoky_makeup", "surgical_scars",
                    "tattoo", "vitiligo", "war_paint"),
         "goggles": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                     "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                     "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                     "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                     "mark_calloused_hands", "mark_constellation_freckles",
                     "mark_lightning_scar", "mark_prosthetic_eye",
                     "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                     "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                     "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "steampunk_goggles": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                               "constellation_tattoo", "cyber_lines", "dimples",
                               "facial_runes", "facial_scar", "freckles", "glowing_freckles",
                               "glowing_veins", "golden_cracks", "ink_stained_hands",
                               "lip_piercing", "lip_scar", "mark_calloused_hands",
                               "mark_constellation_freckles", "mark_lightning_scar",
                               "mark_prosthetic_eye", "mark_ritual_scarification",
                               "mark_stretch_lines", "metallic_makeup", "nose_piercing",
                               "nose_scar", "pierced_ears", "ritual_brand", "smoky_makeup",
                               "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "monocle": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                     "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                     "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                     "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                     "mark_calloused_hands", "mark_constellation_freckles",
                     "mark_lightning_scar", "mark_prosthetic_eye",
                     "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                     "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                     "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "eyepatch": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                      "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                      "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                      "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                      "mark_calloused_hands", "mark_constellation_freckles",
                      "mark_lightning_scar", "mark_prosthetic_eye",
                      "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                      "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                      "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "visor": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                   "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                   "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                   "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                   "mark_calloused_hands", "mark_constellation_freckles",
                   "mark_lightning_scar", "mark_prosthetic_eye", "mark_ritual_scarification",
                   "mark_stretch_lines", "metallic_makeup", "nose_piercing", "nose_scar",
                   "pierced_ears", "ritual_brand", "smoky_makeup", "surgical_scars", "tattoo",
                   "vitiligo", "war_paint"),
         "blindfold": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                       "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                       "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                       "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                       "mark_calloused_hands", "mark_constellation_freckles",
                       "mark_lightning_scar", "mark_prosthetic_eye",
                       "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                       "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                       "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "pince_nez": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                       "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                       "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                       "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                       "mark_calloused_hands", "mark_constellation_freckles",
                       "mark_lightning_scar", "mark_prosthetic_eye",
                       "mark_ritual_scarification", "mark_stretch_lines", "metallic_makeup",
                       "nose_piercing", "nose_scar", "pierced_ears", "ritual_brand",
                       "smoky_makeup", "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "hexagonal_frame": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                             "constellation_tattoo", "cyber_lines", "dimples", "facial_runes",
                             "facial_scar", "freckles", "glowing_freckles", "glowing_veins",
                             "golden_cracks", "ink_stained_hands", "lip_piercing", "lip_scar",
                             "mark_calloused_hands", "mark_constellation_freckles",
                             "mark_lightning_scar", "mark_prosthetic_eye",
                             "mark_ritual_scarification", "mark_stretch_lines",
                             "metallic_makeup", "nose_piercing", "nose_scar", "pierced_ears",
                             "ritual_brand", "smoky_makeup", "surgical_scars", "tattoo",
                             "vitiligo", "war_paint"),
         "wraparound_sport": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                              "constellation_tattoo", "cyber_lines", "dimples",
                              "facial_runes", "facial_scar", "freckles", "glowing_freckles",
                              "glowing_veins", "golden_cracks", "ink_stained_hands",
                              "lip_piercing", "lip_scar", "mark_calloused_hands",
                              "mark_constellation_freckles", "mark_lightning_scar",
                              "mark_prosthetic_eye", "mark_ritual_scarification",
                              "mark_stretch_lines", "metallic_makeup", "nose_piercing",
                              "nose_scar", "pierced_ears", "ritual_brand", "smoky_makeup",
                              "surgical_scars", "tattoo", "vitiligo", "war_paint"),
         "slit_snow_goggles": ("beauty_mark", "birthmark", "body_scar", "burn_scar",
                               "constellation_tattoo", "cyber_lines", "dimples",
                               "facial_runes", "facial_scar", "freckles", "glowing_freckles",
                               "glowing_veins", "golden_cracks", "ink_stained_hands",
                               "lip_piercing", "lip_scar", "mark_calloused_hands",
                               "mark_constellation_freckles", "mark_lightning_scar",
                               "mark_prosthetic_eye", "mark_ritual_scarification",
                               "mark_stretch_lines", "metallic_makeup", "nose_piercing",
                               "nose_scar", "pierced_ears", "ritual_brand", "smoky_makeup",
                               "surgical_scars", "tattoo", "vitiligo", "war_paint")},
    ),
    _compatibility(
        "world_genre", "location",
        {"wuxia": ("bamboo_grove", "city_street", "forest", "market", "mountain",
                   "palace_garden", "tavern", "temple", "village"),
         "xianxia": ("bamboo_grove", "cave", "floating_island", "forest", "mountain",
                     "palace_garden", "temple", "waterfall"),
         "biopunk": ("alien_planet", "archive", "city_street", "greenhouse", "laboratory",
                     "megacity_rooftop"),
         "clockpunk": ("airship", "city_street", "library", "observatory", "train_station",
                       "workshop"),
         "atompunk": ("archive", "city_street", "laboratory", "observatory", "spaceship",
                      "train_station"),
         "arcanepunk": ("airship", "city_street", "laboratory", "market", "megacity_rooftop",
                        "observatory", "workshop"),
         "gaslamp": ("alley", "archive", "cathedral", "city_street", "library", "observatory",
                     "train_station"),
         "nautical_fantasy": ("airship", "coast", "harbor", "tavern", "underwater_city",
                              "waterfall"),
         "cosmic_horror": ("alien_planet", "archive", "cave", "laboratory", "observatory",
                           "ruins"),
         "cozy_fantasy": ("forest", "greenhouse", "hot_spring", "market", "tavern", "village",
                          "workshop")},
    ),
    _compatibility(
        "world_genre", "architecture",
        {"wuxia": ("east_asian", "none", "vernacular"),
         "xianxia": ("crystalline", "east_asian", "none", "organic"),
         "biopunk": ("alien", "biomechanical", "futuristic", "organic"),
         "clockpunk": ("art_nouveau", "industrial", "victorian"),
         "atompunk": ("art_deco", "brutalist", "futuristic", "industrial"),
         "arcanepunk": ("art_nouveau", "crystalline", "futuristic", "gothic", "industrial"),
         "gaslamp": ("art_nouveau", "gothic", "industrial", "victorian"),
         "nautical_fantasy": ("industrial", "medieval", "none", "organic", "vernacular"),
         "cosmic_horror": ("alien", "biomechanical", "brutalist", "gothic", "none"),
         "cozy_fantasy": ("east_asian", "medieval", "none", "organic", "vernacular")},
    ),
    _compatibility(
        "location", "architecture",
        {"palace_garden": ("baroque", "classical", "east_asian", "islamic", "renaissance",
                           "rococo"),
         "cathedral": ("baroque", "byzantine", "gothic", "renaissance"),
         "observatory": ("art_nouveau", "classical", "crystalline", "futuristic", "gothic",
                         "industrial"),
         "workshop": ("futuristic", "industrial", "medieval", "vernacular", "victorian"),
         "greenhouse": ("art_nouveau", "biomechanical", "futuristic", "organic", "victorian"),
         "archive": ("brutalist", "futuristic", "gothic", "industrial"),
         "harbor": ("futuristic", "industrial", "medieval", "vernacular", "victorian"),
         "waterfall": ("crystalline", "none", "organic"),
         "canyon": ("alien", "none", "organic"),
         "tundra": ("alien", "none", "organic"),
         "bamboo_grove": ("east_asian", "none", "organic"),
         "hot_spring": ("east_asian", "none", "organic", "vernacular"),
         "battlefield": ("futuristic", "industrial", "medieval", "none"),
         "airship": ("futuristic", "industrial", "victorian"),
         "megacity_rooftop": ("art_deco", "brutalist", "futuristic", "industrial")},
    ),
    _compatibility(
        "location", "terrain",
        {"palace_garden": ("flat", "lake", "river", "rolling_hills"),
         "cathedral": ("interior",),
         "observatory": ("interior", "jagged_mountains", "plateau", "urban"),
         "workshop": ("interior", "urban"),
         "greenhouse": ("interior",),
         "archive": ("interior",),
         "harbor": ("flat", "urban"),
         "waterfall": ("cliffs", "river", "rolling_hills"),
         "canyon": ("badlands", "cliffs", "river"),
         "tundra": ("flat", "glacial", "plateau"),
         "bamboo_grove": ("giant_mushrooms", "river", "rolling_hills"),
         "hot_spring": ("jagged_mountains", "rolling_hills", "volcanic"),
         "battlefield": ("badlands", "flat", "rolling_hills", "urban"),
         "airship": ("cloud_sea", "interior"),
         "megacity_rooftop": ("urban",)},
    ),
    _compatibility(
        "location", "spatial_scale",
        {"palace_garden": ("courtyard", "district", "landscape"),
         "cathedral": ("epic", "room"),
         "observatory": ("epic", "room"),
         "workshop": ("room", "small_space"),
         "greenhouse": ("district", "room"),
         "archive": ("district", "room"),
         "harbor": ("city", "district", "landscape"),
         "waterfall": ("epic", "landscape"),
         "canyon": ("epic", "landscape"),
         "tundra": ("epic", "landscape"),
         "airship": ("district", "epic", "room"),
         "megacity_rooftop": ("city", "epic", "street")},
    ),
    _compatibility(
        "location", "weather",
        {"cathedral": ("indoor",),
         "workshop": ("indoor",),
         "greenhouse": ("indoor",),
         "archive": ("indoor",),
         "airship": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                     "fog", "hail", "heat_haze", "indoor", "lightning", "magical_rain",
                     "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                     "wind"),
         "palace_garden": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                           "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                           "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                           "storm", "wind"),
         "observatory": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                         "dust", "fog", "hail", "heat_haze", "indoor", "lightning",
                         "magical_rain", "meteor_shower", "pollen", "rain", "rainbow",
                         "sleet", "snow", "storm", "wind"),
         "harbor": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "waterfall": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                       "fog", "hail", "heat_haze", "lightning", "magical_rain",
                       "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                       "wind"),
         "canyon": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "tundra": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                    "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                    "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "bamboo_grove": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                          "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                          "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                          "storm", "wind"),
         "hot_spring": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                        "fog", "hail", "heat_haze", "lightning", "magical_rain",
                        "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                        "storm", "wind"),
         "battlefield": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                         "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                         "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                         "storm", "wind"),
         "megacity_rooftop": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                              "dust", "fog", "hail", "heat_haze", "lightning", "magical_rain",
                              "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow",
                              "storm", "wind")},
    ),
    _compatibility(
        "architecture", "environment_materials",
        {"renaissance": ("brick", "marble", "plaster", "stone", "wood"),
         "rococo": ("fabric", "glass", "marble", "plaster", "wood"),
         "victorian": ("brass", "brick", "copper", "glass", "wood"),
         "byzantine": ("brick", "glass", "marble", "stone"),
         "art_deco": ("brass", "concrete", "glass", "marble", "metal"),
         "vernacular": ("brick", "plaster", "stone", "terracotta", "wood"),
         "crystalline": ("crystal", "glass", "ice"),
         "biomechanical": ("biomass", "bone", "living_plants", "metal")},
    ),
    _compatibility(
        "terrain", "weather",
        {"plateau": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                     "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                     "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "badlands": ("ashfall", "clear", "cloudy", "dust", "heat_haze", "wind"),
         "rice_terraces": ("clear", "cloudy", "drizzle", "fog", "rain", "rainbow", "wind"),
         "coral_reef": ("underwater",),
         "urban": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                   "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                   "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "cloud_sea": ("aurora", "clear", "cloudy", "fog", "meteor_shower", "wind"),
         "giant_mushrooms": ("clear", "cloudy", "drizzle", "fog", "pollen", "rain"),
         "salt_flat": ("clear", "cloudy", "dust", "heat_haze", "meteor_shower", "wind")},
    ),
    _compatibility(
        "season", "weather",
        {"spring": ("hail", "pollen", "rainbow"),
         "summer": ("heat_haze", "lightning", "meteor_shower", "pollen", "rainbow"),
         "autumn": ("ashfall", "hail", "lightning", "meteor_shower", "rainbow"),
         "winter": ("hail", "lightning", "meteor_shower", "sleet"),
         "early_spring": ("clear", "cloudy", "drizzle", "fog", "hail", "indoor", "pollen",
                          "rain", "rainbow", "sleet", "underwater"),
         "rainy_season": ("cloudy", "drizzle", "fog", "indoor", "lightning", "rain",
                          "rainbow", "storm", "underwater"),
         "dry_season": ("ashfall", "clear", "cloudy", "dust", "heat_haze", "indoor",
                        "underwater", "wind"),
         "eternal_winter": ("aurora", "blizzard", "clear", "cloudy", "fog", "hail", "indoor",
                            "sleet", "snow", "underwater")},
    ),
    _compatibility(
        "time_of_day", "weather",
        {"dawn": ("ashfall", "hail", "heat_haze", "lightning", "pollen", "rainbow", "sleet"),
         "morning": ("ashfall", "hail", "heat_haze", "lightning", "pollen", "rainbow",
                     "sleet"),
         "noon": ("ashfall", "hail", "heat_haze", "lightning", "pollen", "rainbow", "sleet"),
         "afternoon": ("ashfall", "hail", "heat_haze", "lightning", "pollen", "rainbow",
                       "sleet"),
         "golden_hour": ("ashfall", "hail", "heat_haze", "lightning", "pollen", "rainbow",
                         "sleet"),
         "dusk": ("ashfall", "hail", "heat_haze", "lightning", "meteor_shower", "pollen",
                  "rainbow", "sleet"),
         "night": ("ashfall", "hail", "heat_haze", "lightning", "meteor_shower", "pollen",
                   "rainbow", "sleet"),
         "midnight": ("ashfall", "hail", "heat_haze", "lightning", "meteor_shower", "pollen",
                      "rainbow", "sleet"),
         "blue_hour": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                       "fog", "hail", "heat_haze", "lightning", "magical_rain",
                       "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                       "wind"),
         "twilight": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                      "fog", "hail", "heat_haze", "lightning", "magical_rain",
                      "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                      "wind"),
         "pre_dawn": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                      "fog", "hail", "heat_haze", "lightning", "magical_rain",
                      "meteor_shower", "pollen", "rain", "rainbow", "sleet", "snow", "storm",
                      "wind"),
         "eclipse": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle", "dust",
                     "fog", "hail", "heat_haze", "lightning", "magical_rain", "meteor_shower",
                     "pollen", "rain", "rainbow", "sleet", "snow", "storm", "wind"),
         "timeless_light": ("ashfall", "aurora", "blizzard", "clear", "cloudy", "drizzle",
                            "dust", "fog", "hail", "heat_haze", "indoor", "lightning",
                            "magical_rain", "meteor_shower", "pollen", "rain", "rainbow",
                            "sleet", "snow", "storm", "underwater", "wind")},
    ),
    _compatibility(
        "time_of_day", "background_lighting",
        {"blue_hour": ("backlit", "god_rays", "lanterns", "magic_crystals", "moonlit",
                       "soft_diffuse", "starlight", "underwater_caustics", "window_light"),
         "twilight": ("backlit", "eclipse_rim", "god_rays", "lanterns", "magic_crystals",
                      "moonlit", "soft_diffuse", "starlight"),
         "pre_dawn": ("bioluminescent", "lanterns", "magic_crystals", "moonlit",
                      "soft_diffuse", "starlight", "window_light"),
         "eclipse": ("backlit", "eclipse_rim", "electrical_arcs", "high_contrast",
                     "magic_crystals", "volumetric"),
         "timeless_light": ("backlit", "bioluminescent", "candlelit", "dawn_rim",
                            "eclipse_rim", "electrical_arcs", "firelit", "fluorescent_panels",
                            "gaslight", "god_rays", "high_contrast", "high_floodlights",
                            "lanterns", "lava_glow", "lightning_flash", "magic_crystals",
                            "moonlit", "neon", "overcast_skylight", "phosphorescent",
                            "projector", "searchlights", "sodium_vapor", "soft_diffuse",
                            "stained_glass", "starlight", "string_lights", "sunbeams",
                            "underwater_caustics", "volumetric", "water_reflection",
                            "window_light")},
    ),
    _compatibility(
        "weather", "atmosphere",
        {"hail": ("adventurous", "chaotic", "ominous", "tense"),
         "sleet": ("desolate", "lonely", "melancholic", "tense"),
         "heat_haze": ("adventurous", "desolate", "oppressive", "tense"),
         "lightning": ("chaotic", "ominous", "tense", "triumphant"),
         "meteor_shower": ("dreamlike", "mysterious", "romantic", "sacred", "triumphant"),
         "ashfall": ("desolate", "ominous", "oppressive", "uncanny"),
         "pollen": ("dreamlike", "peaceful", "romantic", "whimsical"),
         "rainbow": ("festive", "peaceful", "romantic", "triumphant", "whimsical")},
    ),
    _compatibility(
        "background_style", "rendering",
        {"watercolor": ("watercolor_rendering",),
         "ink": ("ink_rendering",),
         "pixel_art": ("voxel",),
         "storybook": ("illustration", "line_and_color", "watercolor_rendering"),
         "gouache": ("illustration", "matte_painting"),
         "oil_painting": ("illustration", "matte_painting"),
         "woodblock": ("ink_rendering", "line_and_color"),
         "retro_anime": ("illustration", "line_and_color"),
         "low_poly": ("stylized_3d", "voxel"),
         "miniature": ("miniature_render", "stylized_3d"),
         "architectural_sketch": ("blueprint", "cutaway", "line_and_color")},
    ),
    _compatibility(
        "fantasy_race", "fantasy_traits",
        {"centaur": ("equine_lower_body", "hooves", "pointed_ears", "tail"),
         "minotaur": ("bovine_horns", "fur", "hooves", "tail"),
         "satyr": ("fur", "goat_horns", "hooves", "pointed_ears", "tail"),
         "harpy": ("avian_talons", "beak", "bird_feathers", "feathered_wings"),
         "naga": ("fangs", "fin_ears", "scales", "serpentine_lower_body"),
         "kitsune": ("ethereal_aura", "fangs", "fox_ears", "fur", "multiple_tails"),
         "tengu": ("avian_talons", "beak", "bird_feathers", "feathered_wings"),
         "phoenixkin": ("avian_talons", "bird_feathers", "ethereal_aura", "flame_hair",
                        "phoenix_wings"),
         "plantfolk": ("body_flowers", "living_vines", "wooden_skin"),
         "mushroomfolk": ("mushroom_cap", "spore_cloud", "wooden_skin"),
         "crystalborn": ("crystal_body", "crystal_growths", "glowing_runes"),
         "celestial": ("constellation_skin", "ethereal_aura", "feathered_wings", "halo"),
         "eldritch": ("extra_arms", "eye_cluster", "shadow_body", "tentacle_hair",
                      "third_eye"),
         "spiderfolk": ("chitin", "extra_arms", "eye_cluster", "spider_lower_body",
                        "web_spinnerets"),
         "deepfolk": ("bioluminescent_patterns", "fin_ears", "gills", "scales",
                      "tentacle_hair")},
    ),
    _compatibility(
        "hair_length", "hair_style",
        {"pixie": ("bixie", "mullet", "pompadour", "quiff"),
         "chin": ("bixie", "mullet", "pageboy", "pompadour", "quiff", "victory_rolls"),
         "shoulder": ("box_braids", "bubble_ponytail", "cornrows", "halo_braid", "locs_updo",
                      "mullet", "pageboy", "pompadour", "quiff", "space_buns",
                      "victory_rolls"),
         "mid_back": ("box_braids", "bubble_ponytail", "cornrows", "halo_braid", "locs_updo",
                      "space_buns", "victory_rolls"),
         "waist": ("box_braids", "bubble_ponytail", "cornrows", "halo_braid", "locs_updo",
                   "space_buns"),
         "floor": ("box_braids", "bubble_ponytail", "halo_braid", "locs_updo"),
         "cheek": ("bixie", "bob", "curly", "loose_straight", "loose_wavy", "mullet",
                   "pageboy", "pompadour", "quiff", "shag", "wolf_cut"),
         "lower_back": ("box_braids", "bubble_ponytail", "curly", "double_braids",
                        "fishtail_braid", "french_braid", "high_ponytail", "locs_updo",
                        "loose_straight", "loose_wavy", "ponytail", "rope_braid",
                        "single_braid", "twin_tails", "waterfall_braid"),
         "calf": ("box_braids", "bubble_ponytail", "curly", "double_braids", "fishtail_braid",
                  "french_braid", "loose_straight", "loose_wavy", "ponytail", "rope_braid",
                  "single_braid", "twin_tails")},
    ),
    _compatibility(
        "hair_length", "bangs",
        {"pixie": ("asymmetrical", "bottleneck", "curly", "feathered", "side_piece"),
         "chin": ("asymmetrical", "bottleneck", "curly", "feathered", "side_piece", "veil"),
         "shoulder": ("asymmetrical", "bottleneck", "curly", "feathered", "side_piece",
                      "veil"),
         "mid_back": ("asymmetrical", "bottleneck", "curly", "feathered", "side_piece",
                      "veil"),
         "waist": ("asymmetrical", "curly", "feathered", "side_piece", "veil"),
         "floor": ("asymmetrical", "curly", "feathered", "side_piece", "veil"),
         "cheek": ("asymmetrical", "bottleneck", "choppy", "curly", "feathered", "micro",
                   "none", "side_piece", "side_swept", "straight", "wispy"),
         "lower_back": ("arched", "asymmetrical", "bangs_deep_side_part", "bangs_v_shaped",
                        "bottleneck", "braided", "choppy", "curly", "curtain", "feathered",
                        "hime", "long_side", "micro", "none", "side_piece", "side_swept",
                        "split", "straight", "veil", "wispy"),
         "calf": ("arched", "asymmetrical", "bangs_deep_side_part", "bangs_v_shaped",
                  "bottleneck", "braided", "choppy", "curly", "curtain", "feathered", "hime",
                  "long_side", "micro", "none", "side_piece", "side_swept", "split",
                  "straight", "veil", "wispy")},
    ),
    _compatibility(
        "hair_style", "bangs",
        {"pompadour": ("none", "side_piece"),
         "quiff": ("none", "side_piece", "side_swept"),
         "cornrows": ("none",),
         "halo_braid": ("none", "side_piece", "wispy")},
    ),
    _compatibility(
        "background_style", "rendering",
        {"pastel_chalk": ("illustration", "pastel_rendering"),
         "dark_academia": ("concept_art", "illustration", "matte_painting"),
         "synthwave": ("cel_rendering", "illustration", "stylized_3d"),
         "papercut": ("paper_cut_rendering",),
         "linocut": ("ink_rendering", "linocut_rendering"),
         "ukiyo_e": ("ink_rendering", "line_and_color", "linocut_rendering"),
         "surreal_collage": ("collage_rendering",),
         "scientific_plate": ("line_and_color", "scientific_rendering")},
    ),
)


_MUTUAL_EXCLUSION_RULES: Final[tuple[_MutualExclusionRule, ...]] = (
    _MutualExclusionRule("makeup", (
        ("no_makeup", "natural"),
        ("no_makeup", "winged_eyeliner"),
        ("no_makeup", "smoky_eyes"),
        ("no_makeup", "cut_crease"),
        ("no_makeup", "glitter"),
        ("no_makeup", "graphic_liner"),
        ("no_makeup", "blush"),
        ("no_makeup", "freckle_makeup"),
        ("no_makeup", "red_lip"),
        ("no_makeup", "dark_lip"),
        ("no_makeup", "gradient_lip"),
        ("no_makeup", "metallic_lip"),
        ("no_makeup", "face_gems"),
        ("no_makeup", "festival_paint"),
        ("no_makeup", "gothic"),
        ("no_makeup", "stage"),
        ("no_makeup", "cyber"),
        ("no_makeup", "fantasy_runes"),
        ("no_makeup", "tear_streaks"),
        ("no_makeup", "pearl_accent_makeup"),
        ("no_makeup", "color_block_makeup"),
        ("no_makeup", "sun_kissed_makeup"),
        ("no_makeup", "porcelain_doll_makeup"),
    )),
    _MutualExclusionRule("fantasy_traits", (
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
    )),
    _MutualExclusionRule("history_and_decay", (
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
        ("pristine", "salt_eroded"),
        ("pristine", "soot_stained"),
        ("pristine", "petrified"),
        ("pristine", "vine_reclaimed"),
        ("pristine", "excavated"),
        ("pristine", "storm_damaged"),
        ("pristine", "unfinished"),
        ("pristine", "ritual_stained"),
        ("newly_built", "salt_eroded"),
        ("newly_built", "soot_stained"),
        ("newly_built", "petrified"),
        ("newly_built", "vine_reclaimed"),
        ("newly_built", "excavated"),
        ("newly_built", "storm_damaged"),
        ("newly_built", "unfinished"),
        ("newly_built", "ritual_stained"),
        ("restored", "storm_damaged"),
        ("restored", "unfinished"),
        ("restored", "soot_stained"),
    )),
    _MutualExclusionRule("weather", (
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
        ("indoor", "hail"),
        ("indoor", "sleet"),
        ("indoor", "heat_haze"),
        ("indoor", "lightning"),
        ("indoor", "meteor_shower"),
        ("indoor", "ashfall"),
        ("indoor", "pollen"),
        ("indoor", "rainbow"),
        ("underwater", "hail"),
        ("underwater", "sleet"),
        ("underwater", "heat_haze"),
        ("underwater", "lightning"),
        ("underwater", "meteor_shower"),
        ("underwater", "ashfall"),
        ("underwater", "pollen"),
        ("underwater", "rainbow"),
    )),
    _MutualExclusionRule("character_state", (
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
    )),
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
        "minor",
        "juvenile",
        "preteen",
        "schoolboy",
        "schoolgirl",
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
_MINOR_AGE_VALUE: Final = rf"(?:[0-9]|1[0-7]|{"|".join(_MINOR_NUMBER_WORDS)})"
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
    return f"{descriptors[0]} with {" and ".join(descriptors[1:])}"


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
        return f"{"-to-".join(colors)} gradient irises"

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

    for state_key, garment_key in _LAYERED_STATE_TARGETS:
        if selections.get(state_key) and not selections.get(garment_key):
            raise ValueError("選擇服裝狀態前，必須先選擇對應服裝")


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
        raise ValueError(f"分類 {category.label_zh} 含未知選項：{", ".join(unknown)}")
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
    ("character_backdrop", "character_location", "framing", "pose", "viewpoint")
)


_CHARACTER_STATE_EXPRESSION_OVERRIDES: Final[frozenset[str]] = frozenset(
    {"pouting", "contemptuous_face", "dazed", "sleepy", "asleep", "unconscious"}
)


CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS: Final[frozenset[str]] = (
    _CHARACTER_STATE_EXPRESSION_OVERRIDES
)


_INCAPACITATED_CHARACTER_STATES: Final[frozenset[str]] = frozenset({"asleep", "unconscious"})


INCAPACITATED_CHARACTER_STATE_KEYS: Final[frozenset[str]] = _INCAPACITATED_CHARACTER_STATES


_INCAPACITATED_COMPATIBLE_POSES: Final[frozenset[str]] = frozenset(
    ("leaning_wall", "lying_down", "lying_on_side", "seated", "seated_crossed_legs",
     "seated_sideways", "sitting_on_edge")
)


def _is_character_sheet_selection(selections: Mapping[str, tuple[str, ...]]) -> bool:
    return bool(
        set(selections.get("character_output_purpose", ())) & _CHARACTER_SHEET_OUTPUT_PURPOSES
    )


def _effective_selection(
    category_key: str,
    selections: Mapping[str, tuple[str, ...]],
    *,
    is_character_sheet: bool,
) -> tuple[str, ...]:
    """Apply scene, sheet, expression, and pose precedence consistently."""

    selected = selections.get(category_key, ())
    states = set(selections.get("character_state", ()))
    if is_character_sheet and category_key in _CHARACTER_SHEET_SUPPRESSED_CATEGORIES:
        return ()
    if category_key == "character_location" and "transparent" in selections.get(
        "character_backdrop", ()
    ):
        return ()
    if category_key == "expression" and states & _CHARACTER_STATE_EXPRESSION_OVERRIDES:
        return ()
    if category_key == "pose" and states & _INCAPACITATED_CHARACTER_STATES:
        return tuple(key for key in selected if key in _INCAPACITATED_COMPATIBLE_POSES)
    return selected


def _resolve_prompt_tokens(
    categories: Sequence[TagCategory],
    selections: Mapping[str, SelectionValue],
    *,
    gender: CharacterGender | None = None,
) -> list[str]:
    known_categories = {category.key for category in categories}
    unknown_categories = sorted(set(selections) - known_categories)
    if unknown_categories:
        raise ValueError(f"含未知標籤分類：{", ".join(unknown_categories)}")

    active_categories: list[TagCategory] = []
    normalized_selections: dict[str, tuple[str, ...]] = {}
    for category in categories:
        if category.applicable_gender is not None and category.applicable_gender is not gender:
            continue
        selected = _normalize_selection(category, selections.get(category.key))
        active_categories.append(category)
        normalized_selections[category.key] = selected

    is_character_sheet = _is_character_sheet_selection(normalized_selections)
    coherence_selections = {
        key: (() if is_character_sheet and key in _CHARACTER_SHEET_SUPPRESSED_CATEGORIES else value)
        for key, value in normalized_selections.items()
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
    return _render(
        (
            selected_gender.prompt_token,
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
        raise ValueError(f"分類 {category_key} 含未知選項：{", ".join(unknown_keys)}")
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


_LAYERED_CLOTHING_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    ("accessory_bags", "accessory_face_neck", "accessory_hand_arm", "accessory_head_hair",
     "accessory_waist_body", "footwear", "hosiery_length", "hosiery_style",
     "outfit_lower", "outfit_lower_state", "outfit_one_piece", "outfit_outerwear",
     "outfit_sleepwear", "outfit_swimwear", "outfit_uniform_sport", "outfit_upper",
     "outfit_upper_state")
)


_LAYERED_ACCESSORY_CATEGORY_KEYS: Final[tuple[str, ...]] = ("accessory_head_hair", "accessory_face_neck", "accessory_hand_arm", "accessory_waist_body",
 "accessory_bags")


_LAYERED_STATE_TARGETS: Final[tuple[tuple[str, str], ...]] = (
    ("outfit_upper_state", "outfit_upper"),
    ("outfit_lower_state", "outfit_lower"),
)


_LAYERED_RANDOM_EXCLUDED_OPTIONS: Final[Mapping[str, frozenset[str]]] = {
    "outfit_sleepwear": frozenset(
        {"pajama_top", "pajama_pants", "pajama_shorts"}
    ),
    "outfit_swimwear": frozenset({"rash_guard"}),
}


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
    selected_poses = set(selections.get("pose", ()))
    for state_key, garment_key in _LAYERED_STATE_TARGETS:
        if category_key == state_key and not selections.get(garment_key):
            return False
    if category_key == "character_state":
        if candidate in _INCAPACITATED_CHARACTER_STATES and any(
            pose not in _INCAPACITATED_COMPATIBLE_POSES for pose in selected_poses
        ):
            return False
        if candidate in _CHARACTER_STATE_EXPRESSION_OVERRIDES and selections.get("expression"):
            return False
    elif category_key == "pose" and selected_states & _INCAPACITATED_CHARACTER_STATES:
        return candidate in _INCAPACITATED_COMPATIBLE_POSES
    elif category_key == "expression" and selected_states & _CHARACTER_STATE_EXPRESSION_OVERRIDES:
        return False
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


def _set_random_layered_option(
    category_key: str,
    category_by_key: Mapping[str, TagCategory],
    selection_state: dict[str, tuple[str, ...]],
    result: SelectionResult,
    *,
    picker: random.Random,
) -> bool:
    """Fill one empty layered slot without changing an author-selected value."""

    category = category_by_key.get(category_key)
    if category is None or selection_state.get(category_key):
        return False
    candidates = tuple(
        option
        for option in category.options
        if option.key
        not in _LAYERED_RANDOM_EXCLUDED_OPTIONS.get(category_key, frozenset())
        if _candidate_is_compatible(category_key, option.key, selection_state)
    )
    if not candidates:
        return False
    option = picker.choice(candidates)
    selected = (option.key,)
    selection_state[category_key] = selected
    result[category_key] = selected
    return True


def _has_layered_primary_garment(
    selection_state: Mapping[str, tuple[str, ...]],
) -> bool:
    return bool(
        (selection_state.get("outfit_upper") and selection_state.get("outfit_lower"))
        or selection_state.get("outfit_one_piece")
        or selection_state.get("outfit_sleepwear")
        or selection_state.get("outfit_swimwear")
        or selection_state.get("outfit_uniform_sport")
    )


def _apply_layered_clothing_recipe(
    category_by_key: Mapping[str, TagCategory],
    selection_state: dict[str, tuple[str, ...]],
    result: SelectionResult,
    *,
    picker: random.Random,
) -> bool:
    """Complete one outfit, then add a bounded set of accessories."""

    if not (_LAYERED_CLOTHING_CATEGORY_KEYS & category_by_key.keys()):
        return True
    if not _has_layered_primary_garment(selection_state):
        if selection_state.get("outfit_upper"):
            primary_slots = ("outfit_lower",)
        elif selection_state.get("outfit_lower"):
            primary_slots = ("outfit_upper",)
        else:
            branches = tuple(
                (slots, weight)
                for slots, weight in (
                    (("outfit_upper", "outfit_lower"), 54),
                    (("outfit_one_piece",), 26),
                    (("outfit_sleepwear",), 10),
                    (("outfit_swimwear",), 10),
                )
                if all(key in category_by_key for key in slots)
            )
            if not branches:
                return True
            swim_theme = "swimwear" in selection_state.get("outfit_archetype", ())
            swim_slots = ("outfit_swimwear",)
            if swim_theme and "outfit_swimwear" in category_by_key:
                primary_slots = swim_slots
            else:
                primary_slots = picker.choices(
                    tuple(slots for slots, _ in branches),
                    weights=tuple(weight for _, weight in branches),
                    k=1,
                )[0]
        for category_key in primary_slots:
            _set_random_layered_option(
                category_key, category_by_key, selection_state, result, picker=picker
            )
    if picker.random() < 0.35:
        _set_random_layered_option(
            "outfit_outerwear", category_by_key, selection_state, result, picker=picker
        )
    if picker.random() < 0.4:
        _set_random_layered_option(
            "hosiery_style", category_by_key, selection_state, result, picker=picker
        )
    if selection_state.get("hosiery_style"):
        _set_random_layered_option(
            "hosiery_length", category_by_key, selection_state, result, picker=picker
        )
    if picker.random() < 0.8:
        _set_random_layered_option(
            "footwear", category_by_key, selection_state, result, picker=picker
        )
    accessory_categories = [
        key for key in _LAYERED_ACCESSORY_CATEGORY_KEYS
        if key in category_by_key and not selection_state.get(key)
    ]
    picker.shuffle(accessory_categories)
    for category_key in accessory_categories[:picker.randint(1, 3)]:
        _set_random_layered_option(
            category_key, category_by_key, selection_state, result, picker=picker
        )
    state_categories = [
        state for state, garment in _LAYERED_STATE_TARGETS
        if state in category_by_key and selection_state.get(garment)
        and not selection_state.get(state)
    ]
    picker.shuffle(state_categories)
    for category_key in state_categories[:picker.randint(0, 2)]:
        _set_random_layered_option(
            category_key, category_by_key, selection_state, result, picker=picker
        )
    return _has_layered_primary_garment(selection_state)


def _randomize_attempt(
    categories: Sequence[TagCategory],
    locked: Mapping[str, tuple[str, ...]],
    *,
    fill_blanks_only: bool,
    required_groups: Sequence[str],
    explicit_required_category_keys: frozenset[str],
    picker: random.Random,
) -> tuple[SelectionResult, bool]:
    selection_state = {key: tuple(value) for key, value in locked.items()}
    result: SelectionResult = {}
    category_by_key = {category.key: category for category in categories}
    required_category_keys = frozenset(
        {
            *_required_random_category_keys(
                categories,
                locked,
                required_groups,
                picker=picker,
            ),
            *explicit_required_category_keys,
        }
    )
    expression_slots = tuple(
        key
        for key in ("expression",)
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
        if category.key in _LAYERED_CLOTHING_CATEGORY_KEYS:
            selection_state[category.key] = ()
            result[category.key] = ()
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
            _OPTIONAL_TAXONOMY_SPECIES_CATEGORIES
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
    complete = _apply_layered_clothing_recipe(
        category_by_key,
        selection_state,
        result,
        picker=picker,
    ) and complete
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
    *,
    locked_selections: Mapping[str, SelectionValue] | None = None,
    required_category_keys: Sequence[str] = (),
) -> SelectionResult:
    """Return coherent random keys while preserving deliberate manual choices.

    With ``fill_blanks_only=True``, every non-empty current value is locked and
    retained in the same order, even when the author intentionally selected a
    conflicting combination.  Only newly generated values are constrained to
    fit those anchors.  A full reroll applies coherence rules to every value.
    ``required_groups`` opts selected callers into at least one non-empty
    randomizable category per named group whenever the locked anchors permit it.
    ``locked_selections`` is an explicit coherence anchor for a full reroll.  It
    is intentionally separate from ``fill_blanks_only`` so callers can keep one
    taxonomy choice (for example an animal species) without forcing every
    optional multi-select category to become non-empty.
    ``required_category_keys`` is the corresponding narrow completeness rule:
    only those named categories are forced to receive a value.
    """

    current = current or {}
    if isinstance(required_groups, (str, bytes, bytearray)):
        raise ValueError("必要隨機群組必須使用群組名稱序列")
    required_groups = tuple(dict.fromkeys(required_groups))
    locked_selections = locked_selections or {}
    if isinstance(required_category_keys, (str, bytes, bytearray)):
        raise ValueError("必要隨機分類必須使用分類鍵序列")
    required_category_keys = tuple(dict.fromkeys(required_category_keys))
    category_by_key = {category.key: category for category in categories}
    known_categories = set(category_by_key)
    unknown_categories = sorted(
        (set(current) | set(locked_selections) | set(required_category_keys))
        - known_categories
    )
    if unknown_categories:
        raise ValueError(f"含未知標籤分類：{", ".join(unknown_categories)}")

    locked = _locked_random_selections(
        categories,
        current,
        fill_blanks_only=fill_blanks_only,
    )
    explicit_locked = _locked_random_selections(
        categories,
        locked_selections,
        fill_blanks_only=True,
    )
    locked.update(explicit_locked)
    unavailable_required_categories = sorted(
        key
        for key in required_category_keys
        if category_by_key[key].random_max == 0 and not locked.get(key)
    )
    if unavailable_required_categories:
        raise ValueError(
            "必要隨機分類沒有可用選項："
            + ", ".join(unavailable_required_categories)
        )
    known_groups = {category.group for category in categories}
    unknown_groups = sorted(set(required_groups) - known_groups)
    if unknown_groups:
        raise ValueError(f"含未知必要隨機群組：{", ".join(unknown_groups)}")
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
        raise ValueError(f"必要隨機群組沒有可用選項：{", ".join(unavailable_groups)}")
    picker = random.Random(seed)
    best: SelectionResult = {}
    best_score = -1
    for _attempt in range(128):
        result, complete = _randomize_attempt(
            categories,
            locked,
            fill_blanks_only=fill_blanks_only,
            required_groups=required_groups,
            explicit_required_category_keys=frozenset(required_category_keys),
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
    "BACKGROUND_CATEGORIES",
    "CHARACTER_CATEGORIES",
    "CHARACTER_IDENTITY_CATEGORY_KEYS",
    "CHARACTER_IDENTITY_MODES",
    "CHARACTER_IDENTITY_MODE_LABELS_ZH",
    "CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS",
    "HOSIERY_ALLOWED_LENGTHS",
    "INCAPACITATED_CHARACTER_STATE_KEYS",
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
    "filter_character_categories_by_identity",
    "randomize_selections",
    "resolve_mutually_exclusive_selection",
    "validate_custom_species_english",
]
