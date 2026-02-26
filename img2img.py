"""Minimal Z-Image image-to-image script (Diffusers)."""

from __future__ import annotations

import argparse
import base64
import io
from pathlib import Path
import threading
import time

import torch
from diffusers import ZImageImg2ImgPipeline
from diffusers.utils import load_image
from PIL import Image, UnidentifiedImageError


def _pick_device(explicit: str | None) -> str:
    if explicit:
        return explicit
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _pick_dtype(dtype_name: str, device: str) -> torch.dtype:
    if dtype_name == "bfloat16":
        if device == "cuda" and hasattr(torch.cuda, "is_bf16_supported") and not torch.cuda.is_bf16_supported():
            return torch.float16
        if device == "cpu":
            return torch.float32
        return torch.bfloat16
    if dtype_name == "float16":
        if device in {"cpu", "mps"}:
            return torch.float32
        return torch.float16
    return torch.float32


class ZImageImg2ImgService:
    def __init__(self, model_id: str, device: str | None = None, dtype_name: str = "bfloat16") -> None:
        self._model_id = model_id
        self._device = _pick_device(device)
        self._dtype = _pick_dtype(dtype_name, self._device)
        self._pipeline: ZImageImg2ImgPipeline | None = None
        self._lock = threading.Lock()

    @property
    def device(self) -> str:
        return self._device

    def lazy_load(self) -> None:
        if self._pipeline is not None:
            return
        with self._lock:
            if self._pipeline is not None:
                return
            pipeline = ZImageImg2ImgPipeline.from_pretrained(
                self._model_id,
                torch_dtype=self._dtype,
                low_cpu_mem_usage=False,
            )
            pipeline.to(self._device)
            self._pipeline = pipeline

    def edit_image(
        self,
        *,
        input_image: Image.Image,
        prompt: str,
        negative_prompt: str | None,
        strength: float,
        num_inference_steps: int,
        guidance_scale: float,
        seed: int | None,
    ) -> tuple[Image.Image, float]:
        if not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be between 0 and 1")
        if not prompt.strip():
            raise ValueError("prompt is required")

        self.lazy_load()
        assert self._pipeline is not None

        call_kwargs: dict[str, object] = {
            "prompt": prompt,
            "image": input_image,
            "negative_prompt": negative_prompt,
            "strength": float(strength),
            "num_inference_steps": int(num_inference_steps),
            "guidance_scale": float(guidance_scale),
        }
        if seed is not None:
            call_kwargs["generator"] = torch.Generator(device=self._device).manual_seed(seed)

        started = time.perf_counter()
        if self._device == "cuda":
            with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=self._dtype):
                result = self._pipeline(**call_kwargs).images[0]
        else:
            with torch.inference_mode():
                result = self._pipeline(**call_kwargs).images[0]
        elapsed = time.perf_counter() - started
        return result, elapsed

    def edit_image_base64(
        self,
        *,
        image_bytes: bytes,
        prompt: str,
        negative_prompt: str | None,
        width: int,
        height: int,
        strength: float,
        num_inference_steps: int,
        guidance_scale: float,
        seed: int | None,
    ) -> tuple[str, float]:
        try:
            input_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except UnidentifiedImageError as exc:
            raise ValueError("Invalid image file.") from exc

        input_image = input_image.resize((width, height), Image.Resampling.LANCZOS)
        image, elapsed = self.edit_image(
            input_image=input_image,
            prompt=prompt,
            negative_prompt=negative_prompt,
            strength=strength,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            seed=seed,
        )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("utf-8"), elapsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Z-Image image-to-image editing")
    parser.add_argument("--prompt", required=True, help="Editing prompt")
    parser.add_argument("--image", required=True, help="Input image path or URL")
    parser.add_argument("--output", default="zimage_img2img.png", help="Output image path")
    parser.add_argument("--model", default="Tongyi-MAI/Z-Image-Turbo", help="Model id")
    parser.add_argument("--negative", default=None, help="Negative prompt")
    parser.add_argument("--strength", type=float, default=0.6, help="Edit strength in [0, 1]")
    parser.add_argument("--steps", type=int, default=9, help="Inference steps")
    parser.add_argument("--guidance", type=float, default=0.0, help="CFG guidance scale")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--height", type=int, default=None, help="Optional output height")
    parser.add_argument("--width", type=int, default=None, help="Optional output width")
    parser.add_argument(
        "--dtype",
        choices=["bfloat16", "float16", "float32"],
        default="bfloat16",
        help="Torch dtype",
    )
    parser.add_argument("--device", default=None, help="Device override: cuda, mps, cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not 0.0 <= args.strength <= 1.0:
        raise ValueError("--strength must be between 0 and 1")

    service = ZImageImg2ImgService(model_id=args.model, device=args.device, dtype_name=args.dtype)

    image = load_image(args.image)
    if args.height and args.width:
        image = image.resize((args.width, args.height))

    result, _elapsed = service.edit_image(
        input_image=image,
        prompt=args.prompt,
        negative_prompt=args.negative,
        strength=args.strength,
        num_inference_steps=args.steps,
        guidance_scale=args.guidance,
        seed=args.seed,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(out_path)
    print(f"Saved image: {out_path}")


if __name__ == "__main__":
    main()
