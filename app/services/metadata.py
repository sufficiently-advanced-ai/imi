import os
import sys
from datetime import datetime

import yaml
from anthropic import APIConnectionError, APIStatusError, RateLimitError
from fastapi import HTTPException

from ..config import settings
from ..core.domain_config.domain_config_service import get_domain_config_service
from ..git_ops import git_ops
from ..models import (
    DocumentMetadata,
    EnhancedDocumentMetadata,
    EntityExtraction,
    EntityType,
    File,
    MetadataResponse,
)
from ..services.claude_client import get_claude_client
from ..services.domain_prompt_builder import DomainPromptBuilder
from ..services.frontmatter import frontmatter


async def analyze_metadata(path: str):
    """Generate metadata for a document without frontmatter using Claude"""
    # Import file cache
    from ..services.file_cache import file_cache

    # Get active domain configuration FIRST (REQUIRED for domain-aware extraction)
    domain_service = get_domain_config_service()
    domain = domain_service.get_active_domain()
    if not domain:
        raise ValueError("No domain configured")

    try:
        # Get document content using the cache
        # Get document content using the cache
        file_obj = await file_cache.get_file(path)
        if not file_obj:
            # Fall back to direct read if not in cache
            content = await git_ops.read_file(path)
            if not content:
                raise HTTPException(status_code=404, detail="Document not found")
            file_obj = File(path=path, content=content)
        else:
            # Extract content from file_obj if it was found in cache
            content = file_obj.content

        # Build prompt from domain schema
        prompt_builder = DomainPromptBuilder()
        prompt = prompt_builder.build_extraction_prompt(content, domain)

        # Estimate token count (~ 4 chars per token)
        estimated_tokens = len(prompt) // 4

        # Log prompt length for debugging
        print(
            f"Sending metadata prompt for {path} (length: {len(prompt)}, est. tokens: {estimated_tokens})",
            file=sys.stderr,
        )

        # Get structured response from Claude
        try:
            claude_client = get_claude_client()
            message = await claude_client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=1024,
                operation="metadata_extraction",
                estimate_token_count=estimated_tokens,
                request_id=f"metadata_{path.replace('/', '_')}",
            )

            # Extract YAML block from response
            yaml_text = message.content[0].text if message and message.content else ""
            print(
                f"Received metadata response for {path} (length: {len(yaml_text)})",
                file=sys.stderr,
            )

            if "```yaml" in yaml_text:
                yaml_text = yaml_text.split("```yaml")[1].split("```")[0].strip()
            else:
                raise ValueError("No YAML block found in Claude's response")

            # Parse YAML to dict and process timestamps
            metadata_dict = yaml.safe_load(yaml_text)

            # Get current time as fallback
            now = datetime.utcnow().isoformat()

            # Get temporal reasoning if available
            temporal_reasoning = metadata_dict.get("temporal_reasoning", "")

            # Logic for created date:
            # 1. If Claude found a date in the document (indicated by temporal_reasoning),
            #    use the date Claude provided in 'created' field
            # 2. Otherwise, fall back to current time
            created_date = now
            if temporal_reasoning:
                # Check if temporal reasoning mentions finding a date in the document content
                has_content_date = any(
                    [
                        "found in document" in temporal_reasoning,
                        "from document" in temporal_reasoning,
                        "extracted from" in temporal_reasoning,
                        "explicit" in temporal_reasoning.lower(),
                        "mentioned in" in temporal_reasoning,
                        "stated in" in temporal_reasoning,
                        "from content" in temporal_reasoning,
                    ]
                )

                # If temporal reasoning indicates a content-derived date, use Claude's date
                if has_content_date and "created" in metadata_dict:
                    created_date = metadata_dict.get("created")

            # Logic for modified date:
            # On first metadata creation, use same as created date
            # (will update on subsequent modifications)
            modified_date = created_date

            # Update metadata with appropriate dates
            metadata_dict.update({"created": created_date, "modified": modified_date})

            # Create DocumentMetadata instance
            metadata = DocumentMetadata(**metadata_dict)

            # Add frontmatter to content
            new_content = frontmatter.update(content, metadata_dict)

            # Save updated document
            full_path = os.path.join(git_ops.repo_path, path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return MetadataResponse(path=path, metadata=metadata)

        except APIConnectionError:
            raise HTTPException(
                status_code=503, detail="Failed to connect to Anthropic API"
            )
        except RateLimitError:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        except APIStatusError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        except ValueError as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to parse metadata response: {str(e)}"
            )

    except ValueError:
        # Re-raise ValueError (e.g., "No domain configured") without wrapping
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


async def analyze_metadata_with_entities(path: str, existing_entities: dict = None):
    """Generate entity-aware metadata for a document - Issue #58"""
    try:
        # Import dependencies
        from ..services.file_cache import file_cache

        # Try to get entity registry if available
        entity_registry = None
        entity_suggestions = {"people": [], "projects": [], "teams": []}
        known_people = []

        try:
            from ..domain.entities.services import get_entity_repository

            entity_registry = get_entity_repository()

            # Get document content for entity suggestions
            file_obj = await file_cache.get_file(path)
            if not file_obj:
                content = await git_ops.read_file(path)
                if not content:
                    raise HTTPException(status_code=404, detail="Document not found")
                file_obj = File(path=path, content=content)
            else:
                content = file_obj.content

            # Get entity suggestions
            entity_suggestions = entity_registry.suggest_entities(content)

            # Get frequently mentioned people
            known_people = [
                person
                for person in entity_registry.people.values()
                if person.confidence >= 0.9
            ][:20]  # Limit to top 20 for prompt size

        except ImportError:
            # Entity registry not available, fallback to standard metadata
            pass

        # If no registry, fall back to standard analyze_metadata
        if not entity_registry:
            result = await analyze_metadata(path)
            # Convert to enhanced metadata with empty entity fields
            enhanced_metadata = EnhancedDocumentMetadata(
                **result.metadata.model_dump(),
                entity_extractions=[],
                entity_confidence=0.0,
                validation_status="pending",
            )
            return MetadataResponse(path=path, metadata=enhanced_metadata)

        # Load enhanced prompt template
        from ..services.prompts import format_prompt, load_prompt_template

        # Try enhanced template first, fall back to standard if not available
        try:
            template = load_prompt_template("metadata_with_entities")
        except (FileNotFoundError, KeyError):
            # Enhanced template not available, use standard with entity context
            template = load_prompt_template("metadata")

        # Format prompt with entity context
        prompt = format_prompt(
            template,
            [file_obj],
            "Please analyze this document and generate valid YAML frontmatter with entity awareness.",
            entity_suggestions=entity_suggestions,
            known_people=known_people,
        )

        # Estimate token count
        estimated_tokens = len(prompt) // 4

        # Log prompt info
        print(
            f"Sending entity-aware metadata prompt for {path} (length: {len(prompt)}, est. tokens: {estimated_tokens})",
            file=sys.stderr,
        )

        # Get response from Claude
        try:
            claude_client = get_claude_client()
            message = await claude_client.generate_message(
                messages=[{"role": "user", "content": prompt}],
                model=settings.CLAUDE_HAIKU_MODEL,
                max_tokens=2048,  # Increased for entity data
                operation="entity_extraction",
                estimate_token_count=estimated_tokens,
                request_id=f"metadata_entities_{path.replace('/', '_')}",
            )

            # Extract YAML from response
            yaml_text = message.content[0].text if message and message.content else ""
            print(
                f"Received entity-aware metadata response for {path} (length: {len(yaml_text)})",
                file=sys.stderr,
            )

            if "```yaml" in yaml_text:
                yaml_text = yaml_text.split("```yaml")[1].split("```")[0].strip()
            else:
                raise ValueError("No YAML block found in Claude's response")

            # Parse YAML
            metadata_dict = yaml.safe_load(yaml_text)

            # Process timestamps
            now = datetime.utcnow().isoformat()
            temporal_reasoning = metadata_dict.get("temporal_reasoning", "")

            # Date logic (same as standard metadata)
            created_date = now
            if temporal_reasoning:
                has_content_date = any(
                    [
                        "found in document" in temporal_reasoning,
                        "from document" in temporal_reasoning,
                        "extracted from" in temporal_reasoning,
                        "explicit" in temporal_reasoning.lower(),
                        "mentioned in" in temporal_reasoning,
                        "stated in" in temporal_reasoning,
                        "from content" in temporal_reasoning,
                    ]
                )

                if has_content_date and "created" in metadata_dict:
                    created_date = metadata_dict.get("created")

            modified_date = created_date

            # Update dates
            metadata_dict.update({"created": created_date, "modified": modified_date})

            # Extract entity-specific fields if present
            entity_extractions = []
            if "entity_extractions" in metadata_dict:
                for extraction in metadata_dict["entity_extractions"]:
                    # Convert string entity type to enum
                    entity_type_str = extraction.get("entity_type", "PERSON").upper()
                    entity_type = (
                        EntityType[entity_type_str]
                        if entity_type_str in EntityType.__members__
                        else EntityType.PERSON
                    )

                    entity_extractions.append(
                        EntityExtraction(
                            entity_type=entity_type,
                            raw_text=extraction.get("raw_text", ""),
                            canonical_id=extraction.get("canonical_id"),
                            confidence=extraction.get("confidence", 0.0),
                            context=extraction.get("context", ""),
                            suggested_by=extraction.get("suggested_by", "claude"),
                            **{
                                k: v
                                for k, v in extraction.items()
                                if k
                                not in [
                                    "entity_type",
                                    "raw_text",
                                    "canonical_id",
                                    "confidence",
                                    "context",
                                    "suggested_by",
                                ]
                            },
                        )
                    )

                # Remove from dict to avoid duplication
                del metadata_dict["entity_extractions"]

            # Extract other enhanced fields
            entity_confidence = metadata_dict.pop("entity_confidence", 0.0)
            validation_status = metadata_dict.pop("validation_status", "pending")

            # Create enhanced metadata
            metadata = EnhancedDocumentMetadata(
                **metadata_dict,
                entity_extractions=entity_extractions,
                entity_confidence=entity_confidence,
                validation_status=validation_status,
            )

            # Add frontmatter to content
            # Convert back to dict for frontmatter, including entity data
            frontmatter_dict = metadata_dict.copy()
            if entity_extractions:
                frontmatter_dict["entity_extractions"] = [
                    {
                        "entity_type": e.entity_type.value,
                        "raw_text": e.raw_text,
                        "canonical_id": e.canonical_id,
                        "confidence": e.confidence,
                        "context": e.context,
                        "suggested_by": e.suggested_by,
                        **getattr(e, "model_extra", {}),
                    }
                    for e in entity_extractions
                ]
                frontmatter_dict["entity_confidence"] = entity_confidence
                frontmatter_dict["validation_status"] = validation_status

            new_content = frontmatter.update(content, frontmatter_dict)

            # Save updated document
            full_path = os.path.join(git_ops.repo_path, path)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return MetadataResponse(path=path, metadata=metadata)

        except APIConnectionError:
            raise HTTPException(
                status_code=503, detail="Failed to connect to Anthropic API"
            )
        except RateLimitError:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        except APIStatusError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e))
        except ValueError as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to parse metadata response: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Entity-aware analysis failed: {str(e)}"
        )
