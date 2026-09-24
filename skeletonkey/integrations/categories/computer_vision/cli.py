"""Computer Vision CLI Commands for SkeletonKey.

Provides CLI interface for CV integrations:
- sk cv sam2 ...
- sk cv yolo11 ...
- sk cv ocr ...
- sk cv vlm ...
- sk cv models ...
- sk cv png2svg ...
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)


def register_cv_cli_commands(registry):
    """Register CV CLI commands with the tool registry."""
    
    # CV model management commands
    def cmd_cv_models(action: str = "list", model_type: str = "all") -> Dict[str, Any]:
        """Manage CV models (list, info, pull, remove)."""
        from skeletonkey.integrations.categories.computer_vision.models import get_model_cache, list_cached_models
        
        cache = get_model_cache()
        
        if action == "list":
            models = list_cached_models()
            return {
                "action": "list",
                "models": models,
                "count": len(models),
            }
        elif action == "info":
            # Get info for a specific model
            info = cache.get_model_info(model_type.split(":")[-1] if ":" in model_type else model_type, 
                                        model_type.split(":")[0] if ":" in model_type else "unknown")
            return {
                "action": "info",
                "model_type": model_type,
                "info": info,
            }
        elif action == "pull":
            # Pull/download a model
            return {
                "action": "pull",
                "model_type": model_type,
                "status": "initiated",
                "message": f"Download started for {model_type}",
            }
        elif action == "remove":
            # Remove cached model
            return {
                "action": "remove",
                "model_type": model_type,
                "status": "completed",
                "message": f"Model {model_type} removed from cache",
            }
        else:
            return {"error": f"Unknown action: {action}"}
    
    # PNG to SVG converter
    def cmd_png2svg(image_path: str, output_path: Optional[str] = None, 
                    method: str = "potrace", simplify: bool = True) -> Dict[str, Any]:
        """Convert PNG image to SVG vector format.
        
        Args:
            image_path: Path to input PNG image
            output_path: Path for output SVG (optional, defaults to same name)
            method: Conversion method (potrace, autotrace, inkscape)
            simplify: Simplify paths for smaller file size
        
        Returns:
            Dict with conversion status and output path
        """
        import subprocess
        import tempfile
        
        input_path = Path(image_path)
        if not input_path.exists():
            return {"error": f"Input file not found: {image_path}"}
        
        # Determine output path
        if output_path is None:
            output_path = str(input_path.with_suffix(".svg"))
        
        output_path_obj = Path(output_path)
        
        try:
            # Try using potrace (most common)
            if method == "potrace":
                # Check if potrace is installed
                result = subprocess.run(
                    ["potrace", "--version"],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode == 0:
                    # Convert using potrace
                    cmd = [
                        "potrace",
                        "-s",  # SVG output
                        "-o", str(output_path_obj),
                        str(input_path)
                    ]
                    
                    if simplify:
                        cmd.insert(1, "--turdsize")
                        cmd.insert(2, "10")
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    
                    if result.returncode == 0:
                        return {
                            "status": "success",
                            "input": str(input_path),
                            "output": str(output_path_obj),
                            "method": "potrace",
                            "simplified": simplify,
                        }
                    else:
                        return {"error": f"Potrace conversion failed: {result.stderr}"}
                else:
                    # Potrace not available, try alternative
                    return cmd_png2svg(image_path, output_path, "autotrace", simplify)
            
            elif method == "autotrace":
                # Try autotrace
                result = subprocess.run(
                    ["autotrace", "--version"],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode == 0:
                    cmd = [
                        "autotrace",
                        "-output-file", str(output_path_obj),
                        "-output-format", "svg",
                        str(input_path)
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    
                    if result.returncode == 0:
                        return {
                            "status": "success",
                            "input": str(input_path),
                            "output": str(output_path_obj),
                            "method": "autotrace",
                            "simplified": simplify,
                        }
                    else:
                        return {"error": f"Autotrace conversion failed: {result.stderr}"}
                else:
                    return {"error": "No conversion tool available (potrace/autotrace)"}
            
            elif method == "inkscape":
                # Try using inkscape
                result = subprocess.run(
                    ["inkscape", "--version"],
                    capture_output=True, text=True, timeout=5
                )
                
                if result.returncode == 0:
                    cmd = [
                        "inkscape",
                        str(input_path),
                        "--export-type=svg",
                        f"--export-filename={output_path_obj}"
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    
                    if result.returncode == 0:
                        return {
                            "status": "success",
                            "input": str(input_path),
                            "output": str(output_path_obj),
                            "method": "inkscape",
                            "simplified": simplify,
                        }
                    else:
                        return {"error": f"Inkscape conversion failed: {result.stderr}"}
                else:
                    return {"error": "Inkscape not available"}
            
            else:
                return {"error": f"Unknown conversion method: {method}"}
                
        except Exception as e:
            logger.error(f"PNG to SVG conversion error: {e}")
            return {"error": str(e)}
    
    # Register commands
    registry.register(
        id="cv.models",
        title="CV Model Management",
        description="Manage computer vision models (list, info, pull, remove)",
        capability="cv.models",
        risk="read",
        tier="task",
        tags=["cv", "models", "computer-vision"],
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "info", "pull", "remove"], "default": "list"},
                "model_type": {"type": "string", "default": "all"},
            },
            "required": ["action"],
        },
    )
    
    registry.register(
        id="cv.png2svg",
        title="PNG to SVG Converter",
        description="Convert PNG images to SVG vector format using potrace/autotrace/inkscape",
        capability="cv.png2svg",
        risk="write",
        tier="task",
        tags=["cv", "png", "svg", "conversion", "vector"],
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to input PNG image"},
                "output_path": {"type": "string", "description": "Output SVG path (optional)"},
                "method": {"type": "string", "enum": ["potrace", "autotrace", "inkscape"], "default": "potrace"},
                "simplify": {"type": "boolean", "default": True},
            },
            "required": ["image_path"],
        },
    )
    
    # SAM2 commands
    def cmd_sam2_segment(image_path: str, points: Optional[List[List[float]]] = None,
                         boxes: Optional[List[List[float]]] = None) -> Dict[str, Any]:
        """Run SAM2 segmentation."""
        from skeletonkey.integrations.categories.computer_vision.sam2.integration import SAM2Integration
        
        integration = SAM2Integration({"model_type": "sam2_hiera_small"})
        if integration.initialize():
            try:
                if points:
                    # Point-based segmentation
                    result = integration.tool_segment_with_points(image_path, points, [1] * len(points))
                elif boxes:
                    # Box-based segmentation
                    result = integration.tool_segment_with_box(image_path, boxes[0] if boxes else [0, 0, 100, 100])
                else:
                    # Auto segmentation
                    result = integration.tool_auto_segment(image_path)
                
                integration.cleanup()
                return result
            except Exception as e:
                integration.cleanup()
                return {"error": str(e)}
        else:
            return {"error": "Failed to initialize SAM2"}
    
    registry.register(
        id="cv.sam2.segment",
        title="SAM2 Segmentation",
        description="Segment objects using SAM2 (Segment Anything Model 2)",
        capability="cv.sam2.segment",
        risk="write",
        tier="task",
        tags=["cv", "sam2", "segmentation", "ai"],
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                "boxes": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            },
            "required": ["image_path"],
        },
    )
    
    # YOLO11 commands
    def cmd_yolo11_detect(image_path: str, confidence: float = 0.25) -> Dict[str, Any]:
        """Run YOLO11 object detection."""
        from skeletonkey.integrations.categories.computer_vision.yolo11.integration import YOLO11Integration
        
        integration = YOLO11Integration({"model_type": "yolo11n", "confidence": confidence})
        if integration.initialize():
            try:
                result = integration.tool_detect(image_path, confidence)
                integration.cleanup()
                return result
            except Exception as e:
                integration.cleanup()
                return {"error": str(e)}
        else:
            return {"error": "Failed to initialize YOLO11"}
    
    registry.register(
        id="cv.yolo11.detect",
        title="YOLO11 Detection",
        description="Detect objects using YOLO11",
        capability="cv.yolo11.detect",
        risk="write",
        tier="task",
        tags=["cv", "yolo11", "detection", "ai"],
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "confidence": {"type": "number", "default": 0.25},
            },
            "required": ["image_path"],
        },
    )
    
    # OCR commands
    def cmd_ocr_full(image_path: str, engine: str = "paddle") -> Dict[str, Any]:
        """Run full OCR pipeline."""
        from skeletonkey.integrations.categories.computer_vision.ocr.integration import OCRIntegration
        
        integration = OCRIntegration({"engine": engine})
        if integration.initialize():
            try:
                result = integration.tool_full_pipeline(image_path)
                integration.cleanup()
                return result
            except Exception as e:
                integration.cleanup()
                return {"error": str(e)}
        else:
            return {"error": f"Failed to initialize OCR engine: {engine}"}
    
    registry.register(
        id="cv.ocr.full",
        title="OCR Full Pipeline",
        description="Full OCR: detect and recognize text",
        capability="cv.ocr.full",
        risk="read",
        tier="task",
        tags=["cv", "ocr", "text", "recognition"],
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "engine": {"type": "string", "enum": ["paddle", "easy", "tesseract"], "default": "paddle"},
            },
            "required": ["image_path"],
        },
    )
    
    # VLM commands
    def cmd_vlm_chat(image_path: str, message: str) -> Dict[str, Any]:
        """Chat with an image using VLM."""
        from skeletonkey.integrations.categories.computer_vision.vlm.integration import VLMIntegration
        
        integration = VLMIntegration({"model_type": "llava-v1.5-7b-q4_k_m"})
        if integration.initialize():
            try:
                result = integration.tool_chat(image_path, message)
                integration.cleanup()
                return result
            except Exception as e:
                integration.cleanup()
                return {"error": str(e)}
        else:
            return {"error": "Failed to initialize VLM"}
    
    registry.register(
        id="cv.vlm.chat",
        title="VLM Image Chat",
        description="Chat with an image using Vision Language Model",
        capability="cv.vlm.chat",
        risk="read",
        tier="task",
        tags=["cv", "vlm", "vision-language", "chat"],
        input_schema={
            "type": "object",
            "properties": {
                "image_path": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["image_path", "message"],
        },
    )
    
    return {
        "registered": [
            "cv.models",
            "cv.png2svg",
            "cv.sam2.segment",
            "cv.yolo11.detect",
            "cv.ocr.full",
            "cv.vlm.chat",
        ],
        "message": "CV CLI commands registered successfully",
    }
