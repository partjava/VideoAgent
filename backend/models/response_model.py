from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    status: str
    data: Any


class ApiErrorResponse(BaseModel):
    status: str
    message: str
