import time
import logging
from typing import Optional, Dict, Any, List
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CascadeLLMClient")

class CascadeLLMClient:
    """
    Центральный роутер запросов к Google Gemini API & Imagen 4 с поддержкой 
    системы автоматического горячего резервирования (Fallback & Retry) и трекингом RPD.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or "MOCK_KEY_FOR_TESTING"
        self._client: Optional[genai.Client] = None
        
        if api_key and api_key != "MOCK_KEY_FOR_TESTING":
            try:
                self._client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Не удалось инициализировать genai.Client: {e}")

        # Счетчики использования запросов в день (RPD)
        self.rpd_tracker: Dict[str, int] = {
            "gemini-3.6-flash": 0,
            "gemini-3.5-flash": 0,
            "gemini-3.5-flash-lite": 0,
            "gemini-3.1-flash-lite": 0,
            "imagen-4-ultra-generate": 0,
            "imagen-4-generate": 0,
            "imagen-4-fast-generate": 0
        }

    def send_text_request(self, prompt: str, category: str = "content", system_instruction: str = None) -> str:
        """
        Отправляет текстовый запрос по Каскаду:
        - category "architect": Gemini 3.6 Flash -> Gemini 3.5 Flash
        - category "analyst": Gemini 3.5 Flash -> Gemini 3.0 Flash / 2.5 Flash
        - category "content": Gemini 3.5 Flash Lite -> Gemini 3.1 Flash Lite
        """
        cascade_map = {
            "architect": ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"],
            "analyst": ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
            "content": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        }
        
        models_to_try = cascade_map.get(category, ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"])
        
        if not self._client or self.api_key == "MOCK_KEY_FOR_TESTING":
            logger.info(f"[MOCK LLM] Использована заглушка для категории '{category}'")
            return f"[MOCK RESPONSE for '{category}'] Ответ на запрос: {prompt[:50]}..."

        last_exception = None
        for model_name in models_to_try:
            # Ретраи для каждой модели при временных сетевых сбоях (3 попытки)
            for attempt in range(1, 4):
                try:
                    logger.info(f"Отправка запроса к модели {model_name} (попытка {attempt})...")
                    config = types.GenerateContentConfig(
                        temperature=0.7,
                        system_instruction=system_instruction
                    ) if system_instruction else types.GenerateContentConfig(temperature=0.7)

                    response = self._client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=config
                    )
                    
                    self.rpd_tracker[model_name] = self.rpd_tracker.get(model_name, 0) + 1
                    logger.info(f"Успешный ответ от {model_name}. RPD {model_name}: {self.rpd_tracker[model_name]}")
                    return response.text

                except Exception as e:
                    last_exception = e
                    logger.warning(f"Модель {model_name} вернула ошибку на попытке {attempt}: {e}")
                    time.sleep(1.5 * attempt)  # Exponential Backoff

            logger.warning(f"Переключение на следующую модель в каскаде после сбоев {model_name}...")

        raise RuntimeError(f"Все модели в каскаде '{category}' завершились ошибкой. Последняя ошибка: {last_exception}")

    def generate_image(self, prompt: str, output_path: str) -> str:
        """
        Генерирует изображение по Каскаду ИИ-Иллюстраций:
        1. Imagen 4 Ultra Generate (Качество Ultra)
        2. Imagen 4 Generate (Высокое качество)
        3. Imagen 4 Fast Generate (Аварийный резерв)
        """
        image_models = ["imagen-4-ultra-generate", "imagen-4-generate", "imagen-4-fast-generate"]

        if not self._client or self.api_key == "MOCK_KEY_FOR_TESTING":
            logger.info(f"[MOCK IMAGEN] Генерация заглушки картинки по пути {output_path}")
            # Создаем пустой PNG для тестов
            from PIL import Image
            img = Image.new('RGB', (1024, 768), color=(50, 100, 150))
            img.save(output_path)
            return output_path

        last_exception = None
        for model_name in image_models:
            try:
                logger.info(f"Запрос генерации изображения к {model_name}...")
                result = self._client.models.generate_images(
                    model=model_name,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(number_of_images=1)
                )
                
                if result and result.generated_images:
                    for generated_image in result.generated_images:
                        generated_image.image.save(output_path)
                        self.rpd_tracker[model_name] = self.rpd_tracker.get(model_name, 0) + 1
                        logger.info(f"Изображение успешно сохранено: {output_path} через {model_name}")
                        return output_path
            except Exception as e:
                last_exception = e
                logger.warning(f"Модель {model_name} вернула ошибку при генерации картинки: {e}")
                time.sleep(2)

        raise RuntimeError(f"Все модели Imagen 4 завершились ошибкой. Последняя ошибка: {last_exception}")
