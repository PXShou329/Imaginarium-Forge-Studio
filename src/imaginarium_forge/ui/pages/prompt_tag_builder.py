"""Project-free tag picker that composes English image prompts."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from functools import cache, lru_cache
from typing import Final, Literal, cast
from unicodedata import normalize

import streamlit as st

from imaginarium_forge.application.services.offline_species_translation_service import (
    OfflineSpeciesTranslationService,
)
from imaginarium_forge.application.services.prompt_tag_builder_service import (
    ADULT_BASE_ACTIVE_EXPRESSION_KEYS,
    ADULT_BASE_EXPRESSION_KEYS,
    ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATE_KEYS,
    ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS,
    ADULT_FEMALE_ACTIVE_STATE_KEYS,
    ADULT_FEMALE_EXPRESSION_KEYS,
    BACKGROUND_CATEGORIES,
    CHARACTER_CATEGORIES,
    CHARACTER_IDENTITY_CATEGORY_KEYS,
    CHARACTER_IDENTITY_MODE_LABELS_ZH,
    CHARACTER_IDENTITY_MODES,
    CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS,
    HOSIERY_ALLOWED_LENGTHS,
    INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY,
    NEGATIVE_CATEGORIES,
    CharacterIdentityMode,
    TagCategory,
    TagOption,
    build_background_prompt,
    build_character_prompt,
    build_combined_scene_prompt,
    build_negative_prompt,
    filter_adult_options,
    filter_character_categories_by_identity,
    randomize_selections,
    resolve_mutually_exclusive_selection,
    validate_custom_species_english,
)
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.copyable_prompt import render_copyable_prompt

PAGE_KEY = "Prompt Tag Builder"
PAGE_LABEL = "懶人標籤生成器"

STATE_PREFIX = "prompt_tag_builder"
MODE_STATE_KEY = f"{STATE_PREFIX}_mode"
_LAST_MODE_STATE_KEY = f"{STATE_PREFIX}_last_mode"
_DURABLE_MODE_STATE_KEY = f"{STATE_PREFIX}_mode_saved"
_CHARACTER_SANITIZE_CONTEXT_STATE_KEY = f"{STATE_PREFIX}_character_sanitize_context"
CHARACTER_GROUP_STATE_KEY = f"{STATE_PREFIX}_character_active_group"
BACKGROUND_GROUP_STATE_KEY = f"{STATE_PREFIX}_background_active_group"
GENDER_STATE_KEY = f"{STATE_PREFIX}_gender"
_GENDER_SELECTION_STATE_KEY = f"{STATE_PREFIX}_gender_selection"
IDENTITY_MODE_STATE_KEY = f"{STATE_PREFIX}_identity_mode"
_DURABLE_IDENTITY_MODE_STATE_KEY = f"{STATE_PREFIX}_identity_mode_saved"
ADULT_MODE_STATE_KEY = f"{STATE_PREFIX}_adult_mode"
_DURABLE_ADULT_MODE_STATE_KEY = f"{STATE_PREFIX}_adult_mode_saved"
TITLE_STATE_KEY = f"{STATE_PREFIX}_title"
_DURABLE_TITLE_STATE_KEY = f"{STATE_PREFIX}_title_saved"
CHARACTER_NAME_STATE_KEY = f"{STATE_PREFIX}_character_name"
_DURABLE_CHARACTER_NAME_STATE_KEY = f"{STATE_PREFIX}_character_name_saved"
CHARACTER_CUSTOM_STATE_KEY = f"{STATE_PREFIX}_character_custom"
_DURABLE_CHARACTER_CUSTOM_STATE_KEY = f"{STATE_PREFIX}_character_custom_saved"
BACKGROUND_CUSTOM_STATE_KEY = f"{STATE_PREFIX}_background_custom"
_DURABLE_BACKGROUND_CUSTOM_STATE_KEY = f"{STATE_PREFIX}_background_custom_saved"
NEGATIVE_CUSTOM_STATE_KEY = f"{STATE_PREFIX}_negative_custom"
_DURABLE_NEGATIVE_CUSTOM_STATE_KEY = f"{STATE_PREFIX}_negative_custom_saved"
CHARACTER_OUTPUT_STATE_KEY = f"{STATE_PREFIX}_character_output"
BACKGROUND_OUTPUT_STATE_KEY = f"{STATE_PREFIX}_background_output"
NEGATIVE_OUTPUT_STATE_KEY = f"{STATE_PREFIX}_negative_output"
COMBINED_OUTPUT_STATE_KEY = f"{STATE_PREFIX}_combined_output"
CHARACTER_OUTPUT_CONTEXT_STATE_KEY = f"{STATE_PREFIX}_character_output_context"
SPECIES_TRANSLATION_HOOK_STATE_KEY = f"{STATE_PREFIX}_species_translation_hook"
CHARACTER_EDITOR_STATE_KEY = f"{STATE_PREFIX}_character_prompt_editor"
BACKGROUND_EDITOR_STATE_KEY = f"{STATE_PREFIX}_background_prompt_editor"
NEGATIVE_EDITOR_STATE_KEY = f"{STATE_PREFIX}_negative_prompt_editor"
_CHARACTER_EDITOR_REROLL_MANUAL_STATE_KEY = (
    f"{STATE_PREFIX}_character_prompt_editor_reroll_manual"
)

_PROMPT_EDITOR_STATE_KEYS: Final[dict[str, str]] = {
    CHARACTER_OUTPUT_STATE_KEY: CHARACTER_EDITOR_STATE_KEY,
    BACKGROUND_OUTPUT_STATE_KEY: BACKGROUND_EDITOR_STATE_KEY,
    NEGATIVE_OUTPUT_STATE_KEY: NEGATIVE_EDITOR_STATE_KEY,
}
_PROMPT_EDITOR_BASELINE_STATE_KEYS: Final[dict[str, str]] = {
    output_key: f"{editor_key}_generated_baseline"
    for output_key, editor_key in _PROMPT_EDITOR_STATE_KEYS.items()
}
_PROMPT_EDITOR_DURABLE_STATE_KEYS: Final[dict[str, str]] = {
    output_key: f"{editor_key}_saved"
    for output_key, editor_key in _PROMPT_EDITOR_STATE_KEYS.items()
}

_CUSTOM_SPECIES_ZH_STATE_KEYS: Final[dict[CharacterIdentityMode, str]] = {
    "beast_humanoid": f"{STATE_PREFIX}_beast_humanoid_custom_species_zh",
    "furry": f"{STATE_PREFIX}_furry_custom_species_zh",
}
_CUSTOM_SPECIES_EN_STATE_KEYS: Final[dict[CharacterIdentityMode, str]] = {
    "beast_humanoid": f"{STATE_PREFIX}_beast_humanoid_custom_species_en",
    "furry": f"{STATE_PREFIX}_furry_custom_species_en",
}
_CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS: Final[dict[CharacterIdentityMode, str]] = {
    "beast_humanoid": f"{STATE_PREFIX}_beast_humanoid_custom_species_zh_saved",
    "furry": f"{STATE_PREFIX}_furry_custom_species_zh_saved",
}
_CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS: Final[dict[CharacterIdentityMode, str]] = {
    "beast_humanoid": f"{STATE_PREFIX}_beast_humanoid_custom_species_en_saved",
    "furry": f"{STATE_PREFIX}_furry_custom_species_en_saved",
}
_CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS: Final[dict[CharacterIdentityMode, str]] = {
    "beast_humanoid": f"{STATE_PREFIX}_beast_humanoid_translation_status",
    "furry": f"{STATE_PREFIX}_furry_translation_status",
}
_SPECIES_CATEGORY_KEYS: Final[dict[CharacterIdentityMode, str]] = {
    "beast_humanoid": "beast_humanoid_species",
    "furry": "furry_species",
}

_ADULT_LABEL_SUFFIX: Final = "（18+）"
_GROUP_OVERVIEW_KEY: Final = "__overview__"

_GROUP_NAVIGATION_STATE_KEYS: Final[dict[str, str]] = {
    "character": CHARACTER_GROUP_STATE_KEY,
    "background": BACKGROUND_GROUP_STATE_KEY,
}
_LAST_ACTIVE_GROUP_STATE_KEYS: Final[dict[str, str]] = {
    kind: f"{STATE_PREFIX}_{kind}_last_active_group" for kind in _GROUP_NAVIGATION_STATE_KEYS
}
_DURABLE_GROUP_NAVIGATION_STATE_KEYS: Final[dict[str, str]] = {
    "character": f"{STATE_PREFIX}_character_active_group_saved",
    "background": f"{STATE_PREFIX}_background_active_group_saved",
}
# dev15 exposed a separate "show all" switch.  These exact keys are read once
# to migrate an existing browser session into the lightweight overview, then
# removed.  They are intentionally not rendered as widgets anymore.
_LEGACY_SHOW_ALL_GROUPS_STATE_KEYS: Final[dict[str, tuple[str, str, str]]] = {
    "character": (
        f"{STATE_PREFIX}_character_show_all_groups",
        f"{STATE_PREFIX}_character_show_all_groups_saved",
        f"{STATE_PREFIX}_character_last_show_all_groups",
    ),
    "background": (
        f"{STATE_PREFIX}_background_show_all_groups",
        f"{STATE_PREFIX}_background_show_all_groups_saved",
        f"{STATE_PREFIX}_background_last_show_all_groups",
    ),
}
_PAGE_WIDGET_DURABLE_STATE_KEYS: Final[dict[str, str]] = {
    MODE_STATE_KEY: _DURABLE_MODE_STATE_KEY,
    GENDER_STATE_KEY: _GENDER_SELECTION_STATE_KEY,
    IDENTITY_MODE_STATE_KEY: _DURABLE_IDENTITY_MODE_STATE_KEY,
    ADULT_MODE_STATE_KEY: _DURABLE_ADULT_MODE_STATE_KEY,
    TITLE_STATE_KEY: _DURABLE_TITLE_STATE_KEY,
    CHARACTER_NAME_STATE_KEY: _DURABLE_CHARACTER_NAME_STATE_KEY,
    CHARACTER_CUSTOM_STATE_KEY: _DURABLE_CHARACTER_CUSTOM_STATE_KEY,
    BACKGROUND_CUSTOM_STATE_KEY: _DURABLE_BACKGROUND_CUSTOM_STATE_KEY,
    NEGATIVE_CUSTOM_STATE_KEY: _DURABLE_NEGATIVE_CUSTOM_STATE_KEY,
    CHARACTER_EDITOR_STATE_KEY: _PROMPT_EDITOR_DURABLE_STATE_KEYS[
        CHARACTER_OUTPUT_STATE_KEY
    ],
    BACKGROUND_EDITOR_STATE_KEY: _PROMPT_EDITOR_DURABLE_STATE_KEYS[
        BACKGROUND_OUTPUT_STATE_KEY
    ],
    NEGATIVE_EDITOR_STATE_KEY: _PROMPT_EDITOR_DURABLE_STATE_KEYS[NEGATIVE_OUTPUT_STATE_KEY],
    CHARACTER_GROUP_STATE_KEY: _DURABLE_GROUP_NAVIGATION_STATE_KEYS["character"],
    BACKGROUND_GROUP_STATE_KEY: _DURABLE_GROUP_NAVIGATION_STATE_KEYS["background"],
}

type PromptMode = Literal["character", "background", "both"]
type SelectionValue = str | tuple[str, ...] | None

_MODE_LABELS: Final[dict[PromptMode, str]] = {
    "character": "只做角色圖",
    "background": "只做背景圖",
    "both": "角色圖＋背景圖",
}
_CHARACTER_OUTPUT_PURPOSE_CATEGORY_KEY: Final = "character_output_purpose"
_PARTNER_INTIMACY_CATEGORY_KEY: Final = "adult_partner_intimacy"
_SOLO_ADULT_ACTIVITY_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {"adult_female_masturbation_pose", "adult_female_masturbation_action"}
)
_AFTERCARE_ACTIVITY_CATEGORY_KEY: Final = "adult_aftercare_action"
_ACTIVE_ADULT_PHASE_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {
        _PARTNER_INTIMACY_CATEGORY_KEY,
        *_SOLO_ADULT_ACTIVITY_CATEGORY_KEYS,
        "adult_female_breast_hand_action",
        "adult_female_breast_suckling_action",
        "adult_female_lactation_action",
    }
)
_NEGATIVE_KEYS_BY_MODE: Final[dict[PromptMode, frozenset[str]]] = {
    "character": frozenset(
        {
            "negative_quality",
            "negative_anatomy",
            "negative_composition",
            "negative_artifacts",
        }
    ),
    "background": frozenset(
        {
            "negative_quality",
            "negative_composition",
            "negative_artifacts",
            "negative_environment",
        }
    ),
    "both": frozenset(category.key for category in NEGATIVE_CATEGORIES),
}
_GROUP_HINTS: Final[dict[str, str]] = {
    "身分與輪廓": "先決定成年年齡感、奇幻種族與整體輪廓；每一項都能留白。",
    "身材細節": "胸型、腰、臀、肩與腿型皆為成年角色標籤，採單選避免互相矛盾。",
    "臉部與眼睛": "從膚色、臉型到眼型與眼色，逐層補足可辨識特徵。",
    "髮型與髮色": "髮長、髮型、瀏海、顏色與質感各自調整。",
    "個性與奇幻特徵": "表情、耳朵、角、翅膀與魔法痕跡可以自由混搭。",
    "獸人／半獸人": "先選一個物種；確認物種後才會展開半獸人細節。",
    "獸人／半獸人細節": (
        "先選半獸人物種，再分開調整融合比例、鼻口、體表、花紋與覆蓋位置、獸耳、尾巴及手腳；"
        "人型手腳永遠可選，獸耳會取代人耳，不會生成兩套耳朵。"
    ),
    "福瑞": "先選一個完整擬人物種；確認物種後才會展開福瑞細節。",
    "福瑞細節": "依物種調整完整擬人角色的體表、花紋、口鼻、腿腳、肢端與尾部。",
    "服裝與配件": (
        "先從上身、下身、連身服裝、內著、泳裝、襪類與配件部位逐項搭配；"
        "頁面底部仍保留穿搭主題、材質、配色與經典配件總覽。"
    ),
    "畫面與風格": "控制姿勢、取景、鏡頭、燈光、背景與成像風格。",
    "狀態": "可混搭一般狀態與情緒；成人模式開啟時，也會在同區顯示成年女性生理狀態。",
    "成人動作與表情（18+）": "只會在已確認 18+ 時出現；所有項目都維持成年、合意的內容邊界。",
    "成人身體細節（18+）": (
        "只會在已確認 18+ 時出現；乳暈、乳頭與陰毛造型可用於成年男女，"
        "外陰與陰唇細節只適用於成年女性。"
    ),
    "世界與地點": "先選世界類型、場景與空間尺度。",
    "建築與材質": "決定年代感、建築語彙與主要表面材質。",
    "自然與天候": "補上地形、季節、時段、天氣與空氣感。",
    "氣氛與光色": "選擇主光、色盤與整體情緒。",
    "構圖與鏡頭": "控制視角、景深、尺度與視線落點。",
    "場景故事": "加入生活痕跡、超自然現象、人物密度與敘事物件。",
    "風格與完成度": "最後決定美術風格、渲染方式與細節密度。",
    "負面提示詞": "獨立排除畫質、人體、構圖、文字浮水印與環境瑕疵。",
}

_CHARACTER_TRAILING_GROUP_ORDER: Final[tuple[str, ...]] = (
    "畫面與風格",
    "狀態",
    "成人身體細節（18+）",
    "成人動作與表情（18+）",
)
_STANDARD_ADULT_GROUP_ORDER: Final[tuple[str, ...]] = (
    "身分與輪廓",
    "身材細節",
    "臉部與眼睛",
    "髮型與髮色",
    "個性與奇幻特徵",
    "服裝與配件",
    *_CHARACTER_TRAILING_GROUP_ORDER,
)
_WARDROBE_CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "outfit_upper",
    "outfit_lower",
    "outfit_one_piece",
    "outfit_outerwear",
    "outfit_bra",
    "outfit_underwear",
    "outfit_sleepwear",
    "outfit_uniform_sport",
    "outfit_swimwear",
    "outfit_lingerie",
    "hosiery_style",
    "hosiery_length",
    "footwear",
    "accessory_head_hair",
    "accessory_face_neck",
    "accessory_hand_arm",
    "accessory_waist_body",
    "accessory_bags",
    "intimate_accessories",
    "adult_toys",
    "outfit_upper_state",
    "outfit_lower_state",
    "outfit_bra_state",
    "outfit_underwear_state",
    # Preserve the original broad selectors as optional finishing controls,
    # without letting their large catalogs hide the composable wardrobe.
    "outfit_archetype",
    "outfit_materials",
    "outfit_palette",
    "accessories",
)
_WARDROBE_CATEGORY_ORDER_INDEX: Final[dict[str, int]] = {
    key: index for index, key in enumerate(_WARDROBE_CATEGORY_ORDER)
}
_LEGACY_WARDROBE_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {"outfit_archetype", "outfit_materials", "outfit_palette", "accessories"}
)
_ADULT_RANDOM_REQUIRED_GROUPS: Final[tuple[str, str]] = (
    "成人身體細節（18+）",
    "成人動作與表情（18+）",
)
_LAYERED_GARMENT_STATE_DEPENDENCIES: Final[dict[str, str]] = {
    "outfit_upper_state": "outfit_upper",
    "outfit_lower_state": "outfit_lower",
    "outfit_bra_state": "outfit_bra",
    "outfit_underwear_state": "outfit_underwear",
}
_LAYERED_GARMENT_DEPENDENT_STATES: Final[dict[str, frozenset[str]]] = {
    garment_key: frozenset(
        state_key
        for state_key, target_key in _LAYERED_GARMENT_STATE_DEPENDENCIES.items()
        if target_key == garment_key
    )
    for garment_key in frozenset(_LAYERED_GARMENT_STATE_DEPENDENCIES.values())
}
_LAYERED_CORE_GARMENT_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "outfit_upper",
        "outfit_lower",
        "outfit_one_piece",
        "outfit_outerwear",
        "outfit_bra",
        "outfit_underwear",
        "outfit_sleepwear",
        "outfit_uniform_sport",
        "outfit_swimwear",
        "outfit_lingerie",
    }
)
_UPPER_LAYERED_GARMENT_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "outfit_upper",
        "outfit_one_piece",
        "outfit_outerwear",
        "outfit_bra",
        "outfit_sleepwear",
        "outfit_uniform_sport",
        "outfit_swimwear",
        "outfit_lingerie",
    }
)
_CLOTHINGLESS_OUTFIT_KEYS: Final[frozenset[str]] = frozenset({"nude", "body_paint"})
_CLOTHINGLESS_ALLOWED_FINISHING_KEYS: Final[
    Mapping[str, Mapping[str, frozenset[str]]]
] = {
    "nude": {
        "outfit_materials": frozenset({"bare_skin"}),
        "outfit_palette": frozenset(
            {
                "natural_skin",
                "adult_palette_warm_flushed_skin",
                "adult_palette_cool_natural_skin",
            }
        ),
    },
    "body_paint": {
        "outfit_materials": frozenset({"bare_skin"}),
        "outfit_palette": frozenset(
            {
                "natural_skin",
                "adult_palette_warm_flushed_skin",
                "adult_palette_cool_natural_skin",
                "adult_palette_metallic_body_paint",
            }
        ),
    },
}
_NUDITY_DEPENDENT_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "areola_size",
        "areola_shape",
        "areola_tone",
        "nipple_size",
        "nipple_shape",
        "nipple_state",
        "vulva_shape",
        "labia_shape",
        "pubic_hair_style",
        "adult_body_adornment",
        "adult_female_breast_hand_action",
        "adult_female_breast_suckling_action",
        "adult_female_lactation_action",
        "adult_partner_intimacy",
        "adult_female_masturbation_action",
    }
)
_NUDITY_DEPENDENT_OPTION_KEYS: Final[dict[str, frozenset[str]]] = {
    "outfit_materials": frozenset({"bare_skin"}),
    "outfit_palette": frozenset(
        {
            "natural_skin",
            "adult_palette_warm_flushed_skin",
            "adult_palette_cool_natural_skin",
            "adult_palette_metallic_body_paint",
        }
    ),
    "accessories": frozenset(
        {"nipple_jewelry", "adult_accessory_nipple_clamps"}
    ),
    "intimate_accessories": frozenset(
        {"adult_accessory_nipple_pasties", "adult_accessory_chain_pasties"}
    ),
    "pose": frozenset({"artistic_nude", "nude_recline", "nude_back_arch"}),
}
_TOPLESS_DEPENDENT_POSE_KEYS: Final[frozenset[str]] = frozenset({"topless_pose"})
_ADULT_RANDOM_NOTICE_STATE_KEY: Final = f"{STATE_PREFIX}_adult_random_notice"
_TAG_BUTTONS_PER_HALF_ROW: Final = 4
_TAG_BUTTONS_PER_WIDE_ROW: Final = 8
_CENTERED_WIDE_HALF_ROW_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(
    {"expression"}
)


def _category_widget_key(kind: str, category: TagCategory) -> str:
    return f"{STATE_PREFIX}_{kind}_{category.key}"


def _remember_gender_selection() -> None:
    raw = st.session_state.get(GENDER_STATE_KEY)
    if isinstance(raw, CharacterGender):
        selected = raw
    elif isinstance(raw, str):
        try:
            selected = CharacterGender(raw)
        except ValueError:
            st.session_state.pop(_GENDER_SELECTION_STATE_KEY, None)
            return
    else:
        st.session_state.pop(_GENDER_SELECTION_STATE_KEY, None)
        return
    _write_session_value_if_changed(_GENDER_SELECTION_STATE_KEY, selected)


def _selection_tracker_key(kind: str, category: TagCategory) -> str:
    return f"{_category_widget_key(kind, category)}_last_valid"


def _selection_notice_key(kind: str, category: TagCategory) -> str:
    return f"{_category_widget_key(kind, category)}_limit_notice"


def _selection_limit(category: TagCategory) -> int | None:
    raw = getattr(category, "selection_max", None)
    if isinstance(raw, int) and raw > 0:
        return raw
    return None


def _category_applies(category: TagCategory, gender: CharacterGender) -> bool:
    applicable = category.applicable_gender
    if applicable is None:
        return True
    value = getattr(applicable, "value", applicable)
    return str(value) == gender.value


def _default_widget_value(category: TagCategory) -> str | list[str] | None:
    defaults = tuple(category.default_keys)
    if category.selection_mode == "multi":
        return list(defaults)
    return defaults[0] if defaults else None


def _selection_store_state_key(kind: str) -> str:
    return f"{STATE_PREFIX}_{kind}_selection_store"


def _selection_store(kind: str) -> dict[str, SelectionValue]:
    raw = st.session_state.get(_selection_store_state_key(kind))
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): cast("SelectionValue", value) for key, value in raw.items()}


def _normalize_selection_value(category: TagCategory, raw: object) -> SelectionValue:
    if category.selection_mode == "multi":
        if isinstance(raw, Sequence) and not isinstance(raw, str):
            values = tuple(str(value) for value in raw)
        else:
            values = ()
        limit = _selection_limit(category)
        return values[:limit] if limit is not None else values
    return str(raw) if raw else None


def _selection_widget_value(category: TagCategory, value: SelectionValue) -> object:
    if category.selection_mode == "multi":
        return list(value) if isinstance(value, tuple) else []
    return value


def _write_selection_store_if_changed(
    kind: str,
    selections: Mapping[str, SelectionValue],
) -> None:
    _write_session_value_if_changed(
        _selection_store_state_key(kind),
        dict(selections),
    )


def _preserve_hidden_widget_state(*keys: str) -> None:
    """Keep authored widget values when their mode temporarily hides them.

    Streamlit removes widget-owned keys after a widget is not rendered for a
    run.  Reassigning the current value before rendering detaches it from that
    cleanup cycle, so changing between character and background modes behaves
    like hiding a panel instead of resetting its form.
    """

    for key in keys:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def _preserve_category_widget_state(
    kind: str,
    categories: Sequence[TagCategory],
) -> None:
    """Detach only existing category widgets before their whole mode is hidden."""

    _preserve_hidden_widget_state(
        *(
            _category_widget_key(kind, category)
            for category in categories
            if _category_widget_key(kind, category) in st.session_state
        )
    )


def _write_session_value_if_changed(key: str, value: object) -> bool:
    """Avoid notifying Streamlit when a durable state value is already equal."""

    if key in st.session_state and st.session_state.get(key) == value:
        return False
    st.session_state[key] = value
    return True


def _remember_page_widget_value(widget_key: str) -> None:
    """Mirror a page-owned widget before Streamlit can clean it on navigation."""

    durable_key = _PAGE_WIDGET_DURABLE_STATE_KEYS[widget_key]
    if widget_key in st.session_state:
        _write_session_value_if_changed(durable_key, st.session_state.get(widget_key))


def _remember_group_navigation(kind: str) -> None:
    _remember_page_widget_value(_GROUP_NAVIGATION_STATE_KEYS[kind])


def _restore_mirrored_page_widget(widget_key: str) -> None:
    if widget_key in st.session_state:
        return
    durable_key = _PAGE_WIDGET_DURABLE_STATE_KEYS[widget_key]
    if durable_key not in st.session_state:
        return
    value = st.session_state.get(durable_key)
    if widget_key == MODE_STATE_KEY and value not in _MODE_LABELS:
        return
    if widget_key == GENDER_STATE_KEY:
        if isinstance(value, CharacterGender):
            pass
        elif isinstance(value, str):
            try:
                value = CharacterGender(value)
            except ValueError:
                return
        else:
            return
    invalid_identity = (
        widget_key == IDENTITY_MODE_STATE_KEY and value not in CHARACTER_IDENTITY_MODES
    )
    invalid_boolean = widget_key == ADULT_MODE_STATE_KEY and not isinstance(value, bool)
    invalid_text = widget_key in {
        TITLE_STATE_KEY,
        CHARACTER_NAME_STATE_KEY,
        CHARACTER_CUSTOM_STATE_KEY,
        BACKGROUND_CUSTOM_STATE_KEY,
        NEGATIVE_CUSTOM_STATE_KEY,
        CHARACTER_EDITOR_STATE_KEY,
        BACKGROUND_EDITOR_STATE_KEY,
        NEGATIVE_EDITOR_STATE_KEY,
        CHARACTER_GROUP_STATE_KEY,
        BACKGROUND_GROUP_STATE_KEY,
    } and not isinstance(value, str)
    if invalid_identity or invalid_boolean or invalid_text:
        return
    st.session_state[widget_key] = value


def _restore_page_widget_state() -> None:
    """Restore only controls that the durable mode will render on this page."""

    _restore_mirrored_page_widget(MODE_STATE_KEY)
    raw_mode = st.session_state.get(MODE_STATE_KEY, "character")
    mode = raw_mode if raw_mode in _MODE_LABELS else "character"
    keys = [TITLE_STATE_KEY, NEGATIVE_CUSTOM_STATE_KEY, NEGATIVE_EDITOR_STATE_KEY]
    if mode in {"character", "both"}:
        keys.extend(
            (
                GENDER_STATE_KEY,
                IDENTITY_MODE_STATE_KEY,
                ADULT_MODE_STATE_KEY,
                CHARACTER_NAME_STATE_KEY,
                CHARACTER_CUSTOM_STATE_KEY,
                CHARACTER_GROUP_STATE_KEY,
                CHARACTER_EDITOR_STATE_KEY,
            )
        )
    if mode in {"background", "both"}:
        keys.extend(
            (
                BACKGROUND_CUSTOM_STATE_KEY,
                BACKGROUND_GROUP_STATE_KEY,
                BACKGROUND_EDITOR_STATE_KEY,
            )
        )
    for widget_key in keys:
        _restore_mirrored_page_widget(widget_key)


def _preserve_mode_transition_state(mode: PromptMode) -> None:
    """Detach widgets only on the rerun where their complete panel disappears."""

    previous = st.session_state.get(_LAST_MODE_STATE_KEY)
    if previous == mode:
        return
    if previous in {"character", "both"} and mode == "background":
        _preserve_category_widget_state("character", CHARACTER_CATEGORIES)
        _preserve_hidden_widget_state(
            CHARACTER_GROUP_STATE_KEY,
            GENDER_STATE_KEY,
            IDENTITY_MODE_STATE_KEY,
            ADULT_MODE_STATE_KEY,
            CHARACTER_NAME_STATE_KEY,
            CHARACTER_CUSTOM_STATE_KEY,
            *tuple(_CUSTOM_SPECIES_ZH_STATE_KEYS.values()),
            *tuple(_CUSTOM_SPECIES_EN_STATE_KEYS.values()),
        )
    if previous in {"background", "both"} and mode == "character":
        _preserve_category_widget_state("background", BACKGROUND_CATEGORIES)
        _preserve_hidden_widget_state(
            BACKGROUND_GROUP_STATE_KEY,
            BACKGROUND_CUSTOM_STATE_KEY,
        )
    if previous in _MODE_LABELS:
        _preserve_category_widget_state("negative", NEGATIVE_CATEGORIES)
    st.session_state[_LAST_MODE_STATE_KEY] = mode


def _ensure_defaults(kind: str, categories: Sequence[TagCategory]) -> None:
    """Initialize only the categories that are about to be rendered."""

    stored = _selection_store(kind)
    for category in categories:
        key = _category_widget_key(kind, category)
        if key not in st.session_state:
            source = stored.get(category.key, _default_widget_value(category))
            st.session_state[key] = _selection_widget_value(
                category,
                _normalize_selection_value(category, source),
            )
        if category.selection_mode == "multi":
            raw = st.session_state.get(key)
            values = (
                [str(value) for value in raw]
                if isinstance(raw, Sequence) and not isinstance(raw, str)
                else []
            )
            limit = _selection_limit(category)
            if limit is not None:
                values = values[:limit]
            if not isinstance(raw, list) or raw != values:
                st.session_state[key] = values
            _write_session_value_if_changed(
                _selection_tracker_key(kind, category),
                values,
            )


@cache
def _category_option_keys(category: TagCategory) -> frozenset[str]:
    return frozenset(option.key for option in category.options)


def _sanitize_visible_category_state(
    kind: str,
    source_categories: Sequence[TagCategory],
    visible_categories: Sequence[TagCategory],
    *,
    context: object | None = None,
) -> None:
    """Remove hidden or stale option keys before Streamlit renders the pills.

    Streamlit validates a pills widget's current value against its option list.
    Adult mode can make that list smaller between reruns, so values that became
    hidden must be removed before the widget is instantiated.  Doing this also
    guarantees that disabled adult-only values cannot leak into randomization
    or prompt output through old session state.
    """

    if (
        context is not None
        and st.session_state.get(_CHARACTER_SANITIZE_CONTEXT_STATE_KEY) == context
    ):
        return

    visible_by_key = {category.key: category for category in visible_categories}
    stored = _selection_store(kind)
    sanitized_store = dict(stored)
    for source in source_categories:
        key = _category_widget_key(kind, source)
        widget_exists = key in st.session_state
        if not widget_exists and source.key not in sanitized_store:
            continue
        raw = st.session_state.get(key) if widget_exists else sanitized_store.get(source.key)
        visible = visible_by_key.get(source.key)
        if visible is None:
            # Remove the key entirely so a category that becomes applicable
            # again can receive its catalog default on the next rerun.
            st.session_state.pop(key, None)
            st.session_state.pop(_selection_tracker_key(kind, source), None)
            st.session_state.pop(_selection_notice_key(kind, source), None)
            sanitized_store.pop(source.key, None)
            continue

        allowed = _category_option_keys(visible)
        if source.selection_mode == "multi":
            if isinstance(raw, Sequence) and not isinstance(raw, str):
                values = [str(value) for value in raw if str(value) in allowed]
            else:
                values = []
            limit = _selection_limit(visible)
            if limit is not None:
                values = values[:limit]
            if widget_exists:
                _write_session_value_if_changed(key, values)
                _write_session_value_if_changed(
                    _selection_tracker_key(kind, source),
                    values,
                )
            sanitized_store[source.key] = tuple(values)
        elif not isinstance(raw, str) or raw not in allowed:
            if widget_exists:
                _write_session_value_if_changed(key, None)
            sanitized_store[source.key] = None
        else:
            sanitized_store[source.key] = raw

    _write_selection_store_if_changed(kind, sanitized_store)

    if context is not None:
        _write_session_value_if_changed(
            _CHARACTER_SANITIZE_CONTEXT_STATE_KEY,
            context,
        )


def _read_selections(
    kind: str,
    categories: Sequence[TagCategory],
) -> dict[str, SelectionValue]:
    stored = _selection_store(kind)
    selections = dict(stored)
    for category in categories:
        key = _category_widget_key(kind, category)
        if key in st.session_state:
            raw = st.session_state.get(key)
        else:
            raw = stored.get(category.key, _default_widget_value(category))
        selections[category.key] = _normalize_selection_value(category, raw)
    _write_selection_store_if_changed(kind, selections)
    return {category.key: selections[category.key] for category in categories}


def _write_selections(
    kind: str,
    categories: Sequence[TagCategory],
    selections: Mapping[str, str | Sequence[str] | None],
) -> None:
    stored = _selection_store(kind)
    updated_store = dict(stored)
    for category in categories:
        raw = selections.get(category.key)
        key = _category_widget_key(kind, category)
        normalized = _normalize_selection_value(category, raw)
        updated_store[category.key] = normalized
        if key in st.session_state:
            st.session_state[key] = _selection_widget_value(category, normalized)
        if category.selection_mode == "multi" and key in st.session_state:
            values = list(normalized) if isinstance(normalized, tuple) else []
            st.session_state[_selection_tracker_key(kind, category)] = values
            st.session_state.pop(_selection_notice_key(kind, category), None)
    _write_selection_store_if_changed(kind, updated_store)


def _clear_selections(kind: str, categories: Sequence[TagCategory]) -> None:
    _write_selections(kind, categories, {})


def _selection_count(
    selections: Mapping[str, SelectionValue],
) -> int:
    count = 0
    for value in selections.values():
        if isinstance(value, tuple):
            count += len(value)
        elif value:
            count += 1
    return count


def _is_adult_only_category(category: TagCategory) -> bool:
    """Return whether every visible option already shares the 18+ boundary."""

    return bool(category.options) and all(option.adult_only for option in category.options)


def _option_display_label(label: str, *, category_is_adult_only: bool) -> str:
    """Avoid repeating the category-level 18+ marker on every pure-adult pill."""

    if category_is_adult_only:
        return label.removesuffix(_ADULT_LABEL_SUFFIX).rstrip()
    return label


def _contextual_option_display_label(
    category: TagCategory,
    option: TagOption,
    *,
    category_is_adult_only: bool,
) -> str:
    """Use the category heading as context and keep every option pill concise."""

    del category
    return _option_display_label(
        option.label_zh,
        category_is_adult_only=category_is_adult_only,
    )


def _render_local_styles() -> None:
    """Keep local actions and tag navigation stable, centered, and legible."""

    expression = next(
        category for category in CHARACTER_CATEGORIES if category.key == "expression"
    )
    expression_counts = {
        len(expression.options),
        sum(not option.adult_only for option in expression.options),
    }
    centered_expression_rules: list[str] = []
    for option_count in sorted(expression_counts):
        if option_count % _TAG_BUTTONS_PER_WIDE_ROW != _TAG_BUTTONS_PER_HALF_ROW:
            continue
        trailing_row = option_count // _TAG_BUTTONS_PER_WIDE_ROW + 1
        first_trailing_option = option_count - _TAG_BUTTONS_PER_HALF_ROW + 1
        count_guard = f':has(> button:nth-of-type({option_count}):last-of-type)'
        for offset, column in enumerate(range(3, 7)):
            option_index = first_trailing_option + offset
            centered_expression_rules.append(
                ".st-key-"
                f"{STATE_PREFIX}_tag_category_character_expression "
                '[data-testid="stButtonGroup"] '
                f'> [role="radiogroup"]{count_guard} '
                f"> button:nth-of-type({option_index}) {{"
                f"grid-row: {trailing_row} !important; "
                f"grid-column: {column} !important;"
                "}"
            )
    centered_expression_css = "\n".join(centered_expression_rules)

    st.markdown(
        f"""
        <style>
        .st-key-{CHARACTER_EDITOR_STATE_KEY} textarea,
        .st-key-{BACKGROUND_EDITOR_STATE_KEY} textarea,
        .st-key-{NEGATIVE_EDITOR_STATE_KEY} textarea {{
            resize: none !important;
            font-family: ui-monospace, SFMono-Regular, Consolas,
                "Liberation Mono", monospace !important;
            line-height: 1.55 !important;
        }}
        .st-key-{STATE_PREFIX}_character_fill_random button,
        .st-key-{STATE_PREFIX}_background_fill_random button {{
            background: linear-gradient(180deg, #68443b, #4c302b) !important;
            border-color: #aa7662 !important;
            color: #fff5e8 !important;
            font-weight: 750 !important;
        }}
        .st-key-{STATE_PREFIX}_character_fill_random button *,
        .st-key-{STATE_PREFIX}_background_fill_random button * {{
            color: #fff5e8 !important;
        }}
        .st-key-{STATE_PREFIX}_character_fill_random button:hover,
        .st-key-{STATE_PREFIX}_background_fill_random button:hover {{
            background: linear-gradient(180deg, #7b5044, #5b3932) !important;
            border-color: #d29a7f !important;
            color: #fffaf2 !important;
        }}
        .st-key-{STATE_PREFIX}_character_fill_random button:hover *,
        .st-key-{STATE_PREFIX}_background_fill_random button:hover * {{
            color: #fffaf2 !important;
        }}
        .st-key-{STATE_PREFIX}_character_reroll button,
        .st-key-{STATE_PREFIX}_background_reroll button {{
            background: linear-gradient(180deg, #f4cf86, #dfa952) !important;
            border-color: #efc36f !important;
            color: #21170f !important;
            box-shadow: 0 0.2rem 0.75rem rgba(223, 169, 82, 0.22) !important;
        }}
        .st-key-{STATE_PREFIX}_character_reroll button *,
        .st-key-{STATE_PREFIX}_background_reroll button * {{
            color: #21170f !important;
        }}
        .st-key-{STATE_PREFIX}_character_reroll button:hover,
        .st-key-{STATE_PREFIX}_background_reroll button:hover {{
            background: linear-gradient(180deg, #ffdda0, #e8b967) !important;
            border-color: #ffd98f !important;
            color: #17100b !important;
        }}
        .st-key-{STATE_PREFIX}_character_reroll button:hover *,
        .st-key-{STATE_PREFIX}_background_reroll button:hover * {{
            color: #17100b !important;
        }}
        .st-key-{STATE_PREFIX}_character_fill_random button,
        .st-key-{STATE_PREFIX}_character_reroll button,
        .st-key-{STATE_PREFIX}_character_clear button,
        .st-key-{STATE_PREFIX}_background_fill_random button,
        .st-key-{STATE_PREFIX}_background_reroll button,
        .st-key-{STATE_PREFIX}_background_clear button {{
            min-height: 3.125rem !important;
            padding: 0.55rem 0.9rem !important;
            font-size: 1rem !important;
            font-weight: 750 !important;
        }}
        .st-key-{STATE_PREFIX}_character_fill_random button *,
        .st-key-{STATE_PREFIX}_character_reroll button *,
        .st-key-{STATE_PREFIX}_character_clear button *,
        .st-key-{STATE_PREFIX}_background_fill_random button *,
        .st-key-{STATE_PREFIX}_background_reroll button *,
        .st-key-{STATE_PREFIX}_background_clear button * {{
            font-size: 1rem !important;
            font-weight: 750 !important;
        }}
        .st-key-{STATE_PREFIX}_character_group_nav,
        .st-key-{STATE_PREFIX}_character_group_nav_standard_adult,
        .st-key-{STATE_PREFIX}_character_group_nav_adult,
        .st-key-{STATE_PREFIX}_background_group_nav {{
            container-type: inline-size;
        }}
        .st-key-{STATE_PREFIX}_character_group_nav
        [data-testid="stButtonGroup"] > [role="radiogroup"],
        .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"],
        .st-key-{STATE_PREFIX}_character_group_nav_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"],
        .st-key-{STATE_PREFIX}_background_group_nav
        [data-testid="stButtonGroup"] > [role="radiogroup"] {{
            display: grid !important;
            grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
            width: 100% !important;
            gap: 0.25rem !important;
        }}
        .st-key-{STATE_PREFIX}_character_group_nav
        [data-testid="stButtonGroup"] > [role="radiogroup"] > button,
        .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"] > button,
        .st-key-{STATE_PREFIX}_character_group_nav_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"] > button,
        .st-key-{STATE_PREFIX}_background_group_nav
        [data-testid="stButtonGroup"] > [role="radiogroup"] > button {{
            width: 100% !important;
            max-width: none !important;
            min-width: 0 !important;
            min-height: 3.125rem !important;
            height: 3.125rem !important;
            max-height: 3.125rem !important;
            padding: 0.3rem 0.5rem !important;
            align-self: stretch !important;
            align-items: center !important;
            justify-content: center !important;
            text-align: center !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            line-height: 1.25 !important;
            box-sizing: border-box !important;
        }}
        .st-key-{STATE_PREFIX}_character_group_nav_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"] {{
            grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
        }}
        .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"] {{
            grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
        }}
        .st-key-{STATE_PREFIX}_character_group_nav_adult
        [data-testid="stButtonGroup"] > [role="radiogroup"]
        > button:nth-last-child(4) {{
            grid-column: 1 !important;
        }}
        @container (max-width: 42rem) {{
            .st-key-{STATE_PREFIX}_character_group_nav
            [data-testid="stButtonGroup"] > [role="radiogroup"],
            .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"],
            .st-key-{STATE_PREFIX}_character_group_nav_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"],
            .st-key-{STATE_PREFIX}_background_group_nav
            [data-testid="stButtonGroup"] > [role="radiogroup"] {{
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            }}
            .st-key-{STATE_PREFIX}_character_group_nav_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"] {{
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            }}
            .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"] {{
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            }}
            .st-key-{STATE_PREFIX}_character_group_nav_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"]
            > button:nth-last-child(4) {{
                grid-column: 1 !important;
            }}
        }}
        @container (max-width: 24rem) {{
            .st-key-{STATE_PREFIX}_character_group_nav
            [data-testid="stButtonGroup"] > [role="radiogroup"],
            .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"],
            .st-key-{STATE_PREFIX}_background_group_nav
            [data-testid="stButtonGroup"] > [role="radiogroup"] {{
                grid-template-columns: minmax(0, 1fr) !important;
            }}
            .st-key-{STATE_PREFIX}_character_group_nav_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"] {{
                grid-template-columns: minmax(0, 1fr) !important;
            }}
            .st-key-{STATE_PREFIX}_character_group_nav_standard_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"] {{
                grid-template-columns: minmax(0, 1fr) !important;
            }}
            .st-key-{STATE_PREFIX}_character_group_nav_adult
            [data-testid="stButtonGroup"] > [role="radiogroup"]
            > button:nth-last-child(4) {{
                grid-column: 1 !important;
            }}
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"] {{
            container: if-tag-category / inline-size;
            padding-block: 0.2rem 0.4rem;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        [data-testid="stButtonGroup"] > [data-testid="stWidgetLabel"] {{
            margin-bottom: 0.75rem !important;
            color: var(--forge-text) !important;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        [data-testid="stButtonGroup"] > [data-testid="stWidgetLabel"]
        [data-testid="stMarkdownContainer"],
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        [data-testid="stButtonGroup"] > [data-testid="stWidgetLabel"]
        [data-testid="stMarkdownContainer"] p {{
            color: inherit !important;
            font-size: 1rem !important;
            font-weight: 700 !important;
            line-height: 1.4 !important;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        [data-testid="stButtonGroup"] > :is([role="radiogroup"], [role="toolbar"]) {{
            display: grid !important;
            grid-template-columns: repeat(8, 8.625rem) !important;
            width: 100% !important;
            max-width: none !important;
            justify-content: space-between !important;
            column-gap: 0.25rem !important;
            row-gap: 0.625rem !important;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        [data-testid="stButtonGroup"]
        > :is([role="radiogroup"], [role="toolbar"])
        > button[data-variant="pills"] {{
            flex: none !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: none !important;
            min-height: 3.125rem !important;
            height: 3.125rem !important;
            max-height: 3.125rem !important;
            padding: 0.3rem 0.5rem !important;
            display: flex !important;
            align-self: stretch !important;
            align-items: center !important;
            justify-content: center !important;
            text-align: center !important;
            white-space: normal !important;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        button[data-variant="pills"] :is(div, span, [data-testid="stMarkdownContainer"], p) {{
            min-width: 0 !important;
            width: 100% !important;
            white-space: normal !important;
            text-align: center !important;
            text-overflow: clip !important;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        button[data-variant="pills"] [data-testid="stMarkdownContainer"] {{
            height: 100% !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }}
        [class*="st-key-{STATE_PREFIX}_tag_category_"]
        button[data-variant="pills"] p {{
            margin: 0 !important;
            overflow-wrap: anywhere !important;
            line-height: 1.25 !important;
        }}
        @container if-tag-category (min-width: 72.001rem) {{
            {centered_expression_css}
        }}
        @container if-tag-category (max-width: 72rem) {{
            [class*="st-key-{STATE_PREFIX}_tag_category_"]
            [data-testid="stButtonGroup"] > :is([role="radiogroup"], [role="toolbar"]) {{
                grid-template-columns: repeat(4, 8.625rem) !important;
            }}
        }}
        @container if-tag-category (max-width: 35rem) {{
            [class*="st-key-{STATE_PREFIX}_tag_category_"]
            [data-testid="stButtonGroup"] > :is([role="radiogroup"], [role="toolbar"]) {{
                grid-template-columns: repeat(2, 8.625rem) !important;
            }}
        }}
        @container if-tag-category (max-width: 18rem) {{
            [class*="st-key-{STATE_PREFIX}_tag_category_"]
            [data-testid="stButtonGroup"] > :is([role="radiogroup"], [role="toolbar"]) {{
                grid-template-columns: minmax(0, 1fr) !important;
            }}
        }}
        .if-tag-selection-summary {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 0.35rem 0.75rem;
            margin: 0.45rem 0 0.8rem;
            padding: 0.7rem 0.85rem;
            border: 1px solid var(--forge-border);
            border-radius: 12px;
            background: rgba(53, 42, 50, 0.66);
        }}
        .if-tag-selection-summary strong {{
            color: var(--forge-text);
        }}
        .if-tag-selection-summary span {{
            color: var(--forge-muted);
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _identity_species_key(identity_mode: CharacterIdentityMode) -> str | None:
    return _SPECIES_CATEGORY_KEYS.get(identity_mode)


def _active_custom_species_values(
    identity_mode: CharacterIdentityMode,
) -> tuple[str, str]:
    if identity_mode == "standard":
        return "", ""
    zh = str(
        st.session_state.get(
            _CUSTOM_SPECIES_ZH_STATE_KEYS[identity_mode],
            st.session_state.get(_CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS[identity_mode], ""),
        )
    ).strip()
    en = str(
        st.session_state.get(
            _CUSTOM_SPECIES_EN_STATE_KEYS[identity_mode],
            st.session_state.get(_CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS[identity_mode], ""),
        )
    ).strip()
    return zh, en


def _restore_custom_species_widget_state(identity_mode: CharacterIdentityMode) -> None:
    if identity_mode == "standard":
        return
    for widget_keys, durable_keys in (
        (_CUSTOM_SPECIES_ZH_STATE_KEYS, _CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS),
        (_CUSTOM_SPECIES_EN_STATE_KEYS, _CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS),
    ):
        widget_key = widget_keys[identity_mode]
        durable_key = durable_keys[identity_mode]
        if widget_key in st.session_state:
            _write_session_value_if_changed(durable_key, st.session_state.get(widget_key, ""))
        elif durable_key in st.session_state:
            st.session_state[widget_key] = st.session_state.get(durable_key, "")


def _remember_custom_species_value(
    identity_mode: CharacterIdentityMode,
    *,
    english: bool,
) -> None:
    widget_keys = _CUSTOM_SPECIES_EN_STATE_KEYS if english else _CUSTOM_SPECIES_ZH_STATE_KEYS
    durable_keys = (
        _CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS if english else _CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS
    )
    _write_session_value_if_changed(
        durable_keys[identity_mode],
        st.session_state.get(widget_keys[identity_mode], ""),
    )


def _clear_inactive_identity_state(identity_mode: CharacterIdentityMode) -> None:
    """Discard custom values from identity branches that are no longer visible."""

    for branch in ("beast_humanoid", "furry"):
        if branch == identity_mode:
            continue
        for keys in (
            _CUSTOM_SPECIES_ZH_STATE_KEYS,
            _CUSTOM_SPECIES_EN_STATE_KEYS,
            _CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS,
            _CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS,
            _CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS,
        ):
            st.session_state.pop(keys[branch], None)


def _clear_unused_custom_species_state(identity_mode: CharacterIdentityMode) -> None:
    species_key = _identity_species_key(identity_mode)
    if species_key is None or "other" in _selected_category_values("character", species_key):
        return
    for keys in (
        _CUSTOM_SPECIES_ZH_STATE_KEYS,
        _CUSTOM_SPECIES_EN_STATE_KEYS,
        _CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS,
        _CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS,
        _CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS,
    ):
        st.session_state.pop(keys[identity_mode], None)


def _clear_all_custom_species_state() -> None:
    for branch in ("beast_humanoid", "furry"):
        for keys in (
            _CUSTOM_SPECIES_ZH_STATE_KEYS,
            _CUSTOM_SPECIES_EN_STATE_KEYS,
            _CUSTOM_SPECIES_DURABLE_ZH_STATE_KEYS,
            _CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS,
            _CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS,
        ):
            st.session_state.pop(keys[branch], None)


def _clear_species_detail_state(species_category_key: str) -> None:
    """Discard detail pills that were validated against a different species."""

    identity_mode = next(
        (
            mode
            for mode, category_key in _SPECIES_CATEGORY_KEYS.items()
            if category_key == species_category_key
        ),
        None,
    )
    if identity_mode is None:
        return
    detail_keys = CHARACTER_IDENTITY_CATEGORY_KEYS[identity_mode] - {species_category_key}
    detail_categories = tuple(
        category for category in CHARACTER_CATEGORIES if category.key in detail_keys
    )
    _clear_selections("character", detail_categories)


def _validate_translated_species(value: object) -> str:
    """Accept only a short English prompt phrase from an injected translator."""

    if not isinstance(value, str):
        raise ValueError("翻譯服務必須回傳英文文字")
    return validate_custom_species_english(value)


def _translate_custom_species(identity_mode: CharacterIdentityMode) -> None:
    """Translate through an injected offline translator and fail closed."""

    if identity_mode == "standard":
        return
    status_key = _CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS[identity_mode]
    source = str(st.session_state.get(_CUSTOM_SPECIES_ZH_STATE_KEYS[identity_mode], "")).strip()
    if not source:
        st.session_state[status_key] = ("error", "請先填寫繁中物種名稱。")
        return
    translator = st.session_state.get(SPECIES_TRANSLATION_HOOK_STATE_KEY)
    try:
        if callable(translator):
            translated = _validate_translated_species(
                cast("Callable[[str], object]", translator)(source)
            )
        else:
            translated = OfflineSpeciesTranslationService().translate(source).species_prompt_en
    except Exception as exc:
        st.session_state[status_key] = (
            "error",
            f"離線翻譯失敗，原本的英文欄與 Prompt 都沒有改變：{exc}",
        )
        return
    species_key = _identity_species_key(identity_mode)
    if species_key is not None:
        _clear_species_detail_state(species_key)
    st.session_state[_CUSTOM_SPECIES_EN_STATE_KEYS[identity_mode]] = translated
    st.session_state[_CUSTOM_SPECIES_DURABLE_EN_STATE_KEYS[identity_mode]] = translated
    st.session_state[status_key] = (
        "success",
        f"已離線填入英文：{translated}",
    )


def _clear_species_translation_status(identity_mode: CharacterIdentityMode) -> None:
    st.session_state.pop(_CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS[identity_mode], None)


def _handle_custom_species_english_change(identity_mode: CharacterIdentityMode) -> None:
    """Invalidate details that were chosen for the previous custom species."""

    _remember_custom_species_value(identity_mode, english=True)
    _clear_species_translation_status(identity_mode)
    species_key = _identity_species_key(identity_mode)
    if species_key is not None:
        _clear_species_detail_state(species_key)


def _handle_custom_species_zh_change(identity_mode: CharacterIdentityMode) -> None:
    _remember_custom_species_value(identity_mode, english=False)
    _clear_species_translation_status(identity_mode)


def _stored_output(key: str) -> str:
    raw = st.session_state.get(key, "")
    return str(raw) if raw else ""


def _prompt_fragments(prompt: str) -> tuple[str, ...]:
    """Split comma/newline Prompt text without rewriting the author's fragments."""

    return tuple(
        fragment.strip()
        for line in prompt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for fragment in line.split(",")
        if fragment.strip()
    )


def _prompt_fragment_identity(fragment: str) -> str:
    """Return a comparison-only identity while preserving the displayed spelling."""

    return " ".join(normalize("NFKC", fragment).split()).casefold()


def _manual_prompt_fragments(editor_text: str, generated_prompt: str) -> tuple[str, ...]:
    """Extract author-added fragments from an editor containing generated Prompt text."""

    remaining_generated = Counter(
        _prompt_fragment_identity(fragment) for fragment in _prompt_fragments(generated_prompt)
    )
    manual: list[str] = []
    for fragment in _prompt_fragments(editor_text):
        identity = _prompt_fragment_identity(fragment)
        if remaining_generated[identity] > 0:
            remaining_generated[identity] -= 1
        else:
            manual.append(fragment)
    return tuple(manual)


def _merge_generated_and_manual_prompt(
    generated_prompt: str,
    manual_fragments: Sequence[str],
) -> str:
    """Append every author fragment after fresh generated tags in authored order.

    A manual fragment may intentionally equal a generated fragment.  Keeping
    that extra occurrence preserves the author's intent across a later tag
    removal; silently absorbing it here would make it impossible to recover.
    """

    generated = generated_prompt.strip().rstrip(",")
    appendable = tuple(fragment.strip() for fragment in manual_fragments if fragment.strip())
    manual = ", ".join(appendable)
    if generated and manual:
        return f"{generated}, {manual}"
    return generated or manual


def _queue_character_editor_manual_for_reroll() -> None:
    """Carry authored fragments across the identity branch chosen by full reroll.

    Identity is otherwise a safety context boundary: an explicit identity
    switch resets the editor.  Full reroll also changes identity internally,
    but from the author's perspective it is a tag refresh, so it gets this
    one-shot exception without weakening explicit context transitions.
    """

    baseline_key = _PROMPT_EDITOR_BASELINE_STATE_KEYS[CHARACTER_OUTPUT_STATE_KEY]
    previous_generated_source = st.session_state.get(baseline_key)
    if not (
        isinstance(previous_generated_source, tuple)
        and len(previous_generated_source) == 2
        and isinstance(previous_generated_source[0], str)
    ):
        st.session_state.pop(_CHARACTER_EDITOR_REROLL_MANUAL_STATE_KEY, None)
        return
    editor_text = str(st.session_state.get(CHARACTER_EDITOR_STATE_KEY, ""))
    st.session_state[_CHARACTER_EDITOR_REROLL_MANUAL_STATE_KEY] = (
        _manual_prompt_fragments(editor_text, previous_generated_source[0])
    )


def _sync_prompt_editor(
    output_key: str,
    generated_prompt: str,
    *,
    context: str | None = None,
) -> str:
    """Keep direct edits until the generated source for that field changes.

    A normal Streamlit rerun must not erase text the author just typed.  When a
    tag changes within the same safety context, generated fragments are rebuilt
    and author-added comma/newline fragments are moved behind them.  A gender,
    identity, adult-mode, or Prompt-mode context change still resets the editor
    so stale manual text cannot cross that boundary.
    """

    editor_key = _PROMPT_EDITOR_STATE_KEYS[output_key]
    baseline_key = _PROMPT_EDITOR_BASELINE_STATE_KEYS[output_key]
    durable_key = _PROMPT_EDITOR_DURABLE_STATE_KEYS[output_key]
    generated_source = (generated_prompt, context)
    previous_generated_source = st.session_state.get(baseline_key)
    reroll_manual: tuple[str, ...] | None = None
    if output_key == CHARACTER_OUTPUT_STATE_KEY:
        raw_reroll_manual = st.session_state.pop(
            _CHARACTER_EDITOR_REROLL_MANUAL_STATE_KEY,
            None,
        )
        if isinstance(raw_reroll_manual, tuple) and all(
            isinstance(fragment, str) for fragment in raw_reroll_manual
        ):
            reroll_manual = raw_reroll_manual
    if editor_key not in st.session_state:
        next_editor = generated_prompt
    elif previous_generated_source != generated_source:
        previous_generated = ""
        previous_context: object = object()
        if (
            isinstance(previous_generated_source, tuple)
            and len(previous_generated_source) == 2
            and isinstance(previous_generated_source[0], str)
        ):
            previous_generated = previous_generated_source[0]
            previous_context = previous_generated_source[1]
        if previous_context == context:
            manual_fragments = _manual_prompt_fragments(
                str(st.session_state.get(editor_key, "")),
                previous_generated,
            )
            next_editor = _merge_generated_and_manual_prompt(
                generated_prompt,
                manual_fragments,
            )
        elif reroll_manual is not None:
            next_editor = _merge_generated_and_manual_prompt(
                generated_prompt,
                reroll_manual,
            )
        else:
            next_editor = generated_prompt
    else:
        next_editor = str(st.session_state.get(editor_key, ""))
    _write_session_value_if_changed(editor_key, next_editor)
    _write_session_value_if_changed(durable_key, next_editor)
    _write_session_value_if_changed(baseline_key, generated_source)
    return str(st.session_state.get(editor_key, ""))


def _keep_last_valid_output(
    key: str,
    candidate: str,
    error: str | None,
    *,
    candidate_is_safe_fallback: bool = False,
    context_key: str | None = None,
    context: str | None = None,
) -> tuple[str, bool]:
    """Persist valid output and retain it when a same-context rebuild fails.

    A tag click causes a full Streamlit rerun.  A temporarily incomplete
    combination (for example, adult anatomy chosen before compatible clothing)
    must not erase a previously valid prompt.  Character output is stricter:
    it may only be reused for the exact same gender/adult-mode context, so a
    safety downgrade can never expose an older adult or wrong-gender prompt.
    """

    previous = _stored_output(key)
    context_matches = context_key is None or st.session_state.get(context_key) == context
    if error is not None and candidate_is_safe_fallback:
        if previous and context_matches:
            return previous, True
        _write_session_value_if_changed(key, candidate)
        if context_key is not None:
            _write_session_value_if_changed(context_key, context)
        return candidate, False
    if error is not None and not candidate:
        if previous and context_matches:
            return previous, True
        _write_session_value_if_changed(key, "")
        if context_key is not None:
            _write_session_value_if_changed(context_key, context)
        return "", False

    _write_session_value_if_changed(key, candidate)
    if context_key is not None:
        _write_session_value_if_changed(context_key, context)
    return candidate, False


def _enforce_selection_limit(kind: str, category: TagCategory) -> None:
    """Make newest compatible pills win, then enforce the authoring limit."""

    if category.selection_mode != "multi":
        return
    limit = _selection_limit(category)
    key = _category_widget_key(kind, category)
    raw = st.session_state.get(key)
    values = (
        [str(value) for value in raw]
        if isinstance(raw, Sequence) and not isinstance(raw, str)
        else []
    )
    tracker_key = _selection_tracker_key(kind, category)
    notice_key = _selection_notice_key(kind, category)
    previous = st.session_state.get(tracker_key)
    previous_values = (
        [str(value) for value in previous]
        if isinstance(previous, Sequence) and not isinstance(previous, str)
        else []
    )
    added_values = [value for value in values if value not in previous_values]
    if added_values:
        values = list(
            resolve_mutually_exclusive_selection(
                category.key,
                values,
                newly_selected_key=added_values[-1],
            )
        )
        st.session_state[key] = values

    if limit is None or len(values) <= limit:
        st.session_state[tracker_key] = values
        st.session_state.pop(notice_key, None)
        if (
            kind == "character"
            and category.key in _SPECIES_CATEGORY_KEYS.values()
            and values != previous_values
        ):
            _clear_species_detail_state(category.key)
        return

    if limit == 1:
        added = [value for value in values if value not in previous_values]
        replacement = added[-1:] if added else values[-1:]
        st.session_state[key] = replacement
        st.session_state[tracker_key] = replacement
        st.session_state.pop(notice_key, None)
        if kind == "character" and category.key in _SPECIES_CATEGORY_KEYS.values():
            _clear_species_detail_state(category.key)
        return

    restored = previous_values[:limit] if previous_values else values[:limit]
    st.session_state[key] = restored
    st.session_state[notice_key] = (
        f"「{category.label_zh}」最多選 {limit} 項；請先取消一項，再選新標籤。"
    )


def _clear_category_selection_state(kind: str, category_keys: frozenset[str]) -> None:
    """Clear only the requested category slots while preserving every other tag."""

    categories = {
        category.key: category
        for category in (*CHARACTER_CATEGORIES, *BACKGROUND_CATEGORIES, *NEGATIVE_CATEGORIES)
    }
    stored = _selection_store(kind)
    updated_store = dict(stored)
    for category_key in category_keys:
        category = categories[category_key]
        widget_key = _category_widget_key(kind, category)
        empty_value: SelectionValue = () if category.selection_mode == "multi" else None
        updated_store[category_key] = empty_value
        if widget_key in st.session_state:
            st.session_state[widget_key] = _selection_widget_value(category, empty_value)
        st.session_state.pop(_selection_notice_key(kind, category), None)
        if category.selection_mode == "multi":
            st.session_state[_selection_tracker_key(kind, category)] = []
    _write_selection_store_if_changed(kind, updated_store)


def _remove_category_selection_keys(
    kind: str,
    category_key: str,
    blocked_keys: frozenset[str],
) -> None:
    """Remove incompatible values while preserving compatible multi-select state."""

    category = next(
        item
        for item in (*CHARACTER_CATEGORIES, *BACKGROUND_CATEGORIES, *NEGATIVE_CATEGORIES)
        if item.key == category_key
    )
    selected = _selected_category_values(kind, category_key)
    retained = tuple(key for key in selected if key not in blocked_keys)
    if retained == selected:
        return
    value: SelectionValue = (
        retained
        if category.selection_mode == "multi"
        else (retained[0] if retained else None)
    )
    stored = _selection_store(kind)
    stored[category_key] = value
    _write_selection_store_if_changed(kind, stored)
    widget_key = _category_widget_key(kind, category)
    if widget_key in st.session_state:
        st.session_state[widget_key] = _selection_widget_value(category, value)
    st.session_state.pop(_selection_notice_key(kind, category), None)
    if category.selection_mode == "multi":
        st.session_state[_selection_tracker_key(kind, category)] = list(retained)


def _normalize_manual_clothing_change(
    kind: str,
    category: TagCategory,
    selected: set[str],
) -> None:
    """Make the newest manual nude/clothing choice win in visible UI state."""

    if category.key == "outfit_archetype":
        if selected & _CLOTHINGLESS_OUTFIT_KEYS:
            cleared_categories = _LAYERED_CORE_GARMENT_CATEGORY_KEYS
        elif "topless" in selected:
            cleared_categories = _UPPER_LAYERED_GARMENT_CATEGORY_KEYS
        else:
            return
        dependent_states = frozenset(
            state_key
            for garment_key in cleared_categories
            for state_key in _LAYERED_GARMENT_DEPENDENT_STATES.get(garment_key, ())
        )
        _clear_category_selection_state(
            kind,
            frozenset({*cleared_categories, *dependent_states}),
        )
        return

    if category.key not in _LAYERED_CORE_GARMENT_CATEGORY_KEYS or not selected:
        return
    conflicting_anchors = set(_CLOTHINGLESS_OUTFIT_KEYS)
    if category.key in _UPPER_LAYERED_GARMENT_CATEGORY_KEYS:
        conflicting_anchors.add("topless")
    active_conflicts = set(
        _selected_category_values(kind, "outfit_archetype")
    ) & conflicting_anchors
    if not active_conflicts:
        return

    _remove_category_selection_keys(
        kind,
        "outfit_archetype",
        frozenset(active_conflicts),
    )
    for category_key, blocked_keys in _NUDITY_DEPENDENT_OPTION_KEYS.items():
        _remove_category_selection_keys(kind, category_key, blocked_keys)
    if category.key in _UPPER_LAYERED_GARMENT_CATEGORY_KEYS:
        _remove_category_selection_keys(
            kind,
            "pose",
            _TOPLESS_DEPENDENT_POSE_KEYS,
        )
    _clear_category_selection_state(kind, _NUDITY_DEPENDENT_CATEGORY_KEYS)


def _normalize_clothingless_finishing_change(
    kind: str,
    category: TagCategory,
    selected: set[str],
) -> None:
    """Remove stale fabric and clothing-palette values from a nude anchor."""

    if category.key != "outfit_archetype":
        return
    clothingless = selected & _CLOTHINGLESS_OUTFIT_KEYS
    if not clothingless:
        return

    for finishing_category in ("outfit_materials", "outfit_palette"):
        allowed = frozenset().union(
            *(
                _CLOTHINGLESS_ALLOWED_FINISHING_KEYS[anchor][finishing_category]
                for anchor in clothingless
            )
        )
        current = frozenset(_selected_category_values(kind, finishing_category))
        _remove_category_selection_keys(
            kind,
            finishing_category,
            current - allowed,
        )


def _normalize_manual_hosiery_change(
    kind: str,
    category: TagCategory,
    selected: set[str],
) -> None:
    """Make the latest hosiery style or length win without stale fragments."""

    style_key = "hosiery_style"
    length_key = "hosiery_length"
    if category.key == style_key:
        if not selected:
            _clear_category_selection_state(kind, frozenset({length_key}))
            return
        allowed = set.intersection(
            *(set(HOSIERY_ALLOWED_LENGTHS[option_key]) for option_key in selected)
        )
        current_lengths = frozenset(_selected_category_values(kind, length_key))
        blocked = current_lengths - allowed
        if not blocked:
            return
        _remove_category_selection_keys(kind, length_key, blocked)
        length_category = next(
            item for item in CHARACTER_CATEGORIES if item.key == length_key
        )
        st.session_state[_selection_notice_key(kind, length_category)] = (
            "襪長與最新襪類不相容，已清除舊襪長。"
        )
        return

    if category.key != length_key or not selected:
        return
    selected_length = next(iter(selected))
    current_styles = frozenset(_selected_category_values(kind, style_key))
    blocked_styles = frozenset(
        option_key
        for option_key in current_styles
        if selected_length not in HOSIERY_ALLOWED_LENGTHS[option_key]
    )
    if not blocked_styles:
        return
    _remove_category_selection_keys(kind, style_key, blocked_styles)
    style_category = next(item for item in CHARACTER_CATEGORIES if item.key == style_key)
    st.session_state[_selection_notice_key(kind, style_category)] = (
        "襪長與舊襪類不相容，已保留最新襪長並移除衝突襪類。"
    )


def _handle_category_change(kind: str, category: TagCategory) -> None:
    """Persist the edit, then apply limits and mutual-exclusion rules."""

    _enforce_selection_limit(kind, category)
    widget_key = _category_widget_key(kind, category)
    stored = _selection_store(kind)
    stored[category.key] = _normalize_selection_value(
        category,
        st.session_state.get(widget_key),
    )
    _write_selection_store_if_changed(kind, stored)
    if kind == "character" and (
        category.group == "服裝與配件"
        or category.group in _ADULT_RANDOM_REQUIRED_GROUPS
    ):
        st.session_state.pop(_ADULT_RANDOM_NOTICE_STATE_KEY, None)
    if kind != "character":
        return
    selected = set(_selected_category_values(kind, category.key))
    _normalize_manual_clothing_change(kind, category, selected)
    _normalize_clothingless_finishing_change(kind, category, selected)
    _normalize_manual_hosiery_change(kind, category, selected)
    dependent_state_keys = _LAYERED_GARMENT_DEPENDENT_STATES.get(category.key)
    if dependent_state_keys is not None and not selected:
        _clear_category_selection_state(kind, dependent_state_keys)
        return
    target_garment_key = _LAYERED_GARMENT_STATE_DEPENDENCIES.get(category.key)
    if (
        target_garment_key is not None
        and selected
        and not _selected_category_values(kind, target_garment_key)
    ):
        _clear_category_selection_state(kind, frozenset({category.key}))
        st.session_state[_selection_notice_key(kind, category)] = (
            "請先選擇對應服裝，再設定穿著狀態。"
        )
        return
    if not selected:
        return
    if category.key == _AFTERCARE_ACTIVITY_CATEGORY_KEY:
        _clear_category_selection_state(kind, _ACTIVE_ADULT_PHASE_CATEGORY_KEYS)
        _remove_category_selection_keys(
            kind,
            "expression",
            ADULT_BASE_ACTIVE_EXPRESSION_KEYS,
        )
        _remove_category_selection_keys(
            kind,
            "adult_female_expression",
            ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS,
        )
        _remove_category_selection_keys(
            kind,
            "adult_female_state",
            ADULT_FEMALE_ACTIVE_STATE_KEYS,
        )
        _remove_category_selection_keys(
            kind,
            "character_state",
            ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATE_KEYS,
        )
        return
    selects_active_adult_phase = (
        category.key in _ACTIVE_ADULT_PHASE_CATEGORY_KEYS
        or (
            category.key == "expression"
            and bool(selected & ADULT_BASE_ACTIVE_EXPRESSION_KEYS)
        )
        or (
            category.key == "adult_female_expression"
            and bool(selected & ADULT_FEMALE_ACTIVE_EXPRESSION_KEYS)
        )
        or (
            category.key == "adult_female_state"
            and bool(selected & ADULT_FEMALE_ACTIVE_STATE_KEYS)
        )
    )
    if selects_active_adult_phase:
        _clear_category_selection_state(
            kind,
            frozenset({_AFTERCARE_ACTIVITY_CATEGORY_KEY}),
        )
    selects_adult_activity = (
        category.key in _ACTIVE_ADULT_PHASE_CATEGORY_KEYS
        or (
            category.key == "expression"
            and bool(selected & ADULT_BASE_EXPRESSION_KEYS)
        )
        or (
            category.key == "adult_female_expression"
            and bool(selected & ADULT_FEMALE_EXPRESSION_KEYS)
        )
        or (
            category.key == "adult_female_state"
            and bool(selected)
        )
        or bool(
            selected
            & INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY.get(
                category.key,
                frozenset(),
            )
        )
    )
    if selects_adult_activity:
        _remove_category_selection_keys(
            kind,
            "character_state",
            ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATE_KEYS,
        )
    if category.key == "expression":
        _clear_category_selection_state(kind, frozenset({"adult_female_expression"}))
        _remove_category_selection_keys(
            kind,
            "character_state",
            CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS,
        )
    elif category.key == "adult_female_expression":
        _clear_category_selection_state(kind, frozenset({"expression"}))
        _remove_category_selection_keys(
            kind,
            "character_state",
            CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS,
        )
    elif category.key == "character_state":
        if selected & CHARACTER_STATE_EXPRESSION_OVERRIDE_KEYS:
            _clear_category_selection_state(
                kind,
                frozenset({"expression", "adult_female_expression"}),
            )
        if selected & ADULT_CONSENT_INCOMPATIBLE_CHARACTER_STATE_KEYS:
            _clear_category_selection_state(
                kind,
                frozenset(
                    {
                        _AFTERCARE_ACTIVITY_CATEGORY_KEY,
                        *_ACTIVE_ADULT_PHASE_CATEGORY_KEYS,
                    }
                ),
            )
            _clear_category_selection_state(
                kind,
                frozenset({"adult_female_expression", "adult_female_state"}),
            )
            _remove_category_selection_keys(
                kind,
                "expression",
                ADULT_BASE_EXPRESSION_KEYS,
            )
            for category_key, blocked_keys in (
                INCAPACITATED_CONFLICT_OPTION_KEYS_BY_CATEGORY.items()
            ):
                _remove_category_selection_keys(
                    kind,
                    category_key,
                    blocked_keys,
                )
    if category.key not in {
        _PARTNER_INTIMACY_CATEGORY_KEY,
        *_SOLO_ADULT_ACTIVITY_CATEGORY_KEYS,
    }:
        return
    conflicts = (
        _SOLO_ADULT_ACTIVITY_CATEGORY_KEYS
        if category.key == _PARTNER_INTIMACY_CATEGORY_KEY
        else frozenset({_PARTNER_INTIMACY_CATEGORY_KEY})
    )
    _clear_category_selection_state(kind, conflicts)


def _render_random_controls(
    *,
    kind: Literal["character", "background"],
    categories: Sequence[TagCategory],
    random_categories: Sequence[TagCategory] | None = None,
    clear_categories: Sequence[TagCategory] | None = None,
    fill_action: Callable[[], None] | None = None,
    reroll_action: Callable[[], None] | None = None,
) -> None:
    randomized_categories = random_categories if random_categories is not None else categories
    labels = "角色" if kind == "character" else "背景"
    st.caption("保留喜歡的選項再補空白，或讓全部標籤重新抽一次；抽完仍可逐項修改。")
    fill, reroll, clear = st.columns(3)
    if fill_action is not None:
        fill.button(
            "只補空白",
            key=f"{STATE_PREFIX}_{kind}_fill_random",
            type="primary",
            use_container_width=True,
            help="目前已選的標籤會鎖住，只替尚未選擇的分類找一個答案。",
            on_click=fill_action,
        )
    elif fill.button(
        "只補空白",
        key=f"{STATE_PREFIX}_{kind}_fill_random",
        type="primary",
        use_container_width=True,
        help="目前已選的標籤會鎖住，只替尚未選擇的分類找一個答案。",
    ):
        randomized = randomize_selections(
            randomized_categories,
            _read_selections(kind, randomized_categories),
            fill_blanks_only=True,
        )
        _write_selections(kind, randomized_categories, randomized)
        st.rerun()
    if reroll_action is not None:
        reroll.button(
            "全部重新隨機",
            key=f"{STATE_PREFIX}_{kind}_reroll",
            type="primary",
            use_container_width=True,
            help=f"重新抽取所有{labels}標籤。",
            on_click=reroll_action,
        )
    elif reroll.button(
        "全部重新隨機",
        key=f"{STATE_PREFIX}_{kind}_reroll",
        type="primary",
        use_container_width=True,
        help=f"重新抽取所有{labels}標籤。",
    ):
        randomized = randomize_selections(
            randomized_categories,
            _read_selections(kind, randomized_categories),
            fill_blanks_only=False,
        )
        _write_selections(kind, randomized_categories, randomized)
        st.rerun()
    if clear.button(
        f"清空{labels}標籤",
        key=f"{STATE_PREFIX}_{kind}_clear",
        use_container_width=True,
    ):
        _clear_selections(kind, clear_categories or categories)
        st.rerun()


def _grouped_category_rows(
    categories: tuple[TagCategory, ...],
) -> tuple[tuple[str, tuple[TagCategory, ...]], ...]:
    grouped: dict[str, list[TagCategory]] = {}
    for category in categories:
        grouped.setdefault(category.group, []).append(category)
    rows = tuple(
        (
            group,
            tuple(
                sorted(
                    items,
                    key=lambda category: _WARDROBE_CATEGORY_ORDER_INDEX.get(
                        category.key,
                        len(_WARDROBE_CATEGORY_ORDER_INDEX),
                    ),
                )
                if group == "服裝與配件"
                else items
            ),
        )
        for group, items in grouped.items()
    )
    trailing = {
        group: index for index, group in enumerate(_CHARACTER_TRAILING_GROUP_ORDER)
    }
    regular_rows = tuple(row for row in rows if row[0] not in trailing)
    trailing_rows = tuple(
        sorted(
            (row for row in rows if row[0] in trailing),
            key=lambda row: trailing[row[0]],
        )
    )
    return (*regular_rows, *trailing_rows)


def _balanced_category_rows(
    categories: tuple[TagCategory, ...],
) -> tuple[tuple[TagCategory, TagCategory | None], ...]:
    """Pair only categories with the same rendered height.

    A previous total-weight split still placed categories such as a 64-option
    fantasy catalog beside a 24-option body catalog.  Streamlit makes both
    columns in that row as tall as the larger widget, leaving what looks like a
    large unfinished area below the shorter one.  Exact four-column row counts
    are now the pairing contract.  A category without an equal-height partner
    receives the full eight-column width.  A scoped category may finish with a
    centered four-button half-row while retaining the same button size and the
    responsive four/two/one-column grids.
    """

    pending_by_height: dict[int, tuple[int, TagCategory]] = {}
    rows: list[tuple[int, TagCategory, TagCategory | None]] = []

    def flush_pending() -> None:
        for index, pending_category in pending_by_height.values():
            remainder = len(pending_category.options) % _TAG_BUTTONS_PER_WIDE_ROW
            allows_centered_half_row = (
                pending_category.key in _CENTERED_WIDE_HALF_ROW_CATEGORY_KEYS
                and remainder == _TAG_BUTTONS_PER_HALF_ROW
            )
            if remainder and not allows_centered_half_row:
                raise ValueError(
                    f"標籤分類 {pending_category.key!r} 的選項數必須填滿八欄寬版按鈕網格"
                )
            rows.append((index, pending_category, None))
        pending_by_height.clear()

    previous_is_legacy_wardrobe: bool | None = None
    for index, category in enumerate(categories):
        is_legacy_wardrobe = category.key in _LEGACY_WARDROBE_CATEGORY_KEYS
        if (
            previous_is_legacy_wardrobe is not None
            and is_legacy_wardrobe != previous_is_legacy_wardrobe
        ):
            # Keep the four broad legacy selectors after every composable
            # clothing editor. Pairing equal-height widgets across this
            # boundary made one legacy selector jump into the middle whenever
            # a new layered category happened to share its row height.
            flush_pending()
        previous_is_legacy_wardrobe = is_legacy_wardrobe
        option_count = len(category.options)
        if option_count % _TAG_BUTTONS_PER_HALF_ROW:
            raise ValueError(
                f"標籤分類 {category.key!r} 的選項數必須填滿四欄按鈕網格"
            )
        height = option_count // _TAG_BUTTONS_PER_HALF_ROW
        pending = pending_by_height.pop(height, None)
        if pending is None:
            pending_by_height[height] = (index, category)
            continue
        first_index, first = pending
        rows.append((first_index, first, category))

    flush_pending()
    rows.sort(key=lambda row: row[0])
    return tuple((left, right) for _index, left, right in rows)


@cache
def _category_render_metadata(
    category: TagCategory,
) -> tuple[dict[str, TagOption], bool, str, str | None]:
    ordered_options = (
        *(option for option in category.options if not option.adult_only),
        *(option for option in category.options if option.adult_only),
    )
    choices = {option.key: option for option in ordered_options}
    adult_count = sum(option.adult_only for option in category.options)
    category_is_adult_only = _is_adult_only_category(category)
    label = category.label_zh
    if category_is_adult_only and "18+" not in label:
        label = f"{label}{_ADULT_LABEL_SUFFIX}"
    selection_limit = _selection_limit(category)
    if category.selection_mode == "multi":
        if selection_limit == 1:
            label = f"{label}（選填，最多 1 項）"
        elif selection_limit is not None:
            label = f"{label}（可複選，最多 {selection_limit} 項）"
        else:
            label = f"{label}（可複選）"
    if adult_count and not category_is_adult_only:
        label = f"{label} · 18+ {adult_count} 項"

    help_text = category.help_text
    if category.key == "female_bust":
        order_hint = "選項依尺寸由最大到最小，從左到右、再由上到下排列。"
        help_text = f"{help_text} {order_hint}".strip()
    if category.selection_mode == "multi":
        multi_hint = "再次點選已選標籤即可取消。"
        if selection_limit == 1:
            multi_hint = f"這個分類可留白，{multi_hint}"
        elif selection_limit is not None:
            multi_hint = f"可同時選擇最多 {selection_limit} 個標籤；{multi_hint}"
        else:
            multi_hint = f"可同時選擇多個標籤；{multi_hint}"
        help_text = f"{help_text} {multi_hint}".strip()
    if adult_count and not category_is_adult_only:
        boundary = "標有 18+ 的選項只供已開啟成人模式的合意成年角色使用。"
        help_text = f"{help_text} {boundary}".strip()
    return choices, category_is_adult_only, label, help_text or None


def _selection_count_for_categories(
    categories: Sequence[TagCategory],
    selections: Mapping[str, SelectionValue],
) -> int:
    return _selection_count({category.key: selections.get(category.key) for category in categories})


def _preserve_group_transition_state(
    kind: str,
    *,
    active_group: str,
) -> None:
    """Keep values mounted by the previous group before Streamlit cleans them up."""

    previous_group = st.session_state.get(_LAST_ACTIVE_GROUP_STATE_KEYS[kind])
    group_changed = isinstance(previous_group, str) and previous_group != active_group
    if kind == "character" and group_changed:
        for identity_mode in ("beast_humanoid", "furry"):
            _restore_custom_species_widget_state(identity_mode)

    _write_session_value_if_changed(_LAST_ACTIVE_GROUP_STATE_KEYS[kind], active_group)


def _consume_legacy_show_all_state(kind: str) -> bool:
    """Migrate the removed dev15 switch into the lightweight overview once."""

    legacy_keys = _LEGACY_SHOW_ALL_GROUPS_STATE_KEYS[kind]
    was_enabled = any(st.session_state.get(key) is True for key in legacy_keys)
    for key in legacy_keys:
        st.session_state.pop(key, None)
    return was_enabled


def _render_group_overview(
    category_rows: tuple[tuple[str, tuple[TagCategory, ...]], ...],
    selections: Mapping[str, SelectionValue],
) -> None:
    """Show a cheap catalog overview without mounting every editor widget."""

    st.markdown("#### 全部分類總覽")
    st.caption("這裡只顯示摘要；直接點上方任一分類，就能開始編輯該區標籤。")
    columns = st.columns(2)
    for index, (group, categories) in enumerate(category_rows):
        selected = _selection_count_for_categories(categories, selections)
        option_count = sum(len(category.options) for category in categories)
        with columns[index % 2], st.container(border=True):
            st.markdown(f"**{group}**")
            st.caption(
                f"{len(categories)} 個細項分類 · {option_count} 個可選標籤 · 已選 {selected} 個"
            )


def _group_navigation_container_key(kind: str, groups: tuple[str, ...]) -> str:
    """Choose a semantic layout hook without inserting placeholder options."""

    if kind == "character" and groups == _STANDARD_ADULT_GROUP_ORDER:
        return f"{STATE_PREFIX}_{kind}_group_nav_standard_adult"
    if kind == "character" and groups[-4:] == _CHARACTER_TRAILING_GROUP_ORDER:
        return f"{STATE_PREFIX}_{kind}_group_nav_adult"
    return f"{STATE_PREFIX}_{kind}_group_nav"


def _visible_group_rows(
    kind: str,
    category_rows: tuple[tuple[str, tuple[TagCategory, ...]], ...],
    selections: Mapping[str, SelectionValue],
) -> tuple[tuple[str, tuple[TagCategory, ...]], ...]:
    """Render a one-click chapter navigator and mount at most one editor group."""

    active_key = _GROUP_NAVIGATION_STATE_KEYS.get(kind)
    if active_key is None or not category_rows:
        return category_rows

    groups = tuple(group for group, _categories in category_rows)
    navigation_options = (_GROUP_OVERVIEW_KEY, *groups)
    migrated_to_overview = _consume_legacy_show_all_state(kind)
    if migrated_to_overview:
        st.session_state[active_key] = _GROUP_OVERVIEW_KEY
    elif st.session_state.get(active_key) not in navigation_options:
        durable_active_key = _DURABLE_GROUP_NAVIGATION_STATE_KEYS[kind]
        durable_group = st.session_state.get(durable_active_key)
        st.session_state[active_key] = (
            durable_group if durable_group in navigation_options else groups[0]
        )

    navigation_container_key = _group_navigation_container_key(kind, groups)
    with st.container(key=navigation_container_key):
        active_group = st.pills(
            "想調整哪一類？",
            navigation_options,
            selection_mode="single",
            required=True,
            format_func=(
                lambda value: "全部分類總覽"
                if value == _GROUP_OVERVIEW_KEY
                else str(value)
            ),
            key=active_key,
            help="直接點分類即可切換；切換不會清除其他分類已選的內容。",
            on_change=_remember_group_navigation,
            args=(kind,),
            width="stretch",
        )
    active_group = str(active_group)
    _preserve_group_transition_state(
        kind,
        active_group=active_group,
    )
    _remember_group_navigation(kind)
    st.caption("切換分類不會清除已選內容；英文 Prompt 也會保留並同步更新。")
    if active_group == _GROUP_OVERVIEW_KEY:
        _render_group_overview(category_rows, selections)
        return ()
    return tuple(row for row in category_rows if row[0] == active_group)


def _render_category_group_body(
    kind: str,
    group: str,
    categories: tuple[TagCategory, ...],
    selections: Mapping[str, SelectionValue],
    after_category: Callable[[TagCategory], None] | None,
) -> None:
    hint = _GROUP_HINTS.get(group)
    if hint:
        st.caption(hint)
    selected = _selection_count_for_categories(categories, selections)
    st.caption(f"本區已選 {selected} 個標籤。")

    def render_category(category: TagCategory) -> None:
        choices, category_is_adult_only, label, help_text = _category_render_metadata(category)
        with st.container(
            key=f"{STATE_PREFIX}_tag_category_{kind}_{category.key}"
        ):
            st.pills(
                label,
                tuple(choices),
                selection_mode=cast("Literal['single', 'multi']", category.selection_mode),
                format_func=(
                    lambda value,
                    choices=choices,
                    category=category,
                    pure_adult=category_is_adult_only: (
                        _contextual_option_display_label(
                            category,
                            choices[value],
                            category_is_adult_only=pure_adult,
                        )
                    )
                ),
                key=_category_widget_key(kind, category),
                help=help_text,
                on_change=_handle_category_change,
                args=(kind, category),
                width="stretch",
            )
        notice = st.session_state.get(_selection_notice_key(kind, category))
        if notice:
            st.caption(f"⚠️ {notice}")
        if after_category is not None:
            after_category(category)

    for left_category, right_category in _balanced_category_rows(categories):
        if right_category is None:
            render_category(left_category)
            continue
        columns = st.columns(2)
        with columns[0]:
            render_category(left_category)
        with columns[1]:
            render_category(right_category)


def _render_category_groups(
    kind: str,
    categories: Sequence[TagCategory],
    *,
    selections: Mapping[str, SelectionValue] | None = None,
    after_category: Callable[[TagCategory], None] | None = None,
) -> None:
    category_tuple = tuple(categories)
    selection_snapshot = (
        selections if selections is not None else _read_selections(kind, category_tuple)
    )
    visible_rows = _visible_group_rows(
        kind,
        _grouped_category_rows(category_tuple),
        selection_snapshot,
    )
    _ensure_defaults(
        kind,
        tuple(category for _group, items in visible_rows for category in items),
    )
    for group_index, (group, group_categories) in enumerate(visible_rows):
        if kind in _GROUP_NAVIGATION_STATE_KEYS and len(visible_rows) == 1:
            with st.container(border=True):
                st.markdown(f"### {group}")
                _render_category_group_body(
                    kind,
                    group,
                    group_categories,
                    selection_snapshot,
                    after_category,
                )
            continue

        # Keep the expander label stable across reruns.  Streamlit uses it as
        # part of the element identity; appending a changing selection count
        # made an open section look like a new, collapsed section after a click.
        with st.expander(
            group,
            expanded=len(visible_rows) == 1 or group_index == 0,
        ):
            _render_category_group_body(
                kind,
                group,
                group_categories,
                selection_snapshot,
                after_category,
            )


def _selected_category_values(kind: str, category_key: str) -> tuple[str, ...]:
    widget_key = f"{STATE_PREFIX}_{kind}_{category_key}"
    raw = (
        st.session_state.get(widget_key)
        if widget_key in st.session_state
        else _selection_store(kind).get(category_key)
    )
    if isinstance(raw, str):
        return (raw,) if raw else ()
    if isinstance(raw, Sequence):
        return tuple(str(value) for value in raw if value)
    return ()


@lru_cache(maxsize=12)
def _cached_complete_character_categories(
    gender: CharacterGender,
    *,
    include_adult: bool,
    identity_mode: CharacterIdentityMode,
) -> tuple[TagCategory, ...]:
    """Build each gender/adult/identity catalog combination only once."""

    gender_categories = tuple(
        category for category in CHARACTER_CATEGORIES if _category_applies(category, gender)
    )
    identity_categories = filter_character_categories_by_identity(
        gender_categories,
        identity_mode,
    )
    if identity_mode != "standard":
        identity_categories = tuple(
            category for category in identity_categories if category.key != "fantasy_race"
        )
    complete_categories = filter_adult_options(
        identity_categories,
        include_adult=include_adult,
    )
    species_key = _identity_species_key(identity_mode)
    if species_key is not None:
        branch_keys = CHARACTER_IDENTITY_CATEGORY_KEYS[identity_mode]
        complete_categories = tuple(
            category for category in complete_categories if category.key in branch_keys
        ) + tuple(category for category in complete_categories if category.key not in branch_keys)
    return complete_categories


def _identity_filtered_character_categories(
    gender: CharacterGender,
    *,
    include_adult: bool,
    identity_mode: CharacterIdentityMode,
) -> tuple[tuple[TagCategory, ...], tuple[TagCategory, ...]]:
    """Return the cached random catalog and the currently renderable subset."""

    complete_categories = _cached_complete_character_categories(
        gender,
        include_adult=include_adult,
        identity_mode=identity_mode,
    )
    species_key = _identity_species_key(identity_mode)
    if species_key is None:
        return complete_categories, complete_categories

    selected_species = _selected_category_values("character", species_key)
    _, custom_species_en = _active_custom_species_values(identity_mode)
    species_ready = bool(selected_species) and (
        "other" not in selected_species or bool(custom_species_en)
    )
    if species_ready:
        return complete_categories, complete_categories

    detail_keys = CHARACTER_IDENTITY_CATEGORY_KEYS[identity_mode] - {species_key}
    visible_categories = tuple(
        category for category in complete_categories if category.key not in detail_keys
    )
    return complete_categories, visible_categories


def _randomizable_character_categories(
    categories: tuple[TagCategory, ...],
) -> tuple[TagCategory, ...]:
    """Keep author-chosen output purpose out of both random actions."""

    return tuple(
        category
        for category in categories
        if category.key != _CHARACTER_OUTPUT_PURPOSE_CATEGORY_KEY
    )


def _catalog_option_count(categories: tuple[TagCategory, ...]) -> int:
    return sum(len(category.options) for category in categories)


def _choose_random_identity_mode() -> CharacterIdentityMode:
    return random.choice(CHARACTER_IDENTITY_MODES)


def _choose_random_species(categories: Sequence[TagCategory], species_key: str) -> str:
    category = next(category for category in categories if category.key == species_key)
    candidates = tuple(option.key for option in category.options if option.key != "other")
    if not candidates:
        raise ValueError("目前身分沒有可供隨機選擇的內建物種")
    return random.choice(candidates)


def _with_random_species_anchor(
    identity_mode: CharacterIdentityMode,
    categories: Sequence[TagCategory],
    current: Mapping[str, SelectionValue],
) -> dict[str, SelectionValue]:
    """Guarantee that animal-identity randomization starts from one species."""

    seeded = dict(current)
    species_key = _identity_species_key(identity_mode)
    if species_key is not None and not seeded.get(species_key):
        seeded[species_key] = (_choose_random_species(categories, species_key),)
    return seeded


def _required_adult_random_groups(
    categories: Sequence[TagCategory],
) -> tuple[str, ...]:
    available_groups = {category.group for category in categories}
    return tuple(
        group for group in _ADULT_RANDOM_REQUIRED_GROUPS if group in available_groups
    )


def _remember_adult_random_coverage(
    categories: Sequence[TagCategory],
    randomized: Mapping[str, SelectionValue],
    required_groups: Sequence[str],
    *,
    fill_blanks_only: bool,
) -> None:
    selected_groups = {
        category.group
        for category in categories
        if category.group in required_groups and bool(randomized.get(category.key))
    }
    missing = tuple(group for group in required_groups if group not in selected_groups)
    if not missing:
        st.session_state.pop(_ADULT_RANDOM_NOTICE_STATE_KEY, None)
        return
    if fill_blanks_only and "成人身體細節（18+）" in missing:
        st.session_state[_ADULT_RANDOM_NOTICE_STATE_KEY] = (
            "已保留目前服裝；這套服裝不會露出成人身體細節，因此本次未自動加入該區標籤。"
            "請先選擇上身裸體、裸體、人體藝術彩繪或透視情趣內衣，再按「只補空白」；"
            "也可以直接使用「全部重新隨機」。"
        )
        return
    st.session_state[_ADULT_RANDOM_NOTICE_STATE_KEY] = (
        f"本次尚未補齊：{'、'.join(missing)}。請再試一次全部重新隨機。"
    )


def _selection_value_keys(value: SelectionValue) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if item)
    return ()


def _character_build_inputs(
    selections: Mapping[str, SelectionValue],
    identity_mode: CharacterIdentityMode,
) -> tuple[dict[str, SelectionValue], str]:
    """Resolve custom-species state without mutating the authored selection store."""

    active_selections = dict(selections)
    _, saved_custom_species_en = _active_custom_species_values(identity_mode)
    species_key = _identity_species_key(identity_mode)
    species_values = (
        _selection_value_keys(active_selections.get(species_key)) if species_key else ()
    )
    custom_species_en = saved_custom_species_en if "other" in species_values else ""
    if species_key is not None and "other" in species_values and not custom_species_en:
        # Keep the common prompt usable while the required English phrase is
        # unfinished. The Chinese display name is never used as a fallback.
        active_selections[species_key] = ()
    return active_selections, custom_species_en


def _validated_random_character_selections(
    gender: CharacterGender,
    identity_mode: CharacterIdentityMode,
    categories: tuple[TagCategory, ...],
    current: Mapping[str, SelectionValue],
    *,
    include_adult: bool,
    fill_blanks_only: bool,
    required_groups: Sequence[str],
    preserved_selections: Mapping[str, SelectionValue] | None = None,
    attempts: int = 8,
) -> dict[str, SelectionValue] | None:
    """Return a buildable random candidate so pills and Prompt update atomically."""

    for _attempt in range(attempts):
        randomized = randomize_selections(
            categories,
            current,
            fill_blanks_only=fill_blanks_only,
            required_groups=required_groups,
        )
        build_selections = dict(preserved_selections or {})
        build_selections.update(randomized)
        active_selections, custom_species_en = _character_build_inputs(
            build_selections,
            identity_mode,
        )
        try:
            prompt = build_character_prompt(
                gender,
                active_selections,
                include_adult=include_adult,
                identity_mode=identity_mode,
                custom_species_en=custom_species_en,
            )
        except ValueError:
            continue
        required_prompts_are_visible = all(
            (not selected_prompts and fill_blanks_only)
            or any(fragment in prompt for fragment in selected_prompts)
            for group in required_groups
            for selected_prompts in (
                tuple(
                    option.prompt_en
                    for category in categories
                    if category.group == group
                    for option in category.options
                    if option.key
                    in _selection_value_keys(randomized.get(category.key))
                ),
            )
        )
        if not required_prompts_are_visible:
            continue
        return dict(randomized)
    return None


def _fill_character_blanks(
    gender: CharacterGender,
    identity_mode: CharacterIdentityMode,
    categories: tuple[TagCategory, ...],
    *,
    include_adult: bool,
) -> None:
    random_categories = _randomizable_character_categories(categories)
    preserved_selections = _read_selections(
        "character",
        tuple(
            category
            for category in CHARACTER_CATEGORIES
            if category.key == _CHARACTER_OUTPUT_PURPOSE_CATEGORY_KEY
        ),
    )
    current = _with_random_species_anchor(
        identity_mode,
        random_categories,
        _read_selections("character", random_categories),
    )
    required_groups = _required_adult_random_groups(random_categories)
    randomized = _validated_random_character_selections(
        gender,
        identity_mode,
        random_categories,
        current,
        include_adult=include_adult,
        fill_blanks_only=True,
        required_groups=required_groups,
        preserved_selections=preserved_selections,
    )
    if randomized is None:
        st.session_state[_ADULT_RANDOM_NOTICE_STATE_KEY] = (
            "這次沒有找到可安全組合的標籤；目前內容已完整保留，請再試一次。"
        )
        return
    _write_selections("character", random_categories, randomized)
    _remember_adult_random_coverage(
        random_categories,
        randomized,
        required_groups,
        fill_blanks_only=True,
    )


def _reroll_character(
    gender: CharacterGender,
    *,
    include_adult: bool,
) -> None:
    """Reroll identity first, then generate a coherent branch-specific character."""

    candidate: tuple[
        CharacterIdentityMode,
        tuple[TagCategory, ...],
        dict[str, SelectionValue],
        tuple[str, ...],
    ] | None = None
    for _attempt in range(8):
        identity_mode = _choose_random_identity_mode()
        complete_categories, _visible_categories = _identity_filtered_character_categories(
            gender,
            include_adult=include_adult,
            identity_mode=identity_mode,
        )
        random_categories = _randomizable_character_categories(complete_categories)
        current = _with_random_species_anchor(identity_mode, random_categories, {})
        required_groups = (
            _required_adult_random_groups(random_categories) if include_adult else ()
        )
        randomized = _validated_random_character_selections(
            gender,
            identity_mode,
            random_categories,
            current,
            include_adult=include_adult,
            # A locked animal species makes every compatible branch detail receive
            # a value. Standard identity has no such anchor and gets a clean reroll.
            fill_blanks_only=_identity_species_key(identity_mode) is not None,
            required_groups=required_groups,
            preserved_selections=_read_selections(
                "character",
                tuple(
                    category
                    for category in CHARACTER_CATEGORIES
                    if category.key == _CHARACTER_OUTPUT_PURPOSE_CATEGORY_KEY
                ),
            ),
            attempts=1,
        )
        if randomized is not None:
            candidate = (identity_mode, random_categories, randomized, required_groups)
            break
    if candidate is None:
        st.session_state[_ADULT_RANDOM_NOTICE_STATE_KEY] = (
            "這次沒有找到可安全組合的標籤；目前內容已完整保留，請再試一次。"
        )
        return

    identity_mode, random_categories, randomized, required_groups = candidate
    _queue_character_editor_manual_for_reroll()
    st.session_state[IDENTITY_MODE_STATE_KEY] = identity_mode
    _remember_page_widget_value(IDENTITY_MODE_STATE_KEY)
    _clear_selections(
        "character",
        _randomizable_character_categories(CHARACTER_CATEGORIES),
    )
    _clear_all_custom_species_state()
    _write_selections("character", random_categories, randomized)
    _remember_adult_random_coverage(
        random_categories,
        randomized,
        required_groups,
        fill_blanks_only=False,
    )


def _render_custom_species_fields(identity_mode: CharacterIdentityMode) -> None:
    species_key = _identity_species_key(identity_mode)
    if species_key is None or "other" not in _selected_category_values("character", species_key):
        return

    _restore_custom_species_widget_state(identity_mode)
    st.markdown("#### 其他／自訂物種")
    st.caption(
        "繁中欄只作畫面上的辨識名稱；英文欄才會加入圖片 Prompt。"
        "英文尚未完成前不會顯示物種細節，也絕不會把中文原樣混入 Prompt。"
    )
    st.text_input(
        "顯示名稱（繁中，可空白）",
        placeholder="例如：雪豹龍裔",
        key=_CUSTOM_SPECIES_ZH_STATE_KEYS[identity_mode],
        help="只留在目前頁面的暫存狀態，不會直接寫進英文 Prompt。",
        on_change=_handle_custom_species_zh_change,
        args=(identity_mode,),
    )
    st.text_input(
        "英文 Prompt 物種",
        placeholder="例如：snow leopard dragonkin",
        key=_CUSTOM_SPECIES_EN_STATE_KEYS[identity_mode],
        help="必填；只能使用英文、數字、空格、連字號與撇號。",
        on_change=_handle_custom_species_english_change,
        args=(identity_mode,),
    )
    st.button(
        "離線翻成英文",
        type="primary",
        key=f"{STATE_PREFIX}_{identity_mode}_translate_species",
        on_click=_translate_custom_species,
        args=(identity_mode,),
        help="不呼叫 API；成功才會寫入英文欄，失敗不會改動現有 Prompt。",
    )
    status = st.session_state.get(_CUSTOM_SPECIES_TRANSLATION_STATUS_KEYS[identity_mode])
    if isinstance(status, tuple) and len(status) == 2:
        level, message = str(status[0]), str(status[1])
        if level == "success":
            st.success(message)
        else:
            st.error(message)
    if not _active_custom_species_values(identity_mode)[1]:
        st.info("可按上方按鈕使用內建詞庫離線翻譯，或直接填寫英文。")


def _render_character_output_purpose(categories: Sequence[TagCategory]) -> None:
    purpose = next(
        (category for category in categories if category.key == "character_output_purpose"),
        None,
    )
    if purpose is None:
        return
    choices = _category_render_metadata(purpose)[0]
    st.markdown("### 想輸出哪一種角色圖？")
    st.caption(
        "可做單張角色立繪、正／側／背三視圖，或把立繪和三視圖放在同一張設定板；"
        "切換用途不會清掉外觀標籤。"
    )
    with st.container(key=f"{STATE_PREFIX}_tag_category_character_{purpose.key}"):
        selected = st.pills(
            "輸出用途",
            tuple(choices),
            selection_mode="single",
            format_func=lambda value: choices[value].label_zh,
            key=_category_widget_key("character", purpose),
            help=purpose.help_text or "三視圖會保持同一角色、服裝與身體比例。",
            width="stretch",
        )
    if selected == "illustration_plus_turnaround":
        st.success("立繪＋三視圖：同一角色會同時呈現完整立繪，以及正面、側面、背面設定圖。")
    elif selected == "orthographic_turnaround":
        st.info("標準三視圖：以正面、側面、背面為主，方便固定角色設計。")


def _build_character(
    gender: CharacterGender,
    selections: Mapping[str, SelectionValue],
    *,
    include_adult: bool,
    identity_mode: CharacterIdentityMode,
) -> tuple[str, str | None, bool]:
    custom = str(st.session_state.get(CHARACTER_CUSTOM_STATE_KEY, "")).strip()
    active_selections, custom_species_en = _character_build_inputs(
        selections,
        identity_mode,
    )
    try:
        return (
            build_character_prompt(
                gender,
                active_selections,
                custom,
                include_adult=include_adult,
                identity_mode=identity_mode,
                custom_species_en=custom_species_en,
            ),
            None,
            False,
        )
    except ValueError as exc:
        try:
            fallback = build_character_prompt(
                gender,
                active_selections,
                include_adult=include_adult,
                identity_mode=identity_mode,
                custom_species_en=custom_species_en,
            )
        except ValueError as fallback_exc:
            safe_base = build_character_prompt(
                gender,
                {},
                include_adult=include_adult,
                identity_mode=identity_mode,
            )
            return safe_base, str(fallback_exc), True
        return fallback, str(exc), False


def _build_background(
    selections: Mapping[str, SelectionValue],
) -> tuple[str, str | None]:
    custom = str(st.session_state.get(BACKGROUND_CUSTOM_STATE_KEY, "")).strip()
    try:
        return build_background_prompt(selections, custom), None
    except ValueError as exc:
        try:
            fallback = build_background_prompt(selections)
        except ValueError as fallback_exc:
            return "", str(fallback_exc)
        return fallback, str(exc)


def _build_negative(
    selections: Mapping[str, SelectionValue],
) -> tuple[str, str | None]:
    custom = str(st.session_state.get(NEGATIVE_CUSTOM_STATE_KEY, "")).strip()
    try:
        return build_negative_prompt(selections, custom), None
    except ValueError as exc:
        try:
            fallback = build_negative_prompt(selections)
        except ValueError as fallback_exc:
            return "", str(fallback_exc)
        return fallback, str(exc)


def _render_character_builder(
    gender: CharacterGender,
) -> tuple[str, str | None, bool, dict[str, SelectionValue]]:
    st.markdown("## 角色圖標籤")
    st.caption("先挑選角色身分與外觀；下方英文 Prompt 會隨每次選擇同步更新。")
    identity_mode = st.radio(
        "角色身分",
        CHARACTER_IDENTITY_MODES,
        format_func=lambda value: CHARACTER_IDENTITY_MODE_LABELS_ZH[value],
        horizontal=True,
        key=IDENTITY_MODE_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(IDENTITY_MODE_STATE_KEY,),
        help=(
            "一般／奇幻只使用種族／血統；獸人／半獸人與福瑞各有獨立物種和細節，"
            "三套身分不會同時混用。"
        ),
    )
    identity_mode = cast("CharacterIdentityMode", identity_mode)
    _remember_page_widget_value(IDENTITY_MODE_STATE_KEY)
    _clear_inactive_identity_state(identity_mode)
    _clear_unused_custom_species_state(identity_mode)
    include_adult = st.toggle(
        "我確認角色為 18 歲以上，顯示 18+ 成人標籤（裸體／色情姿勢）",
        key=ADULT_MODE_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(ADULT_MODE_STATE_KEY,),
        help="預設關閉；只影響角色圖標籤，不影響背景與 Negative Prompt。",
    )
    _remember_page_widget_value(ADULT_MODE_STATE_KEY)
    if include_adult:
        st.warning(
            "18+ 成人模式只適用於年齡明確為 18 歲以上、能自主同意，"
            "且所有成人互動均為合意的角色。未成年、年齡模糊與非合意內容不在可用範圍。"
        )
    else:
        st.session_state.pop(_ADULT_RANDOM_NOTICE_STATE_KEY, None)
        st.caption("一般模式中，裸體與色情姿勢選項會隱藏，也不會進入隨機、輸出或草稿。")

    random_categories, categories = _identity_filtered_character_categories(
        gender,
        include_adult=include_adult,
        identity_mode=identity_mode,
    )
    _sanitize_visible_category_state(
        "character",
        CHARACTER_CATEGORIES,
        categories,
        context=(
            gender.value,
            include_adult,
            identity_mode,
            tuple(category.key for category in categories),
        ),
    )
    purpose_categories = tuple(
        category
        for category in categories
        if category.key == _CHARACTER_OUTPUT_PURPOSE_CATEGORY_KEY
    )
    _ensure_defaults("character", purpose_categories)
    selections = _read_selections("character", categories)
    visible_options = _catalog_option_count(categories)
    selected_options = _selection_count(selections)
    st.markdown(
        '<div class="if-tag-selection-summary">'
        f"<strong>已選 {selected_options} 個標籤</strong>"
        f"<span>{len(categories)} 個分類 · {visible_options} 個可用選項</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.text_input(
        "角色名稱（選填）",
        placeholder="例如：艾莉、緋月；也可以晚點再命名",
        key=CHARACTER_NAME_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(CHARACTER_NAME_STATE_KEY,),
    )
    _remember_page_widget_value(CHARACTER_NAME_STATE_KEY)
    randomizable_categories = _randomizable_character_categories(categories)
    randomizable_complete_categories = _randomizable_character_categories(random_categories)
    _render_random_controls(
        kind="character",
        categories=randomizable_categories,
        random_categories=randomizable_complete_categories,
        clear_categories=_randomizable_character_categories(CHARACTER_CATEGORIES),
        fill_action=lambda: _fill_character_blanks(
            gender,
            identity_mode,
            randomizable_complete_categories,
            include_adult=include_adult,
        ),
        reroll_action=lambda: _reroll_character(
            gender,
            include_adult=include_adult,
        ),
    )
    adult_random_notice = st.session_state.get(_ADULT_RANDOM_NOTICE_STATE_KEY)
    if include_adult and adult_random_notice:
        st.warning(str(adult_random_notice))
    _render_character_output_purpose(categories)
    _render_category_groups(
        "character",
        tuple(category for category in categories if category.key != "character_output_purpose"),
        selections=selections,
        after_category=(
            lambda category: (
                _render_custom_species_fields(identity_mode)
                if category.key == _identity_species_key(identity_mode)
                else None
            )
        ),
    )
    st.text_input(
        "再加幾個英文標籤（選填）",
        placeholder="silver necklace, soft smile",
        help="只接受英文與逗號分隔；中文請直接使用上方繁中標籤。",
        key=CHARACTER_CUSTOM_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(CHARACTER_CUSTOM_STATE_KEY,),
    )
    _remember_page_widget_value(CHARACTER_CUSTOM_STATE_KEY)
    prompt, error, safe_fallback = _build_character(
        gender,
        selections,
        include_adult=include_adult,
        identity_mode=identity_mode,
    )
    return prompt, error, safe_fallback, selections


def _render_background_builder() -> tuple[str, str | None, dict[str, SelectionValue]]:
    st.markdown("## 背景圖標籤")
    st.caption("場景可單獨生成，不需要先有角色、故事或作品。")
    selections = _read_selections("background", BACKGROUND_CATEGORIES)
    _render_random_controls(kind="background", categories=BACKGROUND_CATEGORIES)
    _render_category_groups(
        "background",
        BACKGROUND_CATEGORIES,
        selections=selections,
    )
    st.text_input(
        "再加幾個英文背景標籤（選填）",
        placeholder="floating lanterns, distant airships",
        help="只接受英文與逗號分隔。",
        key=BACKGROUND_CUSTOM_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(BACKGROUND_CUSTOM_STATE_KEY,),
    )
    _remember_page_widget_value(BACKGROUND_CUSTOM_STATE_KEY)
    prompt, error = _build_background(selections)
    return prompt, error, selections


def _render_negative_builder(
    mode: PromptMode,
) -> tuple[str, str | None, dict[str, SelectionValue]]:
    allowed = _NEGATIVE_KEYS_BY_MODE[mode]
    categories = tuple(category for category in NEGATIVE_CATEGORIES if category.key in allowed)
    _ensure_defaults("negative", categories)
    selections = _read_selections("negative", categories)
    st.markdown("## 不要出現的內容（選填）")
    st.caption("這一區會另外產生 Negative Prompt，並依角色／背景模式隱藏不相干的排除項目。")
    _render_category_groups("negative", categories, selections=selections)
    st.text_input(
        "額外英文負面標籤（選填）",
        placeholder="logo, signature",
        help="只接受英文與逗號分隔。",
        key=NEGATIVE_CUSTOM_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(NEGATIVE_CUSTOM_STATE_KEY,),
    )
    _remember_page_widget_value(NEGATIVE_CUSTOM_STATE_KEY)
    prompt, error = _build_negative(selections)
    return prompt, error, selections


def _character_output_context(
    gender: CharacterGender,
    *,
    include_adult: bool,
) -> str:
    raw_mode = st.session_state.get(IDENTITY_MODE_STATE_KEY, "standard")
    identity_mode = (
        cast("CharacterIdentityMode", raw_mode)
        if raw_mode in CHARACTER_IDENTITY_MODES
        else "standard"
    )
    custom_species_zh, custom_species_en = _active_custom_species_values(identity_mode)
    return ":".join(
        (
            gender.value,
            "adult" if include_adult else "general",
            identity_mode,
            custom_species_zh,
            custom_species_en,
        )
    )


def _download_text(
    *,
    mode: PromptMode,
    character_prompt: str,
    background_prompt: str,
    negative_prompt: str,
) -> str:
    title = str(st.session_state.get(TITLE_STATE_KEY, "")).strip()
    name = str(st.session_state.get(CHARACTER_NAME_STATE_KEY, "")).strip()
    sections: list[str] = []
    if title:
        sections.append(f"草稿標題\n{title}")
    if name and mode in {"character", "both"}:
        sections.append(f"角色名稱\n{name}")
    if mode in {"character", "both"} and character_prompt.strip():
        sections.append(f"Character prompt\n{character_prompt}")
    if mode in {"background", "both"} and background_prompt.strip():
        sections.append(f"Background prompt\n{background_prompt}")
    if negative_prompt.strip():
        sections.append(f"Negative prompt\n{negative_prompt}")
    return "\n\n".join(sections).rstrip() + "\n"


def _positive_prompt_ready(
    *,
    mode: PromptMode,
    character_prompt: str,
    background_prompt: str,
) -> bool:
    """Require every positive Prompt represented by the selected output kind."""

    has_character = bool(character_prompt.strip())
    has_background = bool(background_prompt.strip())
    if mode == "character":
        return has_character
    if mode == "background":
        return has_background
    return has_character and has_background


def _build_combined_scene(
    gender: CharacterGender,
    character_selections: Mapping[str, SelectionValue],
    background_selections: Mapping[str, SelectionValue],
) -> tuple[str, str | None]:
    """Build one coordinated scene from the currently authored tag maps."""

    raw_identity_mode = st.session_state.get(IDENTITY_MODE_STATE_KEY, "standard")
    identity_mode = (
        cast("CharacterIdentityMode", raw_identity_mode)
        if raw_identity_mode in CHARACTER_IDENTITY_MODES
        else "standard"
    )
    active_character_selections = dict(character_selections)
    custom_species_zh, custom_species_en = _active_custom_species_values(identity_mode)
    species_key = _identity_species_key(identity_mode)
    if (
        species_key is not None
        and "other" in _selected_category_values("character", species_key)
        and not custom_species_en
    ):
        active_character_selections[species_key] = ()
    try:
        return (
            build_combined_scene_prompt(
                gender,
                active_character_selections,
                background_selections,
                str(st.session_state.get(CHARACTER_CUSTOM_STATE_KEY, "")).strip(),
                str(st.session_state.get(BACKGROUND_CUSTOM_STATE_KEY, "")).strip(),
                include_adult=st.session_state.get(ADULT_MODE_STATE_KEY) is True,
                identity_mode=identity_mode,
                custom_species_en=custom_species_en,
                custom_species_zh=(custom_species_zh if not custom_species_en else ""),
            ),
            None,
        )
    except ValueError as exc:
        return "", str(exc)


def _handoff_to_prompt_scratch(
    *,
    mode: PromptMode,
    gender: CharacterGender | None,
    character_prompt: str,
    background_prompt: str,
    negative_prompt: str,
) -> None:
    from imaginarium_forge.application.services.prompt_scratch_generation_service import (
        PromptScratchKind,
    )
    from imaginarium_forge.ui.pages import prompt_scratch

    if not _positive_prompt_ready(
        mode=mode,
        character_prompt=character_prompt,
        background_prompt=background_prompt,
    ):
        raise ValueError("The selected Prompt kind requires non-empty positive Prompt text.")
    if mode in {"character", "both"} and gender is None:
        raise ValueError("Character Prompt handoff requires an explicit character gender.")

    kind = {
        "character": PromptScratchKind.CHARACTER,
        "background": PromptScratchKind.BACKGROUND,
        "both": PromptScratchKind.BOTH,
    }[mode]
    notes = "由懶人標籤生成器建立。"
    clean_negative_prompt = negative_prompt.strip()
    if clean_negative_prompt:
        notes += f"\n\nNegative prompt:\n{clean_negative_prompt}"
    st.session_state[prompt_scratch.PENDING_SELECTION_STATE_KEY] = "__new__"
    st.session_state[prompt_scratch.BOUND_STATE_KEY] = "__new__"
    pending_form: dict[str, object] = {
        "kind": kind,
        "title": str(st.session_state.get(TITLE_STATE_KEY, "")).strip(),
        "character_name": str(st.session_state.get(CHARACTER_NAME_STATE_KEY, "")).strip(),
        "character_image_prompt_en": (character_prompt if mode in {"character", "both"} else ""),
        "background_image_prompt_en": (background_prompt if mode in {"background", "both"} else ""),
        "notes": notes,
        "link_project": False,
        "project_id": None,
    }
    if gender is not None:
        pending_form["gender"] = gender
    st.session_state[prompt_scratch.PENDING_FORM_STATE_KEY] = pending_form
    st.session_state["pending_nav"] = prompt_scratch.PAGE_KEY
    st.rerun()


def render() -> None:
    """Render a project-free prompt composer with optional offline translation."""

    _restore_page_widget_state()
    _render_local_styles()
    for output_key in (
        CHARACTER_OUTPUT_STATE_KEY,
        BACKGROUND_OUTPUT_STATE_KEY,
        NEGATIVE_OUTPUT_STATE_KEY,
        COMBINED_OUTPUT_STATE_KEY,
    ):
        if output_key not in st.session_state:
            st.session_state[output_key] = ""

    page_header(
        PAGE_LABEL,
        "點選繁中標籤，立即組成全英文、逗號分隔的角色圖與背景圖 Prompt。",
        eyebrow="不想發想、不想翻英文，也能從選項直接開始",
        badges=(("免建專案", "teal"), ("標籤免 API Key", ""), ("成年角色 18+", "amber")),
    )
    st.info(
        "這裡的標籤與自訂物種翻譯都在本機處理，不需要 API Key，也不會送出物種名稱。"
        "可只做角色、只做背景，或兩組一起做。"
    )

    mode = st.radio(
        "這次想拼哪一種 Prompt？",
        tuple(_MODE_LABELS),
        format_func=lambda value: _MODE_LABELS[value],
        horizontal=True,
        key=MODE_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(MODE_STATE_KEY,),
    )
    mode = cast("PromptMode", mode)
    _remember_page_widget_value(MODE_STATE_KEY)
    _preserve_mode_transition_state(mode)
    st.text_input(
        "草稿標題（選填）",
        placeholder="例如：粉紅雙馬尾精靈／雨夜魔法車站",
        key=TITLE_STATE_KEY,
        on_change=_remember_page_widget_value,
        args=(TITLE_STATE_KEY,),
    )
    _remember_page_widget_value(TITLE_STATE_KEY)

    gender: CharacterGender | None = None
    build_feedback: list[tuple[str, bool, bool, bool]] = []
    character_prompt = _stored_output(CHARACTER_OUTPUT_STATE_KEY)
    character_error: str | None = None
    character_selections: dict[str, SelectionValue] = {}
    if mode in {"character", "both"}:
        if GENDER_STATE_KEY not in st.session_state:
            remembered_gender = st.session_state.get(_GENDER_SELECTION_STATE_KEY)
            if remembered_gender in tuple(CharacterGender):
                st.session_state[GENDER_STATE_KEY] = remembered_gender
        selected_gender = st.radio(
            "角色性別",
            tuple(CharacterGender),
            format_func=lambda value: value.zh_label,
            horizontal=True,
            index=None,
            key=GENDER_STATE_KEY,
            help="只提供男、女；兩者都固定為成年角色。",
            on_change=_remember_gender_selection,
        )
        if selected_gender is not None:
            _remember_gender_selection()
        if selected_gender is None:
            st.info("請先選擇角色性別；選好男或女後，才會顯示角色標籤與隨機功能。")
            st.session_state[CHARACTER_OUTPUT_STATE_KEY] = ""
            st.session_state.pop(CHARACTER_OUTPUT_CONTEXT_STATE_KEY, None)
            character_prompt = ""
        else:
            gender = cast("CharacterGender", selected_gender)
            (
                character_candidate,
                character_error,
                character_safe_fallback,
                character_selections,
            ) = _render_character_builder(gender)
            include_adult = st.session_state.get(ADULT_MODE_STATE_KEY) is True
            character_context = _character_output_context(
                gender,
                include_adult=include_adult,
            )
            character_prompt, character_preserved = _keep_last_valid_output(
                CHARACTER_OUTPUT_STATE_KEY,
                character_candidate,
                character_error,
                candidate_is_safe_fallback=character_safe_fallback,
                context_key=CHARACTER_OUTPUT_CONTEXT_STATE_KEY,
                context=character_context,
            )
            if character_error:
                build_feedback.append(
                    (
                        character_error,
                        character_preserved,
                        bool(character_candidate),
                        character_safe_fallback,
                    )
                )

    background_prompt = _stored_output(BACKGROUND_OUTPUT_STATE_KEY)
    background_error: str | None = None
    background_selections: dict[str, SelectionValue] = {}
    if mode in {"background", "both"}:
        if mode == "both":
            st.divider()
        (
            background_candidate,
            background_error,
            background_selections,
        ) = _render_background_builder()
        background_prompt, background_preserved = _keep_last_valid_output(
            BACKGROUND_OUTPUT_STATE_KEY,
            background_candidate,
            background_error,
        )
        if background_error:
            build_feedback.append(
                (background_error, background_preserved, bool(background_candidate), False)
            )

    st.divider()
    negative_candidate, negative_error, _negative_selections = _render_negative_builder(mode)
    negative_prompt, negative_preserved = _keep_last_valid_output(
        NEGATIVE_OUTPUT_STATE_KEY,
        negative_candidate,
        negative_error,
    )
    if negative_error:
        build_feedback.append((negative_error, negative_preserved, bool(negative_candidate), False))

    combined_prompt = ""
    if (
        mode == "both"
        and gender is not None
        and character_prompt
        and background_prompt
        and character_error is None
        and background_error is None
    ):
        combined_prompt, combined_error = _build_combined_scene(
            gender,
            character_selections,
            background_selections,
        )
        if combined_error:
            build_feedback.append((f"角色＋背景合併失敗：{combined_error}", False, False, False))
    _write_session_value_if_changed(COMBINED_OUTPUT_STATE_KEY, combined_prompt)

    for error, preserved, fallback_available, safe_fallback in dict.fromkeys(build_feedback):
        if preserved:
            st.warning(
                f"目前選擇尚未能組成新的 Prompt：{error}。"
                "已保留上一個有效 Prompt，請調整相關標籤後再試。"
            )
        elif safe_fallback:
            st.warning(
                f"目前選擇尚未能組成完整 Prompt：{error}。"
                "已改用符合目前角色性別與成人模式的安全最小 Prompt。"
            )
        elif fallback_available:
            st.warning(f"額外標籤未加入：{error}。上方繁中選項產生的 Prompt 仍然可用。")
        else:
            st.warning(f"目前選擇無法組成 Prompt：{error}。請調整相關標籤後再試。")

    st.divider()
    st.markdown("## 英文 Prompt 與複製")
    if mode == "both":
        render_copyable_prompt(
            "角色＋背景合併 Prompt",
            combined_prompt,
            key="prompt-tag-builder-combined",
            help_text="協調角色、環境、鏡頭與風格後，組成同一個場景 Prompt。",
        )
    if mode in {"character", "both"}:
        _sync_prompt_editor(
            CHARACTER_OUTPUT_STATE_KEY,
            character_prompt,
            context=(
                str(st.session_state.get(CHARACTER_OUTPUT_CONTEXT_STATE_KEY, ""))
                if gender is not None
                else "unselected"
            ),
        )
        character_prompt = render_copyable_prompt(
            "角色圖 Prompt",
            character_prompt,
            key="prompt-tag-builder-character",
            help_text=(
                "可直接輸入或修改；同一角色設定下更動標籤時，手動新增內容會移到最新組合的最後。"
                "右側按鈕可直接複製。"
            ),
            editable_key=CHARACTER_EDITOR_STATE_KEY,
            editor_height=152,
        )
        _remember_page_widget_value(CHARACTER_EDITOR_STATE_KEY)
    if mode in {"background", "both"}:
        _sync_prompt_editor(BACKGROUND_OUTPUT_STATE_KEY, background_prompt)
        background_prompt = render_copyable_prompt(
            "背景圖 Prompt",
            background_prompt,
            key="prompt-tag-builder-background",
            help_text=(
                "可直接輸入或修改；更動背景標籤時，手動新增內容會移到最新組合的最後。"
            ),
            editable_key=BACKGROUND_EDITOR_STATE_KEY,
            editor_height=152,
        )
        _remember_page_widget_value(BACKGROUND_EDITOR_STATE_KEY)
    _sync_prompt_editor(NEGATIVE_OUTPUT_STATE_KEY, negative_prompt, context=mode)
    negative_prompt = render_copyable_prompt(
        "Negative Prompt",
        negative_prompt,
        key="prompt-tag-builder-negative",
        help_text=(
            "可直接輸入或修改；更動負面標籤時，手動新增內容會移到最新組合的最後，"
            "且不會混入正向 Prompt。"
        ),
        editable_key=NEGATIVE_EDITOR_STATE_KEY,
        editor_height=112,
    )
    _remember_page_widget_value(NEGATIVE_EDITOR_STATE_KEY)

    payload = _download_text(
        mode=mode,
        character_prompt=character_prompt,
        background_prompt=background_prompt,
        negative_prompt=negative_prompt,
    )
    positive_prompt_ready = _positive_prompt_ready(
        mode=mode,
        character_prompt=character_prompt,
        background_prompt=background_prompt,
    )
    handoff_ready = positive_prompt_ready and (
        mode == "background" or gender is not None
    )
    actions = st.columns(2)
    actions[0].download_button(
        "下載全部 Prompt（TXT）",
        data=payload,
        file_name="imaginarium-prompt-tags.txt",
        mime="text/plain; charset=utf-8",
        use_container_width=True,
        key=f"{STATE_PREFIX}_download",
        disabled=not positive_prompt_ready,
    )
    if actions[1].button(
        "送到圖片提示詞草稿繼續修改",
        type="primary",
        use_container_width=True,
        key=f"{STATE_PREFIX}_to_scratch",
        disabled=not handoff_ready,
    ):
        _handoff_to_prompt_scratch(
            mode=mode,
            gender=gender,
            character_prompt=character_prompt,
            background_prompt=background_prompt,
            negative_prompt=negative_prompt,
        )
    st.caption("送出只會開一張尚未保存的自由草稿；不會自動建立作品，也不會覆蓋既有內容。")


__all__ = [
    "ADULT_MODE_STATE_KEY",
    "BACKGROUND_EDITOR_STATE_KEY",
    "BACKGROUND_OUTPUT_STATE_KEY",
    "CHARACTER_EDITOR_STATE_KEY",
    "CHARACTER_OUTPUT_STATE_KEY",
    "COMBINED_OUTPUT_STATE_KEY",
    "GENDER_STATE_KEY",
    "IDENTITY_MODE_STATE_KEY",
    "MODE_STATE_KEY",
    "NEGATIVE_EDITOR_STATE_KEY",
    "NEGATIVE_OUTPUT_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "SPECIES_TRANSLATION_HOOK_STATE_KEY",
    "STATE_PREFIX",
    "render",
]
