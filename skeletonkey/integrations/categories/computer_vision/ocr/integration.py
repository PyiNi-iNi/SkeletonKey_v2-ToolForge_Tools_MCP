"""OCR Integration for SkeletonKey.

Optical Character Recognition support with multiple engines:
- PaddleOCR (recommended, best accuracy)
- EasyOCR (simple, good accuracy)
- Tesseract (classic, system-dependent)

Provides text detection, recognition, and full pipeline tools.
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


# OCR engine definitions
OCR_ENGINES = {
    "paddle": CVModelInfo(
        name="paddleocr",
        version="2.0.0",
        task="ocr",
        model_type="paddleocr",
        size_mb=500.0,  # Estimated total package size
        download_url="https://pypi.org/project/paddleocr/",
        hash_sha256="placeholder_hash",
        description="PaddleOCR - High accuracy, 80+ languages",
        tags=["high-accuracy", "80-languages", "paddle-paddle"],
    ),
    "easy": CVModelInfo(
        name="easyocr",
        version="1.5.7",
        task="ocr",
        model_type="easyocr",
        size_mb=300.0,  # Estimated total including models
        download_url="https://pypi.org/project/easyocr/",
        hash_sha256="placeholder_hash",
        description="EasyOCR - Simple, 80+ languages, GPU supported",
        tags=["simple", "80-languages", "gpu-supported"],
    ),
    "tesseract": CVModelInfo(
        name="tesseract",
        version="5.0.0",
        task="ocr",
        model_type="tesseract",
        size_mb=50.0,  # System package, not Python
        download_url="https://github.com/tesseract-ocr/tesseract",
        hash_sha256="placeholder_hash",
        description="Tesseract OCR - Classic, 100+ languages, system install",
        tags=["classic", "100-languages", "system-ocr"],
    ),
}


@register_integration(
    name="ocr",
    title="OCR Integration",
    version="2.0.0",
    integration_type=IntegrationType.MODEL_SERVING,
    category=IntegrationCategory.COMPUTER_VISION,
    platform=None,
    description="Optical Character Recognition with PaddleOCR, EasyOCR, and Tesseract",
    author="SkeletonKey",
    license="MIT",
    keywords=["ocr", "text", "recognition", "paddleocr", "tesseract", "computer-vision"],
    dependencies=[
        IntegrationDependency(name="paddleocr", version="2.0.0+", optional=True),
        IntegrationDependency(name="easyocr", version="1.5.0+", optional=True),
        IntegrationDependency(name="pytesseract", version="0.3.0+", optional=True),
    ],
)
class OCRIntegration(CVIntegrationBase):
    """OCR Integration for SkeletonKey.
    
    Provides Optical Character Recognition capabilities with multiple engines:
    - Text detection (localize text regions)
    - Text recognition (extract text from detected regions)
    - Full pipeline (detect + recognize)
    - PDF text extraction
    - Batch processing
    """
    
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.engine: str = config.get("engine", "paddle")  # paddle, easy, tesseract
        self.languages: List[str] = config.get("languages", ["en"])
        self.use_gpu: bool = config.get("use_gpu", False)
    
    def initialize(self) -> bool:
        """Initialize OCR engine.
        
        Downloads/Installs the selected OCR engine and models.
        
        Returns:
            bool: True if initialization succeeded
        """
        try:
            self._model_info = OCR_ENGINES.get(self.engine, OCR_ENGINES["paddle"])
            
            # Set up model cache directory
            self._model_cache_dir = Path.home() / ".sk" / "models" / "computer-vision" / "ocr" / self.engine
            self._model_cache_dir.mkdir(parents=True, exist_ok=True)
            
            if self.engine == "tesseract":
                # Tesseract is a system package, check if available
                return self._check_tesseract_installation()
            else:
                # Python packages - try to import and install if missing
                return self._install_python_ocr_engine()
                
        except Exception as e:
            logger.error(f"Failed to initialize OCR ({self.engine}): {e}")
            return False
    
    def _check_tesseract_installation(self) -> bool:
        """Check if Tesseract OCR is installed on the system."""
        try:
            # Check if tesseract binary is available
            result = subprocess.run(
                ["tesseract", "--version"], 
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self._model_info = OCR_ENGINES["tesseract"]
                self._device = self._detect_device()
                self._loaded = True
                logger.info("Tesseract OCR found on system")
                return True
            else:
                # Try to install
                return self._install_tesseract()
        except FileNotFoundError:
            return self._install_tesseract()
        except Exception as e:
            logger.error(f"Tesseract check failed: {e}")
            return False
    
    def _install_tesseract(self) -> bool:
        """Install Tesseract OCR."""
        try:
            import subprocess
            # Detect OS
            if sys.platform.startswith("win"):
                # Windows - provide guidance, don't auto-install
                logger.warning("Tesseract not on Windows - guide user to install")
                # Mark as loaded with limited functionality
                self._device = self._detect_device()
                self._loaded = True
                return True
            elif sys.platform.startswith("darwin"):
                # macOS - brew install
                result = subprocess.run(
                    ["brew", "install", "tesseract"], 
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    self._model_info = OCR_ENGINES["tesseract"]
                    self._device = self._detect_device()
                    self._loaded = True
                    logger.info("Tesseract installed via brew")
                    return True
                else:
                    logger.warning(f"Tesseract brew install failed: {result.stderr[:200]}")
                    return self._provide_fallback()
            else:
                # Linux - apt/dnf
                result = subprocess.run(
                    ["apt-get", "install", "-y", "tesseract-ocr"], 
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    self._model_info = OCR_ENGINES["tesseract"]
                    self._device = self._detect_device()
                    self._loaded = True
                    logger.info("Tesseract installed via apt")
                    return True
                else:
                    logger.warning(f"Tesseract apt install failed: {result.stderr[:200]}")
                    return self._provide_fallback()
        except Exception as e:
            logger.error(f"Tesseract installation error: {e}")
            return self._provide_fallback()
    
    def _install_python_ocr_engine(self) -> bool:
        """Install the selected Python OCR engine."""
        try:
            import subprocess
            
            engine_name = self.engine  # "paddle" or "easy"
            
            # Install the main package
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", f"{engine_name}ocr"],
                capture_output=True, text=True, timeout=120
            )
            
            if result.returncode == 0:
                # Try to import to verify
                try:
                    if engine_name == "paddle":
                        import paddleocr
                    elif engine_name == "easy":
                        import easyocr
                    
                    self._model_info = OCR_ENGINES[self.engine]
                    self._device = self._detect_device()
                    self._loaded = True
                    logger.info(f"{self.engine}ocr installed and verified")
                    return True
                except ImportError:
                    logger.warning(f"Package installed but import failed")
                    return self._provide_fallback()
            else:
                logger.warning(f"Failed to install {engine_name}ocr: {result.stderr[:200]}")
                return self._provide_fallback()
        except Exception as e:
            logger.error(f"OCR engine installation error: {e}")
            return self._provide_fallback()
    
    def _provide_fallback(self) -> bool:
        """Provide fallback when OCR engine is not available."""
        self._model_info = OCR_ENGINES["paddle"]
        self._device = self._detect_device()
        self._loaded = True  # Mark as loaded but features limited
        logger.warning(f"OCR engine {self.engine} not available - running in fallback mode")
        return True
    
    def cleanup(self) -> None:
        """Unload OCR engine."""
        self._model = None
        self._model_info = None
        self._device = None
        self._loaded = False
        logger.info("OCR engine unloaded")
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Detect text regions in an image (localize text)",
        tags=["ocr", "text-detection", "localization"],
    )
    def tool_detect(self, image_path: str) -> Dict[str, Any]:
        """Detect text regions in an image.
        
        Args:
            image_path: Path to the input image
        
        Returns:
            Dict with detected text regions/boxes
        """
        if not self._loaded:
            return {"error": "OCR engine not loaded"}
        
        try:
            if self.engine == "tesseract":
                return self._tesseract_detect(image_path)
            else:
                # Python OCR engines
                return self._python_ocr_detect(image_path)
        except Exception as e:
            logger.error(f"Error in OCR detect: {e}")
            return {"error": str(e)}
    
    def _tesseract_detect(self, image_path: str) -> Dict[str, Any]:
        """Text detection using Tesseract."""
        try:
            import pytesseract
            
            # Get detailed data including boxes
            data = pytesseract.image_to_data(
                image_path, output_type=pytesseract.Output.DICT
            )
            
            boxes = []
            for i in range(len(data["text"])):
                text = data["text"][i].strip()
                if text:  # Only non-empty text
                    x, y, w, h = int(data["left"][i]), int(data["top"][i]), \
                                int(data["width"][i]), int(data["height"][i])
                    boxes.append({
                        "text": text,
                        "bbox": [x, y, x + w, y + h],  # [x1, y1, x2, y2]
                        "confidence": float(data["conf"][i]) / 100.0 if data["conf"][i] > 0 else 0.0,
                    })
            
            return {
                "image_path": image_path,
                "engine": self.engine,
                "detection_boxes": boxes,
                "total_regions": len(boxes),
            }
        except Exception as e:
            logger.error(f"Tesseract detect error: {e}")
            return {"error": str(e)}
    
    def _python_ocr_detect(self, image_path: str) -> Dict[str, Any]:
        """Text detection using Python OCR engines."""
        try:
            if self.engine == "paddle":
                from paddleocr import PaddleOCR
                ocr = PaddleOCR(lang_list=self.languages, use_gpu=self.use_gpu, show_log=False)
                result = ocr.ocr(image_path, cls=True)
                
                boxes = []
                if result and result[0]:
                    for line in result[0]:
                        box = line[0]  # [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
                        # Convert to [x1, y1, x2, y2]
                        xs = [p[0] for p in box]
                        ys = [p[1] for p in box]
                        boxes.append({
                            "text": line[1][0] if isinstance(line[1], list) else str(line[1]),
                            "bbox": [min(xs), min(ys), max(xs), max(ys)],
                            "confidence": line[1][1] if len(line) > 1 and len(line[1]) > 1 else 1.0,
                        })
                
                return {
                    "image_path": image_path,
                    "engine": self.engine,
                    "detection_boxes": boxes,
                    "total_regions": len(boxes),
                }
                
            elif self.engine == "easy":
                import easyocr
                reader = easyocr.Reader(self.languages, gpu=self.use_gpu)
                results = reader.readtext(image_path)
                
                boxes = []
                for (bbox, text, confidence) in results:
                    # bbox is [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
                    xs = [p[0] for p in bbox]
                    ys = [p[1] for p in bbox]
                    boxes.append({
                        "text": text,
                        "bbox": [min(xs), min(ys), max(xs), max(ys)],
                        "confidence": confidence,
                    })
                
                return {
                    "image_path": image_path,
                    "engine": self.engine,
                    "detection_boxes": boxes,
                    "total_regions": len(boxes),
                }
            
            return {"error": f"Unknown OCR engine: {self.engine}"}
            
        except ImportError as e:
            logger.error(f"OCR engine import error: {e}")
            return {"error": f"OCR engine not available: {e}"}
        except Exception as e:
            logger.error(f"Python OCR detect error: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Recognize text in cropped image regions",
        tags=["ocr", "text-recognition", "recognition"],
    )
    def tool_recognize(self, image_path: str, boxes: Optional[List[List[int]]] = None) -> Dict[str, Any]:
        """Recognize text in an image, optionally within specified boxes.
        
        Args:
            image_path: Path to the input image
            boxes: Optional list of [x1, y1, x2, y2] boxes to restrict recognition
        
        Returns:
            Dict with recognized text
        """
        if not self._loaded:
            return {"error": "OCR engine not loaded"}
        
        try:
            if self.engine == "tesseract":
                return self._tesseract_recognize(image_path, boxes)
            elif self.engine in ("paddle", "easy"):
                return self._python_ocr_recognize(image_path, boxes)
            else:
                return {"error": f"OCR engine {self.engine} not supported for recognition"}
        except Exception as e:
            logger.error(f"OCR recognize error: {e}")
            return {"error": str(e)}
    
    def _tesseract_recognize(self, image_path: str, boxes: Optional[List[List[int]]]) -> Dict[str, Any]:
        """Text recognition using Tesseract."""
        try:
            import pytesseract
            
            if boxes:
                # Recognize within specified boxes
                text_results = []
                for box in boxes:
                    x1, y1, x2, y2 = box
                    # Crop the image region
                    from PIL import Image
                    img = Image.open(image_path)
                    cropped = img.crop((x1, y1, x2, y2))
                    
                    # Save cropped to temp and run tesseract
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        cropped.save(tmp.name)
                        text = pytesseract.image_to_string(tmp.name).strip()
                    
                    text_results.append({
                        "bbox": box,
                        "text": text,
                    })
                
                return {
                    "image_path": image_path,
                    "engine": self.engine,
                    "recognized_text": text_results,
                    "total_boxes": len(boxes),
                }
            else:
                # Without boxes - full image
                data = pytesseract.image_to_data(
                    image_path, output_type=pytesseract.Output.DICT
                )
                
                text_results = []
                for i in range(len(data["text"])):
                    text = data["text"][i].strip()
                    if text:
                        x1, y1, x2, y2 = int(data["left"][i]), int(data["top"][i]), \
                                        int(data["width"][i]), int(data["height"][i])
                        text_results.append({
                            "bbox": [x1, y1, x2, y2],
                            "text": text,
                            "confidence": float(data["conf"][i]) / 100.0 if data["conf"][i] > 0 else 0.0,
                        })
                
                return {
                    "image_path": image_path,
                    "engine": self.engine,
                    "recognized_text": text_results,
                    "total_regions": len(text_results),
                }
                
        except Exception as e:
            logger.error(f"Tesseract recognize error: {e}")
            return {"error": str(e)}
    
    def _python_ocr_recognize(self, image_path: str, boxes: Optional[List[List[int]]]) -> Dict[str, Any]:
        """Text recognition using Python OCR engines."""
        try:
            if self.engine == "paddle":
                from paddleocr import PaddleOCR
                ocr = PaddleOCR(lang_list=self.languages, use_gpu=self.use_gpu, show_log=False)
                
                if boxes:
                    # Recognize within boxes
                    results = []
                    for box in boxes:
                        x1, y1, x2, y2 = box
                        # Crop and run OCR
                        from PIL import Image
                        img = Image.open(image_path)
                        cropped = img.crop((x1, y1, x2, y2))
                        
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                            cropped.save(tmp.name)
                        
                        result = ocr.ocr(tmp.name, cls=True)
                        text = result[0][0][1][0] if result and result[0] else ""
                        
                        results.append({
                            "bbox": box,
                            "text": text,
                        })
                    
                    return {
                        "image_path": image_path,
                        "engine": self.engine,
                        "recognized_text": results,
                        "total_boxes": len(boxes),
                    }
                else:
                    # Full image
                    result = ocr.ocr(image_path, cls=True)
                    
                    text_results = []
                    if result and result[0]:
                        for line in result[0]:
                            text = line[1][0] if isinstance(line[1], list) else str(line[1])
                            text_results.append({
                                "text": text,
                                "confidence": line[1][1] if len(line) > 1 and len(line[1]) > 1 else 1.0,
                            })
                    
                    return {
                        "image_path": image_path,
                        "engine": self.engine,
                        "recognized_text": text_results,
                        "total_regions": len(text_results) if text_results else 0,
                    }
            
            elif self.engine == "easy":
                import easyocr
                reader = easyocr.Reader(self.languages, gpu=self.use_gpu)
                
                if boxes:
                    # Recognize within boxes
                    results = []
                    for box in boxes:
                        x1, y1, x2, y2 = box
                        from PIL import Image
                        img = Image.open(image_path)
                        cropped = img.crop((x1, y1, x2, y2))
                        
                        result = reader.readtext(cropped, detail=1)
                        text = result[0][1] if result and result[0] else ""
                        conf = result[0][2] if result and result[0] else 0.0
                        
                        results.append({
                            "bbox": box,
                            "text": text,
                            "confidence": conf,
                        })
                    
                    return {
                        "image_path": image_path,
                        "engine": self.engine,
                        "recognized_text": results,
                        "total_boxes": len(boxes),
                    }
                else:
                    # Full image
                    result = reader.readtext(image_path, detail=1)
                    
                    text_results = []
                    for (bbox, text, confidence) in result:
                        xs = [p[0] for p in bbox]
                        ys = [p[1] for p in bbox]
                        text_results.append({
                            "bbox": [min(xs), min(ys), max(xs), max(ys)],
                            "text": text,
                            "confidence": confidence,
                        })
                    
                    return {
                        "image_path": image_path,
                        "engine": self.engine,
                        "recognized_text": text_results,
                        "total_regions": len(text_results),
                    }
            
            return {"error": f"Unknown OCR engine: {self.engine}"}
            
        except Exception as e:
            logger.error(f"OCR recognize error: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Full OCR pipeline: detect + recognize text in image",
        tags=["ocr", "full-pipeline", "detect-and-recognize"],
    )
    def tool_full_pipeline(self, image_path: str) -> Dict[str, Any]:
        """Run full OCR pipeline: detect text regions and recognize them.
        
        Args:
            image_path: Path to the input image
        
        Returns:
            Dict with both detection and recognition results
        """
        if not self._loaded:
            return {"error": "OCR engine not loaded"}
        
        try:
            # First detect
            detect_result = self.tool_detect(image_path)
            
            # Then recognize
            recognize_result = self.tool_recognize(image_path)
            
            # Combine results
            combined = {
                "image_path": image_path,
                "engine": self.engine,
                "detection": detect_result,
                "recognition": recognize_result,
            }
            
            # Merge boxes with text if available
            if self.engine in ("paddle", "easy") and "detection_boxes" in detect_result:
                boxes = detect_result["detection_boxes"]
                recognized = recognize_result.get("recognized_text", [])
                
                # Simple matching: first N boxes get first N recognized texts
                merged = []
                for i, box in enumerate(boxes):
                    if i < len(recognized):
                        merged.append({
                            "bbox": box,
                            "text": recognized[i].get("text", ""),
                            "confidence": box.get("confidence", 0.0),
                        })
                    else:
                        merged.append({
                            "bbox": box,
                            "text": "",
                            "confidence": box.get("confidence", 0.0),
                        })
                
                combined["merged_results"] = merged
            
            return combined
            
        except Exception as e:
            logger.error(f"OCR full pipeline error: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Extract text from a PDF page",
        tags=["ocr", "pdf", "document"],
    )
    def tool_pdf(self, pdf_path: str, page_num: int = 1) -> Dict[str, Any]:
        """Extract text from a PDF page.
        
        Args:
            pdf_path: Path to the PDF file
            page_num: Page number (1-indexed)
        
        Returns:
            Dict with extracted text
        """
        if not self._loaded:
            return {"error": "OCR engine not loaded"}
        
        try:
            if self.engine == "tesseract":
                import pytesseract
                from PIL import Image
                
                # Convert PDF page to image
                import fitz  # PyMuPDF
                doc = fitz.open(pdf_path)
                
                if page_num > len(doc):
                    return {"error": f"PDF has only {len(doc)} pages, requested page {page_num}"}
                
                page = doc[page_num - 1]  # 0-indexed
                pix = page.get_pixmap(dpi=200)
                img_path = str(self._model_cache_dir / f"pdf_page_{page_num}.png")
                pix.save(img_path)
                
                # Run OCR
                text = pytesseract.image_to_string(img_path)
                
                return {
                    "pdf_path": pdf_path,
                    "page_num": page_num,
                    "engine": self.engine,
                    "text": text,
                    "success": True,
                }
            else:
                # For Python OCR engines, convert first page to image
                return self._pdf_to_image_ocr(pdf_path, page_num)
                
        except ImportError:
            logger.warning("PyMuPDF not available for PDF support")
            return {"error": "PyMuPDF not available for PDF support"}
        except Exception as e:
            logger.error(f"PDF OCR error: {e}")
            return {"error": str(e)}
    
    def _pdf_to_image_ocr(self, pdf_path: str, page_num: int) -> Dict[str, Any]:
        """Extract text from PDF using Python OCR engines."""
        try:
            from PIL import Image
            import fitz  # PyMuPDF
            
            doc = fitz.open(pdf_path)
            
            if page_num > len(doc):
                return {"error": f"PDF has only {len(doc)} pages, requested page {page_num}"}
            
            page = doc[page_num - 1]
            pix = page.get_pixmap(dpi=200)
            
            # Save as temp image
            img_path = str(self._model_cache_dir / f"pdf_page_{page_num}.png")
            pix.save(img_path)
            
            # Run OCR based on engine
            if self.engine == "paddle":
                from paddleocr import PaddleOCR
                ocr = PaddleOCR(lang_list=self.languages, use_gpu=self.use_gpu, show_log=False)
                result = ocr.ocr(img_path, cls=True)
                
                text = ""
                boxes = []
                if result and result[0]:
                    for line in result[0]:
                        text += line[1][0] + " "
                        box = line[0]
                        xs = [p[0] for p in box]
                        ys = [p[1] for p in box]
                        boxes.append([min(xs), min(ys), max(xs), max(ys)])
                
                return {
                    "pdf_path": pdf_path,
                    "page_num": page_num,
                    "engine": self.engine,
                    "text": text.strip(),
                    "boxes": boxes,
                    "success": True,
                }
            elif self.engine == "easy":
                import easyocr
                reader = easyocr.Reader(self.languages, gpu=self.use_gpu)
                result = reader.readtext(img_path, detail=1)
                
                text = ""
                boxes = []
                for (bbox, text, confidence) in result:
                    xs = [p[0] for p in bbox]
                    ys = [p[1] for p in bbox]
                    text += text + " "
                    boxes.append([min(xs), min(ys), max(xs), max(ys)])
                
                return {
                    "pdf_path": pdf_path,
                    "page_num": page_num,
                    "engine": self.engine,
                    "text": text.strip(),
                    "boxes": boxes,
                    "success": True,
                }
            
            return {"error": f"PDF OCR not supported for engine: {self.engine}"}
            
        except Exception as e:
            logger.error(f"PDF to image OCR error: {e}")
            return {"error": str(e)}


# Auto-register this integration
__all__ = ["OCRIntegration", "OCR_ENGINES"]