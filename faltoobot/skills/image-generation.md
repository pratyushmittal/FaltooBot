---
description: Use latest image generation models to create pictures or edit existing photos and creatives. Use this when the user asks for image generation, image editing, retouching, background changes, or *any* other visual modifications.
---

`google-genai` is already installed in `run_in_python_shell`.

Image generation can take up to 60 seconds. When using `run_shell_call`, set `timeout_ms` to 60000 for image generation and image editing commands.

## Text-to-Image: Image Generation

Use this when the user wants a brand new image from a prompt.

**Describe the scene, don't just list keywords.** The model's core strength is its deep language understanding. A narrative, descriptive paragraph will almost always produce a better, more coherent image than a list of disconnected words.

Simple usage example:

```python
from pathlib import Path
import base64
from google import genai

client = genai.Client()
response = client.interactions.create(
    model="{gemini_model}",
    input="Create a photorealistic image of a siamese cat with a green left eye and a blue right one and red patches on his face and a black and pink nose",
    tools=[{"type": "google_search", "search_types": ["web_search"]}],
    response_modalities=["image"],
)

for output in response.outputs or []:
    if getattr(output, "type", None) != "image":
        continue
    raw = base64.b64decode(output.data) if isinstance(output.data, str) else bytes(output.data)
    path = Path("workspace/siamese-cat.jpg")
    path.write_bytes(raw)
    # comment: print the saved path so this image can be sent back to the user.
    print(path)
    break
# comment: print the interaction id so it can be reused next turn if the user requests edits.
print("response id:", response.id)
```


## Text-and-Image-to-Image: Image Editing

Use this when the user gives an existing image and asks for changes.

Example pattern:

```python
from pathlib import Path
import base64
import mimetypes
from google import genai


def image_part(path: str) -> dict[str, str]:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    data = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {"type": "image", "mime_type": mime_type, "data": data}


client = genai.Client()
instruction = "Create a picture of my cat eating a nano-banana in a fancy restaurant under the Gemini constellation"
response = client.interactions.create(
    model="{gemini_model}",
    input=[
        {"type": "text", "text": instruction},
        image_part("workspace/cat.png"),
    ],
    tools=[{"type": "google_search", "search_types": ["web_search"]}],
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
```

You can pass multiple reference images too. Gemini can use up to 14 images in one editing request. This is useful for group photos, collages, moodboards, or combining several references into one composition.

Example with multiple reference images:

```python
from pathlib import Path
import base64
import mimetypes
from google import genai


def image_part(path: str) -> dict[str, str]:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    data = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {"type": "image", "mime_type": mime_type, "data": data}


prompt = "An office group photo of these people, they are making funny faces."
aspect_ratio = "5:4"
resolution = "2K"

client = genai.Client()
response = client.interactions.create(
    model="{gemini_model}",
    input=[
        {"type": "text", "text": prompt},
        image_part("workspace/person1.png"),
        image_part("workspace/person2.png"),
        image_part("workspace/person3.png"),
        image_part("workspace/person4.png"),
        image_part("workspace/person5.png"),
    ],
    tools=[{"type": "google_search", "search_types": ["web_search"]}],
    response_modalities=["text", "image"],
    generation_config={
        "image_config": {
            "aspect_ratio": aspect_ratio,
            "image_size": resolution,
        }
    },
)

for output in response.outputs or []:
    if getattr(output, "type", None) == "text":
        print(output.text)
        continue
    if getattr(output, "type", None) != "image":
        continue
    raw = base64.b64decode(output.data) if isinstance(output.data, str) else bytes(output.data)
    path = Path("workspace/office-group.jpg")
    path.write_bytes(raw)
    # comment: print the saved path so this image can be sent back to the user.
    print(path)
# comment: print the interaction id so it can be reused next turn if the user requests edits.
print("response id:", response.id)
```

## Multi-Turn Image Updates

For native multi-turn image work, prefer `client.chats.create(...)` and keep the `chat` object alive across turns. When using `run_in_python_shell`, set `continue_session=True` on the first turn if you plan to reuse that Python session in follow-up turns.


First turn example:

```python
from google import genai
from google.genai import types

client = genai.Client()
chat = client.chats.create(
    model="{gemini_model}",
    config=types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        tools=[{"google_search": {}}],
    ),
)

message = "Create a vibrant infographic that explains photosynthesis as if it were a recipe for a plant's favorite food. Show the ingredients (sunlight, water, CO2) and the finished dish (sugar/energy). The style should be like a page from a colorful kids' cookbook, suitable for a 4th grader."
response = chat.send_message(message)

for part in response.parts:
    if part.text is not None:
        print(part.text)
        continue
    image = part.as_image()
    if image is None:
        continue
    image.save("workspace/photosynthesis.png")
    print("workspace/photosynthesis.png")
```

Follow-up turn in the same Python session:

```python
from google.genai import types

message = "Update this infographic to be in Spanish. Do not change any other elements of the image."
aspect_ratio = "16:9"  # "1:1","1:4","1:8","2:3","3:2","3:4","4:1","4:3","4:5","5:4","8:1","9:16","16:9","21:9"
resolution = "2K"  # "512", "1K", "2K", "4K"

response = chat.send_message(
    message,
    config=types.GenerateContentConfig(
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size=resolution,
        ),
    ),
)

for part in response.parts:
    if part.text is not None:
        print(part.text)
        continue
    image = part.as_image()
    if image is None:
        continue
    image.save("workspace/photosynthesis_spanish.png")
    print("workspace/photosynthesis_spanish.png")
```

Use multi-turns when the user wants:
- iterative refinements
- small edits on the last generated image
- a sequence like “make it brighter”, “now remove the background”, “now make it more minimal”

`client.interactions` also supports multi-turn image work. Save the printed `interaction.id`, then pass it as `previous_interaction_id` on the next turn. This works even without Python session persistence.

## Good Prompt Examples

Icons, stickers and assets:
To create stickers, icons, or assets, be explicit about the style and request a white background.

Example: "An icon representing a cute dog. The background is white. Make the icons in a colorful and tactile 3D style. No text."

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
For realistic images, use photography terms. Mention camera angles, lens types, lighting, and fine details to guide the model toward a photorealistic result.
Example: "A photorealistic close-up portrait of an elderly Japanese ceramicist with deep, sun-etched wrinkles and a warm, knowing smile. He is carefully inspecting a freshly glazed tea bowl. The setting is his rustic, sun-drenched workshop. The scene is illuminated by soft, golden hour light streaming through a window, highlighting the fine texture of the clay.

Captured with an 85mm portrait lens, resulting in a soft, blurred background (bokeh). The overall mood is serene and masterful. Vertical portrait orientation."

## Important

When replying to the user, narrate the final prompt you used. If the user gave a short or rough idea, expand it into a better prompt for generation, but also share that expanded prompt back so the user can refine it further in the next turn.
