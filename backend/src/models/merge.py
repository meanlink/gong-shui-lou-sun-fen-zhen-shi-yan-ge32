from sqlalchemy import String, Double, Integer, JSON, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MergeSession(Base):
    """一次两队分头校对：共同底稿、两个副本、冲突与最终合校批次。"""

    __tablename__ = 'merge_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    base_dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    base_checksum: Mapped[str] = mapped_column(String(64))
    base_records: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='open')
    result_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_records: Mapped[list | None] = mapped_column(JSON, nullable=True)
    final_provenance: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    closed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    output_dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)


class ProofCopy(Base):
    """单个队伍的校对副本；version 用于并发确认，row_revs 记录每段来源修订。"""

    __tablename__ = 'proof_copies'
    __table_args__ = (UniqueConstraint('session_id', 'side', name='uq_proof_copy_side'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('merge_sessions.id'))
    side: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(100))
    records: Mapped[list] = mapped_column(JSON)
    row_revs: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class ProofEdit(Base):
    """逐项来源日志：哪一队、哪一段、哪个字段、旧值到新值。"""

    __tablename__ = 'proof_edits'
    __table_args__ = (Index('ix_proof_edits_copy_segment', 'copy_id', 'segment_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    copy_id: Mapped[int] = mapped_column(ForeignKey('proof_copies.id'))
    segment_id: Mapped[str] = mapped_column(String(80))
    field: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(20))
    old_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(150))
    row_rev: Mapped[int] = mapped_column(Integer, default=0)
    copy_version: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[float] = mapped_column(Double)


class MergeConflict(Base):
    """合并冲突与负责人裁决。裁决绑定两侧来源修订，来源再改则作废。"""

    __tablename__ = 'merge_conflicts'
    __table_args__ = (
        UniqueConstraint('session_id', 'segment_id', 'field', 'kind', name='uq_merge_conflict_key'),
        Index('ix_merge_conflicts_session', 'session_id', 'status'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('merge_sessions.id'))
    segment_id: Mapped[str] = mapped_column(String(80))
    field: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(30))
    base_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    left_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    right_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    left_rev: Mapped[int] = mapped_column(Integer, default=0)
    right_rev: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='open')
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    history: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    decided_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    decision_left_rev: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_right_rev: Mapped[int | None] = mapped_column(Integer, nullable=True)
    closed_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
