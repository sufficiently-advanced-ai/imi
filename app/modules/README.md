# imi Modular Architecture

This directory contains the modular architecture implementation for imi, organized into bounded contexts following Domain-Driven Design principles.

## Module Structure

```
app/modules/
├── __init__.py           # Module registration system
├── core/                 # Core system functionality
├── entities/             # Entity management
├── analysis/             # AI analysis and agents
├── ingestion/            # Ingestion pipeline and webhooks
└── knowledge/            # Knowledge graph and search
```

## Bounded Contexts

### Core (`/core`)
**Purpose**: System-level functionality and infrastructure
- Authentication and authorization
- User management (when database available)
- File operations (upload, diff, digest)
- Admin interfaces
- System metrics and monitoring
- Test endpoints
- Display configuration

**Key Routes**: `/api/auth/*`, `/api/admin/*`, `/api/upload`, `/api/diff`, `/metrics`

### Entities (`/entities`)
**Purpose**: Entity lifecycle management
- Entity CRUD operations
- Search and discovery
- Enrichment and validation
- Bulk operations
- Migration and integration

**Key Routes**: `/api/entities/*`, `/api/entity-*`

### Analysis (`/analysis`)
**Purpose**: AI-powered analysis capabilities
- Agent tools and operations
- Memory management
- Insights generation
- Analysis services

**Key Routes**: `/api/agent-tools/*`, `/api/analysis/*`, `/api/insights/*`

### Ingestion (`/ingestion`)
**Purpose**: Ingestion pipeline and webhooks
- GitHub webhook processing
- Transcript/document ingestion

**Key Routes**: `/api/webhook/github`, `/api/ingest/*`

### Knowledge (`/knowledge`)
**Purpose**: Knowledge management and retrieval
- Domain graph visualization
- Domain configuration management
- Knowledge search and retrieval
- Command processing and chat
- Domain-aware analysis

**Key Routes**: `/api/domain/*`, `/api/graph/*`, `/api/command`, `/api/chat/*`

## Module Registration

All modules are automatically registered through the `register_modules()` function in `__init__.py`:

```python
from app.modules import register_modules

app = FastAPI()
register_modules(app)
```

This replaces the previous pattern of individually importing and registering 58+ route files.

## Benefits

1. **Clear Boundaries**: Each module has well-defined responsibilities
2. **Reduced Coupling**: Modules interact through well-defined interfaces
3. **Easier Testing**: Modules can be tested in isolation
4. **Better Organization**: Related functionality is co-located
5. **Scalability**: New features can be added as new modules
6. **Maintainability**: Easier to understand and modify code

## Migration from Legacy

The legacy approach imported all route files directly in `main.py`:

```python
# Old approach (58+ imports)
from .routes import (
    webhook, digest, upload, diff, command,
    folders, admin, agent_tools, workflows, objectives,
    # ... 50+ more imports
)
```

The new modular approach uses bounded contexts:

```python
# New approach (1 import, 6 modules)
from .modules import register_modules
register_modules(app)
```

## Error Handling

The module system includes graceful error handling for optional dependencies. If a route module fails to import (e.g., missing database dependencies), it logs a warning but continues loading other modules.

## Future Enhancements

- **Service Layer**: Move business logic from routes to dedicated service layers within modules
- **Inter-Module Communication**: Implement event-driven communication between modules
- **Module-Specific Models**: Move shared models into appropriate modules
- **Module-Specific Tests**: Organize tests by module boundaries