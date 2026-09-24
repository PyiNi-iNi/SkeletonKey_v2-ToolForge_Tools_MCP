# Computer Vision Integration - Phase Plan

**Project**: SkeletonKey / ToolForge v2
**Date**: 2026-09-24
**Status**: Planning → Implementation

---

## Overview

Add comprehensive computer vision capabilities to SkeletonKey through the integration framework, including:
- **SAM2** (Segment Anything Model 2) - Instance segmentation
- **YOLO11** (Ultralytics) - Object detection, segmentation, classification, pose, OBB
- **OCR** (PaddleOCR / Tesseract / EasyOCR) - Text detection & recognition
- **VLM** (LLaVA, Qwen-VL, Phi-3-Vision, etc.) - Vision-language understanding

---

## Phase 1: Foundation (Week 1) ✅ IN PROGRESS

### 1.1 Integration Category & Base Structure
- [ ] Add `computer-vision` category to `IntegrationCategory` enum
- [ ] Add `model-serving`, `ml-inference` types to `IntegrationType` enum
- [ ] Create base `CVIntegration` class with common utilities
- [ ] Add model registry/cache manager (`~/.sk/models/`)

### 1.2 Model Management
- [ ] `ModelDownloader` class with progress, resume, verification
- [ ] `ModelCache` with LRU eviction, size limits
- [ ] `ModelRegistry` - known models with URLs, hashes, metadata
- [ ] CLI: `sk cv models list|pull|remove|info`

### 1.3 Device Management
- [ ] Auto-detect CUDA/MPS/CPU
- [ ] Device selection per integration
- [ ] Memory estimation for model loading

---

## Phase 2: Core CV Integrations (Week 2-3)

### 2.1 SAM2 Integration (`sam2`)
**Type**: `model-serving` | **Category**: `computer-vision` | **Platform**: `cross-platform`

**Models**:
- `sam2_hiera_tiny` (~39MB) - Fast, mobile-friendly
- `sam2_hiera_small` (~172MB) - Balanced
- `sam2_hiera_base_plus` (~319MB) - High quality
- `sam2_hiera_large` (~847MB) - Best quality

**Tools**:
| Tool ID | Description |
|---------|-------------|
| `sam2.segment` | Segment everything (automatic mask generation) |
| `sam2.segment_point` | Segment with point prompts |
| `sam2.segment_box` | Segment with box prompts |
| `sam2.segment_mask` | Segment with mask prompts |
| `sam2.video_segment` | Video object segmentation (tracking) |

**Dependencies**: `torch`, `torchvision`, `sam2` (from GitHub)

---

### 2.2 YOLO11 Integration (`yolo11`)
**Type**: `model-serving` | **Category**: `computer-vision` | **Platform**: `cross-platform`

**Models** (auto-downloaded from Ultralytics):
- Detection: `yolo11n.pt` → `yolo11x.pt`
- Segmentation: `yolo11n-seg.pt` → `yolo11x-seg.pt`
- Classification: `yolo11n-cls.pt` → `yolo11x-cls.pt`
- Pose: `yolo11n-pose.pt` → `yolo11x-pose.pt`
- OBB: `yolo11n-obb.pt` → `yolo11x-obb.pt`

**Tools**:
| Tool ID | Description |
|---------|-------------|
| `yolo11.detect` | Object detection with bounding boxes |
| `yolo11.segment` | Instance segmentation |
| `yolo11.classify` | Image classification |
| `yolo11.pose` | Keypoint/pose estimation |
| `yolo11.obb` | Oriented bounding boxes |
| `yolo11.track` | Multi-object tracking (video) |
| `yolo11.predict` | Unified prediction (auto task detection) |
| `yolo11.export` | Export to ONNX, TensorRT, CoreML, etc. |

**Dependencies**: `ultralytics`, `torch`

---

### 2.3 OCR Integration (`ocr`)
**Type**: `model-serving` | **Category**: `computer-vision` | **Platform**: `cross-platform`

**Engines** (pluggable):
- **PaddleOCR** (recommended) - Best accuracy, 80+ languages
- **EasyOCR** - Simple, 80+ languages
- **Tesseract** - Classic, 100+ languages (requires system install)
- **RapidOCR** - ONNX-based, fast

**Tools**:
| Tool ID | Description |
|---------|-------------|
| `ocr.detect` | Text detection only (bounding boxes) |
| `ocr.recognize` | Text recognition on cropped regions |
| `ocr.full` | End-to-end: detect + recognize |
| `ocr.pdf` | Extract text from PDF pages |
| `ocr.batch` | Batch process directory of images |

**Dependencies**: `paddleocr` (or `easyocr`, `pytesseract`)

---

### 2.4 VLM Integration (`vlm`)
**Type**: `model-serving` | **Category**: `computer-vision` | **Platform**: `cross-platform`

**Models** (GGUF/quantized for local inference):
- **LLaVA** (llava-v1.5-7b, llava-v1.6-mistral-7b, llava-next)
- **Qwen-VL** (qwen-vl-chat, qwen2-vl-7b)
- **Phi-3-Vision** (phi-3-vision-128k-instruct)
- **Moondream2** (tiny, 1.8B params)
- **MiniCPM-V** (minicpm-v-2_6)
- **Idefics2** (idefics2-8b)
- **LLaVA-OneVision** (video understanding)

**Tools**:
| Tool ID | Description |
|---------|-------------|
| `vlm.chat` | Image + prompt → response |
| `vlm.describe` | Detailed image description |
| `vlm.qa` | Visual question answering |
| `vlm.ocr` | VLM-based OCR (complex layouts) |
| `vlm.extract` | Structured data extraction (JSON) |
| `vlm.compare` | Compare multiple images |
| `vlm.video_chat` | Video + prompt → response (if supported) |

**Dependencies**: `llama-cpp-python` (GGUF), or `transformers`, `accelerate`, `bitsandbytes`

---

## Phase 3: Advanced Features (Week 4)

### 3.1 Pipeline / Chaining
- [ ] `cv.pipeline` - Chain multiple CV tools
- [ ] Example: `yolo11.detect` → crop → `ocr.full` → `vlm.extract`

### 3.2 Batch & Streaming
- [ ] Directory batch processing with progress
- [ ] Video frame extraction + processing
- [ ] Webcam/RTSP stream processing

### 3.3 Annotation & Export
- [ ] COCO/YOLO/VOC format export
- [ ] LabelStudio / CVAT compatible output
- [ ] Visualization overlays (boxes, masks, keypoints)

### 3.4 Model Fine-tuning Helpers
- [ ] `yolo11.train` - Custom dataset training
- [ ] `sam2.finetune` - LoRA fine-tuning
- [ ] Dataset preparation utilities

---

## Phase 4: Integration & Polish (Week 5)

### 4.1 Catalog Registration
- [ ] Auto-register all CV tools in catalog
- [ ] Add CV-specific tags: `segmentation`, `detection`, `ocr`, `vlm`, `video`

### 4.2 CLI Commands
- [ ] `sk cv sam2 ...`
- [ ] `sk cv yolo11 ...`
- [ ] `sk cv ocr ...`
- [ ] `sk cv vlm ...`

### 4.3 Documentation
- [ ] `docs/CV-INTEGRATION.md` - User guide
- [ ] `docs/CV-MODELS.md` - Model zoo reference
- [ ] Example workflows

### 4.4 Tests
- [ ] Unit tests for each integration
- [ ] Integration tests with sample images
- [ ] Performance benchmarks

---

## Model Storage Structure

```
~/.sk/models/
├── sam2/
│   ├── sam2_hiera_tiny.pt
│   ├── sam2_hiera_small.pt
│   └── ...
├── yolo11/
│   ├── yolo11n.pt
│   ├── yolo11n-seg.pt
│   └── ...
├── ocr/
│   ├── paddleocr/
│   │   ├── det_db_mv3/
│   │   ├── rec_mv3/
│   │   └── ...
│   └── easyocr/
│       └── ...
└── vlm/
    ├── llava-v1.5-7b-q4_k_m.gguf
    ├── qwen2-vl-7b-instruct-q4_k_m.gguf
    ├── phi-3-vision-128k-instruct-q4_k_m.gguf
    └── ...
```

---

## Dependency Matrix

| Integration | Core Deps | Optional Deps | System Deps |
|-------------|-----------|---------------|-------------|
| `sam2` | torch, torchvision, sam2 | opencv-python | - |
| `yolo11` | ultralytics, torch | opencv-python, onnxruntime | - |
| `ocr` (paddle) | paddlepaddle, paddleocr | opencv-python | - |
| `ocr` (easy) | easyocr, torch | opencv-python | - |
| `ocr` (tesseract) | pytesseract, pillow | - | tesseract-ocr |
| `vlm` (gguf) | llama-cpp-python | - | - |
| `vlm` (transformers) | transformers, accelerate, bitsandbytes | flash-attn | CUDA |

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Large model downloads (GBs) | Resume support, progress bars, size warnings |
| GPU memory OOM | Auto-batch sizing, CPU fallback, quantization |
| Dependency conflicts | Optional deps, clear error messages, venv isolation |
| License issues (SAM2, YOLO) | Document licenses, user acceptance |
| Platform compatibility | Test CPU/CUDA/MPS, provide fallbacks |

---

## Success Criteria

- [ ] All 4 integrations load without errors
- [ ] Each provides 5+ working tools
- [ ] Models auto-download on first use
- [ ] Tools appear in `sk tools list` and catalog
- [ ] Basic inference works on CPU (no GPU required)
- [ ] Documentation covers common workflows

---

## Next Steps

1. **Immediate**: Create integration category enums and base CV integration class
2. **Day 1-2**: Implement model downloader/cache
3. **Day 3-5**: Implement SAM2 + YOLO11 integrations
4. **Day 6-8**: Implement OCR + VLM integrations
5. **Day 9-10**: Pipeline, CLI, tests, docs

---

## References

- SAM2: https://github.com/facebookresearch/sam2
- YOLO11: https://github.com/ultralytics/ultralytics
- PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
- EasyOCR: https://github.com/JaidedAI/EasyOCR
- llama.cpp: https://github.com/ggerganov/llama.cpp
- LLaVA: https://github.com/haotian-liu/LLaVA
- Qwen-VL: https://github.com/QwenLM/Qwen-VL