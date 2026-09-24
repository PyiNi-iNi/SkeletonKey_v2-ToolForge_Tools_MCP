"""Base class for Computer Vision integrations.

Provides common functionality for:
- Model management (download, cache, verification)
- Device handling (CUDA/MPS/CPU)
- Result formatting
- Tool registration helpers
"""

from __future__ import annotations

import abc
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple, Union

# Conditional torch import - only needed for device detection
try:
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    torch = None  # type: ignore
    _TORCH_AVAILABLE = False

from skeletonkey.integrations import Integration, IntegrationInfo, IntegrationType, IntegrationCategory


class CVModelInfo:
    """Metadata about a CV model."""
    
    def __init__(
        self,
        name: str,
        version: str,
        task: str,  # "segmentation", "detection", "classification", "ocr", "captioning"
        model_type: str,  # "sam2", "yolo", "paddleocr", "easyocr", "tesseract", "llava", etc.
        size_mb: float,
        download_url: str,
        hash_sha256: str,
        description: str = "",
        min_torch_version: str = "2.0.0",
        recommended_device: str = "cpu",
        tags: list[str] | None = None,
    ):
        self.name = name
        self.version = version
        self.task = task
        self.model_type = model_type
        self.size_mb = size_mb
        self.download_url = download_url
        self.hash_sha256 = hash_sha256
        self.description = description
        self.min_torch_version = min_torch_version
        self.recommended_device = recommended_device
        self.tags = tags or []
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "task": self.task,
            "model_type": self.model_type,
            "size_mb": self.size_mb,
            "download_url": self.download_url,
            "hash_sha256": self.hash_sha256,
            "description": self.description,
            "min_torch_version": self.min_torch_version,
            "recommended_device": self.recommended_device,
            "tags": self.tags,
        }


class CVDeviceInfo:
    """Information about the computation device."""
    
    def __init__(self, device: str, is_cuda: bool, is_mps: bool, 
                 total_memory_mb: Optional[float] = None,
                 available_memory_mb: Optional[float] = None):
        self.device = device
        self.is_cuda = is_cuda
        self.is_mps = is_mps
        self.total_memory_mb = total_memory_mb
        self.available_memory_mb = available_memory_mb
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "is_cuda": self.is_cuda,
            "is_mps": self.is_mps,
            "total_memory_mb": self.total_memory_mb,
            "available_memory_mb": self.available_memory_mb,
        }


class CVIntegrationBase(Integration):
    """Abstract base class for all Computer Vision integrations.
    
    Subclasses should implement:
    - initialize(): Load the model
    - cleanup(): Unload the model
    - get_tools(): Return tool definitions
    - tool_*(): Individual tool handlers
    - get_model_info(): Return CVModelInfo
    """
    
    _integration_type: IntegrationType = IntegrationType.MODEL_SERVING
    _integration_category: IntegrationCategory = IntegrationCategory.COMPUTER_VISION
    
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._model: Any = None
        self._model_info: Optional[CVModelInfo] = None
        self._device: Optional[CVDeviceInfo] = None
        self._model_cache_dir: Path = DEFAULT_MODEL_CACHE
        self._loaded: bool = False
    
    @property
    def model_info(self) -> CVModelInfo:
        """Get model metadata."""
        if self._model_info is None:
            raise RuntimeError("Model not initialized - call initialize() first")
        return self._model_info
    
    @property
    def device(self) -> CVDeviceInfo:
        """Get device information."""
        if self._device is None:
            raise RuntimeError("Device not set - call initialize() first")
        return self._device
    
    @property
    def loaded(self) -> bool:
        """Check if the model is loaded."""
        return self._loaded
    
    @abc.abstractmethod
    def initialize(self) -> bool:
        """Initialize/load the CV model.
        
        Returns:
            bool: True if initialization succeeded
        """
        pass
    
    @abc.abstractmethod
    def cleanup(self) -> None:
        """Clean up/unload the CV model."""
        pass
    
    def _detect_device(self) -> CVDeviceInfo:
        """Automatically detect the best available device."""
        if not _TORCH_AVAILABLE or torch is None:
            # Fallback when torch is not installed
            return CVDeviceInfo(
                device="cpu",
                is_cuda=False,
                is_mps=False,
                total_memory_mb=0.0,
                available_memory_mb=0.0,
            )
        
        is_cuda = torch.cuda.is_available()
        is_mps = getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available()
        
        if is_cuda:
            total_memory = torch.cuda.get_device_properties(0).total_memory / (1024 ** 2)
            available_memory = torch.cuda.memory_reserved(0) / (1024 ** 2) if torch.cuda.is_available() else 0
            device = f"cuda:{torch.cuda.current_device()}"
        elif is_mps:
            total_memory = 8192.0  # MPS memory estimate
            available_memory = 4096.0
            device = "mps"
        else:
            total_memory = 0.0
            available_memory = 0.0
            device = "cpu"
        
        return CVDeviceInfo(
            device=device,
            is_cuda=is_cuda,
            is_mps=is_mps,
            total_memory_mb=total_memory,
            available_memory_mb=available_memory,
        )
    
    def _compute_hash(self, file_path: Path) -> str:
        """Compute SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(8192), b""):
                sha256_hash.update(block)
        return sha256_hash.hexdigest()
    
    def _download_model(self, url: str, dest: Path, progress: bool = True) -> bool:
        """Download a model file with progress tracking."""
        import urllib.request
        
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            
            def reporthook(block_num, block_size, total_size):
                if progress:
                    read = block_num * block_size
                    percent = min(read * 100 / total_size, 100) if total_size > 0 else 0
                    sys.stderr.write(f"\rDownloading {dest.name}: {percent:.1f}%")
                    sys.stderr.flush()
            
            urllib.request.urlretrieve(url, dest, reporthook)
            sys.stderr.write("\n")
            return True
        except Exception as e:
            logger.error(f"Failed to download model from {url}: {e}")
            # Clean up partial download
            if dest.exists():
                dest.unlink()
            return False
    
    def _verify_hash(self, file_path: Path, expected_hash: str) -> bool:
        """Verify file hash matches expected."""
        actual_hash = self._compute_hash(file_path)
        return actual_hash == expected_hash
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of CV tools provided by this integration.
        
        Returns:
            list: List of tool definitions (ToolManifest format)
        """
        # This will be overridden by subclasses
        return []
    
    def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """Dispatch a CV tool call."""
        method = getattr(self, f"tool_{name}", None)
        if not callable(method):
            from ..core.errors import SkeletonKeyError
            from ..core.errors import E
            raise E.BAD_ARGS(f"CV integration has no handler for tool {name!r}")
        return method(**args)


def cv_tool(
    risk: str = "read",
    tier: str = "task",
    description: str = "",
    capability: str | None = None,
    tags: list[str] | None = None,
) -> callable:
    """Decorator to register a CV tool handler method.
    
    This decorator creates a ToolManifest from the function signature and
    registers it with the integration's registry.
    
    Args:
        risk: Risk level (none, read, write, destructive)
        tier: Tier (core, task, full)
        description: Tool description
        capability: Capability string (auto-generated if None)
        tags: List of tags
    
    Returns:
        Decorator function
    """
    def decorator(func):
        # Set tool metadata on the function
        func._cv_risk = risk
        func._cv_tier = tier
        func._cv_description = description or func.__doc__ or ""
        func._cv_capability = capability or f"cv.{func.__name__}"
        func._cv_tags = tags or []
        
        # Generate tool ID from function name
        tool_id = f"cv.{func.__name__}"
        func._cv_tool_id = tool_id
        
        return func
    return decorator