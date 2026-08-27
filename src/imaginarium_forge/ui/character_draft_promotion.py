"""Pure handoff from a loose character draft to the Creative Launchpad.

Nothing in this module creates a Project or writes Canon, Story, Prompt, or
draft rows.  A caller must invoke it only after the author explicitly chooses
``搬上書架`` and supplies the already selected/created project id.

When a new Project is requested, call :func:`validate_character_draft_promotion`
*before* creating it.  This prevents an invalid draft from leaving an empty
book behind.  After the explicit Project creation succeeds, build the real
handoff with :func:`prepare_character_draft_launchpad_handoff`.
"""

from __future__ import annotations

from dataclasses import dataclass

from imaginarium_forge.application.services.creative_inspiration_service import (
    normalize_english_keywords,
    render_english_keywords,
)
from imaginarium_forge.domain.character.biography_draft import CharacterBiographyDraft
from imaginarium_forge.domain.character.gender import apply_gender_prompt_token
from imaginarium_forge.domain.creative.models import (
    CharacterBlueprint,
    CreationMode,
    CreativeLaunchRequest,
)

LAUNCHPAD_PAGE_KEY = "Creative Launchpad"
_VALIDATION_PROJECT_ID = "character-draft-promotion-preflight"


@dataclass(frozen=True, slots=True)
class CharacterDraftLaunchpadHandoff:
    """Side-effect-free instructions for one explicit Launchpad navigation."""

    request: CreativeLaunchRequest
    pending_form: dict[str, object]

    @property
    def session_updates(self) -> dict[str, object]:
        """Return state to apply *after* clearing keys with ``starter_`` prefix."""

        project_id = self.request.project_id
        return {
            "selected_project_id": project_id,
            # Launchpad clears starter state when this differs from the selected
            # project.  Binding both values atomically preserves this handoff.
            "starter_project_id": project_id,
            "starter_request_json": self.request.model_dump_json(),
            "starter_pending_form": dict(self.pending_form),
            "starter_automation_handoff_notice": {
                "kind": "success",
                "text": (
                    "角色草稿已攤在精修桌上；逐欄讀過後，再決定是否保存正式角色或故事。"
                ),
            },
            "pending_nav": LAUNCHPAD_PAGE_KEY,
        }

    @property
    def session_prefixes_to_clear(self) -> tuple[str, ...]:
        """Prefixes whose stale widgets/results must be removed before updates."""

        return ("starter_",)


def _character_prompt_keywords(draft: CharacterBiographyDraft) -> tuple[str, ...]:
    authored = normalize_english_keywords(draft.character_image_prompt_en)
    # The structured gender is authoritative.  This removes either standard
    # gender marker and adds exactly the selected one at the front.
    return apply_gender_prompt_token(authored, draft.gender)


def build_character_draft_launch_request(
    draft: CharacterBiographyDraft,
    *,
    project_id: str,
) -> CreativeLaunchRequest:
    """Convert one validated draft into a project-bound immutable request.

    The mapping preserves the loose draft's two prose blocks and two image
    prompts without inventing a larger screenplay:

    - character details -> character biography
    - personal story -> story synopsis
    - character prompt -> canonical character keywords
    - background prompt -> scene atmosphere
    - page notes -> editable core concept
    """

    keywords = _character_prompt_keywords(draft)
    character = CharacterBlueprint(
        name=draft.character_name,
        gender=draft.gender,
        biography=draft.character_details,
        # CharacterBlueprint owns structured gender and therefore strips the
        # standard marker from Visual DNA.  The complete prompt remains on the
        # request for the Launchpad's editable comma-prompt field.
        distinguishing_features=keywords,
    )
    return CreativeLaunchRequest(
        project_id=project_id,
        mode=CreationMode.CHARACTER_STORY,
        title=draft.title,
        concept=draft.notes,
        story_synopsis=draft.personal_story,
        character=character,
        english_character_keywords=keywords,
        scene_atmosphere=draft.background_image_prompt_en,
    )


def validate_character_draft_promotion(draft: CharacterBiographyDraft) -> None:
    """Preflight the full Launchpad contract without creating or changing data."""

    build_character_draft_launch_request(draft, project_id=_VALIDATION_PROJECT_ID)


def _launchpad_form_values(request: CreativeLaunchRequest) -> dict[str, object]:
    """Render all singular-character widgets from the canonical request."""

    character = request.character
    if character is None:  # This bridge always builds CHARACTER_STORY.
        raise ValueError("角色草稿交接缺少角色設定")
    return {
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
        "starter_include_character": True,
        "starter_p2_enabled": False,
        "starter_char_name": character.name,
        "starter_char_gender": character.gender,
        "starter_char_age": character.explicit_age or 0,
        "starter_char_age_confirmed": character.user_confirmed_age,
        "starter_char_adult_presentation": character.adult_presentation_confirmed,
        "starter_char_bio": character.biography,
        "starter_char_personality": character.personality,
        "starter_char_voice": character.voice,
        "starter_char_motivation": character.motivation,
        "starter_char_fear": character.fear,
        "starter_char_secret": character.secret,
        "starter_char_internal_conflict": character.internal_conflict,
        "starter_char_relationships": "\n".join(character.relationship_hooks),
        "starter_char_arc_start": character.arc_start,
        "starter_char_arc_turns": "\n".join(character.arc_turning_points),
        "starter_char_arc_end": character.arc_end,
        "starter_char_identity": character.identity,
        "starter_char_face": character.face,
        "starter_char_hair": character.hair,
        "starter_char_eyes": character.eyes,
        "starter_char_body": character.body,
        "starter_char_features": render_english_keywords(
            request.english_character_keywords
        ),
        "starter_char_prohibited": ", ".join(character.prohibited_mutations),
        "starter_char_action": character.action,
        "starter_char_expression": character.expression,
    }


def prepare_character_draft_launchpad_handoff(
    draft: CharacterBiographyDraft,
    *,
    project_id: str,
) -> CharacterDraftLaunchpadHandoff:
    """Prepare navigation state after the author explicitly chooses a Project."""

    request = build_character_draft_launch_request(draft, project_id=project_id)
    return CharacterDraftLaunchpadHandoff(
        request=request,
        pending_form=_launchpad_form_values(request),
    )


__all__ = [
    "LAUNCHPAD_PAGE_KEY",
    "CharacterDraftLaunchpadHandoff",
    "build_character_draft_launch_request",
    "prepare_character_draft_launchpad_handoff",
    "validate_character_draft_promotion",
]
