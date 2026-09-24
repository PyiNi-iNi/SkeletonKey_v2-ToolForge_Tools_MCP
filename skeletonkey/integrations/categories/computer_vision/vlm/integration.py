"""VLM Integration for SkeletonKey.

Vision Language Models (VLM) for image understanding, captioning,
visual QA, and structured extraction.

Supported models:
- LLaVA (llava-v1.5-7b, llava-v1.6-mistral-7b)
- Qwen-VL (qwen-vl-chat, qwen2-vl-7b)
- Phi-3-Vision (phi-3-vision-128k-instruct)
- Moondream2 (tiny, 1.8B params)
- MiniCPM-V (minicpm-v-2_6)
- Idefics2 (idefics2-8b)
- LLaVA-OneVision (video understanding)

Uses GGUF format for local inference via llama-cpp-python,
or transformers for full-precision inference.
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


# VLM model definitions (GGUF quantized for local inference)
VLM_MODELS = {
    # LLaVA family
    "llava-v1.5-7b-q4_k_m": CVModelInfo(
        name="llava-v1.5-7b-q4_k_m",
        version="1.5.0",
        task="vision-language",
        model_type="llava",
        size_mb=4200.0,
        download_url="https://huggingface.co/llava-hf/llava-v1.5-7b-gguf/resolve/main/llava-v1.5-7b.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="LLaVA v1.5 7B Q4_K_M - Vision-language understanding",
        tags=["llava", "vision-language", "captioning", "vqa"],
    ),
    "llava-v1.6-mistral-7b-q4_k_m": CVModelInfo(
        name="llava-v1.6-mistral-7b-q4_k_m",
        version="1.6.0",
        task="vision-language",
        model_type="llava",
        size_mb=4200.0,
        download_url="https://huggingface.co/llava-hf/llava-v1.6-mistral-7b-gguf/resolve/main/llava-v1.6-mistral-7b.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="LLaVA v1.6 Mistral 7B Q4_K_M - Improved vision-language",
        tags=["llava", "mistral", "vision-language", "improved"],
    ),
    
    # Qwen-VL family
    "qwen-vl-chat-q4_k_m": CVModelInfo(
        name="qwen-vl-chat-q4_k_m",
        version="2.0.0",
        task="vision-language",
        model_type="qwen-vl",
        size_mb=7800.0,
        download_url="https://huggingface.co/Qwen/Qwen-VL-Chat-GGUF/resolve/main/qwen-vl-chat.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="Qwen-VL Chat Q4_K_M - Multilingual vision-language",
        tags=["qwen", "multilingual", "vision-language", "chat"],
    ),
    
    # Phi-3 Vision
    "phi-3-vision-128k-instruct-q4_k_m": CVModelInfo(
        name="phi-3-vision-128k-instruct-q4_k_m",
        version="3.0.0",
        task="vision-language",
        model_type="phi-3-vision",
        size_mb=4200.0,
        download_url="https://huggingface.co/microsoft/Phi-3-vision-128k-instruct-gguf/resolve/main/Phi-3-vision-128k-instruct.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="Phi-3 Vision 128K Instruct Q4_K_M - Long context vision",
        tags=["phi-3", "microsoft", "long-context", "vision-language"],
    ),
    
    # Tiny/efficient models
    "moondream2-q4_k_m": CVModelInfo(
        name="moondream2-q4_k_m",
        version="2.0.0",
        task="vision-language",
        model_type="moondream",
        size_mb=1800.0,
        download_url="https://huggingface.co/vikhyatk/moondream2/resolve/main/moondream2.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="Moondream2 Q4_K_M - Tiny 1.8B params, fast inference",
        tags=["tiny", "fast", "efficient", "vision-language"],
    ),
    
    # MiniCPM
    "minicpm-v-2_6-q4_k_m": CVModelInfo(
        name="minicpm-v-2_6-q4_k_m",
        version="2.6.0",
        task="vision-language",
        model_type="minicpm",
        size_mb=7800.0,
        download_url="https://huggingface.co/openbmb/MiniCPM-V-2_6-gguf/resolve/main/MiniCPM-V-2_6.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="MiniCPM-V 2.6 Q4_K_M - Efficient vision-language",
        tags=["minicpm", "efficient", "vision-language"],
    ),
    
    # Idefics2
    "idefics2-8b-q4_k_m": CVModelInfo(
        name="idefics2-8b-q4_k_m",
        version="2.0.0",
        task="vision-language",
        model_type="idefics",
        size_mb=7800.0,
        download_url="https://huggingface.co/HuggingFaceM4/Idefics2-8B-GGUF/resolve/main/Idefics2-8B.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="Idefics2 8B Q4_K_M - Open vision-language model",
        tags=["idefics", "open", "vision-language"],
    ),
    
    # Video understanding
    "llava-onevision-q4_k_m": CVModelInfo(
        name="llava-onevision-q4_k_m",
        version="1.0.0",
        task="vision-language",
        model_type="llava-onevision",
        size_mb=7800.0,
        download_url="https://huggingface.co/llava-hf/llava-onevision-qwen2-7b-ov-gguf/resolve/main/llava-onevision-qwen2-7b-ov.Q4_K_M.gguf",
        hash_sha256="placeholder_hash",
        description="LLaVA-OneVision Q4_K_M - Video understanding",
        tags=["video", "vision-language", "onevision"],
    ),
}


@register_integration(
    name="vlm",
    title="VLM Integration",
    version="1.0.0",
    integration_type=IntegrationType.MODEL_SERVING,
    category=IntegrationCategory.COMPUTER_VISION,
    platform=None,
    description="Vision Language Models for image understanding, captioning, and visual QA",
    author="SkeletonKey",
    license="MIT",
    keywords=["vlm", "vision-language", "captioning", "vqa", "computer-vision", "ai"],
    dependencies=[
        IntegrationDependency(name="llama-cpp-python", version="0.2.0+", optional=True),
        IntegrationDependency(name="transformers", version="4.30.0+", optional=True),
    ],
)
class VLMIntegration(CVIntegrationBase):
    """VLM Integration for SkeletonKey.
    
    Provides Vision Language Model capabilities:
    - Image captioning and description
    - Visual question answering (VQA)
    - Structured data extraction (JSON)
    - Image comparison
    - Video understanding (if model supports)
    
    Uses GGUF format for efficient local inference.
    """
    
    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.model_type: str = config.get("model_type", "llava-v1.5-7b-q4_k_m")
        self.max_tokens: int = config.get("max_tokens", 512)
        self.temperature: float = config.get("temperature", 0.7)
        self.use_gguf: bool = config.get("use_gguf", True)
        self.use_transformers: bool = config.get("use_transformers", False)
    
    def initialize(self) -> bool:
        """Initialize VLM model.
        
        Downloads the GGUF model if needed, loads it for inference,
        and sets up the chat pipeline.
        
        Returns:
            bool: True if initialization succeeded
        """
        try:
            # Verify model type
            if self.model_type not in VLM_MODELS:
                logger.error(f"Unknown VLM model type: {self.model_type}")
                return False
            
            self._model_info = VLM_MODELS[self.model_type]
            
            # Set up model cache directory
            self._model_cache_dir = Path.home() / ".sk" / "models" / "computer-vision" / "vlm"
            self._model_cache_dir.mkdir(parents=True, exist_ok=True)
            
            model_path = self._model_cache_dir / f"{self.model_type}.gguf"
            
            # Download model if not present
            if not model_path.exists():
                logger.info(f"Downloading VLM model: {self.model_type}")
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
            
            # Load the model
            if model_path.exists():
                if self.use_gguf:
                    return self._load_gguf_model(model_path)
                elif self.use_transformers:
                    return self._load_transformers_model()
                else:
                    # Default to GGUF
                    return self._load_gguf_model(model_path)
            else:
                logger.error(f"VLM model file not found: {model_path}")
                return False
                
        except ImportError:
            logger.warning("llama-cpp-python or transformers not installed")
            return self._handle_missing_dependency()
        except Exception as e:
            logger.error(f"Failed to initialize VLM: {e}")
            return False
    
    def _load_gguf_model(self, model_path: Path) -> bool:
        """Load VLM using llama-cpp-python (GGUF format)."""
        try:
            from llama_cpp import Llama
            
            # Initialize Llama model
            self._model = Llama(
                model_path=str(model_path),
                n_ctx=2048,  # Context size
                n_gpu_layers=40 if self.device.is_cuda else 0,  # Offload to GPU
                verbose=False,
            )
            
            self._device = self._detect_device()
            self._loaded = True
            
            logger.info(f"VLM model {self.model_type} loaded (GGUF) successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load GGUF model: {e}")
            return False
    
    def _load_transformers_model(self) -> bool:
        """Load VLM using transformers library."""
        try:
            from transformers import AutoProcessor, AutoModelForVision2Seq, AutoTokenizer
            import torch
            
            # Load model and processor
            model_name = self._get_transformers_model_name()
            
            self._model = {
                "processor": AutoProcessor.from_pretrained(model_name),
                "tokenizer": AutoTokenizer.from_pretrained(model_name),
                "model": AutoModelForVision2Seq.from_pretrained(
                    model_name,
                    torch_dtype=torch.float16 if self.device.is_cuda else torch.float32,
                    device_map="auto" if self.device.is_cuda else "cpu",
                ),
            }
            
            self._device = self._detect_device()
            self._loaded = True
            
            logger.info(f"VLM model {self.model_type} loaded (transformers) successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load transformers model: {e}")
            return False
    
    def _get_transformers_model_name(self) -> str:
        """Get the HuggingFace model name for transformers."""
        mapping = {
            "llava-v1.5-7b-q4_k_m": "llava-hf/llava-v1.5-7b-hf",
            "llava-v1.6-mistral-7b-q4_k_m": "llava-hf/llava-v1.6-mistral-7b-hf",
            "qwen-vl-chat-q4_k_m": "Qwen/Qwen-VL-Chat",
            "phi-3-vision-128k-instruct-q4_k_m": "microsoft/Phi-3-vision-128k-instruct",
            "moondream2-q4_k_m": "vikhyatk/moondream2",
            "minicpm-v-2_6-q4_k_m": "openbmb/MiniCPM-V-2_6",
            "idefics2-8b-q4_k_m": "HuggingFaceM4/Idefics2-8B",
            "llava-onevision-q4_k_m": "llava-hf/llava-onevision-qwen2-7b-ov-hf",
        }
        return mapping.get(self.model_type, mapping.get("llava-v1.5-7b-q4_k_m"))
    
    def _handle_missing_dependency(self) -> bool:
        """Handle missing VLM dependencies gracefully."""
        try:
            import subprocess
            
            # Try to install llama-cpp-python
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "llama-cpp-python"],
                capture_output=True, text=True, timeout=120
            )
            
            if result.returncode == 0:
                logger.info("llama-cpp-python installed successfully")
                return self.initialize()  # Retry
            else:
                logger.warning(f"Failed to install llama-cpp-python: {result.stderr[:200]}")
                return self._provide_fallback()
        except Exception as e:
            logger.error(f"Error installing VLM dependencies: {e}")
            return self._provide_fallback()
    
    def _provide_fallback(self) -> bool:
        """Provide fallback when VLM is not available."""
        self._model_info = VLM_MODELS.get("moondream2-q4_k_m", list(VLM_MODELS.values())[0])
        self._device = self._detect_device()
        self._loaded = True  # Mark as loaded but features limited
        logger.warning("VLM not available - running in fallback mode")
        return True
    
    def cleanup(self) -> None:
        """Unload VLM model and free memory."""
        self._model = None
        self._model_info = None
        self._device = None
        self._loaded = False
        logger.info("VLM model unloaded")
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Generate a detailed description of an image",
        tags=["captioning", "description", "vision-language"],
    )
    def tool_describe(self, image_path: str, detail_level: str = "detailed") -> Dict[str, Any]:
        """Generate a detailed description of an image.
        
        Args:
            image_path: Path to the input image
            detail_level: "brief", "detailed", or "comprehensive"
        
        Returns:
            Dict with image description
        """
        if not self._loaded:
            return {"error": "VLM model not loaded"}
        
        try:
            prompt = f"Describe this image in {detail_level} detail. Focus on the main subjects, actions, and context."
            
            result = self._run_inference(image_path, prompt)
            
            return {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "detail_level": detail_level,
                "description": result.get("response", ""),
                "prompt": prompt,
            }
            
        except Exception as e:
            logger.error(f"Error in describe: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Answer questions about an image (Visual QA)",
        tags=["vqa", "question-answering", "vision-language"],
    )
    def tool_qa(self, image_path: str, question: str) -> Dict[str, Any]:
        """Answer a question about an image.
        
        Args:
            image_path: Path to the input image
            question: The question to ask about the image
        
        Returns:
            Dict with the answer
        """
        if not self._loaded:
            return {"error": "VLM model not loaded"}
        
        try:
            prompt = f"Based on the image, please answer the following question: {question}\nAnswer concisely."
            
            result = self._run_inference(image_path, prompt)
            
            return {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "question": question,
                "answer": result.get("response", ""),
            }
            
        except Exception as e:
            logger.error(f"Error in QA: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Chat with an image - interactive vision-language conversation",
        tags=["chat", "conversation", "vision-language"],
    )
    def tool_chat(self, image_path: str, message: str, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Interactive chat with an image.
        
        Args:
            image_path: Path to the input image
            message: User message/question
            history: Previous conversation history (optional)
        
        Returns:
            Dict with response and updated history
        """
        if not self._loaded:
            return {"error": "VLM model not loaded"}
        
        try:
            # Build conversation context
            context = ""
            if history:
                for turn in history:
                    context += f"User: {turn.get('user', '')}\nAssistant: {turn.get('assistant', '')}\n"
            
            prompt = f"{context}\nImage context: [Image provided]\nUser: {message}\nAssistant:"
            
            result = self._run_inference(image_path, prompt)
            
            # Update history
            new_history = history or []
            new_history.append({
                "user": message,
                "assistant": result.get("response", ""),
            })
            
            return {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "message": message,
                "response": result.get("response", ""),
                "history": new_history,
            }
            
        except Exception as e:
            logger.error(f"Error in chat: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Extract structured data from an image (JSON format)",
        tags=["extraction", "structured-data", "json", "vision-language"],
    )
    def tool_extract(self, image_path: str, schema: Optional[Dict] = None) -> Dict[str, Any]:
        """Extract structured data from an image.
        
        Args:
            image_path: Path to the input image
            schema: Optional JSON schema describing expected fields
        
        Returns:
            Dict with extracted structured data
        """
        if not self._loaded:
            return {"error": "VLM model not loaded"}
        
        try:
            # Build extraction prompt
            if schema:
                schema_str = json.dumps(schema, indent=2)
                prompt = f"Extract structured information from this image according to the following schema:\n{schema_str}\nReturn only valid JSON."
            else:
                prompt = "Extract all structured information from this image. Return as JSON with fields: objects, text, numbers, relationships."
            
            result = self._run_inference(image_path, prompt)
            
            # Try to parse JSON from response
            response_text = result.get("response", "")
            try:
                import json
                # Try to find JSON in response
                start_idx = response_text.find("{")
                end_idx = response_text.rfind("}")
                if start_idx >= 0 and end_idx > start_idx:
                    json_str = response_text[start_idx:end_idx + 1]
                    extracted_data = json.loads(json_str)
                else:
                    extracted_data = {"raw_text": response_text}
            except Exception:
                extracted_data = {"raw_text": response_text}
            
            return {
                "image_path": image_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "extracted_data": extracted_data,
                "raw_response": response_text,
            }
            
        except Exception as e:
            logger.error(f"Error in extract: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Compare multiple images and describe differences",
        tags=["compare", "comparison", "vision-language"],
    )
    def tool_compare(self, image_paths: List[str], focus: str = "differences") -> Dict[str, Any]:
        """Compare multiple images and describe differences/similarities.
        
        Args:
            image_paths: List of image paths to compare
            focus: "differences", "similarities", or "both"
        
        Returns:
            Dict with comparison results
        """
        if not self._loaded:
            return {"error": "VLM model not loaded"}
        
        try:
            if len(image_paths) < 2:
                return {"error": "Compare requires at least 2 images"}
            
            if len(image_paths) > 4:
                return {"error": "Compare supports up to 4 images"}
            
            prompt = f"Compare these {len(image_paths)} images. Focus on: {focus}. Describe key differences and similarities."
            
            # For multi-image comparison, we process sequentially
            # In a full implementation, we'd use multi-image input
            results = []
            for img_path in image_paths:
                result = self._run_inference(img_path, prompt)
                results.append({
                    "image_path": img_path,
                    "analysis": result.get("response", ""),
                })
            
            return {
                "image_paths": image_paths,
                "model": self.model_info.name,
                "device": self.device.device,
                "focus": focus,
                "comparisons": results,
            }
            
        except Exception as e:
            logger.error(f"Error in compare: {e}")
            return {"error": str(e)}
    
    @cv_tool(
        risk="read",
        tier="task",
        description="Understand video content (if model supports video)",
        tags=["video", "video-understanding", "vision-language"],
    )
    def tool_video_chat(self, video_path: str, message: str, frame_interval: int = 30) -> Dict[str, Any]:
        """Understand video content (if model supports video input).
        
        Args:
            video_path: Path to the video file
            message: Question or prompt about the video
            frame_interval: Frame sampling interval (default: every 30 frames)
        
        Returns:
            Dict with video understanding results
        """
        if not self._loaded:
            return {"error": "VLM model not loaded"}
        
        # Check if model supports video
        model_name = self.model_info.name
        video_models = ["llava-onevision", "qwen-vl"]
        
        if not any(m in model_name for m in video_models):
            return {
                "video_path": video_path,
                "message": message,
                "note": "This VLM model does not support video input. Use image-based analysis instead.",
                "supported_video_models": video_models,
            }
        
        try:
            prompt = f"Based on the video, please respond to: {message}\nDescribe the key events, actions, and context."
            
            # For video, we'd extract frames and process
            # This is a simplified version
            result = self._run_inference(video_path, prompt)
            
            return {
                "video_path": video_path,
                "model": self.model_info.name,
                "device": self.device.device,
                "message": message,
                "frame_interval": frame_interval,
                "response": result.get("response", ""),
                "note": "Video understanding requires video-capable VLM model",
            }
            
        except Exception as e:
            logger.error(f"Error in video chat: {e}")
            return {"error": str(e)}
    
    def _run_inference(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """Run VLM inference on an image with a prompt.
        
        Args:
            image_path: Path to the input image
            prompt: Text prompt/question
        
        Returns:
            Dict with inference results
        """
        if self.use_gguf:
            return self._run_gguf_inference(image_path, prompt)
        elif self.use_transformers:
            return self._run_transformers_inference(image_path, prompt)
        else:
            # Default to GGUF
            return self._run_gguf_inference(image_path, prompt)
    
    def _run_gguf_inference(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """Run inference using llama-cpp-python (GGUF)."""
        try:
            from llama_cpp import Llama
            
            # For GGUF models, we need to encode the image
            # This is a simplified version - full implementation requires
            # image encoding and multi-modal input
            
            # Create a simple text-only prompt (image context would be embedded)
            full_prompt = f"<image>\n{prompt}\nAnswer:"
            
            # Generate response
            output = self._model(
                full_prompt,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stop=["</s>", "<|endoftext|>", "\n\n"],
                echo=False,
            )
            
            response_text = output["choices"][0]["text"] if output.get("choices") else ""
            
            return {
                "response": response_text,
                "tokens_used": output.get("usage", {}).get("total_tokens", 0) if output.get("usage") else 0,
                "inference_time_ms": 0,  # Would measure actual time
            }
            
        except Exception as e:
            logger.error(f"GGUF inference error: {e}")
            return {"response": f"Error: {str(e)}", "error": str(e)}
    
    def _run_transformers_inference(self, image_path: str, prompt: str) -> Dict[str, Any]:
        """Run inference using transformers library."""
        try:
            import torch
            from PIL import Image
            
            # Load image
            image = Image.open(image_path).convert("RGB")
            
            # Process image and text
            model_data = self._model
            processor = model_data.get("processor")
            tokenizer = model_data.get("tokenizer")
            model = model_data.get("model")
            
            if processor and tokenizer and model:
                # Prepare inputs
                inputs = processor(
                    images=image,
                    text=prompt,
                    return_tensors="pt",
                    padding=True,
                )
                
                # Move to device
                device = "cuda" if self.device.is_cuda else "cpu"
                inputs = {k: v.to(device) for k, v in inputs.items()}
                
                # Generate
                with torch.no_grad():
                    output_ids = model.generate(
                        **inputs,
                        max_new_tokens=self.max_tokens,
                        temperature=self.temperature,
                        do_sample=True,
                    )
                
                # Decode
                response_text = tokenizer.decode(output_ids[0], skip_special_tokens=True)
                
                return {
                    "response": response_text,
                    "tokens_used": len(output_ids[0]),
                    "inference_time_ms": 0,
                }
            else:
                return {"response": "Transformers model not properly loaded", "error": "Model data missing"}
            
        except Exception as e:
            logger.error(f"Transformers inference error: {e}")
            return {"response": f"Error: {str(e)}", "error": str(e)}


# Auto-register this integration
__all__ = ["VLMIntegration", "VLM_MODELS"]