from .llm_node import LLMQueryNode
from .image_saver_node import DigitImageSaver
from .image_loader_node import DigitImageLoader
from .gemini_image_node import DigitGeminiImage
from .veo_video_node import DigitVeoVideo
from .video_saver_node import DigitVideoSaver
from .drag_crop_node import DigitDragCrop, DigitCropInfo
from .srt_maker_node import DigitSRTMaker
from .seedance_video_node import DigitDanceVideo

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
    "DigitDanceVideo": DigitDanceVideo,
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
    "DigitDanceVideo": "DIGIT Seedance Video",
}

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
