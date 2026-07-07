"""
Shared YAML parsing utilities for extraction tools.

Handles the common pattern of extracting and parsing YAML blocks from
Claude LLM responses, with multi-attempt recovery for common issues.
"""

import logging
import re

import yaml

logger = logging.getLogger(__name__)


def extract_yaml_block(response: str) -> str:
    """Extract YAML content from a Claude response.

    Handles: ```yaml...```, ```...```, or raw YAML with various markers.
    """
    if not response:
        return ""

    # Try ```yaml ... ```
    yaml_start = response.find("```yaml")
    if yaml_start != -1:
        yaml_end = response.find("```", yaml_start + 7)
        if yaml_end != -1:
            return response[yaml_start + 7:yaml_end].strip()
        return response[yaml_start + 7:].strip()

    # Try ``` ... ```
    code_start = response.find("```")
    if code_start != -1:
        code_end = response.find("```", code_start + 3)
        if code_end != -1:
            block = response[code_start + 3:code_end].strip()
            lines = block.split("\n")
            # Strip language identifier on first line if present
            if lines and not lines[0].strip().startswith(("-", "{")) and ":" not in lines[0]:
                return "\n".join(lines[1:])
            return block
        return response[code_start + 3:].strip()

    # Try to find YAML start markers in raw text
    for marker in ("relationships:", "enrichments:", "decisions:", "---"):
        idx = response.find(marker)
        if idx != -1:
            return response[idx:].strip()

    return response.strip()


def parse_yaml_list(yaml_content: str, root_key: str) -> list:
    """Parse a YAML block and extract a list under the given root key.

    Tries multiple recovery strategies for common YAML issues.
    Returns empty list on failure.
    """
    if not yaml_content or not yaml_content.strip():
        return []

    # Attempt 1: parse as-is
    try:
        parsed = yaml.safe_load(yaml_content)
        if isinstance(parsed, dict):
            return parsed.get(root_key, []) or []
        if isinstance(parsed, list):
            return parsed
    except yaml.YAMLError:
        pass

    # Attempt 2: strip everything before root_key marker
    try:
        idx = yaml_content.find(f"{root_key}:")
        if idx != -1:
            parsed = yaml.safe_load(yaml_content[idx:])
            if isinstance(parsed, dict):
                return parsed.get(root_key, []) or []
    except yaml.YAMLError:
        pass

    # Attempt 3: fix unquoted strings with colons
    try:
        fixed = re.sub(
            r": ([^\"'\[\]\n{][^\n]*)",
            lambda m: f': "{m.group(1).strip()}"' if ":" in m.group(1) else m.group(0),
            yaml_content,
        )
        parsed = yaml.safe_load(fixed)
        if isinstance(parsed, dict):
            return parsed.get(root_key, []) or []
    except (yaml.YAMLError, re.error):
        pass

    logger.warning(f"[YAML_UTILS] All parse attempts failed for root_key='{root_key}'")
    return []
