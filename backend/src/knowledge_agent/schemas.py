from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class Register(Strict):
    username: str = Field(min_length=3, max_length=40, pattern=r'^[a-z0-9_]+$')
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6, max_length=200)


class ChangePassword(Strict):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=6, max_length=200)


class RenameConversation(Strict):
    title: str = Field(min_length=1, max_length=100)


class Query(Strict):
    message: str = Field(min_length=1, max_length=4000)
    client_request_id: str = Field(min_length=1, max_length=100)


class Person(Strict):
    display_name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=50)
    employee_id: str | None = None


class ProjectWrite(Strict):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    summary: str = Field(default='',max_length=4000)
    phase: Literal['planning', 'development', 'testing', 'acceptance', 'delivered', 'paused'] = 'planning'
    health: Literal['on_track', 'at_risk', 'delayed', 'unknown'] = 'unknown'
    baseline_due_date: date | None = None
    planned_due_date: date | None = None
    actual_delivery_date: date | None = None
    blockers: list[str] = Field(default_factory=list, max_length=20)
    people: list[Person] = Field(default_factory=list, max_length=30)
    org_visible: bool = False
    revision: int | None = None


class Grant(Strict):
    subject_type: Literal['user', 'group']
    subject_id: str = Field(max_length=100)


class ACLWrite(Strict):
    org_visible: bool
    grants: list[Grant] = Field(max_length=100)


class Members(Strict):
    user_ids: list[str] = Field(max_length=100)


class DocumentSelection(Strict):
    document_ids: list[str] = Field(min_length=1, max_length=1000)


class GroupCreate(Strict):
    name: str = Field(min_length=1,max_length=100)

    @model_validator(mode='after')
    def nonblank(self):
        self.name=self.name.strip()
        if not self.name:
            raise ValueError('Group name cannot be blank')
        return self


class Publish(Strict):
    revision: int = Field(ge=1)
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode='after')
    def dates(self):
        if self.effective_from.tzinfo is None or (self.effective_to and self.effective_to.tzinfo is None):
            raise ValueError('Use timezone-aware timestamps')
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError('Invalid effective interval')
        return self


class FeedbackWrite(Strict):
    type: Literal['incorrect', 'citation', 'missing', 'permission', 'other']
    note: str = Field(default='', max_length=1000)


class FeedbackState(Strict):
    state: Literal['open', 'reviewed', 'resolved']


class Search(Strict):
    query: str = Field(min_length=1, max_length=2000)
    project_id: str | None = None
    mode: Literal['current', 'history'] = 'current'
    as_of: date | None = None
    top_k: int = Field(default=6, ge=1, le=10)

    @model_validator(mode='after')
    def history_date(self):
        if self.mode == 'current' and self.as_of:
            raise ValueError('as_of is only valid in history mode')
        return self


class ProjectRef(Strict):
    project_ref: str = Field(min_length=1, max_length=100)


class OwnerRef(ProjectRef):
    role: str | None = Field(default=None, max_length=50)


class Excerpt(Strict):
    document_id: str
    version_id: str
    chunk_id: str


class Fact(Strict):
    text: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class GeneratedAnswer(Strict):
    status: Literal['answered', 'partial', 'no_answer']
    facts: list[Fact] = Field(max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=20)
