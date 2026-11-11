import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserRole


# Token Schemas
class Token(BaseModel):
    session_token: str
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class SessionTokenRequest(BaseModel):
    session_token: Optional[str] = None
    refresh_token: Optional[str] = None

    def get_token(self) -> str:
        token = self.session_token or self.refresh_token
        if not token:
            raise ValueError("session_token or refresh_token is required")
        return token


RefreshTokenRequest = SessionTokenRequest


# Authentication Schemas
class StudentRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    username: Optional[str] = Field(default=None, min_length=3, max_length=50)
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# Profile Schemas
class StudentProfileBase(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=100)


class StudentProfileUpdate(StudentProfileBase):
    pass


class StudentProfileResponse(StudentProfileBase):
    id: uuid.UUID
    user_id: uuid.UUID
    email: Optional[EmailStr] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


# User Response Schemas
class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    username: Optional[str]
    clerk_user_id: Optional[str] = None
    role: UserRole
    is_active: bool
    is_verified: bool
    created_at: datetime
    profile: Optional[StudentProfileResponse] = None
    model_config = ConfigDict(from_attributes=True)


# Clerk Integration Schemas
class ClerkExchangeRequest(BaseModel):
    token: Optional[str] = None


class ClerkOutboxItemResponse(BaseModel):
    id: uuid.UUID
    clerk_user_id: str
    audit_target_id: str
    operation: str
    status: str
    attempts: int
    max_attempts: int
    next_retry_at: datetime
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ClerkOutboxListResponse(BaseModel):
    pending_count: int
    dead_letter_count: int
    completed_count: int
    items: List[ClerkOutboxItemResponse]


# Admin Account Management Schemas
class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: Optional[str] = Field(default=None, min_length=8)
    username: Optional[str] = None
    role: UserRole = UserRole.VIEWER


class AdminUpdateRoleRequest(BaseModel):
    role: UserRole


class AdminUpdateStatusRequest(BaseModel):
    is_active: bool


class UserSessionResponse(BaseModel):
    id: uuid.UUID
    expires_at: datetime
    is_revoked: bool
    user_agent: Optional[str]
    ip_address: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
