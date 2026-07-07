"""
Domain-aware prompt builder for metadata extraction - Issue #595

This service dynamically generates Claude prompts based on domain configuration,
ensuring metadata extraction uses domain-specific entity types and attributes.
"""

from app.model_schemas.domain_config import DomainConfiguration


class DomainPromptBuilder:
    """Builds dynamic extraction prompts from domain schema."""

    def build_extraction_prompt(self, content: str, domain: DomainConfiguration) -> str:
        """Build dynamic extraction prompt from domain schema.

        Args:
            content: Document content to analyze
            domain: Domain configuration with entity schemas

        Returns:
            Prompt string for Claude with domain-specific extraction rules
        """
        # Build entity type descriptions
        if not domain.entities:
            raise ValueError("Domain configuration has no entities")
        entity_descriptions = []
        for entity_type, entity_schema in domain.entities.items():
            # Build attribute list for this entity
            attributes = []
            for attr in entity_schema.attributes:
                attr_desc = f"{attr.name} ({attr.type})"
                if attr.required:
                    attr_desc += " [required]"
                if attr.enum:
                    attr_desc += f" - options: {', '.join(attr.enum)}"
                attributes.append(attr_desc)

            entity_desc = f"""
      - {entity_type}: {entity_schema.description}
        * Attributes: {', '.join(attributes) if attributes else 'name'}
        * Plural form: {entity_schema.plural}"""
            entity_descriptions.append(entity_desc)

        entity_section = "\n".join(entity_descriptions)

        # Build entity ID format instruction with domain-specific examples
        example_entities = list(domain.entities.keys())[:3]  # Use up to 3 entity types as examples
        entity_examples = []
        example_names = [
            ("Acme Corp", "acme-corp"),
            ("John Smith", "john-smith"),
            ("Q4 Migration", "q4-migration"),
            ("TechStart Inc", "techstart-inc"),
            ("Coffee Meeting", "coffee-meeting"),
        ]

        for i, entity_type in enumerate(example_entities):
            if i < len(example_names):
                name, normalized = example_names[i]
                entity_examples.append(f'        * "{name}" ({entity_type}) -> "{entity_type}-{normalized}"')

        examples_text = "\n".join(entity_examples) if entity_examples else '        * "Example Name" (entity_type) -> "entity_type-example-name"'

        entity_id_format = f"""
    <entity_id_format>
      - Entity IDs must follow format: entity_type-normalized-name
      - Examples:
{examples_text}
      - Normalization rules:
        * Convert to lowercase
        * Replace spaces with hyphens
        * Remove special characters (periods, apostrophes)
        * Keep alphanumeric and hyphens only
    </entity_id_format>"""

        # Build output format with domain entities
        entity_fields = []
        for _, entity_schema in domain.entities.items():
            entity_fields.append(f"""
    {entity_schema.plural}:
      - id: "entity_type-normalized-name"
        name: "Original Name"
        # Additional attributes based on domain schema""")

        output_format = "\n".join(entity_fields)

        # Build complete prompt
        prompt = f"""<?xml version="1.0" encoding="UTF-8"?>
<prompt>
  <context>
    You're extracting structured metadata for a {domain.name} knowledge base. Extract entities specific to this domain, following the entity types and attributes defined below.

    Domain: {domain.name} (v{domain.version})
  </context>

  <rules>
    <entity_extraction>
{entity_section}
    </entity_extraction>

{entity_id_format}

    <yaml_rules>
      - Use 2 spaces for indentation
      - Start lists with "- " (hyphen + space)
      - Quote strings with special chars
      - No line wrapping or truncation
      - Include all fields (empty list [] if none)
    </yaml_rules>
  </rules>

  <document>
{content}
  </document>

  <query>Please analyze this document and generate valid YAML frontmatter with domain-specific entities.</query>

  <output_format>
    ```yaml
    type: string  # document type
    created: "2025-01-24T00:00:00Z"  # ISO-8601
    modified: "2025-01-24T00:00:00Z"  # ISO-8601
    source: "auto"
    temporal_reasoning: "string"

    # Domain-specific entity fields
{output_format}

    classification:
      categories: []
      confidence: 0.0

    summary:
      key_points: []
      action_items: []

    references:
      related_docs: []
      context_files: []
    ```
  </output_format>

  <instructions>
    1. Extract entities matching the {domain.name} domain schema
    2. Use entity_type-normalized-name format for all entity IDs
    3. Include all required attributes for each entity type
    4. Generate valid YAML that will parse without errors
    5. Always include all entity fields (use empty list [] if none found)
  </instructions>
</prompt>"""

        return prompt
