import os
import logging
from core.cascade_llm import CascadeLLMClient

logger = logging.getLogger("ImageGenEngine")

def generate_ai_illustration(prompt: str, output_png_path: str, cascade_client: CascadeLLMClient = None) -> str:
    """
    Генерирует ИИ-иллюстрацию (макет ПО, схема концепта, обложка) с помощью Imagen 4 Ultra / Generate.
    """
    client = cascade_client or CascadeLLMClient()
    logger.info(f"Запуск ИИ-генератора изображений для промпта: '{prompt[:40]}...'")
    
    formatted_prompt = f"Professional technical diagram, clean vector layout, high resolution, academic style: {prompt}"
    return client.generate_image(prompt=formatted_prompt, output_path=output_png_path)
