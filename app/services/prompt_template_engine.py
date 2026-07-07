"""
Prompt Template Engine for Domain-Aware Platform.

This service manages domain-specific prompt templates with variable substitution,
conditional logic, and template inclusion support.
"""

import logging
import re
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..core.dependencies import get_domain_config_service
from ..model_schemas.domain_config import DomainConfiguration

logger = logging.getLogger(__name__)


class PromptTemplateEngine:
    """Engine for managing and rendering domain-specific prompt templates."""

    def __init__(self, cache_size: int = 100):
        """
        Initialize the template engine.

        Args:
            cache_size: Maximum number of rendered templates to cache
        """
        self._templates: dict[str, str] = {}
        self._variables: dict[str, Any] = {}
        self._includes: dict[str, str] = {}
        self._current_domain: str | None = None

        # Caching
        self._template_cache: OrderedDict[str, str] = OrderedDict()
        self._max_cache_size = cache_size

        # Usage metrics
        self._usage_metrics: dict[str, int] = defaultdict(int)

        # Template syntax patterns
        self._variable_pattern = re.compile(r"\{([^{}]+)\}")
        self._conditional_pattern = re.compile(r"\{\?([^:]+):([^}]*)\}")
        self._include_pattern = re.compile(r"\{_include:([^}]+)\}")
        self._list_pattern = re.compile(r"\{#([^}]+)\}(.*?)\{/\1\}", re.DOTALL)

    async def load_domain_templates(self, domain_id: str) -> bool:
        """
        Load templates from a domain configuration.

        Args:
            domain_id: Domain configuration ID

        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            loader = get_domain_config_service()
            domain_config = await loader.load_domain(domain_id)

            if not domain_config:
                logger.error(f"Failed to load domain configuration: {domain_id}")
                return False

            # Clear cache when switching domains
            if self._current_domain != domain_id:
                self._template_cache.clear()

            self._current_domain = domain_id

            # Load templates from config
            if hasattr(domain_config, "prompt_templates"):
                self._templates = domain_config.prompt_templates.copy()
            else:
                # Try loading from file
                template_file = Path(f"config/domains/{domain_id}/prompts.yaml")
                if template_file.exists():
                    self._templates = self.load_templates_from_file(str(template_file))
                else:
                    self._templates = {}

            # Load variables
            if hasattr(domain_config, "template_variables"):
                self._variables = domain_config.template_variables.copy()
            else:
                self._variables = self._generate_default_variables(domain_config)

            # Extract includes
            self._includes = self._templates.get("_includes", {})
            if "_includes" in self._templates:
                del self._templates["_includes"]

            logger.info(
                f"Loaded {len(self._templates)} templates and "
                f"{len(self._variables)} variables for domain '{domain_id}'"
            )

            return True

        except Exception as e:
            logger.error(f"Error loading domain templates: {e}")
            return False

    def load_templates_from_file(self, file_path: str) -> dict[str, str]:
        """
        Load templates from a YAML file.

        Args:
            file_path: Path to YAML file

        Returns:
            Dictionary of templates
        """
        try:
            with open(file_path) as f:
                data = yaml.safe_load(f)

            if isinstance(data, dict):
                return data
            else:
                logger.error(f"Invalid template file format: {file_path}")
                return {}

        except Exception as e:
            logger.error(f"Error loading templates from file: {e}")
            return {}

    def get_prompt(
        self, action: str, content_type: str, context: dict[str, Any] | None = None
    ) -> str | None:
        """
        Get a rendered prompt for a specific action and content type.

        Args:
            action: Action to perform (analyze, extract, summarize, etc.)
            content_type: Type of content (meeting, document, email, etc.)
            context: Additional context variables

        Returns:
            Rendered prompt or None if not found
        """
        # Track usage
        metric_key = f"{action}_{content_type}"
        self._usage_metrics[metric_key] += 1

        # Try specific template first
        template_key = f"{content_type}_{action}"
        if template_key not in self._templates:
            # Try reverse order
            template_key = f"{action}_{content_type}"

        if template_key not in self._templates:
            # Try generic action template
            template_key = action

        if template_key not in self._templates:
            # Try generic content type template
            template_key = content_type

        if template_key not in self._templates:
            logger.warning(
                f"No template found for action='{action}', "
                f"content_type='{content_type}'"
            )
            return None

        # Get template
        template = self._templates[template_key]

        # Merge variables
        variables = self._variables.copy()
        if context:
            variables.update(context)

        # Render template
        return self.render_template(template, variables)

    def render_template(self, template: str, variables: dict[str, Any]) -> str:
        """
        Render a template with variable substitution.

        Args:
            template: Template string
            variables: Variables for substitution

        Returns:
            Rendered template
        """
        # Check cache
        cache_key = f"{hash(template)}:{hash(frozenset(variables.items()))}"
        if cache_key in self._template_cache:
            return self._template_cache[cache_key]

        # Start with original template
        result = template

        # Process includes first
        result = self._process_includes(result, variables)

        # Process lists
        result = self._process_lists(result, variables)

        # Process conditionals
        result = self._process_conditionals(result, variables)

        # Process simple variables
        result = self._process_variables(result, variables)

        # Cache result
        self._cache_template(cache_key, result)

        return result

    def _process_includes(
        self, template: str, variables: dict[str, Any], depth: int = 0
    ) -> str:
        """Process template includes."""
        if depth > 5:  # Prevent infinite recursion
            logger.warning("Maximum include depth reached")
            return template

        def replace_include(match):
            include_name = match.group(1)
            if include_name in self._includes:
                included = self._includes[include_name]
                # Recursively process includes in the included template
                return self._process_includes(included, variables, depth + 1)
            else:
                logger.warning(f"Include not found: {include_name}")
                return match.group(0)

        return self._include_pattern.sub(replace_include, template)

    def _process_lists(self, template: str, variables: dict[str, Any]) -> str:
        """Process list iterations in template."""

        def replace_list(match):
            list_var = match.group(1)
            list_template = match.group(2)

            if list_var not in variables:
                return ""

            items = variables[list_var]
            if not isinstance(items, list):
                return ""

            results = []
            for item in items:
                # Create context with item variables
                item_vars = variables.copy()
                if isinstance(item, dict):
                    item_vars.update(item)
                else:
                    item_vars["item"] = item

                # Render list template for this item
                rendered = self._process_variables(list_template, item_vars)
                results.append(rendered)

            return "".join(results)

        return self._list_pattern.sub(replace_list, template)

    def _process_conditionals(self, template: str, variables: dict[str, Any]) -> str:
        """Process conditional statements in template."""

        def replace_conditional(match):
            condition = match.group(1)
            content = match.group(2)

            # Evaluate condition
            if condition in variables:
                value = variables[condition]
                # Check if truthy
                if value:
                    return content

            return ""

        return self._conditional_pattern.sub(replace_conditional, template)

    def _process_variables(self, template: str, variables: dict[str, Any]) -> str:
        """Process simple variable substitutions."""

        def replace_variable(match):
            var_name = match.group(1)

            # Skip special syntax
            if (
                var_name.startswith("?")
                or var_name.startswith("_")
                or var_name.startswith("#")
            ):
                return match.group(0)

            if var_name in variables:
                return str(variables[var_name])
            else:
                # Keep unmatched variables
                return match.group(0)

        return self._variable_pattern.sub(replace_variable, template)

    def validate_template(self, template: str) -> tuple[bool, list[str]]:
        """
        Validate template syntax.

        Args:
            template: Template to validate

        Returns:
            Tuple of (is_valid, error_messages)
        """
        errors = []

        # Check for unclosed variables
        open_count = template.count("{")
        close_count = template.count("}")
        if open_count != close_count:
            errors.append(f"Mismatched braces: {open_count} open, {close_count} close")

        # Check for valid variable syntax
        for match in self._variable_pattern.finditer(template):
            var_content = match.group(1)
            if not var_content:
                errors.append("Empty variable declaration")

        # Check conditional syntax
        for match in self._conditional_pattern.finditer(template):
            condition = match.group(1)
            if not condition:
                errors.append("Empty conditional condition")

        # Check list syntax
        list_starts = re.findall(r"\{#([^}]+)\}", template)
        list_ends = re.findall(r"\{/([^}]+)\}", template)

        for start_name in list_starts:
            if start_name not in list_ends:
                errors.append(f"Unclosed list block: {start_name}")

        return len(errors) == 0, errors

    def extract_required_variables(self, template: str) -> set[str]:
        """
        Extract all required variables from a template.

        Args:
            template: Template to analyze

        Returns:
            Set of variable names
        """
        variables = set()

        # Extract from variable patterns
        for match in self._variable_pattern.finditer(template):
            var_name = match.group(1)
            if not (
                var_name.startswith("?")
                or var_name.startswith("_")
                or var_name.startswith("#")
                or "/" in var_name
            ):
                variables.add(var_name)

        # Extract from conditionals
        for match in self._conditional_pattern.finditer(template):
            variables.add(match.group(1))

        # Extract from lists
        for match in self._list_pattern.finditer(template):
            variables.add(match.group(1))

        return variables

    def _cache_template(self, key: str, rendered: str) -> None:
        """Cache a rendered template."""
        # Remove oldest if at capacity
        if len(self._template_cache) >= self._max_cache_size:
            self._template_cache.popitem(last=False)

        self._template_cache[key] = rendered

    def get_cached_template(self, template_key: str) -> str | None:
        """Get a template from cache or load it."""
        if template_key in self._template_cache:
            return self._template_cache[template_key]

        if template_key in self._templates:
            template = self._templates[template_key]
            self._template_cache[template_key] = template
            return template

        return None

    def _generate_default_variables(
        self, domain_config: DomainConfiguration
    ) -> dict[str, Any]:
        """Generate default variables from domain configuration."""
        variables = {
            "domain_id": domain_config.id,
            "domain_name": domain_config.name,
            "domain_version": domain_config.version,
        }

        # Add entity information
        if domain_config.entities:
            # Primary entity (first one)
            primary_entity_id = list(domain_config.entities.keys())[0]
            primary_entity = domain_config.entities[primary_entity_id]

            variables.update(
                {
                    "primary_entity": primary_entity.name,
                    "primary_entity_plural": f"{primary_entity.name}s",
                    "primary_entity_description": primary_entity.description,
                }
            )

            # Add all entity types
            entity_list = []
            for _entity_id, entity in domain_config.entities.items():
                entity_list.append(f"- {entity.name} ({entity.description})")
            variables["entity_list"] = "\n".join(entity_list)

        return variables

    def get_loaded_domain(self) -> str | None:
        """Get the currently loaded domain ID."""
        return self._current_domain

    def get_available_templates(self) -> list[str]:
        """Get list of available template keys."""
        return list(self._templates.keys())

    def get_usage_metrics(self) -> dict[str, int]:
        """Get template usage metrics."""
        return dict(self._usage_metrics)

    def add_template(self, key: str, template: str) -> None:
        """
        Add or update a template.

        Args:
            key: Template key
            template: Template content
        """
        self._templates[key] = template
        # Clear cache for this template
        self._template_cache.pop(key, None)

    def add_variable(self, key: str, value: Any) -> None:
        """
        Add or update a template variable.

        Args:
            key: Variable name
            value: Variable value
        """
        self._variables[key] = value

    def get_template_info(self, template_key: str) -> dict[str, Any] | None:
        """
        Get information about a template.

        Args:
            template_key: Template key

        Returns:
            Template information or None
        """
        if template_key not in self._templates:
            return None

        template = self._templates[template_key]

        return {
            "key": template_key,
            "length": len(template),
            "required_variables": list(self.extract_required_variables(template)),
            "has_includes": bool(self._include_pattern.search(template)),
            "has_conditionals": bool(self._conditional_pattern.search(template)),
            "has_lists": bool(self._list_pattern.search(template)),
        }

    def export_templates(self) -> dict[str, Any]:
        """
        Export all templates and variables.

        Returns:
            Dictionary with templates and configuration
        """
        return {
            "domain": self._current_domain,
            "templates": self._templates.copy(),
            "variables": self._variables.copy(),
            "includes": self._includes.copy(),
            "exported_at": datetime.utcnow().isoformat(),
        }
