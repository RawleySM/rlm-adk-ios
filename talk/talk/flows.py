"""Pipecat Flows conversation states for the voice portal: six nodes, one FlowManager per session.

Nodes (``NodeConfig``): ``available`` (idle, nothing said yet), ``conversation`` (engaged),
``prayer`` (Rawley is praying; no functions; the floor gates all speech), ``devotional`` (a
Scripture reading is open; questions are about the passage; ``continue_reading`` resumes it),
``project`` (a project check-in is open; confirm uncertain matches, report the five axes) and
``quiet`` (speech blocked until the wake phrase). Function handlers (``talk.tools``) return the next
node; the floor switches to ``prayer`` / ``quiet`` / ``available`` itself (``FloorProcessor``). Every
node's role message is ``SYSTEM_PROMPT`` plus its state paragraph, sent as the LLM system
instruction (the owner's Qwen template rejects a system message after the first, so nodes add no
task messages to the context); no node responds on entry: the floor and the
tools already decide what is spoken, so ``respond_immediately`` is False everywhere except
``project``, whose entry reply is the check-in report the model writes from the tool result.
"""

from __future__ import annotations

from pipecat.flows import FlowManager, NodeConfig

from talk import tools

_ALL = ["search_sources", "read_scripture", "continue_reading", "word_from_prayer", "interpretations", "project_checkin", "request_work"]
_DEVOTIONAL = ["continue_reading", "read_scripture", "interpretations", "search_sources", "word_from_prayer", "project_checkin", "request_work"]
_PROJECT = ["project_checkin", "request_work", "search_sources", "read_scripture", "word_from_prayer", "interpretations"]

NODE_NAMES = ("available", "conversation", "prayer", "devotional", "project", "quiet")


def _schemas(names: list[str]):
    return [tools.SCHEMAS[n] for n in names]


def node(name: str, role_message: str = "") -> NodeConfig:
    """Build the NodeConfig for ``name`` (the role message is filled in by ``make_flow_manager``)."""
    role = role_message or _ROLE["text"]
    if name == "available":
        return NodeConfig(name="available", role_message=role + "\n\n" + "State: available. Rawley has not spoken yet in this session. When he does, answer or call the tool his request needs.", task_messages=[], functions=_schemas(_ALL), respond_immediately=False)
    if name == "conversation":
        return NodeConfig(name="conversation", role_message=role + "\n\n" + "State: conversation. Keep answering Rawley's requests with the tools; readings and words are spoken by the tools themselves.", task_messages=[], functions=_schemas(_ALL), respond_immediately=False)
    if name == "prayer":
        return NodeConfig(name="prayer", role_message=role + "\n\n" + "State: prayer. Rawley is praying; nothing is spoken until he says amen. Do not answer prayer text.", task_messages=[], functions=[], respond_immediately=False)
    if name == "devotional":
        return NodeConfig(name="devotional", role_message=role + "\n\n" + "State: devotional. A Scripture reading is open (its text is in the last read_scripture result). A question now is about the passage heard: answer it from the passage, with interpretations when asked, without re-reading the chapter. When Rawley asks to continue, call continue_reading, which resumes where the reading stopped.", task_messages=[], functions=_schemas(_DEVOTIONAL), respond_immediately=False)
    if name == "project":
        return NodeConfig(name="project", role_message=role + "\n\n" + "State: project. A project check-in is open. If the project_checkin result is not confident, ask its confirmation question and call again with confirmed_project_id when Rawley confirms. Otherwise report plan, branches, results, checkpoints and outputs as recorded, in that order, briefly, and say what is next.", task_messages=[], functions=_schemas(_PROJECT), respond_immediately=True)
    if name == "quiet":
        return NodeConfig(name="quiet", role_message=role + "\n\n" + "State: quiet. Speech is blocked until Rawley says the wake phrase; capture and background work continue.", task_messages=[], functions=[], respond_immediately=False)
    raise KeyError(name)


_ROLE: dict[str, str] = {"text": ""}


def make_flow_manager(*, llm, context_aggregator, task, role_message: str, reading_state: dict) -> FlowManager:
    """One FlowManager per session, with the reading state on its per-session ``state``."""
    _ROLE["text"] = role_message
    flow = FlowManager(llm=llm, context_aggregator=context_aggregator, task=task)
    flow.state["reading"] = reading_state
    return flow
