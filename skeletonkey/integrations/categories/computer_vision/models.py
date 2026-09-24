"""Computer Vision Model Management.

Provides model download, caching, verification, and registry
for all CV integrations (SAM2, YOLO11, OCR, VLM).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


class CVModelCache:
    """Manages CV model downloads, caching, and verification."""
    
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path.home() / ".sk" / "models" / "computer-vision"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Registry file
        self.registry_file = self.cache_dir / "model_registry.json"
        self._load_registry()
    
    def _load_registry(self) -> None:
        """Load model registry from file."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r") as f:
                    self.registry = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load model registry: {e}")
                self.registry = {}
        else:
            self.registry = {}
    
    def _save_registry(self) -> None:
        """Save model registry to file."""
        try:
            with open(self.registry_file, "w") as f:
                json.dump(self.registry, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save model registry: {e}")
    
    def get_model_path(self, model_name: str, model_type: str = "unknown") -> Path:
        """Get the cached path for a model."""
        model_dir = self.cache_dir / model_type
        model_dir.mkdir(parents=True, exist_ok=True)
        return model_dir / f"{model_name}.pt"
    
    def is_model_cached(self, model_name: str, model_type: str = "unknown") -> bool:
        """Check if a model is cached."""
        model_path = self.get_model_path(model_name, model_type)
        return model_path.exists()
    
    def get_model_info(self, model_name: str, model_type: str = "unknown") -> Optional[Dict]:
        """Get cached model metadata."""
        key = f"{model_type}:{model_name}"
        return self.registry.get(key)
    
    def register_model(self, model_name: str, model_type: str, info: Dict) -> None:
        """Register a model in the registry."""
        key = f"{model_type}:{model_name}"
        info["cached_at"] = time.time()
        info["cached_path"] = str(self.get_model_path(model_name, model_type))
        self.registry[key] = info
        self._save_registry()
    
    def verify_model_hash(self, model_path: Path, expected_hash: str) -> bool:
        """Verify model file hash."""
        if expected_hash == "placeholder_hash" or not expected_hash:
            return True  # Skip verification for placeholder hashes
        
        try:
            sha256_hash = hashlib.sha256()
            with open(model_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256_hash.update(chunk)
            actual_hash = sha256_hash.hexdigest()
            return actual_hash == expected_hash
        except Exception as e:
            logger.error(f"Hash verification error: {e}")
            return False
    
    def clean_old_models(self, max_age_days: int = 30) -> int:
        """Clean models older than max_age_days."""
        cleaned = 0
        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 3600
        
        for key, info in list(self.registry.items()):
            cached_at = info.get("cached_at", 0)
            if current_time - cached_at > max_age_seconds:
                cached_path = info.get("cached_path")
                if cached_path and Path(cached_path).exists():
                    try:
                        Path(cached_path).unlink()
                        cleaned += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete old model {cached_path}: {e}")
                
                # Remove from registry
                del self.registry[key]
        
        if cleaned > 0:
            self._save_registry()
        
        return cleaned


# Global model cache instance
_model_cache = None


def get_model_cache() -> CVModelCache:
    """Get the global CV model cache instance."""
    global _model_cache
    if _model_cache is None:
        _model_cache = CVModelCache()
    return _model_cache


def download_model(url: str, dest_path: Path, progress: bool = True) -> bool:
    """Download a model file with progress tracking."""
    import urllib.request
    
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        def reporthook(block_num, block_size, total_size):
            if progress and total_size > 0:
                read = block_num * block_size
                percent = min(read * 100 / total_size, 100)
                # Log progress at intervals
                if block_num % 10 == 0 or percent >= 100:
                    logger.info(f"Downloading: {percent:.1f}% ({read / (1024*1024):.1f}MB / {total_size / (1024*1024):.1f}MB)")
        
        urllib.request.urlretrieve(url, str(dest_path), reporthook)
        logger.info(f"Download complete: {dest_path}")
        return True
        
    except Exception as e:
        logger.error(f"Download failed: {e}")
        # Clean up partial download
        if dest_path.exists():
            try:
                dest_path.unlink()
            except:
                pass
        return False


def list_cached_models() -> List[Dict]:
    """List all cached CV models."""
    cache = get_model_cache()
    models = []
    
    for key, info in cache.registry.items():
        model_type, model_name = key.split(":", 1)
        cached_path = info.get("cached_path")
        
        if cached_path and Path(cached_path).exists():
            file_size = Path(cached_path).stat().st_size / (1024 * 1024)  # MB
            models.append({
                "name": model_name,
                "type": model_type,
                "cached_path": cached_path,
                "file_size_mb": file_size,
                "cached_at": info.get("cached_at"),
                "hash_verified": info.get("hash_verified", False),
            })
    
    return models


def get_model_size(model_path: Path) -> float:
    """Get model file size in MB."""
    if model_path.exists():
        return model_path.stat().st_size / (1024 * 1024)
    return 0.0
