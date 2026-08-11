import os
import sys
import traceback
import logging

logger = logging.getLogger("ScriptExecutor")

class ScriptExecutor:
    """
    Исполнитель сгенерированных Python-скриптов (matplotlib / pandas / seaborn)
    в изолированном контексте для безопасной генерации файлов диаграмм и графиков (.png).
    """
    def __init__(self, output_dir: str = None):
        self.output_dir = output_dir or os.getcwd()

    def execute_chart_script(self, script_code: str, output_png_path: str) -> bool:
        """
        Исполняет Python-код, генерирующий диаграмму, и проверяет появление итогового файла output_png_path.
        """
        try:
            # Подготовка безопасного глобального контекста исполнения
            globals_dict = {
                "__builtins__": __builtins__,
                "os": os,
                "sys": sys,
                "output_png_path": output_png_path
            }
            locals_dict = {}

            # Добавляем принудительное сохранение, если в коде заменена переменная output_png_path
            augmented_code = script_code + f"\n\nimport matplotlib.pyplot as plt\nif plt.get_fignums():\n    plt.savefig(r'{output_png_path}', bbox_inches='tight', dpi=300)\n    plt.close('all')\n"

            logger.info(f"Запуск генерации графика по пути: {output_png_path}")
            exec(augmented_code, globals_dict, locals_dict)

            if os.path.exists(output_png_path) and os.path.getsize(output_png_path) > 0:
                logger.info(f"График успешно сформирован: {output_png_path}")
                return True
            else:
                logger.error(f"Файл графика не был создан или имеет размер 0 байт: {output_png_path}")
                return False

        except Exception as e:
            logger.error(f"Ошибка при исполнении скрипта графика: {e}\n{traceback.format_exc()}")
            return False
