"""webhook payload hash index (replay detection)

Revision ID: c3d91e7a2b10
Revises: a6fefb5bf4a2
Create Date: 2026-11-17 09:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d91e7a2b10"
down_revision: str | None = "a6fefb5bf4a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_webhook_deliveries_payload_sha256_created_at"),
        "webhook_deliveries",
        ["payload_sha256", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_webhook_deliveries_payload_sha256_created_at"), table_name="webhook_deliveries"
    )
