from lovogit.cfg.settings import settings

from langchain.agents import create_agent


def init_cheap_agent():
    agent = create_agent(model=settings.CHEAP_MODEL)


def init_strong_agent():
    agent = create_agent(model=settings.MODEL_STRONG)
