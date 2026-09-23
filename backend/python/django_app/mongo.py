"""MongoDB client shared by the Django app."""

import os
from functools import lru_cache

from pymongo import MongoClient
from pymongo.database import Database

MONGO_USER = os.getenv("MONGO_USER", "root")
MONGO_PASS = os.getenv("MONGO_PASS", "example")
MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = os.getenv("MONGO_PORT", "27019")
MONGO_DB = os.getenv("MONGO_DB", "interneers_lab")


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    uri = os.getenv(
        "MONGO_URI",
        f"mongodb://{MONGO_USER}:{MONGO_PASS}@{MONGO_HOST}:{MONGO_PORT}/?authSource=admin",
    )
    return MongoClient(uri)


def get_database() -> Database:
    return get_client()[MONGO_DB]
