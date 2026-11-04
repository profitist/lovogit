from lovogit.app.services.status import StatusService
from lovogit.bot.service_middleware import SessionServiceMiddleware


class StatusMiddleware(SessionServiceMiddleware):
    _service_key = "status_service"
    _service_type = StatusService
