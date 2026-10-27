from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from engine import Base


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))

    repo_url: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)


class Dependency(Base):
    __tablename__ = "dependencies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))

    current_version: Mapped[str] = mapped_column(String(255))
    version_updated_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)

    lib_url: Mapped[str] = mapped_column(String(255))

    created_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[DateTime] = mapped_column(DateTime, nullable=True)