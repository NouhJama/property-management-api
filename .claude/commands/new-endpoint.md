Scaffold a new resource called **$ARGUMENTS** following the layered architecture of this project (routers → services → repositories → database). This project is in the **scaffolding stage** — create only file/folder structure and placeholder content with descriptive docstrings. Do **not** write real implementation code unless the user explicitly asks for it.

## Files to create

For each file below, check whether it already exists before creating it. If it exists, skip it and note that to the user.

### 1. `app/schemas/$ARGUMENTS.py`
Placeholder Pydantic V2 schema module. Include docstrings describing the three schemas that will be needed:
- `$ARGUMENTSCreate` — fields required when creating a new record.
- `$ARGUMENTSUpdate` — optional fields for partial updates.
- `$ARGUMENTSResponse` — fields returned to the client (never expose sensitive fields like hashed passwords).

### 2. `app/models/$ARGUMENTS.py`
Placeholder SQLAlchemy ORM model module. Include a docstring describing the table, its expected columns, and any relationships to other models. Only create this file if it does not already exist.

### 3. `app/repositories/$ARGUMENTS_repository.py`
Placeholder async repository module. Include docstrings listing the query functions to be implemented:
- `get_by_id` — fetch a single record by primary key.
- `get_all` — list records with optional filters.
- `create` — insert a new record.
- `update` — update an existing record.
- `delete` — delete a record.

Remind in the docstring: no business logic here — only DB queries. All functions will accept an `AsyncSession` and return ORM instances or `None`.

### 4. `app/services/$ARGUMENTS_service.py`
Placeholder service module. Include docstrings describing the business logic functions to be implemented. The service calls the repository and never touches the database directly. Note any validation, password hashing, or orchestration logic that belongs here.

### 5. `app/routers/$ARGUMENTS.py`
Placeholder FastAPI router module. Include a docstring noting:
- All routes are prefixed with `/api/v1/$ARGUMENTS`.
- Expected CRUD endpoints: `GET /`, `GET /{id}`, `POST /`, `PUT /{id}`, `DELETE /{id}`.
- Authentication requirements (JWT where applicable).

### 6. `tests/test_$ARGUMENTS.py`
Placeholder test file. Include a docstring listing the test cases to be written (happy paths and edge cases for each endpoint/service function). Do not write real test code — only the planned test names as comments or `pass` stubs.

## After creating the files

1. **Update `app/models/__init__.py`** — add a comment noting that `$ARGUMENTS` model will be imported here once implemented.
2. **Update `app/routers/__init__.py`** — add a comment noting the `$ARGUMENTS` router will be registered here.
3. **Update `app/schemas/__init__.py`** — add a comment noting the `$ARGUMENTS` schemas will be exported here.
4. **Update `app/services/__init__.py`** — add a comment noting the `$ARGUMENTS` service will be imported here.
5. If a new naming convention or architectural decision was introduced while scaffolding this resource, **update `CLAUDE.md`** to document it.
6. **Stage all new and modified files** (`git add`) but **do not commit** — leave changes staged so the user can review the diff before committing.

## Remind the user
- These are stubs only. Implement each layer one at a time, starting with the model, then repository, then service, then router.
- Run `uv run black app/ tests/` and `uv run flake8 app/ tests/` before committing real code.
- Write tests before or alongside implementation, not after.
