"""A finite, internal official-source lookup API; no arbitrary URL or tool execution."""
from typing import Literal
import re
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.api.dependencies import Database
from backend.core.security import require_internal_write
from backend.services.dynamic_intelligence import lookup, IntelligenceError

router = APIRouter(prefix='/internal/intelligence', dependencies=[Depends(require_internal_write)])


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tool: Literal['nvd_lookup','kev_lookup','osv_lookup','knowledge_lookup','web_search']
    identifier: str | None = Field(default=None, max_length=50)
    ecosystem: Literal['PyPI','npm','Go','Maven','crates.io'] | None = None
    package: str | None = Field(default=None, max_length=100)
    version: str | None = Field(default=None, max_length=100)
    topic: Literal['agency','injection','poisoning'] | None = None
    query: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode='after')
    def arguments(self):
        if self.tool in {'nvd_lookup','kev_lookup'}:
            if not self.identifier or not re.fullmatch(r'CVE-\d{4}-\d{4,}',self.identifier):
                raise ValueError('standard CVE required')
        elif self.tool=='osv_lookup':
            if not (self.ecosystem and self.package and self.version):
                raise ValueError('ecosystem, package and version required')
            if not re.fullmatch(r'[A-Za-z0-9@/_.-]{1,100}',self.package) or not re.fullmatch(r'[A-Za-z0-9_.+~-]{1,100}',self.version):
                raise ValueError('invalid package or version')
        elif self.tool=='web_search':
            if not self.query or not self.query.strip() or any(ord(c)<32 and c not in '\n\t' for c in self.query):
                raise ValueError('bounded search query required')
            if any(getattr(self,name) is not None for name in ('identifier','ecosystem','package','version','topic')):
                raise ValueError('web search accepts only query')
            self.query=self.query.strip()
        elif not self.topic:
            raise ValueError('registered topic required')
        return self


@router.post('/lookup')
def intelligence_lookup(body: LookupRequest, db: Database):
    try:
        return lookup(db, body.model_dump())
    except IntelligenceError as error:
        code=str(error)
        search_status={'search_not_configured':503,'search_auth_failed':502,
            'search_rate_limited':503,'search_timeout':504,'search_source_unavailable':502,
            'search_invalid_response':502,'search_unsafe_url':502,'search_response_too_large':502,'search_busy':503}
        raise HTTPException(search_status.get(code,504 if code=='intelligence_timeout' else 503), code) from None
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(502,'intelligence_invalid_response') from None
