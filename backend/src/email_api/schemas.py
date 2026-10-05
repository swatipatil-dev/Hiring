"""Request bodies for the email API.

Every model forbids unknown fields. That is a security property, not tidiness:
a ``company_id`` (or any other field the caller hopes we might honour) is a 422,
never silently accepted. The company always comes from the credential.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Recipient(_Strict):
    email: str = Field(max_length=255)
    name: Optional[str] = Field(default=None, max_length=255)


class SendMessage(_Strict):
    sender_id: Optional[str] = Field(default=None, max_length=36)
    to: List[Recipient] = Field(min_length=1)
    cc: List[Recipient] = Field(default_factory=list)
    bcc: List[Recipient] = Field(default_factory=list)
    subject: Optional[str] = None
    html: Optional[str] = None
    text: Optional[str] = None
    template_id: Optional[str] = Field(default=None, max_length=36)
    # Accepted so the error can say what is missing; templates are not versioned yet.
    template_version: Optional[int] = None
    variables: Optional[Dict[str, Any]] = None
    event_type: str = Field(default="api.message", max_length=64)
    external_reference: Optional[str] = Field(default=None, max_length=255)
    external_actor_reference: Optional[str] = Field(default=None, max_length=255)
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _content_xor_template(self):
        if self.template_id and (self.subject or self.html or self.text):
            raise ValueError("send either template_id (with variables) or subject/html/text, not both")
        if not self.template_id and not (self.html or self.text):
            raise ValueError("html or text is required when no template_id is given")
        if self.variables is not None and not self.template_id:
            raise ValueError("variables are only used with template_id")
        return self


class RetryMessage(_Strict):
    confirm_duplicate_risk: bool = False


class CreateSmtpConnection(_Strict):
    name: str = Field(min_length=1, max_length=100)
    host: str = Field(min_length=1, max_length=255)
    port: int
    security: Optional[Literal["implicit_tls", "starttls_required"]] = None
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    sender_email: str = Field(max_length=255)
    sender_name: Optional[str] = Field(default=None, max_length=255)
    sender_label: Optional[str] = Field(default=None, max_length=100)
    reply_to: Optional[str] = Field(default=None, max_length=255)
    hourly_limit: Optional[int] = Field(default=None, ge=1, le=100000)


class TestConnection(_Strict):
    to: Optional[str] = Field(default=None, max_length=255)


class CreateSender(_Strict):
    connection_id: str = Field(max_length=36)
    email: str = Field(max_length=255)
    label: str = Field(min_length=1, max_length=100)
    display_name: Optional[str] = Field(default=None, max_length=255)
    reply_to: Optional[str] = Field(default=None, max_length=255)


class UpdateSender(_Strict):
    # The address itself is immutable: queued mail holds it as its identity.
    label: Optional[str] = Field(default=None, min_length=1, max_length=100)
    display_name: Optional[str] = Field(default=None, max_length=255)
    reply_to: Optional[str] = Field(default=None, max_length=255)
    enabled: Optional[bool] = None


class CreateApiKey(_Strict):
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["live", "test"] = "live"
    scopes: List[str] = Field(min_length=1)
    expires_at: Optional[datetime] = None
    allowed_ips: Optional[List[str]] = None
    hourly_quota: Optional[int] = Field(default=None, ge=1, le=1000000)


class RotateApiKey(_Strict):
    overlap_hours: int = Field(default=24, ge=0, le=168)
