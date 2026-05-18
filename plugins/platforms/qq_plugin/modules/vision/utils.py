import logging

def get_logger(name):
    return logging.getLogger(name)

logger = get_logger("vision_utils")

def log(msg):
    logger.debug(msg)
