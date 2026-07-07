"""
Shared utilities for natural language parsing in agent tools.
Provides consistent, secure parsing across all simplified tool interfaces.
"""

import os
import re


def validate_file_path(
    file_path: str, allowed_base_paths: list[str] | None = None
) -> str | None:
    """
    Validate and sanitize file paths to prevent path traversal attacks.

    Args:
        file_path: The file path to validate
        allowed_base_paths: List of allowed base directories (default: repo directory)

    Returns:
        Sanitized absolute path if valid, None if invalid
    """
    if not file_path:
        return None

    # Default to repo directory and current working directory
    if allowed_base_paths is None:
        allowed_base_paths = [
            os.path.join(os.getcwd(), "repo"),
            "/app/repo",  # Docker container path
            "./repo",
            os.getcwd(),
        ]

    try:
        # Block obviously dangerous paths before processing
        dangerous_indicators = [
            "/etc/",
            "/root/",
            "/var/",
            "/sys/",
            "/proc/",
            "C:\\Windows\\",
            "C:\\System",
            "/etc/passwd",
            "/etc/shadow",
            "../",
        ]

        for indicator in dangerous_indicators:
            if indicator in file_path:
                print(f"Security warning: Blocked dangerous path pattern: {file_path}")
                return None

        # Resolve to absolute path and normalize
        abs_path = os.path.abspath(os.path.expanduser(file_path))

        # Additional check: ensure no path traversal after normalization
        if ".." in abs_path or abs_path != os.path.normpath(abs_path):
            print(f"Security warning: Path traversal detected: {file_path}")
            return None

        # Check if path is within allowed directories
        for base_path in allowed_base_paths:
            try:
                base_abs = os.path.abspath(base_path)
                if abs_path.startswith(base_abs + os.sep) or abs_path == base_abs:
                    return abs_path
            except Exception:
                continue

        # Path is outside allowed directories
        print(
            f"Security warning: Attempted access to file outside allowed directories: {file_path}"
        )
        return None

    except Exception as e:
        print(f"Invalid file path: {file_path} - {e}")
        return None


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace in text - collapse multiple spaces, remove tabs/newlines."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def extract_file_path(text: str) -> tuple[str | None, str]:
    """
    Extract file path from text and return validated path and remaining text.

    Returns:
        Tuple of (validated_file_path, text_without_file_path)
    """
    if not text:
        return None, text

    # Match file: prefix with path (handle spaces with backslash)
    file_path_match = re.search(r"file:([^\s]+(?:\\ |\S)*)", text)
    if not file_path_match:
        return None, text

    # Extract and clean file path
    raw_path = file_path_match.group(1).replace("\\ ", " ")
    validated_path = validate_file_path(raw_path)

    # Remove file path from text
    remaining_text = text[: file_path_match.start()] + text[file_path_match.end() :]
    remaining_text = normalize_whitespace(remaining_text)

    return validated_path, remaining_text


def extract_date(
    text: str, patterns: list[tuple[str, int]] | None = None
) -> str | None:
    """
    Extract date from text using standard patterns.

    Args:
        text: Text to search for dates
        patterns: Optional custom patterns as list of (regex, group_number)

    Returns:
        First valid date found in YYYY-MM-DD format, or None
    """
    if patterns is None:
        patterns = [
            (r"\bas of (\d{4}-\d{2}-\d{2})\b", 1),
            (r"\bfrom (\d{4}-\d{2}-\d{2})\b", 1),
            (r"\bdate:\s*(\d{4}-\d{2}-\d{2})\b", 1),
            (r"\b(\d{4}-\d{2}-\d{2})\b", 1),
        ]

    for pattern, group_num in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(group_num)
            # Validate it's a proper date format
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
                return date_str

    return None


def remove_patterns(text: str, patterns: list[str], flags=re.IGNORECASE) -> str:
    """
    Remove multiple regex patterns from text.

    Args:
        text: Input text
        patterns: List of regex patterns to remove
        flags: Regex flags (default: case insensitive)

    Returns:
        Text with patterns removed
    """
    result = text
    for pattern in patterns:
        result = re.sub(pattern, " ", result, flags=flags)

    # Clean up multiple spaces and trim
    result = normalize_whitespace(result)

    # Remove leading/trailing punctuation
    result = re.sub(r"^[:\s,.-]+|[:\s,.-]+$", "", result).strip()

    return result


def extract_entities_from_text(
    text: str, patterns: list[tuple[str, str]] | None = None
) -> list[str]:
    """
    Extract entity names (projects, teams, people) from text.

    Args:
        text: Text to search
        patterns: Optional custom patterns

    Returns:
        List of unique entity names found
    """
    if patterns is None:
        # Patterns to match capitalized phrases that are likely entity names
        # Order matters - more specific patterns first
        patterns = [
            # Match "Team TeamName" (capture full "Team TeamName")
            (r"\b(Team [A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,2})\b", 1),
            # Match "Project ProjectName" (capture full "Project ProjectName")
            (r"\b(Project [A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,2})\b", 1),
            # Match "project ProjectName" (capture just ProjectName)
            (r"\bproject ([A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,3})\b", 1),
            # Match "team TeamName" (capture just TeamName)
            (r"\bteam ([A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,3})\b", 1),
            # Match "for ProjectName" or "for Project Name"
            (r"\bfor ([A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,3})\b", 1),
            # Match "about EntityName"
            (r"\babout ([A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,3})\b", 1),
            # Match "Project: Name" or "Team: Name"
            (
                r"\b(?:Project|Team|Person):\s*([A-Z][A-Za-z0-9\-_]+(?: [A-Z][A-Za-z0-9\-_]+){0,3})\b",
                1,
            ),
        ]

    entities = set()
    used_positions = set()

    # Common words to exclude
    exclude_words = {
        "the",
        "and",
        "for",
        "about",
        "next",
        "week",
        "month",
        "day",
        "year",
        "with",
        "from",
        "into",
        "over",
        "after",
        "before",
    }

    for pattern, group_num in patterns:
        for match in re.finditer(pattern, text):
            # Check if this position overlaps with already used positions
            start, end = match.span()
            if any(pos >= start and pos < end for pos in used_positions):
                continue

            # Clean and validate entity
            entity = match.group(group_num).strip()

            # Filter criteria
            if (
                len(entity) > 2
                and entity.lower() not in exclude_words
                and not entity.isdigit()
                and any(c.isalpha() for c in entity)
            ):
                entities.add(entity)
                # Mark this region as used
                for pos in range(start, end):
                    used_positions.add(pos)

    return sorted(list(entities))


def parse_time_horizon(text: str) -> int:
    """
    Parse time horizon from text and return number of days.

    Args:
        text: Text containing time references

    Returns:
        Number of days (default: 30)
    """
    lower_text = text.lower()

    patterns = [
        (r"\bnext (\d+) weeks?\b", lambda m: int(m.group(1)) * 7),
        (r"\bnext (\d+) days?\b", lambda m: int(m.group(1))),
        (r"\b(\d+) weeks?\b", lambda m: int(m.group(1)) * 7),
        (r"\b(\d+) days?\b", lambda m: int(m.group(1))),
        (r"\bnext month\b", lambda m: 30),
        (r"\bnext quarter\b", lambda m: 90),
        (r"\bnext year\b", lambda m: 365),
    ]

    for pattern, converter in patterns:
        match = re.search(pattern, lower_text)
        if match:
            try:
                return converter(match)
            except Exception:
                continue

    return 30  # Default to 30 days


def clean_instruction_keywords(text: str, instruction_patterns: list[str]) -> str:
    """
    Remove instruction keywords while preserving meaningful content.

    Args:
        text: Input text
        instruction_patterns: List of instruction patterns to remove

    Returns:
        Cleaned text with instructions removed
    """
    # First normalize whitespace
    cleaned = normalize_whitespace(text)

    # Remove each instruction pattern
    cleaned = remove_patterns(cleaned, instruction_patterns)

    # If we removed everything, return a placeholder or original
    if not cleaned and text:
        # Try to extract just the content after a colon
        colon_match = re.search(r":\s*(.+)", text)
        if colon_match:
            cleaned = colon_match.group(1).strip()
        else:
            # Return original normalized text as fallback
            cleaned = normalize_whitespace(text)

    return cleaned
