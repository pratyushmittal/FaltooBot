---
description: Using Gemini image generation for new images and simple image edits. Use this when you need a real image file from a prompt or reference image, including aspect ratio or image size control.
---

`google-genai` is already installed.

Prefer Python + Gemini for image tasks. Use the configured Gemini settings and save outputs inside the current workspace.

Use these config values when generating or editing images:
- Gemini API key: `[gemini].gemini_api_key`
- Gemini model: `[gemini].model`

Preferred pattern:
- use the configured Gemini model first
- for text-to-image, start with one clear prompt and save one output file
- for edits, pass the reference image with a short instruction that says what should change and what should stay unchanged
- when aspect ratio or image size matters, set it explicitly
- if image generation fails, do not take an alternative path; return a one-line error with the exact failure reason

Simple text-to-image example:

```bash
uv run python - <<'PY'
from pathlib import Path
from google import genai
from faltoobot.config import build_config

config = build_config()
api_key = config.gemini_api_key
model = config.gemini_model
prompt = "A cute robot mascot, clean 3D icon, white background, no text"
out = Path("robot.png")

client = genai.Client(api_key=api_key)
response = client.models.generate_content(model=model, contents=prompt)
for part in response.parts:
    if part.inline_data:
        part.as_image().save(out)
        print(out)
        break
PY
```

Simple image edit example:

```bash
uv run python - <<'PY'
from pathlib import Path
from google import genai
from PIL import Image
from faltoobot.config import build_config

api_key = build_config().gemini_api_key
prompt = "Add a small knitted wizard hat. Keep the cat and background natural."
image = Image.open("cat.png")
out = Path("cat-wizard.png")

client = genai.Client(api_key=api_key)
response = client.models.generate_content(
    model="gemini-3.1-flash-image-preview",
    contents=[prompt, image],
)
for part in response.parts:
    if part.inline_data:
        part.as_image().save(out)
        print(out)
        break
PY
```

When aspect ratio or image size matters:

```bash
uv run python - <<'PY'
from pathlib import Path
from google import genai
from google.genai import types
from faltoobot.config import build_config

api_key = build_config().gemini_api_key
prompt = "A cinematic mountain landscape at sunrise"
out = Path("landscape.png")

client = genai.Client(api_key=api_key)
response = client.models.generate_content(
    model="gemini-3.1-flash-image-preview",
    contents=prompt,
    config=types.GenerateContentConfig(
        image_config=types.ImageConfig(
            aspect_ratio="16:9",
            image_size="2K",
        )
    ),
)
for part in response.parts:
    if part.inline_data:
        part.as_image().save(out)
        print(out)
        break
PY
```

Useful patterns:
- generate a new image from a prompt
- edit an existing image with a reference
- create fixed-ratio outputs for banners, thumbnails, or hero images
- save the image and print the final path
