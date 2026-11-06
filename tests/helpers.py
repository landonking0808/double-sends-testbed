"""Test helpers: an agent that plays a fixed list of tool calls."""
from dst.agents.base import Agent
from dst.env import EnvConfig, FaultConfig, Office, build_tree
from dst.logging import MemoryLogger
from dst.tasks import generate_batch


class ListAgent(Agent):
    """Plays ``calls`` in order (each a dict or a function obs -> dict), then sleeps."""

    def __init__(self, agent_id, calls=()):
        self.agent_id = agent_id
        self.calls = list(calls)
        self.observations = []

    def act(self, observation):
        self.observations.append(observation)
        if not self.calls:
            return {"tool": "noop", "args": {"until": "message"}}
        c = self.calls.pop(0)
        return c(observation) if callable(c) else c


def call(tool, **args):
    return {"tool": tool, "args": args}


def make_office(shape="F", condition="B0", faults=None, seed=1, n_repeat=0, **cfg):
    batch = generate_batch(seed, n_messages=12, n_repeat=n_repeat)
    config = EnvConfig(shape=shape, condition=condition, faults=faults or FaultConfig.zero(), **cfg)
    log = MemoryLogger(f"test-{shape}-{condition}-{seed}", seed)
    return Office(batch, config, seed, log, log.episode_id), batch, log


def team(shape="F", **scripts):
    """ListAgents for every node of the shape; ``scripts`` maps agent id -> calls."""
    return {s.id: ListAgent(s.id, scripts.get(s.id, ())) for s in build_tree(shape)}
