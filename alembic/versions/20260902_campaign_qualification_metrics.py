"""Add qualification outcome columns to the campaign daily rollup.

The sales and callback outcome vocabularies have named ``qualified``,
``not_qualified``, ``unreachable`` and ``transferred`` since they were written,
but no rollup column produced them, so a campaign that reached one reported a
zero rather than an absent figure.

Revision ID: 20260902_campaign_qual_metrics
Revises: 20260902_email_identity_activation
"""

from alembic import op
import sqlalchemy as sa


revision = "20260902_campaign_qual_metrics"
down_revision = "20260902_email_identity_activation"
branch_labels = None
depends_on = None


_COLUMNS = ("qualified", "not_qualified", "unreachable", "transferred")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column(
            "campaign_metrics_daily",
            sa.Column(
                column,
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            # The consolidated baseline's create_all already builds these from
            # the model on a fresh database.
            if_not_exists=True,
        )


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("campaign_metrics_daily", column)
