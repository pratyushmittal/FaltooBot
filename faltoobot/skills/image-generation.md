---
description: Use latest image generation models to create pictures or edit existing photos and creatives. Use this when the user asks for image generation, image editing, retouching, background changes, or any other visual modifications.
---

`openai` is already installed.

Image generation and editing can take up to 2 minutes on complex prompts. When using `run_shell_call`, use `timeout_ms` 120000 for complex image jobs. `60000` is usually enough for small drafts.

## Quality guidance

- `quality` supports `low`, `medium`, `high`, and `auto`.
- Use `quality="low"` for fast drafts, thumbnails, and quick iterations.
- On the image generation, default to `low` or `medium`. Do not use `high` unless the user explicitly asks for it.
- `size` also supports `auto`. `gpt-image-2` accepts any resolution that fits the guide constraints, and square images are typically fastest to generate.
- Prefer `1024x1024` while iterating. Common larger sizes include `1536x1024` for landscape and `1024x1536` for portrait.
- The default output format is `png`, but you can also request `jpeg` or `webp`.
- If using `jpeg` or `webp`, you can also set `output_compression`.
- Using `jpeg` is faster than `png`, so prioritize `jpeg` if latency is a concern.
- `background` supports `opaque` or `auto`. `gpt-image-2` does not support transparent backgrounds.

## Text-to-Image: one-shot generation

Use this when the user wants a brand new image from text only.

```python
from pathlib import Path
import base64
from openai import OpenAI

client = OpenAI()
prompt = """
A children's book drawing of a veterinarian using a stethoscope to
listen to the heartbeat of a baby otter.
"""

result = client.images.generate(
    model="gpt-image-2",
    prompt=prompt,
)

image_base64 = result.data[0].b64_json
path = Path("workspace/otter.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
print("prompt:", prompt)
```

## Image Editing: one-shot edit

Use this when the user gives an existing image and wants a direct edit in one step.

For `gpt-image-2`, do **not** set `input_fidelity`; the API already processes image inputs at high fidelity automatically.

```python
from pathlib import Path
import base64
from openai import OpenAI

client = OpenAI()
prompt = """
Generate a photorealistic image of a gift basket on a white background
labeled 'Relax & Unwind' with a ribbon and handwriting-like font,
containing all the items in the reference pictures.
"""

result = client.images.edit(
    model="gpt-image-2",
    image=[
        open("workspace/body-lotion.png", "rb"),
        open("workspace/bath-bomb.png", "rb"),
        open("workspace/incense-kit.png", "rb"),
        open("workspace/soap.png", "rb"),
    ],
    prompt=prompt,
)

image_base64 = result.data[0].b64_json
path = Path("workspace/gift-basket.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
print("prompt:", prompt)
```

You can also pass multiple reference images by supplying a list to `image=[...]`.

## Masked Edit

Use this when only part of the image should change.

The source image and mask must have the same format and size. The mask must include an alpha channel.

```python
from pathlib import Path
import base64
from openai import OpenAI

client = OpenAI()
prompt = "A sunlit indoor lounge area with a pool containing a flamingo"

result = client.images.edit(
    model="gpt-image-2",
    image=open("workspace/sunlit_lounge.png", "rb"),
    mask=open("workspace/mask.png", "rb"),
    prompt=prompt,
)

image_base64 = result.data[0].b64_json
path = Path("workspace/composition.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
print("prompt:", prompt)
```

## Multi-Turn Image Updates

Use the Responses API when the user wants iterative refinement across turns.

Typical flow:
1. first draft at `quality="low"`
2. one or more follow-up edits, still low or medium
3. final regeneration at `quality="high"`

### First turn

```python
from pathlib import Path
import base64
from openai import OpenAI

client = OpenAI()
prompt = "Generate an image of gray tabby cat hugging an otter with an orange scarf"

response = client.responses.create(
    model="gpt-5.4",
    input=prompt,
    tools=[{"type": "image_generation"}],
)

image_calls = [item for item in response.output if item.type == "image_generation_call"]
image_base64 = image_calls[0].result
path = Path("workspace/cat_and_otter.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
print("response id:", response.id)
print("image call id:", image_calls[0].id)
print("prompt:", prompt)
```

### Follow-up turn using `previous_response_id`

```python
from pathlib import Path
import base64
from openai import OpenAI

previous_response_id = "paste-the-response-id-here"
follow_up = "Now make it look realistic"

client = OpenAI()
response = client.responses.create(
    model="gpt-5.4",
    previous_response_id=previous_response_id,
    input=follow_up,
    tools=[{"type": "image_generation"}],
)

image_calls = [item for item in response.output if item.type == "image_generation_call"]
image_base64 = image_calls[0].result
path = Path("workspace/cat_and_otter_realistic.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
print("response id:", response.id)
print("image call id:", image_calls[0].id)
print("prompt:", follow_up)
```

### Final pass at high quality

Use the same follow-up pattern, but switch the tool settings to `quality="high"` for the final asset.

## When to use image call IDs instead

If you want to continue from a specific generated image rather than the whole earlier response chain, include the earlier `image_generation_call` id directly in a new `input` block. This is useful when a response has multiple images and you want to edit just one of them.

## Practical rules

- Expand rough user ideas into a fuller visual prompt before generation.
- Preserve identity details explicitly during edits: face, logo, clothing, pose, camera angle, lighting, etc.
- GPT Image can still struggle with precise layout and exact text placement, so avoid overpromising on typography-heavy designs.
- `gpt-image-2` does not currently support transparent backgrounds.
- When replying, always share the final prompt you used so the user can refine it further next turn.
