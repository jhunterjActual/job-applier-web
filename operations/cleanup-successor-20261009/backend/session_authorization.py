"""Request-scoped v2 evidence, copied into each protected DB transaction."""
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID
import re


class SessionAuthorizationRejected(PermissionError):
    def __init__(self):
        super().__init__('Account authorization is no longer valid.')


@dataclass(frozen=True, repr=False)
class SessionAuthorization:
    local_id: int
    subject: str
    provider_binding: str
    epoch: int
    incarnation: str

    def __post_init__(self):
        if (type(self.local_id) is not int or not 0 < self.local_id <= 9223372036854775807
                or not isinstance(self.subject,str) or not re.fullmatch('user_[A-Za-z0-9_]{1,120}',self.subject)
                or not isinstance(self.provider_binding,str) or not re.fullmatch('[0-9a-f]{64}',self.provider_binding)
                or type(self.epoch) is not int or not 0 < self.epoch < 9223372036854775807
                or not isinstance(self.incarnation, str) or str(UUID(self.incarnation)) != self.incarnation):
            raise ValueError('Invalid session authorization.')


current = ContextVar('careertrellis_session_authorization', default=None)
