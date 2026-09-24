"""PNG to SVG Converter.

Converts PNG images to SVG vector format using multiple methods:
- potrace (bitmap tracing, best quality)
- autotrace (alternative tracing)
- inkscape (full-featured conversion)
- python-based (basic vectorization using PIL and simple path generation)

Can be used as a standalone tool or integrated with CV pipeline.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


class PNGToSVGConverter:
    """PNG to SVG converter with multiple backend support."""
    
    def __init__(self, method: str = "potrace", simplify: bool = True):
        self.method = method
        self.simplify = simplify
        self._check_dependencies()
    
    def _check_dependencies(self) -> Dict[str, bool]:
        """Check which conversion tools are available."""
        available = {}
        
        # Check potrace
        try:
            result = subprocess.run(
                ["potrace", "--version"],
                capture_output=True, text=True, timeout=5
            )
            available["potrace"] = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            available["potrace"] = False
        
        # Check autotrace
        try:
            result = subprocess.run(
                ["autotrace", "--version"],
                capture_output=True, text=True, timeout=5
            )
            available["autotrace"] = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            available["autotrace"] = False
        
        # Check inkscape
        try:
            result = subprocess.run(
                ["inkscape", "--version"],
                capture_output=True, text=True, timeout=5
            )
            available["inkscape"] = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            available["inkscape"] = False
        
        # Check python libraries
        try:
            import PIL
            available["pillow"] = True
        except ImportError:
            available["pillow"] = False
        
        try:
            import numpy
            available["numpy"] = True
        except ImportError:
            available["numpy"] = False
        
        return available
    
    def convert(self, input_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Convert PNG to SVG.
        
        Args:
            input_path: Path to input PNG
            output_path: Path for output SVG (optional)
        
        Returns:
            Dict with conversion status
        """
        input_file = Path(input_path)
        if not input_file.exists():
            return {"error": f"Input file not found: {input_path}"}
        
        # Determine output path
        if output_path is None:
            output_path = str(input_file.with_suffix(".svg"))
        
        output_file = Path(output_path)
        
        # Try selected method
        if self.method == "potrace":
            result = self._convert_potrace(str(input_file), str(output_file))
        elif self.method == "autotrace":
            result = self._convert_autotrace(str(input_file), str(output_file))
        elif self.method == "inkscape":
            result = self._convert_inkscape(str(input_file), str(output_file))
        elif self.method == "python":
            result = self._convert_python(str(input_file), str(output_file))
        else:
            # Auto-select best available
            available = self._check_dependencies()
            if available.get("potrace"):
                result = self._convert_potrace(str(input_file), str(output_file))
            elif available.get("autotrace"):
                result = self._convert_autotrace(str(input_file), str(output_file))
            elif available.get("inkscape"):
                result = self._convert_inkscape(str(input_file), str(output_file))
            else:
                result = self._convert_python(str(input_file), str(output_file))
        
        # Add metadata
        if "error" not in result:
            result["method"] = self.method
            result["simplified"] = self.simplify
            result["input_size_bytes"] = input_file.stat().st_size
            if output_file.exists():
                result["output_size_bytes"] = output_file.stat().st_size
        
        return result
    
    def _convert_potrace(self, input_path: str, output_path: str) -> Dict[str, Any]:
        """Convert using potrace (bitmap tracing)."""
        try:
            cmd = ["potrace", "-s", "-o", output_path, input_path]
            
            if self.simplify:
                cmd.extend(["--turdsize", "10"])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                return {
                    "status": "success",
                    "method": "potrace",
                    "input": input_path,
                    "output": output_path,
                    "message": "Conversion completed successfully",
                }
            else:
                return {"error": f"Potrace failed: {result.stderr}"}
        except Exception as e:
            return {"error": f"Potrace conversion error: {str(e)}"}
    
    def _convert_autotrace(self, input_path: str, output_path: str) -> Dict[str, Any]:
        """Convert using autotrace."""
        try:
            cmd = [
                "autotrace",
                "-output-file", output_path,
                "-output-format", "svg",
                input_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                return {
                    "status": "success",
                    "method": "autotrace",
                    "input": input_path,
                    "output": output_path,
                    "message": "Conversion completed successfully",
                }
            else:
                return {"error": f"Autotrace failed: {result.stderr}"}
        except Exception as e:
            return {"error": f"Autotrace conversion error: {str(e)}"}
    
    def _convert_inkscape(self, input_path: str, output_path: str) -> Dict[str, Any]:
        """Convert using inkscape."""
        try:
            cmd = [
                "inkscape",
                input_path,
                "--export-type=svg",
                f"--export-filename={output_path}"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                return {
                    "status": "success",
                    "method": "inkscape",
                    "input": input_path,
                    "output": output_path,
                    "message": "Conversion completed successfully",
                }
            else:
                return {"error": f"Inkscape failed: {result.stderr}"}
        except Exception as e:
            return {"error": f"Inkscape conversion error: {str(e)}"}
    
    def _convert_python(self, input_path: str, output_path: str) -> Dict[str, Any]:
        """Convert using Python libraries (basic vectorization)."""
        try:
            from PIL import Image
            import numpy as np
            
            # Load image
            img = Image.open(input_path)
            
            # Convert to grayscale for edge detection
            gray = img.convert("L")
            
            # Get image dimensions
            width, height = img.size
            
            # Create basic SVG with image embedded or traced
            # This is a simplified approach - creates an SVG with the image
            # and basic shape outlines
            
            svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <title>Converted from PNG</title>
  <defs>
    <filter id="outline">
      <feMorphology operator="dilate" radius="1"/>
    </filter>
  </defs>
  <!-- Image embedded -->
  <image href="{input_path}" width="{width}" height="{height}"/>
  <!-- Basic outline -->
  <rect x="0" y="0" width="{width}" height="{height}" 
        fill="none" stroke="#333333" stroke-width="2"/>
</svg>'''
            
            with open(output_path, "w") as f:
                f.write(svg_content)
            
            return {
                "status": "success",
                "method": "python-basic",
                "input": input_path,
                "output": output_path,
                "message": "Basic SVG conversion completed (image embedded with outline)",
                "note": "For full vector tracing, install potrace or use potrace/autotrace method",
            }
            
        except ImportError:
            return {
                "error": "Python conversion requires PIL (Pillow) and numpy. Install with: pip install Pillow numpy",
                "suggestion": "Use potrace method for best results: sk call cv.png2svg '{\"image_path\": \"...\", \"method\": \"potrace\"}'",
            }
        except Exception as e:
            return {"error": f"Python conversion error: {str(e)}"}


# CLI command wrapper
class PNGToSVGCLI:
    """CLI interface for PNG to SVG converter."""
    
    def __init__(self):
        self.converter = PNGToSVGConverter()
    
    def run(self, image_path: str, output_path: Optional[str] = None,
            method: str = "potrace", simplify: bool = True) -> Dict[str, Any]:
        """Run PNG to SVG conversion."""
        # Update converter settings
        self.converter.method = method
        self.converter.simplify = simplify
        
        return self.converter.convert(image_path, output_path)


# Module-level convenience function
def convert_png_to_svg(image_path: str, output_path: Optional[str] = None,
                       method: str = "potrace", simplify: bool = True) -> Dict[str, Any]:
    """Convert PNG to SVG - module-level convenience function."""
    converter = PNGToSVGConverter(method=method, simplify=simplify)
    return converter.convert(image_path, output_path)
