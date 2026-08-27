"""Deterministic, network-free translation for short custom species names.

This module intentionally does not accept a provider, URL, model, or API key.
It translates a deliberately bounded Traditional-Chinese lexicon and fails
closed when any input fragment is unknown.  The caller can then ask the author
to enter the English species phrase directly instead of silently contacting a
remote translation service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache, lru_cache
from typing import Final, Literal

from imaginarium_forge.application.services.prompt_tag_builder_service import (
    validate_custom_species_english,
)

type OfflineTranslationStrategy = Literal["dictionary", "direct_english"]

MAX_OFFLINE_SOURCE_CHARS: Final = 80


class OfflineSpeciesTranslationUnavailableError(ValueError):
    """Raised when the offline lexicon cannot consume the complete input."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(
            "內建離線詞庫無法完整翻譯這個物種名稱；"
            "請改在英文欄輸入簡短物種名稱，或在本機安裝 Ollama 後再使用本機模型。"
        )


@dataclass(frozen=True, slots=True)
class OfflineSpeciesTranslationResult:
    """A validated Prompt fragment produced without any network access."""

    source: str
    species_prompt_en: str
    strategy: OfflineTranslationStrategy


@dataclass(frozen=True, slots=True)
class _Lexeme:
    source_zh: str
    prompt_en: str
    kind: Literal["modifier", "species"]


# Exact aliases are checked before compositional parsing.  Entries include the
# species already available in the curated tag catalog plus common fantasy
# creatures authors are likely to combine with an elemental modifier.
_SPECIES_TERMS: Final[dict[str, str]] = {
    "犬科": "canine",
    "犬": "dog",
    "狗": "dog",
    "柴犬": "Shiba Inu",
    "哈士奇": "husky",
    "德國牧羊犬": "German shepherd",
    "杜賓犬": "Doberman",
    "柯基犬": "corgi",
    "狐狸": "fox",
    "狐": "fox",
    "赤狐": "red fox",
    "北極狐": "arctic fox",
    "耳廓狐": "fennec fox",
    "狼": "wolf",
    "灰狼": "gray wolf",
    "北極狼": "arctic wolf",
    "郊狼": "coyote",
    "豺": "jackal",
    "鬣狗": "hyena",
    "貓": "cat",
    "家貓": "cat",
    "貓咪": "cat",
    "獅": "lion",
    "老虎": "tiger",
    "虎": "tiger",
    "豹": "leopard",
    "雪豹": "snow leopard",
    "獵豹": "cheetah",
    "黑豹": "black panther",
    "美洲獅": "cougar",
    "猞猁": "lynx",
    "藪貓": "serval",
    "兔": "rabbit",
    "野兔": "hare",
    "北極兔": "arctic hare",
    "棕熊": "brown bear",
    "北極熊": "polar bear",
    "熊": "bear",
    "熊貓": "giant panda",
    "小熊貓": "red panda",
    "赤鹿": "red deer",
    "馴鹿": "reindeer",
    "駝鹿": "moose",
    "麋鹿": "elk",
    "鹿": "deer",
    "山羊": "goat",
    "綿羊": "sheep",
    "公羊": "ram",
    "羊": "sheep",
    "乳牛": "dairy cow",
    "公牛": "bull",
    "水牛": "buffalo",
    "牛": "bovine",
    "馬": "horse",
    "斑馬": "zebra",
    "驢": "donkey",
    "小鼠": "mouse",
    "大鼠": "rat",
    "倉鼠": "hamster",
    "松鼠": "squirrel",
    "水獺": "otter",
    "浣熊": "raccoon",
    "臭鼬": "skunk",
    "蝙蝠": "bat",
    "刺蝟": "hedgehog",
    "袋鼠": "kangaroo",
    "樹懶": "sloth",
    "猩猩": "gorilla",
    "大猩猩": "gorilla",
    "猴": "monkey",
    "猴族": "monkey",
    "水豚": "capybara",
    "河狸": "beaver",
    "獾": "badger",
    "雪貂": "ferret",
    "狐獴": "meerkat",
    "狼獾": "wolverine",
    "無尾熊": "koala",
    "袋熊": "wombat",
    "負鼠": "opossum",
    "犰狳": "armadillo",
    "野豬": "wild boar",
    "家豬": "pig",
    "駱駝": "camel",
    "羊駝": "alpaca",
    "美洲駝": "llama",
    "牦牛": "yak",
    "野牛": "bison",
    "羚羊": "antelope",
    "瞪羚": "gazelle",
    "北山羊": "ibex",
    "龍貓": "chinchilla",
    "天竺鼠": "guinea pig",
    "狐猴": "lemur",
    "大象": "elephant",
    "犀牛": "rhinoceros",
    "河馬": "hippopotamus",
    "海豹": "seal",
    "海獅": "sea lion",
    "海牛": "manatee",
    "鷹": "eagle",
    "金雕": "golden eagle",
    "白肩鵰": "Steller's sea eagle",
    "貓頭鷹": "owl",
    "渡鴉": "raven",
    "烏鴉": "crow",
    "鸚鵡": "parrot",
    "天鵝": "swan",
    "孔雀": "peacock",
    "企鵝": "penguin",
    "蜥蜴": "lizard",
    "守宮": "gecko",
    "蛇": "snake",
    "眼鏡蛇": "cobra",
    "鱷魚": "crocodile",
    "龜": "turtle",
    "鯊魚": "shark",
    "鯊": "shark",
    "海豚": "dolphin",
    "虎鯨": "orca",
    "錦鯉": "koi",
    "六角恐龍": "axolotl",
    "章魚": "octopus",
    "水母": "jellyfish",
    "蝴蝶": "butterfly",
    "飛蛾": "moth",
    "蜜蜂": "bee",
    "甲蟲": "beetle",
    "蜻蜓": "dragonfly",
    "螳螂": "mantis",
    "蜘蛛": "spider",
    "蠍子": "scorpion",
    "龍": "dragon",
    "飛龍": "wyvern",
    "麒麟": "kirin",
    "獨角獸": "unicorn",
    "天馬": "pegasus",
    "鳳凰": "phoenix",
    "獅鷲": "griffin",
    "地獄犬": "hellhound",
    "克拉肯": "kraken",
    "史萊姆": "slime",
    "精靈": "elf",
}

_MODIFIER_TERMS: Final[dict[str, str]] = {
    "月光": "moonlit",
    "月": "lunar",
    "星辰": "astral",
    "星光": "starlit",
    "星空": "celestial",
    "宇宙": "cosmic",
    "太陽": "solar",
    "日光": "sunlit",
    "暗影": "shadow",
    "幽影": "spectral",
    "午夜": "midnight",
    "夜": "nocturnal",
    "暮光": "twilight",
    "晨曦": "dawn",
    "黎明": "dawn",
    "黃昏": "dusk",
    "冰霜": "frost",
    "霜": "frost",
    "冰": "ice",
    "烈焰": "blazing",
    "火焰": "flame",
    "火": "fire",
    "雷霆": "thunder",
    "雷": "lightning",
    "風暴": "storm",
    "風": "wind",
    "雲": "cloud",
    "深海": "deep-sea",
    "海洋": "oceanic",
    "海": "sea",
    "珊瑚": "coral",
    "森林": "forest",
    "森": "forest",
    "沙漠": "desert",
    "沙": "sand",
    "水晶": "crystal",
    "黑曜石": "obsidian",
    "翡翠": "jade",
    "寶石": "gem",
    "黃金": "golden",
    "金色": "golden",
    "銀色": "silver",
    "緋紅": "crimson",
    "蒼藍": "azure",
    "白色": "white",
    "黑色": "black",
    "紅色": "red",
    "機械": "mechanical",
    "機甲": "mecha",
    "賽博": "cybernetic",
    "虛空": "void",
    "混沌": "chaos",
    "神聖": "sacred",
    "聖": "holy",
    "惡魔": "demonic",
    "煉獄": "infernal",
    "亡靈": "undead",
    "幽靈": "ghostly",
    "夢境": "dream",
    "夢": "dream",
    "櫻花": "cherry-blossom",
    "玫瑰": "rose",
    "花": "floral",
    "蘑菇": "mushroom",
    "劇毒": "venomous",
    "毒": "toxic",
    "遠古": "ancient",
    "古代": "ancient",
    "元素": "elemental",
}

_IDENTITY_SUFFIXES: Final[tuple[str, ...]] = (
    "半獸人",
    "獸人",
    "福瑞",
    "族群",
    "物種",
    "種族",
    "族",
    "種",
)
_IGNORABLE_SEPARATORS = re.compile(r"[\s·・／/_-]+")
_ASCII_ONLY = re.compile(r"[\x00-\x7f]+")
_CHARACTER_FORM_WRAPPERS: Final[tuple[str, ...]] = (
    "anthro",
    "anthropomorphic",
    "beastfolk",
    "furry",
    "half-beast",
    "humanoid",
    "kemonomimi",
)


def validate_direct_english(value: str) -> str:
    """Normalize a manually entered English phrase using the Prompt contract."""

    normalized = validate_custom_species_english(value)
    padded = f" {' '.join(normalized.casefold().replace('-', ' ').split())} "
    if any(f" {wrapper.replace('-', ' ')} " in padded for wrapper in _CHARACTER_FORM_WRAPPERS):
        raise ValueError("英文欄只需填物種名稱，不可加入獸人、福瑞或人型 wrapper")
    return normalized


def _normalized_chinese_source(value: str) -> str:
    source = value.strip()
    if not source:
        raise ValueError("自訂物種名稱不可空白")
    if len(source) > MAX_OFFLINE_SOURCE_CHARS:
        raise ValueError(f"自訂物種名稱不可超過 {MAX_OFFLINE_SOURCE_CHARS} 個字元")
    if source.isascii():
        return source
    normalized = _IGNORABLE_SEPARATORS.sub("", source)
    previous = ""
    while normalized != previous:
        previous = normalized
        for suffix in _IDENTITY_SUFFIXES:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[: -len(suffix)]
                break
    return normalized


@lru_cache(maxsize=1)
def _lexemes() -> tuple[_Lexeme, ...]:
    terms = (
        *(_Lexeme(source, target, "species") for source, target in _SPECIES_TERMS.items()),
        *(_Lexeme(source, target, "modifier") for source, target in _MODIFIER_TERMS.items()),
    )
    return tuple(sorted(terms, key=lambda item: (-len(item.source_zh), item.source_zh)))


def _tokenize(value: str) -> tuple[_Lexeme, ...] | None:
    @cache
    def visit(offset: int) -> tuple[_Lexeme, ...] | None:
        if offset == len(value):
            return ()
        candidates: list[tuple[_Lexeme, ...]] = []
        for lexeme in _lexemes():
            if not value.startswith(lexeme.source_zh, offset):
                continue
            remainder = visit(offset + len(lexeme.source_zh))
            if remainder is not None:
                candidates.append((lexeme, *remainder))
        if not candidates:
            return None
        # Prefer the parse with the fewest terms.  This makes 雪豹 one species
        # instead of the modifier 雪 plus 豹, while remaining deterministic.
        return min(candidates, key=lambda parsed: (len(parsed), tuple(x.source_zh for x in parsed)))

    return visit(0)


class OfflineSpeciesTranslationService:
    """Translate a short species phrase without providers or network I/O."""

    def translate(self, source_zh: str) -> OfflineSpeciesTranslationResult:
        source = source_zh.strip()
        normalized = _normalized_chinese_source(source)
        if _ASCII_ONLY.fullmatch(normalized) is not None:
            return OfflineSpeciesTranslationResult(
                source=source,
                species_prompt_en=validate_direct_english(normalized),
                strategy="direct_english",
            )
        if normalized in _SPECIES_TERMS:
            translated = _SPECIES_TERMS[normalized]
        else:
            tokens = _tokenize(normalized)
            if tokens is None:
                raise OfflineSpeciesTranslationUnavailableError(source)
            species = [token.prompt_en for token in tokens if token.kind == "species"]
            if not species or len(species) > 2:
                raise OfflineSpeciesTranslationUnavailableError(source)
            modifiers = [token.prompt_en for token in tokens if token.kind == "modifier"]
            pieces = [*dict.fromkeys(modifiers), *dict.fromkeys(species)]
            if len(set(species)) > 1:
                pieces.append("hybrid")
            translated = " ".join(pieces)
        return OfflineSpeciesTranslationResult(
            source=source,
            species_prompt_en=validate_direct_english(translated),
            strategy="dictionary",
        )


__all__ = [
    "MAX_OFFLINE_SOURCE_CHARS",
    "OfflineSpeciesTranslationResult",
    "OfflineSpeciesTranslationService",
    "OfflineSpeciesTranslationUnavailableError",
    "OfflineTranslationStrategy",
    "validate_direct_english",
]
