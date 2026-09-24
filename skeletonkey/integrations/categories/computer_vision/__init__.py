"""Computer Vision Integrations for SkeletonKey.

Provides SAM2, YOLO11, OCR, and VLM (Vision Language Model) integrations
with model management, caching, and inference tools.

Integrations:
- sam2: Segment Anything Model 2 (segmentation)
- yolo11: Ultralytics YOLO11 (detection, segmentation, pose, etc.)
- ocr: Optical Character Recognition (PaddleOCR, EasyOCR, Tesseract)
- vlm: Vision Language Models (LLaVA, Qwen-VL, Phi-3-Vision, etc.)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .base import CVIntegrationBase, CVModelInfo, CVDeviceInfo

logger = logging.getLogger(__name__)

# Default model cache directory
DEFAULT_MODEL_CACHE = Path.home() / ".sk" / "models" / "computer-vision"

# Available CV integrations registry
_available_integrations: dict[str, type[CVIntegrationBase]] = {}

CV_INTEGRATION_DIR = Path(__file__).parent


def register_cv_integration(integration_class: type[CVIntegrationBase]) -> type[CVIntegrationBase]:
    """Register a CV integration class."""
    name = integration_class._integration_name
    _available_integrations[name] = integration_class
    logger.debug(f"Registered CV integration: {name}")
    return integration_class


def get_cv_integration(name: str) -> type[CVIntegrationBase] | None:
    """Get a registered CV integration class by name."""
    return _available_integrations.get(name)


def list_cv_integrations() -> list[str]:
    """List all registered CV integration names."""
    return list(_available_integrations.keys())


def discover_cv_integrations() -> list[type[CVIntegrationBase]]:
    """Discover CV integrations from the categories directory and subdirectories."""
    integrations = []
    
    # Check subdirectories (sam2, yolo11, ocr, vlm)
    for subdir in CV_INTEGRATION_DIR.iterdir():
        if subdir.is_dir() and subdir.name not in ("__pycache__", ".git"):
            # Look for integration.py in subdirectory
            integration_file = subdir / "integration.py"
            if integration_file.exists():
                try:
                    module_name = f"skeletonkey.integrations.categories.computer_vision.{subdir.name}.integration"
                    module = __import__(module_name, fromlist=[""])
                    
                    # Check for registered classes
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type) and 
                            issubclass(attr, CVIntegrationBase) and 
                            attr is not CVIntegrationBase):
                            integrations.append(attr)
                except Exception as e:
                    logger.warning(f"Failed to load CV integration from {subdir.name}: {e}")
    
    # Also check .py files directly in directory
    if CV_INTEGRATION_DIR.exists():
        for py_file in CV_INTEGRATION_DIR.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            try:
                module_name = f"skeletonkey.integrations.categories.computer_vision.{py_file.stem}"
                module = __import__(module_name, fromlist=[""])
                
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and 
                        issubclass(attr, CVIntegrationBase) and 
                        attr is not CVIntegrationBase):
                        integrations.append(attr)
            except Exception as e:
                logger.warning(f"Failed to load CV integration from {py_file}: {e}")
    
    return integrations


# Auto-discover integrations on import
_discovered = discover_cv_integrations()
for integ in _discovered:
    register_cv_integration(integ)

logger.info(f"Discovered {len(_discovered)} CV integrations")