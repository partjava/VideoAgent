import asyncio
from typing import Any

from config import settings


class MongoDatabase:
    def __init__(self) -> None:
        self.client: Any | None = None
        self.database: Any | None = None

    async def connect(self) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        self.client = AsyncIOMotorClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        )
        self.database = self.client[settings.mongodb_db_name]

        try:
            await asyncio.wait_for(self.client.list_database_names(), timeout=5.0)
            print("[Database] Successfully connected to MongoDB.")
        except Exception as exc:
            self.client.close()
            self.client = None
            self.database = None
            raise RuntimeError(f"MongoDB unavailable: {exc}") from exc

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
        self.client = None
        self.database = None

    def is_configured(self) -> bool:
        return bool(settings.mongodb_uri and settings.mongodb_db_name)

    def get_collection(self, collection_name: str) -> Any:
        if self.database is None:
            raise RuntimeError("MongoDB is not connected")
        return self.database[collection_name]

    async def insert_one(self, collection_name: str, document: dict[str, Any]) -> str:
        collection = self.get_collection(collection_name)
        result = await collection.insert_one(document)
        return str(result.inserted_id)

    async def find_one(self, collection_name: str, query: dict[str, Any]) -> dict[str, Any] | None:
        collection = self.get_collection(collection_name)
        return await collection.find_one(query)

    async def find_many(
        self,
        collection_name: str,
        limit: int = 50,
        sort_field: str = "created_at",
        sort_direction: int = -1,
    ) -> list[dict[str, Any]]:
        collection = self.get_collection(collection_name)
        cursor = collection.find().sort(sort_field, sort_direction).limit(limit)
        return await cursor.to_list(length=limit)

    async def update_one(
        self,
        collection_name: str,
        query: dict[str, Any],
        values: dict[str, Any],
    ) -> int:
        collection = self.get_collection(collection_name)
        result = await collection.update_one(query, {"$set": values})
        return int(result.modified_count)

    async def replace_one(
        self,
        collection_name: str,
        query: dict[str, Any],
        document: dict[str, Any],
        upsert: bool = False,
    ) -> int:
        collection = self.get_collection(collection_name)
        result = await collection.replace_one(query, document, upsert=upsert)
        return int(result.modified_count or bool(result.upserted_id))

    async def delete_one(self, collection_name: str, query: dict[str, Any]) -> int:
        collection = self.get_collection(collection_name)
        result = await collection.delete_one(query)
        return int(result.deleted_count)


mongodb = MongoDatabase()
