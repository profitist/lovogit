class BaseMessageStructure:
    """Base message structure."""

    title: str | None
    content: str | None
    footer: str | None


class _MessageBuilder:
    def __init__(self):
        self.text = ""

    def build(self, message_structure: BaseMessageStructure) -> str:
        entry = message_structure.title or ""
        entry += "\n" + message_structure.content or ""
        entry += "\n" + message_structure.footer or ""
        return entry


message_builder = _MessageBuilder()
