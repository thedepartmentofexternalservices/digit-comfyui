"""Standalone test for DigitGeminiImage batch output.

Runs the node outside ComfyUI: 16 parallel generations, random seeds,
saves each frame + a 4x4 contact sheet to test_outputs/.

Usage: <comfyui venv python> scripts/test_gemini_batch.py
"""

import importlib.util
import logging
import os
import sys
import time
import types

import numpy as np
from PIL import Image

logging.basicConfig(level=logging.WARNING, format="%(message)s")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "test_outputs")
os.makedirs(OUT_DIR, exist_ok=True)

# Fake package so the node's relative imports (.gcp_config etc.) resolve
pkg = types.ModuleType("digitpkg")
pkg.__path__ = [REPO]
sys.modules["digitpkg"] = pkg
spec = importlib.util.spec_from_file_location(
    "digitpkg.gemini_image_node", os.path.join(REPO, "gemini_image_node.py")
)
mod = importlib.util.module_from_spec(spec)
sys.modules["digitpkg.gemini_image_node"] = mod
spec.loader.exec_module(mod)

node = mod.DigitGeminiImage()

BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 16
PROMPT = (
    "Anthropomorphic hotdogs with cartoon arms, legs, and cheerful faces sitting at a "
    "diner lunch table, happily eating regular hotdogs with mustard and ketchup. "
    "Bright playful 3D render, warm lunchtime lighting."
)

start = time.time()
image_batch, text = node.generate(
    prompt=PROMPT,
    model="gemini-3.1-flash-image",
    aspect_ratio="1:1",
    resolution="1K",
    thinking_level="MINIMAL",
    seed=0,
    temperature=1.0,
    batch_count=BATCH,
)
elapsed = time.time() - start

print(f"\nBatch tensor shape: {tuple(image_batch.shape)}")
print(f"Wall clock for {BATCH} images: {elapsed:.1f}s")

# Count blank fallback frames (failed items)
blanks = sum(1 for i in range(image_batch.shape[0]) if image_batch[i].max().item() == 0)
print(f"Blank (failed) frames: {blanks}/{image_batch.shape[0]}")

# Save individual frames + contact sheet
frames = []
for i in range(image_batch.shape[0]):
    arr = (image_batch[i].cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    img.save(os.path.join(OUT_DIR, f"hotdog_b{BATCH}_{i:02d}.png"))
    frames.append(img)

import math
cols = math.ceil(math.sqrt(BATCH))
rows = math.ceil(BATCH / cols)
thumb = 320
sheet = Image.new("RGB", (cols * thumb, rows * thumb), (20, 20, 20))
for i, img in enumerate(frames):
    t = img.resize((thumb, thumb), Image.LANCZOS)
    sheet.paste(t, ((i % cols) * thumb, (i // cols) * thumb))
sheet_path = os.path.join(OUT_DIR, f"contact_sheet_b{BATCH}.png")
sheet.save(sheet_path)
print(f"Contact sheet: {sheet_path}")
