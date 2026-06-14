"""
Tenant repository — async SQLAlchemy query functions for the Tenant model.

Responsibilities (to be implemented):
- Fetch a single tenant by ID or email.
- List all tenants, with optional filtering (e.g. by unit or lease status).
- Insert a new tenant record.
- Update tenant fields (e.g. contact info).
- Soft-delete or hard-delete a tenant record.

All functions receive an AsyncSession and return ORM model instances or None.
No business logic lives here — validation and orchestration belong in services.
"""
