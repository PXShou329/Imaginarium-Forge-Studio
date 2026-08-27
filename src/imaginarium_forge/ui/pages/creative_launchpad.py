"""Creative Launchpad — one guided entry into Canon, Story and Prompt."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Literal, cast
from urllib.parse import urlsplit

import streamlit as st
from pydantic import ValidationError

from imaginarium_forge.application.errors import ApplicationError
from imaginarium_forge.application.services.creative_automation_service import (
    CreativeAutomationResult,
)
from imaginarium_forge.application.services.creative_inspiration_service import (
    CreativeInspiration,
    CreativeInspirationService,
    InspirationKind,
    normalize_english_keywords,
    render_english_keywords,
)
from imaginarium_forge.application.services.video_prompt_bundle_service import (
    VideoPromptCompileResult,
    VideoPromptPreparedExport,
)
from imaginarium_forge.domain.character.gender import (
    CharacterGender,
    apply_gender_prompt_token,
)
from imaginarium_forge.domain.creative.models import (
    CharacterBlueprint,
    CharacterLaunchResult,
    CreationMode,
    CreativeLaunchRequest,
    CreativeParticipantDraft,
    CreativeParticipantSource,
    GenreFamily,
    ParticipantLaunchResult,
    ParticipantManifest,
    ParticipantPin,
    PromptLaunchResult,
    StoryFoundationResult,
    WorldFoundationResult,
)
from imaginarium_forge.domain.creative.story_bootstrap import (
    CreativeStoryBootstrapRequest,
    CreativeStoryBootstrapResult,
)
from imaginarium_forge.domain.prompt.content_mode import ContentMode, derives_adult
from imaginarium_forge.domain.prompt.video import VideoAspectRatio
from imaginarium_forge.domain.story.models import (
    IntensityLevel,
    NarrativePov,
    NarrativeTense,
    StructureProfile,
)
from imaginarium_forge.domain.story.short_story import StoryGenerationPurpose
from imaginarium_forge.ui.bootstrap import Services, get_services
from imaginarium_forge.ui.components import page_header
from imaginarium_forge.ui.project_gateway import ProjectGatewayCopy, render_project_gateway

_MODE_LABELS = {
    CreationMode.SERIES_STORY: "系列故事",
    CreationMode.CHARACTER_STORY: "角色＋背景故事",
    CreationMode.CHARACTER_ONLY: "僅角色",
    CreationMode.WORLD_ONLY: "僅世界觀",
}
_MODE_HELP = {
    CreationMode.SERIES_STORY: "建立世界觀、故事需求與大綱，適合章回或連載。",
    CreationMode.CHARACTER_STORY: "先建立可追溯角色，再把角色放進自己的背景故事。",
    CreationMode.CHARACTER_ONLY: "專注角色身分、個性、Visual DNA 與媒體提示草稿。",
    CreationMode.WORLD_ONLY: "建立可獨立保存的世界設定，不強迫建立角色或故事主線。",
}
_GENRE_LABELS = {
    GenreFamily.FANTASY: "奇幻",
    GenreFamily.SCIENCE_FICTION: "科幻",
    GenreFamily.MYSTERY_CRIME: "推理／犯罪",
    GenreFamily.THRILLER_SUSPENSE: "驚悚／懸疑",
    GenreFamily.ROMANCE: "愛情",
    GenreFamily.HORROR: "恐怖",
    GenreFamily.ACTION_ADVENTURE: "動作／冒險",
    GenreFamily.DRAMA_LITERARY: "劇情／文學",
    GenreFamily.HISTORICAL: "歷史",
    GenreFamily.COMEDY_SATIRE: "喜劇／諷刺",
    GenreFamily.SLICE_OF_LIFE: "日常",
    GenreFamily.HYBRID_CUSTOM: "混合／自訂",
}
_POV_LABELS = {
    NarrativePov.FIRST: "第一人稱",
    NarrativePov.SECOND: "第二人稱",
    NarrativePov.THIRD_LIMITED: "第三人稱限知",
    NarrativePov.THIRD_OMNISCIENT: "第三人稱全知",
}
_TENSE_LABELS = {NarrativeTense.PAST: "過去式", NarrativeTense.PRESENT: "現在式"}
_STRUCTURE_LABELS = {
    StructureProfile.THREE_ACT: "三幕式",
    StructureProfile.FOUR_ACT: "四幕式",
    StructureProfile.HEROS_JOURNEY: "英雄旅程",
    StructureProfile.SAVE_THE_CAT: "救貓咪節拍",
    StructureProfile.KISHOTENKETSU: "起承轉合",
    StructureProfile.SEVEN_POINT: "七點結構",
    StructureProfile.FREEFORM: "自由結構",
}
_INTENSITY_LABELS = {
    IntensityLevel.NONE: "無",
    IntensityLevel.LOW: "低",
    IntensityLevel.MODERATE: "中",
    IntensityLevel.HIGH: "高",
    IntensityLevel.EXPLICIT: "露骨",
}
_CONTENT_LABELS = {
    ContentMode.GENERAL: "一般",
    ContentMode.MATURE_NONSEXUAL: "成熟題材（非性）",
    ContentMode.DARK: "黑暗",
    ContentMode.HORROR: "恐怖",
    ContentMode.VIOLENT: "暴力",
    ContentMode.SUGGESTIVE: "成人暗示",
    ContentMode.EXPLICIT_ADULT: "成人露骨",
}

_VideoAspectValue = Literal["16:9", "9:16", "1:1", "4:3", "3:4"]


def _lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


def _commas(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.replace("，", ",").split(",") if part.strip())


def _inspiration_form_values(inspiration: CreativeInspiration) -> dict[str, object]:
    """Map an offline suggestion into ordinary editable widget values."""
    common: dict[str, object] = {}
    if inspiration.title_suggestion:
        common["starter_title"] = inspiration.title_suggestion
    if inspiration.concept:
        common["starter_concept"] = inspiration.concept
    common.update(
        {
            "starter_genre_primary": inspiration.primary_genre,
            "starter_genre_tags": ", ".join(inspiration.genre_tags),
            "starter_tone": inspiration.tone,
            "starter_world_setting": inspiration.setting,
            "starter_world_period": inspiration.time_period,
            "starter_world_rules": "\n".join(inspiration.world_rules),
            "starter_world_locations": "\n".join(inspiration.locations),
            "starter_world_social": inspiration.social_context,
            "starter_world_tech_magic": inspiration.technology_or_magic,
            "starter_central_conflict": inspiration.central_conflict,
            "starter_direction": inspiration.direction,
            "starter_ending": inspiration.ending_preference,
        }
    )
    if inspiration.kind is InspirationKind.STORY:
        common["starter_path"] = CreationMode.SERIES_STORY
    elif inspiration.kind is InspirationKind.CHARACTER:
        common.update(
            {
                "starter_path": CreationMode.CHARACTER_ONLY,
                "starter_include_character": True,
                "starter_p2_enabled": False,
                "starter_char_name": inspiration.character_name_suggestion,
                "starter_char_age": inspiration.character_age_suggestion or 21,
                "starter_char_bio": inspiration.character_biography,
                "starter_char_personality": inspiration.character_personality,
                "starter_char_voice": inspiration.character_voice,
                "starter_char_identity": inspiration.character_identity,
                "starter_char_face": inspiration.character_face,
                "starter_char_hair": inspiration.character_hair,
                "starter_char_eyes": inspiration.character_eyes,
                "starter_char_body": inspiration.character_body,
                "starter_char_features": inspiration.english_character_prompt,
            }
        )
    elif inspiration.kind is InspirationKind.WORLD:
        common["starter_path"] = CreationMode.WORLD_ONLY
    return common


def _queue_inspiration(
    inspiration: CreativeInspiration,
) -> None:
    values = _inspiration_form_values(inspiration)
    if inspiration.kind is InspirationKind.CHARACTER:
        selected_gender = st.session_state.get(
            "starter_char_gender", CharacterGender.FEMALE
        )
        if isinstance(selected_gender, CharacterGender):
            incoming_keywords = normalize_english_keywords(
                str(values.get("starter_char_features", ""))
            )
            values["starter_char_features"] = render_english_keywords(
                apply_gender_prompt_token(incoming_keywords, selected_gender)
            )
    # Inspiration fills blanks; it never silently overwrites authored form
    # content.  The route is the deliberate exception because each button is
    # also an explicit request to switch to that matching creation path.
    for key in tuple(values):
        if key == "starter_path":
            continue
        authored_value = st.session_state.get(key)
        if _is_meaningful_form_value(authored_value):
            values[key] = authored_value
    st.session_state["starter_pending_form"] = values
    st.session_state["starter_inspiration_notice"] = inspiration.kind.value
    st.rerun()


def _is_meaningful_form_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, set, frozenset, dict)):
        return bool(value)
    # Numbers, booleans and enum selections are explicit form values.  Keep
    # them rather than treating valid falsy choices such as 0/False as empty.
    return True


def _go(page: str) -> None:
    st.session_state["pending_nav"] = page
    st.rerun()


def _queue_story_studio_handoff(
    *,
    story: StoryFoundationResult,
    bootstrap: CreativeStoryBootstrapResult | None,
    purpose: StoryGenerationPurpose,
) -> None:
    """Stage one Story Studio destination without generating or saving."""

    pending: dict[str, object] = {
        "story_req_id": story.requirement_id,
        "story_bible_id": story.bible_id,
        "story_outline_id": story.outline_id,
        "story_chapter_id": "",
        "story_scene_id": "",
        "story_active_section": "generation",
        "story_generation_task": purpose.value,
        "story_generation_preview": bootstrap is not None,
    }
    if bootstrap is not None:
        pending.update(
            {
                "story_chapter_id": bootstrap.chapter_id,
                "story_scene_id": bootstrap.scene_id,
            }
        )
    st.session_state["story_pending"] = pending


def _load_request() -> CreativeLaunchRequest | None:
    raw = st.session_state.get("starter_request_json")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return CreativeLaunchRequest.model_validate_json(raw)
    except ValidationError:
        st.session_state.pop("starter_request_json", None)
        return None


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    return str(first.get("msg", "創作藍圖欄位有誤")).removeprefix("Value error, ")


@dataclass(frozen=True, slots=True)
class _EndpointSummary:
    """Security-relevant endpoint facts without retaining URL secrets."""

    redacted_origin: str
    has_userinfo: bool
    uses_tls: bool
    valid: bool
    is_loopback: bool


def _loopback_host(host: str) -> bool:
    normalized = host.casefold().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _endpoint_summary(url: str) -> _EndpointSummary:
    """Parse an Ollama URL into a safe display origin and gating facts.

    The returned origin is deliberately limited to scheme, host and numeric
    port.  User info, paths, query strings and fragments are never echoed.
    Query strings and fragments are rejected because they are unnecessary for
    an Ollama base URL and are common places for credentials to be embedded.
    """

    raw = url.strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return _EndpointSummary(
            redacted_origin="（無效端點）",
            has_userinfo=False,
            uses_tls=False,
            valid=False,
            is_loopback=False,
        )

    scheme = parsed.scheme.casefold()
    host = parsed.hostname or ""
    has_userinfo = parsed.username is not None or parsed.password is not None
    try:
        parsed_port = parsed.port
        port_is_valid = parsed_port is None or 1 <= parsed_port <= 65535
    except ValueError:
        parsed_port = None
        port_is_valid = False
    authority_without_userinfo = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    if authority_without_userinfo.endswith(":"):
        port_is_valid = False

    display_scheme = scheme if scheme in {"http", "https"} else "unknown"
    display_host = host or "（無效主機）"
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    display_port = f":{parsed_port}" if parsed_port is not None else ""
    origin = f"{display_scheme}://{display_host}{display_port}"

    host_is_well_formed = bool(host) and not any(
        character.isspace() or character in {"/", "\\", "@", "#", "?"} for character in host
    )
    valid = (
        bool(raw)
        and scheme in {"http", "https"}
        and host_is_well_formed
        and port_is_valid
        and not has_userinfo
        and not parsed.query
        and not parsed.fragment
    )
    return _EndpointSummary(
        redacted_origin=origin,
        has_userinfo=has_userinfo,
        uses_tls=scheme == "https",
        valid=valid,
        is_loopback=valid and _loopback_host(host),
    )


def _reset_for_project(project_id: str | None) -> None:
    previous = st.session_state.get("starter_project_id")
    if previous == project_id:
        return
    for key in list(st.session_state):
        if str(key).startswith("starter_"):
            st.session_state.pop(key, None)
    st.session_state["starter_project_id"] = project_id


def _existing_role_key(version_id: str) -> str:
    return f"starter_p2_existing_{version_id}_role"


def _blueprint_form_values(prefix: str, character: CharacterBlueprint) -> dict[str, object]:
    values: dict[str, object] = {
        f"{prefix}_name": character.name,
        f"{prefix}_gender": character.gender or CharacterGender.FEMALE,
        f"{prefix}_age": character.explicit_age or 0,
        f"{prefix}_age_confirmed": character.user_confirmed_age,
        f"{prefix}_adult_presentation": character.adult_presentation_confirmed,
        f"{prefix}_bio": character.biography,
        f"{prefix}_personality": character.personality,
        f"{prefix}_voice": character.voice,
        f"{prefix}_motivation": character.motivation,
        f"{prefix}_fear": character.fear,
        f"{prefix}_secret": character.secret,
        f"{prefix}_internal_conflict": character.internal_conflict,
        f"{prefix}_relationships": "\n".join(character.relationship_hooks),
        f"{prefix}_arc_start": character.arc_start,
        f"{prefix}_arc_turns": "\n".join(character.arc_turning_points),
        f"{prefix}_arc_end": character.arc_end,
        f"{prefix}_identity": character.identity,
        f"{prefix}_face": character.face,
        f"{prefix}_hair": character.hair,
        f"{prefix}_eyes": character.eyes,
        f"{prefix}_body": character.body,
    }
    if prefix == "starter_char":
        values.update(
            {
                "starter_char_features": ", ".join(character.distinguishing_features),
                "starter_char_prohibited": ", ".join(character.prohibited_mutations),
                "starter_char_action": character.action,
                "starter_char_expression": character.expression,
            }
        )
    return values


def _request_form_values(request: CreativeLaunchRequest) -> dict[str, object]:
    """Stage AI suggestions before their keyed widgets are instantiated."""
    values: dict[str, object] = {
        "starter_path": request.mode,
        "starter_title": request.title,
        "starter_concept": request.concept,
        "starter_story_logline": request.story_logline,
        "starter_story_synopsis": request.story_synopsis,
        "starter_story_opening_hook": request.story_opening_hook,
        "starter_genre_primary": request.primary_genre,
        "starter_genre_secondary": list(request.secondary_genres),
        "starter_genre_tags": ", ".join(request.genre_tags),
        "starter_genre_custom": request.custom_genre,
        "starter_world_setting": request.setting,
        "starter_world_period": request.time_period,
        "starter_world_rules": "\n".join(request.world_rules),
        "starter_world_locations": "\n".join(request.locations),
        "starter_world_social": request.social_context,
        "starter_world_tech_magic": request.technology_or_magic,
        "starter_pov": request.pov,
        "starter_tense": request.tense,
        "starter_structure": request.structure_profile,
        "starter_direction": request.direction,
        "starter_central_conflict": request.central_conflict,
        "starter_ending": request.ending_preference,
        "starter_tone": request.tone,
        "starter_prose": request.prose_style_notes,
        "starter_target_length": request.target_length,
        "starter_pacing": request.pacing,
        "starter_dialogue": request.dialogue_density,
        "starter_audience": request.audience,
        "starter_themes": ", ".join(request.themes),
        "starter_must_include": "\n".join(request.must_include),
        "starter_must_avoid": "\n".join(request.must_avoid),
        "starter_violence": request.violence_intensity,
        "starter_horror": request.horror_intensity,
        "starter_intimacy": request.intimacy_intensity,
        "starter_content_mode": request.content_mode,
        "starter_scene_location": request.scene_location,
        "starter_scene_time": request.scene_time,
        "starter_scene_weather": request.scene_weather,
        "starter_scene_atmosphere": request.scene_atmosphere,
        "starter_scene_lighting": request.scene_lighting,
        "starter_scene_camera": request.scene_camera,
        "starter_scene_motion": request.scene_motion,
        "starter_scene_duration": request.video_duration_seconds,
        "starter_video_fps": request.video_fps,
        "starter_video_aspect_ratio": request.video_aspect,
        "starter_video_loop": request.video_loop,
    }
    if request.character is not None:
        values.update(
            {
                "starter_include_character": True,
                "starter_p2_enabled": False,
                **_blueprint_form_values("starter_char", request.character),
            }
        )
        if request.english_character_keywords:
            values["starter_char_features"] = render_english_keywords(
                request.english_character_keywords
            )
    elif request.participants:
        values.update(
            {
                "starter_include_character": True,
                "starter_p2_enabled": True,
            }
        )
        primary = next((draft for draft in request.participants if draft.is_primary), None)
        if primary is not None and primary.blueprint is not None:
            values.update(_blueprint_form_values("starter_char", primary.blueprint))
            values["starter_p2_primary_role"] = primary.role

        additional_new = [
            draft
            for draft in request.participants
            if not draft.is_primary
            and draft.source is CreativeParticipantSource.NEW_BLUEPRINT
            and draft.blueprint is not None
        ]
        values["starter_p2_new_count"] = len(additional_new)
        for index, draft in enumerate(additional_new, start=1):
            prefix = f"starter_p2_new_{index}"
            blueprint = draft.blueprint
            if blueprint is None:  # filtered above; keep static typing honest
                continue
            values.update(_blueprint_form_values(prefix, blueprint))
            values[f"{prefix}_role"] = draft.role

        existing = [
            draft
            for draft in request.participants
            if draft.source is CreativeParticipantSource.EXISTING_CANON
        ]
        values["starter_p2_existing_versions"] = [draft.character_version_id for draft in existing]
        for draft in existing:
            values[_existing_role_key(draft.character_version_id)] = draft.role
    if request.english_character_keywords:
        values["starter_char_features"] = render_english_keywords(
            request.english_character_keywords
        )
    return values


def _consume_automation_handoff() -> None:
    """Move one project-bound Inspiration Desk candidate into the editor.

    The handoff only pre-fills widgets and the immutable request preview.  It
    never saves Canon/story/prompt rows, carries provider authorization, or
    bypasses the normal validation and eligibility checks below.
    """

    raw = st.session_state.pop("creative_automation_handoff", None)
    if not isinstance(raw, dict):
        return
    selected = st.session_state.get("selected_project_id")
    try:
        if raw.get("schema_version") != 2:
            raise ValueError("不支援的靈感交接版本")
        project_id = str(raw.get("project_id", ""))
        result = CreativeAutomationResult.model_validate(
            {
                "request": raw.get("request"),
                "draft": raw.get("draft"),
                "filled_fields": raw.get("filled_fields", ()),
                "preserved_fields": raw.get("preserved_fields", ()),
                "provenance": raw.get("provenance"),
                "result_fingerprint": raw.get("result_fingerprint"),
            }
        )
        request = result.request
        if not selected or project_id != selected or request.project_id != selected:
            raise ValueError("靈感候選不屬於目前打開的作品")
    except (TypeError, ValueError, ValidationError):
        st.session_state["starter_automation_handoff_notice"] = {
            "kind": "error",
            "text": "這份靈感候選已失效或屬於另一本作品，沒有套用任何內容。",
        }
        return

    for key in (
        "starter_preview",
        "starter_character_result",
        "starter_participant_result",
        "starter_story_result",
        "starter_world_result",
        "starter_story_bootstrap_result",
        "starter_prompt_result",
        "starter_video_prompt_result",
        "starter_video_request_fingerprint",
        "starter_video_prepared_export",
    ):
        st.session_state.pop(key, None)
    st.session_state["starter_request_json"] = request.model_dump_json()
    st.session_state["starter_pending_form"] = _request_form_values(request)
    st.session_state["starter_automation_handoff_notice"] = {
        "kind": "success",
        "text": "靈感候選已攤在精修桌上；逐欄讀過後，再決定要保存哪些內容。",
    }


def render() -> None:
    raw_project_id = st.session_state.get("selected_project_id")
    project_id = raw_project_id if isinstance(raw_project_id, str) else None
    # Clear the previous book before consuming a project-bound handoff.  Doing
    # this in the opposite order would erase a valid B handoff when the last
    # Launchpad visit belonged to book A.
    _reset_for_project(project_id)
    _consume_automation_handoff()
    for key, value in st.session_state.pop("starter_pending_form", {}).items():
        st.session_state[key] = value
    page_header(
        "精修桌",
        "逐頁調整角色、世界、故事與提示詞；確認喜歡之後，再正式收進這本作品。",
        eyebrow="把自動靈感磨成自己的文字",
        badges=(("手動細調", "teal"), ("分段保存", "")),
    )
    automation_notice = st.session_state.pop("starter_automation_handoff_notice", None)
    if isinstance(automation_notice, dict):
        if automation_notice.get("kind") == "success":
            st.success(str(automation_notice.get("text", "靈感候選已套用")))
        else:
            st.error(str(automation_notice.get("text", "靈感候選無法套用")))
    services = get_services()
    if not project_id:
        render_project_gateway(
            key_prefix="creative_launchpad",
            copy=ProjectGatewayCopy(
                feature_name="完整創作與精修",
                project_reason="這裡會把角色、世界與故事分段正式寫入作品，因此需要明確的收藏位置。",
                draft_description="先做獨立角色與個人故事，喜歡後再帶進完整精修流程。",
                draft_button_label="先做角色與故事草稿",
            ),
        )
        return

    project = services.projects.get_project(project_id)
    st.caption(f"目前作品：{project.name} · 內容預設只保存在這台電腦")

    inspiration_notice = st.session_state.pop("starter_inspiration_notice", None)
    if inspiration_notice:
        st.success("本機隨機靈感已填入可編輯欄位；名稱若已由你填寫則保持不變。")
    st.markdown("#### 本機靈感起手式")
    st.caption("只使用內建素材隨機組合，不連線、不呼叫模型，也不自動保存。")

    st.markdown("### 1 · 選擇創作路線")
    mode = st.radio(
        "你想從哪裡開始？",
        list(CreationMode),
        format_func=lambda value: _MODE_LABELS[value],
        horizontal=True,
        key="starter_path",
    )
    st.info(_MODE_HELP[mode])
    include_character = mode is not CreationMode.WORLD_ONLY
    if mode is CreationMode.SERIES_STORY:
        st.session_state.setdefault("starter_include_character", True)
        include_character = st.checkbox(
            "同時建立主要角色設定",
            key="starter_include_character",
        )

    use_roster = False
    additional_new_count = 0
    selected_existing_versions: list[str] = []
    existing_roles: dict[str, str] = {}
    existing_version_labels: dict[str, str] = {}
    existing_version_characters: dict[str, str] = {}
    if include_character:
        st.markdown("#### 參與角色名單")
        use_roster = st.checkbox(
            "啟用多人角色名單（主要角色仍使用下方完整編輯器）",
            key="starter_p2_enabled",
        )
        if use_roster:
            st.caption(
                "可加入最多 4 位新角色，也能從目前作品選擇已保存的角色版本。"
                "保存後，故事與圖片提示會使用同一份已確認角色名單。"
            )
            additional_new_count = int(
                st.selectbox(
                    "額外新角色數量",
                    list(range(5)),
                    format_func=lambda value: f"{value} 位",
                    key="starter_p2_new_count",
                )
            )
            choices: list[tuple[str, str, str, int]] = []
            for existing_character in services.characters.list_characters(
                project_id, include_archived=False
            ):
                for existing_version in services.versions.list_versions(existing_character.id):
                    choices.append(
                        (
                            existing_version.id,
                            existing_character.id,
                            existing_character.name,
                            existing_version.version_number,
                        )
                    )
            for version_id, character_id, name, version_number in sorted(
                choices, key=lambda item: (item[2].casefold(), item[3], item[0])
            ):
                existing_version_characters[version_id] = character_id
                existing_version_labels[version_id] = (
                    f"{name} · v{version_number} · {character_id[:8]}/{version_id[:8]}"
                )
            selected_existing_versions = st.multiselect(
                "加入既有角色版本",
                list(existing_version_labels),
                format_func=lambda version_id: existing_version_labels[version_id],
                key="starter_p2_existing_versions",
            )
            for version_id in selected_existing_versions:
                existing_roles[version_id] = st.text_input(
                    f"{existing_version_labels[version_id]} 的故事定位",
                    value="參與角色",
                    key=_existing_role_key(version_id),
                )

    st.markdown("### 2 · 定義作品、世界與內容尺度")
    char_name = ""
    with st.form("starter_blueprint_form", border=True):
        inspiration_buttons = st.columns(3)
        random_story = inspiration_buttons[0].form_submit_button(
            "隨機故事靈感",
            use_container_width=True,
            key="starter_random_story",
        )
        random_character = inspiration_buttons[1].form_submit_button(
            "隨機角色靈感",
            use_container_width=True,
            key="starter_random_character",
        )
        random_world = inspiration_buttons[2].form_submit_button(
            "隨機世界觀",
            use_container_width=True,
            key="starter_random_world",
        )
        title = st.text_input("作品／角色企劃名稱 *", key="starter_title")
        concept = st.text_area(
            "核心概念與期望走向" if mode is CreationMode.CHARACTER_ONLY else "故事概念 *",
            height=120,
            key="starter_concept",
            placeholder="描述世界、衝突、角色現在面對的問題，以及你期待故事往哪裡走。",
        )
        story_logline = ""
        story_synopsis = ""
        story_opening_hook = ""
        if mode in {CreationMode.SERIES_STORY, CreationMode.CHARACTER_STORY}:
            st.markdown("#### 這個故事會長成什麼樣子")
            story_logline = st.text_area(
                "一句話故事",
                height=80,
                key="starter_story_logline",
                placeholder="用一句話說清楚主角、阻力與最重要的選擇。",
            )
            story_synopsis = st.text_area(
                "故事全貌",
                height=180,
                key="starter_story_synopsis",
                placeholder="把靈感桌生成的完整故事摘要留在這裡，再自由改寫。",
            )
            story_opening_hook = st.text_area(
                "第一個讓人想讀下去的畫面",
                height=100,
                key="starter_story_opening_hook",
            )

        genre_left, genre_right = st.columns(2)
        primary_genre = genre_left.selectbox(
            "主要類型",
            list(GenreFamily),
            format_func=lambda value: _GENRE_LABELS[value],
            key="starter_genre_primary",
        )
        secondary_genres = genre_right.multiselect(
            "次要類型",
            [value for value in GenreFamily if value != primary_genre],
            format_func=lambda value: _GENRE_LABELS[value],
            key="starter_genre_secondary",
        )
        tag_left, tag_right = st.columns(2)
        genre_tags = tag_left.text_input("類型標籤（逗號分隔）", key="starter_genre_tags")
        custom_genre = tag_right.text_input("自訂類型", key="starter_genre_custom")

        st.markdown("#### 世界觀")
        world_left, world_right = st.columns(2)
        setting = world_left.text_area("世界／舞台", key="starter_world_setting")
        time_period = world_right.text_input("時代／時間範圍", key="starter_world_period")
        world_rules = st.text_area("不可違反的世界規則（每行一項）", key="starter_world_rules")
        world_locations = st.text_area("重要地點（每行一項）", key="starter_world_locations")
        world_meta_left, world_meta_right = st.columns(2)
        social_context = world_meta_left.text_area("社會／文化背景", key="starter_world_social")
        technology_or_magic = world_meta_right.text_area(
            "科技／魔法系統", key="starter_world_tech_magic"
        )

        st.markdown("#### 敘事方向")
        narrative_a, narrative_b, narrative_c = st.columns(3)
        pov = narrative_a.selectbox(
            "敘事人稱",
            list(NarrativePov),
            format_func=lambda value: _POV_LABELS[value],
            key="starter_pov",
        )
        tense = narrative_b.selectbox(
            "時態",
            list(NarrativeTense),
            format_func=lambda value: _TENSE_LABELS[value],
            key="starter_tense",
        )
        structure = narrative_c.selectbox(
            "故事結構",
            list(StructureProfile),
            format_func=lambda value: _STRUCTURE_LABELS[value],
            key="starter_structure",
        )
        direction = st.text_area("故事走向／角色弧線", key="starter_direction")
        central_conflict = st.text_area("核心衝突", key="starter_central_conflict")
        ending = st.text_area("期望結局或收束狀態", key="starter_ending")
        tone_left, tone_right = st.columns(2)
        tone = tone_left.text_input("基調", key="starter_tone")
        prose = tone_right.text_input("文風", key="starter_prose")
        craft_a, craft_b, craft_c = st.columns(3)
        target_length = craft_a.text_input("篇幅／連載規模", key="starter_target_length")
        pacing = craft_b.text_input("節奏", key="starter_pacing")
        dialogue = craft_c.text_input("對話密度", key="starter_dialogue")
        audience = st.text_input("目標讀者／閱讀體驗", key="starter_audience")
        themes = st.text_input("主題（逗號分隔）", key="starter_themes")
        include_text = st.text_area("必須包含（每行一項）", key="starter_must_include")
        avoid_text = st.text_area("必須避免（每行一項）", key="starter_must_avoid")

        st.markdown("#### 內容尺度")
        st.caption("暴力、恐怖與親密是三個獨立創作旋鈕；只有成人性內容會啟動成人資格閘門。")
        scale_a, scale_b, scale_c = st.columns(3)
        violence = scale_a.selectbox(
            "暴力強度",
            list(IntensityLevel),
            format_func=lambda value: _INTENSITY_LABELS[value],
            key="starter_violence",
        )
        horror = scale_b.selectbox(
            "恐怖強度",
            list(IntensityLevel),
            format_func=lambda value: _INTENSITY_LABELS[value],
            key="starter_horror",
        )
        intimacy_options = list(IntensityLevel)
        if mode is CreationMode.WORLD_ONLY:
            intimacy_options = [
                value for value in intimacy_options if value is not IntensityLevel.EXPLICIT
            ]
        intimacy = scale_c.selectbox(
            "親密強度",
            intimacy_options,
            format_func=lambda value: _INTENSITY_LABELS[value],
            key="starter_intimacy",
        )
        content_options = list(ContentMode)
        if mode is CreationMode.WORLD_ONLY:
            content_options = [value for value in content_options if not derives_adult(value)]
        content_mode = st.selectbox(
            "內容模式",
            content_options,
            format_func=lambda value: _CONTENT_LABELS[value],
            key="starter_content_mode",
        )

        character_values: dict[str, object] | None = None
        primary_role = ""
        additional_new_values: list[tuple[str, dict[str, object]]] = []
        if include_character:
            st.markdown("### 3 · 主要角色與視覺 DNA")
            char_a, char_b = st.columns([2, 1])
            char_name = char_a.text_input("角色名稱 *", key="starter_char_name")
            char_gender = char_b.radio(
                "角色性別",
                options=(CharacterGender.FEMALE, CharacterGender.MALE),
                format_func=lambda value: value.zh_label,
                horizontal=True,
                key="starter_char_gender",
            )
            st.session_state.setdefault("starter_char_age", 21)
            char_age = int(
                char_b.number_input(
                    "明確年齡",
                    min_value=0,
                    max_value=200,
                    step=1,
                    key="starter_char_age",
                )
            )
            confirm_a, confirm_b = st.columns(2)
            age_confirmed = confirm_a.checkbox(
                "我確認這是角色目前設定的明確年齡",
                key="starter_char_age_confirmed",
            )
            adult_presentation = confirm_b.checkbox(
                "我確認此版本是成人呈現（非未成年期或孩童化設計）",
                key="starter_char_adult_presentation",
            )
            if use_roster:
                primary_role = st.text_input(
                    "主要角色的故事定位",
                    value="主角",
                    key="starter_p2_primary_role",
                )
            biography = st.text_area("角色背景", key="starter_char_bio")
            personality = st.text_area("個性、慾望與弱點", key="starter_char_personality")
            voice = st.text_area("語氣／聲音", key="starter_char_voice")
            depth_a, depth_b = st.columns(2)
            motivation = depth_a.text_area("真正想要的事", key="starter_char_motivation")
            fear = depth_b.text_area("最害怕的事", key="starter_char_fear")
            secret = depth_a.text_area("不願被知道的秘密", key="starter_char_secret")
            internal_conflict = depth_b.text_area(
                "內在衝突", key="starter_char_internal_conflict"
            )
            relationship_hooks = st.text_area(
                "可延伸的人際線（每行一項）", key="starter_char_relationships"
            )
            st.markdown("#### 角色成長線")
            arc_a, arc_b = st.columns(2)
            arc_start = arc_a.text_area("故事開始時", key="starter_char_arc_start")
            arc_end = arc_b.text_area("故事結束時", key="starter_char_arc_end")
            arc_turns = st.text_area(
                "重要轉折（每行一項）", key="starter_char_arc_turns"
            )
            visual_a, visual_b = st.columns(2)
            identity = visual_a.text_input("身分特徵", key="starter_char_identity")
            face = visual_b.text_input("臉部", key="starter_char_face")
            visual_c, visual_d, visual_e = st.columns(3)
            hair = visual_c.text_input("髮型／髮色", key="starter_char_hair")
            eyes = visual_d.text_input("眼睛", key="starter_char_eyes")
            body = visual_e.text_input("體態", key="starter_char_body")
            features = st.text_input(
                "英文角色關鍵字 prompt（英文逗號分隔）",
                key="starter_char_features",
                placeholder="short black hair, amber eyes, dark long coat",
            )
            st.caption("可自由編輯；保存時會正規化為英文 comma prompt，非英文內容會拒絕。")
            prohibited = st.text_input("禁止外觀漂移（逗號分隔）", key="starter_char_prohibited")
            act_a, act_b = st.columns(2)
            action = act_a.text_input("代表動作", key="starter_char_action")
            expression = act_b.text_input("代表表情", key="starter_char_expression")
            character_values = {
                "name": char_name,
                "gender": char_gender,
                "explicit_age": char_age,
                "user_confirmed_age": age_confirmed,
                "adult_presentation_confirmed": adult_presentation,
                "biography": biography,
                "personality": personality,
                "voice": voice,
                "motivation": motivation,
                "fear": fear,
                "secret": secret,
                "internal_conflict": internal_conflict,
                "relationship_hooks": _lines(relationship_hooks),
                "arc_start": arc_start,
                "arc_turning_points": _lines(arc_turns),
                "arc_end": arc_end,
                "identity": identity,
                "face": face,
                "hair": hair,
                "eyes": eyes,
                "body": body,
                "english_character_prompt": features,
                "prohibited_mutations": _commas(prohibited),
                "action": action,
                "expression": expression,
            }

            if use_roster and additional_new_count:
                st.markdown("#### 額外新角色")
                for index in range(1, additional_new_count + 1):
                    prefix = f"starter_p2_new_{index}"
                    with st.expander(f"新角色 {index}", expanded=True):
                        new_a, new_b, new_c = st.columns([2, 1, 2])
                        new_name = new_a.text_input("角色名稱 *", key=f"{prefix}_name")
                        new_gender = new_c.radio(
                            "角色性別",
                            options=(CharacterGender.FEMALE, CharacterGender.MALE),
                            format_func=lambda value: value.zh_label,
                            horizontal=True,
                            key=f"{prefix}_gender",
                        )
                        new_age = int(
                            new_b.number_input(
                                "明確年齡",
                                min_value=0,
                                max_value=200,
                                value=21,
                                step=1,
                                key=f"{prefix}_age",
                            )
                        )
                        new_role = new_c.text_input(
                            "故事定位", value="參與角色", key=f"{prefix}_role"
                        )
                        new_confirm_a, new_confirm_b = st.columns(2)
                        new_age_confirmed = new_confirm_a.checkbox(
                            "我確認這是此角色目前設定的明確年齡",
                            key=f"{prefix}_age_confirmed",
                        )
                        new_adult_presentation = new_confirm_b.checkbox(
                            "我確認此角色版本是成人呈現（非未成年期或孩童化設計）",
                            key=f"{prefix}_adult_presentation",
                        )
                        new_biography = st.text_area("角色背景", key=f"{prefix}_bio")
                        new_personality = st.text_area(
                            "個性、慾望與弱點", key=f"{prefix}_personality"
                        )
                        new_voice = st.text_area("語氣／聲音", key=f"{prefix}_voice")
                        new_depth_a, new_depth_b = st.columns(2)
                        new_motivation = new_depth_a.text_area(
                            "真正想要的事", key=f"{prefix}_motivation"
                        )
                        new_fear = new_depth_b.text_area(
                            "最害怕的事", key=f"{prefix}_fear"
                        )
                        new_secret = new_depth_a.text_area(
                            "秘密", key=f"{prefix}_secret"
                        )
                        new_conflict = new_depth_b.text_area(
                            "內在衝突", key=f"{prefix}_internal_conflict"
                        )
                        new_relationships = st.text_area(
                            "人際線（每行一項）", key=f"{prefix}_relationships"
                        )
                        new_arc_start = st.text_area(
                            "成長線起點", key=f"{prefix}_arc_start"
                        )
                        new_arc_turns = st.text_area(
                            "重要轉折（每行一項）", key=f"{prefix}_arc_turns"
                        )
                        new_arc_end = st.text_area(
                            "成長線終點", key=f"{prefix}_arc_end"
                        )
                        new_visual_a, new_visual_b = st.columns(2)
                        new_identity = new_visual_a.text_input("身分特徵", key=f"{prefix}_identity")
                        new_face = new_visual_b.text_input("臉部", key=f"{prefix}_face")
                        new_visual_c, new_visual_d, new_visual_e = st.columns(3)
                        new_hair = new_visual_c.text_input("髮型／髮色", key=f"{prefix}_hair")
                        new_eyes = new_visual_d.text_input("眼睛", key=f"{prefix}_eyes")
                        new_body = new_visual_e.text_input("體態", key=f"{prefix}_body")
                        additional_new_values.append(
                            (
                                new_role,
                                {
                                    "name": new_name,
                                    "gender": new_gender,
                                    "explicit_age": new_age,
                                    "user_confirmed_age": new_age_confirmed,
                                    "adult_presentation_confirmed": (new_adult_presentation),
                                    "biography": new_biography,
                                    "personality": new_personality,
                                    "voice": new_voice,
                                    "motivation": new_motivation,
                                    "fear": new_fear,
                                    "secret": new_secret,
                                    "internal_conflict": new_conflict,
                                    "relationship_hooks": _lines(new_relationships),
                                    "arc_start": new_arc_start,
                                    "arc_turning_points": _lines(new_arc_turns),
                                    "arc_end": new_arc_end,
                                    "identity": new_identity,
                                    "face": new_face,
                                    "hair": new_hair,
                                    "eyes": new_eyes,
                                    "body": new_body,
                                },
                            )
                        )

        st.markdown("#### 視覺場景（供後續圖片／影片提示）")
        scene_a, scene_b, scene_c = st.columns(3)
        scene_location = scene_a.text_input("地點", key="starter_scene_location")
        scene_time = scene_b.text_input("時間", key="starter_scene_time")
        scene_weather = scene_c.text_input("天氣", key="starter_scene_weather")
        scene_atmosphere = st.text_input("氛圍", key="starter_scene_atmosphere")
        visual_scene_a, visual_scene_b = st.columns(2)
        scene_lighting = visual_scene_a.text_input("光線", key="starter_scene_lighting")
        scene_camera = visual_scene_b.text_input("鏡頭／構圖", key="starter_scene_camera")
        scene_motion = st.text_input("鏡頭與環境動態", key="starter_scene_motion")
        st.session_state.setdefault("starter_scene_duration", 6)
        video_duration = int(
            st.number_input(
                "影片提示預計秒數",
                min_value=1,
                max_value=60,
                step=1,
                key="starter_scene_duration",
            )
        )
        video_meta_a, video_meta_b = st.columns(2)
        st.session_state.setdefault("starter_video_fps", 24)
        video_fps = int(
            video_meta_a.number_input(
                "影片 FPS",
                min_value=1,
                max_value=120,
                step=1,
                key="starter_video_fps",
            )
        )
        video_aspect = video_meta_b.selectbox(
            "影片畫面比例",
            [value.value for value in VideoAspectRatio],
            key="starter_video_aspect_ratio",
        )
        video_loop = st.checkbox("加入無縫循環約束", key="starter_video_loop")

        submitted = st.form_submit_button(
            "建立可儲存的創作藍圖",
            type="primary",
            use_container_width=True,
            key="starter_build_blueprint",
        )

    inspiration = None
    seed = st.session_state.pop("starter_inspiration_seed", None)
    if random_story:
        inspiration = CreativeInspirationService.story(seed=seed)
    elif random_character:
        inspiration = CreativeInspirationService.character(seed=seed)
    elif random_world:
        inspiration = CreativeInspirationService.world(seed=seed)
    if inspiration is not None:
        _queue_inspiration(inspiration)

    if submitted:
        try:
            character = None
            english_character_keywords: tuple[str, ...] = ()
            if character_values is not None:
                normalized_character_values = dict(character_values)
                english_prompt = str(
                    normalized_character_values.pop("english_character_prompt", "")
                )
                canonical_prompt = render_english_keywords(english_prompt)
                english_character_keywords = normalize_english_keywords(canonical_prompt)
                normalized_character_values["distinguishing_features"] = english_character_keywords
                character = CharacterBlueprint.model_validate(normalized_character_values)
                english_character_keywords = character.distinguishing_features
            participants: tuple[CreativeParticipantDraft, ...] = ()
            request_character = character
            if use_roster:
                if character is None:
                    raise ApplicationError("多人名單需要一位主要新角色")
                if not additional_new_values and not selected_existing_versions:
                    raise ApplicationError("啟用多人名單後，請至少加入一位額外參與角色")
                roster: list[CreativeParticipantDraft] = [
                    CreativeParticipantDraft(
                        slot_id="primary",
                        source=CreativeParticipantSource.NEW_BLUEPRINT,
                        role=primary_role,
                        is_primary=True,
                        blueprint=character,
                    )
                ]
                for index, (role, values) in enumerate(additional_new_values, start=1):
                    roster.append(
                        CreativeParticipantDraft(
                            slot_id=f"new-{index}",
                            source=CreativeParticipantSource.NEW_BLUEPRINT,
                            role=role,
                            blueprint=CharacterBlueprint.model_validate(values),
                        )
                    )
                roster.extend(
                    CreativeParticipantDraft(
                        slot_id=f"canon-{version_id}",
                        source=CreativeParticipantSource.EXISTING_CANON,
                        role=existing_roles[version_id],
                        character_id=existing_version_characters[version_id],
                        character_version_id=version_id,
                    )
                    for version_id in selected_existing_versions
                )
                participants = tuple(roster)
                request_character = None
            submitted_request = CreativeLaunchRequest(
                project_id=project_id,
                mode=mode,
                title=title,
                concept=concept,
                story_logline=story_logline,
                story_synopsis=story_synopsis,
                story_opening_hook=story_opening_hook,
                primary_genre=primary_genre,
                secondary_genres=tuple(secondary_genres),
                genre_tags=_commas(genre_tags),
                custom_genre=custom_genre,
                target_length=target_length,
                audience=audience,
                tone=tone,
                setting=setting,
                time_period=time_period,
                world_rules=_lines(world_rules),
                locations=_lines(world_locations),
                social_context=social_context,
                technology_or_magic=technology_or_magic,
                central_conflict=central_conflict,
                themes=_commas(themes),
                must_include=_lines(include_text),
                must_avoid=_lines(avoid_text),
                pov=pov,
                tense=tense,
                pacing=pacing,
                prose_style_notes=prose,
                dialogue_density=dialogue,
                direction=direction,
                ending_preference=ending,
                structure_profile=structure,
                violence_intensity=violence,
                horror_intensity=horror,
                intimacy_intensity=intimacy,
                content_mode=content_mode,
                character=request_character,
                participants=participants,
                english_character_keywords=english_character_keywords,
                scene_location=scene_location,
                scene_time=scene_time,
                scene_weather=scene_weather,
                scene_atmosphere=scene_atmosphere,
                scene_lighting=scene_lighting,
                scene_camera=scene_camera,
                scene_motion=scene_motion,
                video_duration_seconds=video_duration,
                video_fps=video_fps,
                video_aspect=cast(_VideoAspectValue, video_aspect),
                video_loop=video_loop,
            )
            preview = services.creative_launch.preview(submitted_request)
            st.session_state["starter_request_json"] = submitted_request.model_dump_json()
            st.session_state["starter_preview"] = preview.model_dump(mode="json")
            st.session_state.pop("starter_character_result", None)
            st.session_state.pop("starter_participant_result", None)
            st.session_state.pop("starter_story_result", None)
            st.session_state.pop("starter_world_result", None)
            st.session_state.pop("starter_story_bootstrap_result", None)
            st.session_state.pop("starter_prompt_result", None)
            st.session_state.pop("starter_video_prompt_result", None)
            st.session_state.pop("starter_video_request_fingerprint", None)
            st.session_state.pop("starter_video_prepared_export", None)
            st.success("創作藍圖已建立。請先檢查預覽，再分階段保存。")
        except ValidationError as exc:
            st.error(_validation_message(exc))
        except ValueError as exc:
            st.error(str(exc))
        except ApplicationError as exc:
            st.error(str(exc))

    request = _load_request()
    if request is None:
        return
    preview = services.creative_launch.preview(request)
    notice = st.session_state.pop("starter_assist_notice", None)
    if isinstance(notice, dict):
        if notice.get("kind") == "success":
            st.success(str(notice.get("text", "模型建議已填入空白欄位")))
        else:
            st.error(str(notice.get("text", "模型補完失敗，原藍圖完全保留")))

    st.markdown("### 4 · 檢查與保存")
    st.caption(
        f"內容檢查碼 `{preview.request_fingerprint[:16]}…` · 每個保存動作都沿用同一份已確認內容"
    )
    with st.expander("用本機模型補完空白設定", expanded=False):
        if st.session_state.pop("starter_reset_remote_provider_confirm", False):
            st.session_state.pop("starter_remote_provider_confirm", None)
        st.caption("模型只提供可編輯的企劃建議；不會設定年齡確認、成人呈現、資格結果或接受版本。")
        model = st.text_input(
            "Ollama 模型",
            value=services.settings.default_model,
            placeholder="例如 qwen2.5:7b",
            key="starter_assist_model",
        )
        endpoint = services.settings.ollama_base_url
        endpoint_info = _endpoint_summary(endpoint)
        remote_confirmed = endpoint_info.valid and endpoint_info.is_loopback
        if not endpoint_info.valid:
            st.error(
                f"模型端點設定無效：{endpoint_info.redacted_origin}。"
                "請使用 http(s) URL，且不要加入使用者資訊、查詢參數或片段。"
            )
            if endpoint_info.has_userinfo:
                st.error("端點 URL 含有使用者資訊；為避免憑證外露，請先從 URL 移除。")
        elif endpoint_info.is_loopback:
            st.caption(f"本機模型端點：{endpoint_info.redacted_origin}")
        else:
            st.warning(
                f"目前模型端點不是本機：{endpoint_info.redacted_origin}。"
                "創作藍圖會離開本機並傳送至此。"
            )
            if not endpoint_info.uses_tls:
                st.error("此遠端端點未使用 HTTPS，傳輸內容可能未加密。")
            remote_confirmed = st.checkbox(
                "僅授權這一次將創作藍圖傳送到此遠端端點",
                key="starter_remote_provider_confirm",
            )
        if st.button(
            "讓模型補完空白欄位",
            key="starter_assist_expand",
            disabled=(
                not model.strip()
                or not endpoint_info.valid
                or not remote_confirmed
                or (not endpoint_info.is_loopback and not endpoint_info.uses_tls)
            ),
            use_container_width=True,
        ):
            # Consume remote consent before any provider call.  Even an
            # unexpected exception cannot leave this authorization reusable.
            st.session_state["starter_reset_remote_provider_confirm"] = True
            try:
                result = services.creative_assist.expand(request, model=model.strip())
            except Exception:
                st.session_state["starter_assist_notice"] = {
                    "kind": "error",
                    "text": "模型補完發生未預期錯誤，原藍圖完全保留。",
                }
            else:
                if result.fallback:
                    st.session_state["starter_assist_notice"] = {
                        "kind": "error",
                        "text": (
                            f"模型補完失敗，原藍圖完全保留。技術原因：{result.error_reason.value}"
                        ),
                    }
                else:
                    st.session_state["starter_request_json"] = result.request.model_dump_json()
                    st.session_state["starter_pending_form"] = _request_form_values(result.request)
                    st.session_state["starter_assist_notice"] = {
                        "kind": "success",
                        "text": "模型建議已填入原本空白的欄位；請逐項檢查",
                    }
            st.rerun()
    preview_left, preview_right = st.columns([1.2, 1])
    with preview_left:
        st.markdown("#### 故事與角色摘要")
        st.info(preview.route_summary)
        st.text_area(
            "故事摘要",
            value=preview.story_summary,
            height=180,
            disabled=True,
            key="starter_story_preview",
        )
        if preview.character_summary:
            st.text_area(
                "角色摘要",
                value=preview.character_summary,
                height=150,
                disabled=True,
                key="starter_character_preview",
            )
    with preview_right:
        st.markdown("#### 圖片／影片提示草稿")
        st.caption(preview.prompt_bundle.status_message)
        if preview.prompt_bundle.character_prompt_en:
            st.text_area(
                "英文角色 Prompt",
                value=preview.prompt_bundle.character_prompt_en,
                disabled=True,
                key="starter_character_prompt_en_preview",
            )
        if preview.prompt_bundle.character_image_prompt:
            st.text_area(
                "角色圖片提示",
                value=preview.prompt_bundle.character_image_prompt,
                disabled=True,
                key="starter_character_image_prompt",
            )
            st.text_area(
                "場景圖片提示",
                value=preview.prompt_bundle.scene_image_prompt,
                disabled=True,
                key="starter_scene_image_prompt",
            )
            st.caption("影片 Prompt 需先保存明確的角色版本，再進行獨立資格檢查。")

    character_result = _character_result(request)
    participant_result = _participant_result(request)
    story_result = _story_result(request)
    world_result = _world_result(request)
    prompt_result = _prompt_result(request)
    saved_manifest = _saved_manifest(character_result, participant_result)
    story_bootstrap_result = _story_bootstrap_result(
        project_id=project_id,
        story_result=story_result,
        manifest=saved_manifest,
    )
    primary_character_id = ""
    primary_version_id = ""
    if participant_result is not None:
        primary_character_id = participant_result.manifest.primary.character_id
        primary_version_id = participant_result.manifest.primary.character_version_id
    elif character_result is not None:
        primary_character_id = character_result.character_id
        primary_version_id = character_result.character_version_id

    action_columns = st.columns(3)
    with action_columns[0]:
        if request.character is None and not request.participants:
            st.caption("此藍圖未包含角色。")
        elif participant_result is not None:
            st.success("參與角色與所選版本清單已保存")
        elif character_result is not None:
            st.success("角色與初始版本已保存")
        elif st.button(
            ("1 · 儲存參與角色設定" if request.participants else "1 · 儲存角色設定"),
            key="starter_save_character",
            type="primary",
            use_container_width=True,
        ):
            try:
                if request.participants:
                    saved_participants = services.creative_launch.save_participants(request)
                    st.session_state["starter_participant_result"] = saved_participants.model_dump(
                        mode="json"
                    )
                    st.toast("參與角色與已確認版本名單已保存")
                else:
                    saved_character = services.creative_launch.save_character(request)
                    st.session_state["starter_character_result"] = saved_character.model_dump(
                        mode="json"
                    )
                    st.toast("角色與外觀版本已保存")
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))
    with action_columns[1]:
        if request.mode is CreationMode.CHARACTER_ONLY:
            st.caption("僅角色路線不建立故事基礎。")
        elif request.mode is CreationMode.WORLD_ONLY:
            if world_result is not None:
                st.success("世界需求與世界聖經草稿已保存")
                st.caption(
                    f"Requirement `{world_result.requirement_version_id}` · "
                    f"Bible `{world_result.bible_version_id}` · 未建立大綱或角色"
                )
            elif st.button(
                "2 · 儲存世界觀基礎",
                key="starter_save_world",
                use_container_width=True,
            ):
                try:
                    saved_world = services.creative_launch.save_world_foundation(request)
                    st.session_state["starter_world_result"] = saved_world.model_dump(mode="json")
                    st.toast("世界觀基礎已保存；尚未建立故事主線")
                    st.rerun()
                except ApplicationError as exc:
                    st.error(str(exc))
        elif story_result is not None:
            st.success("故事需求、聖經與大綱草稿已保存")
        elif st.button(
            "2 · 儲存故事基礎",
            key="starter_save_story",
            use_container_width=True,
        ):
            try:
                if request.participants and participant_result is None:
                    raise ApplicationError("這份創作設定包含多人名單；請先儲存參與角色設定。")
                if request.character is not None and character_result is None:
                    raise ApplicationError("這份創作設定包含角色；請先儲存角色設定。")
                saved_story = services.creative_launch.save_story_foundation(
                    request,
                    participant_manifest=(
                        participant_result.manifest if participant_result else None
                    ),
                    character_id=(character_result.character_id if character_result else ""),
                    character_version_id=(
                        character_result.character_version_id if character_result else ""
                    ),
                )
                st.session_state["starter_story_result"] = saved_story.model_dump(mode="json")
                st.toast("故事基礎已保存為工作草稿；尚未自動接受")
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))
    with action_columns[2]:
        if request.character is None and not request.participants:
            st.caption("加入角色後才能保存視覺提示。")
        elif prompt_result is not None:
            st.success("圖像提示專案與版本草稿已保存")
        elif st.button(
            "3 · 儲存視覺提示",
            key="starter_save_prompt",
            use_container_width=True,
        ):
            if request.participants and participant_result is None:
                st.error("請先儲存參與角色設定，讓提示詞使用同一份已確認角色名單。")
            elif not request.participants and character_result is None:
                st.error("請先儲存角色設定，讓提示詞使用目前確認的角色版本。")
            else:
                try:
                    saved_prompt = services.creative_launch.save_visual_prompt(
                        request,
                        participant_manifest=(
                            participant_result.manifest if participant_result else None
                        ),
                        character_id=(character_result.character_id if character_result else ""),
                        character_version_id=(
                            character_result.character_version_id if character_result else ""
                        ),
                    )
                    st.session_state["starter_prompt_result"] = saved_prompt.model_dump(mode="json")
                    st.toast("提示詞草稿已保存；沒有呼叫 ComfyUI")
                    st.rerun()
                except ApplicationError as exc:
                    st.error(str(exc))

    _render_video_prompt_panel(
        services=services,
        project_id=project_id,
        request=request,
        prompt_result=prompt_result,
    )

    if participant_result is not None:
        st.markdown("#### 已保存的參與角色")
        for pin in participant_result.manifest.participants:
            marker = "主要角色" if pin.is_primary else "參與角色"
            st.caption(
                f"{marker} · {pin.role or '未設定定位'} · 角色版本已固定"
            )
        story_audits = len(story_result.eligibility_evaluation_ids) if story_result else 0
        prompt_audits = len(prompt_result.image_eligibility_evaluation_ids) if prompt_result else 0
        st.caption(
            f"角色版本清單檢查碼 `{participant_result.manifest.fingerprint[:16]}…` · "
            f"故事資格檢查 {story_audits} 項 · 圖片資格檢查 {prompt_audits} 項"
        )

    if story_result is not None:
        st.markdown("#### 建立第一章與第一場景")
        if saved_manifest is None:
            st.info(
                "故事基礎已保存；加入並保存至少一位角色後，才能建立綁定精確角色版本的第一場景。"
            )
        elif story_bootstrap_result is not None:
            replay_note = "（已讀取先前成功結果）" if story_bootstrap_result.replayed else ""
            st.success(
                "第一章、第一版章節計畫、第一場景與第一版場景卡已建立"
                f"{replay_note}；全部仍是未接受的工作版本。"
            )
            st.caption(
                f"章節 ID `{story_bootstrap_result.chapter_id}` · "
                f"場景 ID `{story_bootstrap_result.scene_id}` · "
                f"場景卡版本 ID `{story_bootstrap_result.scene_card_version_id}` · "
                f"成人資格檢查 {len(story_bootstrap_result.eligibility_evaluation_ids)} 項"
            )
        elif st.button(
            "4 · 建立第一章與第一場景",
            key="starter_bootstrap_first_scene",
            use_container_width=True,
        ):
            try:
                bootstrapped = services.creative_story_bootstrap.bootstrap(
                    CreativeStoryBootstrapRequest(
                        project_id=project_id,
                        foundation=story_result,
                        participant_manifest=saved_manifest,
                    )
                )
                st.session_state["starter_story_bootstrap_result"] = bootstrapped.model_dump(
                    mode="json"
                )
                st.toast("第一章與第一場景已建立；尚未呼叫模型")
                st.rerun()
            except ApplicationError as exc:
                st.error(str(exc))

    if character_result or participant_result or story_result or world_result or prompt_result:
        st.markdown("#### 繼續編輯")
        nav = st.columns(2)
        if primary_character_id and nav[0].button(
            "前往角色", key="starter_go_canon", use_container_width=True
        ):
            st.session_state["selected_character_id"] = primary_character_id
            _go("角色")
        if prompt_result and nav[1].button(
            "前往圖像提示工作室", key="starter_go_prompt", use_container_width=True
        ):
            video_result = _video_result(request, prompt_result)
            st.session_state["studio_pending_form"] = {
                "studio_pp_choice": prompt_result.prompt_project_id,
                "studio_version_browser": prompt_result.prompt_version_id,
                "studio_char": primary_character_id or None,
                "studio_char_ver": primary_version_id or None,
                "studio_mode": request.content_mode,
            }
            if video_result is not None and video_result.allowed and video_result.bundle_id:
                st.session_state["studio_pending_form"]["studio_video_bundle_choice"] = (
                    video_result.bundle_id
                )
            st.session_state["studio_pending_project_id"] = project_id
            _go("Prompt Studio")
        if story_result is not None:
            st.markdown("##### 寫故事")
            if story_bootstrap_result is None:
                st.caption("尚未建立章節與場景；兩個入口都會先帶你查看準備清單，不會直接呼叫模型。")
            story_nav = st.columns(2)
            if story_nav[0].button(
                "續寫場景",
                key="starter_go_story_scene",
                use_container_width=True,
            ):
                _queue_story_studio_handoff(
                    story=story_result,
                    bootstrap=story_bootstrap_result,
                    purpose=StoryGenerationPurpose.SCENE,
                )
                _go("Story Studio")
            if story_nav[1].button(
                "完整故事",
                key="starter_go_story_complete",
                use_container_width=True,
            ):
                _queue_story_studio_handoff(
                    story=story_result,
                    bootstrap=story_bootstrap_result,
                    purpose=StoryGenerationPurpose.COMPLETE_SHORT_STORY,
                )
                _go("Story Studio")

    if derives_adult(request.content_mode):
        st.warning(
            "成人性內容只在角色明確年滿 18 歲、使用者確認年齡、且所選版本為成人呈現時放行。"
            "未成年、年齡不明／有爭議、或孩童化版本一律阻擋；模型不能替你授權。"
        )


def _character_result(request: CreativeLaunchRequest) -> CharacterLaunchResult | None:
    raw = st.session_state.get("starter_character_result")
    result = CharacterLaunchResult.model_validate(raw) if isinstance(raw, dict) else None
    return result if result and result.request_fingerprint == request.fingerprint else None


def _participant_result(request: CreativeLaunchRequest) -> ParticipantLaunchResult | None:
    raw = st.session_state.get("starter_participant_result")
    result = ParticipantLaunchResult.model_validate(raw) if isinstance(raw, dict) else None
    return result if result and result.request_fingerprint == request.fingerprint else None


def _story_result(request: CreativeLaunchRequest) -> StoryFoundationResult | None:
    raw = st.session_state.get("starter_story_result")
    result = StoryFoundationResult.model_validate(raw) if isinstance(raw, dict) else None
    return result if result and result.request_fingerprint == request.fingerprint else None


def _world_result(request: CreativeLaunchRequest) -> WorldFoundationResult | None:
    raw = st.session_state.get("starter_world_result")
    result = WorldFoundationResult.model_validate(raw) if isinstance(raw, dict) else None
    return result if result and result.request_fingerprint == request.fingerprint else None


def _prompt_result(request: CreativeLaunchRequest) -> PromptLaunchResult | None:
    raw = st.session_state.get("starter_prompt_result")
    result = PromptLaunchResult.model_validate(raw) if isinstance(raw, dict) else None
    return result if result and result.request_fingerprint == request.fingerprint else None


def _video_result(
    request: CreativeLaunchRequest,
    prompt_result: PromptLaunchResult | None,
) -> VideoPromptCompileResult | None:
    raw = st.session_state.get("starter_video_prompt_result")
    bound_fingerprint = st.session_state.get("starter_video_request_fingerprint")
    if not isinstance(raw, dict) or prompt_result is None:
        return None
    try:
        result = VideoPromptCompileResult.model_validate(raw)
    except ValidationError:
        st.session_state.pop("starter_video_prompt_result", None)
        st.session_state.pop("starter_video_request_fingerprint", None)
        st.session_state.pop("starter_video_prepared_export", None)
        return None
    if (
        bound_fingerprint != request.fingerprint
        or result.prompt_project_version_id != prompt_result.prompt_version_id
    ):
        return None
    return result


def _video_prepared_export(bundle_id: str) -> VideoPromptPreparedExport | None:
    raw = st.session_state.get("starter_video_prepared_export")
    if not isinstance(raw, dict):
        return None
    try:
        prepared = VideoPromptPreparedExport.model_validate(raw)
    except ValidationError:
        st.session_state.pop("starter_video_prepared_export", None)
        return None
    return prepared if prepared.bundle_id == bundle_id else None


def _render_video_prompt_panel(
    *,
    services: Services,
    project_id: str,
    request: CreativeLaunchRequest,
    prompt_result: PromptLaunchResult | None,
) -> None:
    st.markdown("#### 影片 Prompt 套件")
    st.caption("影片與圖片 Prompt 分開保存；匯出前會再次檢查所有參與角色的內容資格。")
    if prompt_result is None:
        st.caption("先完成「3 · 儲存視覺提示」，才能綁定已保存的 Prompt 版本。")
        return
    if st.button(
        "建立影片 Prompt 套件",
        key="starter_compile_video_prompt",
        use_container_width=True,
    ):
        st.session_state.pop("starter_video_prompt_result", None)
        st.session_state.pop("starter_video_request_fingerprint", None)
        st.session_state.pop("starter_video_prepared_export", None)
        try:
            compiled_result = services.video_prompts.compile_from_version(
                prompt_version_id=prompt_result.prompt_version_id,
                expected_project_id=project_id,
                video_duration_seconds=request.video_duration_seconds,
                video_fps=request.video_fps,
                video_aspect=request.video_aspect,
                video_loop=request.video_loop,
            )
        except ApplicationError as exc:
            st.error(str(exc))
        else:
            st.session_state["starter_video_prompt_result"] = compiled_result.model_dump(
                mode="python"
            )
            st.session_state["starter_video_request_fingerprint"] = request.fingerprint
            st.rerun()

    active_result = _video_result(request, prompt_result)
    if active_result is None:
        return
    if not active_result.allowed:
        st.error("影片資格或內容檢查未通過，因此沒有建立可匯出的影片 Prompt 套件。")
        for reason in active_result.blocking_reasons:
            st.warning(reason)
        if active_result.video_eligibility_evaluation_ids:
            st.caption(
                f"本次完成 {len(active_result.video_eligibility_evaluation_ids)} "
                "項角色資格檢查。"
            )
        return
    if active_result.bundle is None or active_result.bundle_id is None:
        st.error("影片 Prompt 套件資料不完整，已停止顯示與匯出。")
        return

    replay_note = "（已讀取先前成功結果）" if active_result.replayed else ""
    st.success(f"影片 Prompt 套件已保存{replay_note}")
    st.caption(
        f"角色資格檢查：{len(active_result.video_eligibility_evaluation_ids)} 項 · "
        f"內容檢查碼 `{active_result.input_fingerprint[:12]}…` · "
        f"套件 ID `{active_result.bundle_id}`"
    )
    video_a, video_b = st.columns(2)
    with video_a.expander("角色影片 Prompt", expanded=False):
        st.code(active_result.bundle.character_asset.positive_prompt, language="text")
        st.code(
            active_result.bundle.character_asset.natural_language_prompt,
            language="text",
        )
    with video_b.expander("場景影片 Prompt", expanded=False):
        st.code(active_result.bundle.scene_asset.positive_prompt, language="text")
        st.code(
            active_result.bundle.scene_asset.natural_language_prompt,
            language="text",
        )

    if st.button(
        "再次檢查並準備匯出",
        key="starter_video_prepare_export",
    ):
        st.session_state.pop("starter_video_prepared_export", None)
        try:
            prepared_export = services.video_prompts.prepare_export(
                active_result.bundle_id,
                expected_project_id=project_id,
            )
        except ApplicationError as exc:
            st.error(str(exc))
        else:
            st.session_state["starter_video_prepared_export"] = prepared_export.model_dump(
                mode="python"
            )
            st.rerun()

    active_export = _video_prepared_export(active_result.bundle_id)
    if active_export is not None:
        st.caption(
            f"匯出前檢查已通過："
            f"{len(active_export.video_eligibility_evaluation_ids)} 項 · "
            f"SHA-256 `{active_export.bundle_sha256[:16]}…`"
        )
        export_a, export_b = st.columns(2)
        export_a.download_button(
            "下載 Video JSON",
            active_export.json_bytes,
            file_name=active_export.json_filename,
            mime="application/json",
            key="starter_video_export_json",
        )
        export_b.download_button(
            "下載 Video TXT",
            active_export.text_bytes,
            file_name=active_export.text_filename,
            mime="text/plain",
            key="starter_video_export_txt",
        )
    st.caption("只建立與匯出文字，不會連線、排隊或啟動 ComfyUI。")


def _saved_manifest(
    character_result: CharacterLaunchResult | None,
    participant_result: ParticipantLaunchResult | None,
) -> ParticipantManifest | None:
    if participant_result is not None:
        return participant_result.manifest
    if character_result is None:
        return None
    return ParticipantManifest(
        participants=(
            ParticipantPin(
                slot_id="legacy-primary",
                character_id=character_result.character_id,
                character_version_id=character_result.character_version_id,
                role="主要角色",
                is_primary=True,
            ),
        )
    )


def _story_bootstrap_result(
    *,
    project_id: str,
    story_result: StoryFoundationResult | None,
    manifest: ParticipantManifest | None,
) -> CreativeStoryBootstrapResult | None:
    raw = st.session_state.get("starter_story_bootstrap_result")
    if not isinstance(raw, dict) or story_result is None or manifest is None:
        return None
    result = CreativeStoryBootstrapResult.model_validate(raw)
    expected = CreativeStoryBootstrapRequest(
        project_id=project_id,
        foundation=story_result,
        participant_manifest=manifest,
    )
    if (
        result.idempotency_fingerprint != expected.idempotency_fingerprint
        or result.project_id != project_id
        or result.outline_id != story_result.outline_id
        or result.outline_version_id != story_result.outline_version_id
        or result.participant_manifest_fingerprint != manifest.fingerprint
    ):
        return None
    return result
