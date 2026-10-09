"""A project-optional notebook for loose character ideas."""

from __future__ import annotations

import inspect
import json
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, cast

import streamlit as st

from imaginarium_forge.application.errors import ApplicationError, ConflictError
from imaginarium_forge.application.services.creative_inspiration_service import (
    render_english_keywords,
)
from imaginarium_forge.application.services.generation.contracts import (
    GenerationErrorCategory,
    GenerationFailure,
    GenerationTask,
)
from imaginarium_forge.application.services.generation.facade import (
    CreativeCandidateAction,
)
from imaginarium_forge.application.services.generation.output_measurement import (
    measure_output,
)
from imaginarium_forge.application.services.generation.refinement import (
    InvalidRefinementCandidateError,
    StaleRefinementBaseError,
)
from imaginarium_forge.application.services.generation.refinement_candidate_persistence import (
    RefinementCandidateBinding,
)
from imaginarium_forge.application.user_facing_errors import ErrorDataSafety
from imaginarium_forge.domain.character.biography_draft import CharacterBiographyDraft
from imaginarium_forge.domain.character.gender import CharacterGender
from imaginarium_forge.domain.generation.durable import ArtifactKind, sha256_json
from imaginarium_forge.infrastructure.db.adapters.draft_candidate_target import (
    CHARACTER_BIOGRAPHY_DRAFT_TARGET,
)
from imaginarium_forge.ui.bootstrap import get_services
from imaginarium_forge.ui.character_draft_promotion import (
    prepare_character_draft_launchpad_handoff,
    validate_character_draft_promotion,
)
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.creative_generation import (
    authoring_editor_state_sha256,
    candidate_apply_validation,
    candidate_authoring_response,
    candidate_value_replaces_current,
    clear_candidate_state,
    render_candidate_action_state,
    render_candidate_card,
    render_candidate_error,
    render_passive_creative_runtime_status,
    request_candidate,
)
from imaginarium_forge.ui.error_presenter import present_exception
from imaginarium_forge.ui.openai_session import (
    OPENAI_API_KEY_SESSION_KEY,
)
from imaginarium_forge.ui.refinement import (
    RefinementUIDecision,
    candidate_receipt_for_save_as_new,
    clear_refinement_state,
    commit_durable_refinement_decision,
    protected_facts_and_spans,
    record_refinement_apply,
    refinement_receipt,
    render_durable_refinement_history,
    render_refinement_candidate,
    render_refinement_controls,
    render_refinement_failure,
    render_refinement_undo,
    set_refinement_failure,
)
from imaginarium_forge.ui.writing_request_controls import (
    WritingContextSourceInput,
    WritingRequestDefaults,
    WritingRequestSurface,
    build_writing_request_defaults,
    render_writing_request_controls,
)

PAGE_KEY = "Character Drafts"
PAGE_LABEL = "角色與故事草稿"

STATE_PREFIX = "character_draft_box"
SELECTED_DRAFT_STATE_KEY = f"{STATE_PREFIX}_selected"
PENDING_SELECTION_STATE_KEY = f"{STATE_PREFIX}_pending_selection"
BOUND_DRAFT_STATE_KEY = f"{STATE_PREFIX}_bound"
BASE_UPDATED_AT_STATE_KEY = f"{STATE_PREFIX}_base_updated_at"
EDITOR_VALUES_STATE_KEY = f"{STATE_PREFIX}_editor_values"
PENDING_FORM_STATE_KEY = f"{STATE_PREFIX}_pending_form"
FLASH_STATE_KEY = f"{STATE_PREFIX}_flash"
ARCHIVE_CONFIRM_STATE_KEY = f"{STATE_PREFIX}_archive_confirm"
PROVIDER_GENERATION_HOOK_STATE_KEY = f"{STATE_PREFIX}_provider_generation_hook"
GENERATION_SOURCE_STATE_KEY: Final = f"{STATE_PREFIX}_generation_source"
GENERATION_CLUE_STATE_KEY: Final = f"{STATE_PREFIX}_generation_clue"
GENERATION_REVISION_STATE_KEY: Final = f"{STATE_PREFIX}_generation_revision"
GENERATION_ACTION_STATE_KEY: Final = f"{STATE_PREFIX}_generation_action"
GENERATION_ERROR_STATE_KEY: Final = f"{STATE_PREFIX}_generation_error"
GENERATION_LENGTH_RESULT_STATE_KEY: Final = f"{STATE_PREFIX}_generation_length_result"
GENERATION_PROVIDER_STATE_KEY: Final = f"{STATE_PREFIX}_injected_provider"
# Compatibility export for integrations that inject a masked session secret.
OPENAI_API_KEY_STATE_KEY: Final = OPENAI_API_KEY_SESSION_KEY

_NEW_DRAFT = "__new__"
_FORM_KEYS = {
    "title": f"{STATE_PREFIX}_title",
    "character_name": f"{STATE_PREFIX}_character_name",
    "gender": f"{STATE_PREFIX}_gender",
    "character_details": f"{STATE_PREFIX}_character_details",
    "character_image_prompt_en": f"{STATE_PREFIX}_character_image_prompt_en",
    "personal_story": f"{STATE_PREFIX}_personal_story",
    "background_image_prompt_en": f"{STATE_PREFIX}_background_image_prompt_en",
    "notes": f"{STATE_PREFIX}_notes",
    "link_project": f"{STATE_PREFIX}_link_project",
    "project_id": f"{STATE_PREFIX}_project_id",
}
_EDITOR_STATE_KEYS = (
    *_FORM_KEYS.values(), SELECTED_DRAFT_STATE_KEY, BOUND_DRAFT_STATE_KEY,
    BASE_UPDATED_AT_STATE_KEY,
)
_WRITE_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("draft_title",),
    "character_name": ("name",),
    "gender": ("character_gender",),
    "character_details": ("character_description",),
    "character_image_prompt_en": ("character_prompt",),
    "personal_story": ("background_story", "story"),
    "background_image_prompt_en": ("background_prompt",),
    "notes": ("memo",),
    "project_id": ("linked_project_id",),
}
_READ_ALIASES: dict[str, tuple[str, ...]] = {
    key: (key, *aliases) for key, aliases in _WRITE_ALIASES.items()
}
_READ_ALIASES["id"] = ("id", "draft_id")

_REFINEMENT_FIELDS: dict[str, tuple[str, ArtifactKind]] = {
    "character_details": ("角色完整設定", ArtifactKind.CHARACTER_CARD),
    "personal_story": ("個人故事", ArtifactKind.CHARACTER_BACKSTORY),
    "character_image_prompt_en": ("角色圖片提示詞", ArtifactKind.IMAGE_PROMPT),
    "background_image_prompt_en": ("背景圖片提示詞", ArtifactKind.IMAGE_PROMPT),
}


@dataclass(frozen=True, slots=True)
class _DraftView:
    id: str
    title: str
    character_name: str
    gender: CharacterGender
    character_details: str
    character_image_prompt_en: str
    personal_story: str
    background_image_prompt_en: str
    notes: str
    project_id: str | None
    updated_at: str | None


class _CharacterDraftAdapter:
    """Keep the page tolerant of the original provisional service vocabulary."""

    def __init__(self, service: Any) -> None:
        self._service = service

    @property
    def available(self) -> bool:
        return all(
            self._find_method(method) is not None
            for method in ("create", "update", "get", "list", "archive")
        )

    def list_active(self) -> list[Any]:
        method = self._required_method("list")
        kwargs: dict[str, Any] = {}
        if _accepts_parameter(method, "include_archived"):
            kwargs["include_archived"] = False
        records = method(**kwargs)
        return [record for record in records if not _is_archived(record)]

    def get(self, draft_id: str) -> Any:
        return self._required_method("get")(draft_id)

    def create(self, payload: Mapping[str, Any]) -> Any:
        method = self._required_method("create")
        return method(**_adapt_payload(method, payload))

    def update(
        self, draft_id: str, payload: Mapping[str, Any], *, expected_updated_at: str | None = None,
    ) -> Any:
        method = self._required_method("update")
        versioned = dict(payload)
        if expected_updated_at is not None:
            versioned["expected_updated_at"] = expected_updated_at
        return method(draft_id, **_adapt_payload(method, versioned))

    def archive(self, draft_id: str, *, expected_updated_at: str | None = None) -> Any:
        method = self._required_method("archive")
        versioned = (
            {"expected_updated_at": expected_updated_at} if expected_updated_at is not None else {}
        )
        return method(draft_id, **_adapt_payload(method, versioned))

    def _required_method(self, name: str) -> Callable[..., Any]:
        method = self._find_method(name)
        if method is None:
            raise RuntimeError(f"角色草稿服務缺少 {name}()")
        return method

    def _find_method(self, name: str) -> Callable[..., Any] | None:
        official = {
            "create": "create_draft",
            "update": "update_draft",
            "get": "get_draft",
            "list": "list_drafts",
            "archive": "archive_draft",
        }[name]
        for candidate in (official, name):
            method = getattr(self._service, candidate, None)
            if callable(method):
                return cast(Callable[..., Any], method)
        return None


def _accepts_parameter(method: Callable[..., Any], name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _adapt_payload(method: Callable[..., Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Map canonical fields once, before a mutating call can have side effects."""

    if payload.get("expected_updated_at") is not None and not _accepts_parameter(
        method, "expected_updated_at"
    ):
        raise RuntimeError("角色草稿服務不支援版本檢查，已阻止未受保護的寫入")
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return dict(payload)
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return dict(payload)

    adapted: dict[str, Any] = {}
    for canonical, value in payload.items():
        candidates = (canonical, *_WRITE_ALIASES.get(canonical, ()))
        accepted = next((name for name in candidates if name in parameters), None)
        if accepted is not None:
            adapted[accepted] = value
    return adapted


def _record_value(record: Any, names: tuple[str, ...], default: Any = "") -> Any:
    if isinstance(record, Mapping):
        for name in names:
            if name in record:
                return record[name]
        return default
    for name in names:
        if hasattr(record, name):
            return getattr(record, name)
    return default


def _gender(value: Any) -> CharacterGender:
    if isinstance(value, CharacterGender):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"male", "男", "man", "adult man"}:
        return CharacterGender.MALE
    return CharacterGender.FEMALE


def _draft_view(record: Any) -> _DraftView:
    project_id = _record_value(record, _READ_ALIASES["project_id"], None)
    updated_at = _record_value(record, ("updated_at",), None)
    return _DraftView(
        id=str(_record_value(record, _READ_ALIASES["id"])),
        title=str(_record_value(record, _READ_ALIASES["title"])),
        character_name=str(_record_value(record, _READ_ALIASES["character_name"])),
        gender=_gender(_record_value(record, _READ_ALIASES["gender"])),
        character_details=str(_record_value(record, _READ_ALIASES["character_details"])),
        character_image_prompt_en=str(
            _record_value(record, _READ_ALIASES["character_image_prompt_en"])
        ),
        personal_story=str(_record_value(record, _READ_ALIASES["personal_story"])),
        background_image_prompt_en=str(
            _record_value(record, _READ_ALIASES["background_image_prompt_en"])
        ),
        notes=str(_record_value(record, _READ_ALIASES["notes"])),
        project_id=str(project_id) if project_id else None,
        updated_at=str(updated_at) if updated_at else None,
    )


def _expected_editor_version(record: Any) -> str | None:
    """Use only the loaded editor base, never this rerun's freshly read version."""

    draft = _draft_view(record)
    loaded = st.session_state.get(BASE_UPDATED_AT_STATE_KEY)
    if st.session_state.get(BOUND_DRAFT_STATE_KEY) != draft.id or (
        draft.updated_at is not None and (not isinstance(loaded, str) or not loaded)
    ):
        raise ConflictError("無法確認這張紙頁的載入版本；原文已保留，未寫入或封存草稿")
    return loaded if isinstance(loaded, str) and loaded else None


def _is_archived(record: Any) -> bool:
    status = _record_value(record, ("status",), "")
    status_value = getattr(status, "value", status)
    if str(status_value).casefold() == "archived":
        return True
    return bool(_record_value(record, ("archived_at",), None))


def _blank_form() -> dict[str, Any]:
    return {
        "title": "",
        "character_name": "",
        "gender": CharacterGender.FEMALE,
        "character_details": "",
        "character_image_prompt_en": "",
        "personal_story": "",
        "background_image_prompt_en": "",
        "notes": "",
        "link_project": False,
        "project_id": None,
    }


def _form_from_draft(draft: _DraftView) -> dict[str, Any]:
    return {
        "title": draft.title,
        "character_name": draft.character_name,
        "gender": draft.gender,
        "character_details": draft.character_details,
        "character_image_prompt_en": draft.character_image_prompt_en,
        "personal_story": draft.personal_story,
        "background_image_prompt_en": draft.background_image_prompt_en,
        "notes": draft.notes,
        "link_project": draft.project_id is not None,
        "project_id": draft.project_id,
    }


def _durable_refinement_binding(
    adapter: _CharacterDraftAdapter,
    *,
    selected: object,
    field_name: str,
) -> RefinementCandidateBinding | None:
    """Bind only a production draft whose complete mounted form is unchanged."""

    if not isinstance(selected, str) or not selected or selected == _NEW_DRAFT:
        return None
    try:
        record = adapter.get(selected)
    except (ApplicationError, RuntimeError, TypeError, ValueError):
        return None
    if not isinstance(record, CharacterBiographyDraft):
        return None
    if st.session_state.get(BOUND_DRAFT_STATE_KEY) != record.id:
        return None
    expected_form = _form_from_draft(_draft_view(record))
    if any(
        st.session_state.get(_FORM_KEYS[field]) != value
        for field, value in expected_form.items()
    ):
        return None
    return RefinementCandidateBinding(
        target_type=CHARACTER_BIOGRAPHY_DRAFT_TARGET,
        target_id=record.id,
        field_name=field_name,
        expected_base_snapshot_sha256=sha256_json(record.model_dump(mode="json")),
    )


def _capture_editor_state() -> None:
    """Retain author fields before navigation unmounts their widgets.

    Preserve the optional hidden project selection, but never collect a key by
    prefix: credentials, confirmations, candidates and pending actions stay out.
    """

    previous = st.session_state.get(EDITOR_VALUES_STATE_KEY, {})
    saved = {
        key: previous[key] for key in _EDITOR_STATE_KEYS
        if isinstance(previous, dict) and key in previous
    }
    saved.update({
        key: st.session_state[key] for key in _EDITOR_STATE_KEYS if key in st.session_state
    })
    st.session_state[EDITOR_VALUES_STATE_KEY] = saved


def _restore_editor_state() -> None:
    """Restore widget-cleaned keys without replacing this rerun's input events."""

    saved = st.session_state.get(EDITOR_VALUES_STATE_KEY)
    if isinstance(saved, dict):
        for key in _EDITOR_STATE_KEYS:
            if key not in st.session_state and key in saved:
                st.session_state[key] = saved[key]


def _write_form(values: Mapping[str, Any]) -> None:
    for field, key in _FORM_KEYS.items():
        st.session_state[key] = values.get(field, _blank_form()[field])
    _capture_editor_state()


def _apply_pending_form() -> None:
    pending = st.session_state.pop(PENDING_FORM_STATE_KEY, None)
    if isinstance(pending, Mapping):
        for field, value in pending.items():
            key = _FORM_KEYS.get(str(field))
            if key is not None:
                st.session_state[key] = value
        _capture_editor_state()


def _offline_inspiration(gender: CharacterGender, *, seed: int | None = None) -> dict[str, str]:
    """Build one coherent local character card without provider or network I/O."""

    picker: random.Random | random.SystemRandom
    picker = random.Random(seed) if seed is not None else random.SystemRandom()
    profiles = {
        CharacterGender.FEMALE: (
            (
                "璃央",
                "替陌生人保管未寄出信件的夜班郵差",
                "安靜敏銳，會把每個承諾記進袖口；害怕被需要之後又失去對方",
                ("adult woman", "long rose pink twin tails", "amber eyes", "gentle face"),
            ),
            (
                "祈夏",
                "能聽見舊建築夢話的修復師",
                "外柔內韌，總先照顧別人的裂痕；卻不肯承認自己也需要被接住",
                ("adult woman", "short silver bob hair", "blue eyes", "freckled face"),
            ),
            (
                "青棠",
                "在雨季替失蹤者畫回家地圖的製圖師",
                "觀察細膩，遇到危險反而冷靜；最大的盲點是把孤獨誤認成自由",
                ("adult woman", "dark braided hair", "gray eyes", "delicate features"),
            ),
        ),
        CharacterGender.MALE: (
            (
                "洛岑",
                "替陌生人保管未寄出信件的夜班郵差",
                "安靜敏銳，會把每個承諾記進袖口；害怕被需要之後又失去對方",
                ("adult man", "wavy black hair", "amber eyes", "gentle face"),
            ),
            (
                "聞澈",
                "能聽見舊建築夢話的修復師",
                "外冷內柔，總先修補別人的裂痕；卻不肯承認自己也需要被接住",
                ("adult man", "short silver hair", "blue eyes", "freckled face"),
            ),
            (
                "季遙",
                "在雨季替失蹤者畫回家地圖的製圖師",
                "觀察細膩，遇到危險反而冷靜；最大的盲點是把孤獨誤認成自由",
                ("adult man", "dark shoulder length hair", "gray eyes", "refined features"),
            ),
        ),
    }
    worlds = (
        (
            "只在退潮時出現的霧城",
            "多年以前，{name}沒能送達一封求救信。如今相同筆跡再次出現，收件人卻是明天的自己。"
            "為了找到寫信的人，{name}必須走進每次漲潮都會改寫記憶的舊城，也學會把真相交給一位值得信任的人。",
            ("misty tidal city", "blue hour", "wet stone streets", "distant lighthouse"),
            ("weathered travel coat", "canvas mailbag", "thoughtful expression"),
        ),
        (
            "永遠行駛在雲海上的末班列車",
            "{name}一直替乘客收藏不敢帶下車的回憶。某夜，一只盒子裡傳出自己童年的聲音，"
            "並預告列車會在天亮前失去最後一座車站。{name}必須決定留下守護所有人，或第一次追尋自己的歸處。",
            ("train above clouds", "warm carriage lights", "dawn sky", "vast cloud sea"),
            ("layered conductor coat", "memory box", "quiet determined expression"),
        ),
        (
            "由巨樹根系連起的地下聚落",
            "{name}能讀出樹皮上被抹去的承諾，卻發現其中一條出自失蹤多年的家人。"
            "追查使整座聚落的和平開始鬆動；若要讓真相重見天日，{name}必須先承認自己最想守住的其實是一段關係。",
            ("underground root city", "bioluminescent moss", "ancient trees", "soft green light"),
            ("practical layered outfit", "leather notebook", "alert gentle expression"),
        ),
    )
    name, role, personality, appearance = picker.choice(profiles[gender])
    world, story, background_prompt, wardrobe = picker.choice(worlds)
    # The role is Chinese and therefore belongs in prose only, never in the
    # explicitly English image prompt.
    character_prompt = (*appearance, *wardrobe)
    return {
        "title": f"{world}的{name}",
        "character_name": name,
        "character_details": (
            f"{name}是一名{role}。{personality}。外表與動作看似從容，碰到與失蹤者有關的線索時，"
            "會下意識反覆確認出口。真正想要的是一個能安心留下的地方；可延伸的人際線是一位知道舊案真相、"
            "卻始終沒有說破的搭檔。"
        ),
        "character_image_prompt_en": render_english_keywords(character_prompt),
        "personal_story": story.format(name=name),
        "background_image_prompt_en": render_english_keywords(background_prompt),
    }


def _form_payload(project_id: str | None) -> dict[str, Any]:
    return {
        "title": str(st.session_state.get(_FORM_KEYS["title"], "")).strip(),
        "character_name": str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip(),
        "gender": _gender(st.session_state.get(_FORM_KEYS["gender"])),
        "character_details": str(st.session_state.get(_FORM_KEYS["character_details"], "")).strip(),
        "character_image_prompt_en": render_english_keywords(
            str(st.session_state.get(_FORM_KEYS["character_image_prompt_en"], ""))
        ),
        "personal_story": str(st.session_state.get(_FORM_KEYS["personal_story"], "")).strip(),
        "background_image_prompt_en": render_english_keywords(
            str(st.session_state.get(_FORM_KEYS["background_image_prompt_en"], ""))
        ),
        "notes": str(st.session_state.get(_FORM_KEYS["notes"], "")).strip(),
        "project_id": project_id,
    }


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return (cleaned or "角色草稿")[:60]


def _json_export(payload: Mapping[str, Any], draft_id: str | None) -> str:
    gender = _gender(payload.get("gender"))
    body = {
        "schema_version": 1,
        "draft_id": draft_id,
        **payload,
        "gender": gender.value,
    }
    return json.dumps(body, ensure_ascii=False, indent=2)


def _txt_export(payload: Mapping[str, Any]) -> str:
    gender = _gender(payload.get("gender"))
    project_note = str(payload.get("project_id") or "未連結作品")
    return "\n\n".join(
        (
            f"角色草稿｜{payload.get('title', '')}",
            (
                f"角色名：{payload.get('character_name', '')}\n"
                f"性別：{gender.zh_label}\n作品連結：{project_note}"
            ),
            f"角色完整描述\n{payload.get('character_details', '')}",
            f"角色圖片 Prompt（English）\n{payload.get('character_image_prompt_en', '')}",
            f"個人故事\n{payload.get('personal_story', '')}",
            f"背景圖片 Prompt（English）\n{payload.get('background_image_prompt_en', '')}",
            f"備註\n{payload.get('notes', '')}",
        )
    )


def _character_writing_defaults(*, clue: str) -> WritingRequestDefaults:
    """Build the visible N2 request without hidden provider-side truncation."""

    gender = _gender(st.session_state.get(_FORM_KEYS["gender"]))
    name = str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip()
    title = str(st.session_state.get(_FORM_KEYS["title"], "")).strip()
    protected_facts = tuple(
        value
        for value in (
            f"角色姓名：{name}" if name else "",
            f"角色性別：{gender.zh_label}",
            f"草稿標題：{title}" if title else "",
        )
        if value
    )
    sources = (
        WritingContextSourceInput(
            source_id="author_clue",
            source_type="author_instruction",
            label="一句線索",
            text=clue,
            max_visible_chars=1_200,
            priority=0,
        ),
        WritingContextSourceInput(
            source_id="character_details",
            source_type="current_field",
            label="目前角色設定",
            text=str(st.session_state.get(_FORM_KEYS["character_details"], "")),
            max_visible_chars=3_000,
            priority=1,
        ),
        WritingContextSourceInput(
            source_id="personal_story",
            source_type="current_field",
            label="目前個人故事",
            text=str(st.session_state.get(_FORM_KEYS["personal_story"], "")),
            max_visible_chars=5_000,
            priority=2,
        ),
        WritingContextSourceInput(
            source_id="character_image_prompt_en",
            source_type="current_field",
            label="目前角色圖片提示詞",
            text=str(
                st.session_state.get(_FORM_KEYS["character_image_prompt_en"], "")
            ),
            max_visible_chars=1_200,
            priority=3,
        ),
        WritingContextSourceInput(
            source_id="background_image_prompt_en",
            source_type="current_field",
            label="目前背景圖片提示詞",
            text=str(
                st.session_state.get(_FORM_KEYS["background_image_prompt_en"], "")
            ),
            max_visible_chars=1_200,
            priority=4,
        ),
        WritingContextSourceInput(
            source_id="notes",
            source_type="author_notes",
            label="頁邊備註",
            text=str(st.session_state.get(_FORM_KEYS["notes"], "")),
            max_visible_chars=1_200,
            priority=5,
        ),
    )
    return build_writing_request_defaults(
        WritingRequestSurface.CHARACTER,
        sources=sources,
        protected_facts=protected_facts,
    )


def _render_ai_generation(services: Any) -> None:
    """Create a replaceable candidate without touching editor or persistence."""

    length_result = st.session_state.get(GENERATION_LENGTH_RESULT_STATE_KEY)
    if isinstance(length_result, dict):
        length_message = str(length_result.get("message", ""))
        if length_result.get("state") == "within":
            st.success(length_message)
        elif length_result.get("state") in {"below", "above"}:
            st.warning(length_message)
        elif length_message:
            st.caption(length_message)

    with st.expander("✦ AI 生成／精修候選角色"):
        clue = st.text_area(
            "一句線索（選填）",
            placeholder="例如：可愛的女孩子，孤單，雙馬尾，粉紅色頭髮",
            height=90,
            key=GENERATION_CLUE_STATE_KEY,
        )
        revision = st.text_area(
            "這一版想怎麼調整？（選填）",
            placeholder="例如：保留粉紅雙馬尾，把職業改成夜班列車員，故事更溫柔。",
            height=90,
            key=GENERATION_REVISION_STATE_KEY,
        )
        st.markdown("##### 生成內容的長度與參考範圍")
        writing_request = render_writing_request_controls(
            _character_writing_defaults(clue=clue),
            key_prefix=f"{STATE_PREFIX}_authoring",
        )
        gender = _gender(st.session_state.get(_FORM_KEYS["gender"]))
        preferred_name = str(
            st.session_state.get(_FORM_KEYS["character_name"], "")
        ).strip()
        inputs = {
            "title": str(st.session_state.get(_FORM_KEYS["title"], "")),
            "character_name": preferred_name,
            "gender": gender.value,
        }
        editor_identity = str(
            st.session_state.get(SELECTED_DRAFT_STATE_KEY, _NEW_DRAFT)
        )
        apply_mode = st.radio(
            "之後套用時怎麼落到紙上？",
            options=("fill", "rewrite"),
            format_func=lambda value: (
                "只補空白，保留我已寫的欄位" if value == "fill" else "依照指示重寫內容"
            ),
            horizontal=True,
            key=GENERATION_ACTION_STATE_KEY,
        )
        st.caption(
            "沿用頂部「狀態」中保存的全域 provider、模型與 fallback；本頁只顯示被動狀態，"
            "不提供設定、Key 或逐次同意。"
            "只有按下按鈕才會執行，角色名若已填寫會保留，性別永遠以上方選擇為準。"
        )
        generate_status, refine_status = render_passive_creative_runtime_status(
            services,
            task=GenerationTask.CHARACTER_DRAFT,
        )
        busy = render_candidate_action_state(STATE_PREFIX)
        actions = st.columns(2)
        generate = actions[0].button(
            "✦ 生成完整候選角色",
            key=f"{STATE_PREFIX}_generate_complete",
            type="primary",
            width="stretch",
            disabled=(busy or writing_request is None or not generate_status.ready),
        )
        refine = actions[1].button(
            "精修目前內容為候選",
            key=f"{STATE_PREFIX}_ai_refine",
            width="stretch",
            disabled=(busy or writing_request is None or not refine_status.ready),
        )
        selected_action = (
            CreativeCandidateAction.GENERATE
            if generate
            else CreativeCandidateAction.REFINE
            if refine
            else None
        )
        if selected_action is not None and writing_request is not None:
            st.session_state.pop(GENERATION_LENGTH_RESULT_STATE_KEY, None)
            def offline_candidate() -> dict[str, str]:
                values = _offline_inspiration(gender)
                generated_name = values["character_name"]
                if preferred_name:
                    values = {
                        field: value.replace(generated_name, preferred_name)
                        for field, value in values.items()
                    }
                    values["character_name"] = preferred_name
                return values

            request_candidate(
                services,
                prefix=STATE_PREFIX,
                task=GenerationTask.CHARACTER_DRAFT,
                action=selected_action,
                inputs=inputs,
                instruction=revision,
                apply_mode=apply_mode,
                offline_candidate=offline_candidate,
                writing_request=writing_request,
                editor_identity=editor_identity,
            )
            st.session_state[PROVIDER_GENERATION_HOOK_STATE_KEY] = {
                "status": "candidate_requested"
            }
            st.rerun()

    st.session_state.setdefault(PROVIDER_GENERATION_HOOK_STATE_KEY, {})
    render_candidate_error(STATE_PREFIX)
    decision = render_candidate_card(
        STATE_PREFIX,
        field_labels={
            "title": "候選草稿標題",
            "character_name": "候選角色名",
            "character_details": "候選完整角色設定",
            "character_image_prompt_en": "候選角色圖片 Prompt",
            "personal_story": "候選個人故事",
            "background_image_prompt_en": "候選背景圖片 Prompt",
        },
        current_authoring_state_sha256=(
            authoring_editor_state_sha256(
                task=GenerationTask.CHARACTER_DRAFT,
                inputs=inputs,
                instruction=revision,
                writing_request=writing_request,
                editor_identity=editor_identity,
            )
            if writing_request is not None
            else None
        ),
    )
    if decision is None:
        return
    choice, candidate, apply_mode = decision
    if choice == "discard":
        clear_candidate_state(STATE_PREFIX)
        st.rerun()
    pending: dict[str, Any] = {}
    for field in (
        "title",
        "character_name",
        "character_details",
        "character_image_prompt_en",
        "personal_story",
        "background_image_prompt_en",
    ):
        current = str(st.session_state.get(_FORM_KEYS[field], ""))
        preserve_identity = field in {"title", "character_name"}
        candidate_value = str(candidate.get(field, ""))
        if current.strip() and preserve_identity:
            pending[field] = current
        elif candidate_value_replaces_current(
            candidate_value,
            current,
            apply_mode=apply_mode,
        ):
            pending[field] = candidate_value
        else:
            pending[field] = current
    validation = candidate_apply_validation(STATE_PREFIX)
    authoring_response = candidate_authoring_response(STATE_PREFIX)
    if validation is None or authoring_response is None:
        st.error("候選稿的長度合約無法驗證；已阻止套用，原稿沒有變更。")
        return
    story = str(pending.get("personal_story", ""))
    length_spec = authoring_response.provenance.length_spec
    measurement = measure_output(story, unit=length_spec.unit)
    assessment = measurement.assess(length_spec)
    st.session_state[GENERATION_LENGTH_RESULT_STATE_KEY] = {
        "actual": assessment.actual,
        "minimum": assessment.minimum,
        "maximum": assessment.maximum,
        "target": assessment.target,
        "unit": assessment.unit.value,
        "state": assessment.band.value,
        "satisfied": assessment.satisfied,
        "length_spec_sha256": validation.length_spec_sha256,
        "message": (
            f"實際 {assessment.actual:g} {assessment.unit.value}；"
            f"目標 {assessment.target:,}，判定 {assessment.band.value}。"
        ),
    }
    st.session_state[PENDING_FORM_STATE_KEY] = pending
    st.session_state[FLASH_STATE_KEY] = (
        "已把 AI 候選角色套用到編輯器；尚未保存，每一行仍可自由改寫。"
    )
    clear_candidate_state(STATE_PREFIX)
    st.rerun()


def _list_projects(services: Any) -> list[Any]:
    service = getattr(services, "projects", None)
    method = getattr(service, "list_projects", None)
    if not callable(method):
        return []
    try:
        return list(method(include_archived=True))
    except (ApplicationError, TypeError):
        return []


def _project_label(project: Any) -> str:
    name = str(_record_value(project, ("name",), "未命名作品"))
    status = _record_value(project, ("status",), "")
    status_value = str(getattr(status, "value", status)).casefold()
    return f"{name}（已收起）" if status_value == "archived" else name


def _render_project_link(services: Any) -> str | None:
    projects = _list_projects(services)
    by_id = {str(_record_value(project, ("id",))): project for project in projects}
    with st.expander("要替這張草稿夾上一張作品書籤嗎？（選填）"):
        link = st.checkbox(
            "記住它可能屬於哪一本作品",
            key=_FORM_KEYS["link_project"],
            on_change=_capture_editor_state,
            disabled=not projects,
        )
        if not projects:
            st.caption("現在沒有可連結的作品；草稿仍可獨立保存，完全不受影響。")
            return None
        if not link:
            st.caption("先放在草稿箱也很好。這不會自動加入作品角色或改動作品。")
            return None

        options = list(by_id)
        current = st.session_state.get(_FORM_KEYS["project_id"])
        if current not in options:
            selected_project = str(st.session_state.get("selected_project_id") or "")
            st.session_state[_FORM_KEYS["project_id"]] = (
                selected_project if selected_project in options else options[0]
            )
        project_id = st.selectbox(
            "哪一本作品？",
            options,
            format_func=lambda value: _project_label(by_id[value]),
            key=_FORM_KEYS["project_id"],
            on_change=_capture_editor_state,
        )
        st.caption("這只是替草稿做連結；等你準備好時，再把它升級成正式角色。")
        return str(project_id)


def _draft_matches_payload(
    draft: CharacterBiographyDraft,
    payload: Mapping[str, Any],
) -> bool:
    comparable_fields = (
        "title",
        "character_name",
        "gender",
        "character_details",
        "character_image_prompt_en",
        "personal_story",
        "background_image_prompt_en",
        "notes",
        "project_id",
    )
    for field in comparable_fields:
        current = payload.get(field)
        saved = getattr(draft, field)
        if field == "gender":
            if _gender(current) is not saved:
                return False
        elif current != saved:
            return False
    return True


def _as_character_draft(record: Any) -> CharacterBiographyDraft:
    if isinstance(record, CharacterBiographyDraft):
        return record
    if isinstance(record, Mapping):
        return CharacterBiographyDraft.model_validate(record)
    model_dump = getattr(record, "model_dump", None)
    if callable(model_dump):
        return CharacterBiographyDraft.model_validate(model_dump(mode="python"))
    raise TypeError("目前的草稿服務無法提供搬上書架所需的完整資料")


def _render_promotion(
    *,
    services: Any,
    adapter: _CharacterDraftAdapter,
    selected: str,
    payload: Mapping[str, Any],
    prompt_error: bool,
) -> None:
    st.markdown("#### 想把這張紙頁搬上書架嗎？")
    st.caption("完全選填。普通保存只會留在草稿箱；只有下面的確認按鈕會建立或連結作品。")
    if selected == _NEW_DRAFT:
        st.info("先把這張紙頁收進草稿箱；保存後才會出現搬上書架的選項。")
        return

    try:
        saved_draft = _as_character_draft(adapter.get(selected))
    except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
        present_exception(
            exc,
            operation_zh_tw="準備把角色草稿搬上書架",
            key_prefix="character_draft_prepare_promotion",
        )
        return
    dirty = bool(prompt_error) or not _draft_matches_payload(saved_draft, payload)
    if dirty:
        st.warning("紙頁上有尚未保存的修改；請先按「保存這張紙頁」，再搬上書架。")

    projects = [
        project
        for project in _list_projects(services)
        if str(getattr(_record_value(project, ("status",), "active"), "value", "active"))
        == "active"
    ]
    by_id = {str(_record_value(project, ("id",))): project for project in projects}
    with st.expander("搬上書架的去向"):
        options = ("new", "existing") if projects else ("new",)
        destination = st.radio(
            "要放到哪裡？",
            options=options,
            format_func=lambda value: (
                "以這名角色建立一本新作品" if value == "new" else "放進已有作品"
            ),
            horizontal=True,
            key=f"{STATE_PREFIX}_promotion_destination",
        )
        project_id: str | None = None
        project_name = ""
        if destination == "new":
            project_name = st.text_input(
                "新作品名稱",
                value=saved_draft.title,
                key=f"{STATE_PREFIX}_promotion_new_name_{selected}",
            ).strip()
            st.caption("會先建立一個只有書名的作品，再把角色草稿攤到精修工作台。")
        else:
            project_options = list(by_id)
            selection_key = f"{STATE_PREFIX}_promotion_existing_project"
            preferred = saved_draft.project_id or str(
                st.session_state.get("selected_project_id") or ""
            )
            if st.session_state.get(selection_key) not in project_options:
                st.session_state[selection_key] = (
                    preferred if preferred in project_options else project_options[0]
                )
            project_id = str(
                st.selectbox(
                    "選一本作品",
                    project_options,
                    format_func=lambda value: _project_label(by_id[value]),
                    key=selection_key,
                )
            )
            st.caption("這只會先交給精修工作台，不會直接建立正式角色內容、章節或劇本。")

        if st.button(
            "確定搬上書架，打開精修工作台",
            key=f"{STATE_PREFIX}_promote",
            type="primary",
            width="stretch",
            disabled=dirty or (destination == "new" and not project_name),
        ):
            created_project = False
            try:
                # This is a side-effect-free full-contract check and therefore
                # must run before an explicitly requested new Project is made.
                expected_version = _expected_editor_version(saved_draft)
                if expected_version != saved_draft.updated_at:
                    raise ConflictError("角色草稿已被其他編輯更新，未建立作品或搬上書架")
                validate_character_draft_promotion(saved_draft)
                if destination == "new":
                    project = services.projects.create_project(
                        name=project_name,
                        description=(
                            f"從角色草稿〈{saved_draft.title}〉開始；"
                            "是否擴充成完整作品，留待精修工作台決定。"
                        ),
                    )
                    project_id = str(project.id)
                    created_project = True
                if not project_id:
                    raise ValueError("尚未選擇作品")
                handoff = prepare_character_draft_launchpad_handoff(
                    saved_draft,
                    project_id=project_id,
                )
                promoted = adapter.update(
                    selected, {"project_id": project_id}, expected_updated_at=expected_version,
                )
                st.session_state[BASE_UPDATED_AT_STATE_KEY] = _draft_view(promoted).updated_at
                st.session_state[PENDING_FORM_STATE_KEY] = {
                    "project_id": project_id, "link_project": True,
                }
                _capture_editor_state()
                for key in tuple(st.session_state):
                    if any(
                        str(key).startswith(prefix) for prefix in handoff.session_prefixes_to_clear
                    ):
                        st.session_state.pop(key, None)
                st.session_state.update(handoff.session_updates)
                st.rerun()
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="把角色草稿搬上書架",
                    key_prefix="character_draft_promote",
                    data_safety=ErrorDataSafety(
                        message_zh_tw=(
                            "新作品可能已建立；請先到書架確認，再決定是否重試。"
                            if created_project
                            else "這張草稿仍留在草稿箱；沒有標示為已搬上書架。"
                        ),
                        author_input_preserved=True,
                        original_preserved=True,
                    ),
                )


def _strict_refinement_text(target: str, value: str) -> str:
    if target not in {"character_image_prompt_en", "background_image_prompt_en"}:
        return value
    normalized = render_english_keywords(value)
    if normalized != value.strip():
        raise ValueError("圖片提示詞候選必須是規範化的英文逗號關鍵詞")
    return normalized


def _render_content_refinement(
    services: Any,
    *,
    adapter: _CharacterDraftAdapter,
    project_id: str | None,
) -> None:
    target = st.selectbox(
        "通用精修目標欄位",
        tuple(_REFINEMENT_FIELDS),
        format_func=lambda value: _REFINEMENT_FIELDS[value][0],
        key=f"{STATE_PREFIX}_refinement_target",
    )
    field_label, artifact_kind = _REFINEMENT_FIELDS[target]
    prefix = f"{STATE_PREFIX}_content_{target}"
    original = str(st.session_state.get(_FORM_KEYS[target], ""))
    selected = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    binding = _durable_refinement_binding(
        adapter,
        selected=selected,
        field_name=target,
    )
    name = str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip()
    gender = _gender(st.session_state.get(_FORM_KEYS["gender"]))
    protected_facts, protected_spans = protected_facts_and_spans(
        original,
        (name, gender.prompt_token, gender.zh_label),
    )
    with st.expander("✦ 通用內容精修（10 種模式）", expanded=False):
        st.caption(
            f"目前目標：{field_label}。角色名與已選性別若出現在此欄，會作為鎖定內容逐字保護。"
        )
        render_refinement_controls(
            services,
            prefix=prefix,
            artifact_kind=artifact_kind,
            original_text=original,
            context_snapshot={
                "target_field": target,
                "locked_character_facts": {
                    "character_name": name,
                    "gender": gender.value,
                    "gender_prompt_token": gender.prompt_token,
                },
                "notes": str(st.session_state.get(_FORM_KEYS["notes"], "")),
            },
            protected_facts=protected_facts,
            protected_spans=protected_spans,
            durable_binding=binding,
        )
    render_refinement_failure(prefix)
    durable_undo = render_durable_refinement_history(
        services,
        prefix=prefix,
        binding=binding,
    )
    if durable_undo is not None and durable_undo.after_snapshot is not None:
        restored_id = str(durable_undo.after_snapshot["id"])
        st.session_state[PENDING_SELECTION_STATE_KEY] = restored_id
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        st.session_state[FLASH_STATE_KEY] = "已建立復原版本；精修版本紀錄會在重新啟動後保留。"
        st.rerun()
    undo_text = render_refinement_undo(prefix, current_text=original)
    if undo_text is not None:
        st.session_state[PENDING_FORM_STATE_KEY] = {target: undo_text}
        st.session_state[FLASH_STATE_KEY] = (
            f"已復原上次對「{field_label}」的精修；尚未正式保存。"
        )
        st.rerun()

    decision = render_refinement_candidate(prefix, field_name=target)
    if decision is None:
        return
    choice, candidate = decision
    try:
        durable_result = commit_durable_refinement_decision(
            services,
            prefix=prefix,
            decision=choice,
            candidate=candidate,
        )
    except (RuntimeError, TypeError, ValueError):
        set_refinement_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                safe_message="已保存角色草稿版本已變更；候選仍保留，沒有覆寫任何內容。",
            ),
        )
        st.rerun()
    if durable_result is not None:
        if choice is RefinementUIDecision.REJECT:
            clear_refinement_state(prefix)
            st.session_state[FLASH_STATE_KEY] = "已拒絕並保存候選決定；角色草稿沒有變更。"
            st.rerun()
        after = getattr(durable_result, "after_snapshot", None)
        if not isinstance(after, dict) or not isinstance(after.get("id"), str):
            set_refinement_failure(
                prefix,
                GenerationFailure(
                    category=GenerationErrorCategory.INVALID_RESPONSE,
                    safe_message="候選版本收據不完整；沒有覆寫任何內容。",
                ),
            )
            st.rerun()
        st.session_state[PENDING_SELECTION_STATE_KEY] = after["id"]
        st.session_state[BOUND_DRAFT_STATE_KEY] = None
        clear_refinement_state(prefix)
        st.session_state[FLASH_STATE_KEY] = (
            f"精修候選已建立「{field_label}」正式版本與可復原收據。"
            if choice is not RefinementUIDecision.SAVE_AS_NEW
            else "精修候選已另存為新角色草稿，來源與版本關係已保存。"
        )
        st.rerun()
    if choice is RefinementUIDecision.REJECT:
        clear_refinement_state(prefix)
        st.session_state[FLASH_STATE_KEY] = "已拒絕精修候選；角色原稿與結構欄位均未變更。"
        st.rerun()
    try:
        if choice is RefinementUIDecision.SAVE_AS_NEW:
            receipt = candidate_receipt_for_save_as_new(
                candidate,
                current_text=original,
                field_name=target,
            )
            candidate_text = _strict_refinement_text(target, receipt.after_text)
            payload = _form_payload(project_id)
            title = str(payload["title"]).strip()
            payload["title"] = (f"{title}（精修副本）" if title else "精修副本")[:200]
            payload[target] = candidate_text
            adapter.create(payload)
            clear_refinement_state(prefix)
            st.session_state[FLASH_STATE_KEY] = (
                "精修候選已另存為新角色草稿；目前紙頁與原稿欄位均未覆寫。"
            )
            st.rerun()
        receipt = refinement_receipt(
            choice,
            candidate,
            current_text=original,
            field_name=target,
        )
        candidate_text = _strict_refinement_text(target, receipt.after_text)
    except (
        InvalidRefinementCandidateError,
        StaleRefinementBaseError,
        ApplicationError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        set_refinement_failure(
            prefix,
            GenerationFailure(
                category=GenerationErrorCategory.IDEMPOTENCY_CONFLICT,
                safe_message="目前欄位已變更或候選無法安全套用。",
            ),
        )
        st.rerun()
    record_refinement_apply(prefix, receipt, candidate)
    st.session_state[PENDING_FORM_STATE_KEY] = {target: candidate_text}
    st.session_state[FLASH_STATE_KEY] = (
        f"已把精修候選套用至「{field_label}」；尚未正式保存，可用工作階段復原。"
    )
    clear_refinement_state(prefix)
    st.rerun()


def render(*, services: Any | None = None) -> None:
    """Render the draft box; a project is deliberately not a prerequisite."""

    services = services or get_services()
    _restore_editor_state()
    _apply_pending_form()
    pending_selection = st.session_state.pop(PENDING_SELECTION_STATE_KEY, None)
    if pending_selection is not None:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = str(pending_selection)

    raw_service = getattr(services, "character_biography_drafts", None)
    if raw_service is None:
        # Compatibility for the provisional name used before the persistence
        # contract was finalized.
        raw_service = getattr(services, "character_drafts", None)
    adapter = _CharacterDraftAdapter(raw_service)
    persistence_ready = raw_service is not None and adapter.available

    page_header(
        PAGE_LABEL,
        "先收下一個角色念頭，不必先建立作品。名字、故事與圖片提示都能慢慢補齊。",
        eyebrow="一疊可以隨手翻寫的角色紙頁",
        badges=(("無專案也能開始", "teal"), ("離線靈感可用", "amber")),
    )
    # Keep the high-value offline action visually beside the page status while
    # rendering its widgets only after the selected draft has been bound below.
    offline_action_slot = st.container(border=True)
    flash = st.session_state.pop(FLASH_STATE_KEY, None)
    if isinstance(flash, str) and flash:
        st.success(flash)
    generation_error = st.session_state.pop(GENERATION_ERROR_STATE_KEY, None)
    if isinstance(generation_error, str) and generation_error:
        st.error(generation_error)

    records: list[Any] = []
    if persistence_ready:
        try:
            records = adapter.list_active()
        except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
            present_exception(
                exc,
                operation_zh_tw="讀取角色草稿箱",
                key_prefix="character_draft_list",
            )
    else:
        st.info(
            "草稿箱的本機保存服務正在接上書架。你仍可離線隨機填滿、編輯與匯出；"
            "保存按鈕會在服務就緒後啟用。"
        )

    views = [_draft_view(record) for record in records]
    by_id = {draft.id: draft for draft in views if draft.id}
    options = [_NEW_DRAFT, *by_id]
    selected_before = st.session_state.get(SELECTED_DRAFT_STATE_KEY)
    preserve_missing_bound = (
        isinstance(selected_before, str)
        and selected_before != _NEW_DRAFT
        and selected_before == st.session_state.get(BOUND_DRAFT_STATE_KEY)
        and selected_before not in by_id
    )
    if preserve_missing_bound:
        options.append(str(selected_before))
        st.warning("目前草稿已不在可編輯清單；原文仍保留，保存或封存前會檢查版本。")
    if selected_before not in options:
        st.session_state[SELECTED_DRAFT_STATE_KEY] = _NEW_DRAFT

    selector, new_action = st.columns((4, 1))
    with selector:
        selected = st.selectbox(
            "翻開哪張草稿？",
            options,
            format_func=lambda value: (
                "＋ 一張新的角色草稿"
                if value == _NEW_DRAFT
                else f"已變更或封存的草稿 · {value[-6:]}" if value not in by_id
                else f"{by_id[value].title or '未命名草稿'} · "
                f"{by_id[value].character_name or '角色待命名'} · {value[-6:]}"
            ),
            key=SELECTED_DRAFT_STATE_KEY,
            on_change=_capture_editor_state,
        )
    with new_action:
        st.write("")
        if st.button(
            "新紙頁",
            key=f"{STATE_PREFIX}_new",
            width="stretch",
            disabled=selected == _NEW_DRAFT,
        ):
            st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
            st.rerun()

    bound = st.session_state.get(BOUND_DRAFT_STATE_KEY)
    if selected != bound:
        st.session_state.pop(GENERATION_LENGTH_RESULT_STATE_KEY, None)
        st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
        if selected == _NEW_DRAFT:
            _write_form(_blank_form())
            st.session_state[BASE_UPDATED_AT_STATE_KEY] = None
            st.session_state[BOUND_DRAFT_STATE_KEY] = selected
        else:
            try:
                loaded_draft = _draft_view(adapter.get(selected))
                _write_form(_form_from_draft(loaded_draft))
                st.session_state[BASE_UPDATED_AT_STATE_KEY] = loaded_draft.updated_at
                st.session_state[BOUND_DRAFT_STATE_KEY] = selected
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="讀取角色草稿",
                    key_prefix="character_draft_load",
                )
        _capture_editor_state()

    st.caption("切換紙頁前，記得先按「收進草稿箱」。")
    identity = st.columns((3, 3, 2))
    identity[0].text_input(
        "這張草稿叫什麼？",
        placeholder="例如：雨夜郵差／粉紅雙馬尾女孩",
        key=_FORM_KEYS["title"],
        on_change=_capture_editor_state,
    )
    identity[1].text_input(
        "角色名",
        placeholder="可以自己命名，也可留空後抽一個",
        key=_FORM_KEYS["character_name"],
        on_change=_capture_editor_state,
    )
    identity[2].radio(
        "角色性別",
        tuple(CharacterGender),
        format_func=lambda value: value.zh_label,
        horizontal=True,
        key=_FORM_KEYS["gender"],
        on_change=_capture_editor_state,
    )

    st.text_area(
        "這個人完整是什麼樣子？",
        placeholder="外貌、身分、個性、渴望、弱點、習慣與人際鉤子，都可以寫在這裡。",
        height=180,
        key=_FORM_KEYS["character_details"],
        on_change=_capture_editor_state,
    )
    st.text_area(
        "角色圖片提示詞（英文，逗號分隔）",
        placeholder="pink twin tails, amber eyes, gentle expression, ...",
        height=110,
        key=_FORM_KEYS["character_image_prompt_en"],
        on_change=_capture_editor_state,
    )
    st.text_area(
        "她／他的個人故事",
        placeholder="可以是一段過去、一個秘密，也可以是整條角色弧線。",
        height=180,
        key=_FORM_KEYS["personal_story"],
        on_change=_capture_editor_state,
    )
    st.text_area(
        "背景圖片提示詞（英文，逗號分隔）",
        placeholder="misty tidal city, blue hour, wet stone street, ...",
        height=110,
        key=_FORM_KEYS["background_image_prompt_en"],
        on_change=_capture_editor_state,
    )
    st.text_area(
        "頁邊備註",
        placeholder="還沒決定的事、禁用元素、之後想補的細節……",
        height=100,
        key=_FORM_KEYS["notes"],
        on_change=_capture_editor_state,
    )

    # Populate the earlier visual slot only after every editor widget has been
    # mounted. The button's explicit rerun therefore cannot make Streamlit
    # discard authored values that are outside the generated field set.
    with offline_action_slot:
        random_controls = st.columns((2, 3), vertical_alignment="center")
        preserve_written = random_controls[1].checkbox(
            "保留已經寫好的內容，只補空白",
            value=True,
            key=f"{STATE_PREFIX}_random_preserve",
        )
        if random_controls[0].button(
            "✦ 離線隨機填滿",
            key=f"{STATE_PREFIX}_randomize",
            width="stretch",
        ):
            current_gender = _gender(st.session_state.get(_FORM_KEYS["gender"]))
            generated = _offline_inspiration(current_gender)
            if preserve_written:
                preserved_name = str(
                    st.session_state.get(_FORM_KEYS["character_name"], "")
                ).strip()
                if preserved_name:
                    generated_name = generated["character_name"]
                    generated = {
                        field: value.replace(generated_name, preserved_name)
                        for field, value in generated.items()
                    }
            pending_form: dict[str, Any] = {}
            for field, value in generated.items():
                current = str(st.session_state.get(_FORM_KEYS[field], "")).strip()
                if preserve_written:
                    pending_form[field] = current or value
                else:
                    pending_form[field] = value
            st.session_state[PENDING_FORM_STATE_KEY] = pending_form
            st.session_state[FLASH_STATE_KEY] = "離線靈感已落在紙上；每一行都還能自由改寫。"
            st.rerun()

    _render_ai_generation(services)
    project_id = _render_project_link(services)
    _render_content_refinement(
        services,
        adapter=adapter,
        project_id=project_id,
    )

    try:
        payload = _form_payload(project_id)
        prompt_error = False
    except ValueError as exc:
        payload = {
            "title": str(st.session_state.get(_FORM_KEYS["title"], "")).strip(),
            "character_name": str(st.session_state.get(_FORM_KEYS["character_name"], "")).strip(),
            "gender": _gender(st.session_state.get(_FORM_KEYS["gender"])),
            "character_details": str(
                st.session_state.get(_FORM_KEYS["character_details"], "")
            ).strip(),
            "character_image_prompt_en": str(
                st.session_state.get(_FORM_KEYS["character_image_prompt_en"], "")
            ).strip(),
            "personal_story": str(st.session_state.get(_FORM_KEYS["personal_story"], "")).strip(),
            "background_image_prompt_en": str(
                st.session_state.get(_FORM_KEYS["background_image_prompt_en"], "")
            ).strip(),
            "notes": str(st.session_state.get(_FORM_KEYS["notes"], "")).strip(),
            "project_id": project_id,
        }
        prompt_error = True
        present_exception(
            exc,
            operation_zh_tw="檢查角色圖片提示詞格式",
            key_prefix="character_draft_prompt_validation",
            data_safety=ErrorDataSafety(
                message_zh_tw="目前欄位內容仍保留在畫面，未儲存或覆寫草稿。",
                author_input_preserved=True,
                original_preserved=True,
            ),
        )

    save_column, archive_column = st.columns((3, 2))
    if save_column.button(
        "收進草稿箱" if selected == _NEW_DRAFT else "保存這張紙頁",
        key=f"{STATE_PREFIX}_save",
        type="primary",
        width="stretch",
        disabled=not persistence_ready or bool(prompt_error),
    ):
        if not str(payload["title"]).strip():
            st.error("先替這張草稿取一個名字，之後才找得回來。")
        elif not str(payload["character_name"]).strip():
            st.error("先替角色留一個名字；之後仍可隨時改名。")
        else:
            try:
                saved = (
                    adapter.create(payload)
                    if selected == _NEW_DRAFT
                    else adapter.update(
                        selected, payload,
                        expected_updated_at=_expected_editor_version(adapter.get(selected)),
                    )
                )
                saved_id = _draft_view(saved).id
                st.session_state[PENDING_SELECTION_STATE_KEY] = saved_id or selected
                st.session_state[BOUND_DRAFT_STATE_KEY] = None
                st.session_state[FLASH_STATE_KEY] = (
                    "角色草稿已收好。" if selected == _NEW_DRAFT else "這張角色紙頁已保存。"
                )
                st.rerun()
            except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                present_exception(
                    exc,
                    operation_zh_tw="保存角色草稿",
                    key_prefix="character_draft_save",
                    data_safety=ErrorDataSafety(
                        message_zh_tw="目前原文、未保存修改與載入版本均保留；沒有覆寫較新草稿。",
                        author_input_preserved=True,
                        original_preserved=True,
                        draft_selection_preserved=True,
                    ) if isinstance(exc, ConflictError) else None,
                )

    if selected != _NEW_DRAFT:
        confirm_id = st.session_state.get(ARCHIVE_CONFIRM_STATE_KEY)
        if confirm_id != selected:
            if archive_column.button(
                "把這張草稿收起來",
                key=f"{STATE_PREFIX}_archive_start",
                width="stretch",
            ):
                st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = selected
                st.rerun()
        else:
            archive_column.warning("收起後不會刪除內容，但會離開目前草稿清單。")
            confirm, cancel = archive_column.columns(2)
            if confirm.button(
                "確定收起",
                key=f"{STATE_PREFIX}_archive_confirm_action",
                type="secondary",
                width="stretch",
            ):
                try:
                    adapter.archive(
                        selected,
                        expected_updated_at=_expected_editor_version(adapter.get(selected)),
                    )
                    st.session_state[PENDING_SELECTION_STATE_KEY] = _NEW_DRAFT
                    st.session_state[BOUND_DRAFT_STATE_KEY] = None
                    st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
                    st.session_state[FLASH_STATE_KEY] = "草稿已收起，內容沒有被刪除。"
                    st.rerun()
                except (ApplicationError, RuntimeError, TypeError, ValueError) as exc:
                    present_exception(
                        exc,
                        operation_zh_tw="收起角色草稿",
                        key_prefix="character_draft_archive",
                        data_safety=ErrorDataSafety(
                            message_zh_tw="目前原文、未保存修改與載入版本均保留；沒有封存較新草稿。",
                            author_input_preserved=True,
                            original_preserved=True,
                            draft_selection_preserved=True,
                        ) if isinstance(exc, ConflictError) else None,
                    )
            if cancel.button(
                "先不要",
                key=f"{STATE_PREFIX}_archive_cancel",
                width="stretch",
            ):
                st.session_state[ARCHIVE_CONFIRM_STATE_KEY] = None
                st.rerun()

    _render_promotion(
        services=services,
        adapter=adapter,
        selected=selected,
        payload=payload,
        prompt_error=prompt_error,
    )

    st.markdown("#### 帶走這張紙頁")
    export_name = _safe_filename(
        str(payload.get("title") or payload.get("character_name") or "角色草稿")
    )
    exports = st.columns(2)
    exports[0].download_button(
        "下載 TXT",
        data=_txt_export(payload),
        file_name=f"{export_name}.txt",
        mime="text/plain; charset=utf-8",
        key=f"{STATE_PREFIX}_export_txt",
        width="stretch",
        disabled=bool(prompt_error),
    )
    exports[1].download_button(
        "下載 JSON",
        data=_json_export(payload, None if selected == _NEW_DRAFT else selected),
        file_name=f"{export_name}.json",
        mime="application/json",
        key=f"{STATE_PREFIX}_export_json",
        width="stretch",
        disabled=bool(prompt_error),
    )
    _capture_editor_state()


__all__ = [
    "ARCHIVE_CONFIRM_STATE_KEY",
    "BASE_UPDATED_AT_STATE_KEY",
    "BOUND_DRAFT_STATE_KEY",
    "GENERATION_ACTION_STATE_KEY",
    "GENERATION_CLUE_STATE_KEY",
    "GENERATION_PROVIDER_STATE_KEY",
    "GENERATION_SOURCE_STATE_KEY",
    "OPENAI_API_KEY_STATE_KEY",
    "PAGE_KEY",
    "PAGE_LABEL",
    "PENDING_FORM_STATE_KEY",
    "PROVIDER_GENERATION_HOOK_STATE_KEY",
    "SELECTED_DRAFT_STATE_KEY",
    "render",
]
