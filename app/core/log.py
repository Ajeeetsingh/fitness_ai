import logging
from logging.handlers import TimedRotatingFileHandler
import os
import shutil
from datetime import datetime

LOG_DIR = "logs"
BACKUP_DIR = os.path.join(LOG_DIR, "backup")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] - %(message)s")
    console_handler.setFormatter(console_formatter)

    # Timed rotating file handler (daily)
    log_file = os.path.join(LOG_DIR, "app.log")
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=0
    )
    file_handler.setFormatter(console_formatter)

    # Custom rotation callback to move files into YEAR/MONTH folders
    def move_to_year_month(src_path):
        # Get date from file name or use today's date
        filename = os.path.basename(src_path)
        # Try to extract date from name if exists (e.g., app.log.2025-10-08)
        parts = filename.split(".")
        date_str = None
        if len(parts) >= 3:
            date_str = parts[2]  # YYYY-MM-DD
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                dt = datetime.now()
        else:
            dt = datetime.now()

        # Year/Month folder
        year_folder = os.path.join(BACKUP_DIR, str(dt.year))
        month_folder = os.path.join(year_folder, f"{dt.month:02d}")
        os.makedirs(month_folder, exist_ok=True)

        dst_path = os.path.join(month_folder, filename)
        shutil.move(src_path, dst_path)

    # Overriding the handler’s doRollover method
    old_doRollover = file_handler.doRollover
    def custom_doRollover():
        old_doRollover()
        # Move rotated files to year/month folder
        for fname in os.listdir(LOG_DIR):
            if fname.startswith("app.log.") and not fname.endswith(".log"):
                src = os.path.join(LOG_DIR, fname)
                move_to_year_month(src)

    file_handler.doRollover = custom_doRollover

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

logger = setup_logging()

# Example logs
logger.info("Application started")
