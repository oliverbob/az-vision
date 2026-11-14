# Z-Image Chat Frontend (SvelteKit)

Mobile-first chat UI for fast model interface prototyping.

## 1) Install

```bash
cd frontend
npm install
```

## 2) Configure backend (optional)

```bash
cp .env.example .env
```

Set `MODEL_CHAT_URL` to your chat backend endpoint. By default, `.env.example` points to:

`http://127.0.0.1:9090/`

The local frontend API route is a caller/proxy only. It forwards requests to OpenAI-compatible endpoints on the configured backend base URL:

- text-to-image/chat:

`POST /v1/chat/completions`

- image upload (image-to-image **and** image-to-text):

`POST /v1/images/chat`

You can attach one or more images in the UI. The composer shows a mode selector next to the attachment:

| Mode | Behaviour |
| --- | --- |
| **Auto** (default) | Questions ("what is this?", "how many people?", "read the text") get a text answer; instructions ("make it a watercolor", "remove the background") get an edited image. |
| **Edit image** | Always image-to-image. Multiple images are combined server-side before instruction-based editing. |
| **Ask about it** | Always image-to-text. If the vision model only accepts one image at a time, each image is described separately and the answers are labelled. |

### Follow-up edits (conversational image-to-image)

After an edit returns a result, that result becomes the **working image**: it is shown as a green chip above the composer, and your next plain-text message refines it instead of generating a brand-new image.

```
[upload photo] "make it a pencil sketch"        -> edited image
               "now change the background to a snowy mountain" -> edits that result
               "add falling snow"               -> edits that result
```

- Clear the chip (✕) to go back to plain text-to-image generation.
- A plain text-to-image generation does **not** arm the chip, so unrelated prompts stay independent. Use the **Edit this image** button under any generated result to start editing from it.
- Follow-ups are always sent as `mode=edit`, so a polite phrasing like "can you make it brighter?" is never mistaken for a question.
- The working image resets when you switch or start a conversation.

Each follow-up re-sends the previous result at `ZIMAGE_EDIT_STRENGTH` (default `0.6`). Raise it for more dramatic changes per step, lower it to keep more of the original.

Image-to-text is served by a local vision model over Ollama — see `ZIMAGE_VISION_*` in the backend README. `GET /health` reports whether it is reachable.

The lower-level backend endpoints are still available directly:

- `POST /v1/images/edits` — OpenAI-compatible image edit
- `POST /v1/images/describe` — multipart image-to-text
- `POST /v1/chat/completions` and `POST /api/chat` — accept `image_url` blocks / `images` and answer in text when the prompt is a question

The frontend route returns:

```json
{
  "reply": "string",
  "imageUrl": "string|null"
}
```

If `MODEL_CHAT_URL` is not set, it defaults to `http://127.0.0.1:9090/` and still calls the backend.

## 3) Run

```bash
npm run dev

# Or run on 4040
npm run dev -- --port 4040
```

Open: `http://localhost:4040`

## Reverse proxy (Caddy HTTPS)

If you expose `npm run dev` through Caddy and see WebSocket/HMR errors (`Failed to connect to websocket`, `502` on `+page.svelte`), set these in `frontend/.env`:

```dotenv
VITE_HMR_HOST=your-domain.example
VITE_HMR_PROTOCOL=wss
VITE_HMR_CLIENT_PORT=443
```

Then restart the frontend dev server.

Minimal Caddy example:

```caddyfile
your-domain.example {
  encode gzip zstd
  reverse_proxy 127.0.0.1:4040
}
```

For public hosting, prefer production mode instead of Vite dev:

```bash
npm run build
npm run preview -- --host 0.0.0.0 --port 4040
```