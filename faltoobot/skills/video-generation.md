---
description: Generate MiniMax H3 videos with OpenRouter. Use for text-to-video, first/last-frame video, reference-image video, audiovisual prompt writing, storyboards, product ads, animation, music videos, and short-form video production.
---

# MiniMax H3 video generation

Use MiniMax H3 through Faltoobot's OpenRouter video command. Video generation spends credits and can take several minutes, so confirm unclear assets, duration, aspect ratio, and non-negotiable visual details before submitting. If the user has already clearly asked to generate the video, do not ask for a second confirmation.

This skill incorporates the portable guidance and prompt examples published by MiniMax in the MiniMax-H3 repository. Preserve the official prompt field names and use the examples below verbatim as patterns rather than improvising a different H3 format.

## Current OpenRouter route

- Model: `minimax/hailuo-3`
- Duration: 5–15 seconds through OpenRouter
- Resolution: `2K`
- Aspect ratios: `21:9`, `16:9`, `4:3`, `1:1`, `3:4`, `9:16`
- Native stereo audio: supported and enabled by default
- Exact frame control: first frame, last frame, or both
- Reference-image guidance: use public, directly downloadable HTTPS image URLs

OpenRouter's capabilities can change. Before relying on a non-default parameter, inspect the current model metadata:

```bash
python - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("https://openrouter.ai/api/v1/videos/models") as response:
    models = json.load(response)["data"]
print(json.dumps(next(model for model in models if model["id"] == "minimax/hailuo-3"), indent=2))
PY
```

## Production workflow

1. Identify the generation mode and available assets.
2. Confirm duration and aspect ratio. Recommend 5 seconds for a quick experiment, 10 seconds for an ordinary complete beat, and 15 seconds only when the action needs it.
3. Lock identity, wardrobe, product shape/color, location, visual style, visible text, and anything that must not change.
4. Build a chronological audiovisual prompt. Concrete actions and sounds are more useful than abstract adjectives.
5. For multi-shot work, define exact cut times. For work longer than 15 seconds, generate separate clips with shared identity/style anchors and explicit tail-to-head continuity, then assemble them.
6. Write the prompt to a file. This avoids shell-quoting damage to long prompts.
7. Run `faltoobot generate-video`. Generation can take several minutes, so use a 20-minute shell-command timeout. Return its final Markdown media line unchanged so Faltoobot can send the MP4 on WhatsApp and Faltoochat can show the saved file.

Common lessons from MiniMax's bundled production skills:

- Establish references and approval anchors before expensive generation.
- Keep each reference's role explicit: exact frame, subject identity, style, environment, motion, or audio.
- Preserve product body color and distinctive physical details as hard constraints.
- For character work, repeat concise identity locks across clips.
- Plan typography as an in-frame visual event, not as an accidental subtitle. Spell all required visible text exactly in the prompt.
- Native H3 audio should describe ambience, physical sounds, dialogue, and music timing together with the visuals.
- For stitched clips, keep color treatment, light direction, camera direction, action state, and audio rhythm continuous.

## Generation modes

- **T2VA**: build the full audiovisual timeline from text.
- **I2VA**: start from the first frame and develop forward from it.
- **FL2VA**: describe the continuous path between the first and last frames.
- **L2VA**: infer a plausible opening and converge to the supplied last frame.
- **Ref2VA**: use reference images for subject, identity, environment, or style without forcing an exact frame.

OpenRouter currently accepts image references for this route. The broader H3 repository also describes reference video and audio inputs, but OpenRouter's generic API currently only promises audio/video reference handling for providers that expose it. Do not claim that H3 audio/video references work through OpenRouter unless the current model metadata or documentation confirms it.

## Official base prompt structure

T2VA starts directly with these fields:

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

I2VA always starts with:

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.
```

FL2VA always starts with:

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.
```

L2VA always starts with:

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.
```

`N` is the actual final shot and `S.SS` is the duration with exactly two decimal places.

### Shot and audio rules

- `[Shot 1]` has no timestamp.
- Later shots use strictly increasing cut times such as `[Shot 2] At 00:03.500, the camera cuts to...`.
- Express camera motion naturally with motion type, meaningful amplitude, and speed.
- Stable speakers use `(S1)`, `(S2)`, and so on.
- Dialogue uses `<d>[Language] exact words.</d>`.
- `overall_soundscape` summarizes ambience, physical actions, and non-verbal human sounds.
- `non_diegetic_music` describes audience-only music through instrumentation, speed, rhythm, and dynamics. Use `N/A` when absent.

## Official prompt examples

These examples are copied verbatim from MiniMax's `base-en.txt` guide.

### T2VA

```text
integrated_multimodal_description: [Shot 1] Live-action, cinematic, a medium-wide shot frames a baker opening the shutters of a small street bakery before sunrise. The camera pushes in with small amplitude at slow speed as the middle-aged baker with a calm, slightly raspy voice (S1) places a fresh loaf on the wooden counter and says: <d>[English] First batch of the morning.</d> [Shot 2] At 00:05.000, the camera cuts to a close-up of steam rising from the sliced bread while the baker's final words carry over from the previous shot.

overall_soundscape: Wooden shutters scrape open over a quiet street as trays clink softly inside the bakery. The doorbell rings once, followed by light footsteps and the crisp sound of bread being sliced.

non_diegetic_music: A soft acoustic-guitar pattern at a moderate tempo, joined by sparse upright-bass notes and a gentle fade at the end.
```

### I2VA

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, the young woman shown in <Picture 1> remains beside the rain-covered train window, preserving her appearance, clothing, seat position, and the carriage layout. The camera trucks right with small amplitude at slow speed as she lifts her gaze from the folded letter toward the passing city lights. Her reflection moves across the glass while the quiet, breathy young woman (S1) says: <d>[English] I get off at the next station.</d> She folds the letter along its existing crease.

overall_soundscape: The train wheels produce a steady metallic rhythm beneath a low ventilation hum. Rain ticks against the window while paper rustles softly in her hands.

non_diegetic_music: Sustained cello notes at a slow tempo with widely spaced piano tones, gradually decreasing in volume.
```

### FL2VA

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a rain-soaked cyclist begins in the position and framing established by Picture 1, holding a closed black umbrella beside a silver bicycle. The camera pulls out with small amplitude at slow speed as she releases the bicycle handle, raises the umbrella above her shoulder, and presses the runner upward until the canopy opens. Water rolls from the expanding fabric while she steps beneath it, rotates the handle into the final angle, and settles into the pose, spacing, and composition established by Picture 2 at the end of the shot.

overall_soundscape: Rain falls steadily on the pavement, followed by the metallic click of the umbrella runner and the soft snap of the canopy opening. Water drips from the bicycle frame as distant traffic passes.

non_diegetic_music: N/A
```

### L2VA

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot 1]) aligns with the 6.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, a close shot begins with an intact drinking glass near the edge of a dark wooden table, while the same hand and sleeve visible in <Picture 1> approach from the right. The camera pushes in with small amplitude at slow speed as the fingertips strike the rim. The glass tips, falls, and hits the floor with a sharp impact; cracks spread through it as fragments slide outward. Toward the end, the moving pieces lose momentum and settle into the exact broken arrangement, hand position, camera angle, lighting, and final composition established by <Picture 1>.

overall_soundscape: Fingertips tap the glass before it scrapes across the tabletop, falls, and breaks with a sharp crash. Small fragments scatter and gradually stop sliding across the floor.

non_diegetic_music: A low electronic pulse at a slow tempo, ending immediately after the glass breaks.
```

## Ref2VA prompt structure

When using reference images as guidance rather than exact frames, use these six sections in order:

```text
subject_definitions:
<Subject 1> is ...

summary:
[reference generation] ...

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - ...

detailed_description:
The target video is ...
[Shot 1] ...

overall_soundscape:
...

non_diegetic_music:
...
```

Reference labels remain stable across every section:

- `<Subject N>`: reusable visible identity, object, environment, clothing, style, action, or effect.
- `<Picture N>`: a concrete frame or storyboard/composition anchor.
- `<Video N>`: whole-video editing, continuation, camera, cuts, rhythm, or temporal structure.
- `<Audio N>`: copied or referenced voice, music, dialogue, rhythm, or effects.

Visible retention markers are `fully_preserved`, `partially_preserved`, `attribute_transfer`, and `weak_reference`. Audio markers are `fully_copy`, `partially_copy`, `reference`, and `weak_reference`.

## Run the generation

Write the final prompt:

```bash
mkdir -p .generated-videos
cat > .generated-videos/h3-prompt.txt <<'PROMPT'
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
PROMPT
```

Text-to-video with native audio:

```bash
faltoobot generate-video \
  --prompt-file .generated-videos/h3-prompt.txt \
  --output .generated-videos/h3-video.mp4 \
  --duration 5 \
  --resolution 2K \
  --aspect-ratio 16:9
```

First-frame video:

```bash
faltoobot generate-video \
  --prompt-file .generated-videos/h3-prompt.txt \
  --output .generated-videos/h3-video.mp4 \
  --first-frame-url 'https://example.com/first-frame.png' \
  --duration 5 \
  --resolution 2K \
  --aspect-ratio 16:9
```

First-and-last-frame video adds:

```bash
--first-frame-url 'https://example.com/first-frame.png' \
--last-frame-url 'https://example.com/last-frame.png'
```

Reference-image video adds one option per image:

```bash
--reference-image-url 'https://example.com/character-front.png' \
--reference-image-url 'https://example.com/style-reference.png'
```

Use `--no-audio` only when the user requests silence. The command prints a line such as:

```markdown
[Generated video](.generated-videos/h3-video.mp4)
```

Return that media line in the answer. Do not paste the API key, job response, signed download URL, or full prompt unless the user asks for them.
