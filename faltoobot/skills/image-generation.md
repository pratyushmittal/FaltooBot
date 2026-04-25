---
description: Use latest image generation models to create pictures or edit existing photos and creatives. Use this when the user asks for image generation, image editing, retouching, background changes, or any other visual modifications.
---

`openai` is already installed in `run_in_python_shell`. Prefer `run_in_python_shell` for image jobs.

## Normal Image Generation

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
path = Path("otter.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
```

## Image Editing

Use this when the user gives an existing image and wants to make edits. You can also use multiple images as input.

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

with (
    open("body-lotion.png", "rb") as body_lotion,
    open("bath-bomb.png", "rb") as bath_bomb,
    open("incense-kit.png", "rb") as incense_kit,
    open("soap.png", "rb") as soap,
):
    result = client.images.edit(
        model="gpt-image-2",
        image=[body_lotion, bath_bomb, incense_kit, soap],
        prompt=prompt,
    )

image_base64 = result.data[0].b64_json
path = Path("gift-basket.png")
path.write_bytes(base64.b64decode(image_base64))
print(path)
```

## Important

When replying to the user, narrate the final prompt you used. If the user gave a short or rough idea, expand it into a better prompt for generation, but also share that expanded prompt back so the user can refine it further in the next turn.
