import logging
import os

def setup(path="logs/run.log"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    logger = logging.getLogger("indirim")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    # Dosya çıktısı
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_format = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler.setFormatter(file_format)
    logger.addHandler(file_handler)

    # Terminal çıktısı
    console_handler = logging.StreamHandler()
    console_format = logging.Formatter("[%(levelname)s] %(message)s")
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    return logger