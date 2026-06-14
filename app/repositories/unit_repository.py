"""
Unit repository — async SQLAlchemy query functions for the Unit model.

Responsibilities (to be implemented):
- Fetch a single unit by ID or by property.
- List all units, with optional filtering (e.g. by vacancy status or property ID).
- Insert a new unit record.
- Update unit fields (e.g. rent amount, status).
- Delete a unit record.

All functions receive an AsyncSession and return ORM model instances or None.
No business logic lives here — validation and orchestration belong in services.
"""
