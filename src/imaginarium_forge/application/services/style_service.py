"""Style profile + outfit application services."""

from __future__ import annotations

from pydantic import ValidationError

from imaginarium_forge.application.errors import NotFoundError, ValidationFailedError
from imaginarium_forge.application.services.base import ServiceBase
from imaginarium_forge.domain.common.enums import RecordStatus
from imaginarium_forge.domain.common.ids import new_id, utc_now_iso
from imaginarium_forge.domain.outfit.model import OutfitProfile
from imaginarium_forge.domain.style.model import StyleDNA, StyleProfile, StyleProfileVersion
from imaginarium_forge.infrastructure.db.repositories.outfits import OutfitRepository
from imaginarium_forge.infrastructure.db.repositories.styles import StyleRepository


class StyleProfileService(ServiceBase):
    def create_profile(
        self, *, project_id: str, name: str, style_dna: StyleDNA | None = None
    ) -> StyleProfile:
        now = utc_now_iso()
        dna = style_dna or StyleDNA()
        try:
            dna = dna.validated()
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc
        try:
            profile = StyleProfile(
                id=new_id(), project_id=project_id, name=name, created_at=now, updated_at=now
            )
        except ValidationError as exc:
            raise ValidationFailedError(str(exc.errors()[0].get("msg", exc))) from exc
        with self._transaction() as session:
            repo = StyleRepository(session)
            repo.add(profile)
            session.flush()
            version = StyleProfileVersion(
                id=new_id(),
                style_profile_id=profile.id,
                version_number=1,
                style_dna=dna,
                change_note="initial",
                created_at=now,
            )
            repo.add_version(version)
            session.flush()
            repo.update_fields(profile.id, current_version_id=version.id, updated_at=now)
        return self.get_profile(profile.id)

    def create_version(
        self,
        *,
        profile_id: str,
        style_dna: StyleDNA,
        change_note: str = "",
        set_as_current: bool = True,
    ) -> StyleProfileVersion:
        try:
            dna = style_dna.validated()
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc
        with self._transaction() as session:
            repo = StyleRepository(session)
            if repo.get(profile_id) is None:
                raise NotFoundError(f"找不到風格設定：{profile_id}")
            version = StyleProfileVersion(
                id=new_id(),
                style_profile_id=profile_id,
                version_number=repo.next_version_number(profile_id),
                style_dna=dna,
                change_note=change_note,
                created_at=utc_now_iso(),
            )
            repo.add_version(version)
            if set_as_current:
                session.flush()
                repo.update_fields(
                    profile_id, current_version_id=version.id, updated_at=utc_now_iso()
                )
        return version

    def get_profile(self, profile_id: str) -> StyleProfile:
        profile = self._read_only(lambda s: StyleRepository(s).get(profile_id))
        if profile is None:
            raise NotFoundError(f"找不到風格設定：{profile_id}")
        return profile

    def list_profiles(
        self, project_id: str, *, include_archived: bool = True
    ) -> list[StyleProfile]:
        return self._read_only(
            lambda s: StyleRepository(s).list_for_project(
                project_id, include_archived=include_archived
            )
        )

    def list_versions(self, profile_id: str) -> list[StyleProfileVersion]:
        return self._read_only(lambda s: StyleRepository(s).list_versions(profile_id))

    def get_version(self, version_id: str) -> StyleProfileVersion:
        version = self._read_only(lambda s: StyleRepository(s).get_version(version_id))
        if version is None:
            raise NotFoundError(f"找不到風格版本：{version_id}")
        return version

    def set_current_version(self, *, profile_id: str, version_id: str) -> None:
        """Explicit current-version switch (ownership validated + DB composite FK)."""
        with self._transaction() as session:
            repo = StyleRepository(session)
            version = repo.get_version(version_id)
            if version is None or version.style_profile_id != profile_id:
                raise ValidationFailedError("指定版本不存在或不屬於此風格設定")
            if not repo.update_fields(
                profile_id, current_version_id=version_id, updated_at=utc_now_iso()
            ):
                raise NotFoundError(f"找不到風格設定：{profile_id}")

    def archive_profile(self, profile_id: str) -> StyleProfile:
        with self._transaction() as session:
            ok = StyleRepository(session).update_fields(
                profile_id, status=RecordStatus.ARCHIVED.value, updated_at=utc_now_iso()
            )
            if not ok:
                raise NotFoundError(f"找不到風格設定：{profile_id}")
        return self.get_profile(profile_id)

    def restore_profile(self, profile_id: str) -> StyleProfile:
        with self._transaction() as session:
            ok = StyleRepository(session).update_fields(
                profile_id, status=RecordStatus.ACTIVE.value, updated_at=utc_now_iso()
            )
            if not ok:
                raise NotFoundError(f"找不到風格設定：{profile_id}")
        return self.get_profile(profile_id)


class OutfitService(ServiceBase):
    def create_outfit(
        self,
        *,
        character_id: str,
        name: str,
        description: str = "",
        canonical_traits: tuple[str, ...] = (),
        optional_traits: tuple[str, ...] = (),
        prohibited_traits: tuple[str, ...] = (),
    ) -> OutfitProfile:
        now = utc_now_iso()
        try:
            outfit = OutfitProfile(
                id=new_id(),
                character_id=character_id,
                name=name,
                description=description,
                canonical_traits=canonical_traits,
                optional_traits=optional_traits,
                prohibited_traits=prohibited_traits,
                created_at=now,
                updated_at=now,
            )
        except ValidationError as exc:
            raise ValidationFailedError(str(exc.errors()[0].get("msg", exc))) from exc
        with self._transaction() as session:
            OutfitRepository(session).add(outfit)
        return outfit

    def get_outfit(self, outfit_id: str) -> OutfitProfile:
        outfit = self._read_only(lambda s: OutfitRepository(s).get(outfit_id))
        if outfit is None:
            raise NotFoundError(f"找不到服裝：{outfit_id}")
        return outfit

    def list_outfits(
        self, character_id: str, *, include_archived: bool = True
    ) -> list[OutfitProfile]:
        return self._read_only(
            lambda s: OutfitRepository(s).list_for_character(
                character_id, include_archived=include_archived
            )
        )

    def archive_outfit(self, outfit_id: str) -> OutfitProfile:
        with self._transaction() as session:
            ok = OutfitRepository(session).update_status(
                outfit_id, RecordStatus.ARCHIVED, utc_now_iso()
            )
            if not ok:
                raise NotFoundError(f"找不到服裝：{outfit_id}")
        return self.get_outfit(outfit_id)

    def restore_outfit(self, outfit_id: str) -> OutfitProfile:
        with self._transaction() as session:
            ok = OutfitRepository(session).update_status(
                outfit_id, RecordStatus.ACTIVE, utc_now_iso()
            )
            if not ok:
                raise NotFoundError(f"找不到服裝：{outfit_id}")
        return self.get_outfit(outfit_id)
