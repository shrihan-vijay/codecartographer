import datetime
import enum

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NodeType(enum.StrEnum):
    FILE = "FILE"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    CLASS = "CLASS"


class EdgeType(enum.StrEnum):
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    CONTAINS = "CONTAINS"


class IndexingRun(Base):
    __tablename__ = "indexing_runs"
    __table_args__ = (
        UniqueConstraint("repo_path", "commit_sha", name="uq_indexing_runs_repo_commit"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_path: Mapped[str] = mapped_column(Text, nullable=False)
    commit_sha: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    node_count: Mapped[int | None] = mapped_column(Integer)
    edge_count: Mapped[int | None] = mapped_column(Integer)
    unresolved_count: Mapped[int | None] = mapped_column(Integer)

    # passive_deletes=True: on delete, trust the DB's own ON DELETE CASCADE (see the
    # FKs below) instead of having the ORM SELECT and individually DELETE/null-out
    # children itself. Without it, deleting a run either 4xx's on the NOT NULL FK
    # (no cascade) or double-deletes children and logs spurious warnings (cascade
    # without passive_deletes racing the DB's own cascade).
    nodes: Mapped[list["Node"]] = relationship(
        back_populates="indexing_run", cascade="all, delete-orphan", passive_deletes=True
    )
    edges: Mapped[list["Edge"]] = relationship(
        back_populates="indexing_run", cascade="all, delete-orphan", passive_deletes=True
    )
    unresolved_calls: Mapped[list["UnresolvedCall"]] = relationship(
        back_populates="indexing_run", cascade="all, delete-orphan", passive_deletes=True
    )
    file_metrics: Mapped[list["FileMetric"]] = relationship(
        back_populates="indexing_run", cascade="all, delete-orphan", passive_deletes=True
    )


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint(
            "indexing_run_id", "qualified_name", "node_type", name="uq_nodes_run_qualname_type"
        ),
        Index("idx_nodes_file_path", "indexing_run_id", "file_path"),
        Index("idx_nodes_name", "indexing_run_id", "name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indexing_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("indexing_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_type: Mapped[NodeType] = mapped_column(SAEnum(NodeType, name="node_type"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(Text)
    docstring: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str] = mapped_column(Text, nullable=False)

    indexing_run: Mapped["IndexingRun"] = relationship(back_populates="nodes")


class Edge(Base):
    __tablename__ = "edges"
    __table_args__ = (
        UniqueConstraint(
            "indexing_run_id",
            "edge_type",
            "src_node_id",
            "dst_node_id",
            "line",
            name="uq_edges_run_type_src_dst_line",
        ),
        Index("idx_edges_src", "indexing_run_id", "edge_type", "src_node_id"),
        Index("idx_edges_dst", "indexing_run_id", "edge_type", "dst_node_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indexing_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("indexing_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    edge_type: Mapped[EdgeType] = mapped_column(SAEnum(EdgeType, name="edge_type"), nullable=False)
    src_node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dst_node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line: Mapped[int | None] = mapped_column(Integer)

    indexing_run: Mapped["IndexingRun"] = relationship(back_populates="edges")


class UnresolvedCall(Base):
    __tablename__ = "unresolved_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indexing_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("indexing_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    src_node_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False
    )
    called_name: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    indexing_run: Mapped["IndexingRun"] = relationship(back_populates="unresolved_calls")


class FileMetric(Base):
    __tablename__ = "file_metrics"
    __table_args__ = (
        UniqueConstraint("indexing_run_id", "file_path", name="uq_file_metrics_run_path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indexing_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("indexing_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    loc: Mapped[int] = mapped_column(Integer, nullable=False)
    function_count: Mapped[int] = mapped_column(Integer, nullable=False)
    git_churn: Mapped[int] = mapped_column(Integer, nullable=False)

    indexing_run: Mapped["IndexingRun"] = relationship(back_populates="file_metrics")
