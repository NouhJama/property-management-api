# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A property management API that manages properties, units, tenants, and employees, and tracks rent and renovations. Currently in early scaffolding stage — no application code beyond the entry point exists yet.

## Toolchain

- This project uses **uv** for Python package management (Python 3.13).
- Database management is handled with **SQLAlchemy** and **Alembic** for migrations. The API will be built using **FastAPI**(Always use the official documentation).
- Database: **PostgreSQL**, driver: **asyncpg**.
- Validation: **Pydantic V2 models** for data validation and settings management.

## Authentication and security
- Authentication: **JWT**(OAuth2 password flow).
- Password hashing: **bcrypt** for secure password storage.
- CORS: Configure CORS middleware to allow requests from trusted origins.

```bash
# Install dependencies
uv sync

# Run the app
uv run python main.py

# Add a dependency
uv add <package>

# Run a script/tool in the venv
uv run <command>
```

## Testing, Linting, and Formatting
- Testing: **pytest** for unit and integration tests.
- Linting: **flake8** for code style and quality checks.
- Formatting: **black** for consistent code formatting.

## Code/Project Organization
- `main.py`: Entry point for the application.
- `models/`: SQLAlchemy models for database tables.
- `schemas/`: Pydantic models for request and response validation.
- `routers/`: FastAPI routers for different API endpoints.
- `services/`: Business logic and interactions with the database.
- `core/`: Core utilities, such as database session management and common functions.
- `migrations/`: Alembic migration scripts for database schema changes.
- `config/`: Configuration files and settings management.
- `tests/`: Unit and integration tests for the application.

## Development Workflow
1. **Branching**: Use feature branches for new features and bug fixes. Merge into `main` only after code review and testing.
2. **Code Reviews**: As I am now a solo developer, I will perform code reviews myself, so I don't need to worry about getting reviews from others.
3. **Testing**: Write tests for new features and bug fixes. Run all tests before pushing changes.
4. **Documentation**: Update documentation as needed when adding new features or changing existing functionality.

## Workflow - before any code is written
1. Understand the feature fully, including the requirements and any edge cases, ask clear questions if anything is unclear.
2. Plan the implementation, including the necessary database models, API endpoints, and any business logic.
3. Write the code following the code standards outlined below.
4. Understand which layers of the application the code belongs to (models, schemas, routers, services, core, etc.) and place it accordingly.
5. Write tests for the new code, ensuring good coverage and testing edge cases.
6. Run all tests to ensure everything is working correctly.
7. Update documentation if necessary, including docstrings and any relevant README sections.

## Code standards
- Follow PEP 8 for Python code style, max line of 100 characters.
- Use descriptive variable and function names.
- Write docstrings for all functions and classes.
- Use type hints for function signatures and variable annotations.
- Avoid global state and side effects where possible.
- Handle exceptions gracefully and provide meaningful error messages.
- Keep functions and methods small and focused on a single responsibility.
- All routes use 'api/v1' prefix, e.g., `api/v1/properties`, `api/v1/units`, etc.
