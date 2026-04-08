---
description: Use latest image generation models to create pictures or edit existing photos and creatives. Use this when the user asks for image generation, image editing, retouching, background changes, or *any* other visual modifications.
---

`google-genai` is already installed.

Image generation can take up to 60 seconds. When using `run_shell_call`, set `timeout_ms` to 60000 for image generation and image editing commands.


Simple usage example:

```bash
uv run python - <<'PY'
from pathlib import Path
import base64
from google import genai

client = genai.Client()
response = client.interactions.create(
    model="{gemini_model}",
    input="Create a cozy reading nook with warm lighting and indoor plants",
    response_modalities=["image"],
)

for output in response.outputs or []:
    if getattr(output, "type", None) != "image":
        continue
    raw = base64.b64decode(output.data) if isinstance(output.data, str) else bytes(output.data)
    path = Path("workspace/cozy-reading.jpg")
    path.write_bytes(raw)
    # comment: print the saved path so this image can be sent back to the user.
    print(path)
    break
# comment: print the interaction id so it can be reused next turn if the user requests edits.
print("response id:", response.id)
PY
```

## Text-to-Image: Image Generation

Use this when the user wants a brand new image from a prompt.

Example prompts:
- Create a product hero image for a matte black water bottle on a stone pedestal, soft studio lighting, premium feel.
- Generate a playful sticker-style illustration of a sleepy orange cat wrapped in a burrito.
- Make a cinematic travel poster of Kyoto in the rain at night, neon reflections, vertical composition.

Example pattern:

```bash
uv run python - <<'PY'
from pathlib import Path
import base64
from google import genai

client = genai.Client()
prompt = "Create a flat vector illustration of a banana-shaped spaceship flying over Saturn"
response = client.interactions.create(
    model="{gemini_model}",
    input=prompt,
    response_modalities=["image"],
)

for output in response.outputs or []:
    if getattr(output, "type", None) != "image":
        continue
    raw = base64.b64decode(output.data) if isinstance(output.data, str) else bytes(output.data)
    path = Path("workspace/banana-spaceship.jpg")
    path.write_bytes(raw)
    # comment: print the saved path so this image can be sent back to the user.
    print(path)
    break
# comment: print the interaction id so it can be reused next turn if the user requests edits.
print("response id:", response.id)
PY
```

## Text-and-Image-to-Image: Image Editing

Use this when the user gives an existing image and asks for changes.

Keep the edit instruction concrete:
- say what should change
- say what should stay unchanged
- mention style, lighting, framing, or background when relevant

Example edits:
- Remove the background and replace it with a clean white studio backdrop.
- Add subtle evening lighting, but keep the subject pose and clothing unchanged.
- Turn this sketch into a polished app-icon concept while preserving the original composition.

Example pattern:

```bash
uv run python - <<'PY'
from pathlib import Path
import base64
from google import genai
from PIL import Image

client = genai.Client()
reference = Image.open(Path("workspace/photo.png"))
instruction = "Add a red umbrella. Keep the person, pose, and street background unchanged."
response = client.interactions.create(
    model="{gemini_model}",
    input=[instruction, reference],
    response_modalities=["image"],
)

for output in response.outputs or []:
    if getattr(output, "type", None) != "image":
        continue
    raw = base64.b64decode(output.data) if isinstance(output.data, str) else bytes(output.data)
    path = Path("workspace/photo-red-umbrella.jpg")
    path.write_bytes(raw)
    # comment: print the saved path so this image can be sent back to the user.
    print(path)
    break
# comment: print the interaction id so it can be reused next turn if the user requests edits.
print("response id:", response.id)
PY
```

## Multi-Turn Image Updates

`client.interactions` supports multi-turn image work. After one generation or edit finishes, keep the printed `interaction.id`. On the next turn, pass it as `previous_interaction_id` so Gemini can continue from that earlier result.

Example follow-up pattern:

```bash
uv run python - <<'PY'
from pathlib import Path
import base64
from google import genai

previous_interaction_id = "paste-the-previous-interaction-id-here"
client = genai.Client()
response = client.interactions.create(
    model="{gemini_model}",
    input="Make the mascot more original. Give it asymmetrical ears, blue overalls, and a yellow scarf. Keep the rest unchanged.",
    previous_interaction_id=previous_interaction_id,
    response_modalities=["image"],
)

for output in response.outputs or []:
    if getattr(output, "type", None) != "image":
        continue
    raw = base64.b64decode(output.data) if isinstance(output.data, str) else bytes(output.data)
    path = Path("workspace/mascot-followup.jpg")
    path.write_bytes(raw)
    # comment: print the saved path so this image can be sent back to the user.
    print(path)
    break
# comment: print the interaction id so it can be reused next turn if the user requests edits.
print("response id:", response.id)
PY
```

Use multi-turns when the user wants:
- iterative refinements
- small edits on the last generated image
- a sequence like “make it brighter”, “now remove the background”, “now make it more minimal”

## Good Prompt Examples

Icons, stickers and assets:
- "An icon representing a cute dog. The background is white. Make the icons in a colorful and tactile 3D style. No text."

Professional product shots:
- "A photo of a glossy magazine cover, the minimal blue cover has the large bold words Nano Banana. The text is in a serif font and fills the view. No other text. In front of the text there is a portrait of a person in a sleek and minimal dress. She is playfully holding the number 2, which is the focal point. Put the issue number and "Feb 2026" date in the corner along with a barcode. The magazine is on a shelf against an orange plastered wall, within a designer store."

Isometric miniature based on live data:
- "Present a clear, 45° top-down isometric miniature 3D cartoon scene of London, featuring its most iconic landmarks and architectural elements. Use soft, refined textures with realistic PBR materials and gentle, lifelike lighting and shadows. Integrate the current weather conditions directly into the city environment to create an immersive atmospheric mood. Use a clean, minimalistic composition with a soft, solid-colored background. At the top-center, place the title "London" in large bold text, a prominent weather icon beneath it, then the date (small text) and temperature (medium text). All text must be centered with consistent spacing, and may subtly overlap the tops of the buildings."

High fidelity detail preservation:
- "Put this logo on a high-end ad for a banana scented perfume. The logo is perfectly integrated into the bottle."

Artistic variations:
- "A photo of an everyday scene at a busy cafe serving breakfast. In the foreground is an anime man with blue hair, one of the people is a pencil sketch, another is a claymation person"

Image from search results:
- "Use search to find how the Gemini 3 Flash launch has been received. Use this information to write a short article about it (with headings). Return a photo of the article as it appeared in a design focused glossy magazine. It is a photo of a single folded over page, showing the article about Gemini 3 Flash. One hero photo. Headline in serif."

Photorealistic image generation:
- "Make a photo that is perfectly isometric. It is not a miniature, it is a captured photo that just happened to be perfectly isometric. It is a photo of a beautiful modern garden. There's a large 2 shaped pool and the words: Nano Banana 2."
