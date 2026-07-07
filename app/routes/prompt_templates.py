"""
API routes for prompt template management.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..services.prompt_template_engine import PromptTemplateEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt-templates", tags=["prompt-templates"])

# Global template engine instance
_template_engine = PromptTemplateEngine()


def get_template_engine() -> PromptTemplateEngine:
    """Get the global template engine instance."""
    return _template_engine


class PromptRequest(BaseModel):
    """Request model for prompt generation."""

    action: str
    content_type: str
    context: dict[str, Any] | None = None


class TemplateInfo(BaseModel):
    """Information about a template."""

    key: str
    length: int
    required_variables: list[str]
    has_includes: bool
    has_conditionals: bool
    has_lists: bool


class RenderRequest(BaseModel):
    """Request model for template rendering."""

    template: str
    variables: dict[str, Any]


class ValidationResponse(BaseModel):
    """Response model for template validation."""

    is_valid: bool
    errors: list[str]
    required_variables: list[str]


@router.post("/load-domain/{domain_id}")
async def load_domain_templates(
    domain_id: str, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> dict[str, Any]:
    """
    Load prompt templates for a specific domain.

    Args:
        domain_id: Domain configuration ID

    Returns:
        Loading status and template count
    """
    try:
        success = engine.load_domain_templates(domain_id)

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Failed to load templates for domain '{domain_id}'",
            )

        return {
            "domain_id": domain_id,
            "success": True,
            "template_count": len(engine.get_available_templates()),
            "templates": engine.get_available_templates(),
        }

    except Exception as e:
        logger.error(f"Error loading domain templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/current-domain")
async def get_current_domain(
    engine: PromptTemplateEngine = Depends(get_template_engine),
) -> dict[str, str | None]:
    """Get the currently loaded domain."""
    return {"domain_id": engine.get_loaded_domain()}


@router.post("/get-prompt")
async def get_prompt(
    request: PromptRequest, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> dict[str, str | None]:
    """
    Get a rendered prompt for a specific action and content type.

    Args:
        request: Prompt request with action, content type, and context

    Returns:
        Rendered prompt or None if not found
    """
    try:
        prompt = engine.get_prompt(
            action=request.action,
            content_type=request.content_type,
            context=request.context,
        )

        return {"prompt": prompt, "found": prompt is not None}

    except Exception as e:
        logger.error(f"Error getting prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/templates")
async def list_templates(
    engine: PromptTemplateEngine = Depends(get_template_engine),
) -> dict[str, list[str]]:
    """List all available templates."""
    return {
        "templates": engine.get_available_templates(),
        "count": len(engine.get_available_templates()),
    }


@router.get("/template/{template_key}")
async def get_template_info(
    template_key: str, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> TemplateInfo:
    """Get information about a specific template."""
    info = engine.get_template_info(template_key)

    if not info:
        raise HTTPException(
            status_code=404, detail=f"Template '{template_key}' not found"
        )

    return TemplateInfo(**info)


@router.post("/render")
async def render_template(
    request: RenderRequest, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> dict[str, str]:
    """
    Render a template with provided variables.

    Args:
        request: Template and variables

    Returns:
        Rendered template
    """
    try:
        rendered = engine.render_template(request.template, request.variables)

        return {"rendered": rendered}

    except Exception as e:
        logger.error(f"Error rendering template: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate")
async def validate_template(
    template: str, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> ValidationResponse:
    """
    Validate template syntax.

    Args:
        template: Template string to validate

    Returns:
        Validation results
    """
    try:
        is_valid, errors = engine.validate_template(template)
        required_vars = list(engine.extract_required_variables(template))

        return ValidationResponse(
            is_valid=is_valid, errors=errors, required_variables=required_vars
        )

    except Exception as e:
        logger.error(f"Error validating template: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
async def get_usage_metrics(
    engine: PromptTemplateEngine = Depends(get_template_engine),
) -> dict[str, Any]:
    """Get template usage metrics."""
    return {"metrics": engine.get_usage_metrics()}


@router.post("/add-template")
async def add_template(
    key: str, template: str, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> dict[str, str]:
    """
    Add or update a template.

    Args:
        key: Template key
        template: Template content

    Returns:
        Success message
    """
    try:
        engine.add_template(key, template)

        return {"message": f"Template '{key}' added successfully"}

    except Exception as e:
        logger.error(f"Error adding template: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add-variable")
async def add_variable(
    key: str, value: Any, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> dict[str, str]:
    """
    Add or update a template variable.

    Args:
        key: Variable name
        value: Variable value

    Returns:
        Success message
    """
    try:
        engine.add_variable(key, value)

        return {"message": f"Variable '{key}' added successfully"}

    except Exception as e:
        logger.error(f"Error adding variable: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
async def export_templates(
    engine: PromptTemplateEngine = Depends(get_template_engine),
) -> dict[str, Any]:
    """Export all templates and configuration."""
    return engine.export_templates()


# Example templates for demonstration
EXAMPLE_TEMPLATES = {
    "consulting_firm": {
        "meeting_analysis": """Analyze this meeting for a consulting firm context:
- Which clients and engagements were discussed?
- What resource allocation decisions were made?
- Identify scope changes or new requirements
- Extract deliverables and deadlines
- Note client satisfaction signals

Meeting content:
{content}""",
        "entity_extraction": """Extract {entity_type} entities from:
{content}

For each {entity_type}, identify key attributes and relationships.""",
        "risk_assessment": """Assess risks in this {content_type}:
{content}

Identify project, client, resource, and financial risks.""",
    }
}


@router.post("/load-examples/{domain_id}")
async def load_example_templates(
    domain_id: str, engine: PromptTemplateEngine = Depends(get_template_engine)
) -> dict[str, Any]:
    """Load example templates for a domain."""
    if domain_id not in EXAMPLE_TEMPLATES:
        raise HTTPException(
            status_code=404, detail=f"No examples for domain '{domain_id}'"
        )

    # Load domain first
    engine.load_domain_templates(domain_id)

    # Add example templates
    for key, template in EXAMPLE_TEMPLATES[domain_id].items():
        engine.add_template(key, template)

    # Add default variables
    engine.add_variable("domain_name", domain_id.replace("_", " ").title())
    engine.add_variable("primary_entity", "client")
    engine.add_variable("primary_entity_plural", "clients")

    return {
        "domain_id": domain_id,
        "templates_loaded": list(EXAMPLE_TEMPLATES[domain_id].keys()),
        "success": True,
    }
