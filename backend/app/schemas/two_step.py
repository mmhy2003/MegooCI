import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

_TICKET = Field(max_length=200)
_CODE = Field(default=None, max_length=64)


class SignInResponse(BaseModel):
    """The answer to a correct password. ``signed_in`` carries the session;
    the other two carry a ticket and never a token."""

    status: Literal["signed_in", "two_step_required", "two_step_setup_required"]
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    ticket: str | None = None
    methods: list[str] | None = None
    passkey_origin: str | None = None
    # Set when a recovery code was used to sign in.
    recovery_codes_left: int | None = None


class TicketRequest(BaseModel):
    ticket: str = _TICKET


class VerifyRequest(BaseModel):
    ticket: str = _TICKET
    code: str | None = _CODE
    recovery_code: str | None = _CODE


class TicketCodeRequest(BaseModel):
    ticket: str = _TICKET
    code: str = Field(max_length=64)


class PasskeyAnswer(BaseModel):
    ticket: str = _TICKET
    credential: dict[str, Any]


class NamedPasskeyAnswer(PasskeyAnswer):
    name: str | None = Field(default=None, max_length=300)


class PasskeyOptions(BaseModel):
    options: dict[str, Any]
    # Only when adding a passkey from Settings: hand it back with the answer.
    ticket: str | None = None


class TotpSetup(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodes(BaseModel):
    """Ten new codes, or None when the account already had its codes."""

    recovery_codes: list[str] | None = None


class PasswordRequest(BaseModel):
    password: str = Field(max_length=1024)


class ConfirmTotpRequest(PasswordRequest):
    """Turning an authenticator on from Settings: its code, and the password."""

    code: str = Field(max_length=64)


class AddPasskeyRequest(NamedPasskeyAnswer):
    """Adding a passkey from Settings: the browser's answer, and the password."""

    password: str = Field(max_length=1024)


class RenameRequest(BaseModel):
    name: str = Field(max_length=300)


class PasskeyInfo(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class PasskeyAdded(BaseModel):
    passkey: PasskeyInfo
    recovery_codes: list[str] | None = None


class TotpInfo(BaseModel):
    confirmed_at: datetime | None = None
    last_used_at: datetime | None = None


class RecoveryCodesInfo(BaseModel):
    left: int
    generated_at: datetime | None = None


class TwoStepSummary(BaseModel):
    enabled: bool
    required: bool
    totp: TotpInfo | None = None
    passkeys: list[PasskeyInfo] = []
    recovery_codes: RecoveryCodesInfo | None = None
    passkey_origin: str


class SecuritySettings(BaseModel):
    require_two_step: bool
    users_total: int
    users_with_two_step: int


class SecuritySettingsUpdate(BaseModel):
    require_two_step: bool
