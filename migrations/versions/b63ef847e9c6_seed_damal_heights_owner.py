"""seed damal heights owner

Revision ID: b63ef847e9c6
Revises: b26b44352c21
Create Date: 2026-07-18 16:01:43.236348

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b63ef847e9c6"
down_revision: Union[str, None] = "b26b44352c21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DATA migration, not a schema migration — no table structure changes,
    # only inserts one required row.
    #
    # - created_at must be set explicitly here (NOW()) because the Python-side
    #   lambda default on the Owner model only applies when the SQLAlchemy ORM
    #   performs the insert, not on raw SQL.
    # - This is the ONLY place in the entire system where a type=COMPANY row is
    #   ever created. The normal "create owner" API endpoint deliberately
    #   hardcodes type=individual in the service layer and can never produce
    #   this row — this migration is the sole, one-time source of it.
    # - Every unsold Unit's owner_id will point to this row's id.
    # - The enum literal is 'COMPANY' (uppercase): SQLEnum(OwnerType) stores
    #   Python enum member NAMES as the native PostgreSQL enum labels, not the
    #   lowercase values (see the ownertype enum in revision b26b44352c21).
    op.execute(
        """
        INSERT INTO owners (name, type, phone, email, national_id, created_at)
        VALUES ('Damal Heights', 'COMPANY', NULL, NULL, NULL, NOW())
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM owners WHERE name = 'Damal Heights' AND type = 'COMPANY'
        """
    )
