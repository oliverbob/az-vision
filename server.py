"""Z-Image API server with OpenAI-compatible and Ollama-like endpoints."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import io
import os
import threading
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import torch

from utils import AttentionBackend, ensure_model_weights, load_from_local_dir, set_attention_backend
from zimage import generate


def _select_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    try:
        import torch_xla.core.xla_model as xm

        return str(xm.xla_device())
    except (ImportError, RuntimeError):
        if torch.backends.mps.is_available():
            return "mps"
        return "cpu"


def _normalize_content(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        part_type = str(item.get("type", ""))
        if part_type in {"text", "input_text"}:
            text = item.get("text", "")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()


def _build_prompt_from_openai_messages(messages: list["OpenAIChatMessage"]) -> str:
    if not messages:
        return ""

    rendered: list[str] = []
    for message in messages:
        text = _normalize_content(message.content)
        if not text:
            continue
        rendered.append(f"{message.role}: {text}")

    return "\n".join(rendered).strip()


def _build_prompt_from_ollama_messages(messages: list["OllamaMessage"]) -> str:
    rendered: list[str] = []
    for message in messages:
        text = message.content.strip()
        if text:
            rendered.append(f"{message.role}: {text}")
    return "\n".join(rendered).strip()


def _is_cuda_oom(exc: Exception) -> bool:
    message = str(exc).lower()
    return "cuda out of memory" in message or "cudnn_status_alloc_failed" in message


class ZImageService:
    def __init__(self) -> None:
        self._components: dict[str, Any] | None = None
        self._device = _select_device()
        self._dtype = torch.bfloat16
        self._compile = os.environ.get("ZIMAGE_COMPILE", "0") == "1"
        self._attn_backend = os.environ.get("ZIMAGE_ATTENTION", "_native_flash")
        self._model_path = os.environ.get("ZIMAGE_MODEL_PATH", "ckpts/Z-Image-Turbo")
        self._repo_id = os.environ.get("ZIMAGE_REPO_ID", "Tongyi-MAI/Z-Image-Turbo")
        self._lock = threading.Lock()

    def _lazy_load(self) -> None:
        if self._components is not None:
            return

        model_path = ensure_model_weights(self._model_path, repo_id=self._repo_id, verify=False)
        components = load_from_local_dir(
            model_path,
            device=self._device,
            dtype=self._dtype,
            compile=self._compile,
        )
        AttentionBackend.print_available_backends()
        set_attention_backend(self._attn_backend)

        text_encoder = components.get("text_encoder")
        if text_encoder is not None and torch.cuda.is_available():
            text_encoder.to("cpu")
            torch.cuda.empty_cache()

        self._components = components

    def generate_image_base64(
        self,
        prompt: str,
        height: int = 512,
        width: int = 512,
        num_inference_steps: int = 8,
        guidance_scale: float = 0.0,
        seed: int | None = None,
    ) -> tuple[str, float]:
        if not prompt.strip():
            raise ValueError("Prompt is required")

        with self._lock:
            self._lazy_load()
            assert self._components is not None

            text_encoder = self._components.get("text_encoder")
            if text_encoder is not None:
                text_encoder.to(self._device)

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            attempts = [
                (height, width, num_inference_steps),
                (min(height, 768), min(width, 768), min(num_inference_steps, 6)),
                (512, 512, min(num_inference_steps, 4)),
            ]

            unique_attempts: list[tuple[int, int, int]] = []
            for attempt in attempts:
                if attempt not in unique_attempts:
                    unique_attempts.append(attempt)

            effective_seed = seed if seed is not None else int(time.time() * 1000) % 2_147_483_647
            last_error: Exception | None = None

            for try_height, try_width, try_steps in unique_attempts:
                try:
                    generator = torch.Generator(self._device).manual_seed(effective_seed)

                    started = time.perf_counter()
                    images = generate(
                        prompt=prompt,
                        **self._components,
                        height=try_height,
                        width=try_width,
                        num_inference_steps=try_steps,
                        guidance_scale=guidance_scale,
                        generator=generator,
                        offload_text_encoder=True,
                    )
                    elapsed = time.perf_counter() - started

                    image = images[0]
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
                    return image_b64, elapsed
                except RuntimeError as exc:
                    last_error = exc
                    if not _is_cuda_oom(exc):
                        raise
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    continue

            if last_error is not None:
                raise last_error

            raise RuntimeError("Generation failed with unknown error.")


class OpenAIChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[dict[str, Any]]


class OpenAIChatCompletionsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "Z-image-turbo"
    messages: list[OpenAIChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    height: int = 512
    width: int = 512
    num_inference_steps: int = 8
    guidance_scale: float = 0.0
    seed: int | None = None


class OpenAIImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "Z-image-turbo"
    prompt: str
    n: int = 1
    size: str = "512x512"
    response_format: Literal["b64_json", "url"] = "b64_json"
    num_inference_steps: int = 8
    guidance_scale: float = 0.0
    seed: int | None = None


class OllamaMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    images: list[str] = Field(default_factory=list)


class OllamaChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "Z-image-turbo"
    messages: list[OllamaMessage]
    stream: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class OllamaGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "Z-image-turbo"
    prompt: str
    images: list[str] = Field(default_factory=list)
    stream: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Z-Image API", version="0.1.0")
service = ZImageService()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "Z-image-turbo",
                "object": "model",
                "owned_by": "Tongyi-MAI",
            }
        ],
    }


@app.post("/v1/chat/completions")
def openai_chat_completions(body: OpenAIChatCompletionsRequest) -> dict[str, Any]:
    if body.stream:
        raise HTTPException(status_code=400, detail="Streaming is not supported yet.")

    prompt = _build_prompt_from_openai_messages(body.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="No usable text content found in messages.")

    try:
        image_b64, elapsed = service.generate_image_base64(
            prompt=prompt,
            height=body.height,
            width=body.width,
            num_inference_steps=body.num_inference_steps,
            guidance_scale=body.guidance_scale,
            seed=body.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

    data_url = f"data:image/png;base64,{image_b64}"
    created = int(time.time())

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Generated image with Z-image-turbo in {elapsed:.2f}s.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                            },
                        },
                    ],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


@app.post("/v1/images/generations")
def openai_image_generations(body: OpenAIImageGenerationRequest) -> dict[str, Any]:
    if body.n != 1:
        raise HTTPException(status_code=400, detail="Only n=1 is supported.")

    try:
        width, height = (int(value) for value in body.size.lower().split("x", maxsplit=1))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="size must be formatted as WIDTHxHEIGHT, e.g. 1024x1024.")

    try:
        image_b64, _ = service.generate_image_base64(
            prompt=body.prompt,
            height=height,
            width=width,
            num_inference_steps=body.num_inference_steps,
            guidance_scale=body.guidance_scale,
            seed=body.seed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

    if body.response_format == "url":
        return {
            "created": int(time.time()),
            "data": [{"url": f"data:image/png;base64,{image_b64}"}],
        }

    return {
        "created": int(time.time()),
        "data": [{"b64_json": image_b64}],
    }


@app.post("/api/chat")
def ollama_chat(body: OllamaChatRequest) -> dict[str, Any]:
    if body.stream:
        raise HTTPException(status_code=400, detail="Streaming is not supported yet.")

    prompt = _build_prompt_from_ollama_messages(body.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="No usable text content found in messages.")

    options = body.options or {}
    try:
        image_b64, elapsed = service.generate_image_base64(
            prompt=prompt,
            height=int(options.get("height", 512)),
            width=int(options.get("width", 512)),
            num_inference_steps=int(options.get("num_inference_steps", 8)),
            guidance_scale=float(options.get("guidance_scale", 0.0)),
            seed=options.get("seed"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "model": body.model,
        "created_at": created_at,
        "message": {
            "role": "assistant",
            "content": f"Generated image with Z-image-turbo in {elapsed:.2f}s.",
            "images": [image_b64],
        },
        "done": True,
        "total_duration": int(elapsed * 1_000_000_000),
        "eval_count": 0,
        "eval_duration": int(elapsed * 1_000_000_000),
    }


@app.post("/api/generate")
def ollama_generate(body: OllamaGenerateRequest) -> dict[str, Any]:
    if body.stream:
        raise HTTPException(status_code=400, detail="Streaming is not supported yet.")

    options = body.options or {}
    try:
        image_b64, elapsed = service.generate_image_base64(
            prompt=body.prompt,
            height=int(options.get("height", 512)),
            width=int(options.get("width", 512)),
            num_inference_steps=int(options.get("num_inference_steps", 8)),
            guidance_scale=float(options.get("guidance_scale", 0.0)),
            seed=options.get("seed"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

    created_at = datetime.now(timezone.utc).isoformat()
    return {
        "model": body.model,
        "created_at": created_at,
        "response": f"Generated image with Z-image-turbo in {elapsed:.2f}s.",
        "images": [image_b64],
        "done": True,
        "total_duration": int(elapsed * 1_000_000_000),
        "eval_count": 0,
        "eval_duration": int(elapsed * 1_000_000_000),
    }


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "9090"))
    uvicorn.run("server:app", host=host, port=port, reload=False)