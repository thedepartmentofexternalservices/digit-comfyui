from .llm_node import LLMQueryNode
from .image_saver_node import DigitImageSaver
from .image_loader_node import DigitImageLoader
from .gemini_image_node import DigitGeminiImage
from .veo_video_node import DigitVeoVideo
from .video_saver_node import DigitVideoSaver
from .drag_crop_node import DigitDragCrop, DigitCropInfo
from .srt_maker_node import DigitSRTMaker
from .srt_from_video_node import DigitSRTFromVideo, DigitBatchSRTFromVideo, DigitSRTTools, DigitSRTPreview
from .random_prompt_node import DigitRandomPrompt
from .lora_loader_node import DigitLoraLoader, DigitLoraLoaderModelOnly
from .prompt_combine_node import DigitPromptCombine
from .batch_caption_node import DigitBatchCaption
from .batch_gemini_image_node import DigitBatchGeminiImage
from .text_encode_node import DigitTextEncode
from .dataset_prep_node import DigitDatasetPrep
from .caption_viewer_node import DigitCaptionViewer
from .console_doctor_node import DigitConsoleDoctor
from .caption_find_replace_node import DigitCaptionFindReplace
from .frame_size_node import DigitFrameSize
from .dataset_node import DigitDatasetManager
from .captioner_node import DigitCaptioner, DigitCaptionPresetManager
from .trainer_node import DigitLoRATrainer, DigitLoRALoader
from .preset_node import DigitNamingPreset, DigitTriggerPreset, DigitSamplePromptPreset
from .elevenlabs_nodes import (
    DigitElevenLabsVoiceSelector,
    DigitElevenLabsTTS,
    DigitElevenLabsSTT,
    DigitElevenLabsSFX,
    DigitElevenLabsVoiceIsolation,
    DigitElevenLabsVoiceClone,
    DigitElevenLabsSTS,
    DigitElevenLabsDialogue,
)
from .seedance_video_node import DigitDanceVideo
from .replicate_seedance_node import DigitReplicateSeedance
from .shade_nodes import ShadeMount, ShadeSave

NODE_CLASS_MAPPINGS = {
    "DigitLLMQuery": LLMQueryNode,
    "DigitImageSaver": DigitImageSaver,
    "DigitImageLoader": DigitImageLoader,
    "DigitGeminiImage": DigitGeminiImage,
    "DigitVeoVideo": DigitVeoVideo,
    "DigitVideoSaver": DigitVideoSaver,
    "DigitDragCrop": DigitDragCrop,
    "DigitCropInfo": DigitCropInfo,
    "DigitSRTMaker": DigitSRTMaker,
    "DigitSRTFromVideo": DigitSRTFromVideo,
    "DigitBatchSRTFromVideo": DigitBatchSRTFromVideo,
    "DigitSRTTools": DigitSRTTools,
    "DigitSRTPreview": DigitSRTPreview,
    "DigitRandomPrompt": DigitRandomPrompt,
    "DigitLoraLoader": DigitLoraLoader,
    "DigitLoraLoaderModelOnly": DigitLoraLoaderModelOnly,
    "DigitPromptCombine": DigitPromptCombine,
    "DigitBatchCaption": DigitBatchCaption,
    "DigitBatchGeminiImage": DigitBatchGeminiImage,
    "DigitTextEncode": DigitTextEncode,
    "DigitDatasetPrep": DigitDatasetPrep,
    "DigitCaptionViewer": DigitCaptionViewer,
    "DigitConsoleDoctor": DigitConsoleDoctor,
    "DigitCaptionFindReplace": DigitCaptionFindReplace,
    "DigitFrameSize": DigitFrameSize,
    # Training nodes
    "DigitDatasetManager": DigitDatasetManager,
    "DigitCaptioner": DigitCaptioner,
    "DigitCaptionPresetManager": DigitCaptionPresetManager,
    "DigitLoRATrainer": DigitLoRATrainer,
    "DigitLoRALoader": DigitLoRALoader,
    "DigitNamingPreset": DigitNamingPreset,
    "DigitTriggerPreset": DigitTriggerPreset,
    "DigitSamplePromptPreset": DigitSamplePromptPreset,
    # ElevenLabs nodes
    "DigitElevenLabsVoiceSelector": DigitElevenLabsVoiceSelector,
    "DigitElevenLabsTTS": DigitElevenLabsTTS,
    "DigitElevenLabsSTT": DigitElevenLabsSTT,
    "DigitElevenLabsSFX": DigitElevenLabsSFX,
    "DigitElevenLabsVoiceIsolation": DigitElevenLabsVoiceIsolation,
    "DigitElevenLabsVoiceClone": DigitElevenLabsVoiceClone,
    "DigitElevenLabsSTS": DigitElevenLabsSTS,
    "DigitElevenLabsDialogue": DigitElevenLabsDialogue,
    "DigitDanceVideo": DigitDanceVideo,
    "DigitReplicateSeedance": DigitReplicateSeedance,
    # Shade.inc nodes
    "ShadeMount": ShadeMount,
    "ShadeSave": ShadeSave,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DigitLLMQuery": "DIGIT LLM Query",
    "DigitImageSaver": "DIGIT Image Saver",
    "DigitImageLoader": "DIGIT Image Loader",
    "DigitGeminiImage": "DIGIT Gemini Image",
    "DigitVeoVideo": "DIGIT Veo Video",
    "DigitVideoSaver": "DIGIT Video Saver",
    "DigitDragCrop": "DIGIT Drag Crop",
    "DigitCropInfo": "DIGIT Crop Info",
    "DigitSRTMaker": "DIGIT SRT Maker",
    "DigitSRTFromVideo": "DIGIT SRT From Video",
    "DigitBatchSRTFromVideo": "DIGIT Batch SRT From Video",
    "DigitSRTTools": "DIGIT SRT Tools",
    "DigitSRTPreview": "DIGIT SRT Preview",
    "DigitRandomPrompt": "DIGIT Random Prompt",
    "DigitLoraLoader": "DIGIT LoRA Loader",
    "DigitLoraLoaderModelOnly": "DIGIT LoRA Loader (Model Only)",
    "DigitPromptCombine": "DIGIT Prompt Combine",
    "DigitBatchCaption": "DIGIT Batch Caption",
    "DigitBatchGeminiImage": "DIGIT Batch Gemini Image",
    "DigitTextEncode": "DIGIT Text Encode",
    "DigitDatasetPrep": "DIGIT Dataset Prep",
    "DigitCaptionViewer": "DIGIT Caption Viewer",
    "DigitConsoleDoctor": "DIGIT Console Doctor",
    "DigitCaptionFindReplace": "DIGIT Caption Find & Replace",
    "DigitFrameSize": "DIGIT Frame Size",
    # Training nodes
    "DigitDatasetManager": "DIGIT Dataset Manager",
    "DigitCaptioner": "DIGIT Captioner",
    "DigitCaptionPresetManager": "DIGIT Caption Preset Manager",
    "DigitLoRATrainer": "DIGIT LoRA Trainer",
    "DigitLoRALoader": "DIGIT LoRA Loader",
    "DigitNamingPreset": "DIGIT Naming Preset",
    "DigitTriggerPreset": "DIGIT Trigger Preset",
    "DigitSamplePromptPreset": "DIGIT Sample Prompt Preset",
    # ElevenLabs nodes
    "DigitElevenLabsVoiceSelector": "DIGIT ElevenLabs Voice Selector",
    "DigitElevenLabsTTS": "DIGIT ElevenLabs Text to Speech",
    "DigitElevenLabsSTT": "DIGIT ElevenLabs Speech to Text",
    "DigitElevenLabsSFX": "DIGIT ElevenLabs Sound Effects",
    "DigitElevenLabsVoiceIsolation": "DIGIT ElevenLabs Voice Isolation",
    "DigitElevenLabsVoiceClone": "DIGIT ElevenLabs Voice Clone",
    "DigitElevenLabsSTS": "DIGIT ElevenLabs Speech to Speech",
    "DigitElevenLabsDialogue": "DIGIT ElevenLabs Dialogue",
    "DigitDanceVideo": "DIGIT Seedance Video",
    "DigitReplicateSeedance": "DIGIT Seedance Video (Replicate)",
    # Shade.inc nodes
    "ShadeMount": "Shade Mount",
    "ShadeSave": "Save to Shade",
}

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# Initialize GCP Compute Broker execution hook
try:
    from .broker_hook import init_broker_hook
    init_broker_hook()
except Exception as _e:
    import logging
    logging.getLogger("DigitBrokerHook").error(f"Failed to load broker execution hook: {_e}")

