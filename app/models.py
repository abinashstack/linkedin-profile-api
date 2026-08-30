"""Response schema returned by the API. Field names are ours, not LinkedIn's.

Experience/Education dates changed shape in this version: LinkedIn's current
profile rendering exposes a single free-text date range (e.g. "Jan 2024 -
Present · 2 yrs 8 mos", "2007 – 2017") rather than separate structured
start/end fields -- see the README's "Known limitations" for why splitting
that reliably into starts_at/ends_at isn't done here."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class Experience(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    employment_type: Optional[str] = None
    location: Optional[str] = None
    date_range: Optional[str] = None
    description: Optional[str] = None


class Education(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None
    date_range: Optional[str] = None
    description: Optional[str] = None


class Certification(BaseModel):
    name: Optional[str] = None
    authority: Optional[str] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    license_number: Optional[str] = None
    url: Optional[str] = None


class Language(BaseModel):
    name: Optional[str] = None
    proficiency: Optional[str] = None


class ProfileImage(BaseModel):
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


class ProfileRequest(BaseModel):
    url: str  # a full profile URL or a bare handle, e.g. "satyanadella"
    li_at: Optional[str] = None  # per-request session cookie override; used only in-memory


class ProfileResponse(BaseModel):
    public_id: str
    profile_url: str
    name: Optional[str] = None
    headline: Optional[str] = None
    location: Optional[str] = None
    about: Optional[str] = None
    profile_picture: Optional[ProfileImage] = None
    background_image: Optional[ProfileImage] = None
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)


class BatchProfileRequest(BaseModel):
    urls: list[str]  # each a full profile URL or a bare handle
    li_at: Optional[str] = None  # per-request session cookie override; used only in-memory


class BatchProfileResult(BaseModel):
    url: str
    ok: bool
    profile: Optional[ProfileResponse] = None
    error: Optional[str] = None


class BatchProfileResponse(BaseModel):
    results: list[BatchProfileResult]
