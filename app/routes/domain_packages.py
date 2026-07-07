"""API routes for domain package management."""

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services.domain_package_manager import DomainPackageManager

router = APIRouter(prefix="/api/domain-packages", tags=["domain-packages"])

# Initialize package manager
package_manager = DomainPackageManager()


@router.post("/validate")
async def validate_package(file: UploadFile = File(...)):
    """Validate a domain package (zip file or directory structure)."""
    temp_dir = tempfile.mkdtemp()

    try:
        # Save uploaded file
        temp_file = Path(temp_dir) / file.filename
        with open(temp_file, "wb") as f:
            content = await file.read()
            f.write(content)

        # Extract if it's an archive
        if temp_file.suffix in [".zip", ".tar", ".gz"]:
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()

            if temp_file.suffix == ".zip":
                import zipfile

                with zipfile.ZipFile(temp_file, "r") as zf:
                    zf.extractall(extract_dir)
            else:
                import tarfile

                with tarfile.open(temp_file, "r:*") as tf:
                    tf.extractall(extract_dir)

            # Find package directory
            package_dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
            if len(package_dirs) == 1:
                package_path = package_dirs[0]
            else:
                # Look for manifest.yaml
                for d in package_dirs:
                    if (d / "manifest.yaml").exists():
                        package_path = d
                        break
                else:
                    raise ValueError("Could not find package directory")
        else:
            package_path = temp_file.parent

        # Validate package
        result = package_manager.validate_package(package_path)

        return {
            "success": result.is_valid,
            "data": {
                "is_valid": result.is_valid,
                "errors": result.errors,
                "warnings": result.warnings,
                "package_info": result.package_info,
            },
            "message": "Package validation completed",
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@router.post("/install")
async def install_package(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    """Install a domain package."""
    temp_dir = tempfile.mkdtemp()

    try:
        # Save uploaded file
        temp_file = Path(temp_dir) / file.filename
        with open(temp_file, "wb") as f:
            content = await file.read()
            f.write(content)

        # Install package
        result = await package_manager.install_package(temp_file)

        if not result.success:
            raise HTTPException(status_code=400, detail=result.error)

        # Clean up in background
        background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)

        return {
            "success": True,
            "data": {
                "package_name": result.package_name,
                "version": result.version,
                "installed_path": str(result.installed_path),
            },
            "message": f"Successfully installed package '{result.package_name}' version {result.version}",
        }

    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export/{domain_name}")
async def export_domain(
    domain_name: str,
    format: str = "zip",
    author: str | None = None,
    organization: str | None = None,
):
    """Export a domain as a package."""
    if format not in ["zip", "tar.gz"]:
        raise HTTPException(status_code=400, detail="Format must be 'zip' or 'tar.gz'")

    temp_dir = tempfile.mkdtemp()

    try:
        # Prepare metadata
        metadata = {}
        if author:
            metadata["author"] = author
        if organization:
            metadata["organization"] = organization

        # Export domain
        output_file = Path(temp_dir) / f"{domain_name}-export.{format}"
        result = await package_manager.export_domain(
            domain_name, output_file, format=format, metadata=metadata
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.error)

        # Return file
        return FileResponse(
            path=str(result.export_path),
            filename=f"{result.package_name}-{result.version}.{format}",
            media_type="application/octet-stream",
        )

    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/installed")
async def list_installed_packages():
    """List all installed domain packages."""
    packages = package_manager.installed_packages

    return {
        "success": True,
        "data": {"packages": packages},
        "message": f"Found {len(packages)} installed packages",
    }


@router.delete("/{package_name}")
async def uninstall_package(package_name: str):
    """Uninstall a domain package."""
    if package_name not in package_manager.installed_packages:
        raise HTTPException(
            status_code=404, detail=f"Package '{package_name}' not found"
        )

    try:
        # For MVP, just remove from tracking
        # In production, would also clean up files and registry
        del package_manager.installed_packages[package_name]
        package_manager._save_installed_packages()

        return {
            "success": True,
            "message": f"Successfully uninstalled package '{package_name}'",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
