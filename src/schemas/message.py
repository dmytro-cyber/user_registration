from pydantic import BaseModel

import datetime

class MessageResponseSchema(BaseModel):
    message: str