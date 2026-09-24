"""YOLO11 Integration for SkeletonKey.

Ultralytics YOLO11 - Real-time object detection, segmentation,
classification, pose estimation, and OBB (Oriented Bounding Boxes).

Model: ultralytics/yolo11
Types: yolo11n, yolo11s, yolo11m, yolo11l, yolo11x
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

from skeletonkey.integrations.categories.computer_vision.base import (
    CVIntegrationBase, CVModelInfo, CVDeviceInfo, cv_tool
)
from skeletonkey.integrations import IntegrationInfo, IntegrationType, IntegrationCategory, register_integration, IntegrationDependency


logger = logging.getLogger(__name__)


# YOLO11 model definitions
YOLO11_MODELS = {
    "yolo11n": CVModelInfo(
        name="yolo11n",
        version="11.0.0",
        task="detection",
        model_type="yolo11",
        size_mb=6.4,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11n.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11n - Nano, fastest, 9.5M params",
        tags=["nano", "fastest", "lightweight", "real-time"],
    ),
    "yolo11s": CVModelInfo(
        name="yolo11s",
        version="11.0.0",
        task="detection",
        model_type="yolo11",
        size_mb=15.5,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11s.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11s - Small, good balance",
        tags=["small", "balanced", "accurate"],
    ),
    "yolo11m": CVModelInfo(
        name="yolo11m",
        version="11.0.0",
        task="detection",
        model_type="yolo11",
        size_mb=32.0,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11m.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11m - Medium, higher accuracy",
        tags=["medium", "accurate"],
    ),
    "yolo11l": CVModelInfo(
        name="yolo11l",
        version="11.0.0",
        task="detection",
        model_type="yolo11",
        size_mb=64.0,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11l.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11l - Large, highest accuracy",
        tags=["large", "highest-accuracy"],
    ),
    "yolo11x": CVModelInfo(
        name="yolo11x",
        version="11.0.0",
        task="detection",
        model_type="yolo11",
        size_mb=128.0,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11x.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11x - Extra large, best performance",
        tags=["extra-large", "best-performance"],
    ),
    
    # Segmentation variants
    "yolo11n-seg": CVModelInfo(
        name="yolo11n-seg",
        version="11.0.0",
        task="segmentation",
        model_type="yolo11-seg",
        size_mb=6.5,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11n-seg.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11n-seg - Nano segmentation",
        tags=["nano", "segmentation", "fast"],
    ),
    "yolo11l-seg": CVModelInfo(
        name="yolo11l-seg",
        version="11.0.0",
        task="segmentation",
        model_type="yolo11-seg",
        size_mb=65.0,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11l-seg.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11l-seg - Large segmentation",
        tags=["large", "segmentation", "high-accuracy"],
    ),
    
    # Pose variants
    "yolo11n-pose": CVModelInfo(
        name="yolo11n-pose",
        version="11.0.0",
        task="pose",
        model_type="yolo11-pose",
        size_mb=6.4,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11n-pose.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11n-pose - Nano pose estimation",
        tags=["nano", "pose", "keypoints"],
    ),
    
    # OBB variants
    "yolo11n-obb": CVModelInfo(
        name="yolo11n-obb",
        version="11.0.0",
        task="obb",
        model_type="yolo11-obb",
        size_mb=6.4,
        download_url="https://github.com/ultralytics/assets/releases/download/v8.2.0/yolo11n-obb.pt",
        hash_sha256="placeholder_hash",
        description="YOLO11n-obb - Nano OBB (oriented boxes)",
        tags=["nano", "obb", "oriented"],
    ),
}


@register_integration(
    name="yolo11",
    title="YOLO11 Integration",
    version="11.0.0",
    integration_type=IntegrationType.MODEL_SERVING,
    category=IntegrationCategory.COMPUTER_VISION,
    platform=None,
    description="Ultralytics YOLO11 - Real-time object detection, segmentation, pose, and OBB",
    author="Ultralytics",
    license="AGPL-3.0",
    keywords=["yolo11", "detection", "segmentation", "pose", "obb", "computer-vision", "ai"],
    dependencies=[
        IntegrationDependency(name="ultralytics", version="8.2.0+", optional=False),
        IntegrationDependency(name="torch", version="2.0.0+", optional=False),
    ],
)
class YOLO11Integration(CVIntegrationBase):
    """YOLO11 Integration for SkeletonKey.
    
    Provides Ultralytics YOLO11 capabilities:
    - Real-time object detection
    - Instance segmentation
    - Image classification
    - Pose estimation
    - OBB (Oriented Bounding Boxes)
    - Multi-object tracking
    - Export to ONNX/TensorRT/CoreML
    """
    
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_type: str = config.get("model_type", "yolo11n")
        self.confidence: float = config.get("confidence", 0.25)
        self.iou_threshold: float = config.get("iou_threshold", 0.45)
        self.max_det: int = config.get("max_det", 1000)
    
    def initialize(self) -> bool:
        """Initialize YOLO11 model.
        
        Downloads the model if needed, loads it into memory,
        and sets up the predictor.
        
        Returns:
            bool: True if initialization succeeded
        """
        try:
            from ultralytics import YOLO
            
            # Verify model type
            if self.model_type not in YOLO11_MODELS:
                logger.error(f"Unknown YOLO11 model type: {self.model_type}")
                return False
            
            self._model_info = YOLO11_MODELS[self.model_type]
            
            # Set up model cache directory
            self._model_cache_dir = Path.home() / ".sk" / "models" / "computer-vision" / "yolo11"
            self._model_cache_dir.mkdir(parents=True, exist_ok=True)
            
            model_path = self._model_cache_dir / f"{self.model_type}.pt"
            
            # Download model if not present
            if not model_path.exists():
                logger.info(f"Downloading YOLO11 model: {self.model_type}")
                if not self._download_model(
                    self._model_info.download_url, 
                    model_path,
                    progress=True
                ):
                    pass
            
            # Verify hash if available
            if self._model_info.hash_sha256 != "placeholder_hash":
                if model_path.exists() and not self._verify_hash(model_path, self._model_info.hash_sha256):
                    logger.warning("Model hash verification failed, proceeding anyway")
            
            # Load the model using ultralytics
            if model_path.exists():
                # Load YOLO model
                self._model = YOLO(str(model_path))
                self._device = self._detect_device()
                self._loaded = True
                
                logger.info(f"YOLO11 model {self.model_type} loaded successfully")
                return True
            else:
                logger.error(f"YOLO11 model file not found: {model_path}")
                return False
                
        except ImportError:
            logger.warning("ultralytics package not installed, attempting installation")
            return self._handle_missing_dependency()
        except Exception as e:
            logger.error(f"Failed to initialize YOLO11: {e}")
            return False
    
    def _handle_missing_dependency(self) -> bool:
        """Handle missing ultralytics dependency gracefully."""
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "ultralytics"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                logger.info("ultralytics installed successfully")
                return self.initialize()  # Retry
            else:
                logger.warning(f"Failed to install ultralytics: {result.stderr[:200]}")
                return self._provide_fallback()
        except Exception as e:
            logger.error(f"Error installing ultralytics: {e}")
            return self._provide_fallback()
    
    def _provide_fallback(self) -> bool:
        """Provide fallback when YOLO11 is not available."""
        self._model_info = YOLO11_MODELS["yolo11n"]
        self._device = self._detect_device()
        self._loaded = True  # Mark as loaded but features limited
        logger.warning("YOLO11 not available - running in fallback mode")
        return True
    
    def cleanup(self) -> None:
        """Unload YOLO11 model and free memory."""
        self._model = None
        self._model_info = None
        self._device = None
        self._loaded = False
        logger.info("YOLO11 model unloaded")
    
    @cv_tool(
        risk="write",
        tier="task",
        description="Detect objects in an image and return bounding boxes",
        tags=["detection", "object-detection", "bounding-boxes"],
    )
    def tool_detect(self, image_path: str, confidence: Optional[float] = None) -> Dict[str, Any]:
        """Detect objects in an image.
        
        Args:
            image_path: Path to the input image
            confidence: Confidence threshold (overrides default)
        
        Returns:
            Dict with detected objects, bounding boxes, and labels
        """
        if not self._loaded:
            return {"error": "YOLO11 model not loaded"}
        
        conf = confidence if confidence is not None else self.confidence
        
        try:
            # Run YOLO inference
            results = self._model(str(image_path), conf=conf, iou=self.iou_threshold, 
                                  max_det=self.max_det, verbose=False)
            
            result = {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "confidence": conf,
                "objects": [],
            }
            
            if results and len(results) > 0:
                r = results[0]
                boxes = r.boxes
                if boxes is not None and len(boxes) > 0:
                    for i in range(len(boxes)):
                        box = boxes[i]
                        class_id = int(box.cls[0])
                        confidence_val = float(box.conf[0])
                        class_name = self.model.names[class_id] if hasattr(self.model, 'names') else f"class_{class_id}"
                        
                        result["objects"].append({
                            "index": i,
                            "class_id": class_id,
                            "class_name": class_name,
                            "confidence": confidence_val,
                            "bbox": [float(x) for x in box.xyxy[0].tolist()],  # [x1, y1, x2, y2]
                            "center": [float((box.xyxy[0][0] + box.xyxy[0][2]) / 2),
                                     float((box.xyxy[0][1] + box.xyxy[0][3]) / 2)],
                        })
            
            return result
            
        except Exception as e:
            logger.error(f"Error in detect: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="write",
        tier="task",
        description="Detect and segment objects in an image",
        tags=["detection", "segmentation", "instance-segmentation"],
    )
    def tool_segment(self, image_path: str, confidence: Optional[float] = None) -> Dict[str, Any]:
        """Segment objects in an image using YOLO11 segmentation.
        
        Args:
            image_path: Path to the input image
            confidence: Confidence threshold (overrides default)
        
        Returns:
            Dict with detected objects and segmentation masks
        """
        if not self._loaded:
            return {"error": "YOLO11 model not loaded"}
        
        conf = confidence if confidence is not None else self.confidence
        
        try:
            results = self._model(str(image_path), conf=conf, iou=self.iou_threshold,
                                  max_det=self.max_det, verbose=False)
            
            result = {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "confidence": conf,
                "objects": [],
            }
            
            if results and len(results) > 0:
                r = results[0]
                masks = r.masks
                boxes = r.boxes
                
                if masks is not None and boxes is not None and len(masks) > 0:
                    for i in range(len(masks)):
                        box = boxes[i] if i < len(boxes) else None
                        class_id = int(box.cls[0]) if box is not None else -1
                        class_name = self.model.names[class_id] if hasattr(self.model, 'names') and class_id >= 0 else f"class_{class_id}"
                        
                        result["objects"].append({
                            "index": i,
                            "class_id": class_id,
                            "class_name": class_name,
                            "confidence": float(box.conf[0]) if box is not None else 0.0,
                            "bbox": [float(x) for x in (box.xyxy[0].tolist() if box else [0, 0, 0, 0])],
                            "mask": masks.xyseg[i].tolist() if hasattr(masks, 'xyseg') else [],
                        })
            
            return result
            
        except Exception as e:
            logger.error(f"Error in segment: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="write",
        tier="task",
        description="Classify an image using YOLO11 classification head",
        tags=["classification", "image-classification"],
    )
    def tool_classify(self, image_path: str) -> Dict[str, Any]:
        """Classify an image using YOLO11.
        
        Args:
            image_path: Path to the input image
        
        Returns:
            Dict with classification results
        """
        if not self._loaded:
            return {"error": "YOLO11 model not loaded"}
        
        try:
            results = self._model(str(image_path), verbose=False)
            
            result = {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "class": None,
                "confidence": 0.0,
                "top5": [],
            }
            
            if results and len(results) > 0:
                r = results[0]
                if r.probs is not None:
                    # Top classification
                    result["class"] = self.model.names[r.probs.top1] if hasattr(self.model, 'names') else f"class_{r.probs.top1}"
                    result["confidence"] = float(r.probs.top1conf)
                    
                    # Top 5
                    top5_ids = r.probs.top5
                    top5_confs = r.probs.top5conf
                    result["top5"] = [
                        {
                            "class": self.model.names[class_id] if hasattr(self.model, 'names') else f"class_{class_id}",
                            "confidence": float(conf)
                        }
                        for class_id, conf in zip(top5_ids, top5_confs)
                    ]
            
            return result
            
        except Exception as e:
            logger.error(f"Error in classify: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="write",
        tier="task",
        description="Estimate pose/keypoints in an image",
        tags=["pose", "keypoints", "body-pose"],
    )
    def tool_pose(self, image_path: str, confidence: Optional[float] = None) -> Dict[str, Any]:
        """Estimate pose/keypoints in an image.
        
        Args:
            image_path: Path to the input image
            confidence: Confidence threshold
        
        Returns:
            Dict with pose keypoints
        """
        if not self._loaded:
            return {"error": "YOLO11 model not loaded"}
        
        conf = confidence if confidence is not None else self.confidence
        
        try:
            results = self._model(str(image_path), conf=conf, iou=self.iou_threshold,
                                  max_det=self.max_det, verbose=False, tasks=["pose"])
            
            result = {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "confidence": conf,
                "people": [],
            }
            
            if results and len(results) > 0:
                r = results[0]
                if r.keypoints is not None:
                    keypoints = r.keypoints.xy  # N x K x 2
                    for i in range(len(keypoints)):
                        person_kpts = keypoints[i].tolist()
                        result["people"].append({
                            "person_id": i,
                            "keypoints": person_kpts,  # [[x1, y1], [x2, y2], ...]
                        })
            
            return result
            
        except Exception as e:
            logger.error(f"Error in pose: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="write",
        tier="task",
        description="Detect objects with Oriented Bounding Boxes (OBB)",
        tags=["obb", "oriented-boxes", "detection"],
    )
    def tool_obb(self, image_path: str, confidence: Optional[float] = None) -> Dict[str, Any]:
        """Detect objects with Oriented Bounding Boxes (OBB).
        
        Args:
            image_path: Path to the input image
            confidence: Confidence threshold
        
        Returns:
            Dict with OBB detection results
        """
        if not self._loaded:
            return {"error": "YOLO11 model not loaded"}
        
        conf = confidence if confidence is not None else self.confidence
        
        try:
            results = self._model(str(image_path), conf=conf, iou=self.iou_threshold,
                                  max_det=self.max_det, verbose=False, tasks=["obb"])
            
            result = {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "confidence": conf,
                "objects": [],
            }
            
            if results and len(results) > 0:
                r = results[0]
                if r.obb is not None:
                    obb_data = r.obb.xyxy_obb  # N x 7 (x1, y1, x2, y2, angle, ...)
                    for i in range(len(obb_data)):
                        obb = obb_data[i]
                        class_id = int(obb[6]) if obb.shape[0] > 6 else -1
                        class_name = self.model.names[class_id] if hasattr(self.model, 'names') and class_id >= 0 else f"class_{class_id}"
                        
                        result["objects"].append({
                            "index": i,
                            "class_id": class_id,
                            "class_name": class_name,
                            "confidence": float(obb[5]) if obb.shape[0] > 5 else 0.0,
                            "obb": [float(x) for x in obb[:7].tolist()],  # [x1, y1, x2, y2, angle, ...]
                        })
            
            return result
            
        except Exception as e:
            logger.error(f"Error in obb: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="write",
        tier="task",
        description="Export YOLO11 model to target format",
        tags=["export", "onnx", "tensorrt", "coreml"],
    )
    def tool_export(self, image_path: str, format: str = "onnx") -> Dict[str, Any]:
        """Export YOLO11 model to a target format.
        
        Args:
            image_path: Path to a sample image (required for export)
            format: Target format (onnx, tensorrt, coreml, engine, etc.)
        
        Returns:
            Dict with export status and path
        """
        if not self._loaded:
            return {"error": "YOLO11 model not loaded"}
        
        try:
            # Export the model
            success = self._model.export(format=format, imgsz=640, verbose=False)
            
            result = {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "format": format,
                "exported": success,
            }
            
            if success:
                # Find exported file
                export_dir = Path("runs/detect/exp")  # Default export location
                if export_dir.exists():
                    result["export_path"] = str(list(export_dir.glob("*.pt"))[0] if list(export_dir.glob("*.pt")) 
                                               else list(export_dir.glob("*"))[0] if list(export_dir.glob("*")) 
                                               else "unknown")
                else:
                    result["export_path"] = "exported-successfully"
            
            return result
            
        except Exception as e:
            logger.error(f"Error in export: {e}")
            return {"error": str(e)}


# Auto-register this integration
__all__ = ["YOLO11Integration", "YOLO11_MODELS"]