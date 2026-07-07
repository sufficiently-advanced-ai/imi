import os

from fastapi import APIRouter, HTTPException

from ..git_ops import git_ops
from ..models import DiffExplanationRequest, DiffExplanationResponse
from ..services.diff_brain import DiffBrain

router = APIRouter()
diff_brain = DiffBrain()


@router.post("/explain-diff", response_model=DiffExplanationResponse)
async def explain_diff(request: DiffExplanationRequest):
    """Explain the difference between two versions of content"""
    try:
        explanation = await diff_brain.explain_difference(
            request.old_content,
            request.new_content,
            request.file_path,
            request.current_commit,
            request.previous_commit,
            request.context_files,
            request.force_refresh,
        )

        return DiffExplanationResponse(
            explanation=explanation, file_path=request.file_path
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/{path}/last-commit")
async def get_document_last_commit(path: str):
    """Get the last commit that modified a document"""
    try:
        # Use git log to get the last two commits that modified this file
        import subprocess

        repo_path = git_ops.repo_path

        # First check if the file exists
        full_path = os.path.join(repo_path, path)
        if not os.path.exists(full_path):
            raise HTTPException(status_code=404, detail="File not found")

        # Get the last 2 commits for this file
        result = subprocess.run(
            [
                "git",
                "-C",
                repo_path,
                "log",
                "-n",
                "2",
                "--date=iso",
                "--pretty=format:%H|%an|%ae|%ad|%s",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,  # Don't raise an exception if the command fails
        )

        # If the command failed or returned empty result, the file might not be in git yet
        if result.returncode != 0 or not result.stdout.strip():
            return {
                "current_commit": None,
                "previous_commit": None,
                "author": None,
                "date": None,
                "message": "No commit history found",
            }

        commits = result.stdout.strip().split("\n")

        # If we have only one commit, that's the current and there's no previous
        if len(commits) == 1:
            parts = commits[0].split("|")
            if len(parts) >= 5:
                commit_id, author_name, author_email, date_str, message = parts
                return {
                    "current_commit": commit_id,
                    "previous_commit": None,
                    "author": author_name,
                    "date": date_str,
                    "message": message,
                }

        # If we have two or more commits, we can show a diff
        if len(commits) >= 2:
            current_parts = commits[0].split("|")
            previous_parts = commits[1].split("|")

            if len(current_parts) >= 5 and len(previous_parts) >= 5:
                current_id = current_parts[0]
                previous_id = previous_parts[0]
                author = current_parts[1]
                date = current_parts[3]
                message = current_parts[4]

                return {
                    "current_commit": current_id,
                    "previous_commit": previous_id,
                    "author": author,
                    "date": date,
                    "message": message,
                }

        # Fallback if parsing failed
        return {
            "current_commit": None,
            "previous_commit": None,
            "author": None,
            "date": None,
            "message": "Failed to parse commit history",
        }

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Git error: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/{path}/content/{commit_id}")
async def get_document_content_at_version(path: str, commit_id: str):
    """Get document content at a specific version"""
    try:
        # Get file content at specific commit
        import subprocess

        repo_path = git_ops.repo_path

        # Use git show to get file content at commit
        result = subprocess.run(
            ["git", "-C", repo_path, "show", f"{commit_id}:{path}"],
            capture_output=True,
            text=True,
            check=True,
        )

        return {"content": result.stdout, "commit_id": commit_id, "path": path}
    except subprocess.CalledProcessError as e:
        # If file didn't exist at that commit, return empty content
        if "does not exist" in e.stderr:
            return {"content": "", "commit_id": commit_id, "path": path}
        raise HTTPException(status_code=500, detail=f"Git error: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
