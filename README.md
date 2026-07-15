# DIGIT Nodes for ComfyUI

**Production-grade AI nodes that connect directly to Google Cloud Vertex AI.** No middleman, no proxy, no rate limits beyond your own GCP quota. Your usage bills directly to your GCP account at Google's API pricing — no markup.

DIGIT Nodes give you raw, unfiltered access to Google's most powerful generative AI models from inside ComfyUI. Every node auto-detects your GCP credentials, so once you're authenticated, everything just works.

---

## Why DIGIT Nodes?

Most ComfyUI nodes that talk to Google's models go through third-party proxies or require API keys from wrapper services. DIGIT Nodes skip all of that. You authenticate once with `gcloud`, and every node talks directly to Vertex AI using the official `google-genai` SDK.

This means:

- **No API key management** — uses your existing GCP credentials
- **No rate limit surprises** — you control your own quotas
- **No data routing through third parties** — your prompts and outputs stay between you and Google
- **Lossless video output** — the only way to get uncompressed Veo output is through the API with a GCS bucket, and DIGIT Nodes support this natively
- **Auto-detection** — on GCP instances (Compute Engine, GKE), project ID and region are detected automatically from the metadata service. On local machines, it uses your `gcloud` login.

---

## The Nodes

### DIGIT Gemini Image

Generate and edit images using Google's Gemini image models directly through Vertex AI.

This is a unified node — it handles text-to-image, image editing, and multi-image composition all in one place. Feed it a prompt and it generates an image. Feed it a prompt plus up to 3 input images and it edits or combines them.

**Supported Models:**

| Model | Internal Name | What It Is |
|-------|--------------|------------|
| Gemini 3.1 Flash Image | `gemini-3.1-flash-image` | Nano Banana 2 — balanced quality and speed. Default choice. |
| Gemini 3.1 Flash-Lite Image | `gemini-3.1-flash-lite-image` | Nano Banana 2 Lite — fastest and most cost-efficient; 1K resolution only. |
| Gemini 3 Pro Image | `gemini-3-pro-image-preview` | Nano Banana Pro — higher quality, slower. |
| Gemini 2.5 Flash Image | `gemini-2.5-flash-image` | Previous generation. Still solid. |

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| prompt | STRING | — | Your image generation prompt. Required. |
| model | COMBO | gemini-3.1-flash-image | Which Gemini image model to use. |
| aspect_ratio | COMBO | 16:9 | Output aspect ratio. 12 options: 1:1, 2:3, 3:2, 3:4, 4:1, 4:3, 4:5, 5:4, 8:1, 9:16, 16:9, 21:9. |
| resolution | COMBO | 1K | Output resolution: 1K, 2K, or 4K. Nano Banana 2 Lite supports 1K only (the dropdown updates automatically). |
| seed | INT | 0 | Reproducibility seed. 0 = random each run. Max 2,147,483,647. |
| temperature | FLOAT | 1.0 | Creativity control. Range 0.0–2.0. Higher = more creative/varied. |
| image1 … image9 | IMAGE | — | Optional input images for editing, style transfer, or composition. Batched images are iterated automatically. |
| batch_count | INT | 1 | Number of images to generate (1–128). All API calls fire in parallel, each with its own seed; results return as one IMAGE batch. |
| system_instruction | STRING | (built-in) | System prompt that tells the model to always produce images. Customizable. |
| top_p, top_k | FLOAT/INT | 1.0 / 32 | Nucleus and top-k sampling parameters for fine-tuning output diversity. |
| harassment_threshold | COMBO | BLOCK_NONE | Safety filter for harassment content. Options: BLOCK_NONE, BLOCK_ONLY_HIGH, BLOCK_MEDIUM_AND_ABOVE, BLOCK_LOW_AND_ABOVE. |
| hate_speech_threshold | COMBO | BLOCK_NONE | Safety filter for hate speech. |
| sexually_explicit_threshold | COMBO | BLOCK_NONE | Safety filter for sexually explicit content. |
| dangerous_content_threshold | COMBO | BLOCK_NONE | Safety filter for dangerous content. |
| gcp_project_id | STRING | (auto) | Your GCP project ID. Leave blank to auto-detect. |
| gcp_region | STRING | global | Vertex AI region. "global" uses Google's default routing. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| image | IMAGE | Generated RGBA image tensor. |
| text | STRING | Any text the model returned alongside the image. |

**Built-in resilience:** Automatic retry with exponential backoff on 429 (rate limit) and 503 (service unavailable) errors. Up to 3 retries.

---

### DIGIT Veo Video

Generate videos using Google's Veo models directly through Vertex AI. Supports text-to-video, image-to-video, frame interpolation, and reference-based generation — all in one unified node.

The node auto-detects which mode to use based on what you connect:
- **Nothing connected** → text-to-video
- **first_frame connected** → image-to-video (animates from your image)
- **first_frame + last_frame** → interpolation (generates video between two frames)
- **reference images connected** → reference-based (generates video maintaining visual consistency with reference images)

**Supported Models:**

| Model | Internal Name | Description |
|-------|--------------|-------------|
| Veo 3.1 | `veo-3.1-generate-preview` | Latest and most capable. Default choice. |
| Veo 3.1 Fast | `veo-3.1-fast-generate-preview` | Faster generation, slightly lower quality. |
| Veo 3.0 | `veo-3.0-generate-001` | Previous generation, very capable. |
| Veo 3.0 Fast | `veo-3.0-fast-generate-001` | Fast version of Veo 3.0. |
| Veo 2.0 | `veo-2.0-generate-001` | Older model, still available. |

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| prompt | STRING | — | Video generation prompt. Required. |
| model | COMBO | veo-3.1-generate-preview | Which Veo model to use. |
| aspect_ratio | COMBO | 16:9 | 16:9 (landscape) or 9:16 (portrait). |
| resolution | COMBO | 720p | 720p or 1080p. |
| duration_seconds | INT | 8 | Video length: 4, 6, or 8 seconds. |
| generate_audio | BOOLEAN | true | Whether Veo generates synchronized audio. |
| seed | INT | 0 | Reproducibility seed. 0 = random. |
| first_frame | IMAGE | — | Starting frame for image-to-video mode. |
| last_frame | IMAGE | — | Ending frame for interpolation mode (requires first_frame). |
| reference1, reference2, reference3 | IMAGE | — | Reference images for style/asset consistency. Cannot be used with first_frame. |
| negative_prompt | STRING | — | What you don't want in the video. |
| person_generation | COMBO | allow_adult | "allow_adult" or "dont_allow". |
| sample_count | INT | 1 | Generate 1–4 videos per run. |
| compression_quality | COMBO | optimized | "optimized" = compressed MP4 in API response. "lossless" = full-quality MP4 written to your GCS bucket. |
| output_gcs_uri | STRING | — | GCS bucket path for lossless output, e.g. `gs://my-bucket/output/`. Required when using lossless compression. |
| enhance_prompt | BOOLEAN | true | Let Veo enhance your prompt for better results. |
| gcp_project_id | STRING | (auto) | Your GCP project ID. |
| gcp_region | STRING | us-central1 | Vertex AI region. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| video | VIDEO | First generated video as a ComfyUI VIDEO type. |
| video_paths | VEO_PATHS | List of all generated video file paths (for batch saving). |
| status | STRING | Generation details: model, mode, duration, resolution, file paths. |

**About lossless vs. optimized:**

Every Veo video generation — whether through Google's AI Studio, the web console, Freepik, Weavy, or any other third-party tool — returns the **optimized** (compressed) version. The **only** way to get lossless output is through the API with a GCS bucket URI. This node is one of the only tools that gives you that option. You can be on your local Mac, a Linux workstation, or a cloud VM — it doesn't matter where you run ComfyUI. As long as the API call includes `output_gcs_uri`, the lossless file goes to your bucket.

**Built-in resilience:** Automatic retry with exponential backoff. 20-second polling interval for long-running operations. Multiple response parsing fallback paths for SDK version compatibility.

---

### DIGIT LLM Query

Send text (and optionally images) to Gemini LLM models and get text responses. Useful for prompt engineering, image analysis, script writing, or any text generation task within a ComfyUI workflow.

**Supported Models:**

| Model | Internal Name | Description |
|-------|--------------|-------------|
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | Latest and most capable text model. Default. |
| Gemini 2.5 Pro | `gemini-2.5-pro` | Very strong, slightly older. |
| Gemini 2.5 Flash | `gemini-2.5-flash` | Fast and cost-effective. |
| Gemini 2.5 Flash Lite | `gemini-2.5-flash-lite` | Fastest and cheapest. |

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| model | COMBO | gemini-3.1-pro-preview | Which Gemini text model to use. |
| prompt | STRING | — | Your text prompt. Required. |
| system_prompt | STRING | — | Optional system instructions to guide the model's behavior. |
| image | IMAGE | — | Optional image input for vision/multimodal queries. |
| max_tokens | INT | 1024 | Maximum response length. Range 1–8192. |
| temperature | FLOAT | 0.7 | Creativity control. Range 0.0–2.0. |
| gcp_project_id | STRING | (auto) | Your GCP project ID. |
| gcp_region | STRING | (auto) | GCP region. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| response | STRING | The model's text response. |

---

### DIGIT SRT Maker

Automatically generate SRT subtitle files from scripts. Paste a Google Doc URL (private or public) or raw script text, and Gemini 3.1 Pro analyzes it to extract only the spoken dialogue — stripping out stage directions, scene headings, camera instructions, and action lines — then generates a properly timed SRT file.

**How it works:**

1. Fetches the script from a Google Doc URL (using your GCP credentials for private docs) or accepts pasted text
2. Sends the full script to Gemini 3.1 Pro with instructions to identify only spoken dialogue
3. Gemini generates timed SRT subtitles based on natural speaking pace
4. Saves the `.srt` file to your project's `assets/auto_srt/` folder

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| script_url | STRING | — | Google Doc URL or any web URL containing the script. Private Google Docs are supported via your `gcloud auth login` credentials. |
| extra_instructions | STRING | (built-in) | Instructions for Gemini about what to include/exclude. Default tells it to only extract spoken dialogue. Customize to filter by character, scene, etc. |
| words_per_second | FLOAT | 2.5 | Speaking rate for subtitle timing. 2.5 wps is natural conversational pace. Lower = slower reading, longer subtitles. |
| script_text | STRING | — | Paste script text directly instead of using a URL. Overrides the URL if both are provided. |
| projekts_root | COMBO | (auto) | PROJEKTS volume root. |
| project | COMBO | (auto) | Project folder (dynamic dropdown). |
| filename | STRING | dialogue | Output filename (without .srt extension). |
| gcp_project_id | STRING | (auto) | Your GCP project ID. |
| gcp_region | STRING | global | Vertex AI region. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| srt_filepath | STRING | Full path to the saved .srt file. |
| srt_text | STRING | Raw SRT content as text. |

**Output path:** `PROJEKTS/project/assets/auto_srt/filename.srt`

**Google Docs authentication:** For private docs, the node uses `gcloud auth print-access-token` from your `gcloud auth login --enable-gdrive-access` session to authenticate with the Google Drive API. If authenticated access fails, it falls back to public export.

---

### DIGIT SRT From Video

Transcribe the audio from a video file into SRT subtitles using Gemini. Extracts audio via ffmpeg, sends it to Gemini for transcription with accurate timestamps, then runs a full post-processing pipeline before saving.

**How it works:**

1. Extracts audio from the video with ffmpeg (mono 16kHz WAV — small and fast)
2. Sends audio to Gemini for transcription with precise timestamps
3. Runs the post-processing pipeline: hallucination removal → line-length enforcement → frame padding → snap-to-frame
4. Optionally translates to another language
5. Saves in your chosen format(s) (SRT, VTT, ASS, TXT, or all)
6. Optionally burns subtitles directly into the video with full styling control

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| video_path | STRING | — | Path to the video file. Browse button in the UI. |
| model | COMBO | gemini-2.5-flash | Gemini model for transcription. |
| subtitle_output | COMBO | srt_only | `srt_only`: sidecar file(s). `burn_in_only`: hardcode subs into video. `both`: file(s) + burned-in video. |
| extra_instructions | STRING | — | Additional instructions for Gemini (e.g. "ignore background music", "this is a commercial"). |
| projekts_root | COMBO | (auto) | PROJEKTS volume root. |
| project | COMBO | (auto) | Project folder (dynamic dropdown). |
| filename | STRING | transcription | Output filename (without extension). |
| gcp_project_id | STRING | (auto) | Your GCP project ID. |
| gcp_region | STRING | global | Vertex AI region. |

**Post-processing inputs (optional):**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| identify_speakers | BOOLEAN | true | Label different speakers as SPEAKER 1, SPEAKER 2, etc. |
| pad_frames | INT | 0 | Extend each subtitle by N frames on both head and tail. Gives captions breathing room. |
| frame_rate | FLOAT | 23.976 | Video frame rate. Used for pad_frames and snap-to-frame calculations. |
| snap_to_frames | BOOLEAN | false | Round all timestamps to nearest frame boundary. Prevents subtitle flicker on frame-accurate systems like Flame. |
| max_chars_per_line | INT | 42 | Maximum characters per subtitle line. 42 = Netflix/broadcast standard. 0 = no enforcement. Lines exceeding this are word-wrapped. |
| max_lines | INT | 2 | Maximum lines per subtitle entry. Entries exceeding this are split into multiple entries with proportional timing. |
| remove_hallucinations | BOOLEAN | true | Detect and remove repeated/hallucinated entries (common LLM transcription artifact). |
| output_format | COMBO | srt | `srt`, `vtt`, `ass`, `txt`, or `all` (saves all four formats). |
| language | COMBO | auto | Audio language. 30+ languages supported. `auto` = let Gemini detect. Specifying improves accuracy. |
| translate_to | COMBO | none | Translate subtitles after transcription. Preserves all SRT timing, only translates text. |

**Burn-in styling inputs (optional):**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| font_name | STRING | Arial | Font family for burned-in subtitles. |
| font_size | INT | 24 | Font size (8–120). |
| font_color | COMBO | white | Text color. 10 presets: white, yellow, cyan, green, red, orange, magenta, blue, black, gray. |
| outline_color | COMBO | black | Outline/border color around text. |
| outline_width | INT | 2 | Outline thickness (0–8). |
| shadow_depth | INT | 1 | Drop shadow depth (0–8). |
| position | COMBO | bottom_center | Where subtitles appear: bottom_center, bottom_left, bottom_right, top_center, top_left, top_right, middle_center. |
| margin_v | INT | 30 | Vertical margin from screen edge in pixels (at 1080p). |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| srt_filepath | STRING | Path to the saved SRT file. |
| srt_text | STRING | Raw SRT content as text. |

**Output path:** `PROJEKTS/project/assets/auto_srt/filename.srt` (and `.vtt`, `.ass`, `.txt` if using `all` format)

**Burn-in output:** When using `burn_in_only` or `both`, saves as `PROJEKTS/project/assets/auto_srt/filename_subtitled.mp4` (preserves original video extension). If an ASS file exists (from `all` or `ass` format), it's used for burn-in with full styling. Otherwise, force_style is applied to the SRT.

---

### DIGIT Batch SRT From Video

Batch version of SRT From Video. Point it at a folder and it recursively finds all video files, transcribes each one, and saves the output. Designed for processing dozens of files unattended.

**How it works:**

1. Recursively scans the folder (and all subdirectories) for video files
2. Filters by file type if specified (e.g. only `.mp4`, skip `.mov`)
3. Skips files that already have output (unless overwrite is on)
4. Transcribes each file through the same pipeline as the single-file node
5. Shows progress bar in ComfyUI and per-file status in the log

**Inputs (in addition to all post-processing and styling inputs from SRT From Video):**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| video_folder | STRING | — | Top-level folder path. Browse button in UI. Scans recursively through all subdirectories. |
| file_types | COMBO | all | Filter: `all`, `mp4`, `mov`, `mxf`, `mkv`, `avi`, `m4v`, `qt`. Use this when you have MOVs and MP4s in the same folder and only want to process one type. |
| subtitle_output | COMBO | srt_only | Same as single-file node. |
| model | COMBO | gemini-2.5-flash | Gemini model. |
| output_mode | COMBO | alongside_video | `alongside_video`: saves `.srt` next to each video, wherever it lives in the tree. `projekts_auto_srt`: collects all output to one project folder. |
| overwrite | BOOLEAN | false | Skip files that already have output. Set to true to regenerate everything. |
| delay_seconds | FLOAT | 1.0 | Pause between API calls to avoid rate limiting. |
| gcp_project_id | STRING | (auto) | Your GCP project ID. |
| gcp_region | STRING | global | Vertex AI region. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| log | STRING | Per-file status log with relative paths. Shows OK/SKIPPED/ERROR for each file. |
| transcribed_count | INT | Number of files successfully transcribed. |
| output_folder | STRING | The output directory path. |

**Skip logic:** The skip check is output-mode-aware. If `subtitle_output` is `srt_only`, it checks for the `.srt` file. If `burn_in_only`, it checks for the `_subtitled` video. If `both`, it requires both to exist before skipping. This means you can re-run after errors and it picks up only the failures.

**Log format:**
```
[1/27] 30_Hero_Pre/spot_01.mp4 -> OK (24 entries, .srt)
[2/27] 30_Hero_Pre/spot_02.mp4 -> SKIPPED (exists)
[3/27] Paid_Social/social_01.mp4 -> ERROR: ffmpeg audio extraction failed
```

---

### DIGIT SRT Tools

Post-process and manipulate existing SRT files. Takes SRT text (pasted or from a file) and applies transformations. Use this to clean up, convert, or adjust subtitle files after generation or from external sources.

**Actions:**

| Action | Description |
|--------|-------------|
| `post_process` | Full pipeline: hallucination removal → line-length enforcement → frame padding → snap-to-frame. Same pipeline as the transcription nodes. |
| `convert_format` | Convert SRT to VTT, ASS/SSA, TXT, or all formats. ASS output includes full styling (font, color, outline, shadow, position). |
| `time_offset` | Shift all timestamps by N milliseconds. Positive = later, negative = earlier. Useful for syncing subtitles to re-edited video. |
| `merge` | Combine adjacent subtitle entries that have gaps smaller than a threshold (default 500ms). Reduces subtitle entry count for cleaner reading. |
| `renumber` | Re-number all entries sequentially starting from 1. Fixes gaps after manual editing or merging. |

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| srt_input | STRING | — | Paste SRT text directly. |
| action | COMBO | post_process | Which operation to perform. |
| srt_filepath | STRING | — | Path to an SRT file. Browse button in UI. Overrides srt_input if provided. |
| save_filepath | STRING | — | Path to save output. Leave empty for text-only output (no file saved). |
| time_offset_ms | INT | 0 | Milliseconds to shift (for `time_offset` action). Range -600000 to +600000. |
| merge_gap_ms | INT | 500 | Maximum gap between entries to merge (for `merge` action). |

Plus all post-processing inputs (pad_frames, frame_rate, snap_to_frames, max_chars_per_line, max_lines, remove_hallucinations) and all styling inputs (for ASS format conversion).

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| output_text | STRING | Processed SRT/VTT/ASS/TXT text. |
| output_filepath | STRING | Path to saved file (if save_filepath was set). |
| log | STRING | Summary of what was done. |

---

### DIGIT SRT Preview

Validate and QA-check SRT subtitle files. Shows a summary with entry count, duration, and character stats, plus warnings for common issues.

**What it checks:**

| Check | Description |
|-------|-------------|
| Overlapping timestamps | Entries where the start time is before the previous entry's end time. |
| Long lines | Lines exceeding max_chars_per_line (default 42, Netflix/broadcast standard). |
| Too many lines | Entries with more than 2 lines of text. |
| Reading speed (CPS) | Characters per second exceeding max_cps (default 20, Netflix adult standard). Subtitle is on screen too briefly for comfortable reading. |
| Bad timing | Entries with zero or negative duration. |

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| srt_input | STRING | — | Paste SRT text directly. |
| srt_filepath | STRING | — | Path to an SRT file. Browse button in UI. Overrides srt_input if provided. |
| max_chars_per_line | INT | 42 | Flag lines exceeding this length. 0 = no check. |
| max_cps | FLOAT | 20.0 | Flag entries with reading speed above this (characters per second). 0 = no check. Netflix standard: 20 CPS adult, 17 CPS children. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| summary | STRING | Entry count, duration, character stats, warning count. |
| entry_count | INT | Number of subtitle entries. |
| warnings | STRING | All warnings, one per line. Empty if no issues found. |

**Summary format:**
```
Entries: 47
Duration: 2m 34s
Total characters: 3842
Avg chars/entry: 81
Warnings: 3
```

---

### DIGIT Image Saver

Save images to a VFX-pipeline folder structure with auto-incrementing frame numbers. Designed for production workflows where files need to follow a strict naming and directory convention.

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| image | IMAGE | — | Image to save. Batch images save as sequential frames. |
| projekts_root | COMBO | (auto) | PROJEKTS volume root. Auto-detects available mount points. |
| project | COMBO | (auto) | Project folder (dynamic dropdown, scans for `#####_` prefix pattern). |
| shot | COMBO | (auto) | Shot folder (dynamic dropdown, scans `project/shots/`). |
| subfolder | STRING | comfy | Subfolder within the shot (e.g. "comfy", "renders", "plates"). |
| task | STRING | comp | Task name (e.g. "comp", "paint", "roto"). |
| format | COMBO | png | Output format: PNG, JPEG, or EXR. |
| tonemap | COMBO | linear | EXR tone mapping: linear, sRGB, or Reinhard. Only applies to EXR format. |
| quality | INT | 95 | JPEG quality (1–100). Only applies to JPEG format. |
| start_frame | INT | 1001 | Starting frame number if no existing frames are found. |
| frame_pad | INT | 4 | Frame number padding (e.g. 4 = `0001`, 8 = `00000001`). |
| show_preview | BOOLEAN | true | Show saved image in ComfyUI's preview panel. |
| save_workflow | COMBO | ui | Save workflow metadata as JSON sidecar: "ui", "api", "ui + api", or "none". |

**Output path:** `PROJEKTS/project/shots/shot/subfolder/task/PREFIX_SHOT_TASK.FRAME.EXT`

**Example:** `~/PROJEKTS/10001_my_project/shots/sh010/comfy/comp/10001_sh010_comp.1001.png`

**EXR support:** Full 32-bit float EXR with OpenCV. Supports RGBA with inverted alpha (VFX convention). Tone mapping options let you convert from sRGB gamma space to linear on save.

**Batch support:** If a batched IMAGE tensor is connected (e.g. from a batch generation), each image in the batch is saved as a sequential frame.

---

### DIGIT Video Saver

Save videos to the same VFX-pipeline folder structure as the Image Saver. Accepts either a single VIDEO or a batch of video file paths from the Veo node.

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| projekts_root | COMBO | (auto) | PROJEKTS volume root. |
| project | COMBO | (auto) | Project folder (dynamic dropdown). |
| shot | COMBO | (auto) | Shot folder (dynamic dropdown). |
| subfolder | STRING | comfy | Subfolder within the shot. |
| task | STRING | comp | Task name. |
| start_frame | INT | 1001 | Starting frame number. |
| frame_pad | INT | 4 | Frame number padding. |
| save_workflow | COMBO | ui | Save workflow metadata as JSON sidecar: "ui", "api", "ui + api", or "none". |
| video | VIDEO | — | Single video input (from Veo node's VIDEO output). |
| video_paths | VEO_PATHS | — | Batch video paths (from Veo node's VEO_PATHS output). Saves all videos with incrementing frame numbers. |

**Output path:** `PROJEKTS/project/shots/shot/subfolder/task/PREFIX_SHOT_TASK.FRAME.mp4`

**Batch support:** Connect the `video_paths` output from the Veo node and all generated videos (up to 4) are saved with sequential frame numbers.

---

### DIGIT Image Loader

Load the latest rendered frame from a shot/task directory. Pairs with the Image Saver — point both at the same shot and task to always have the most recent output available as an IMAGE tensor.

**Inputs:**

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| projekts_root | COMBO | (auto) | PROJEKTS volume root. |
| project | COMBO | (auto) | Project folder (dynamic dropdown). |
| shot | COMBO | (auto) | Shot folder (dynamic dropdown). |
| subfolder | STRING | comfy | Subfolder within the shot. |
| task | STRING | comp | Task name. |
| format | COMBO | png | File format to scan for: PNG, JPEG, or EXR. |
| filepath | STRING | — | Optional direct filepath input. If connected from a Saver node, loads that specific file instead of scanning. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| image | IMAGE | Loaded image as a ComfyUI tensor. |
| filepath | STRING | Full path to the loaded file. |
| frame | INT | Frame number of the loaded file. |

**Smart loading:** Automatically finds the highest-numbered frame in the target directory. If a `filepath` is connected (e.g. from the Image Saver's output), it loads that exact file instead.

**EXR support:** Full 32-bit float EXR loading with OpenCV. BGRA to RGBA conversion and alpha un-inversion handled automatically.

---

### DIGIT Drag Crop

Interactive image cropping with a drag-and-drop crop box directly on the node's image preview. No more guessing pixel coordinates — drag to select, resize with handles, and the cropped region updates in real time.

**Features:**

- **Drag to crop** — Click and drag anywhere on the preview to create a new crop region
- **Resize handles** — Corner and edge handles for precise resizing
- **Move** — Drag inside the crop box to reposition it
- **Aspect ratio locking** — Enter values like `16:9`, `2.35`, or `0.5` and toggle the lock to constrain the crop box
- **Pixel snapping** — Snap crop dimensions to grids of 2, 4, 8, 16, 32, or 64 pixels
- **Box color** — 11 preset colors (Lime, Grey, White, Black, Red, Green, Blue, Yellow, Magenta, Cyan, Hot Pink)
- **Info overlay** — Shows crop dimensions in pixels and percentage on the crop box. Toggle on/off.
- **Resolution tracking** — Automatically resets crop when the input image resolution changes
- **Mask pass-through** — Optional mask input is cropped to match the image crop region

**Inputs:**

| Input | Type | Description |
|-------|------|-------------|
| image | IMAGE | Image to crop. Run the graph once to load the preview. |
| crop_left/right/top/bottom | INT | Numeric crop offsets (also adjustable via the interactive UI). |
| mask | MASK | Optional mask that gets cropped to match. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| IMAGE | IMAGE | Cropped image. |
| MASK | MASK | Cropped mask (or zero mask if none connected). |
| CROP_JSON | STRING | JSON with all crop coordinates and dimensions. |

**Note:** You must run the graph once before the interactive preview appears. This is a technical limitation of ComfyUI's widget system — the node needs to receive image data from upstream before it can display anything.

---

### DIGIT Crop Info

Companion node for DIGIT Drag Crop. Takes the CROP_JSON string output and breaks it into individual integer values for use in other nodes.

**Inputs:**

| Input | Type | Description |
|-------|------|-------------|
| crop_json | STRING | CROP_JSON output from the Drag Crop node. |

**Outputs:**

| Output | Type | Description |
|--------|------|-------------|
| left | INT | Left crop offset in pixels. |
| top | INT | Top crop offset in pixels. |
| right | INT | Right crop offset in pixels. |
| bottom | INT | Bottom crop offset in pixels. |
| width | INT | Cropped region width in pixels. |
| height | INT | Cropped region height in pixels. |
| csv | STRING | All values as comma-separated string. |
| pretty | STRING | Human-readable formatted string. |

---

## Installation

### From ComfyUI Manager (Recommended)
1. Open ComfyUI Manager
2. Search for **`comfyui-digit`**
3. Click Install
4. Restart ComfyUI

All nodes will appear under the **DIGIT** category (and **DIGIT/ElevenLabs** for ElevenLabs nodes).

### Manual
```bash
cd ComfyUI/custom_nodes
git clone https://github.com/thedepartmentofexternalservices/comfyui-digit.git
cd comfyui-digit
pip install -r requirements.txt
```

### Deploy to GCP ComfyUI VMs
If your fleet runs ComfyUI on Compute Engine with `comfyui-digit` cloned into `custom_nodes`, update all nodes from a machine with `gcloud` access:

```bash
gcloud config set project YOUR_PROJECT_ID
./scripts/deploy-gcp-comfyui.sh
```

Common overrides:

```bash
# Match instances by label instead of name
INSTANCE_FILTER="labels.app=comfyui" ./scripts/deploy-gcp-comfyui.sh

# Private VMs without external IPs
USE_IAP=1 ./scripts/deploy-gcp-comfyui.sh

# Custom install path or service name
DIGIT_NODE_DIR="/opt/ComfyUI/custom_nodes/comfyui-digit" COMFYUI_SERVICE=comfyui ./scripts/deploy-gcp-comfyui.sh
```

The script runs `git pull` on each matching VM and restarts the `comfyui` systemd service.

---

## GCP Setup

DIGIT Nodes require a Google Cloud project with Vertex AI enabled. Setup takes about 2 minutes.

### 1. Install the Google Cloud SDK
https://cloud.google.com/sdk/docs/install

### 2. Authenticate
```bash
# Log in to your Google account
gcloud auth login --enable-gdrive-access

# Set up application default credentials (for Vertex AI)
gcloud auth application-default login

# Set your default project
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
```

### 3. Enable APIs
```bash
gcloud services enable aiplatform.googleapis.com
gcloud services enable drive.googleapis.com
```

### 4. (Optional) Create a GCS bucket for lossless Veo output
```bash
gcloud storage buckets create gs://your-bucket-name --location=us-central1
```

### 5. Use in ComfyUI
Set `gcp_project_id` on any DIGIT node, or leave it blank if running on a GCP instance (auto-detected via metadata service).

---

## Project Folder Structure

The Image Saver, Video Saver, and Image Loader nodes use a VFX-pipeline folder convention:

```
PROJEKTS_ROOT/
  PROJECT_NAME/           (e.g. 10001_my_project)
    shots/
      SHOT_NAME/          (e.g. sh010)
        SUBFOLDER/        (e.g. comfy)
          TASK/           (e.g. comp)
            PREFIX_SHOT_TASK.FRAME.EXT
```

**Example paths:**
```
~/PROJEKTS/10001_my_project/shots/sh010/comfy/comp/10001_sh010_comp.1001.png
~/PROJEKTS/10001_my_project/shots/sh010/comfy/comp/10001_sh010_comp.1001.mp4
~/PROJEKTS/10001_my_project/assets/auto_srt/dialogue.srt
```

The SRT nodes (SRT Maker, SRT From Video) save to `PROJECT/assets/auto_srt/` instead of the shots hierarchy. The Batch SRT From Video node can save either alongside each video or to the `auto_srt` folder.

**Configuring PROJEKTS roots:**

Set the `DIGIT_PROJEKTS_ROOTS` environment variable to a colon-separated list of paths:
```bash
export DIGIT_PROJEKTS_ROOTS="/mnt/storage/PROJEKTS:/Volumes/shared/PROJEKTS"
```

If not set, the node auto-detects common mount points or falls back to `~/PROJEKTS`.

Project folders must follow the `#####_name` pattern (5-digit prefix) to appear in the dropdown menus.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `google-genai` | Official Google GenAI SDK for Gemini and Veo via Vertex AI |
| `google-auth` | GCP authentication and credential management |
| `google-cloud-storage` | GCS bucket access for lossless Veo output |
| `piexif` | EXIF metadata embedding in JPEG files |
| `opencv-python` | EXR file reading and writing |
| `requests` | HTTP requests for LLM Query node |
| `ffmpeg` (system) | Audio extraction for SRT From Video nodes. Must be on PATH. |

---

## License

MIT

---

**Built by [DIGIT](https://github.com/thedepartmentofexternalservices/comfyui-digit)**
