"""Append-only author decision journal."""

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from imaginarium_forge.infrastructure.db.models.orm import Base


class CreativeUnitAuthorDecisionRow(Base):
    __tablename__ = "creative_unit_author_decisions"
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    job_id: Mapped[str] = mapped_column(
        Text, ForeignKey("creative_generation_jobs.id", ondelete="RESTRICT"), nullable=False,
    )
    step_id: Mapped[str] = mapped_column(
        Text, ForeignKey("creative_generation_steps.id", ondelete="RESTRICT"), nullable=False,
    )
    unit_id: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    command_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
