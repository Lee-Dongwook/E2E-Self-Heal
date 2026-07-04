# AI Agent Development Guide

This document provides essential guidelines for AI agents working on this LangGraph FastAPI Agent project.

## Quick Commands

```bash
make install              # Install deps (uv sync) + pre-commit hooks
make dev                  # Dev server with hot reload (port 8000)
make lint                 # ruff check .
make format               # ruff format .
make typecheck            # uv run pyright (static type check)
make check                # lint + typecheck
make eval                 # Run LLM evals (interactive)
make eval-quick           # Run LLM evals (default settings)
make migrate              # Run DB migrations to latest (Alembic)
make docker-up            # Docker: API + DB (ENV=development by default)
make stack-up ENV=development  # Full stack: API + DB + Prometheus + Grafana
```

> All server/DB/Docker targets accept `ENV=development|staging|production|test`.
> Run `make help` for the full list of targets.

## Project Structure

```
app/
  api/v1/          # Route handlers (auth.py, chatbot.py, api.py)
  core/
    config.py      # Pydantic Settings config
    database.py    # Async DB setup
    langgraph/     # LangGraph agent graph + tools
    logging.py     # structlog setup
    llm.py         # LLM service with retry logic
    limiter.py     # Rate limiting (slowapi)
    metrics.py     # Prometheus metrics
    middleware.py  # ASGI middleware
    prompts/       # System prompts
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas + graph state
  services/        # Business logic services
  utils/           # Shared utilities
evals/             # LLM evaluation framework (Langfuse-based)
scripts/           # Environment setup, Docker build scripts
```

## Project Overview

This is a production-ready AI agent application built with:

- **LangGraph** for stateful, multi-step AI agent workflows
- **FastAPI** for high-performance async REST API endpoints

## Quick Reference: Critical Rules

### Import Rules

- **All imports MUST be at the top of the file** - never add imports inside functions or classes

### Logging Rules

- Use **structlog** for all logging
- Log messages must be **lowercase_with_underscores** (e.g., `"user_login_successful"`)
- **NO f-strings in structlog events** - pass variables as kwargs
- Use `logger.exception()` instead of `logger.error()` to preserve tracebacks
- Example: `logger.info("chat_request_received", session_id=session.id, message_count=len(messages))`

### Retry Rules

- **Always use tenacity library** for retry logic
- Configure with exponential backoff
- Example: `@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))`

### Output Rules

- **Always enable rich library** for formatted console outputs
- Use rich for progress bars, tables, panels, and formatted text

### Caching Rules

- **Only cache successful responses**, never cache errors
- Use appropriate cache TTL based on data volatility

### FastAPI Rules

- All routes must have rate limiting decorators
- Use dependency injection for services, database connections, and auth
- All database operations must be async

## Code Style Conventions

### Python/FastAPI

- Use `async def` for asynchronous operations
- Use type hints for all function signatures
- Prefer Pydantic models over raw dictionaries
- Use functional, declarative programming; avoid classes except for services and agents
- File naming: lowercase with underscores (e.g., `user_routes.py`)
- Use the RORO pattern (Receive an Object, Return an Object)

### Error Handling

- Handle errors at the beginning of functions
- Use early returns for error conditions
- Place the happy path last in the function
- Use guard clauses for preconditions
- Use `HTTPException` for expected errors with appropriate status codes

## LangGraph & LangChain Patterns

### Graph Structure

- Use `StateGraph` for building AI agent workflows
- Define clear state schemas using Pydantic models (see `app/schemas/graph.py`)
- Use `CompiledStateGraph` for production workflows
- Implement `AsyncPostgresSaver` for checkpointing and persistence
- Use `Command` for controlling graph flow between nodes

### Tracing

- Use LangChain's `CallbackHandler` from Langfuse for tracing all LLM calls
- All LLM operations must have Langfuse tracing enabled

## Database Operations

- Use SQLModel for ORM models (combines SQLAlchemy + Pydantic)
- Define models in `app/models/` directory
- Use async database operations with asyncpg
- Use LangGraph's AsyncPostgresSaver for agent checkpointing

## Performance Guidelines

- Minimize blocking I/O operations
- Use async for all database and external API calls
- Implement caching for frequently accessed data
- Use connection pooling for database connections
- Optimize LLM calls with streaming responses

## Observability

- Integrate Langfuse for LLM tracing on all agent operations
- Export Prometheus metrics for API performance
- Use structured logging with context binding (request_id, session_id, user_id)
- Track LLM inference duration, token usage, and costs

## Testing & Evaluation

- Implement metric-based evaluations for LLM outputs (see `evals/` directory)
- Create custom evaluation metrics as markdown files in `evals/metrics/prompts/`
- Use Langfuse traces for evaluation data sources
- Generate JSON reports with success rates

## Configuration Management

- Use environment-specific configuration files (`.env.development`, `.env.staging`, `.env.production`)
- Use Pydantic Settings for type-safe configuration (see `app/core/config.py`)
- Never hardcode secrets or API keys

## Key Dependencies

- **FastAPI** - Web framework
- **LangGraph** - Agent workflow orchestration
- **LangChain** - LLM abstraction and tools
- **Pydantic v2** - Data validation and settings

## 10 Commandments for This Project

1. All routes must have rate limiting decorators
2. All async operations must have proper error handling
3. All logs must follow structured logging format with lowercase_underscore event names
4. All retries must use tenacity library
5. All console outputs should use rich formatting
6. All caching should only store successful responses
7. All imports must be at the top of files
8. All database operations must be async
9. All endpoints must have proper type hints and Pydantic models
10. All code must pass `make typecheck` (pyright standard mode)

## Common Pitfalls to Avoid

- ❌ Using f-strings in structlog events
- ❌ Adding imports inside functions
- ❌ Forgetting rate limiting decorators on routes
- ❌ Missing Langfuse tracing on LLM calls
- ❌ Caching error responses
- ❌ Using `logger.error()` instead of `logger.exception()` for exceptions
- ❌ Blocking I/O operations without async
- ❌ Hardcoding secrets or API keys
- ❌ Missing type hints on function signatures

## When Making Changes

Before modifying code:

1. Read the existing implementation first
2. Check for related patterns in the codebase
3. Ensure consistency with existing code style
4. Add appropriate logging with structured format
5. Include error handling with early returns
6. Add type hints and Pydantic models

## References

- LangGraph Documentation: https://langchain-ai.github.io/langgraph/
- LangChain Documentation: https://python.langchain.com/docs/
- FastAPI Documentation: https://fastapi.tiangolo.com/
