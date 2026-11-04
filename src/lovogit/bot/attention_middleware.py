from lovogit.app.services.attention import AttentionService
from lovogit.bot.service_middleware import SessionServiceMiddleware


class AttentionMiddleware(SessionServiceMiddleware):
    _service_key = "attention_service"
    _service_type = AttentionService
