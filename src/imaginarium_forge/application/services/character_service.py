"""Character and character-version application services.

CharacterService creates identity records (original vs existing, with the
derived-classification rule for originals). CharacterVersionService appends
immutable versions and switches the current version through an EXPLICIT command
— editing never mutates an accepted version.
"""

from __future__ import annotations

from pydantic import ValidationError

from imaginarium_forge.application.errors import (
    ConflictError,
    NotFoundError,
    ValidationFailedError,
)
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.character.model import (
    AgeStatus,
    Character,
    CharacterProfile,
    OriginalityReview,
    SourceMetadata,
    derive_original_age_classification,
)
from imaginarium_forge.domain.character.version import (
    AdultPresentation,
    CharacterVersion,
    VisualDNA,
)
from imaginarium_forge.domain.common.enums import CharacterOrigin, RecordStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.infrastructure.db.repositories.characters import CharacterRepository


class CharacterService(ServiceBase):
    def create_original_character(
        self,
        *,
        project_id: str,
        name: str,
        explicit_age: int | None = None,
        user_confirmed_age: bool = False,
        profile: CharacterProfile | None = None,
        originality_review: OriginalityReview | None = None,
    ) -> Character:
        """Create an ORIGINAL character. Classification is DERIVED from the age."""
        now = utc_now_iso()
        age_status = AgeStatus(
            classification=derive_original_age_classification(explicit_age),
            explicit_age=explicit_age,
            user_confirmed=user_confirmed_age,
        )
        try:
            character = Character(
                id=new_id(),
                project_id=project_id,
                name=name,
                character_origin=CharacterOrigin.ORIGINAL,
                age_status=age_status,
                originality_review=originality_review,
                profile=profile or CharacterProfile(),
                created_at=now,
                updated_at=now,
            )
        except ValidationError as exc:
            raise ValidationFailedError(str(exc.errors()[0].get("msg", exc))) from exc
        with self._transaction() as session:
            CharacterRepository(session).add(character)
        return character

    def create_existing_character(
        self,
        *,
        project_id: str,
        name: str,
        age_status: AgeStatus,
        source_metadata: SourceMetadata,
        profile: CharacterProfile | None = None,
    ) -> Character:
        """Create an EXISTING character. Source age status is explicit/auditable."""
        now = utc_now_iso()
        try:
            character = Character(
                id=new_id(),
                project_id=project_id,
                name=name,
                character_origin=CharacterOrigin.EXISTING,
                age_status=age_status,
                source_metadata=source_metadata,
                profile=profile or CharacterProfile(),
                created_at=now,
                updated_at=now,
            )
        except ValidationError as exc:
            raise ValidationFailedError(str(exc.errors()[0].get("msg", exc))) from exc
        with self._transaction() as session:
            CharacterRepository(session).add(character)
        return character

    def get_character(self, character_id: str) -> Character:
        character = self._read_only(lambda s: CharacterRepository(s).get(character_id))
        if character is None:
            raise NotFoundError(f"找不到角色：{character_id}")
        return character

    def list_characters(
        self, project_id: str, *, include_archived: bool = True
    ) -> list[Character]:
        return self._read_only(
            lambda s: CharacterRepository(s).list_for_project(
                project_id, include_archived=include_archived
            )
        )

    def _set_status(self, character_id: str, status: RecordStatus) -> Character:
        with self._transaction() as session:
            ok = CharacterRepository(session).update_identity_fields(
                character_id, status=status.value, updated_at=utc_now_iso()
            )
            if not ok:
                raise NotFoundError(f"找不到角色：{character_id}")
        return self.get_character(character_id)

    def archive_character(self, character_id: str) -> Character:
        return self._set_status(character_id, RecordStatus.ARCHIVED)

    def restore_character(self, character_id: str) -> Character:
        return self._set_status(character_id, RecordStatus.ACTIVE)

    def update_identity(
        self, character_id: str, *, name: str | None = None
    ) -> Character:
        """Edit identity-level fields (A-05 §9.1). Name required if provided."""
        if name is not None and not name.strip():
            raise ValidationFailedError("角色名稱為必填")
        with self._transaction() as session:
            fields: dict[str, str] = {"updated_at": utc_now_iso()}
            if name is not None:
                fields["name"] = name.strip()
            if not CharacterRepository(session).update_identity_fields(character_id, **fields):
                raise NotFoundError(f"找不到角色：{character_id}")
        return self.get_character(character_id)

    def update_profile(
        self,
        character_id: str,
        *,
        biography: str | None = None,
        personality_notes: str | None = None,
        voice_notes: str | None = None,
        motivation: str | None = None,
        fear: str | None = None,
        secret: str | None = None,
        internal_conflict: str | None = None,
        relationship_hooks: tuple[str, ...] | None = None,
        arc_start: str | None = None,
        arc_turning_points: tuple[str, ...] | None = None,
        arc_end: str | None = None,
        freeform_notes: str | None = None,
    ) -> Character:
        """Edit the character profile (A-05 §9.1: biography/personality/voice/notes)."""
        current = self.get_character(character_id)
        updated = current.profile.model_copy(
            update={
                k: v
                for k, v in {
                    "biography": biography,
                    "personality_notes": personality_notes,
                    "voice_notes": voice_notes,
                    "motivation": motivation,
                    "fear": fear,
                    "secret": secret,
                    "internal_conflict": internal_conflict,
                    "relationship_hooks": relationship_hooks,
                    "arc_start": arc_start,
                    "arc_turning_points": arc_turning_points,
                    "arc_end": arc_end,
                    "freeform_notes": freeform_notes,
                }.items()
                if v is not None
            }
        )
        with self._transaction() as session:
            ok = CharacterRepository(session).update_identity_fields(
                character_id,
                profile_json=updated.model_dump_json(),
                updated_at=utc_now_iso(),
            )
            if not ok:
                raise NotFoundError(f"找不到角色：{character_id}")
        return self.get_character(character_id)

    def update_age_status(self, character_id: str, *, age_status: AgeStatus) -> Character:
        """Replace the age status (A-02 layer 2 + A-05).

        The provided AgeStatus already passed the model contradiction guard;
        this is the persistence boundary. Callers reset user_confirmed when the
        age changes (the UI enforces this; see the character page).
        """
        with self._transaction() as session:
            ok = CharacterRepository(session).update_identity_fields(
                character_id,
                age_status_json=age_status.model_dump_json(),
                updated_at=utc_now_iso(),
            )
            if not ok:
                raise NotFoundError(f"找不到角色：{character_id}")
        return self.get_character(character_id)

    def update_source_metadata(
        self, character_id: str, *, source_metadata: SourceMetadata
    ) -> Character:
        """Replace existing-character source metadata (A-06). Requires core fields."""
        character = self.get_character(character_id)
        if character.character_origin is not CharacterOrigin.EXISTING:
            raise ValidationFailedError("只有既有角色可設定來源資訊")
        if not (
            source_metadata.source_title.strip()
            and source_metadata.source_character_name.strip()
        ):
            raise ValidationFailedError("來源作品名稱與來源角色名稱皆為必填")
        with self._transaction() as session:
            ok = CharacterRepository(session).update_identity_fields(
                character_id,
                source_metadata_json=source_metadata.model_dump_json(),
                updated_at=utc_now_iso(),
            )
            if not ok:
                raise NotFoundError(f"找不到角色：{character_id}")
        return self.get_character(character_id)


class CharacterVersionService(ServiceBase):
    def create_version(
        self,
        *,
        character_id: str,
        visual_dna: VisualDNA | None = None,
        adult_presentation: AdultPresentation | None = None,
        voice_profile: str = "",
        personality_profile: str = "",
        change_note: str = "",
        set_as_current: bool = True,
    ) -> CharacterVersion:
        """Append a NEW immutable version. Optionally switch current to it."""
        dna = (visual_dna or VisualDNA())
        try:
            dna = dna.validated_for_base_version()
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc

        with self._transaction() as session:
            repo = CharacterRepository(session)
            if repo.get(character_id) is None:
                raise NotFoundError(f"找不到角色：{character_id}")
            version = CharacterVersion(
                id=new_id(),
                character_id=character_id,
                version_number=repo.next_version_number(character_id),
                visual_dna=dna,
                adult_presentation=adult_presentation or AdultPresentation(),
                voice_profile=voice_profile,
                personality_profile=personality_profile,
                change_note=change_note,
                created_at=utc_now_iso(),
            )
            repo.add_version(version)
            if set_as_current:
                # flush so the new version row exists before the ownership FK check
                session.flush()
                repo.update_identity_fields(
                    character_id,
                    current_version_id=version.id,
                    updated_at=utc_now_iso(),
                )
        return version

    def set_current_version(self, *, character_id: str, version_id: str) -> None:
        """Explicit current-version switch (validates ownership via the DB FK)."""
        with self._transaction() as session:
            repo = CharacterRepository(session)
            version = repo.get_version(version_id)
            if version is None or version.character_id != character_id:
                raise ValidationFailedError("指定版本不存在或不屬於此角色")
            try:
                ok = repo.update_identity_fields(
                    character_id, current_version_id=version_id, updated_at=utc_now_iso()
                )
            except ConflictError:
                raise
            if not ok:
                raise NotFoundError(f"找不到角色：{character_id}")

    def list_versions(self, character_id: str) -> list[CharacterVersion]:
        return self._read_only(lambda s: CharacterRepository(s).list_versions(character_id))

    def get_version(self, version_id: str) -> CharacterVersion:
        version = self._read_only(lambda s: CharacterRepository(s).get_version(version_id))
        if version is None:
            raise NotFoundError(f"找不到角色版本：{version_id}")
        return version
