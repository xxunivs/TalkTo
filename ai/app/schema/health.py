# 서버 살아있나 응답

from app.schema.field_rule import ApiModel


class HealthResponse(ApiModel):
    status: str
    service: str
