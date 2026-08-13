import json
import logging
import os
import re
import urllib.request

from html.parser import HTMLParser
from server import PromptServer
from aiohttp import web

from .gcp_config import resolve_gcp_config, default_project, default_region
from .projekts_utils import combo_choices, get_available_projekts_roots, scan_projects

logger = logging.getLogger(__name__)

# Gap between subtitles
GAP_SECONDS = 0.15


class _HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self):
        return "".join(self._parts)


def _fetch_google_doc_authenticated(doc_id):
    """Fetch a Google Doc using gcloud user credentials."""
    import subprocess
    import json

    # Get access token directly from gcloud CLI — works with user login
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcloud auth failed: {result.stderr.strip()}")

    token = result.stdout.strip()

    export_url = f"https://www.googleapis.com/drive/v3/files/{doc_id}/export?mimeType=text/plain"
    req = urllib.request.Request(
        export_url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "ComfyUI-DIGIT/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_url_text(url):
    """Fetch text content from a URL. Handles Google Docs with GCP auth."""
    # Google Docs: use Drive API with application default credentials
    gdoc_match = re.match(
        r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)", url
    )
    if gdoc_match:
        doc_id = gdoc_match.group(1)
        print(f"[DigitSRTMaker] Detected Google Doc ID: {doc_id}")
        try:
            return _fetch_google_doc_authenticated(doc_id)
        except Exception as e:
            print(f"[DigitSRTMaker] Authenticated fetch failed ({e}), trying public export...")
            # Fall through to public fetch
            url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    req = urllib.request.Request(url, headers={"User-Agent": "ComfyUI-DIGIT/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()

        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip()
        else:
            charset = "utf-8"

        text = raw.decode(charset, errors="replace")

    if "<html" in text[:500].lower() or "<body" in text[:500].lower():
        extractor = _HTMLTextExtractor()
        extractor.feed(text)
        text = extractor.get_text()

    return text


def _seconds_to_srt_time(seconds):
    """Convert seconds float to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


SRT_SYSTEM_PROMPT = """You are an expert script analyst and subtitle creator.

Given a script or screenplay, you must:
1. Identify ONLY the spoken dialogue lines — ignore all stage directions, scene headings (INT./EXT.), parenthetical acting instructions, transitions (CUT TO, FADE IN), camera directions, and action/description lines.
2. For each spoken line, estimate how long it would take to speak naturally at a conversational pace.
3. Output valid SRT subtitle format.

Rules for timing:
- Average speaking rate is about 2.5 words per second for natural speech.
- Minimum subtitle duration is 1.0 second.
- Leave a 0.15 second gap between subtitles.
- Start from 00:00:00,000.

Rules for text:
- Include the character name before their line, formatted as "CHARACTER: dialogue text"
- If no character names are present, just output the spoken text.
- Keep each subtitle entry to a maximum of 2 lines and ~42 characters per line for readability.
- Split long dialogue into multiple subtitle entries.

Output ONLY the raw SRT content. No markdown code fences, no explanations, no preamble.
Start with subtitle number 1.

Example output format:
1
00:00:00,000 --> 00:00:02,400
JOHN: Hey, how are you doing today?

2
00:00:02,550 --> 00:00:04,150
SARAH: I'm great, thanks for asking.

"""


class DigitSRTMaker:
    MODELS = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite-preview",
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
    ]

    CATEGORY = "DIGIT"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("srt_filepath", "srt_text")
    FUNCTION = "make_srt"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        available_roots = get_available_projekts_roots() or [""]
        first_root = available_roots[0]
        projects = combo_choices(scan_projects(first_root)) if first_root else [""]

        return {
            "required": {
                "model": (cls.MODELS, {"default": "gemini-2.5-flash"}),
                "script_url": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "placeholder": "Paste Google Doc or web URL here",
                }),
                "extra_instructions": ("STRING", {
                    "default": "only include actually spoken word.  look at the script and do not include any words describing people, actors, or stage direction.  figure out only what is actually going to be spoken, and only use that in the SRT file outputs.  The SRT outputs are only for on screen dialogue.",
                    "multiline": True,
                }),
                "words_per_second": ("FLOAT", {
                    "default": 2.5,
                    "min": 0.5,
                    "max": 10.0,
                    "step": 0.1,
                }),
                "projekts_root": (available_roots,),
                "project": (projects,),
                "filename": ("STRING", {"default": "dialogue"}),
                "gcp_project_id": ("STRING", {
                    "default": default_project(),
                    "tooltip": "GCP project ID. Auto-detected from DIGIT_GCP_PROJECT env var or GCP metadata.",
                }),
                "gcp_region": ("STRING", {
                    "default": default_region(),
                    "tooltip": "GCP region. Auto-detected from DIGIT_GCP_REGION env var or GCP metadata. Defaults to 'global'.",
                }),
            },
            "optional": {
                "script_text": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": "Or paste script text directly here (overrides URL)",
                }),
            },
        }

    @classmethod
    def VALIDATE_INPUTS(cls, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def make_srt(self, model, script_url, extra_instructions, words_per_second,
                 projekts_root, project, filename,
                 script_text="", gcp_project_id="", gcp_region="global"):

        from google import genai
        from google.genai import types

        # Get script content
        raw_text = ""
        if script_text and script_text.strip():
            raw_text = script_text.strip()
            print("[DigitSRTMaker] Using pasted script text.")
        elif script_url and script_url.strip():
            print(f"[DigitSRTMaker] Fetching script from: {script_url}")
            raw_text = _fetch_url_text(script_url.strip())
        else:
            raise ValueError("[DigitSRTMaker] Provide either a script URL or paste script text.")

        if not raw_text.strip():
            raise ValueError("[DigitSRTMaker] No text content found.")

        # Setup Gemini client
        gcp_project, gcp_reg = resolve_gcp_config(gcp_project_id, gcp_region)

        client = genai.Client(
            vertexai=True,
            project=gcp_project,
            location=gcp_reg,
        )

        # Build the prompt
        system_prompt = SRT_SYSTEM_PROMPT
        if words_per_second != 2.5:
            system_prompt += f"\nIMPORTANT: Use a speaking rate of {words_per_second} words per second instead of the default 2.5.\n"

        user_prompt = f"Here is the script. Extract ONLY the spoken dialogue and create an SRT subtitle file:\n\n---\n{raw_text}\n---"

        if extra_instructions and extra_instructions.strip():
            user_prompt += f"\n\nAdditional instructions:\n{extra_instructions.strip()}"

        print(f"[DigitSRTMaker] Sending script ({len(raw_text)} chars) to {model}...")

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
        )

        response = client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=config,
        )

        srt_text = response.text.strip()

        # Clean up any markdown code fences Gemini might add
        if srt_text.startswith("```"):
            lines = srt_text.split("\n")
            # Remove first line (```srt or ```) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            srt_text = "\n".join(lines).strip()

        if not srt_text:
            raise ValueError("[DigitSRTMaker] Gemini returned empty SRT content.")

        # Save
        target_dir = os.path.join(projekts_root, project, "assets", "auto_srt")
        os.makedirs(target_dir, exist_ok=True)

        srt_filename = f"{filename}.srt"
        filepath = os.path.join(target_dir, srt_filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(srt_text)

        print(f"[DigitSRTMaker] Saved SRT to: {filepath}")

        # Count entries
        entry_count = len(re.findall(r"^\d+$", srt_text, re.MULTILINE))
        print(f"[DigitSRTMaker] Generated {entry_count} subtitle entries.")

        return {
            "ui": {"filepath_text": [filepath]},
            "result": (filepath, srt_text),
        }
