"""
Repository layer for the property management API.

Repositories are the only layer that interacts directly with the database via
async SQLAlchemy sessions. They contain no business logic — only query functions
(SELECT, INSERT, UPDATE, DELETE). Services call repositories to fetch or persist
data; repositories never call services.

Architecture flow:
    routers → services → repositories → database (SQLAlchemy / asyncpg)
"""
