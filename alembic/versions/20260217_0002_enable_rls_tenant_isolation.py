"""enable RLS and add tenant isolation policies

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-17

Enables Row Level Security (RLS) on all tenant-scoped tables and the
audit_logs table. Adds policies so that:

- The service_role (used by our backend via Supabase) bypasses RLS.
- Authenticated Supabase users can only see rows matching their tenant.
- audit_logs rows are tenant-scoped for reads.

NOTE: Our backend uses the service_role key which bypasses RLS entirely.
These policies provide defense-in-depth — they protect against:
  1. Direct Supabase client SDK access (if ever exposed)
  2. Supabase Dashboard RLS enforcement
  3. Any future client-side query path
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables that have a tenant_id column and need RLS
TENANT_SCOPED_TABLES = [
    "users",
    "tenant_locations",
    "tenant_providers",
    "tenant_appointment_types",
    "tenant_availabilities",
    "tenant_descriptors",
    "tenant_operatories",
    "audit_logs",
]


def upgrade() -> None:
    # =========================================================================
    # 1. Enable RLS on the tenants table (self-scoping)
    # =========================================================================
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;")

    # Tenants can only see their own row
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON tenants
            FOR ALL
            USING (
                id = (current_setting('request.jwt.claims', TRUE)::json ->> 'tenant_id')::uuid
            );
    """)

    # =========================================================================
    # 2. Enable RLS on all tenant_id-scoped tables
    # =========================================================================
    for table in TENANT_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")

        op.execute(f"""
            CREATE POLICY tenant_isolation_policy ON {table}
                FOR ALL
                USING (
                    tenant_id = (current_setting('request.jwt.claims', TRUE)::json ->> 'tenant_id')::uuid
                );
        """)

    # =========================================================================
    # 3. Grant service_role full access (bypasses RLS by default in Supabase,
    #    but explicit grant ensures clarity)
    # =========================================================================
    all_tables = ["tenants"] + TENANT_SCOPED_TABLES
    for table in all_tables:
        op.execute(f"GRANT ALL ON {table} TO service_role;")


def downgrade() -> None:
    # Drop all policies and disable RLS
    all_tables = ["tenants"] + TENANT_SCOPED_TABLES

    for table in all_tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
