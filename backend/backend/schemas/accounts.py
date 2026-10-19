import re
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class AccountCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    phone: str = Field(min_length=1, max_length=32)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("phone")
    @classmethod
    def mainland_phone(cls, value):
        value = re.sub(r"[ -]", "", value.strip())
        if not re.fullmatch(r"(?:\+86)?1[3-9][0-9]{9}", value):
            raise ValueError("invalid_phone")
        return value if value.startswith("+86") else "+86" + value


class AccountSession(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: datetime
    owner_id: str
    account_id: UUID
    phone_masked: str
    phone_verified: bool = False
