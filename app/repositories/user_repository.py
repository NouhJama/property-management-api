"""
User repository — async SQLAlchemy query functions for the User model.

Responsibilities (to be implemented):
- Fetch a single user by ID or username/email (used by auth flows).
- List all users, with optional role-based filtering.
- Insert a new user record (stores hashed password, never plaintext).
- Update user fields (e.g. email, hashed password).
- Delete a user record.

All functions receive an AsyncSession and return ORM model instances or None.
No business logic or password hashing lives here — those belong in services/core.
"""
