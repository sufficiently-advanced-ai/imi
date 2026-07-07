import os
import xml.etree.ElementTree as ET

from fastapi import HTTPException

from ..models import File

# Cache for prompt templates
prompt_templates: dict[str, str] = {}


class PromptService:
    """Service for loading and managing prompt templates."""

    def __init__(self):
        self.cache: dict[str, str] = {}

    def get_prompt(self, prompt_name: str) -> str:
        """Get a prompt template by name."""
        if prompt_name in self.cache:
            return self.cache[prompt_name]

        # Load the prompt
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "prompts", f"{prompt_name}.xml"
        )

        if not os.path.exists(prompt_path):
            raise ValueError(f"Prompt template not found: {prompt_name}")

        try:
            with open(prompt_path, encoding="utf-8") as f:
                content = f.read()

            # Parse XML to extract instructions
            root = ET.fromstring(content)
            instructions = root.find("instructions")

            if instructions is not None:
                prompt_content = instructions.text or ""
                self.cache[prompt_name] = prompt_content
                return prompt_content
            else:
                raise ValueError(f"No instructions found in prompt: {prompt_name}")

        except Exception as e:
            raise ValueError(f"Failed to load prompt template {prompt_name}: {str(e)}")


def load_prompt_template(prompt_type: str) -> str:
    """Load and cache prompt template from XML file."""
    if prompt_type in prompt_templates:
        return prompt_templates[prompt_type]

    template_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "prompts", f"{prompt_type}.xml"
    )
    if not os.path.exists(template_path):
        raise HTTPException(
            status_code=400, detail=f"Invalid prompt type: {prompt_type}"
        )

    try:
        tree = ET.parse(template_path)
        prompt_templates[prompt_type] = ET.tostring(tree.getroot(), encoding="unicode")
        return prompt_templates[prompt_type]
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to load prompt template: {str(e)}"
        )


def format_prompt(
    template: str, files: list[File], question: str = "", context: dict = None, **kwargs
) -> str:
    """Format prompt template with files, question, context variables, and entity data."""
    try:
        root = ET.fromstring(template)

        # Handle files element if present
        doc_elem = root.find(".//files")
        if doc_elem is not None:
            doc_elem.clear()  # Remove placeholder comment
            # Add file info as attributes, content as text
            for file in files:
                file_elem = ET.SubElement(doc_elem, "file")
                file_elem.set("path", file.path)
                if hasattr(file, "created_at") and file.created_at:
                    file_elem.set("created_at", file.created_at.isoformat())
                if hasattr(file, "modified_at") and file.modified_at:
                    file_elem.set("modified_at", file.modified_at.isoformat())
                file_elem.text = file.content

        # Insert question if query element present
        query_elem = root.find(".//query")
        if query_elem is not None:
            query_elem.clear()  # Remove placeholder comment
            query_elem.text = question

        # Handle entity suggestions (Issue #58)
        if "entity_suggestions" in kwargs:
            suggestions = kwargs["entity_suggestions"]
            elem = root.find(".//entity_suggestions")
            if elem is not None:
                elem.clear()
                # Add people
                if suggestions.get("people"):
                    people_elem = ET.SubElement(elem, "people")
                    for person in suggestions["people"]:
                        p_elem = ET.SubElement(people_elem, "person")
                        p_elem.set("id", person.id)
                        p_elem.set("name", person.canonical_name)
                        p_elem.set("confidence", str(person.confidence))
                        if person.aliases:
                            p_elem.set("aliases", ", ".join(person.aliases))
                        if hasattr(person, "titles") and person.titles:
                            p_elem.set("titles", ", ".join(person.titles))

                # Add projects
                if suggestions.get("projects"):
                    projects_elem = ET.SubElement(elem, "projects")
                    for project in suggestions["projects"]:
                        p_elem = ET.SubElement(projects_elem, "project")
                        p_elem.set("id", project.id)
                        p_elem.set("name", project.canonical_name)
                        p_elem.set("confidence", str(project.confidence))
                        if project.aliases:
                            p_elem.set("aliases", ", ".join(project.aliases))

                # Add teams
                if suggestions.get("teams"):
                    teams_elem = ET.SubElement(elem, "teams")
                    for team in suggestions["teams"]:
                        t_elem = ET.SubElement(teams_elem, "team")
                        t_elem.set("id", team.id)
                        t_elem.set("name", team.canonical_name)
                        t_elem.set("confidence", str(team.confidence))
                        if team.aliases:
                            t_elem.set("aliases", ", ".join(team.aliases))

        # Handle known people
        if "known_people" in kwargs:
            elem = root.find(".//frequent_people") or root.find(".//known_people")
            if elem is not None:
                elem.clear()
                for person in kwargs["known_people"]:
                    p_elem = ET.SubElement(elem, "person")
                    p_elem.set("id", person.id)
                    p_elem.set("name", person.canonical_name)
                    if person.aliases:
                        p_elem.set("aliases", ", ".join(person.aliases))
                    if hasattr(person, "titles") and person.titles:
                        p_elem.set("titles", ", ".join(person.titles))

        # Handle context variables if provided
        if context or kwargs:
            prompt_text = ET.tostring(root, encoding="unicode")
            # Merge context and remaining kwargs
            all_context = context.copy() if context else {}
            # Only add non-entity kwargs to context
            for k, v in kwargs.items():
                if k not in ["entity_suggestions", "known_people"]:
                    all_context[k] = v

            # Replace context variables in the template
            for key, value in all_context.items():
                placeholder = "{" + key + "}"
                if isinstance(value, str):
                    prompt_text = prompt_text.replace(placeholder, value)
                elif value is None:
                    prompt_text = prompt_text.replace(placeholder, "")
                else:
                    prompt_text = prompt_text.replace(placeholder, str(value))
            return prompt_text

        return ET.tostring(root, encoding="unicode")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to format prompt: {str(e)}"
        )
