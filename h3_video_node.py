"""DIGIT MiniMax Video node — H3 across fal, MUAPI, and Replicate.

Mode auto-detects from connected inputs. See README for wiring examples.
Cost estimates: web/digit_h3_cost.js and POST /digit/h3/estimate.
"""

import logging

try:
    from . import h3_backends, h3_models, h3_payloads, h3_pricing
except ImportError:
    import h3_backends
    import h3_models
    import h3_payloads
    import h3_pricing

logger = logging.getLogger("DigitMiniMaxVideo")


class DigitH3Video:
    CATEGORY = "DIGIT"
    RETURN_TYPES = ("VIDEO", "VIDEO_PATHS", "STRING")
    RETURN_NAMES = ("video", "video_paths", "status")
    FUNCTION = "generate"

    @classmethod
    def INPUT_TYPES(cls):
        ref_image_sockets = {
            f"reference_image{i}": ("IMAGE",)
            for i in range(1, h3_models.MAX_REFERENCE_IMAGES + 1)
        }
        ref_video_sockets = {
            f"reference_video{i}": ("VIDEO",)
            for i in range(1, h3_models.MAX_REFERENCE_VIDEOS + 1)
        }
        ref_audio_sockets = {
            f"reference_audio{i}": ("AUDIO",)
            for i in range(1, h3_models.MAX_REFERENCE_AUDIOS + 1)
        }

        provider_tooltip = "\n".join(h3_pricing.PROVIDER_BLURBS.values())
        if "replicate" not in h3_models.available_providers():
            provider_tooltip += (
                "\nreplicate is hidden until MiniMax H3 is published on Replicate."
            )

        return {
            "required": {
                "prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "placeholder": (
                        "Describe the video. In reference mode, cite Image 1, Video 1, Audio 1."
                    ),
                }),
                "provider": (h3_models.available_providers(), {
                    "default": "fal",
                    "tooltip": provider_tooltip,
                }),
                "resolution": (h3_models.RESOLUTIONS, {
                    "default": "2K",
                    "tooltip": "MUAPI supports 2K only. fal supports 768P, 2K, and 4K.",
                }),
                "aspect_ratio": (h3_models.ASPECT_RATIOS, {
                    "default": "16:9",
                    "tooltip": (
                        "T2V requires a fixed ratio. I2V/FLF follow the source image. "
                        "R2V supports adaptive."
                    ),
                }),
                "duration": (h3_models.DURATIONS, {
                    "default": "5",
                    "tooltip": f"Output length ({h3_models.MIN_DURATION}-{h3_models.MAX_DURATION}s).",
                }),
                "batch_count": ("INT", {
                    "default": 1,
                    "min": 1,
                    "max": h3_models.MAX_BATCH_COUNT,
                    "tooltip": "Submits this many generations before polling.",
                }),
                "enable_prompt_expansion": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "fal only.",
                }),
                "enable_safety_checker": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "fal only.",
                }),
            },
            "optional": {
                "first_frame": ("IMAGE", {
                    "tooltip": "Image-to-video. Mutually exclusive with reference inputs.",
                }),
                "last_frame": ("IMAGE", {
                    "tooltip": "Optional end frame for first-to-last interpolation.",
                }),
                **ref_image_sockets,
                **ref_video_sockets,
                **ref_audio_sockets,
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def generate(
        self,
        prompt,
        provider,
        resolution,
        aspect_ratio,
        duration,
        batch_count,
        enable_prompt_expansion,
        enable_safety_checker,
        first_frame=None,
        last_frame=None,
        **kwargs,
    ):
        ref_images, ref_videos, ref_audios = h3_payloads.collect_reference_inputs(
            kwargs,
            max_images=h3_models.MAX_REFERENCE_IMAGES,
            max_videos=h3_models.MAX_REFERENCE_VIDEOS,
            max_audios=h3_models.MAX_REFERENCE_AUDIOS,
        )

        mode = h3_payloads.validate_h3_request(
            prompt=prompt,
            provider=provider,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            first_frame=first_frame,
            last_frame=last_frame,
            ref_images=ref_images,
            ref_videos=ref_videos,
            ref_audios=ref_audios,
        )

        duration_seconds = int(duration)
        ctx = h3_backends.H3GenerationContext(
            prompt=prompt,
            mode=mode,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration_seconds,
            batch_count=int(batch_count),
            enable_prompt_expansion=enable_prompt_expansion if provider == "fal" else False,
            enable_safety_checker=enable_safety_checker if provider == "fal" else False,
            first_frame=first_frame,
            last_frame=last_frame,
            ref_images=ref_images,
            ref_videos=ref_videos,
            ref_audios=ref_audios,
        )

        if provider == "fal":
            result = h3_backends.generate_fal(ctx)
        elif provider == "muapi":
            result = h3_backends.generate_muapi(ctx)
        elif provider == "replicate":
            result = h3_backends.generate_replicate(ctx)
        else:
            raise ValueError(f"Unknown provider: {provider}")

        from comfy_api.latest._input_impl.video_types import VideoFromFile

        cost_summary = h3_pricing.estimate(
            provider,
            mode,
            resolution,
            duration_seconds,
            len(result.video_paths),
            has_video_refs=bool(ref_videos),
            ref_image_count=len(ref_images),
            use_live=False,
        )

        status_lines = h3_pricing.format_status_lines(cost_summary)
        status_lines += h3_backends.format_job_status_lines(result, ctx)
        if len(result.video_paths) < len(result.jobs):
            status_lines.append(
                f"Partial batch: {len(result.video_paths)}/{len(result.jobs)} clips succeeded."
            )

        return (
            VideoFromFile(result.video_paths[0]),
            result.video_paths,
            "\n".join(status_lines),
        )


try:
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.post("/digit/h3/estimate")
    async def _digit_h3_estimate(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        try:
            params = h3_payloads.parse_estimate_request(body)
        except ValueError as error:
            return web.json_response({"error": str(error)}, status=400)

        import asyncio

        loop = asyncio.get_event_loop()
        summary = await loop.run_in_executor(
            None,
            lambda: h3_pricing.estimate(
                params["provider"],
                params["mode"],
                params["resolution"],
                params["duration_seconds"],
                params["batch_count"],
                has_video_refs=params["has_video_refs"],
                ref_image_count=params["ref_image_count"],
                use_live=True,
            ),
        )
        return web.json_response({
            "range": False,
            "summary": summary,
            "available_providers": h3_models.available_providers(),
        })

except Exception:
    pass
