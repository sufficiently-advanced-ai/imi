from datetime import datetime

from anthropic import APIConnectionError, APIStatusError, RateLimitError
from fastapi import HTTPException

from ..config import settings
from ..models import DiffExplanationCache, File
from ..services.claude_client import get_claude_client
from ..services.prompts import format_prompt, load_prompt_template


class DiffBrain:
    """Service for explaining differences between file versions with caching"""

    def __init__(self):
        self.client = get_claude_client()
        self._cache: dict[str, DiffExplanationCache] = {}  # In-memory cache

    async def _get_cached_explanation(
        self, file_path: str, current_commit: str, previous_commit: str
    ) -> str | None:
        """Get explanation from cache if available and not expired"""
        cache_key = f"{file_path}:{previous_commit}:{current_commit}"

        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            # Check if cache entry is still valid (less than 24 hours old)
            cache_age = datetime.utcnow() - cache_entry.created_at
            if cache_age.total_seconds() < 86400:  # 24 hours in seconds
                return cache_entry.explanation

            # Remove expired cache entry
            del self._cache[cache_key]

        return None

    async def _cache_explanation(
        self,
        file_path: str,
        current_commit: str,
        previous_commit: str,
        explanation: str,
    ) -> None:
        """Cache explanation for future use"""
        cache_key = f"{file_path}:{previous_commit}:{current_commit}"

        self._cache[cache_key] = DiffExplanationCache(
            file_path=file_path,
            current_commit=current_commit,
            previous_commit=previous_commit,
            explanation=explanation,
            created_at=datetime.utcnow(),
        )

    async def explain_difference(
        self,
        old_content: str,
        new_content: str,
        file_path: str,
        current_commit: str,
        previous_commit: str,
        context_files: list[str] | None = None,
        force_refresh: bool = False,
    ) -> str:
        """Generate an explanation of the differences between two versions of content"""
        try:
            # Check cache first (unless force_refresh is True)
            if not force_refresh:
                cached_explanation = await self._get_cached_explanation(
                    file_path, current_commit, previous_commit
                )
                if cached_explanation:
                    return cached_explanation

            # Load explain-diff prompt template
            template = load_prompt_template("explain-diff")

            # Create File objects
            old_file = File(path=f"{file_path}.old", content=old_content)
            new_file = File(path=file_path, content=new_content)

            # Get context files if provided
            context = []
            if context_files:
                from ..git_ops import git_ops

                context = await git_ops.read_markdown_files(context_files)

            # Format prompt with old and new content plus context
            files_to_include = [old_file, new_file] + context
            prompt = format_prompt(
                template,
                files_to_include,
                f"Explain the changes between the old and new versions of {file_path}",
            )

            # Get explanation from Claude - Haiku sufficient for diff explanation
            message = await self.client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=1024,
                operation="explain_diff",
            )

            explanation = message.content[0].text if message and message.content else ""

            # Cache the explanation for future use
            await self._cache_explanation(
                file_path, current_commit, previous_commit, explanation
            )

            return explanation

        except APIConnectionError:
            raise HTTPException(
                status_code=503, detail="Failed to connect to Anthropic API"
            )
        except RateLimitError:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        except APIStatusError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Explanation generation failed: {str(e)}"
            )
