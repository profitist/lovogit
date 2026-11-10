from lovogit.agent.comparer.state import ComparerState


async def collect_context(state: ComparerState):
    package = state['change']['package']
    current_version = state['change']['current_version']
    target_version = state['change']['target_version']
    change_description = state['change']['change_description']
    changed_api = state['change']['changed_api']

    