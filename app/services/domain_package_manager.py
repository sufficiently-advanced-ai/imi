"""Domain Package Manager for packaging, distributing, and deploying domain configurations."""

import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from app.config import get_settings
except ImportError:
    # Fallback for testing without full environment
    class MockSettings:
        DOMAINS_DIR = Path("/tmp/domains")
        PACKAGES_DIR = Path("/tmp/packages")

    def get_settings():
        return MockSettings()


from pydantic import BaseModel

from app.services.domain_registry import domain_registry

# Configure logging
logger = logging.getLogger(__name__)


class ValidationResult(BaseModel):
    """Result of package validation."""

    is_valid: bool
    errors: list[str]
    warnings: list[str] = []
    package_info: dict[str, Any] = {}


class InstallResult(BaseModel):
    """Result of package installation."""

    success: bool
    package_name: str = ""
    version: str = ""
    installed_path: Path | None = None
    error: str = ""


class ExportResult(BaseModel):
    """Result of package export."""

    success: bool
    export_path: Path | None = None
    package_name: str = ""
    version: str = ""
    error: str = ""


class PackageDependency(BaseModel):
    """Represents a package dependency."""

    name: str
    version: str

    def matches_version(self, installed_version: str) -> bool:
        """Check if installed version satisfies dependency."""
        # Simple version matching - can be enhanced with semver
        if self.version.startswith(">="):
            required = self.version[2:]
            return self._compare_versions(installed_version, required) >= 0
        elif self.version.startswith(">"):
            required = self.version[1:]
            return self._compare_versions(installed_version, required) > 0
        elif self.version.startswith("<="):
            required = self.version[2:]
            return self._compare_versions(installed_version, required) <= 0
        elif self.version.startswith("<"):
            required = self.version[1:]
            return self._compare_versions(installed_version, required) < 0
        else:
            # Exact match
            return installed_version == self.version

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare two semantic versions."""
        try:
            # Handle pre-release versions by stripping them
            v1_clean = v1.split("-")[0].split("+")[0]
            v2_clean = v2.split("-")[0].split("+")[0]

            parts1 = [int(x) for x in v1_clean.split(".")]
            parts2 = [int(x) for x in v2_clean.split(".")]

            # Pad with zeros to same length
            max_len = max(len(parts1), len(parts2))
            parts1.extend([0] * (max_len - len(parts1)))
            parts2.extend([0] * (max_len - len(parts2)))

            for p1, p2 in zip(parts1, parts2, strict=False):
                if p1 < p2:
                    return -1
                elif p1 > p2:
                    return 1

            # If base versions are equal, compare pre-release
            if "-" in v1 or "-" in v2:
                # Version without pre-release is greater
                if "-" not in v1 and "-" in v2:
                    return 1
                elif "-" in v1 and "-" not in v2:
                    return -1
                # Both have pre-release, compare lexically
                else:
                    pre1 = v1.split("-")[1] if "-" in v1 else ""
                    pre2 = v2.split("-")[1] if "-" in v2 else ""
                    return -1 if pre1 < pre2 else (1 if pre1 > pre2 else 0)

            return 0
        except (ValueError, IndexError) as e:
            logger.warning(f"Failed to compare versions {v1} and {v2}: {str(e)}")
            # Fallback to string comparison
            return -1 if v1 < v2 else (1 if v1 > v2 else 0)


class DomainPackageManager:
    """Manages domain package operations."""

    REQUIRED_MANIFEST_FIELDS = ["name", "version", "description", "author"]
    REQUIRED_FILES = ["manifest.yaml", "domain.yaml"]
    EXCLUDED_FILES = {".git", ".gitignore", "__pycache__", ".DS_Store", "*.pyc", ".env"}
    VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    MAX_EXTRACTED_SIZE = 500 * 1024 * 1024  # 500MB

    def __init__(self):
        """Initialize the package manager."""
        self.settings = get_settings()
        self.domains_dir = Path(self.settings.DOMAINS_DIR)
        self.packages_dir = Path(self.settings.PACKAGES_DIR)
        self.installed_packages: dict[str, dict[str, Any]] = {}
        self._load_installed_packages()

    def _load_installed_packages(self):
        """Load information about installed packages."""
        if self.packages_dir and self.packages_dir.exists():
            packages_file = self.packages_dir / "installed.json"
            if packages_file.exists():
                with open(packages_file) as f:
                    self.installed_packages = json.load(f)

    def _save_installed_packages(self):
        """Save information about installed packages."""
        if self.packages_dir:
            self.packages_dir.mkdir(exist_ok=True)
            packages_file = self.packages_dir / "installed.json"
            with open(packages_file, "w") as f:
                json.dump(self.installed_packages, f, indent=2)

    def _validate_archive_safety(self, archive_path: Path) -> tuple[bool, str]:
        """Validate archive file for security issues."""
        # Check file size
        file_size = archive_path.stat().st_size
        if file_size > self.MAX_FILE_SIZE:
            return (
                False,
                f"Archive too large: {file_size} bytes (max: {self.MAX_FILE_SIZE})",
            )

        # Check extracted size to prevent zip bombs
        total_extracted_size = 0

        if archive_path.suffix == ".zip":
            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    for info in zf.infolist():
                        # Check for path traversal
                        if os.path.isabs(info.filename) or ".." in info.filename:
                            return False, f"Unsafe path in archive: {info.filename}"
                        total_extracted_size += info.file_size
            except Exception as e:
                return False, f"Failed to validate zip file: {str(e)}"

        elif archive_path.suffix in [".tar", ".gz"]:
            try:
                with tarfile.open(archive_path, "r:*") as tf:
                    for member in tf.getmembers():
                        # Check for path traversal
                        if os.path.isabs(member.name) or ".." in member.name:
                            return False, f"Unsafe path in archive: {member.name}"
                        if member.isfile():
                            total_extracted_size += member.size
            except Exception as e:
                return False, f"Failed to validate tar file: {str(e)}"

        if total_extracted_size > self.MAX_EXTRACTED_SIZE:
            ratio = total_extracted_size / file_size
            if ratio > 10:  # Potential zip bomb if compression ratio > 10:1
                return (
                    False,
                    f"Suspicious compression ratio ({ratio:.1f}:1), possible zip bomb",
                )

        return True, ""

    def validate_package(self, package_path: Path) -> ValidationResult:
        """Validate a domain package structure and contents."""
        errors = []
        warnings = []
        package_info = {}

        # Check if path exists
        if not package_path.exists():
            errors.append(f"Package path does not exist: {package_path}")
            return ValidationResult(is_valid=False, errors=errors)

        # Check manifest.yaml
        manifest_path = package_path / "manifest.yaml"
        if not manifest_path.exists():
            errors.append("manifest.yaml not found")
        else:
            try:
                with open(manifest_path) as f:
                    manifest = yaml.safe_load(f)
                    package_info = manifest.copy()

                # Check required fields
                for field in self.REQUIRED_MANIFEST_FIELDS:
                    if field not in manifest:
                        errors.append(f"Missing required field: {field}")

                # Validate version format
                if "version" in manifest:
                    if not self.VERSION_PATTERN.match(manifest["version"]):
                        errors.append(
                            "Invalid version format. Use semantic versioning (e.g., 1.0.0)"
                        )

            except Exception as e:
                errors.append(f"Error parsing manifest.yaml: {str(e)}")

        # Check domain.yaml
        domain_path = package_path / "domain.yaml"
        if not domain_path.exists():
            errors.append("domain.yaml not found")
        else:
            try:
                with open(domain_path) as f:
                    domain = yaml.safe_load(f)
                    if "name" not in domain:
                        errors.append("domain.yaml missing 'name' field")
            except Exception as e:
                errors.append(f"Error parsing domain.yaml: {str(e)}")

        # Check directory structure
        expected_dirs = ["prompts", "workflows"]
        for dir_name in expected_dirs:
            if not (package_path / dir_name).exists():
                warnings.append(f"Expected directory '{dir_name}' not found")

        # Validate prompt files
        prompts_dir = package_path / "prompts"
        if prompts_dir.exists():
            for prompt_file in prompts_dir.glob("*.xml"):
                try:
                    content = prompt_file.read_text()
                    if not content.strip().startswith("<prompt"):
                        errors.append(f"Invalid prompt file format: {prompt_file.name}")
                except Exception as e:
                    errors.append(
                        f"Error reading prompt file {prompt_file.name}: {str(e)}"
                    )

        # Check for non-XML files in prompts
        if prompts_dir.exists():
            for file in prompts_dir.iterdir():
                if file.is_file() and not file.suffix == ".xml":
                    errors.append(
                        f"Invalid prompt file format: {file.name} (expected .xml)"
                    )

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            package_info=package_info,
        )

    async def install_package(self, package_source: Path | str) -> InstallResult:
        """Install a domain package from a directory or archive."""
        package_path = Path(package_source)
        temp_dir = None
        installation_steps = []

        try:
            # Handle archive files
            if package_path.is_file():
                # Validate archive safety first
                is_safe, error_msg = self._validate_archive_safety(package_path)
                if not is_safe:
                    logger.warning(f"Archive validation failed: {error_msg}")
                    return InstallResult(success=False, error=error_msg)

                temp_dir = tempfile.mkdtemp()
                extracted_path = Path(temp_dir)
                logger.info(f"Extracting package to {temp_dir}")

                if package_path.suffix == ".zip":
                    with zipfile.ZipFile(package_path, "r") as zf:
                        zf.extractall(extracted_path)
                elif package_path.suffix in [".tar", ".gz"]:
                    with tarfile.open(package_path, "r:*") as tf:
                        tf.extractall(extracted_path)
                else:
                    return InstallResult(
                        success=False,
                        error=f"Unsupported archive format: {package_path.suffix}",
                    )

                # Find the package directory (might be nested)
                package_dirs = [d for d in extracted_path.iterdir() if d.is_dir()]
                if len(package_dirs) == 1:
                    package_path = package_dirs[0]
                else:
                    # Look for manifest.yaml
                    for d in package_dirs:
                        if (d / "manifest.yaml").exists():
                            package_path = d
                            break
                    else:
                        return InstallResult(
                            success=False,
                            error="Could not find package directory in archive",
                        )

            # Validate package
            validation = self.validate_package(package_path)
            if not validation.is_valid:
                return InstallResult(
                    success=False,
                    error=f"Package validation failed: {'; '.join(validation.errors)}",
                )

            package_info = validation.package_info
            package_name = package_info["name"]
            package_version = package_info["version"]

            # Check if already installed
            target_dir = self.domains_dir / package_name
            if target_dir.exists():
                return InstallResult(
                    success=False, error=f"Domain '{package_name}' already exists"
                )

            # Check dependencies
            if "dependencies" in package_info:
                deps_ok, deps_errors = self.check_dependencies(
                    package_info["dependencies"]
                )
                if not deps_ok:
                    return InstallResult(
                        success=False,
                        error=f"Dependency check failed: {'; '.join(deps_errors)}",
                    )

            # Perform installation with proper rollback tracking
            try:
                # Step 1: Copy package files
                logger.info(f"Copying package files to {target_dir}")
                shutil.copytree(package_path, target_dir)
                installation_steps.append(("directory_created", target_dir))

                # Step 2: Register domain
                logger.info(f"Registering domain '{package_name}'")
                domain_registry.register_domain(package_name, target_dir)
                installation_steps.append(("domain_registered", package_name))

                # Step 3: Record installation
                logger.info("Recording package installation")
                self.installed_packages[package_name] = {
                    "version": package_version,
                    "installed_at": datetime.now().isoformat(),
                    "path": str(target_dir),
                }
                self._save_installed_packages()
                installation_steps.append(("package_recorded", package_name))

            except Exception as e:
                logger.error(f"Installation failed: {str(e)}")
                # Rollback in reverse order
                for step_type, step_data in reversed(installation_steps):
                    try:
                        logger.info(f"Rolling back: {step_type}")
                        if step_type == "directory_created":
                            shutil.rmtree(step_data, ignore_errors=True)
                        elif step_type == "domain_registered":
                            if hasattr(domain_registry, "unregister_domain"):
                                domain_registry.unregister_domain(step_data)
                        elif step_type == "package_recorded":
                            if step_data in self.installed_packages:
                                del self.installed_packages[step_data]
                                self._save_installed_packages()
                    except Exception as rollback_error:
                        logger.error(
                            f"Rollback failed for {step_type}: {str(rollback_error)}"
                        )

                return InstallResult(
                    success=False, error=f"Installation failed: {str(e)}"
                )

            logger.info(
                f"Successfully installed package '{package_name}' version {package_version}"
            )
            return InstallResult(
                success=True,
                package_name=package_name,
                version=package_version,
                installed_path=target_dir,
            )

        except Exception as e:
            logger.error(f"Unexpected error during installation: {str(e)}")
            return InstallResult(success=False, error=f"Installation failed: {str(e)}")
        finally:
            if temp_dir:
                logger.debug(f"Cleaning up temporary directory: {temp_dir}")
                shutil.rmtree(temp_dir, ignore_errors=True)

    def check_dependencies(
        self, dependencies: list[dict[str, Any]]
    ) -> tuple[bool, list[str]]:
        """Check if all dependencies are satisfied."""
        errors = []

        for dep_info in dependencies:
            try:
                dep = PackageDependency(**dep_info)
            except Exception as e:
                errors.append(
                    f"Invalid dependency specification: {dep_info} - {str(e)}"
                )
                continue

            if dep.name not in self.installed_packages:
                errors.append(
                    f"Missing dependency: {dep.name} (required: {dep.version})"
                )
                logger.warning(f"Dependency {dep.name} not found in installed packages")
                continue

            installed_version = self.installed_packages[dep.name].get(
                "version", "unknown"
            )
            if not dep.matches_version(installed_version):
                errors.append(
                    f"Version mismatch for {dep.name}: required {dep.version}, "
                    f"but {installed_version} is installed"
                )
                logger.warning(
                    f"Dependency version mismatch: {dep.name} requires {dep.version}, "
                    f"but {installed_version} is installed"
                )

        if errors:
            logger.info(f"Dependency check failed with {len(errors)} error(s)")
        else:
            logger.info("All dependencies satisfied")

        return len(errors) == 0, errors

    async def export_domain(
        self,
        domain_name: str,
        output_path: Path | str,
        format: str = "directory",
        metadata: dict[str, Any] | None = None,
    ) -> ExportResult:
        """Export a domain as a distributable package."""
        output_path = Path(output_path)
        domain_path = self.domains_dir / domain_name

        # Check if domain exists
        if not domain_path.exists():
            return ExportResult(success=False, error=f"Domain not found: {domain_name}")

        try:
            # Load domain configuration
            domain_config_path = domain_path / "domain.yaml"
            if not domain_config_path.exists():
                return ExportResult(
                    success=False, error="domain.yaml not found in domain directory"
                )

            with open(domain_config_path) as f:
                domain_config = yaml.safe_load(f)

            # Determine version
            version = domain_config.get("version", "0.1.0")

            # Create manifest
            manifest = {
                "name": domain_name,
                "version": version,
                "description": domain_config.get(
                    "description", f"Domain package for {domain_name}"
                ),
                "author": "System Export",
                "exported_at": datetime.now().isoformat(),
                "dependencies": [],
            }

            # Add custom metadata
            if metadata:
                manifest.update(metadata)

            # Export based on format
            if format == "directory":
                # Create output directory
                output_path.mkdir(parents=True, exist_ok=True)

                # Write manifest
                with open(output_path / "manifest.yaml", "w") as f:
                    yaml.dump(manifest, f, default_flow_style=False)

                # Copy domain files
                for item in domain_path.iterdir():
                    if item.name not in self.EXCLUDED_FILES and not any(
                        item.match(pattern) for pattern in self.EXCLUDED_FILES
                    ):
                        if item.is_dir():
                            shutil.copytree(item, output_path / item.name)
                        else:
                            shutil.copy2(item, output_path / item.name)

                final_path = output_path

            elif format in ["zip", "tar.gz"]:
                # Create temporary directory
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir) / domain_name

                    # Export to temp directory first
                    export_result = await self.export_domain(
                        domain_name, temp_path, format="directory", metadata=metadata
                    )

                    if not export_result.success:
                        return export_result

                    # Create archive
                    if format == "zip":
                        with zipfile.ZipFile(
                            output_path, "w", zipfile.ZIP_DEFLATED
                        ) as zf:
                            for file_path in temp_path.rglob("*"):
                                if file_path.is_file():
                                    arcname = str(
                                        file_path.relative_to(temp_path.parent)
                                    )
                                    zf.write(file_path, arcname)
                    else:  # tar.gz
                        with tarfile.open(output_path, "w:gz") as tf:
                            tf.add(temp_path, arcname=domain_name)

                final_path = output_path

            else:
                return ExportResult(
                    success=False, error=f"Unsupported format: {format}"
                )

            return ExportResult(
                success=True,
                export_path=final_path,
                package_name=domain_name,
                version=version,
            )

        except Exception as e:
            return ExportResult(success=False, error=f"Export failed: {str(e)}")
