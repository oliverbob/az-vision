"""Z-Image API server with OpenAI-compatible and Ollama-like endpoints."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import html
import io
import json
import os
import threading
import time
import uuid
from typing import Any, Iterator, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
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


def _sse_line(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        return f"data: {payload}\n\n"
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _ndjson_line(payload: dict[str, Any]) -> str:
    return f"{json.dumps(payload, ensure_ascii=False)}\n"


def _build_final_html(message_text: str, image_url: str) -> str:
    safe_text = html.escape(message_text)
    safe_url = html.escape(image_url, quote=True)
    return f"<p>{safe_text}</p><div class=\"mt-4\"><img src=\"{safe_url}\"></div>"


def _public_base_url(request: Request) -> str:
    configured = os.environ.get("OPENAI_PUBLIC_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")

    forwarded_host = request.headers.get("x-forwarded-host", "").strip()
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip() or "https"
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    host = request.headers.get("host", "").strip()
    if host:
        return f"https://{host}".rstrip("/")

    return str(request.base_url).rstrip("/")


def _openai_error_body(
    *,
    message: str,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


def _extract_error_message(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str):
            return message
        return json.dumps(detail, ensure_ascii=False)
    if isinstance(detail, list):
        return json.dumps(detail, ensure_ascii=False)
    return "Request failed"


def _openai_chat_stream_chunks(
    *,
    completion_id: str,
    created: int,
    model: str,
    text_block: dict[str, Any],
    image_block: dict[str, Any],
    message_text: str,
    image_url: str,
    include_admin_log: bool,
    include_final_event: bool,
) -> Iterator[str]:
    if include_admin_log:
        yield _sse_line(
            {
                "admin_log": True,
                "message": "[chat] generated image response",
                "provider": "zimage_server",
                "model": model,
            }
        )

    yield _sse_line(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": [text_block]},
                    "finish_reason": None,
                }
            ],
        }
    )

    yield _sse_line(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": [image_block]},
                    "finish_reason": None,
                }
            ],
        }
    )

    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield _sse_line(final_chunk)

    if include_final_event:
        yield _sse_line(
            {
                "final": True,
                "html": _build_final_html(message_text, image_url),
                "reasoningHtml": "",
                "contentEmpty": False,
                "provider": "zimage_server",
                "model": model,
                "raw_content": message_text,
            }
        )

    yield _sse_line("[DONE]")


def _ollama_chat_stream_chunks(
    *,
    model: str,
    created_at: str,
    message_text: str,
    image_b64: str,
    elapsed: float,
) -> Iterator[str]:
    yield _ndjson_line(
        {
            "model": model,
            "created_at": created_at,
            "message": {
                "role": "assistant",
                "content": message_text,
                "images": [image_b64],
            },
            "done": False,
        }
    )
    yield _ndjson_line(
        {
            "model": model,
            "created_at": created_at,
            "message": {
                "role": "assistant",
                "content": "",
            },
            "done": True,
            "done_reason": "stop",
            "total_duration": int(elapsed * 1_000_000_000),
            "eval_count": 0,
            "eval_duration": int(elapsed * 1_000_000_000),
        }
    )


def _ollama_generate_stream_chunks(
    *,
    model: str,
    created_at: str,
    response_text: str,
    image_b64: str,
    elapsed: float,
) -> Iterator[str]:
    yield _ndjson_line(
        {
            "model": model,
            "created_at": created_at,
            "response": response_text,
            "images": [image_b64],
            "done": False,
        }
    )
    yield _ndjson_line(
        {
            "model": model,
            "created_at": created_at,
            "response": "",
            "done": True,
            "done_reason": "stop",
            "total_duration": int(elapsed * 1_000_000_000),
            "eval_count": 0,
            "eval_duration": int(elapsed * 1_000_000_000),
        }
    )


class ZImageService:
    def __init__(self) -> None:
        self._components: dict[str, Any] | None = None
        self._device = _select_device()
        self._dtype = torch.bfloat16
        self._compile = os.environ.get("ZIMAGE_COMPILE", "0") == "1"
        self._attn_backend = os.environ.get("ZIMAGE_ATTENTION", "_native_flash")
        self._model_path = os.environ.get("ZIMAGE_MODEL_PATH", "ckpts/Z-Image-Turbo")
        self._repo_id = os.environ.get("ZIMAGE_REPO_ID", "Tongyi-MAI/Z-Image-Turbo")
        self._park_text_encoder_on_cpu = os.environ.get("ZIMAGE_PARK_TEXT_ENCODER_ON_CPU", "0") == "1"
        self._offload_text_encoder = os.environ.get("ZIMAGE_OFFLOAD_TEXT_ENCODER", "0") == "1"
        self._clear_cuda_cache_per_request = os.environ.get("ZIMAGE_CLEAR_CUDA_CACHE_PER_REQUEST", "0") == "1"
        self._max_cached_images = max(1, int(os.environ.get("ZIMAGE_MAX_CACHED_IMAGES", "64")))
        self._lock = threading.Lock()
        self._image_lock = threading.Lock()
        self._image_cache: dict[str, bytes] = {}
        self._image_cache_order: list[str] = []

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
        if self._park_text_encoder_on_cpu and text_encoder is not None and torch.cuda.is_available():
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
            if text_encoder is not None and self._park_text_encoder_on_cpu:
                text_encoder.to(self._device)

            if self._clear_cuda_cache_per_request and torch.cuda.is_available():
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
                        offload_text_encoder=self._offload_text_encoder,
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

    def cache_image_base64(self, image_b64: str) -> str:
        image_id = f"img_{uuid.uuid4().hex}"
        image_bytes = base64.b64decode(image_b64)
        with self._image_lock:
            self._image_cache[image_id] = image_bytes
            self._image_cache_order.append(image_id)
            while len(self._image_cache_order) > self._max_cached_images:
                oldest = self._image_cache_order.pop(0)
                self._image_cache.pop(oldest, None)
        return image_id

    def get_cached_image(self, image_id: str) -> bytes | None:
        with self._image_lock:
            return self._image_cache.get(image_id)


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
    include_admin_log: bool = False
    include_final_event: bool = False


class OpenAIImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "Z-image-turbo"
    prompt: str
    n: int = 1
    size: str = "512x512"
    response_format: Literal["b64_json", "url"] = "b64_json"
    background: str | None = None
    quality: str | None = None
    style: str | None = None
    user: str | None = None
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


@app.exception_handler(HTTPException)
def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if exc.detail is not None else "Request failed"
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=exc.status_code,
            content=_openai_error_body(message=_extract_error_message(detail)),
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=422,
            content=_openai_error_body(
                message=_extract_error_message(exc.errors()),
                error_type="invalid_request_error",
            ),
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    message = f"Internal server error: {exc}"
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=500,
            content=_openai_error_body(
                message=message,
                error_type="server_error",
                code="internal_error",
            ),
        )
    return JSONResponse(status_code=500, content={"message": message})


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


@app.get("/v1/images/{image_id}")
def get_generated_image(image_id: str) -> Response:
    image_bytes = service.get_cached_image(image_id)
    if image_bytes is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=image_bytes, media_type="image/png")


@app.post("/v1/chat/completions", response_model=None)
def openai_chat_completions(request: Request, body: OpenAIChatCompletionsRequest) -> dict[str, Any] | StreamingResponse:

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

    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    message_text = f"Image generation completed in {elapsed:.2f}s."
    image_id = service.cache_image_base64(image_b64)
    image_url = f"{_public_base_url(request)}/v1/images/{image_id}"
    text_block = {
        "type": "text",
        "text": message_text,
    }
    image_block = {
        "type": "image_url",
        "image_url": {
            "url": image_url,
        },
    }
    content_blocks = [
        text_block,
        image_block,
    ]

    if body.stream:
        return StreamingResponse(
            _openai_chat_stream_chunks(
                completion_id=completion_id,
                created=created,
                model=body.model,
                text_block=text_block,
                image_block=image_block,
                message_text=message_text,
                image_url=image_url,
                include_admin_log=body.include_admin_log,
                include_final_event=body.include_final_event,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content_blocks,
                },
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _parse_image_size(size: str) -> tuple[int, int]:
    try:
        width, height = (int(value) for value in size.lower().split("x", maxsplit=1))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="size must be formatted as WIDTHxHEIGHT, e.g. 1024x1024.")
    if width <= 0 or height <= 0:
        raise HTTPException(status_code=400, detail="size values must be positive integers.")
    return width, height


def _generate_images(
    *,
    request: Request,
    prompt: str,
    n: int,
    size: str,
    response_format: Literal["b64_json", "url"],
    num_inference_steps: int,
    guidance_scale: float,
    seed: int | None,
) -> dict[str, Any]:
    if n < 1:
        raise HTTPException(status_code=400, detail="n must be >= 1.")

    width, height = _parse_image_size(size)

    images: list[dict[str, str]] = []
    for index in range(n):
        current_seed = seed + index if seed is not None else None
        try:
            image_b64, _ = service.generate_image_base64(
                prompt=prompt,
                height=height,
                width=width,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                seed=current_seed,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc

        if response_format == "url":
            image_id = service.cache_image_base64(image_b64)
            images.append({"url": f"{_public_base_url(request)}/v1/images/{image_id}"})
        else:
            images.append({"b64_json": image_b64})

    return {
        "created": int(time.time()),
        "data": images,
    }


def _edit_image_bytes(
    *,
    image_bytes: bytes,
    prompt: str,
    size: str,
) -> str:
    width, height = _parse_image_size(size)

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=400, detail="Invalid image file.") from exc

    image = image.resize((width, height), Image.Resampling.LANCZOS)

    prompt_lc = prompt.lower()

    if any(token in prompt_lc for token in ("enhance", "enhanced", "clean", "improve")):
        image = ImageEnhance.Sharpness(image).enhance(1.2)
        image = ImageEnhance.Contrast(image).enhance(1.08)

    if any(token in prompt_lc for token in ("bright", "lighter", "lighten")):
        image = ImageEnhance.Brightness(image).enhance(1.12)

    if any(token in prompt_lc for token in ("contrast", "crisp")):
        image = ImageEnhance.Contrast(image).enhance(1.15)

    if any(token in prompt_lc for token in ("satur", "vibrant", "vivid")):
        image = ImageEnhance.Color(image).enhance(1.15)

    if "grayscale" in prompt_lc or "black and white" in prompt_lc:
        image = ImageOps.grayscale(image).convert("RGB")

    if "blur" in prompt_lc:
        image = image.filter(ImageFilter.GaussianBlur(radius=1.0))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


@app.post("/v1/images/generations")
@app.post("/v1/images/generations/")
@app.post("/images/generations")
@app.post("/images/generations/")
def openai_image_generations(request: Request, body: OpenAIImageGenerationRequest) -> dict[str, Any]:
    return _generate_images(
        request=request,
        prompt=body.prompt,
        n=body.n,
        size=body.size,
        response_format=body.response_format,
        num_inference_steps=body.num_inference_steps,
        guidance_scale=body.guidance_scale,
        seed=body.seed,
    )


@app.post("/v1/images/edits")
@app.post("/v1/images/edits/")
@app.post("/images/edits")
@app.post("/images/edits/")
async def openai_image_edits(
    request: Request,
    model: str = Form("Z-image-turbo"),
    prompt: str = Form(...),
    image: UploadFile = File(...),
    mask: UploadFile | None = File(None),
    n: int = Form(1),
    size: str = Form("512x512"),
    response_format: Literal["b64_json", "url"] = Form("b64_json"),
    background: str | None = Form(None),
    quality: str | None = Form(None),
    style: str | None = Form(None),
    user: str | None = Form(None),
    num_inference_steps: int = Form(8),
    guidance_scale: float = Form(0.0),
    seed: int | None = Form(None),
) -> dict[str, Any]:
    _ = model
    _ = mask
    _ = background
    _ = quality
    _ = style
    _ = user

    if not image.filename:
        raise HTTPException(status_code=400, detail="image file is required.")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="image file is empty.")

    if n < 1:
        raise HTTPException(status_code=400, detail="n must be >= 1.")

    edited_images: list[dict[str, str]] = []
    for _index in range(n):
        edited_b64 = _edit_image_bytes(image_bytes=image_bytes, prompt=prompt, size=size)
        if response_format == "url":
            image_id = service.cache_image_base64(edited_b64)
            edited_images.append({"url": f"{_public_base_url(request)}/v1/images/{image_id}"})
        else:
            edited_images.append({"b64_json": edited_b64})

    return {
        "created": int(time.time()),
        "data": edited_images,
    }


@app.post("/api/chat", response_model=None)
def ollama_chat(body: OllamaChatRequest) -> dict[str, Any] | StreamingResponse:

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
    message_text = f"Image generation completed in {elapsed:.2f}s."

    if body.stream:
        return StreamingResponse(
            _ollama_chat_stream_chunks(
                model=body.model,
                created_at=created_at,
                message_text=message_text,
                image_b64=image_b64,
                elapsed=elapsed,
            ),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return {
        "model": body.model,
        "created_at": created_at,
        "message": {
            "role": "assistant",
            "content": message_text,
            "images": [image_b64],
        },
        "done": True,
        "total_duration": int(elapsed * 1_000_000_000),
        "eval_count": 0,
        "eval_duration": int(elapsed * 1_000_000_000),
    }


@app.post("/api/generate", response_model=None)
def ollama_generate(body: OllamaGenerateRequest) -> dict[str, Any] | StreamingResponse:

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
    response_text = f"Image generation completed in {elapsed:.2f}s."

    if body.stream:
        return StreamingResponse(
            _ollama_generate_stream_chunks(
                model=body.model,
                created_at=created_at,
                response_text=response_text,
                image_b64=image_b64,
                elapsed=elapsed,
            ),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return {
        "model": body.model,
        "created_at": created_at,
        "response": response_text,
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