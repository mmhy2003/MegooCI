"""Key-value store for runtime system settings.

Allows admins to override env-var defaults (e.g. AI provider, base URL)
without restarting the backend.  Settings written here take precedence
over the corresponding ``MEGOOCI_*`` environment variables.
"""

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
